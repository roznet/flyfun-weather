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

from weatherbrief.fetch.grib import ECMWF_FLIGHT_WINDOW_MARGIN
from weatherbrief.models import (
    AdvisoryAggregation,
    Disposition,
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

# ECMWF enrichment margin around the flight window — the same constant the
# enrichment step filter uses, so the coverage rule window can't drift from
# what was actually decoded (imported at the top with the other deps).
_ECMWF_ENRICH_MARGIN = ECMWF_FLIGHT_WINDOW_MARGIN

# Candidate grid cap per scan — a fleet of flights can't stampede the
# analysis stage. Daylight rarely exceeds this at hourly cadence.
_MAX_GRID = 24

# Severity ranking for the improve/worsen diff.
_SEV = {"green": 0, "amber": 1, "red": 2}

# A candidate is "improving" only if the net severity drop over the scan-class
# set reaches this margin with no offsetting regression (decision D). This is
# now the disposition threshold, not a persistence filter — every graded
# candidate is kept and tagged (#434).
_MIN_MARGIN = 1

# Rank order for persisting candidates: improving first, then neutral, then
# worse — so the client can slice "same-or-better" off the front and reveal
# ``worse`` rows behind a "show all" toggle (#434).
_DISPOSITION_RANK = {"improving": 0, "neutral": 1, "worse": 2}


def _disposition(margin: float, worsens: list[str]) -> Disposition:
    """Classify a candidate vs the like-coverage baseline (#434 taxonomy).

    ``worsens`` spans the **full** advisory set (never call a window that
    quietly introduced a crosswind "improving"), so any regression makes it
    ``worse``. Otherwise it's ``improving`` when it clears the ranking margin,
    else ``neutral``.

    The ``margin < 0`` clause is a defensive backstop: ``_diff_manifests``
    already appends every negative-delta advisory to ``worsens`` before the
    scan-class margin sum, so a negative ``margin`` cannot occur with an empty
    ``worsens`` today. It's kept because "negative margin ⇒ worse" is the
    taxonomy the issue states, so the classifier stays correct even if the
    diff's ordering is ever refactored.
    """
    if worsens or margin < 0:
        return "worse"
    if margin >= _MIN_MARGIN:
        return "improving"
    return "neutral"

#: GRIB-enriched models — coverage is windowed; everything else is uniform OM.
_GRIB_MODELS = {ModelSource.ECMWF, ModelSource.GFS, ModelSource.ICON}

_ECMWF_MODEL = ModelSource.ECMWF.value


def _pack_fetched_at(pack_dir: Path) -> datetime | None:
    """The pack's fetch time — pins the extension to the run the briefing used."""
    from weatherbrief.tasks.artifacts import load_fetch_meta

    meta = load_fetch_meta(pack_dir)
    if not meta or not meta.get("fetched_at"):
        return None
    try:
        return datetime.fromisoformat(meta["fetched_at"])
    except ValueError:
        return None


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
# ECMWF daylight enrichment extension (slice 2 — the plan's v1 primitive)
# ---------------------------------------------------------------------------


def extend_ecmwf_daylight(
    cross_sections: list[RouteCrossSection],
    route_points,
    window_start: datetime,
    window_end: datetime,
    flight_duration_hours: float,
    *,
    as_of_time: datetime | None = None,
) -> tuple[int | None, list[tuple[datetime, datetime] | None]]:
    """Decode the daylight ECMWF fhours onto the loaded cross-sections.

    Decode-only (local ECPDS disk, no download) at BACKGROUND priority so a
    live briefing is never starved. Reuses the production enrichment machinery
    by fabricating a wide "flight window": ``_enrich_ecmwf`` filters steps to
    ``[departure − 3h, departure + duration + 3h]``, so passing the window
    span as the duration decodes every step across daylight **contiguously**
    (required — accumulated fields tp/sf/cp are step-differenced across
    consecutive processed steps; cherry-picked hours would corrupt them).

    Mutates only the ECMWF section of the **in-memory** list — decision G:
    nothing here is ever persisted; ``time_options.json`` is the only artifact
    the scan writes.

    ``as_of_time`` should be the pack's ``fetched_at`` so the extension picks
    the SAME run the briefing used — otherwise a run that landed between the
    briefing and the scan would silently mix runs inside one grade.

    Returns ``(run_ts, per_point_spans)`` — the ECMWF init unix timestamp (the
    decision-H staleness key) and the recomputed honest per-point coverage.
    ``(None, [])`` when ECMWF is absent or no run covers the window.
    """
    from weatherbrief.fetch.grib import (
        DecodePriority,
        _enrich_ecmwf,
        set_decode_priority,
    )
    from weatherbrief.fetch.grib.fill import propagate_all

    ecmwf_sections = [cs for cs in cross_sections if cs.model == ModelSource.ECMWF]
    if not ecmwf_sections:
        return None, []

    w_lo = _aware(window_start)
    dep_end = _aware(window_end) + timedelta(hours=max(flight_duration_hours, 1.0))
    span_h = max((dep_end - w_lo).total_seconds() / 3600.0, 1.0)

    set_decode_priority(DecodePriority.BACKGROUND)
    run_ts = _enrich_ecmwf(
        cross_sections, [], route_points, w_lo,
        flight_duration_hours=span_h,
        as_of_time=as_of_time,
    )
    if run_ts is None:
        return None, []
    # Interpolate any between-anchor gap hours (only matters past the 1h
    # cadence horizon, where ECMWF steps at 3h/6h). ECMWF-only — the other
    # models' sections were already propagated at pack time.
    propagate_all(ecmwf_sections, [], gfs_init=None)

    # Recompute the honest ECMWF coverage from the now-extended data, using
    # the extended rule window (the fabricated flight window above).
    rule_lo = w_lo - _ECMWF_ENRICH_MARGIN
    rule_hi = dep_end + _ECMWF_ENRICH_MARGIN
    spans: list[tuple[datetime, datetime] | None] = []
    for wf in ecmwf_sections[0].point_forecasts:
        hours = sorted(wf.hourly, key=_hour_time)
        if not hours:
            spans.append(None)
            continue
        counts = [len(h.pressure_levels) for h in hours]
        baseline = min(counts)
        marked = [_hour_time(h) for h, c in zip(hours, counts) if c > baseline]
        if not marked:
            spans.append((_hour_time(hours[0]), _hour_time(hours[-1])))
            continue
        lo = max(marked[0], rule_lo)
        hi = min(marked[-1], rule_hi)
        spans.append((lo, hi) if lo <= hi else None)
    return run_ts, spans


def current_ecmwf_run_ts(
    cover_until: datetime, *, as_of_time: datetime | None = None,
) -> int | None:
    """The init timestamp of the run the extension would use — the decision-H
    reuse key. Cheap (directory scan, no decode)."""
    try:
        from weatherbrief.fetch.grib.ecmwf_fetch import (
            ecmwf_grib_dir,
            find_best_ecmwf_run,
            scan_ecmwf_files,
        )

        files = scan_ecmwf_files(ecmwf_grib_dir())
        if as_of_time is not None:
            files = [f for f in files if f.base_time <= as_of_time]
        if not files:
            return None
        run = find_best_ecmwf_run(files, cover_until=cover_until, data_dir=ecmwf_grib_dir())
        return int(run[0].base_time.timestamp()) if run else None
    except Exception:
        logger.debug("time-scan: current run lookup failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Open-Meteo adjacent-day extension (slice 4 — the ±day base fetch)
# ---------------------------------------------------------------------------


def _pack_hourly_bounds(
    cross_sections: list[RouteCrossSection],
) -> tuple[datetime, datetime] | None:
    """Overall ``(earliest, latest)`` OM hourly time present across the pack —
    the fetched-day span. Used to decide whether a candidate window needs the
    adjacent-day OM fetch (any ETA past this span would grade OM-clamped)."""
    times: list[datetime] = []
    for cs in cross_sections:
        for wf in cs.point_forecasts:
            if wf.hourly:
                times.append(_hour_time(wf.hourly[0]))
                times.append(_hour_time(wf.hourly[-1]))
    return (min(times), max(times)) if times else None


def extend_openmeteo_adjacent_day(
    cross_sections: list[RouteCrossSection],
    route_points,
    start_date: date,
    end_date: date,
    *,
    historical: bool = False,
) -> int:
    """Re-fetch the Open-Meteo base for ``[start_date, end_date]`` and splice it
    onto the in-memory cross-sections (ephemeral — decision G).

    The pack's OM base spans only the target day: the fetch stage windows the
    request with ``start_date = end_date = target day``
    (``tasks/fetch.py``). So a ±day candidate's per-point ETAs fall past the
    last stored OM hour, and ``at_time()`` would silently clamp **every** model's
    OM layer (and the OM-only models MétéoFr/UKMO/GEM outright) to the day edge
    — the exact confident-but-wrong grade the honesty posture forbids. This
    fills the OM layer for the candidate day(s) so the multi-model confirm
    grades on real values; ``enrich_forecasts`` overlays GRIB fidelity
    (ECMWF/GFS/ICON) afterwards, exactly as the target day was built.

    Fetches each fetched model over the same date window; a model whose range
    doesn't reach the day (raises / returns empty) is skipped, not faked.
    Mutates ``cross_sections`` in place; returns the number of sections that
    gained hours.
    """
    from weatherbrief.fetch.open_meteo import EmptyForecastError, OpenMeteoClient

    client = OpenMeteoClient(historical=historical)
    s = start_date.strftime("%Y-%m-%d")
    e = end_date.strftime("%Y-%m-%d")
    extended = 0
    for cs in cross_sections:
        try:
            new_pfs = client.fetch_multi_point(
                route_points, cs.model, start_date=s, end_date=e,
            )
        except EmptyForecastError:
            logger.info(
                "time-scan: %s has no OM data for %s–%s (out of range) — skipped",
                cs.model.value, s, e,
            )
            continue
        except Exception:
            logger.debug(
                "time-scan: OM adjacent-day fetch failed for %s",
                cs.model.value, exc_info=True,
            )
            continue
        if not new_pfs or len(new_pfs) != len(cs.point_forecasts):
            continue
        added = False
        for existing_wf, new_wf in zip(cs.point_forecasts, new_pfs):
            have = {_hour_time(h) for h in existing_wf.hourly}
            for h in new_wf.hourly:
                if _hour_time(h) not in have:
                    existing_wf.hourly.append(h)
                    added = True
            existing_wf.hourly.sort(key=_hour_time)
        if added:
            extended += 1
    logger.info(
        "time-scan: OM adjacent-day extension %s–%s → %d/%d sections extended",
        s, e, extended, len(cross_sections),
    )
    return extended


# ---------------------------------------------------------------------------
# On-tap multi-model confirm (slice 3 — the deferred, user-gated cost)
# ---------------------------------------------------------------------------


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
    airport_conditions_recompute: Callable | None = None,
    icing_method: str | None = None,
    cloud_source: str | None = None,
    convective_method: str | None = None,
    locale: str | None = None,
    cruise_speed_ias_kt: float | None = None,
    declared_approach_icaos: list[str] | None = None,
):
    """Multi-model check of one provisional candidate — the honesty-ladder top.

    Fetches + decodes GFS (S3 byte-range) and ICON (DWD download) for the
    candidate's *shifted* flight window — roughly one briefing-equivalent of
    fetch, which is why it's gated on a user tap — then grades the full
    advisory model set and diffs against the planned time graded through the
    same path on the same data. ``better_than_baseline=False`` (a tapped
    suggestion that ICON/GFS reveal isn't better) is a designed outcome, not
    an error: it shows the cross-check working.

    Uses the **latest available** model runs (``as_of_time=None``): DWD
    opendata only retains current runs, and "do the other models agree right
    now" is the honest question a confirm answers. ``models_checked`` on the
    returned :class:`TimeConfirmation` records what actually contributed.

    Ephemeral throughout (decision G): enrichment mutates a freshly loaded
    copy; the caller persists only the updated ``time_options.json``.

    Returns ``(confirmation, advisory_status, disposition)``:

    * ``advisory_status`` — the now-multi-model per-(advisory, model) status
      map, so the caller can fill out the candidate's provisional ECMWF-only
      map to the full graded set (#434, "the same map fills out on confirm").
    * ``disposition`` — re-derived from the multi-model diff, so a confirm
      up/downgrade updates what the client shows (the display partition and
      headline are keyed off ``disposition``, not the confirmation — #435).

    ``None`` when grading fails outright.
    """
    from weatherbrief.fetch.grib import DecodePriority, enrich_forecasts
    from weatherbrief.models import TimeConfirmation
    from weatherbrief.tasks.advise import (
        derive_assessment_from_advisories,
        per_model_reasons_from_manifest,
        per_model_status_from_manifest,
        run_alt_from_pack,
    )
    from weatherbrief.tasks.artifacts import load_cross_sections, load_route_points

    from weatherbrief.analysis.advisories.registry import get_scan_class_ids

    cand = _aware(candidate_time)
    cross_sections = load_cross_sections(pack_dir)
    route_points = load_route_points(pack_dir)
    if not cross_sections or not route_points:
        return None

    # ±day confirm: the pack's OM base covers only the target day, so a
    # candidate on an adjacent day would grade every model's OM layer clamped to
    # the day edge (and the OM-only models outright). Re-fetch the OM base for
    # the candidate's day(s) FIRST, so enrich_forecasts overlays GRIB onto real
    # OM values and the multi-model grade below is honest across the day
    # boundary. Same-day candidates already have their OM base — the bounds
    # check skips the fetch for them.
    cand_end = cand + timedelta(hours=max(route.flight_duration_hours, 1.0))
    bounds = _pack_hourly_bounds(cross_sections)
    if bounds is None or cand < bounds[0] or cand_end > bounds[1]:
        extend_openmeteo_adjacent_day(
            cross_sections, route_points, cand.date(), cand_end.date(),
        )

    try:
        enrich_forecasts(
            cross_sections, [], route_points, cand,
            data_dir=data_dir,
            flight_duration_hours=route.flight_duration_hours,
            priority=DecodePriority.BACKGROUND,
        )
    except Exception:
        # A failed multi-model enrichment must not degrade into a quiet
        # OM-clamped grade presented as "all models checked".
        logger.warning("time-scan confirm: enrichment failed", exc_info=True)
        return None

    def _grade(t: datetime):
        res = run_alt_from_pack(
            pack_dir, t, route,
            advisory_models=advisory_models,
            enabled_ids=enabled_ids,
            advisory_enabled=advisory_enabled,
            user_params=user_params,
            aggregation=aggregation,
            airports_db_path=airports_db_path,
            airport_conditions_recompute=airport_conditions_recompute,
            icing_method=icing_method,
            cloud_source=cloud_source,
            convective_method=convective_method,
            locale=locale,
            cruise_speed_ias_kt=cruise_speed_ias_kt,
            declared_approach_icaos=declared_approach_icaos,
            cross_sections=cross_sections,
            persist=False,
            detect_fronts=False,
        )
        return res.manifest

    # Baseline through the same path on the same (candidate-window-enriched)
    # sections — like vs like. The planned window's own enrichment is intact
    # (the candidate-window fetch only adds hours).
    baseline_manifest = _grade(_aware_pack_departure(pack_dir) or cand)
    candidate_manifest = _grade(cand)
    if baseline_manifest is None or candidate_manifest is None:
        return None

    improves, worsens, margin = _diff_manifests(
        baseline_manifest, candidate_manifest, get_scan_class_ids(),
    )
    assess, reason = derive_assessment_from_advisories(candidate_manifest)
    confirmation = TimeConfirmation(
        models_checked=candidate_manifest.models,
        assessment=assess,
        assessment_reason=reason,
        per_model_reasons=per_model_reasons_from_manifest(candidate_manifest),
        better_than_baseline=margin >= _MIN_MARGIN and not worsens,
        improves=improves,
        worsens=worsens,
        confirmed_at=datetime.now(timezone.utc),
    )
    # Re-derive disposition from the multi-model diff (same _disposition the
    # sweep used) so a confirm up/downgrade updates what the client shows — the
    # display partition + headline key off `disposition`, not the confirmation.
    return (
        confirmation,
        per_model_status_from_manifest(candidate_manifest),
        _disposition(margin, worsens),
    )


def _aware_pack_departure(pack_dir: Path) -> datetime | None:
    """The pack's planned departure (from briefing.json), for the confirm diff."""
    import json

    try:
        b = json.loads((pack_dir / "briefing.json").read_text())
        raw = b.get("departure_time")
        return _aware(datetime.fromisoformat(raw.replace("Z", "+00:00"))) if raw else None
    except Exception:
        return None


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


def _clamp_past_grid(
    grid: list[datetime], departure: datetime, now: datetime,
) -> tuple[list[datetime], bool]:
    """Drop already-elapsed candidate hours (slice 4's past-day clamp).

    Only clamps when the planned ``departure`` is itself still in the future —
    i.e. we're doing forward planning. An eval/replay of an already-flown pack
    (``departure`` in the past) must grade its whole window unchanged, so those
    packs are left alone. Returns ``(kept_grid, past_clipped)``.
    """
    if _aware(departure) <= now:
        return grid, False
    kept = [t for t in grid if t > now]
    return kept, len(kept) != len(grid)


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
    cloud_source: str | None = None,
    convective_method: str | None = None,
    locale: str | None = None,
    cruise_speed_ias_kt: float | None = None,
    declared_approach_icaos: list[str] | None = None,
) -> TimeWindowScan | None:
    """Grade the flight's Flexibility scenarios against the saved pack.

    Returns the scan (caller persists it as ``time_options.json``) or ``None``
    when there is nothing to do (``flexibility="none"``, or missing pack
    data). Never raises for data problems — refused candidates are recorded on
    the scan, and hard failures return ``None`` after logging.

    Two tiers (the honesty ladder):

    1. **Free tier** — candidates whose whole shifted flight fits every
       fetched model's enriched coverage grade multi-model
       (``confirmed_in_window``), pure re-analysis.
    2. **Provisional tier (slice 2)** — the rest get the daylight ECMWF
       extension (decode-only, ephemeral) and grade ECMWF-only against an
       ECMWF-only baseline (``ecmwf_only``). Hours even ECMWF can't cover
       (horizon/daylight edges) land in ``refused_times``.

    ``ecmwf_run_ts`` records the run the extension decoded — the decision-H
    staleness key (a refresh on the same run may reuse this scan).
    """
    from weatherbrief.analysis.advisories.registry import get_scan_class_ids
    from weatherbrief.tasks.advise import (
        _compute_advisory_model_names,
        derive_assessment_from_advisories,
        per_model_status_from_manifest,
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
        cloud_source=cloud_source,
        convective_method=convective_method,
        locale=locale,
        cruise_speed_ias_kt=cruise_speed_ias_kt,
        declared_approach_icaos=declared_approach_icaos,
        cross_sections=cross_sections,
    )

    def _grade(t: datetime, *, persist: bool, detect_fronts: bool,
               models_override: list[str] | None = None):
        kwargs = dict(grade_kwargs)
        if models_override is not None:
            kwargs["advisory_models"] = models_override
            # Analyze only the graded models' cross-sections — the analysis
            # stage is the per-candidate cost, and re-analyzing the other
            # models just to discard them roughly 4x-es the provisional tier.
            wanted = {m.lower() for m in models_override}
            kwargs["cross_sections"] = [
                cs for cs in cross_sections if cs.model.value in wanted
            ]
        res = run_alt_from_pack(
            pack_dir, t, route,
            persist=persist,
            detect_fronts=detect_fronts,
            **kwargs,
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

        # Past-day clamp (slice 4): drop already-elapsed hours up front —
        # prev_day's window is largely behind "now", and a late-in-day scan
        # trims same_day's morning too. Dropping beats a coverage refusal
        # (which reads as "data gap", not "already flown"). An empty grid is
        # the honest "the previous day is already behind you".
        grid, window_model.past_clipped = _clamp_past_grid(
            grid, dep, datetime.now(timezone.utc),
        )

        # ±day (slice 4): the pack's OM base only covers the target day, so the
        # adjacent-day grid hours have NO hourly rows at all. That breaks the
        # ECMWF daylight extension — `_enrich_ecmwf` *attaches* decoded GRIB to
        # existing hourly rows (it never creates them), so with no adjacent-day
        # rows it decodes the next-day steps, matches nothing, and bails with
        # "no matching steps" → the whole day refuses. Lay down the adjacent-day
        # OM skeleton first (all models), then recompute coverage so the ECMWF
        # extension has rows to enrich and OM-only models span the new day.
        if flexibility in ("prev_day", "next_day") and grid:
            flight_end = w_hi + timedelta(hours=max(duration, 1.0))
            extend_openmeteo_adjacent_day(
                cross_sections, route_points, w_lo.date(), flight_end.date(),
            )
            per_point, coverage_summary = compute_model_coverage(
                cross_sections, dep, duration,
            )

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
        advisory_status=per_model_status_from_manifest(baseline_manifest),
        confidence="confirmed_in_window",
        is_baseline=True,
    )

    # Every graded swept candidate (all dispositions) — the improving-only cut
    # moved to the display layer (#434). Bounded by _MAX_GRID up front.
    graded: list[TimeCandidate] = []
    alternate_row: TimeCandidate | None = None
    # (time, is_alternate) pairs the free tier couldn't grade — the daylight
    # ECMWF extension (below) gives them a second, provisional chance.
    deferred: list[tuple[datetime, bool, list[datetime]]] = []

    for t, is_alt in considered:
        if abs((t - dep).total_seconds()) < 60:
            if is_alt:
                alternate_row = baseline_row.model_copy(update={"is_alternate": True})
            continue
        vts = candidate_valid_times(route_points, t, duration)
        if not covers(per_point, graded_models, vts):
            # The honesty invariant: never grade an hour whose enriched fields
            # aren't actually there — at_time() would silently clamp to the
            # window edge and label OM values as the GRIB model. Deferred to
            # the ECMWF-only tier rather than refused outright.
            deferred.append((t, is_alt, vts))
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
            disposition=_disposition(margin, worsens),
            advisory_status=per_model_status_from_manifest(manifest),
            confidence="confirmed_in_window",
            is_alternate=is_alt,
        )
        if is_alt:
            alternate_row = row
        else:
            # Persist every disposition (#434) — the display layer, not the
            # scan, applies the improving-only cut.
            graded.append(row)

    # --- Slice 2: ECMWF-only provisional tier for the deferred hours.
    # Decode the daylight ECMWF fhours (local disk, BACKGROUND priority,
    # ephemeral) and grade the deferred candidates ECMWF-only against an
    # ECMWF-only baseline — like against like, labelled provisional.
    ecmwf_run_ts: int | None = None
    base_e_assess: str | None = None
    base_e_reason: str | None = None
    horizon_clipped = False
    if deferred and _ECMWF_MODEL in [m.lower() for m in model_names]:
        ext_lo = min(t for t, _, _ in deferred)
        ext_hi = max(t for t, _, _ in deferred)
        as_of = _pack_fetched_at(pack_dir)
        ecmwf_run_ts, ecmwf_spans = extend_ecmwf_daylight(
            cross_sections, route_points, ext_lo, ext_hi, duration,
            as_of_time=as_of,
        )
        if ecmwf_run_ts is not None:
            ecmwf_per_point = {_ECMWF_MODEL: ecmwf_spans}
            base_e_manifest = _grade(
                dep, persist=False, detect_fronts=False,
                models_override=[_ECMWF_MODEL],
            )
            if base_e_manifest is not None:
                base_e_assess, base_e_reason = derive_assessment_from_advisories(base_e_manifest)
                span_his = [s[1] for s in ecmwf_spans if s]
                horizon = min(span_his) if span_his else None
                for t, is_alt, vts in deferred:
                    if not covers(ecmwf_per_point, [_ECMWF_MODEL], vts):
                        refused.append(t)
                        if horizon and vts and vts[-1] > horizon:
                            horizon_clipped = True
                        continue
                    # detect_fronts=is_alt mirrors the free tier: the pinned
                    # alternate keeps route_fronts_alt.json fresh (like the
                    # retired in-pipeline stage), even when it grades
                    # provisionally — an off-window alternate is the COMMON
                    # case, and a stale planned-time fronts artifact under a
                    # fresh alt manifest would lie. The tight-loop concern
                    # only applies to the swept candidates.
                    manifest = _grade(
                        t, persist=is_alt, detect_fronts=is_alt,
                        models_override=[_ECMWF_MODEL],
                    )
                    if manifest is None:
                        continue
                    improves, worsens, margin = _diff_manifests(
                        base_e_manifest, manifest, scan_ids,
                    )
                    assess, reason = derive_assessment_from_advisories(manifest)
                    row = TimeCandidate(
                        departure_time=t,
                        departure_shift_hours=round((t - dep).total_seconds() / 3600.0, 2),
                        valid_times=vts,
                        assessment=assess,
                        assessment_reason=reason,
                        models_used=[_ECMWF_MODEL],
                        improves=improves,
                        worsens=worsens,
                        margin=float(margin),
                        disposition=_disposition(margin, worsens),
                        advisory_status=per_model_status_from_manifest(manifest),
                        confidence="ecmwf_only",
                        is_alternate=is_alt,
                    )
                    if is_alt:
                        # Provisional alternate: route_advisories_alt.json was
                        # persisted from the ECMWF-only manifest (its `models`
                        # field carries the honest coverage).
                        alternate_row = row
                    else:
                        # Persist every disposition — including provisional
                        # ``worse`` ±day rows, the honesty gap this closes (#434).
                        graded.append(row)
            else:
                refused.extend(t for t, _, _ in deferred)
        else:
            logger.info("time-scan: no ECMWF run for the extension — deferred hours refused")
            refused.extend(t for t, _, _ in deferred)
    else:
        refused.extend(t for t, _, _ in deferred)

    # Rank: same-or-better first (improving, then neutral, then worse), best
    # margin first within a disposition, confirmed over provisional, ties broken
    # by proximity to the alternate time (the pilot's stated preference) or else
    # the planned time. No persistence cap — every graded candidate is kept
    # (#434); the display layer slices the front and gates ``worse`` behind
    # "show all". _MAX_GRID already bounds the count.
    anchor = _aware(alt_departure_time) if alt_departure_time else dep
    graded.sort(
        key=lambda r: (
            _DISPOSITION_RANK.get(r.disposition, 1),
            -r.margin,
            0 if r.confidence == "confirmed_in_window" else 1,
            abs((r.departure_time - anchor).total_seconds()),
        ),
    )
    n_improving = sum(1 for r in graded if r.disposition == "improving")

    out_candidates = [baseline_row]
    if alternate_row is not None:
        out_candidates.append(alternate_row)
    out_candidates.extend(graded)

    if window_model is not None:
        window_model.horizon_clipped = horizon_clipped

    scan = TimeWindowScan(
        flexibility=flexibility,  # type: ignore[arg-type]
        baseline=TimeScanBaseline(
            departure_time=dep,
            assessment=base_assess,
            assessment_reason=base_reason,
            models_used=graded_models,
            ecmwf_assessment=base_e_assess,
            ecmwf_assessment_reason=base_e_reason,
        ),
        window=window_model,
        candidates=out_candidates,
        refused_times=sorted(refused),
        coverage=coverage_summary,
        models=model_names,
        ecmwf_run_ts=ecmwf_run_ts,
        generated_at=datetime.now(timezone.utc),
    )
    logger.info(
        "time-scan[%s]: %d graded (%d improving), %d refused in %.1fs",
        flexibility, len(out_candidates), n_improving, len(refused),
        perf_counter() - t0,
    )
    return scan
