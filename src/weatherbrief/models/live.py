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
    "storm",
]
ChangeSource = Literal["METAR", "SPECI", "TAF", "SIGMET", "LIGHTNING", "RADAR"]
ChangeRole = Literal["departure", "destination", "alternate", "route"]
#: ``updated``: a change that is neither better nor worse, e.g. a FIR's plain
#: reissue of a SIGMET the briefing already had (#689). Not counted as worse
#: or better; clients render it neutral.
ChangeDirection = Literal["worse", "better", "updated"]


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


class LiveSigmetTrace(BaseModel):
    """A route SIGMET the live layer has seen, kept so the FIR's reissue of
    it (LFMM T01 → T02) reads as a replacement, not a new SIGMET (#682).

    Persisted on the layer (``sigmet_traces``) and reset with it on a new
    pack, like the alert memory. A trace is dropped once its SIGMET is gone
    and its validity ended more than the reissue window ago.
    """

    key: str  # "sigmet:LFMM|T02" (live_significance._sigmet_key_str)
    label: str  # "LFMM T02"
    # The key of the first SIGMET in its reissue chain: the change row's key,
    # so the row (and its trail) carries on across reissues.
    chain: str
    # The chain started from a SIGMET the baseline already had (the
    # replacement is then highlight, not alert).
    chain_in_baseline: bool = False
    # The SIGMET this one replaced (None: it was new, or in the baseline).
    replaces_key: str | None = None
    replaces_label: str | None = None
    replaced_at_destination: bool = False
    # An alert-tier row of this chain has been shown, so a later reissue
    # does not alert again. False on layers written before the field: the
    # next reissue then alerts once (the safe side).
    chain_alerted: bool = False
    fir_id: str
    hazard: str | None = None
    qualifier: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    # (min_lon, min_lat, max_lon, max_lat) of the area; None without geometry.
    bbox: tuple[float, float, float, float] | None = None
    at_destination: bool = False
    # Vertical band and route proximity (``SigmetAlongRoute.min_distance_nm``,
    # 0 = the route enters it), kept so a reissue can be compared with its
    # predecessor (#689). None on traces written before the fields.
    base_ft: int | None = None
    top_ft: int | None = None
    min_distance_nm: float | None = None
    # Reissue only (#689): the reissue is worse than its predecessor (area now
    # reaches the route, vertical extent now reaches the flight's band).
    # Decided once, when the reissue is first seen, so the row's direction
    # never flips between ticks.
    reissue_worse: bool = False
    last_seen: datetime


FocusKind = Literal["storm", "sigmet", "station", "segment"]


class LiveFocus(BaseModel):
    """Where the map opens when an item is tapped (#690): the tap-to-map
    contract shared by iOS, web and anything else that draws the layer.

    The client frames the map on ``bbox`` and turns on ``layers`` (any of
    ``route``, ``radar``, ``cells``, ``lightning``, ``sigmets``, ``metar``),
    highlighting the item ``kind`` / ``id`` names. ``time`` is the frame to
    show (the cell frame for a storm, the METAR for a station); None = now.
    """

    kind: FocusKind
    # storm: its lineage id; sigmet: "sigmet:LECM|6" (the change-row key form);
    # station: the ICAO; segment: "seg:<index>" ("seg:ahead" for the en-route
    # line's stretch still ahead).
    id: str
    # (min_lon, min_lat, max_lon, max_lat), like ``LiveSigmetTrace.bbox``.
    bbox: tuple[float, float, float, float]
    layers: list[str] = Field(default_factory=list)
    time: datetime | None = None


class StormTrackPoint(BaseModel):
    """A storm's observed position against the route at one earlier frame."""

    at: datetime
    offtrack_nm: float
    # Signed: + right of track, − left (facing the direction of flight).
    cross_nm: float


class StormEstimate(BaseModel):
    """Closest approach to the planned 4-D track if the storm kept its current
    motion (#688 addendum). A projection, not an observation: logged every
    tick for scoring and shown only in the storm's detail, labelled
    "Estimate at current motion" — never in an alert, the nutshell or the
    ribbon until the scoring shows skill at that horizon."""

    cpa_nm: float
    cpa_time: datetime
    # Off-track distance of the storm when the aircraft reaches the point
    # abeam its current position, at current motion.
    at_eta_offtrack_nm: float | None = None
    # cpa_time − frame time, minutes.
    horizon_min: float


class LiveStorm(BaseModel):
    """One radar storm against the route (#688): a core35 cell with the
    core41 cells inside it, or a core41 on its own.

    Everything here but ``estimate`` is an observation of the newest cell
    frame: position, strength, trend, motion over the last frames.
    """

    # The storm's lineage id: its core35 cell's id, else the core41's own.
    id: str
    cell_ids: list[str]
    lat: float
    lon: float
    peak_dbz: float
    # "heavy" / "very heavy" / "extreme" (the VIP ladder, §33); None below heavy.
    intensity: str | None = None
    flashes: int | None = None
    flashes_pending: bool = False
    top_fl: int | None = None
    truncated: bool = False
    # developing / steady / decaying / mixed / new (the node's 30-min trend);
    # "developing" when any cell of the storm is.
    trend: str | None = None
    d_peak_db: float | None = None
    area_ratio: float | None = None
    d_flashes: int | None = None
    # The node's motion: available / withheld / unsupported / … .
    motion_status: str | None = None
    speed_kt: float | None = None
    toward_deg: float | None = None

    # Route geometry.
    along_nm: float
    offtrack_nm: float
    cross_nm: float
    side: Literal["left", "right"] | None = None
    # The storm lies before the route's first point / past its last: say
    # where from the airport, not left/right of track.
    end: Literal["departure", "destination"] | None = None
    end_icao: str | None = None
    # Compass point from that airport ("NE"), only with ``end``.
    end_bearing: str | None = None
    abeam_eta: datetime | None = None
    minutes_to_abeam: float | None = None
    ahead: bool = True
    # Observed motion relative to the track: the velocity's component toward
    # the track (closing, kt). Unknown without an available motion.
    relative_motion: Literal["closing", "moving_away", "parallel", "stationary", "unknown"] = "unknown"
    closing_kt: float | None = None
    # Off-track distance at the earlier frames of the last 30 min, oldest first.
    history: list[StormTrackPoint] = Field(default_factory=list)
    # En-route stations whose CB/TCU/TS report this storm backs ("LFMT CB").
    backing: list[str] = Field(default_factory=list)
    estimate: StormEstimate | None = None
    # Tap-to-map (#690): framed on the storm and the track point abeam it.
    focus: LiveFocus | None = None


class LiveStorms(BaseModel):
    """The radar storms near the route at the newest cell frame (#688).

    ``status`` is never silently "nothing": a dark feed says so, and the
    classifier then falls back to the station and radar-ring rows.
    """

    status: Literal["available", "stale", "disabled", "unavailable"]
    frame_time: datetime | None = None
    # Newest frame's time when the feed is stale ("unavailable since").
    unavailable_since: datetime | None = None
    lightning_pending: bool = False
    # Storms listed: within this distance of the track (behind included).
    corridor_nm: float
    # The route's length (NM), so a storm's along-track position reads
    # against its ends.
    route_nm: float | None = None
    storms: list[LiveStorm] = Field(default_factory=list)
    # The cell policy the node ran (provisional thresholds).
    policy_version: str | None = None


GlancePhase = Literal["departure", "enroute", "arrival"]


class LiveGlanceLine(BaseModel):
    """One nutshell line (#690): what the pilot needs to know for one phase
    of the flight, in one sentence of " · "-separated clauses.

    Server-built so iOS, web and the agent ``live`` block show the same text.
    Observations only: no estimate, no verdict. Missing data says
    "unavailable", never "clear"; counts are storms, not threshold tiers.
    """

    phase: GlancePhase
    # The airport the line is about (departure / destination); None en route.
    icao: str | None = None
    text: str
    # An alert-tier change row belongs to this phase. A styling hint only:
    # the text never depends on it.
    alert: bool = False
    # The phase is behind the flight at plan (departure after take-off time,
    # arrival after the planned landing). Clients may dim the line.
    passed: bool = False
    # Sources this line could not read ("metar", "taf", "storms",
    # "lightning", "sigmets"), so a client can flag them without parsing.
    unavailable: list[str] = Field(default_factory=list)
    # What the line summarises: "metar:LPPR", "taf:LPPT", "storm:<id>",
    # "sigmet:LECM|6". The map focus for each is on the ribbon / storm list.
    sources: list[str] = Field(default_factory=list)
    focus: LiveFocus | None = None


class LiveHighlight(BaseModel):
    """The one-glance highlight above the nutshell (#697), written by a small
    model from facts the code has already computed.

    The model only chooses what leads and words it: every fact in ``text``
    comes from the facts block :mod:`weatherbrief.tasks.live_highlight` built
    deterministically from this tick. Written after the tick has committed, so
    a layer always exists without one: ``None`` until the first generation
    lands, and ``None`` again each time the facts change until the next tick
    fills it. A facts state whose generation is rejected is retried once and
    then left alone (``MAX_ATTEMPTS_PER_FACTS``), so this stays ``None`` for
    that state — the next real weather change gets a fresh attempt. Readers
    fall back to :attr:`LiveGlance.headline`.

    **Not displayed yet** (owner, 2026-10-07): written to the layer so real
    flight days can be reviewed and the prompt calibrated before any client
    shows it. Deliberately absent from the agent ``live`` block for the same
    reason — ``summarize_live`` names the glance fields it exposes.
    """

    text: str
    model: str
    # Hash of the facts block minus ``now``: the same facts carry the previous
    # highlight forward instead of paying for an identical call.
    facts_hash: str
    generated_at: datetime
    # Round-trip of the model call, for the latency budget (#697: p50 ~1.0 s).
    latency_ms: int | None = None


class LiveGlance(BaseModel):
    """The Observed tab's top block (#690, observed-tab-presentation §3):
    one "as of" time, one line comparing with the briefing, then one line per
    phase."""

    as_of: datetime
    # "Observed 14:29Z · as briefed" — the comparison line.
    headline: str
    comparison: Literal["as_briefed", "worse", "better", "mixed", "unavailable"]
    lines: list[LiveGlanceLine] = Field(default_factory=list)
    # The model-written highlight (#697), patched in after the tick commits.
    # None whenever it was not generated, not reusable, or rejected by the
    # grounding check — readers fall back to ``headline``.
    highlight: LiveHighlight | None = None


class RibbonWaypoint(BaseModel):
    icao: str
    along_nm: float
    eta: datetime | None = None


class RibbonSegment(BaseModel):
    """One stretch of the route on the ribbon, with the lanes already binned."""

    index: int
    from_nm: float
    to_nm: float
    eta_from: datetime | None = None
    eta_to: datetime | None = None
    # Strongest echo within ``LiveRibbon.radar_radius_nm`` of the route
    # points in this stretch. ``radar_status``: "measured" (None = nothing
    # detected), "no_coverage" (radar could not see enough of it) or
    # "no_sample" (no observed point in this stretch / no radar field).
    radar_max_dbz: float | None = None
    radar_intensity: str | None = None
    radar_status: Literal["measured", "no_coverage", "no_sample"] = "no_sample"
    # Any flash within the same radius; None when there is no lightning
    # field or no point in this stretch.
    lightning: bool | None = None
    # Route SIGMETs (key form "sigmet:LECM|6") whose along-route span
    # overlaps this stretch, and the storms abeam it (ids on ``storms``).
    sigmet_ids: list[str] = Field(default_factory=list)
    storm_ids: list[str] = Field(default_factory=list)
    focus: LiveFocus | None = None


class RibbonStation(BaseModel):
    """An airport on the station lane: METAR now, TAF at its ETA."""

    icao: str
    role: ChangeRole
    along_nm: float | None = None
    # Signed: + right of track, − left (None without a position).
    cross_nm: float | None = None
    eta: datetime | None = None
    metar_category: str | None = None
    metar_time: datetime | None = None
    # CB / TCU / TS in the observed part of the METAR.
    convective: list[str] = Field(default_factory=list)
    taf_category_at_eta: str | None = None
    # PROB30 / TEMPO / … and its category, when worse than prevailing:
    # clients hatch it.
    taf_temporary_type: str | None = None
    taf_temporary_category: str | None = None
    taf_weather: list[str] = Field(default_factory=list)
    focus: LiveFocus | None = None


class RibbonSigmet(BaseModel):
    """A route SIGMET on the SIGMET band."""

    id: str  # "sigmet:LECM|6"
    label: str  # "LECM 6: EMBD TS"
    hazard: str | None = None
    qualifier: str | None = None
    from_nm: float | None = None
    to_nm: float | None = None
    # Closest distance to the route when it does not cross it.
    min_distance_nm: float | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    pending: bool = False
    # New to the flight (its chain did not start in the baseline); None when
    # not known (``LiveChanges.new_sigmets``).
    new: bool | None = None
    # Its forecast movement (MOV) against the route: toward / away /
    # parallel / stationary, unknown without geometry.
    motion: Literal["toward", "away", "parallel", "stationary", "unknown"] = "unknown"
    focus: LiveFocus | None = None


class RibbonWeather(BaseModel):
    """One rain area or convective core beside the route, from the cells
    feed's outlines (``observed/route_bands.py``): where along the route it
    lies, on which side and how far off, how strong, and how it moves
    relative to the course."""

    id: str
    # rain: the rain20 outline (≥ 20 dBZ); core: a core35 outline (≥ 35 dBZ).
    tier: Literal["rain", "core"]
    # Along-route extent of the part within the corridor.
    from_nm: float
    to_nm: float
    # "both" when the area lies across the track.
    side: Literal["left", "right", "both"]
    # Distance off track of its nearest and farthest edge (capped at the
    # corridor); near is 0 across the track.
    near_nm: float
    far_nm: float
    # The shape: per ``LiveRibbon.weather_bin_nm`` of route, [along_nm (bin
    # centre), cross_lo_nm, cross_hi_nm] — the off-track range the outline
    # covers there, signed (− left, + right), capped at the corridor.
    profile: list[tuple[float, float, float]] = Field(default_factory=list)
    peak_dbz: float | None = None
    intensity: str | None = None
    flashes: int | None = None
    # Motion direction relative to the course, degrees: 0 along it, +90
    # toward the right of track, -90 toward the left, ±180 back down it.
    # None without an available motion.
    motion_rel_deg: float | None = None
    speed_kt: float | None = None
    # The storm (``LiveLayer.storms``) this core is, for its detail sheet.
    storm_id: str | None = None


class LiveRibbon(BaseModel):
    """The route ribbon (#690, observed-tab-presentation §3 layer 1): x =
    distance along the route with ETAs, y = left/right of track.

    The storm lane is ``LiveLayer.storms`` itself (``along_nm`` /
    ``cross_nm`` / ``relative_motion`` / ``focus``), not copied here.
    ``weather`` is the symbolic map's rain/core bands from the cells feed;
    ``weather_status`` is that feed's state, and clients fall back to the
    radar ``segments`` when it is not ``available``.
    """

    route_nm: float
    # Planned position now (on-time departure, constant speed); None untimed.
    flown_nm: float | None = None
    departure_at: datetime | None = None
    arrival_at: datetime | None = None
    segment_nm: float
    radar_radius_nm: float
    radar_time: datetime | None = None
    waypoints: list[RibbonWaypoint] = Field(default_factory=list)
    segments: list[RibbonSegment] = Field(default_factory=list)
    stations: list[RibbonStation] = Field(default_factory=list)
    sigmets: list[RibbonSigmet] = Field(default_factory=list)
    weather: list[RibbonWeather] = Field(default_factory=list)
    weather_status: str | None = None
    weather_corridor_nm: float | None = None
    weather_bin_nm: float | None = None


class LiveChange(BaseModel):
    """One significant change since the briefing.

    ``message`` is deterministic, language-neutral aviation shorthand (ICAO
    codes, flight categories, FIR/SIGMET ids) — clients may render it as is or
    rebuild their own text from the structured fields.
    """

    # Stable identity across ticks: "metar:EGLL" (category), "conv:EGLL",
    # "wx:EGLL", "wind:EGLL", "taf:EGLL", "sigmet:LFFF|3",
    # "lightning:<station>", "radar:<station>", "storm:<storm id>",
    # "storms:later". The alert memory is keyed on it.
    key: str
    kind: ChangeKind
    source: ChangeSource
    direction: ChangeDirection
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
    # SIGMET reissue only (#682): the SIGMET this one replaces ("LFMM T01").
    # The row then keeps the key of the first SIGMET in the chain.
    replaces: str | None = None
    # Storm rows only (§41): the storms (lineage ids) the row stands for — one,
    # or a cluster along the route — for the map focus (#690).
    storm_ids: list[str] | None = None
    # Storm rows only: the members meeting the alert rule this tick. The
    # alert-once memory reads it; never dumped.
    alert_storm_ids: list[str] | None = Field(default=None, exclude=True)
    # Storm rows only: (lo, hi) NM along the route of the alerting members, for
    # the per-stretch alert-once memory. Never dumped.
    storm_span: tuple[float, float] | None = Field(default=None, exclude=True)
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
    # Keys (``sigmet:LECM|6``) of the listed route SIGMETs that are new to the
    # flight: their reissue chain did not start from a SIGMET the baseline
    # had (#689). Clients mark Area Hazards rows NEW from this, never from the
    # change rows (a reissue of a briefed SIGMET is not new). None = not
    # computed (no SIGMET baseline, or a layer written before the field).
    new_sigmets: list[str] | None = None

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
    # Radar storms against the route (#688), recomputed every tick.
    storms: LiveStorms | None = None
    # The Observed tab's nutshell and route ribbon (#690), recomputed every
    # tick from the blocks above (``tasks/live_glance.py``).
    glance: LiveGlance | None = None
    ribbon: LiveRibbon | None = None

    changes: LiveChanges | None = None
    # Worsening-only view of ``changes``, kept so clients that predate the
    # live layer (they read ``last_refresh_delta`` off the snapshot) still
    # get a banner.
    last_refresh_delta: RefreshDelta | None = None

    # Last-alerted memory: change key -> the ``to_value`` last alerted. A
    # change alerts again only when its value moves; a key that returns to the
    # briefing's state is dropped so a later recurrence alerts afresh.
    alerted: dict[str, str] = Field(default_factory=dict)
    # Route SIGMETs seen recently, so a reissue reads as a replacement (#682).
    sigmet_traces: list[LiveSigmetTrace] = Field(default_factory=list)

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
    storms: LiveStorms | None = None
    glance: LiveGlance | None = None
    ribbon: LiveRibbon | None = None
    changes: LiveChanges | None = None
    last_refresh_delta: RefreshDelta | None = None
