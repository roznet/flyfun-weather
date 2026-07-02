"""Timing-scenario scan — better departure-window discovery.

Implements ``designs/plans/timing-scenario-plan.md``. When a briefing flags a
hazard that genuinely varies through the day, this surfaces *"a better
departure time may exist"* — an **attention-director, never a verdict**.

Two regimes, treated separately (see the plan's *enrichment-window reality*):

* **±3h of the flight window** — free re-analysis of already-enriched data
  (``run_alt_from_pack`` as-is; used by the pinned *preferred* departure time).
* **Full daylight window** — the v1 primitive here:
  :func:`extend_ecmwf_enrichment_daylight` **re-decodes the daylight ECMWF
  fhours** (decode-only, local disk) into an extended cross-section set, so a
  candidate hour is graded on *decoded* ECMWF fields — never a silent
  ``at_time()`` clamp that would present OM-clamped values labelled ECMWF.

Hard invariant: **never grade a candidate hour whose ECMWF fields aren't
decoded.** Extend enrichment to cover it, or refuse the hour.

Cost asymmetry exploited: ECMWF is both the best model and the locally-delivered
one, so the cheap background search is ECMWF-only (full fidelity, decode-only).
The expensive multi-model confirm (ICON/GFS download+decode) is gated on a user
tap — see :func:`confirm_candidate`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from weatherbrief.models import (
    AdvisoryAggregation,
    RouteAdvisoriesManifest,
    RouteConfig,
    RouteCrossSection,
    TimeCandidate,
    TimeConfirmation,
    TimeScanBaseline,
    TimeScanWindow,
    TimeWindowScan,
)

logger = logging.getLogger(__name__)

# Enrichment filters GRIB steps to the window ± this margin (mirrors the
# flight-window filter in ``fetch/grib/__init__.py``). The honest ECMWF
# coverage is bounded by the decoded native anchors, so this only affects which
# anchors get decoded, not the honesty edge (which is min/max decoded anchor).
_ENRICH_MARGIN = timedelta(hours=3)

# Cap on candidate departures graded per scan, so a fleet of flights can't
# stampede the decode pool. Daylight rarely has more native anchors than this.
_MAX_GRID = 24

# Severity ranking for the improve/worsen diff.
_SEV = {"green": 0, "amber": 1, "red": 2}

# A candidate is surfaced only if it drops at least this much *net* severity on
# the trigger (scan-class) advisory set — the "better margin" that suppresses
# noise. 1 == at least one scan advisory drops a level with no offsetting
# scan-class regression.
_MIN_TRIGGER_MARGIN = 1

# How many improving windows to keep in the artifact (the UI caps at ~3).
_MAX_IMPROVING_KEPT = 5


# ---------------------------------------------------------------------------
# ECMWF daylight enrichment-window extension (the v1 primitive)
# ---------------------------------------------------------------------------


@dataclass
class EcmwfExtendResult:
    """Outcome of extending ECMWF enrichment across the daylight window."""

    cross_sections: list[RouteCrossSection]
    run_ts: int | None = None                       # ECMWF init unix ts
    decoded_valid_times: list[datetime] = field(default_factory=list)  # sorted native anchors
    horizon: datetime | None = None                 # base_time + max step_hours

    @property
    def coverage(self) -> tuple[datetime, datetime] | None:
        """Honest ECMWF coverage ``[earliest anchor, latest anchor]`` or None."""
        if not self.decoded_valid_times:
            return None
        return (self.decoded_valid_times[0], self.decoded_valid_times[-1])

    def covers(self, valid_times: list[datetime]) -> bool:
        """True iff every valid-time falls inside the decoded coverage.

        The honesty guardrail: propagation interpolates ECMWF fields *between*
        decoded anchors, so any time in ``[first, last]`` is ECMWF-derived;
        outside it, ``at_time()`` would clamp to OM — refuse those.
        """
        cov = self.coverage
        if cov is None or not valid_times:
            return False
        lo, hi = cov
        return all(lo <= _aware(t) <= hi for t in valid_times)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ecmwf_run_coverage(
    cover_until: datetime,
    *,
    as_of_time: datetime | None,
    ecmwf_data_dir: Path | None,
) -> tuple[int | None, list[datetime], datetime | None]:
    """Select the ECMWF run and return ``(run_ts, decoded_anchors, horizon)``.

    Mirrors the run selection ``_enrich_ecmwf_inner`` performs internally, so
    the anchors we compute here match the fhours the enrichment actually
    decodes. ``decoded_anchors`` are the pressure-level (a2) native valid-times
    within the enrichment window (``cover-back margin`` … ``cover_until +
    margin``), which is exactly the honest ECMWF coverage.
    """
    from weatherbrief.fetch.grib.ecmwf_fetch import (
        ecmwf_grib_dir,
        find_best_ecmwf_run,
        scan_ecmwf_files,
    )

    grib_dir = ecmwf_data_dir or ecmwf_grib_dir()
    all_files = scan_ecmwf_files(grib_dir)
    if as_of_time is not None:
        all_files = [f for f in all_files if f.base_time <= as_of_time]
    if not all_files:
        return None, [], None

    run_files = find_best_ecmwf_run(all_files, cover_until=cover_until, data_dir=grib_dir)
    if not run_files:
        return None, [], None

    base_time = run_files[0].base_time
    max_step = max(f.step_hours for f in run_files)
    horizon = base_time + timedelta(hours=max_step)

    anchors = sorted(
        base_time + timedelta(hours=f.step_hours)
        for f in run_files
        if f.is_pressure_level
    )
    return int(base_time.timestamp()), anchors, horizon


def extend_ecmwf_enrichment_daylight(
    pack_dir: Path,
    *,
    window_start: datetime,
    window_end: datetime,
    flight_duration_hours: float,
    as_of_time: datetime | None = None,
    ecmwf_data_dir: Path | None = None,
    cross_sections: list[RouteCrossSection] | None = None,
    persist: bool = True,
) -> EcmwfExtendResult:
    """Re-decode the daylight ECMWF fhours into an extended cross-section set.

    ``window_start`` / ``window_end`` bound the candidate *departure* times; the
    enrichment must cover departures through their arrivals, so it spans
    ``[window_start, window_end + flight_duration]``. Decode-only (local ECPDS
    disk, no download). Enriches the ECMWF cross-sections in place and
    interpolates the gap hours, then optionally writes ``cross_section_ext.json``.

    Reuses the production enrichment machinery: ``_enrich_ecmwf`` writes GRIB
    pressure levels + surface diagnostics onto the matching hours, and
    ``propagate_all`` fills the between-anchor hours. ``all_forecasts=[]`` is
    passed deliberately — only the cross-sections (what the re-grade reads) need
    enriching; the waypoint-only forecasts are irrelevant to the scan.
    """
    from weatherbrief.fetch.grib import (
        DecodePriority,
        _enrich_ecmwf,
        set_decode_priority,
    )
    from weatherbrief.fetch.grib.fill import propagate_all
    from weatherbrief.tasks.artifacts import (
        load_cross_sections,
        load_route_points,
        save_cross_sections_ext,
    )

    cs = cross_sections if cross_sections is not None else load_cross_sections(pack_dir)
    route_points = load_route_points(pack_dir)
    if not cs or not route_points:
        return EcmwfExtendResult(cross_sections=cs or [])

    cover_until = window_end + timedelta(hours=max(flight_duration_hours, 1))
    run_ts, anchors, horizon = _ecmwf_run_coverage(
        cover_until, as_of_time=as_of_time, ecmwf_data_dir=ecmwf_data_dir,
    )
    if run_ts is None:
        logger.info("time-scan: no ECMWF run available to extend enrichment")
        return EcmwfExtendResult(cross_sections=cs)

    # Restrict the reported coverage to the anchors within the enrichment
    # window — outside it the enrichment won't decode, so grading there would
    # clamp to OM.
    lo = window_start - _ENRICH_MARGIN
    hi = cover_until + _ENRICH_MARGIN
    decoded = [a for a in anchors if lo <= a <= hi]

    # Decode-only ECMWF enrichment across the daylight span, at BACKGROUND
    # priority so it never starves a live briefing (issue #172 dispatcher).
    span_h = (cover_until - window_start).total_seconds() / 3600.0
    set_decode_priority(DecodePriority.BACKGROUND)
    _enrich_ecmwf(
        cs, [], route_points, window_start,
        flight_duration_hours=span_h,
        as_of_time=as_of_time,
        ecmwf_data_dir=ecmwf_data_dir,
    )
    propagate_all(cs, [], gfs_init=None)

    if persist:
        save_cross_sections_ext(pack_dir, cs)

    return EcmwfExtendResult(
        cross_sections=cs, run_ts=run_ts, decoded_valid_times=decoded, horizon=horizon,
    )


# ---------------------------------------------------------------------------
# Daylight window
# ---------------------------------------------------------------------------


def _sun_events_for(lat: float, lon: float, on: date) -> tuple[datetime | None, datetime | None]:
    """Return ``(morning, evening)`` sunrise/sunset on *on* for a point."""
    try:
        from euro_aip.utils.solar import sun_events

        ev = sun_events(lat, lon, on, depression=0.0)
        return ev.get("morning"), ev.get("evening")
    except Exception:
        logger.debug("time-scan: sun_events failed for (%s, %s)", lat, lon, exc_info=True)
        return None, None


def compute_daylight_window(
    route: RouteConfig,
    departure_time: datetime,
    flight_duration_hours: float,
) -> tuple[datetime, datetime, bool]:
    """Daylight departure window ``(start, latest_departure, clipped)``.

    Departure no earlier than sunrise at the origin, arrival no later than
    sunset at the destination — a day-VFR-friendly window (GA pilots typically
    flex within the day). Falls back to a generous ±6h around the planned time
    if solar computation is unavailable. ``clipped`` marks that daylight (not
    just the horizon) bounded the window.
    """
    dep = _aware(departure_time)
    day = dep.date()
    dur = timedelta(hours=max(flight_duration_hours, 0.0))

    o_lat, o_lon = route.origin.lat, route.origin.lon
    d_lat, d_lon = route.destination.lat, route.destination.lon
    sunrise_o, _ = _sun_events_for(o_lat, o_lon, day)
    _, sunset_d = _sun_events_for(d_lat, d_lon, day)

    if sunrise_o is None or sunset_d is None:
        # No reliable solar edges (polar day/night, or failure) → don't clip.
        return dep - timedelta(hours=6), dep + timedelta(hours=6), False

    start = _aware(sunrise_o)
    latest_departure = _aware(sunset_d) - dur
    if latest_departure <= start:
        # Flight barely fits the daylight window — search collapses to the
        # planned time; keep a hair of slack so the planned time is includable.
        return min(start, dep), max(start, dep), True
    return start, latest_departure, True


# ---------------------------------------------------------------------------
# Candidate grading + diff
# ---------------------------------------------------------------------------


def _candidate_valid_times(
    route_points, departure: datetime, flight_duration_hours: float,
) -> list[datetime]:
    """Per-route-point ETAs for a departure — the honesty-check inputs.

    Uses the same ``compute_interpolated_time`` the analysis stage uses, so the
    coverage check matches exactly the hours the re-grade will read.
    """
    from weatherbrief.tasks.analyze import compute_interpolated_time

    if not route_points:
        return [departure]
    total = route_points[-1].distance_from_origin_nm
    return [
        compute_interpolated_time(
            departure, flight_duration_hours, rp.distance_from_origin_nm, total,
        )
        for rp in route_points
    ]


def _grade_candidate(
    pack_dir: Path,
    route: RouteConfig,
    departure: datetime,
    cross_sections: list[RouteCrossSection],
    *,
    advisory_models: list[str],
    enabled_ids: set[str] | None,
    advisory_enabled: dict[str, bool] | None,
    user_params: dict | None,
    aggregation: AdvisoryAggregation | None,
    airports_db_path: str | None = None,
    airport_conditions_recompute=None,
    icing_method: str | None = None,
    cloud_method: str | None = None,
    convective_method: str | None = None,
    locale: str | None = None,
    cruise_speed_ias_kt: float | None = None,
) -> RouteAdvisoriesManifest | None:
    """Grade one departure against the (extended) cross-sections, no persistence."""
    from weatherbrief.tasks.advise import run_alt_from_pack

    res = run_alt_from_pack(
        pack_dir, departure, route,
        advisory_models=advisory_models,
        enabled_ids=enabled_ids,
        advisory_enabled=advisory_enabled,
        user_params=user_params,
        aggregation=aggregation,
        airports_db_path=airports_db_path,
        airport_conditions_recompute=airport_conditions_recompute,
        icing_method=icing_method,
        cloud_method=cloud_method,
        convective_method=convective_method,
        locale=locale,
        cruise_speed_ias_kt=cruise_speed_ias_kt,
        cross_sections=cross_sections,
        persist=False,
        detect_fronts=False,
    )
    return res.manifest


def _diff_manifests(
    baseline: RouteAdvisoriesManifest,
    candidate: RouteAdvisoriesManifest,
    scan_ids: set[str],
) -> tuple[list[str], list[str], int]:
    """Full-picture diff. Returns ``(improves, worsens, trigger_margin)``.

    ``improves``/``worsens`` span the **full** advisory set (so we never surface
    a window that fixed icing but quietly introduced a crosswind), while
    ``trigger_margin`` sums the net severity drop over the scan-class set only —
    the ranking objective. Scope (what triggers/ranks) stays separate from the
    objective (grade the whole picture).
    """
    bmap = {a.advisory_id: a.aggregate_status for a in baseline.advisories}
    improves: list[str] = []
    worsens: list[str] = []
    trigger_margin = 0
    for a in candidate.advisories:
        b = bmap.get(a.advisory_id)
        if b is None:
            continue
        # aggregate_status is an ``AdvisoryStatus`` str-enum; take ``.value`` so
        # the severity lookup keys on "red"/"amber"/"green" (str(enum) would be
        # "AdvisoryStatus.RED").
        bv = _SEV.get(getattr(b, "value", b))
        cv = _SEV.get(getattr(a.aggregate_status, "value", a.aggregate_status))
        if bv is None or cv is None:  # either side UNAVAILABLE → ignore
            continue
        d = bv - cv  # positive == improved (severity dropped)
        if d > 0:
            improves.append(a.advisory_id)
        elif d < 0:
            worsens.append(a.advisory_id)
        if a.advisory_id in scan_ids:
            trigger_margin += d
    return improves, worsens, trigger_margin


# ---------------------------------------------------------------------------
# Scan orchestration
# ---------------------------------------------------------------------------

_ECMWF_MODEL = "ecmwf"


def _load_baseline_manifest(pack_dir: Path) -> RouteAdvisoriesManifest | None:
    p = pack_dir / "route_advisories.json"
    if not p.exists():
        return None
    try:
        return RouteAdvisoriesManifest.model_validate_json(p.read_text())
    except Exception:
        logger.warning("time-scan: failed to load route_advisories.json", exc_info=True)
        return None


def _relevance_flagged(baseline: RouteAdvisoriesManifest, scan_ids: set[str]) -> list[str]:
    """Scan-class advisories that are RED/AMBER at the planned time (the firm gate)."""
    return [
        a.advisory_id
        for a in baseline.advisories
        if a.advisory_id in scan_ids and a.aggregate_status in ("red", "amber")
    ]


def run_time_scan(
    pack_dir: Path,
    route: RouteConfig,
    departure_time: datetime,
    *,
    flight_duration_hours: float,
    advisory_models: list[str] | None = None,
    enabled_ids: set[str] | None = None,
    advisory_enabled: dict[str, bool] | None = None,
    user_params: dict | None = None,
    aggregation: AdvisoryAggregation | None = None,
    airports_db_path: str | None = None,
    airport_conditions_recompute=None,
    icing_method: str | None = None,
    cloud_method: str | None = None,
    convective_method: str | None = None,
    locale: str | None = None,
    cruise_speed_ias_kt: float | None = None,
    preferred_departure_time: datetime | None = None,
    as_of_time: datetime | None = None,
    ecmwf_data_dir: Path | None = None,
) -> TimeWindowScan | None:
    """Run the ECMWF-only daylight timing scan, persisting ``time_options.json``.

    Returns the scan (also written to disk) or ``None`` when the scan is gated
    off (no scan-class advisory flagged, ECMWF absent, or no daylight/horizon
    coverage). Designed to run *after* ``run_advisories`` at BACKGROUND decode
    priority.
    """
    from weatherbrief.analysis.advisories import get_scan_class_ids
    from weatherbrief.tasks.advise import derive_assessment_from_advisories
    from weatherbrief.tasks.artifacts import load_route_points, save_time_options

    dep = _aware(departure_time)
    scan_ids = get_scan_class_ids()

    # 1. Relevance gate (firm) — need a scan-class advisory RED/AMBER now.
    baseline = _load_baseline_manifest(pack_dir)
    if baseline is None:
        return None
    flagged = _relevance_flagged(baseline, scan_ids)
    if not flagged:
        logger.info("time-scan: no scan-class advisory flagged — skipping")
        return None
    if _ECMWF_MODEL not in [m.lower() for m in baseline.models]:
        logger.info("time-scan: ECMWF not in fetched models — skipping")
        return None

    # 2. Daylight window (departure bounds).
    day_start, day_end, daylight_clipped = compute_daylight_window(
        route, dep, flight_duration_hours,
    )

    # 3. Extend ECMWF enrichment across the daylight window (the real cost).
    ext = extend_ecmwf_enrichment_daylight(
        pack_dir,
        window_start=day_start,
        window_end=day_end,
        flight_duration_hours=flight_duration_hours,
        as_of_time=as_of_time,
        ecmwf_data_dir=ecmwf_data_dir,
    )
    cov = ext.coverage
    if cov is None:
        logger.info("time-scan: no decoded ECMWF coverage — skipping")
        return None
    cov_lo, cov_hi = cov
    dur = timedelta(hours=max(flight_duration_hours, 0.0))

    # 4. Native-cadence candidate grid, clipped to daylight ∩ ECMWF horizon.
    #    A candidate is honest only if its whole flight fits the decoded window.
    horizon_clipped = ext.horizon is not None and (day_end + dur) > ext.horizon
    grid_lo = max(day_start, cov_lo)
    grid_hi = min(day_end, cov_hi - dur)
    route_points = load_route_points(pack_dir)

    grid: list[datetime] = [
        a for a in ext.decoded_valid_times if grid_lo <= a <= grid_hi
    ]
    # Always include the planned time (baseline) and, if in coverage, the pinned
    # preferred time — even if they're between native anchors (interpolated,
    # still honest inside coverage).
    graded_times: dict[datetime, dict] = {}

    def _consider(t: datetime, *, is_baseline=False, is_preferred=False) -> None:
        t = _aware(t)
        vts = _candidate_valid_times(route_points, t, flight_duration_hours)
        if not ext.covers(vts):
            return  # honesty guardrail: refuse hours we didn't decode
        entry = graded_times.setdefault(t, {"is_baseline": False, "is_preferred": False, "vts": vts})
        entry["is_baseline"] = entry["is_baseline"] or is_baseline
        entry["is_preferred"] = entry["is_preferred"] or is_preferred

    _consider(dep, is_baseline=True)
    if preferred_departure_time is not None:
        _consider(preferred_departure_time, is_preferred=True)
    for t in grid[:_MAX_GRID]:
        _consider(t)

    if len(graded_times) <= 1:
        logger.info("time-scan: no gradable candidates besides the planned time")
        # Still emit a (candidate-less) scan so the UI can say "no better window".

    # 5. Grade every considered time ECMWF-only against the extended sections.
    grade_kwargs = dict(
        advisory_models=[_ECMWF_MODEL],
        enabled_ids=enabled_ids,
        advisory_enabled=advisory_enabled,
        user_params=user_params,
        aggregation=aggregation,
        airports_db_path=airports_db_path,
        airport_conditions_recompute=airport_conditions_recompute,
        icing_method=icing_method,
        cloud_method=cloud_method,
        convective_method=convective_method,
        locale=locale,
        cruise_speed_ias_kt=cruise_speed_ias_kt,
    )

    baseline_manifest = _grade_candidate(
        pack_dir, route, dep, ext.cross_sections, **grade_kwargs,
    )
    if baseline_manifest is None:
        logger.warning("time-scan: baseline ECMWF grade failed — skipping")
        return None
    base_assess, base_reason = derive_assessment_from_advisories(baseline_manifest)

    candidates: list[TimeCandidate] = []
    baseline_row: TimeCandidate | None = None
    preferred_row: TimeCandidate | None = None
    improving: list[TimeCandidate] = []

    for t, meta in graded_times.items():
        is_base = meta["is_baseline"]
        if is_base:
            manifest = baseline_manifest  # reuse — same time, same grade
        else:
            manifest = _grade_candidate(pack_dir, route, t, ext.cross_sections, **grade_kwargs)
        if manifest is None:
            continue
        improves, worsens, margin = _diff_manifests(baseline_manifest, manifest, scan_ids)
        assess, reason = derive_assessment_from_advisories(manifest)
        shift_h = round((t - dep).total_seconds() / 3600.0, 2)
        row = TimeCandidate(
            departure_time=t,
            departure_shift_hours=shift_h,
            valid_times=meta["vts"],
            ecmwf_assessment=assess,
            ecmwf_assessment_reason=reason,
            improves=improves,
            worsens=worsens,
            margin=float(margin),
            confidence="ecmwf_only",
            is_preferred=meta["is_preferred"],
            is_baseline=is_base,
        )
        if is_base:
            baseline_row = row
        elif meta["is_preferred"]:
            preferred_row = row
        elif margin >= _MIN_TRIGGER_MARGIN:
            improving.append(row)

    # 6. Rank improving windows: best margin first, ties → nearest the
    #    preferred time (else nearest the planned time). Cap for the artifact.
    anchor = _aware(preferred_departure_time) if preferred_departure_time else dep

    def _rank_key(r: TimeCandidate) -> tuple:
        return (-r.margin, abs((r.departure_time - anchor).total_seconds()))

    improving.sort(key=_rank_key)
    improving = improving[:_MAX_IMPROVING_KEPT]

    # Assemble candidate list: baseline first, then preferred (if distinct), then windows.
    if baseline_row is not None:
        candidates.append(baseline_row)
    if preferred_row is not None:
        candidates.append(preferred_row)
    candidates.extend(improving)

    scan = TimeWindowScan(
        baseline=TimeScanBaseline(
            departure_time=dep,
            ecmwf_assessment=base_assess,
            ecmwf_assessment_reason=base_reason,
        ),
        window=TimeScanWindow(
            start=grid_lo,
            end=grid_hi,
            cadence_hours=1.0,
            daylight_clipped=daylight_clipped,
            horizon_clipped=horizon_clipped,
            day_flex="day",
        ),
        candidates=candidates,
        scan_flagged=flagged,
        models=[m for m in baseline.models if m.lower() != "best_match"],
        ecmwf_run_ts=ext.run_ts,
        generated_at=datetime.now(timezone.utc),
        cross_section_ext=True,
    )
    save_time_options(pack_dir, scan)
    logger.info(
        "time-scan: %d candidate(s) (%d improving) over daylight window %s–%s",
        len(candidates), len(improving), grid_lo.isoformat(), grid_hi.isoformat(),
    )
    return scan


_SCAN_EXECUTOR = None


def _scan_executor():
    """Lazily-created single-worker executor for detached background scans.

    ``max_workers=1`` serialises scans across a fleet of flights so a burst of
    refreshes can't stampede the decode pool. Threads are joined at interpreter
    exit (so a scan in flight isn't silently dropped on shutdown).
    """
    global _SCAN_EXECUTOR
    if _SCAN_EXECUTOR is None:
        import concurrent.futures

        _SCAN_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="time-scan",
        )
    return _SCAN_EXECUTOR


def submit_time_scan_background(pack_dir: Path, route: RouteConfig,
                                departure_time: datetime, **kwargs) -> None:
    """Fire-and-forget: run :func:`run_time_scan` on a detached background thread.

    The briefing pipeline calls this *after* the visible artifacts are on disk
    and returns immediately, so the refresh completes as usual. The scan reads
    everything it needs from ``pack_dir`` on disk, writes ``time_options.json``
    when done, and the client picks it up on its next ``GET .../time-options``
    (i.e. next time the briefing is viewed). Never blocks or fails the caller.
    """
    def _run() -> None:
        try:
            run_time_scan(pack_dir, route, departure_time, **kwargs)
        except Exception:
            logger.warning("Background time-scan failed (non-fatal)", exc_info=True)

    try:
        _scan_executor().submit(_run)
    except Exception:
        logger.warning("Could not submit background time-scan", exc_info=True)


def confirm_candidate(
    pack_dir: Path,
    route: RouteConfig,
    candidate_time: datetime,
    *,
    data_dir: Path,
    advisory_models: list[str] | None = None,
    enabled_ids: set[str] | None = None,
    advisory_enabled: dict[str, bool] | None = None,
    user_params: dict | None = None,
    aggregation: AdvisoryAggregation | None = None,
    airports_db_path: str | None = None,
    airport_conditions_recompute=None,
    icing_method: str | None = None,
    cloud_method: str | None = None,
    convective_method: str | None = None,
    locale: str | None = None,
    cruise_speed_ias_kt: float | None = None,
    as_of_time: datetime | None = None,
) -> TimeConfirmation | None:
    """Multi-model confirm of one candidate (the deferred, gated-on-tap cost).

    That candidate's ICON/GFS GRIB is off the flight window, so this runs the
    full GFS+ICON+ECMWF enrichment at the candidate's flight window (the
    download+decode we gate on demonstrated intent), then grades the full
    advisory model set and diffs against the planned time's multi-model grade
    (the pack's ``route_advisories.json``).

    ``better_than_baseline=False`` is the on-brand downgrade outcome — a tapped
    suggestion that ICON/GFS reveal isn't actually better.
    """
    from weatherbrief.analysis.advisories import get_scan_class_ids
    from weatherbrief.fetch.grib import DecodePriority, enrich_forecasts
    from weatherbrief.tasks.advise import (
        derive_assessment_from_advisories,
        run_alt_from_pack,
    )
    from weatherbrief.tasks.artifacts import (
        load_cross_sections,
        load_cross_sections_ext,
        load_route_points,
    )

    cand = _aware(candidate_time)
    planned = _load_baseline_manifest(pack_dir)
    if planned is None:
        return None

    # Start from the daylight-extended sections if present (ECMWF already there),
    # else the primary sections. Enrich GFS+ICON at the candidate window.
    cs = load_cross_sections_ext(pack_dir) or load_cross_sections(pack_dir)
    route_points = load_route_points(pack_dir)
    if not cs or not route_points:
        return None

    try:
        enrich_forecasts(
            cs, [], route_points, cand,
            data_dir=data_dir,
            flight_duration_hours=route.flight_duration_hours,
            as_of_time=as_of_time,
            priority=DecodePriority.BACKGROUND,
        )
    except Exception:
        logger.warning("time-scan confirm: multi-model enrichment failed", exc_info=True)
        # Fall through — we can still grade on whatever is enriched (degraded),
        # but honesty says a failed confirm should surface as unavailable.
        return None

    res = run_alt_from_pack(
        pack_dir, cand, route,
        advisory_models=advisory_models,   # None → full default multi-model set
        enabled_ids=enabled_ids,
        advisory_enabled=advisory_enabled,
        user_params=user_params,
        aggregation=aggregation,
        airports_db_path=airports_db_path,
        airport_conditions_recompute=airport_conditions_recompute,
        icing_method=icing_method,
        cloud_method=cloud_method,
        convective_method=convective_method,
        locale=locale,
        cruise_speed_ias_kt=cruise_speed_ias_kt,
        cross_sections=cs,
        persist=False,
        detect_fronts=False,
    )
    if res.manifest is None:
        return None

    scan_ids = get_scan_class_ids()
    improves, worsens, margin = _diff_manifests(planned, res.manifest, scan_ids)
    assess, reason = derive_assessment_from_advisories(res.manifest)
    better = margin >= _MIN_TRIGGER_MARGIN

    return TimeConfirmation(
        models_checked=res.manifest.models,
        assessment=assess,
        assessment_reason=reason,
        better_than_baseline=better,
        improves=improves,
        worsens=worsens,
        confirmed_at=datetime.now(timezone.utc),
    )
