"""Pydantic v2 models for flights, briefing packs, and flight profiles (API/storage layer)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from weatherbrief.debriefs.taxonomy import (
    NOTE_MAX_LENGTH,
    OUTCOME_CATEGORIES,
    ConditionTag,
    Decision,
    OutcomeValue,
)
from weatherbrief.models.diagnostic import Diagnostic


class FlightProfile(BaseModel):
    """A named set of flight parameters and advisory settings."""

    id: int
    user_id: str = ""
    name: str = "Default"
    is_default: bool = False
    settings: dict = Field(default_factory=dict)
    system_template_key: str | None = None
    created_at: datetime
    updated_at: datetime


class Flight(BaseModel):
    """A saved briefing target — route + date/time specifics."""

    id: str  # slug: "{route_name}-{YYYY-MM-DD}-{hash}"
    user_id: str = ""  # owner; empty in single-user / dev mode
    profile_id: int | None = None  # associated flight profile
    aircraft_id: int | None = None  # associated user aircraft
    route_name: str  # user-assigned name or derived from waypoints
    waypoints: list[str] = Field(default_factory=list)  # airports, navaids, or fixes
    departure_time: datetime  # aware UTC datetime
    cruise_altitude_ft: int = 8000
    flight_ceiling_ft: int = 18000
    flight_duration_hours: float = 0.0
    private: bool = False
    alt_departure_time: datetime | None = None  # the "Alternate time" flexibility value
    # Timing-scenario flexibility (timing-scenario-plan.md): how much departure
    # flexibility the pilot has, and therefore what the scenario job grades.
    #   none      — no scenario work (local yes/no flights)
    #   alternate — grade the one alt_departure_time as a pinned candidate
    #   same_day  — scan the daylight window of the target date
    #   prev_day / next_day — scan the adjacent day as well (needs extra OM fetch)
    flexibility: Literal["none", "alternate", "same_day", "prev_day", "next_day"] = "none"
    # Per-flight briefing-notification override (ios-app-briefing-notifications.md):
    #   default — follow the global notify scope
    #   notify  — notify on ANY completion of this flight (even if global is off/auto)
    #   mute    — never notify for this flight
    # Independent of auto_refresh (they are separate controls and stay separate).
    notify_override: Literal["default", "notify", "mute"] = "default"
    auto_refresh: bool = False
    auto_refresh_hour: int | None = None
    last_auto_refresh_at: datetime | None = None
    # Original Field-15 input the pilot typed, when captured. NULL for
    # iOS/MCP-created flights and for flights that pre-date the column —
    # no input string was ever recorded for those, distinct from "input
    # was empty." Stays in sync with ``waypoints``: cleared when waypoints
    # are edited directly, overwritten when a new raw string is parsed.
    raw_route: str | None = None
    # euro_aip version that derived ``waypoints`` from ``raw_route``
    # (e.g. ``"euro_aip/0.9.0"`` — the ``library/version`` shape comes
    # from ``_euro_aip_parser_version`` in the API layer). Lets a future
    # re-derive job spot flights that would benefit from a newer parser.
    # NULL whenever raw_route is NULL.
    parser_version: str | None = None
    # Short share token for /s/{code} redirect. Generated at save_flight
    # time when missing — only ever NULL for direct FlightRow construction
    # in tests.
    share_code: str | None = None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def target_date(self) -> str:
        """YYYY-MM-DD derived from departure_time (backward compat)."""
        return self.departure_time.strftime("%Y-%m-%d")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def target_time_utc(self) -> int:
        """Departure hour derived from departure_time (backward compat)."""
        return self.departure_time.hour


class AdvisoryChip(BaseModel):
    """One named advisory concern, for the flights-list summary chips."""

    # Uppercase to match the TypeScript ``'RED' | 'AMBER'`` union the card
    # renders; deliberately distinct from ``AdvisoryStatus`` (lowercase).
    status: Literal["RED", "AMBER"]
    name: str  # catalog display name, e.g. "Convection"


class AdvisorySummary(BaseModel):
    """Compact per-flight advisory breakdown denormalized onto the pack.

    Small by design — persisted as JSON on ``BriefingPackRow`` so the
    flights-list card can show RED/AMBER counts + top categories without
    reading ``route_advisories.json`` per flight on every page load.
    """

    red: int = 0
    amber: int = 0
    top: list[AdvisoryChip] = Field(default_factory=list)  # severity-ordered, capped at 3


class BriefingPackMeta(BaseModel):
    """Metadata for one fetch — lightweight index for history listing."""

    id: int | None = None  # DB primary key (auto-generated)
    flight_id: str
    fetch_timestamp: datetime  # aware UTC datetime
    days_out: int
    has_gramet: bool = False
    has_skewt: bool = False
    has_digest: bool = False
    # Whether the LLM digest was requested for this pack (profile toggle).
    # Defaults True so legacy packs (no stored value) behave as before:
    # has_digest=False reads as "still generating / failed", not "off". A pack
    # built with the profile's AI toggle off carries False, which the UI uses
    # to show "AI summary off for this profile" + a Generate button instead.
    llm_digest_requested: bool = True
    # GREEN/AMBER/RED from the digest (short range), or UNAVAILABLE when nothing
    # could be graded (#392). NULL = not briefed yet — a different state.
    assessment: Optional[str] = None
    assessment_reason: Optional[str] = None
    # Compact RED/AMBER advisory breakdown denormalized at briefing-build time
    # so the flights-list card renders the per-flight summary chips without
    # reading route_advisories.json. None for old packs (set on next refresh).
    advisory_summary: Optional[AdvisorySummary] = None
    # Long-range (beyond the GRIB horizon) outlook, in place of the traffic-light
    # assessment: TRENDING_SETTLED / MIXED_SIGNALS / TRENDING_UNSETTLED. Mutually
    # exclusive with ``assessment`` — a long-range pack shows a soft outlook, not
    # a go/no-go verdict.
    outlook: Optional[str] = None
    outlook_reason: Optional[str] = None
    # LangSmith root run id of the digest LLM call (issue #244). Set when the
    # digest is generated with tracing-controlled run_id; NULL for provisional
    # rows (digest not yet run) and legacy packs created before #244. Used by
    # the feedback endpoint to attach thumb ratings to the LangSmith run.
    digest_trace_id: Optional[str] = None
    artifact_path: str = ""  # path to pack directory
    model_init_times: dict[str, int] = Field(default_factory=dict)
    grib_init_times: dict[str, int] = Field(default_factory=dict)
    # Maps logical model name → freshness source key (e.g. "ecmwf" → "ecmwf:direct"
    # or "ecmwf:openmeteo").  Set at enrichment time so the freshness marker
    # store knows which source to compare each pack's init against.  Empty for
    # legacy packs created before issue #108 — the freshness check infers from
    # ``grib_init_times`` presence in that case.
    model_sources: dict[str, str] = Field(default_factory=dict)
    models_skipped_region: list[str] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    # GREEN/AMBER/RED/UNAVAILABLE for the alt departure time (#392).
    alt_assessment: Optional[str] = None
    alt_assessment_reason: Optional[str] = None
    has_alt_advisories: bool = False
    # DWD Surface Analysis & Forecast — references the shared chart cache.
    # NULL run_cycle = section is unavailable (out of coverage, beyond
    # horizon, or chart refresh failed at briefing time).
    dwd_charts_run_cycle: Optional[str] = None  # e.g. "2026-05-08T06Z"
    dwd_charts_default_id: Optional[str] = None  # "ana" | "036" | "048" | "060" | "084" | "108"
    dwd_charts_in_coverage: bool = False
    dwd_charts_within_horizon: bool = False
    # Met Office surface-pressure charts — same shape, second source.
    metoffice_charts_run_cycle: Optional[str] = None  # e.g. "2026-05-29T00Z"
    metoffice_charts_default_id: Optional[str] = None  # "ana" | "012" .. "120"
    metoffice_charts_in_coverage: bool = False
    metoffice_charts_within_horizon: bool = False
    # Météo-France TEMSI (SIGWX) charts — AEROWEB. Deliberately NOT the
    # (run_cycle, default_chart_id) shape the two front-chart sources use:
    # AEROWEB keys charts by absolute valid time with no run/offset split, so
    # the selected validity is per *zone*, and the zones do not publish in
    # lockstep (euroc has been seen a validity ahead of france). Maps zone slug
    # -> the validity chosen for this flight, e.g. {"france": "2026-08-31T15Z"}.
    # Empty = unavailable: no access code, route outside French airspace (the
    # licence limit), or no validity near the ETD — TEMSI's horizon is ~3h, so
    # a briefing built the day before a flight legitimately has none.
    meteofrance_charts_zone_cycles: dict[str, str] = Field(default_factory=dict)
    meteofrance_charts_in_coverage: bool = False
    meteofrance_charts_within_horizon: bool = False
    # Hash of the flight parameters (route, departure time, altitude, ceiling,
    # duration) this pack was computed for — see
    # ``storage.flights.compute_flight_params_hash``. Lets the refresh gate tell
    # "no new model run" apart from "the flight itself changed" (#552). None for
    # packs written before the column existed.
    flight_params_hash: Optional[str] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_historical(self) -> bool:
        """True when the briefing was generated for a past departure date."""
        return self.days_out < 0


# Single source of truth is `debriefs.taxonomy.NOTE_MAX_LENGTH` (also served to
# iOS/mirrored in TS as the pilot-facing counter/gate). Kept as an alias so the
# server-enforced limit can't drift from what clients display, and so the
# existing `models.NOTE_MAX_LEN` export keeps resolving.
NOTE_MAX_LEN = NOTE_MAX_LENGTH


class FlightDebrief(BaseModel):
    """Pilot post-flight judgement against one flight.

    Outcomes-dict keys are the categories that were *queried* at debrief
    time (those with an advisory raised on the briefing); values default to
    ``CONSISTENT``. Categories absent from the dict were not queried — they
    do not count toward per-category accuracy stats.
    """

    flight_id: str
    decision: Decision
    reasons: list[ConditionTag] = Field(default_factory=list)
    outcomes: dict[ConditionTag, OutcomeValue] = Field(default_factory=dict)
    note: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("reasons", mode="before")
    @classmethod
    def _dedupe_reasons(cls, v):
        if isinstance(v, list):
            seen: set = set()
            out: list = []
            for item in v:
                key = item.value if isinstance(item, ConditionTag) else item
                if key not in seen:
                    seen.add(key)
                    out.append(item)
            return out
        return v

    @field_validator("note")
    @classmethod
    def _note_length(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if len(v) > NOTE_MAX_LENGTH:
            raise ValueError(f"note must be at most {NOTE_MAX_LENGTH} characters")
        return v

    @model_validator(mode="after")
    def _decision_shape(self) -> FlightDebrief:
        if self.decision is Decision.FLOWN and self.reasons:
            raise ValueError("reasons must be empty when decision is 'flown'")
        if self.decision is Decision.CANCELLED and self.outcomes:
            raise ValueError("outcomes must be empty when decision is 'cancelled'")
        if self.decision is Decision.MONITORING:
            # Monitoring flights aren't real go/no-go decisions, so neither
            # field has meaning. Note is still allowed (pilot may want to
            # remember why they set it up).
            if self.reasons:
                raise ValueError("reasons must be empty when decision is 'monitoring'")
            if self.outcomes:
                raise ValueError("outcomes must be empty when decision is 'monitoring'")
        for tag in self.outcomes:
            if tag not in OUTCOME_CATEGORIES:
                raise ValueError(f"{tag.value} is not a valid outcome category")
        return self
