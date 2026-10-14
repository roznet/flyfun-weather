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

from pydantic import BaseModel, ConfigDict, Field, computed_field

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
    # Cancelled (#686): last seen before its start, then missing from a fetch
    # whose queries would have listed it (euro_aip ``isigmet_covers``). Set
    # once, so its row stays until the trace is dropped.
    cancelled_at: datetime | None = None


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
    # The terminal reads a nearby station's METAR/TAF (#767): "using EGLL as
    # proxy · 8 NM NE", or "METAR EGLF (4 NM N) · TAF EGLL (…)". Clients show
    # it as a gray line under the text. None when the field reports itself.
    proxy_caption: str | None = None


class LiveHighlight(BaseModel):
    """The one-glance highlight above the nutshell (#697), written by a small
    model from facts the code has already computed.

    The model only chooses what leads and words it: every fact in ``text``
    comes from the facts block :mod:`weatherbrief.tasks.live_highlight` built
    deterministically from this tick. Written after the tick has committed, so
    a layer always exists without one: ``None`` until the first generation
    lands, and ``None`` again each time the weather changes significantly
    (#706: compared with :attr:`LiveHighlight.gate`) until the next tick fills
    it, and after the planned arrival. A gate state whose generation is
    rejected is retried once and then left alone (``MAX_ATTEMPTS_PER_FACTS``),
    so this stays ``None`` for that state — the next real weather change gets
    a fresh attempt. Readers
    fall back to :attr:`LiveGlance.headline`.

    Displayed (owner, 2026-10-09) above the nutshell on iOS and web, with an
    "experimental" caption, its written time and a 👍/👎 rated as feedback
    ``target="live_highlight"``; the agent ``live`` block quotes ``text`` and
    ``generated_at`` (as ``written_at``) only. ``gate`` is never served.
    """

    text: str
    model: str
    # Hash of the gate state the text was written from (#706; before it, of
    # the facts block minus ``now``). Identical hash: nothing to compare.
    facts_hash: str
    # The gate state itself (``live_highlight.facts_and_gate``): what the
    # next ticks are compared against to decide whether to regenerate — the
    # state at this generation, not the previous tick, so slow drift is
    # caught. ``None`` on a highlight written before #706 (regenerated once).
    gate: dict | None = None
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
    # A departure/destination read through a proxy station (#767): the
    # station(s) its METAR/TAF come from ("EGLL"). None otherwise.
    via: list[str] | None = None


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
    # What this tick actually read (#754): full airport change keys
    # ("metar:LFAT") and dimension prefixes ("sigmet:"). The cleared-push
    # sustain rule counts only ticks where a key was evaluated. In memory
    # only: never dumped, so None on anything read back from disk.
    evaluated: list[str] | None = Field(default=None, exclude=True)

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


class LivePushedAlert(BaseModel):
    """An alert-tier change pushed to the pilot and not yet pushed as cleared
    (#754). What the clear push needs to say, plus its sustain counter."""

    key: str
    kind: ChangeKind
    role: ChangeRole
    icao: str | None = None
    to_value: str | None = None
    message: str
    pushed_at: datetime
    # Consecutive evaluated ticks on which the key has not been an alert-tier
    # row. A clear is pushed when it reaches ``live_alerts.CLEAR_SUSTAIN_TICKS``.
    clear_ticks: int = 0
    # Recorded in shadow mode, so never actually on the pilot's phone: once
    # sending is on, its clear is dropped silently rather than pushed.
    shadow: bool = False


class LivePushState(BaseModel):
    """The live-alert push stream's memory for one flight (#754), next to the
    classifier's ``alerted``. Written only by the server tick, after its
    commit (``live_layer.patch_push_state``); reset with the layer by a new
    pack. In shadow mode it advances as if each decision had been sent."""

    # Pushed and still active, by change key.
    active: dict[str, LivePushedAlert] = Field(default_factory=dict)
    # Keys whose clear was pushed -> when. A later alert-tier row for such a
    # key pushes again even though §45's memory keeps it quiet on screen.
    rearmed: dict[str, datetime] = Field(default_factory=dict)
    # Alerts whose push failed (APNs error, no device reached) -> the first
    # failed attempt. Their ``new_alert`` is spent, so they are re-sent on
    # later ticks while still alert-tier rows, within ``PUSH_TTL`` of that
    # attempt (the push would have expired by then anyway).
    retry: dict[str, datetime] = Field(default_factory=dict)
    # Shadow-mode measurement (owner review): how often each fired.
    alerts_pushed: int = 0
    clears_pushed: int = 0
    rearms_fired: int = 0


class LiveProgress(BaseModel):
    """Where the live layer places the flight, and from what (#759, #760).

    ``analysis/flight_progress.FlightProgress`` as of the tick, for clients
    to draw the "position used" marker at ``along_nm`` and caption it with
    ``source``. ``departed_at`` / ``landed_at`` are **observed** entries only
    (None on the plan, whatever the clock says), so a client never shows
    "Landed" from a schedule. ``eta_destination`` is the effective arrival
    (the plan, shifted by a take-off, or the landing itself).

    The fix fields (``fix_at``, ``fix_age_min``, ``off_route``) stay empty
    until a position source exists (#761 app GPS, #762 ADS-B). ``estimated``
    is True whenever the position is extrapolated rather than a fresh fix,
    which is always the case for the plan and manual sources.

    Redacted for non-owner viewers per the flight's ``position_sharing``
    (``live_layer.redact_live``).
    """

    # "plan" | "plan+takeoff" (later: "app", "adsb").
    source: str
    as_of: datetime
    along_nm: float | None = None
    route_nm: float | None = None
    lat: float | None = None
    lon: float | None = None
    fix_at: datetime | None = None
    fix_age_min: float | None = None
    estimated: bool = True
    off_route: bool = False
    departed_at: datetime | None = None
    eta_destination: datetime | None = None
    landed_at: datetime | None = None
    # Observed take-off minus planned departure, minutes (+ = late).
    delay_min: float | None = None


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
    # Where the tick placed the flight and from what source (#760).
    progress: LiveProgress | None = None

    changes: LiveChanges | None = None
    # Worsening-only view of ``changes``, kept so clients that predate the
    # live layer (they read ``last_refresh_delta`` off the snapshot) still
    # get a banner.
    last_refresh_delta: RefreshDelta | None = None

    # Alert memory: change key -> what has alerted. Airport rows keep the
    # worst value alerted (significant weather: every phenomenon alerted) for
    # the flight and alert again only above it (§45, #722). Other rows keep
    # the ``to_value`` last alerted and alert again when it moves; such a key
    # that returns to the briefing's state is dropped so a recurrence alerts
    # afresh.
    alerted: dict[str, str] = Field(default_factory=dict)
    # Route SIGMETs seen recently, so a reissue reads as a replacement (#682).
    sigmet_traces: list[LiveSigmetTrace] = Field(default_factory=list)
    # Live-alert push memory (#754): what was pushed and not yet cleared, and
    # which keys a pushed clear re-armed. None until the first push decision.
    push_state: LivePushState | None = None

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
    progress: LiveProgress | None = None
    changes: LiveChanges | None = None
    last_refresh_delta: RefreshDelta | None = None


class LiveTickResponse(LiveLayerResponse):
    """``GET /api/flights/{id}/live/at?tick=`` (#711): the Observed section as
    it stood at one recorded tick, in the ``/live`` shape. ``route_observations``
    is rebuilt from the history's raw reports (no runway/wind picture); the
    rest is the tick's stored snapshot, with the change trails as of then."""

    tick_at: datetime
    # The radar frame the ribbon was binned from (None without radar).
    radar_time: datetime | None = None
    # The ``FlightProgress`` state the tick used: source, flown_nm,
    # departed_at, arrival_at.
    progress: dict | None = None


# --- Debrief replay and recap (#711 server, #769 typed) ----------------------
#
# The recap is computed from stored snapshots, so the blocks it passes through
# (weather bands, storms, waypoints, lane advisories) keep ``extra="allow"``:
# a field a later snapshot adds reaches the client instead of being dropped,
# and an older snapshot missing one does not 500. Times stay the strings the
# snapshots stored, so a ``tick_at`` round-trips to ``/live/at`` unchanged.


class TickSegment(BaseModel):
    """One ribbon stretch of a recorded tick (``/live/ticks?segments=1``),
    for the distance × time grid: the radar lane only."""

    from_nm: float
    to_nm: float
    radar_status: str | None = None
    radar_max_dbz: float | None = None
    lightning: bool | None = None


class LiveTickEntry(BaseModel):
    """One recorded tick of the live window."""

    tick_at: str | None = None
    # The radar frame the tick's ribbon was binned from (None without radar).
    radar_time: str | None = None
    pack_timestamp: str | None = None
    # The planned (or observed, #759) position at the tick.
    flown_nm: float | None = None
    progress_source: str | None = None
    alert_count: int | None = None
    # Only with ``?segments=1``.
    segments: list[TickSegment] | None = None


class LiveTicksResponse(BaseModel):
    """``GET /api/flights/{id}/live/ticks``: oldest first, empty when nothing
    was recorded."""

    flight_id: str
    ticks: list[LiveTickEntry] = Field(default_factory=list)


class RecapSegment(BaseModel):
    """One ribbon stretch on the recap's diagonal: what was observed there
    at the time the aircraft was there."""

    index: int | None = None
    from_nm: float
    to_nm: float
    # The aircraft's time at the stretch's midpoint (``FlightProgress.eta``).
    at: str | None = None
    # observed / not_reached / before_recording / untimed.
    status: str
    # The tick and radar frame this stretch was read from (observed only).
    tick_at: str | None = None
    radar_time: str | None = None
    pack_timestamp: str | None = None
    # Frame time minus aircraft time, minutes.
    gap_min: float | None = None
    weather_status: str | None = None
    radar_max_dbz: float | None = None
    radar_intensity: str | None = None
    radar_status: str | None = None
    lightning: bool | None = None
    sigmet_ids: list[str] | None = None
    storm_ids: list[str] | None = None


class RecapWeather(BaseModel):
    """A ``RibbonWeather`` band of the tick a stretch was read from."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    tier: str | None = None
    from_nm: float | None = None
    to_nm: float | None = None
    side: str | None = None
    near_nm: float | None = None
    far_nm: float | None = None
    profile: list[tuple[float, float, float]] = Field(default_factory=list)
    peak_dbz: float | None = None
    intensity: str | None = None
    tick_at: str | None = None
    segment_index: int | None = None


class RecapStorm(BaseModel):
    """A radar storm abeam a stretch, as the tick it was read from saw it."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    lat: float | None = None
    lon: float | None = None
    peak_dbz: float | None = None
    intensity: str | None = None
    flashes: int | None = None
    top_fl: int | None = None
    trend: str | None = None
    along_nm: float | None = None
    offtrack_nm: float | None = None
    cross_nm: float | None = None
    side: str | None = None
    relative_motion: str | None = None
    tick_at: str | None = None
    segment_index: int | None = None


class RecapAlert(BaseModel):
    """A change raised or cleared over the flight day (history ``event``),
    placed at the aircraft's position then."""

    at: str | None = None
    event: Literal["raised", "cleared"]
    tier: str | None = None
    direction: str | None = None
    key: str | None = None
    kind: str | None = None
    role: str | None = None
    icao: str | None = None
    message: str | None = None
    observed_at: str | None = None
    enroute_distance_nm: float | None = None
    aircraft_nm: float | None = None


class RecapWaypoint(BaseModel):
    model_config = ConfigDict(extra="allow")

    icao: str | None = None
    along_nm: float | None = None
    eta: str | None = None


class ForecastLaneModel(BaseModel):
    model: str
    status: str | None = None
    # [from_nm, to_nm, severity] — a gapless partition of the route.
    ribbon: list[tuple[float, float, str]] = Field(default_factory=list)


class ForecastLaneAdvisory(BaseModel):
    model_config = ConfigDict(extra="allow")

    advisory_id: str
    aggregate_status: str | None = None
    representative_model: str | None = None
    per_model: list[ForecastLaneModel] = Field(default_factory=list)


class RecapForecast(BaseModel):
    """The forecast row of one pack (``forecast_lane.json``)."""

    pack_timestamp: str
    # default (newest before departure) / request (``?pack=``).
    chosen_by: str
    # That pack's planned departure: its advisories are at its own ETAs.
    planned_departure: str | None = None
    available: bool = False
    v: int | None = None
    route_nm: float | None = None
    cruise_altitude_ft: int | None = None
    models: list[str] = Field(default_factory=list)
    aggregation: str | None = None
    advisories: list[ForecastLaneAdvisory] = Field(default_factory=list)


class RecapResponse(BaseModel):
    """``GET /api/flights/{id}/recap`` (``live_recap.recap_for_flight``)."""

    flight_id: str
    note: str
    # ok / no_live_window / not_recorded / untimed.
    status: str
    message: str | None = None
    cancelled: bool = False
    # planned / partial / observed (``FlightProgress.time_base``).
    time_base: str | None = None
    progress_source: str | None = None
    departed_at: str | None = None
    arrival_at: str | None = None
    ticks: int = 0
    first_tick_at: str | None = None
    last_tick_at: str | None = None
    # Recording stopped at the size cap: later stretches were not recorded.
    capped: bool = False
    forecast: RecapForecast | None = None
    alerts: list[RecapAlert] = Field(default_factory=list)
    route_nm: float | None = None
    segment_nm: float | None = None
    # The base ribbon's scales, so the client draws the recap like the ribbon.
    radar_radius_nm: float | None = None
    weather_corridor_nm: float | None = None
    weather_bin_nm: float | None = None
    waypoints: list[RecapWaypoint] = Field(default_factory=list)
    segments: list[RecapSegment] = Field(default_factory=list)
    weather: list[RecapWeather] = Field(default_factory=list)
    storms: list[RecapStorm] = Field(default_factory=list)
    # Every pack of the flight, newest first (the pack picker's choices).
    packs: list[str] = Field(default_factory=list)
