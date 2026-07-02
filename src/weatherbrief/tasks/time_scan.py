"""Timing-scenario scan — grade candidate departure times (Flexibility).

Implements ``designs/plans/timing-scenario-plan.md``. The pilot opts in per
flight via ``Flight.flexibility``; the scan grades other departure times and
surfaces "a smoother departure may exist" — an **attention-director, never a
verdict**. User intent is the compute gate: when a scan mode is set we scan
even an all-green flight ("no better window found" is a useful answer).

Slice 1 (this module today) — the **in-window free tier**: candidates whose
whole (shifted) flight fits inside every fetched model's enriched coverage are
graded **multi-model, for free** (pure re-analysis of saved pack data via
``run_alt_from_pack``) and labelled ``confirmed_in_window``. Hours outside
coverage are **refused, never clamp-graded** (the honesty invariant: a naive
``at_time()`` re-grade past the enrichment window would silently return
OM-clamped values labelled as the GRIB model). Slice 2 adds the daylight ECMWF
enrichment extension (decode-only, ephemeral) that turns refused hours into
``ecmwf_only`` provisional candidates; slice 3 adds the on-tap multi-model
confirm; slice 4 adds the ±day OM fetch.

Coverage is **rule-window ∩ data-marker** per model per route point:

* the fetch-window *rule* (ECMWF ±3h margin; GFS/ICON forward flight window)
  bounds the trailing smear — GFS CLW/ICMR overlays and cloud diagnostics
  forward-fill past the last native anchor (``fetch/grib/fill.py``), so data
  presence alone would over-claim evening hours;
* the data *marker* (pressure-level count above the OM baseline for
  ECMWF/ICON — the same anchor detector ``_interp_levels_hourly`` uses —
  CLW/diagnostics presence for GFS) catches enrichment that failed inside the
  rule window.

OM-only models (MétéoFrance/UKMO/GEM) are ``uniform``: their fidelity doesn't
change across the fetched day (icing/cloud inputs are synthesized at the
flight window too), so any hour of the fetched day is as honest as the
planned one.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Callable

from weatherbrief.models import (
    AdvisoryAggregation,
    ModelCoverage,
    ModelSource,
    RouteAdvisoriesManifest,
    RouteConfig,
    RouteCrossSection,
    TimeCandidate,
    TimeScanBaseline,
    TimeScanWindow,
    TimeWindowScan,
)

logger = logging.getLogger(__name__)

# ECMWF enrichment margin around the flight window — mirrors ``margin`` in
# ``fetch/grib/__init__.py`` (``_enrich_ecmwf_inner`` step filter). If that
# constant changes, coverage here goes conservative/stale rather than wrong
# (the data marker still bounds what we actually grade).
_ECMWF_ENRICH_MARGIN = timedelta(hours=3)

# Candidate grid cap per scan — a fleet of flights can't stampede the
# analysis stage. Daylight rarely exceeds this at hourly cadence.
_MAX_GRID = 24

# Severity ranking for the improve/worsen diff.
_SEV = {"green": 0, "amber": 1, "red": 2}

# A candidate surfaces only if the net severity drop over the scan-class set
# reaches this margin with no offsetting scan-class regression (decision D).
_MIN_MARGIN = 1

# Improving windows kept in the artifact (the UI caps display at ~3).
_MAX_IMPROVING_KEPT = 5

#: GRIB-enriched models — coverage is windowed; everything else is uniform OM.
_GRIB_MODELS = {ModelSource.ECMWF, ModelSource.GFS, ModelSource.ICON}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Enrichment coverage (the honesty guardrail's data source)
# ---------------------------------------------------------------------------


def _hour_time(h) -> datetime:
    return _aware(h.time)


def _gfs_marker(h) -> bool:
    """GFS enrichment overlays CLW/ICMR onto OM levels and attaches cloud
    diagnostics — the level *count* doesn't change, so presence is the marker."""
    if h.nwp_cloud_diagnostics is not None:
        return True
    return any(
        pl.cloud_liquid_water_kg_kg is not None or pl.ice_mixing_ratio_kg_kg is not None
        for pl in h.pressure_levels
    )


def _rule_window(
    model: ModelSource, departure: datetime, duration_hours: float,
) -> tuple[datetime, datetime]:
    """The span the fetch stage *intended* to enrich for this model.

    ECMWF filters steps to flight window ±3h; GFS/ICON fetch forward hours
    from ``floor(departure)`` through ``departure + ceil(duration) (+1 when
    departure has minutes)`` (``compute_flight_window_hours`` /
    ``compute_icon_eu_flight_window_hours``).
    """
    dep = _aware(departure)
    dep_end = dep + timedelta(hours=max(duration_hours, 1.0))
    if model == ModelSource.ECMWF:
        return dep - _ECMWF_ENRICH_MARGIN, dep_end + _ECMWF_ENRICH_MARGIN
    floor = dep.replace(minute=0, second=0, microsecond=0)
    import math

    extra = 1 if dep.minute > 0 else 0
    hi = dep + timedelta(hours=max(1, math.ceil(max(duration_hours, 0.0)) + extra))
    return floor, hi


def compute_model_coverage(
    cross_sections: list[RouteCrossSection],
    departure_time: datetime,
    flight_duration_hours: float,
) -> tuple[dict[str, list[tuple[datetime, datetime] | None]], list[ModelCoverage]]:
    """Per-model, per-route-point gradable spans from the saved pack.

    Returns ``(per_point, summary)``: ``per_point[model][i]`` is the honest
    ``(lo, hi)`` valid-time span at route point ``i`` (None = no coverage at
    that point), and ``summary`` is the per-model intersection across points
    for the artifact.
    """
    per_point: dict[str, list[tuple[datetime, datetime] | None]] = {}
    summary: list[ModelCoverage] = []

    for cs in cross_sections:
        model = cs.model
        name = model.value
        spans: list[tuple[datetime, datetime] | None] = []

        if model not in _GRIB_MODELS:
            # OM-only model: fidelity is uniform across the fetched day.
            for wf in cs.point_forecasts:
                if not wf.hourly:
                    spans.append(None)
                    continue
                times = sorted(_hour_time(h) for h in wf.hourly)
                spans.append((times[0], times[-1]))
            per_point[name] = spans
            agg = _intersect_spans(spans)
            summary.append(
                ModelCoverage(
                    model=name,
                    start=agg[0] if agg else None,
                    end=agg[1] if agg else None,
                    uniform=True,
                )
            )
            continue

        rule_lo, rule_hi = _rule_window(model, departure_time, flight_duration_hours)
        n_uniform = 0
        for wf in cs.point_forecasts:
            hours = sorted(wf.hourly, key=_hour_time)
            if not hours:
                spans.append(None)
                continue
            if model == ModelSource.GFS:
                marked = [_hour_time(h) for h in hours if _gfs_marker(h)]
            else:
                # ECMWF / ICON replace the level list — the codebase's own
                # anchor detector: count above the day's OM baseline.
                counts = [len(h.pressure_levels) for h in hours]
                baseline = min(counts)
                marked = [
                    _hour_time(h)
                    for h, c in zip(hours, counts)
                    if c > baseline
                ]
            if not marked:
                # Fidelity parity: this point was never GRIB-enriched (fetch
                # gap), so the baseline graded it on OM data too — any hour of
                # the fetched day is exactly as honest here as the planned one.
                spans.append((_hour_time(hours[0]), _hour_time(hours[-1])))
                n_uniform += 1
                continue
            lo = max(marked[0], rule_lo)
            hi = min(marked[-1], rule_hi)
            spans.append((lo, hi) if lo <= hi else None)

        per_point[name] = spans
        agg = _intersect_spans(spans)
        summary.append(
            ModelCoverage(
                model=name,
                start=agg[0] if agg else None,
                end=agg[1] if agg else None,
                uniform=n_uniform == len(cs.point_forecasts),
            )
        )

    return per_point, summary


def _intersect_spans(
    spans: list[tuple[datetime, datetime] | None],
) -> tuple[datetime, datetime] | None:
    real = [s for s in spans if s is not None]
    if not real or len(real) != len(spans):
        return None
    lo = max(s[0] for s in real)
    hi = min(s[1] for s in real)
    return (lo, hi) if lo <= hi else None


def candidate_valid_times(
    route_points, departure: datetime, flight_duration_hours: float,
) -> list[datetime]:
    """Per-route-point ETAs for a departure — the coverage-check inputs.

    Uses the same ``compute_interpolated_time`` the analysis stage uses, so
    the check matches exactly the hours the re-grade will read."""
    from weatherbrief.tasks.analyze import compute_interpolated_time

    dep = _aware(departure)
    if not route_points:
        return [dep]
    total = route_points[-1].distance_from_origin_nm
    return [
        _aware(
            compute_interpolated_time(
                dep, flight_duration_hours, rp.distance_from_origin_nm, total,
            )
        )
        for rp in route_points
    ]


def covers(
    per_point: dict[str, list[tuple[datetime, datetime] | None]],
    models: list[str],
    valid_times: list[datetime],
) -> bool:
    """True iff every model grades every per-point ETA inside its honest span."""
    for m in models:
        spans = per_point.get(m)
        if spans is None:
            return False
        for i, t in enumerate(valid_times):
            span = spans[i] if i < len(spans) else None
            if span is None or not (span[0] <= t <= span[1]):
                return False
    return True


# ---------------------------------------------------------------------------
# Daylight window
# ---------------------------------------------------------------------------


def _sun_events_for(lat: float, lon: float, on: date):
    try:
        from euro_aip.utils.solar import sun_events

        ev = sun_events(lat, lon, on)
        return ev.get("morning"), ev.get("evening")
    except Exception:
        logger.debug("time-scan: sun_events failed for (%s, %s)", lat, lon, exc_info=True)
        return None, None


def compute_daylight_window(
    route: RouteConfig,
    departure_time: datetime,
    flight_duration_hours: float,
    *,
    day_shift: int = 0,
) -> tuple[datetime, datetime, bool]:
    """Daylight departure window ``(earliest, latest, clipped)`` on the target
    day shifted by ``day_shift`` (±1 for prev/next-day modes).

    Departure no earlier than sunrise at the origin, arrival no later than
    sunset at the destination — day-VFR-friendly. Falls back to ±6h around the
    (shifted) planned time when solar edges are unavailable (polar day/night).
    """
    dep = _aware(departure_time) + timedelta(days=day_shift)
    day = dep.date()
    dur = timedelta(hours=max(flight_duration_hours, 0.0))

    sunrise_o, _ = _sun_events_for(route.origin.lat, route.origin.lon, day)
    _, sunset_d = _sun_events_for(route.destination.lat, route.destination.lon, day)

    if sunrise_o is None or sunset_d is None:
        return dep - timedelta(hours=6), dep + timedelta(hours=6), False

    start = _aware(sunrise_o)
    latest = _aware(sunset_d) - dur
    if latest <= start:
        # Flight barely fits daylight — window collapses toward the planned time.
        return min(start, dep), max(start, dep), True
    return start, latest, True


# ---------------------------------------------------------------------------
# Grading + diff
# ---------------------------------------------------------------------------


def _diff_manifests(
    baseline: RouteAdvisoriesManifest,
    candidate: RouteAdvisoriesManifest,
    scan_ids: set[str],
) -> tuple[list[str], list[str], int]:
    """Full-picture diff. Returns ``(improves, worsens, margin)``.

    ``improves``/``worsens`` span the **full** advisory set (never surface a
    window that fixed icing but quietly introduced a crosswind); ``margin``
    sums the net severity drop over the scan-class set only — the ranking
    objective (scope vs objective stay separate).
    """
    bmap = {a.advisory_id: a.aggregate_status for a in baseline.advisories}
    improves: list[str] = []
    worsens: list[str] = []
    margin = 0
    for a in candidate.advisories:
        b = bmap.get(a.advisory_id)
        if b is None:
            continue
        # aggregate_status is a str-enum; ``getattr(x, "value", x)`` keys the
        # severity table on "red"/"amber"/"green" for enum and plain-str alike.
        bv = _SEV.get(getattr(b, "value", b))
        cv = _SEV.get(getattr(a.aggregate_status, "value", a.aggregate_status))
        if bv is None or cv is None:  # either side unavailable → not comparable
            continue
        d = bv - cv  # positive == improved (severity dropped)
        if d > 0:
            improves.append(a.advisory_id)
        elif d < 0:
            worsens.append(a.advisory_id)
        if a.advisory_id in scan_ids:
            margin += d
    return improves, worsens, margin


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------


def run_time_scan(
    pack_dir: Path,
    route: RouteConfig,
    departure_time: datetime,
    *,
    flexibility: str,
    alt_departure_time: datetime | None = None,
    advisory_models: list[str] | None = None,
    enabled_ids: set[str] | None = None,
    advisory_enabled: dict[str, bool] | None = None,
    user_params: dict | None = None,
    aggregation: AdvisoryAggregation | None = None,
    airports_db_path: str | None = None,
    airport_conditions_recompute: Callable | None = None,
    icing_method: str | None = None,
    cloud_method: str | None = None,
    convective_method: str | None = None,
    locale: str | None = None,
    cruise_speed_ias_kt: float | None = None,
) -> TimeWindowScan | None:
    """Grade the flight's Flexibility scenarios against the saved pack.

    Returns the scan (caller persists it as ``time_options.json``) or ``None``
    when there is nothing to do (``flexibility="none"``, or missing pack
    data). Never raises for data problems — refused candidates are recorded on
    the scan, and hard failures return ``None`` after logging.

    Slice-1 scope: every graded candidate is ``confirmed_in_window``
    (multi-model, free). Hours outside coverage land in ``refused_times`` —
    slice 2's ECMWF daylight extension will graduate them to ``ecmwf_only``.
    ``ecmwf_run_ts`` stays ``None`` until slice 2 reads the run off disk (the
    decision-H reuse key matters only once scans cost decode time).
    """
    from weatherbrief.analysis.advisories.registry import get_scan_class_ids
    from weatherbrief.tasks.advise import (
        _compute_advisory_model_names,
        derive_assessment_from_advisories,
        run_alt_from_pack,
    )
    from weatherbrief.tasks.artifacts import load_cross_sections, load_route_points

    if flexibility == "none":
        return None

    t0 = perf_counter()
    dep = _aware(departure_time)
    duration = route.flight_duration_hours

    cross_sections = load_cross_sections(pack_dir)
    route_points = load_route_points(pack_dir)
    if not cross_sections or not route_points:
        logger.info("time-scan: missing cross-sections/route points — skipping")
        return None

    model_names = [cs.model.value for cs in cross_sections]
    graded_models = _compute_advisory_model_names(model_names, advisory_models)
    per_point, coverage_summary = compute_model_coverage(
        cross_sections, dep, duration,
    )

    grade_kwargs = dict(
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
    )

    def _grade(t: datetime, *, persist: bool, detect_fronts: bool):
        res = run_alt_from_pack(
            pack_dir, t, route,
            persist=persist,
            detect_fronts=detect_fronts,
            **grade_kwargs,
        )
        return res.manifest

    # --- Baseline: the planned departure through the SAME path, so diffs
    # compare like with like (and shift=0 must reproduce the briefing grades).
    baseline_manifest = _grade(dep, persist=False, detect_fronts=False)
    if baseline_manifest is None:
        logger.warning("time-scan: baseline grade failed — skipping")
        return None
    base_assess, base_reason = derive_assessment_from_advisories(baseline_manifest)
    scan_ids = get_scan_class_ids()

    # --- Candidate departure times by mode.
    window_model: TimeScanWindow | None = None
    grid: list[datetime] = []
    if flexibility in ("same_day", "prev_day", "next_day"):
        day_shift = {"same_day": 0, "prev_day": -1, "next_day": 1}[flexibility]
        w_lo, w_hi, clipped = compute_daylight_window(
            route, dep, duration, day_shift=day_shift,
        )
        window_model = TimeScanWindow(
            start=w_lo, end=w_hi, flexibility=flexibility, daylight_clipped=clipped,
        )
        # Hourly grid across the window (pack data is hourly — its native
        # cadence). Slice 2 snaps to decoded ECMWF anchors instead.
        t = w_lo.replace(minute=0, second=0, microsecond=0)
        if t < w_lo:
            t += timedelta(hours=1)
        while t <= w_hi and len(grid) < _MAX_GRID:
            grid.append(t)
            t += timedelta(hours=1)

    # Consideration order: alternate first (pinned), then the grid.
    considered: list[tuple[datetime, bool]] = []  # (time, is_alternate)
    if alt_departure_time is not None:
        considered.append((_aware(alt_departure_time), True))
    for t in grid:
        if not any(abs((t - c).total_seconds()) < 60 for c, _ in considered):
            considered.append((t, False))

    candidates: list[TimeCandidate] = []
    refused: list[datetime] = []
    baseline_vts = candidate_valid_times(route_points, dep, duration)
    baseline_row = TimeCandidate(
        departure_time=dep,
        departure_shift_hours=0.0,
        valid_times=baseline_vts,
        assessment=base_assess,
        assessment_reason=base_reason,
        models_used=graded_models,
        confidence="confirmed_in_window",
        is_baseline=True,
    )

    improving: list[TimeCandidate] = []
    alternate_row: TimeCandidate | None = None

    for t, is_alt in considered:
        if abs((t - dep).total_seconds()) < 60:
            if is_alt:
                alternate_row = baseline_row.model_copy(update={"is_alternate": True})
            continue
        vts = candidate_valid_times(route_points, t, duration)
        if not covers(per_point, graded_models, vts):
            # The honesty invariant: never grade an hour whose enriched fields
            # aren't actually there — at_time() would silently clamp to the
            # window edge and label OM values as the GRIB model.
            refused.append(t)
            continue
        # The pinned alternate keeps feeding route_advisories_alt.json (the
        # existing planned↔alt web UI) and re-runs alt front detection like the
        # retired in-pipeline stage did.
        manifest = _grade(t, persist=is_alt, detect_fronts=is_alt)
        if manifest is None:
            continue
        improves, worsens, margin = _diff_manifests(baseline_manifest, manifest, scan_ids)
        assess, reason = derive_assessment_from_advisories(manifest)
        row = TimeCandidate(
            departure_time=t,
            departure_shift_hours=round((t - dep).total_seconds() / 3600.0, 2),
            valid_times=vts,
            assessment=assess,
            assessment_reason=reason,
            models_used=graded_models,
            improves=improves,
            worsens=worsens,
            margin=float(margin),
            confidence="confirmed_in_window",
            is_alternate=is_alt,
        )
        if is_alt:
            alternate_row = row
        elif margin >= _MIN_MARGIN and not worsens:
            improving.append(row)

    # Rank: best margin first, ties broken by proximity to the alternate time
    # (the pilot's stated preference) or else the planned time.
    anchor = _aware(alt_departure_time) if alt_departure_time else dep
    improving.sort(
        key=lambda r: (-r.margin, abs((r.departure_time - anchor).total_seconds())),
    )
    improving = improving[:_MAX_IMPROVING_KEPT]

    out_candidates = [baseline_row]
    if alternate_row is not None:
        out_candidates.append(alternate_row)
    out_candidates.extend(improving)

    scan = TimeWindowScan(
        flexibility=flexibility,  # type: ignore[arg-type]
        baseline=TimeScanBaseline(
            departure_time=dep,
            assessment=base_assess,
            assessment_reason=base_reason,
            models_used=graded_models,
        ),
        window=window_model,
        candidates=out_candidates,
        refused_times=refused,
        coverage=coverage_summary,
        models=model_names,
        ecmwf_run_ts=None,
        generated_at=datetime.now(timezone.utc),
    )
    logger.info(
        "time-scan[%s]: %d graded (%d improving), %d refused in %.1fs",
        flexibility, len(out_candidates), len(improving), len(refused),
        perf_counter() - t0,
    )
    return scan
