"""Per-flight live observation layer (#637).

The briefing pack is immutable: what the assessment saw is frozen in
``briefing.json``. On flight day the observations keep moving, so the latest
METAR/TAF, route SIGMETs and observed radar/lightning/tops live in a separate
per-flight store, one file next to the flight's pack directories. A new full
pack resets it (the store records which pack it is relative to).

Two mechanisms share the store and must not be confused:

- **Display** — ``route_observations`` / ``route_sigmets`` /
  ``observed_conditions``: always the newest data, no thresholds.
- **Significance** — ``changes``: what moved since the *briefing* (the pack's
  own observations), in both directions, with hysteresis on flight-category
  crossings and a last-alerted memory so the same change never alerts twice.

The live layer annotates; it never re-grades the briefing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from weatherbrief.models.observations import (
    RefreshDelta,
    RouteObservations,
    RouteSigmets,
)
from weatherbrief.models.observed import ObservedConditions

ChangeKind = Literal[
    "metar_category",
    "metar_convective",
    "metar_weather",
    "metar_wind",
    "taf_category",
    "sigmet_issued",
    "sigmet_cancelled",
    "lightning",
    "radar",
]
ChangeSource = Literal["METAR", "SPECI", "TAF", "SIGMET", "LIGHTNING", "RADAR"]
ChangeRole = Literal["departure", "destination", "alternate", "route"]


class LiveEvidencePoint(BaseModel):
    """One route point that made a radar/lightning change fire (#643).

    History-only: kept so ``live_history.jsonl`` can say *why* an observed
    change fired. Never serialized onto ``LiveChange`` (see ``evidence``).
    """

    station_id: str
    enroute_distance_nm: float | None = None
    # The innermost ring the rule reads.
    radius_nm: float
    flash_count: int | None = None  # lightning
    max_dbz: float | None = None  # radar
    valid_px: int | None = None  # radar coverage: valid_px / total_px
    total_px: int | None = None


class LiveTrailSpan(BaseModel):
    """One period a change was on screen (#669). ``end`` is None while it
    still is."""

    start: datetime
    end: datetime | None = None


class LiveTrailReport(BaseModel):
    """One METAR/SPECI at the airport, for a ``metar_category`` row's strip."""

    at: datetime
    category: str | None = None  # VFR / MVFR / IFR / LIFR; None when unparsed
    report_type: str | None = None  # METAR / SPECI


class LiveChangeTrail(BaseModel):
    """A change's recent history (#669): display only, computed at read time
    from ``live_history.jsonl`` (``tasks/live_trail.py``), never stored in
    ``live.json`` and never in the snapshot overlay.

    Grouped by change ``key`` *and* direction: a value or tier change on the
    same key (MVFR → IFR) continues the span, a pack switch that re-shows the
    change at the same tick continues it too. Never feeds significance.
    """

    spans: list[LiveTrailSpan] = Field(default_factory=list)
    # How many times this change has come on screen over the flight day,
    # the current/last time included. Clients say "Nth time today" from 2.
    times_today: int = 1
    # metar_category only: the airport's category per METAR/SPECI, oldest
    # first, from the report that first showed this change (latest 6).
    reports: list[LiveTrailReport] | None = None
    # What ``from_value`` is measured against for this change: the briefing's
    # own observations, or the live layer's starting point.
    baseline_source: Literal["briefing", "live_start"] | None = None


class LiveChange(BaseModel):
    """One significant change since the briefing.

    ``message`` is deterministic, language-neutral aviation shorthand (ICAO
    codes, flight categories, FIR/SIGMET ids) — clients may render it as is or
    rebuild their own text from the structured fields.
    """

    # Stable identity across ticks: "metar:EGLL" (category), "conv:EGLL",
    # "wx:EGLL", "wind:EGLL", "taf:EGLL", "sigmet:LFFF|3",
    # "lightning:<station>", "radar:<station>". The alert memory is keyed on it.
    key: str
    kind: ChangeKind
    source: ChangeSource
    direction: Literal["worse", "better"]
    # highlight = significant somewhere on the route; alert = departure,
    # destination or an alternate (the tier #638's push delivery consumes).
    tier: Literal["highlight", "alert"]
    role: ChangeRole
    icao: str | None = None
    station_id: str | None = None
    from_value: str | None = None
    to_value: str | None = None
    # When the evidence was issued/observed: METAR time, TAF issue time,
    # SIGMET valid_from, radar/lightning frame time.
    observed_at: datetime | None = None
    enroute_distance_nm: float | None = None
    message: str
    # True on the tick where this alert-tier change first appeared (or changed
    # value). Consumers that deliver alerts act on this, never on ``tier``
    # alone, so a change persisting across ticks alerts exactly once.
    new_alert: bool = False
    # Radar/lightning only: the route points that triggered the change.
    # Excluded from every dump (live.json, /live, snapshot overlay): only the
    # history writer reads it, off the in-memory change (#643).
    evidence: list[LiveEvidencePoint] | None = Field(default=None, exclude=True)
    # Read-time only (#669): set on ``GET /live`` and realtime refresh
    # responses, never stored and never in the snapshot overlay (see
    # ``TRAIL_EXCLUDE``).
    trail: LiveChangeTrail | None = None
    # Only on ``LiveChanges.recently_cleared`` rows: when it left the screen.
    cleared_at: datetime | None = None


class LiveChanges(BaseModel):
    """Everything significant that moved since the briefing was built."""

    # What the changes are measured against: the pack's fetch timestamp, or —
    # when the pack carries no observations (built before flight day) — when
    # the live layer recorded its own starting point.
    baseline_at: datetime | None = None
    baseline_source: Literal["briefing", "live_start"] = "briefing"
    computed_at: datetime
    changes: list[LiveChange] = Field(default_factory=list)
    # Read-time only (#669): changes that cleared on the weather within the
    # last hour, newest first, each with ``cleared_at``. Display only: never
    # counted below, never an alert. None = not computed (stored layer,
    # snapshot overlay); [] = computed, nothing recent.
    recently_cleared: list[LiveChange] | None = None

    @computed_field
    @property
    def worsened_count(self) -> int:
        return sum(1 for c in self.changes if c.direction == "worse")

    @computed_field
    @property
    def improved_count(self) -> int:
        return sum(1 for c in self.changes if c.direction == "better")

    @computed_field
    @property
    def alert_count(self) -> int:
        return sum(1 for c in self.changes if c.tier == "alert")


#: ``model_dump`` exclude for a ``LiveChanges``: the read-time trail fields,
#: so ``live.json`` and the snapshot overlay stay exactly as before #669.
TRAIL_EXCLUDE: dict = {
    "recently_cleared": True,
    "changes": {"__all__": {"trail", "cleared_at"}},
}


class LiveLayer(BaseModel):
    """The per-flight live store (``live.json``)."""

    flight_id: str
    # The pack this layer is relative to: its fetch timestamp (ISO) and its
    # directory name. A layer whose pack is not the flight's latest is stale
    # and must be ignored by readers — that is how a full refresh resets it.
    pack_timestamp: str
    pack_dir_name: str
    live_updated_at: datetime | None = None

    route_observations: RouteObservations | None = None
    observations_updated_at: datetime | None = None
    route_sigmets: RouteSigmets | None = None
    sigmets_updated_at: datetime | None = None
    observed_conditions: ObservedConditions | None = None
    observed_updated_at: datetime | None = None

    changes: LiveChanges | None = None
    # Worsening-only view of ``changes``, kept so clients that predate the
    # live layer (they read ``last_refresh_delta`` off the snapshot) still
    # get a banner.
    last_refresh_delta: RefreshDelta | None = None

    # Last-alerted memory: change key -> the ``to_value`` last alerted. A
    # change alerts again only when its value moves; a key that returns to the
    # briefing's state is dropped so a later recurrence alerts afresh.
    alerted: dict[str, str] = Field(default_factory=dict)

    # Starting point for blocks the pack lacks. Observations and SIGMETs are
    # only fetched for a D-0 briefing, so a flight briefed the day before has
    # nothing to compare against: the first live fetch of such a block is kept
    # here and changes are measured from it ("since live tracking began").
    # Reset with the rest of the layer when a new pack arrives.
    seeded_observations: RouteObservations | None = None
    seeded_sigmets: RouteSigmets | None = None
    seeded_observed: ObservedConditions | None = None
    seeded_at: datetime | None = None


class LiveLayerResponse(BaseModel):
    """``GET /api/flights/{id}/live``.

    Always 200 once the flight has a pack. When no live data exists yet for the
    latest pack, every block is null and ``live_updated_at`` is null: the
    client keeps showing the pack's own observations.
    """

    flight_id: str
    pack_timestamp: str
    live_updated_at: datetime | None = None
    route_observations: RouteObservations | None = None
    observations_updated_at: datetime | None = None
    route_sigmets: RouteSigmets | None = None
    sigmets_updated_at: datetime | None = None
    observed_conditions: ObservedConditions | None = None
    observed_updated_at: datetime | None = None
    changes: LiveChanges | None = None
    last_refresh_delta: RefreshDelta | None = None
