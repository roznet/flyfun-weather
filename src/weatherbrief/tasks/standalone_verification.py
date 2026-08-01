"""Standalone airport verification pipeline.

Flight-independent NWP accuracy dataset: predict weather at METAR-reporting
airports across Western/Central Europe, then score against actual METARs
at multiple lead times (D-0 through D-7).
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from itertools import batched
from pathlib import Path
from typing import TYPE_CHECKING

import requests
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    AirportForecastSnapshotRow,
    TafVerificationScoreRow,
    VerificationCycleRow,
    VerificationObservationRow,
    VerificationScoreRow,
    snapshot_insert_ignore,
    snapshot_natural_key,
)
from weatherbrief.process_memory_sampler import MemorySampler, MemoryPeaks
from weatherbrief.process_rss import current_rss_mb, log_memory
from weatherbrief.tasks.airport_watchlist import WatchlistAirport
from weatherbrief.tasks.forecast_grid import (
    FINE_SAMPLE_HOURS,
    MAP_FORECAST_DAYS,
    all_sample_hours,
    sample_hours_for_day,
)

if TYPE_CHECKING:
    from weatherbrief.analysis.sounding.edr import EdrAccumulator
    from weatherbrief.fetch.grib import DecodePriority

logger = logging.getLogger(__name__)

# Anomaly thresholds (issue #137). The relative trigger catches gradual
# memory creep (e.g. the 5/7 → 5/8 drift from 2.57 → 2.97 GiB that ended
# in OOM); the absolute trigger catches hitting a tight ceiling regardless
# of trend. Per-source baselining so e.g. ``standalone_forecast`` is
# compared only against itself, not against the lighter ``standalone_light``.
_RSS_ANOMALY_RELATIVE_FACTOR = 1.4
_RSS_ANOMALY_ABSOLUTE_PCT_OF_CGROUP = 0.80
_RSS_ANOMALY_MIN_BASELINE_SAMPLES = 3


def _rss_log(label: str) -> None:
    """Log parent-process RSS at a cycle phase boundary.

    Memory regressions in this cycle have killed the container at the 3 GiB
    cgroup limit (issue #134). Periodic RSS logs make creep visible before
    the next OOM rather than after.
    """
    rss = current_rss_mb()
    if rss is not None:
        logger.info("Standalone cycle RSS @ %s: %dMB", label, int(rss))


def _read_cgroup_limit_mb() -> int | None:
    """Read the container's cgroup memory limit in MB.

    Tries cgroup v2 first (``memory.max``), falls back to v1
    (``memory.limit_in_bytes``). Returns ``None`` outside a container or
    when the limit is unset (cgroup v2 prints "max" in that case).
    """
    for path in (
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    ):
        try:
            with open(path) as f:
                raw = f.read().strip()
            if raw == "max":
                return None
            return int(raw) // (1024 * 1024)
        except OSError:
            continue
    return None


def _check_memory_anomaly(
    db: Session,
    source: str,
    peaks: MemoryPeaks,
    cycle_started_at: datetime,
) -> None:
    """Emit a WARN log if this cycle's peak memory is anomalously high.

    Two complementary triggers, each comparing against the metric that
    actually matters for the failure mode it's guarding:

    - **Relative** (``peak_rss_mb`` vs RSS baseline): catches gradual creep
      in the parent process. ``peak_rss_mb`` × ``_RSS_ANOMALY_RELATIVE_FACTOR``
      vs the median of the last N same-source cycles' ``peak_rss_mb``.
    - **Absolute** (``peak_cgroup_mb`` vs % of cgroup limit): catches
      approaching the OOM ceiling. The OOM-killer compares the *cgroup
      total* (parent + decode workers + healthcheck) against the limit, so
      this is the metric that decides whether we're about to die. Falls
      back to ``peak_rss_mb`` only when cgroup readings are unavailable
      (e.g. dev outside a container).

    The current cycle's row is excluded from the baseline (``started_at <
    cycle_started_at``) so a single elevated peak doesn't immediately
    pollute its own comparison — important for catching gradual creep
    rather than absorbing it into the rolling median.

    The relative check is skipped until at least
    ``_RSS_ANOMALY_MIN_BASELINE_SAMPLES`` *prior* populated rows exist for
    the same source (otherwise a single high outlier becomes its own
    baseline and suppresses warnings on subsequent runs).
    """
    if peaks.peak_rss_mb is None and peaks.peak_cgroup_mb is None:
        return

    cgroup_limit_mb = _read_cgroup_limit_mb()
    absolute_threshold_mb: int | None = None
    if cgroup_limit_mb is not None:
        absolute_threshold_mb = int(cgroup_limit_mb * _RSS_ANOMALY_ABSOLUTE_PCT_OF_CGROUP)

    # Cgroup total is the metric the OOM-killer compares against the limit;
    # parent RSS is only one of several processes inside the cgroup. Use
    # cgroup when we have it, fall back to RSS when running outside a
    # container.
    peak_for_absolute = peaks.peak_cgroup_mb if peaks.peak_cgroup_mb is not None else peaks.peak_rss_mb

    prev = db.execute(
        select(VerificationCycleRow.peak_rss_mb)
        .where(VerificationCycleRow.source == source)
        .where(VerificationCycleRow.peak_rss_mb.is_not(None))
        .where(VerificationCycleRow.started_at < cycle_started_at)
        .order_by(VerificationCycleRow.started_at.desc())
        .limit(10)
    ).scalars().all()

    relative_threshold_mb: int | None = None
    baseline_mb: int | None = None
    if len(prev) >= _RSS_ANOMALY_MIN_BASELINE_SAMPLES:
        sorted_prev = sorted(prev)
        mid = len(sorted_prev) // 2
        if len(sorted_prev) % 2 == 1:
            baseline_mb = sorted_prev[mid]
        else:
            baseline_mb = (sorted_prev[mid - 1] + sorted_prev[mid]) // 2
        relative_threshold_mb = int(baseline_mb * _RSS_ANOMALY_RELATIVE_FACTOR)

    triggered = False
    if (
        relative_threshold_mb is not None
        and peaks.peak_rss_mb is not None
        and peaks.peak_rss_mb > relative_threshold_mb
    ):
        triggered = True
    if (
        absolute_threshold_mb is not None
        and peak_for_absolute is not None
        and peak_for_absolute > absolute_threshold_mb
    ):
        triggered = True

    if triggered:
        logger.warning(
            "Standalone cycle memory anomaly (source=%s): "
            "peak_rss=%dMB peak_cgroup=%s baseline=%s cgroup_limit=%s "
            "(relative_threshold=%s, absolute_threshold=%s)",
            source,
            peaks.peak_rss_mb,
            peaks.peak_cgroup_mb if peaks.peak_cgroup_mb is not None else "n/a",
            baseline_mb if baseline_mb is not None else "n/a",
            cgroup_limit_mb if cgroup_limit_mb is not None else "n/a",
            relative_threshold_mb if relative_threshold_mb is not None else "n/a",
            absolute_threshold_mb if absolute_threshold_mb is not None else "n/a",
        )

# ---------------------------------------------------------------------------
# Configuration defaults (hardcoded until config table in Step 10)
# ---------------------------------------------------------------------------

STANDALONE_MODELS = ["gfs", "icon", "ecmwf"]

# Region-scoped model sets. EU is the pre-seam default; the US entry lands with
# US onboarding (us-expansion-plan.md steps 3/§model-names) once the `ncep_`
# Open-Meteo models are registered in MODEL_ENDPOINTS — until then an unknown
# region falls back to the EU list, which is harmless because no US cycle runs.
STANDALONE_MODELS_BY_REGION = {
    "eu": STANDALONE_MODELS,
    # "us": ["ncep_gfs025", "ecmwf"],  # ICON is European-domain only
}
# Verification target hours. Derived from the map grid rather than restated:
# a second literal here is a rule that can silently drift out of step with
# forecast_grid, which is the thing that module exists to prevent.
SAMPLE_HOURS_UTC = list(FINE_SAMPLE_HOURS)
FULL_CYCLE_HOURS_UTC = {6, 18}  # legacy: combined fetch+score; kept for CLI/tests, no longer scheduled

# Fetch loop fires ~30 min after each ECMWF delivery (00Z lands ~06:35,
# 12Z lands ~18:35). Picks up the freshest GFS/ICON/ECMWF inits before the
# next verification cycle reads from DB.
FORECAST_FETCH_HOURS_UTC = [7, 19]

# Verification loop fires on the synoptic-bucket hours. cycle_time = wall
# clock, so METARs are pulled near each bucket and scored against whatever
# snapshots are already in DB (most recent fetch wins).
VERIFICATION_HOURS_UTC = [6, 9, 12, 15, 18]

# Per-flight alternates horizon — how far ahead a flight ETA can be fetched.
# The forecast *map* has its own, longer horizon in ``forecast_grid``: it is
# bounded by ECMWF's delivery, not by how far ahead someone files a flight.
MODEL_FORECAST_DAYS = {
    "gfs": 4,
    "icon": 4,
    "ecmwf": 4,
}

_OPEN_METEO_BATCH_SIZE = 100  # airports per Open-Meteo API call (also retry boundary)
_OPEN_METEO_CONCURRENCY = int(os.environ.get("STANDALONE_FETCH_CONCURRENCY", "4"))
_LCL_CONSTANT_FT = 400  # 400 * (T - Td) approximation for LCL in feet
# Profiles per pooled sounding-analysis job (#448 PR B). Amortises IPC over
# ~100 profiles (~5 s of MetPy work per job on the droplet) while keeping
# enough jobs in flight to feed every pool worker.
_SOUNDING_BATCH_SIZE = 100
# Flush pending payloads to the pool once this many accumulate, instead of
# collecting a whole chunk (~3K profiles for a 100-airport GFS chunk) — the
# same bounded-accumulation rationale as the ECMWF per-step merge, plus a
# pipelining win: workers start analysing while the chunk is still parsing.
_SOUNDING_FLUSH_THRESHOLD = _SOUNDING_BATCH_SIZE * 5


def _pooled_soundings_active(pool_soundings: bool) -> bool:
    """Should sounding analysis go to the decode process pool?

    Requires BOTH the caller's opt-in (only the standalone cycle sets it —
    the interactive alternates path shares these fetchers and stays inline)
    AND an enabled pool (``GRIB_DECODE_WORKERS`` > 0; the cycle child gets
    ``STANDALONE_ANALYSIS_WORKERS``, default 2, via the scheduler).
    """
    if not pool_soundings:
        return False
    from weatherbrief.fetch.grib import decode_pool_enabled

    return decode_pool_enabled()


def _analyze_soundings_pooled(
    pending: list[tuple[int, dict]],
    results: list[dict],
    priority: "int | DecodePriority | None" = None,
) -> None:
    """Dispatch pending profiles to the pool in batches; merge fields back.

    ``pending`` pairs an index into ``results`` with a serialised payload
    (see ``build_sounding_payload``). Degradation contract matches the inline
    path, isolated per batch (``return_exceptions=True``): a batch that fails
    post-retry loses only its own ~100 snapshots' sounding fields (they keep
    surface-only data; scoring then skips sounding-based comparisons) — the
    cycle never aborts over sounding analysis.

    ``priority`` follows the same convention as every other dispatch in this
    module: the standalone cycle passes ``DecodePriority.BACKGROUND``
    explicitly (the subprocess path never sets the decode-priority
    ContextVar, so leaving it unset would silently dispatch at SCHEDULED).
    """
    if not pending:
        return
    from weatherbrief.fetch.grib import _dispatch_decode_parallel

    jobs = []
    slices: list[list[tuple[int, dict]]] = []
    for i in range(0, len(pending), _SOUNDING_BATCH_SIZE):
        chunk = pending[i : i + _SOUNDING_BATCH_SIZE]
        slices.append(chunk)
        jobs.append(("analyze_sounding_batch", ([payload for _, payload in chunk],)))

    try:
        batch_results = _dispatch_decode_parallel(
            jobs, priority=priority, return_exceptions=True,
        )
    except Exception:
        logger.warning(
            "Pooled sounding analysis failed for %d profiles; "
            "snapshots keep surface data only",
            len(pending), exc_info=True,
        )
        return

    failed = 0
    for chunk, br in zip(slices, batch_results):
        if isinstance(br, Exception) or br is None:
            failed += len(chunk)
            continue
        for (idx, _), fields in zip(chunk, br):
            if fields:
                results[idx].update(fields)
    if failed:
        logger.warning(
            "Pooled sounding analysis: %d/%d profiles failed dispatch; "
            "their snapshots keep surface data only",
            failed, len(pending),
        )


# ---------------------------------------------------------------------------
# Sounding proxy — lightweight stand-in for SoundingAnalysis at scoring time
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field as dc_field
from typing import Optional


@dataclass
class _IndicesProxy:
    sounding_ceiling_ft: Optional[float] = None


@dataclass
class _CloudLayerProxy:
    base_ft: float = 0.0
    coverage: object = None


@dataclass
class _ConvectiveProxy:
    risk_level: object = None


@dataclass
class _SoundingProxy:
    """Minimal sounding stand-in for reconcile_ceiling and convective scoring."""
    indices: Optional[_IndicesProxy] = None
    dd_cloud_layers: list = dc_field(default_factory=list)
    convective: Optional[_ConvectiveProxy] = None


def _build_sounding_proxy(snap: AirportForecastSnapshotRow):
    """Reconstruct a minimal SoundingAnalysis proxy from stored snapshot fields.

    Returns None when no sounding data was stored (backward-compatible fallback).
    """
    if (snap.sounding_ceiling_ft is None
            and snap.sounding_cloud_base_ft is None
            and snap.sounding_convective_risk is None):
        return None

    from weatherbrief.models.analysis import CloudCoverage, ConvectiveRisk

    indices = _IndicesProxy(sounding_ceiling_ft=snap.sounding_ceiling_ft)

    dd_cloud_layers = []
    if snap.sounding_cloud_base_ft is not None:
        dd_cloud_layers = [_CloudLayerProxy(
            base_ft=snap.sounding_cloud_base_ft,
            coverage=CloudCoverage.BKN,
        )]

    convective = None
    if snap.sounding_convective_risk is not None:
        try:
            risk = ConvectiveRisk(snap.sounding_convective_risk)
        except ValueError:
            risk = ConvectiveRisk.NONE
        convective = _ConvectiveProxy(risk_level=risk)

    return _SoundingProxy(
        indices=indices,
        dd_cloud_layers=dd_cloud_layers,
        convective=convective,
    )


# ---------------------------------------------------------------------------
# Phase A: Fetch forecasts from Open-Meteo + GRIB ceiling
# ---------------------------------------------------------------------------

def _fetch_forecasts_for_model(
    model: str,
    init_time: datetime,
    airports: list[WatchlistAirport],
    session: requests.Session,
    sample_hours: list[int] | None = None,
    edr_acc: "EdrAccumulator | None" = None,
    days: int | None = None,
    pool_soundings: bool = False,
) -> tuple[list[dict], int]:
    """Fetch Open-Meteo surface forecasts for all airports for one model.

    Returns (snapshots, api_call_count) — list of dicts with airport ICAO
    and per-sample-hour values, plus the number of actual HTTP requests made.

    ``sample_hours`` given as a flat list applies to every day — the route
    alternates stage passes the flight's ETA hour that way to fetch off-grid
    hours (issue #210, "Seam 1"). Left as ``None`` it means "the map's grid",
    which varies by day: the far day is sampled at three hours rather than
    five because ECMWF only delivers 6-hourly steps out there (#415).

    ``days`` likewise defaults to the alternates horizon; the map cycle passes
    its own, longer per-model horizon.
    """
    from weatherbrief.models.analysis import ModelSource, RoutePoint
    from weatherbrief.fetch.open_meteo import OpenMeteoClient

    # A flat list applies to all days; None means the map's per-day grid.
    explicit_hours = set(sample_hours) if sample_hours is not None else None

    # What Open-Meteo is asked to materialise. This must stay a real set even
    # on the per-day-grid path: it is a parse-time memory bound (#236), not a
    # correctness filter. The per-day grid is a subset of the superset, so
    # filtering to the superset here discards nothing the loop below wants —
    # while leaving it None would parse every hour of a now-6-day horizon.
    hour_filter = explicit_hours if explicit_hours is not None else set(all_sample_hours())

    model_source = ModelSource(model)
    forecast_days = days if days is not None else MODEL_FORECAST_DAYS.get(model, 7)

    start_date = init_time.strftime("%Y-%m-%d")
    end_dt = init_time + timedelta(days=forecast_days)
    end_date = end_dt.strftime("%Y-%m-%d")

    client = OpenMeteoClient(timeout=60)

    chunks: list[list[WatchlistAirport]] = [
        list(c) for c in batched(airports, _OPEN_METEO_BATCH_SIZE)
    ]
    total_chunks = len(chunks)

    def _fetch_one_chunk(
        chunk_idx: int, chunk_list: list[WatchlistAirport],
    ) -> tuple[list[dict], int]:
        # Reset thread-local call count so the caller can attribute HTTP
        # calls — including failed retries — back to this chunk after the
        # pool joins. The shared `client.call_count` is racy under threads.
        client._reset_thread_call_count()
        chunk_num = chunk_idx + 1
        points = [
            RoutePoint(
                lat=a.lat, lon=a.lon,
                distance_from_origin_nm=0.0,
                waypoint_icao=a.icao,
            )
            for a in chunk_list
        ]

        # Time the whole retry ladder, not just the successful attempt: a
        # chunk that needed retries took that long to deliver useful data,
        # and that's the signal we want surfaced when scanning logs for
        # slow chunks.
        chunk_started = time.monotonic()
        forecasts = None
        for attempt in range(3):
            try:
                # hour_filter drops non-sample hours at parse time — without
                # it, ~80% of the response (a 6-day hourly horizon at up to
                # 28 pressure levels) was materialised as Pydantic objects
                # only to be discarded by the loop below, and those objects
                # stayed alive across the chunk's sounding analysis in up to
                # _OPEN_METEO_CONCURRENCY threads at once (issue #236).
                forecasts = client.fetch_multi_point(
                    points, model_source,
                    start_date=start_date, end_date=end_date,
                    hour_filter=hour_filter,
                )
                break
            except Exception:
                if attempt < 2:
                    wait = 5 * (attempt + 1)
                    logger.warning(
                        "Open-Meteo %s chunk %d airports attempt %d/3 failed, "
                        "retrying in %ds",
                        model, len(chunk_list), attempt + 1, wait,
                    )
                    time.sleep(wait)
                else:
                    logger.warning(
                        "Open-Meteo %s fetch failed for chunk of %d airports "
                        "after 3 attempts",
                        model, len(chunk_list), exc_info=True,
                    )
        if forecasts is None:
            return [], client._thread_call_count()

        logger.info(
            "Model %s chunk %d/%d: processing %d airports (fetch %.1fs)",
            model, chunk_num, total_chunks, len(chunk_list),
            time.monotonic() - chunk_started,
        )
        # Time the post-fetch loop separately: sounding analysis is the
        # dominant cycle cost (85–92% of each model leg, #448) and was
        # previously log-silent.
        analysis_started = time.monotonic()
        chunk_results: list[dict] = []
        # Pooled path (#448 PR B): collect payloads during the loop, dispatch
        # in batches after it, merge scalar fields back by index. Inline path
        # (pool disabled, or interactive alternates caller) is unchanged.
        pooled = _pooled_soundings_active(pool_soundings)
        pending_soundings: list[tuple[int, dict]] = []
        init_date = init_time.date()
        for airport, wpf in zip(chunk_list, forecasts):
            # Filter to sample hours only
            for hourly in wpf.hourly:
                utc_hour = hourly.time.hour if hasattr(hourly.time, 'hour') else None
                if utc_hour is None:
                    continue
                day_offset = (hourly.time.date() - init_date).days
                # Enforce the horizon here rather than trusting Open-Meteo to
                # honour end_date. The horizons are load-bearing now — ICON is
                # cut at 4 days because its ceiling GRIB stops at 120h — so a
                # row past the horizon is a bug, not a bonus.
                if day_offset < 0 or day_offset > forecast_days:
                    continue
                if explicit_hours is not None:
                    if utc_hour not in explicit_hours:
                        continue
                elif utc_hour not in sample_hours_for_day(day_offset):
                    continue

                # Compute LCL from T-Td
                lcl_ft = None
                if hourly.temperature_2m_c is not None and hourly.dewpoint_2m_c is not None:
                    spread = hourly.temperature_2m_c - hourly.dewpoint_2m_c
                    if spread >= 0:
                        lcl_ft = _LCL_CONSTANT_FT * spread

                snap = {
                    "icao": airport.icao,
                    "model": model,
                    "model_init_time": init_time,
                    "forecast_hour": hourly.time if hourly.time.tzinfo else hourly.time.replace(tzinfo=timezone.utc),
                    "temperature_2m_c": hourly.temperature_2m_c,
                    "dewpoint_2m_c": hourly.dewpoint_2m_c,
                    "visibility_m": hourly.visibility_m,
                    "wind_speed_10m_kt": hourly.wind_speed_10m_kt,
                    "wind_direction_10m_deg": hourly.wind_direction_10m_deg,
                    "wind_gusts_10m_kt": hourly.wind_gusts_10m_kt,
                    "precipitation_mm": hourly.precipitation_mm,
                    "snowfall_cm": hourly.snowfall_cm,
                    "precip_period_h": 1,  # Open-Meteo reports hourly totals
                    "cape_jkg": hourly.cape_jkg,
                    "cloud_cover_pct": hourly.cloud_cover_pct,
                    "cloud_cover_low_pct": hourly.cloud_cover_low_pct,
                    "lcl_ft": lcl_ft,
                }

                # Sounding analysis on pressure levels (already fetched):
                # pooled → defer to a process-pool batch; else inline.
                if pooled and getattr(hourly, "pressure_levels", None):
                    from weatherbrief.analysis.sounding.snapshot_fields import (
                        build_sounding_payload,
                    )

                    pending_soundings.append(
                        (len(chunk_results), build_sounding_payload(hourly, model))
                    )
                else:
                    _enrich_with_sounding(snap, hourly, model, edr_acc=edr_acc)

                chunk_results.append(snap)

                # Bounded accumulation + pipelining: flush to the pool once
                # enough payloads pile up (after the append — the last
                # pending index refers to the snap just appended) instead of
                # collecting the whole ~3K-profile chunk. Mirrors the ECMWF
                # per-step merge rationale.
                if len(pending_soundings) >= _SOUNDING_FLUSH_THRESHOLD:
                    from weatherbrief.fetch.grib import DecodePriority

                    _analyze_soundings_pooled(
                        pending_soundings, chunk_results,
                        priority=DecodePriority.BACKGROUND,
                    )
                    pending_soundings.clear()
        if pending_soundings:
            from weatherbrief.fetch.grib import DecodePriority

            # Explicit BACKGROUND, mirroring _enrich_with_grib: pooled
            # soundings only run for the standalone cycle (see
            # _pooled_soundings_active), and the subprocess path never sets
            # the decode-priority ContextVar.
            _analyze_soundings_pooled(
                pending_soundings, chunk_results,
                priority=DecodePriority.BACKGROUND,
            )
        logger.info(
            "Model %s chunk %d/%d: analyzed %d snapshots in %.1fs%s",
            model, chunk_num, total_chunks, len(chunk_results),
            time.monotonic() - analysis_started,
            " (pooled)" if pooled else "",
        )
        return chunk_results, client._thread_call_count()

    all_results: list[dict] = []
    if not chunks:
        return all_results, client.call_count

    # Unlike the briefing-side parallel fetch (tasks/fetch.py), this path
    # doesn't gate on `client.has_api_key`. The briefing gate exists
    # because briefings fire per-user-action on any deployment — including
    # local dev without a key — so the gate keeps free-tier dev runs from
    # tripping rate limits. Standalone is different: it's a scheduled
    # server-side cycle (twice daily), and a deployment that runs it at
    # all is expected to have the paid key set. Without the key the cycle
    # would still complete (peak ~4 in-flight requests, well under
    # 600/min), but the daily call budget for verification at full
    # watchlist scale belongs in a paid plan regardless of parallelism.
    max_workers = max(1, min(_OPEN_METEO_CONCURRENCY, total_chunks))
    total_calls = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_fetch_one_chunk, idx, chunk)
            for idx, chunk in enumerate(chunks)
        ]
        for future in concurrent.futures.as_completed(futures):
            try:
                chunk_results, chunk_calls = future.result()
                all_results.extend(chunk_results)
                total_calls += chunk_calls
            except Exception:
                # Per-chunk retries already logged inside the worker. A leak
                # here is unexpected — note it but don't abort other chunks.
                logger.warning(
                    "Open-Meteo %s chunk worker raised unexpectedly",
                    model, exc_info=True,
                )

    # Replace the racy aggregate with the summed per-thread totals so the
    # caller sees the correct count of HTTP calls made under concurrency.
    client.call_count = total_calls
    return all_results, client.call_count


def _enrich_with_sounding(
    snap: dict, hourly, model: str, edr_acc: "EdrAccumulator | None" = None,
) -> None:
    """Run sounding analysis on pressure-level data and store results in snap dict.

    Fails silently — surface data is preserved even if sounding analysis fails.

    When ``edr_acc`` is supplied (standalone calibration path only), the full
    per-level Richardson distribution is fed into the EDR calibration
    accumulator (issue #221). The accumulation shares this function's
    fail-silent try, so it can never break a forecast run.
    """
    # Implementation lives in analysis/sounding/snapshot_fields.py so the
    # inline path and the pooled batch worker (#448 PR B) share one code
    # path — including the parked EDR harvest (#221) and the fail-silent
    # contract. This wrapper only keeps the historical call shape.
    from weatherbrief.analysis.sounding.snapshot_fields import (
        compute_snapshot_sounding_fields,
    )

    snap.update(compute_snapshot_sounding_fields(hourly, model, edr_acc=edr_acc))


def _grib_step_snapper(model: str):
    """Return ``fhour -> published fhour`` for *model*'s GRIB output grid.

    Only ICON needs one today (hourly to +78 h, 3-hourly to +120 h). Everything
    else gets identity: GFS is hourly across the range this grid samples, and
    ECMWF never reaches here (it takes the local-GRIB path above).
    """
    if model != "icon":
        return lambda fhour: fhour

    from weatherbrief.fetch.grib.icon_eu_fetch import _snap_to_icon_eu_grid

    return _snap_to_icon_eu_grid


def _enrich_with_grib(
    snapshots: list[dict],
    model: str,
    init_time: datetime,
    airports: list[WatchlistAirport],
    session: requests.Session,
    priority: "int | DecodePriority | None" = None,
) -> None:
    """Enrich snapshot dicts with GRIB ceiling/cloud_base data in-place.

    Fetches GRIB cloud diagnostics for the model's forecast hours
    and maps nwp_ceiling_ft and cloud_base_ft onto the matching snapshot dicts.

    ``priority`` is forwarded to the GFS/ICON cloud-diag fetchers. ``None``
    (default) falls through to the decode-priority ContextVar — the interactive
    alternates path inherits INTERACTIVE; the standalone cycle passes
    ``DecodePriority.BACKGROUND`` explicitly.
    """
    from weatherbrief.tasks.standalone_grib import (
        AirportCeilingData,
        datetime_to_init_parts,
        fetch_gfs_cloud_diag,
        fetch_icon_cloud_diag,
    )

    if model == "ecmwf":
        # ECMWF GRIB ceiling is populated inline by fetch_ecmwf_grib_snapshots —
        # the Open-Meteo fallback path doesn't have a usable cloud-diag GRIB feed.
        return

    init_date, init_hour = datetime_to_init_parts(init_time)

    # Offset from init → the model's own output grid. The verification grid
    # samples daily, so offsets land 24 h apart (4, 28, 52, 76, 100 …) with no
    # regard for where a model thins out. ICON-EU is hourly only to +78 h and
    # 3-hourly after, so f100 is simply not a published step: it 404'd ten
    # times per run AND left the far-day sample with no ICON cloud diagnostics
    # at all. Snapping to the nearest real step costs an hour of currency at
    # four days out and returns actual data.
    #
    # GFS needs no equivalent here: it is hourly through +120 h and this grid
    # never samples past ~100 h (MODEL_FORECAST_DAYS = 4), so its coarse region
    # is out of reach. If that horizon ever grows, GFS needs the same snap via
    # ``grib_fetch._snap_to_gfs_grid``.
    snap_step = _grib_step_snapper(model)

    fhour_set: set[int] = set()
    for snap in snapshots:
        if snap["model"] != model:
            continue
        delta = snap["forecast_hour"] - init_time
        offset = int(delta.total_seconds() / 3600)
        if offset >= 0:
            fhour_set.add(snap_step(offset))

    if not fhour_set:
        return

    forecast_hours = sorted(fhour_set)

    if model == "icon":
        # ICON-EU's cloud-diag GRIB stops at 120 h (main cycles). Asking for a
        # step past that is nine guaranteed 404s per hour, so don't ask.
        from weatherbrief.fetch.grib.icon_eu_fetch import icon_eu_model_level_max_hour

        max_hour = icon_eu_model_level_max_hour(init_hour)
        beyond = [h for h in forecast_hours if h > max_hour]
        if beyond:
            logger.info(
                "ICON-EU cloud diag: %d forecast hour(s) beyond the %dh horizon "
                "(%s) — no ceiling available, skipping",
                len(beyond), max_hour, ", ".join(f"{h}h" for h in beyond),
            )
            forecast_hours = [h for h in forecast_hours if h <= max_hour]
        if not forecast_hours:
            return

    lats = [a.lat for a in airports]
    lons = [a.lon for a in airports]

    # Build ICAO → index mapping
    icao_to_idx = {a.icao: i for i, a in enumerate(airports)}

    fetch_fn = fetch_gfs_cloud_diag if model == "gfs" else fetch_icon_cloud_diag

    try:
        grib_data = fetch_fn(
            init_date, init_hour, forecast_hours, lats, lons, session=session,
            priority=priority,
        )
    except Exception:
        logger.warning("GRIB enrichment failed for %s", model, exc_info=True)
        return

    # Map GRIB data back to snapshots. MUST apply the same snap as the fetch
    # above — ``grib_data`` is keyed by the snapped step, so recomputing the
    # raw offset here would miss every hour that got snapped and silently drop
    # the data we just paid to download.
    for snap in snapshots:
        if snap["model"] != model:
            continue
        delta = snap["forecast_hour"] - init_time
        fhour = snap_step(int(delta.total_seconds() / 3600))
        airport_idx = icao_to_idx.get(snap["icao"])

        if fhour in grib_data and airport_idx is not None:
            ceiling_data = grib_data[fhour]
            if airport_idx < len(ceiling_data):
                cd = ceiling_data[airport_idx]
                snap["nwp_ceiling_ft"] = cd.nwp_ceiling_ft
                snap["cloud_base_ft"] = cd.cloud_base_ft


# ---------------------------------------------------------------------------
# Phase A (ECMWF GRIB-first): decode local a1+a2 files into snapshot dicts
# ---------------------------------------------------------------------------

def _select_ecmwf_grib_run(om_init_time: datetime, days: int):
    """Pick the freshest ready ECMWF GRIB run that is not older than Open-Meteo's.

    Returns the file list for the chosen run, or None if no GRIB run is
    available locally with init >= ``om_init_time``. The caller falls back
    to Open-Meteo when None is returned.
    """
    from weatherbrief.fetch.grib.ecmwf_fetch import (
        filter_ready_runs,
        scan_ecmwf_files,
        ecmwf_grib_dir,
    )

    grib_dir = ecmwf_grib_dir()
    all_files = scan_ecmwf_files(grib_dir)
    if not all_files:
        return None

    ready_files = filter_ready_runs(all_files, grib_dir)
    if not ready_files:
        return None

    runs: dict[datetime, list] = {}
    for f in ready_files:
        runs.setdefault(f.base_time, []).append(f)

    # Newest ready run wins, provided it isn't older than Open-Meteo's view.
    best_bt = max(runs)
    if best_bt < om_init_time:
        return None

    return runs[best_bt]


def fetch_ecmwf_grib_snapshots(
    run_files: list,
    airports: list[WatchlistAirport],
    sample_hours: list[int] | None,
    days: int,
    edr_acc: "EdrAccumulator | None" = None,
    priority: "int | DecodePriority | None" = None,
    pool_soundings: bool = False,
) -> list[dict]:
    """Decode an ECMWF run on disk into standalone-snapshot dicts.

    For each (day_offset × sample_hour) inside the run's horizon, opens the
    matching a1 (surface) and a2 (pressure-level) files once and extracts
    values for all ``airports``. Sounding-derived columns come from
    ``analyze_sounding_lite`` on the GRIB-native pressure-level profile.

    ``tp`` and ``sf`` arrive accumulated from init in the a1 GRIB, so this
    fetcher also decodes the preceding *delivered* step and stores the delta,
    together with the window that delta covers (``precip_period_h``): 1 h in
    the hourly region, 3 h or 6 h further out where ECMWF thins its cadence.

    Args:
        run_files: ECMWFFileInfo list scoped to one run (single base_time).
        airports: Watchlist airports to decode for.
        sample_hours: UTC hours to sample, applied to every day. ``None`` means
            the map's per-day grid (five hours near, three at the far day).
        days: Forecast horizon in days from init.
        priority: Decode priority for the dispatched a1/a2 jobs. ``None``
            (default) falls through to the decode-priority ContextVar via
            ``_resolve_priority`` — the interactive alternates path inherits
            INTERACTIVE; the standalone cycle passes
            ``DecodePriority.BACKGROUND`` explicitly.

    Returns:
        Snapshot dicts with the same shape as ``_fetch_forecasts_for_model``
        produces. Empty list when run_files has no usable steps.
    """
    from datetime import time as dt_time

    from weatherbrief.fetch.grib import (
        _decode_workers_ecmwf,
        _dispatch_decode_parallel,
    )
    from weatherbrief.fetch.grib.decode import (
        build_ecmwf_surface_snapshot,
        build_pressure_levels_from_grib,
    )
    from weatherbrief.models.analysis import HourlyForecast

    if not run_files:
        return []

    # Group files by step_hours, separate a1 (surface) and a2 (pressure)
    files_by_step: dict[int, dict[str, object]] = {}
    for f in run_files:
        part = "a2" if f.is_pressure_level else "a1" if f.is_surface else None
        if part is not None:
            files_by_step.setdefault(f.step_hours, {})[part] = f.path

    if not files_by_step:
        return []

    init_time = run_files[0].base_time
    if init_time.tzinfo is None:
        init_time = init_time.replace(tzinfo=timezone.utc)

    lats = [a.lat for a in airports]
    lons = [a.lon for a in airports]
    n = len(airports)
    empty: list[dict] = [{} for _ in range(n)]

    snapshots: list[dict] = []
    max_step = max(files_by_step.keys())
    delivered_steps = sorted(files_by_step)
    # Decode is logged per file by the worker; the per-step airport loop
    # (sounding analysis, ~92% of this leg per #448) was log-silent — track it.
    analysis_s = 0.0
    steps_processed = 0
    pooled = _pooled_soundings_active(pool_soundings)

    def _previous_delivered_step(step_h: int) -> int | None:
        """Nearest delivered step before ``step_h``, for the accumulation diff.

        ``tp``/``sf`` accumulate from init, so a per-period value needs the
        preceding step. That is *not* ``step_h - 1`` outside the hourly region:
        ECMWF delivers 3-hourly past 90 h and 6-hourly past 144 h. Reaching for
        an undelivered step used to yield the empty sentinel, which silently
        skipped the diff and left the value accumulated-since-init (#415).
        """
        prev = [s for s in delivered_steps if s < step_h]
        return prev[-1] if prev else None

    # Fan the whole run's a1/a2 decodes out through the pool in one batch (#459)
    # instead of walking one blocking dispatch per step — the ~6 min ECMWF leg
    # sat ~70% idle because only one decode worker was ever busy. Enumerate the
    # (day_offset × sample_hour) grid to the set of delivered steps we touch:
    # each target step plus the previous delivered step its tp/sf accumulation
    # diff reads.
    def _iter_delivered_samples():
        """Yield ``(target_dt, step_h)`` for every sampled, delivered step.

        Single source of truth for the (day_offset × sample_hour) grid and its
        delivered-step filter: the decode precompute and the snapshot loop both
        consume this, so the two enumerations cannot drift apart — a step
        sampled by one but not the other would resolve to the ``empty``/``None``
        cache sentinel and degrade data silently (the #415 failure shape).
        """
        for day_offset in range(days + 1):
            target_date = (init_time + timedelta(days=day_offset)).date()
            hours = (
                sample_hours if sample_hours is not None
                else sample_hours_for_day(day_offset)
            )
            for hour in hours:
                target_dt = datetime.combine(
                    target_date, dt_time(hour, 0, 0), tzinfo=timezone.utc,
                )
                step_h = int((target_dt - init_time).total_seconds() / 3600)
                if step_h <= 0 or step_h > max_step or step_h not in files_by_step:
                    continue
                yield target_dt, step_h

    a1_needed: set[int] = set()
    a2_needed: set[int] = set()
    for _target_dt, step_h in _iter_delivered_samples():
        a1_needed.add(step_h)
        prev_step = _previous_delivered_step(step_h)
        if prev_step is not None:
            a1_needed.add(prev_step)
        if files_by_step[step_h].get("a2") is not None:
            a2_needed.add(step_h)

    # A step lacking an a1 file resolves to the empty sentinel without a decode
    # (matches the old per-step short-circuit in ``_decode_a1``).
    a1_steps = sorted(
        s for s in a1_needed if files_by_step.get(s, {}).get("a1") is not None
    )
    a2_steps = sorted(a2_needed)

    decode_jobs: list[tuple[str, tuple]] = [
        ("decode_ecmwf_surface", (str(files_by_step[s]["a1"]), lats, lons))
        for s in a1_steps
    ]
    decode_jobs += [
        ("decode_ecmwf_pressure", (str(files_by_step[s]["a2"]), lats, lons))
        for s in a2_steps
    ]

    # ``max_inflight`` throttles concurrency *below* the pool for a
    # memory-constrained host via GRIB_DECODE_WORKERS_ECMWF; unset → the pool
    # (GRIB_DECODE_WORKERS) bounds it. Same explicit priority as the per-step
    # sounding merge below.
    ecmwf_window = _decode_workers_ecmwf()
    t_decode = time.monotonic()
    decoded = (
        _dispatch_decode_parallel(
            decode_jobs, priority=priority, return_exceptions=True,
            max_inflight=ecmwf_window,
        )
        if decode_jobs
        else []
    )
    decode_s = time.monotonic() - t_decode

    # ``empty`` is shared read-only across steps with no/failed a1 — never
    # mutated, so aliasing it is safe (as the old cache did).
    a1_cache: dict[int, list[dict[str, float]]] = {}
    for step_h, res in zip(a1_steps, decoded):
        if isinstance(res, Exception):
            logger.warning("ECMWF a1 decode failed for step %dh", step_h, exc_info=res)
            a1_cache[step_h] = empty
        else:
            data, _ = res
            a1_cache[step_h] = data
    a2_cache: dict[int, list[dict[int, dict[str, float]]] | None] = {}
    for step_h, res in zip(a2_steps, decoded[len(a1_steps):]):
        if isinstance(res, Exception):
            logger.warning("ECMWF a2 decode failed for step %dh", step_h, exc_info=res)
            a2_cache[step_h] = None
        else:
            pl_data, _ = res
            a2_cache[step_h] = pl_data

    for target_dt, step_h in _iter_delivered_samples():
        cur_a1 = a1_cache.get(step_h, empty)
        prev_step = _previous_delivered_step(step_h)
        prev_a1 = (
            a1_cache.get(prev_step, empty) if prev_step is not None else None
        )
        # Window the accumulation actually covers: 1 h inside the hourly
        # region, 3 h or 6 h further out. Recorded per row so a 6 h total is
        # never mistaken for an hourly one.
        precip_period_h = (step_h - prev_step) if prev_step is not None else None

        pl_data = a2_cache.get(step_h)

        t_step = time.monotonic()
        step_pending: list[tuple[int, dict]] = []
        for i, airport in enumerate(airports):
            cur_raw = cur_a1[i] if i < len(cur_a1) else {}
            snap_fields = build_ecmwf_surface_snapshot(cur_raw)

            # Step-diff for accumulated fields (precipitation, snowfall)
            if prev_a1 is not None and i < len(prev_a1):
                prev_fields = build_ecmwf_surface_snapshot(prev_a1[i])
                cur_pp = snap_fields.get("precipitation_mm")
                prev_pp = prev_fields.get("precipitation_mm")
                if cur_pp is not None and prev_pp is not None:
                    snap_fields["precipitation_mm"] = max(0.0, cur_pp - prev_pp)
                cur_sf = snap_fields.get("snowfall_cm")
                prev_sf = prev_fields.get("snowfall_cm")
                if cur_sf is not None and prev_sf is not None:
                    snap_fields["snowfall_cm"] = max(0.0, cur_sf - prev_sf)

            # LCL from T-Td spread
            t = snap_fields.get("temperature_2m_c")
            d = snap_fields.get("dewpoint_2m_c")
            lcl_ft = None
            if t is not None and d is not None:
                spread = t - d
                if spread >= 0:
                    lcl_ft = _LCL_CONSTANT_FT * spread

            snap = {
                "icao": airport.icao,
                "model": "ecmwf",
                "model_init_time": init_time,
                "forecast_hour": target_dt,
                "temperature_2m_c": snap_fields.get("temperature_2m_c"),
                "dewpoint_2m_c": snap_fields.get("dewpoint_2m_c"),
                "visibility_m": snap_fields.get("visibility_m"),
                "wind_speed_10m_kt": snap_fields.get("wind_speed_10m_kt"),
                "wind_direction_10m_deg": snap_fields.get("wind_direction_10m_deg"),
                "wind_gusts_10m_kt": snap_fields.get("wind_gusts_10m_kt"),
                "precipitation_mm": snap_fields.get("precipitation_mm"),
                "snowfall_cm": snap_fields.get("snowfall_cm"),
                "precip_period_h": precip_period_h,
                "cape_jkg": snap_fields.get("cape_jkg"),
                "cloud_cover_pct": snap_fields.get("cloud_cover_pct"),
                "cloud_cover_low_pct": snap_fields.get("cloud_cover_low_pct"),
                "nwp_ceiling_ft": snap_fields.get("nwp_ceiling_ft"),
                "cloud_base_ft": snap_fields.get("cloud_base_ft"),
                "lcl_ft": lcl_ft,
            }

            # Sounding analysis on a2 pressure levels for sounding-derived columns
            if pl_data is not None and i < len(pl_data) and pl_data[i]:
                try:
                    levels = build_pressure_levels_from_grib(pl_data[i])
                except Exception:
                    logger.warning(
                        "ECMWF pressure-level build failed for %s step %dh",
                        airport.icao, step_h, exc_info=True,
                    )
                    levels = []
                if levels:
                    hourly = HourlyForecast(
                        time=target_dt,
                        temperature_2m_c=snap_fields.get("temperature_2m_c"),
                        dewpoint_2m_c=snap_fields.get("dewpoint_2m_c"),
                        surface_pressure_hpa=snap_fields.get("surface_pressure_hpa"),
                        wind_speed_10m_kt=snap_fields.get("wind_speed_10m_kt"),
                        wind_direction_10m_deg=snap_fields.get("wind_direction_10m_deg"),
                        cape_jkg=snap_fields.get("cape_jkg"),
                        cloud_cover_pct=snap_fields.get("cloud_cover_pct"),
                        cloud_cover_low_pct=snap_fields.get("cloud_cover_low_pct"),
                        pressure_levels=levels,
                    )
                    if pooled:
                        from weatherbrief.analysis.sounding.snapshot_fields import (
                            build_sounding_payload,
                        )

                        step_pending.append(
                            (len(snapshots), build_sounding_payload(hourly, "ecmwf"))
                        )
                    else:
                        _enrich_with_sounding(snap, hourly, "ecmwf", edr_acc=edr_acc)

            snapshots.append(snap)

        # Merge per step (not per run) so pending payloads stay bounded
        # at ~len(airports) and results land before the next decode.
        # Same explicit priority as this function's own a1/a2 decodes.
        _analyze_soundings_pooled(step_pending, snapshots, priority=priority)

        analysis_s += time.monotonic() - t_step
        steps_processed += 1

    logger.info(
        "ECMWF GRIB leg: %d steps, %d snapshots, decode %.0fs "
        "(%d a1 + %d a2, %s), sounding analysis %.0fs%s",
        steps_processed, len(snapshots), decode_s, len(a1_steps), len(a2_steps),
        f"max_inflight={ecmwf_window}" if ecmwf_window else "full pool",
        analysis_s, " (pooled)" if pooled else "",
    )
    return snapshots


# ---------------------------------------------------------------------------
# Phase B: Store forecast snapshots in DB
# ---------------------------------------------------------------------------

def _normalize_key(icao: str, model: str, init_time: datetime, fhour: datetime) -> tuple:
    """Normalize a snapshot key for consistent comparison.

    Thin alias for the shared :func:`snapshot_natural_key` so the standalone
    inserter and the artifact importer share one dedup technique.
    """
    return snapshot_natural_key(icao, model, init_time, fhour)


def _store_snapshots(snapshots: list[dict], db, region: str = "eu") -> int:
    """Insert new forecast snapshot rows, skipping duplicates. Returns count of new rows.

    ``region`` tags every stored row (EU/US seam). The final write is an
    INSERT-OR-IGNORE (``snapshot_insert_ignore``), so a concurrent writer on the
    same natural key is a no-op rather than an ``IntegrityError``.
    """
    if not snapshots:
        return 0

    # Bulk-fetch existing keys in one query
    snap_keys = [
        (s["icao"], s["model"], s["model_init_time"], s["forecast_hour"])
        for s in snapshots
    ]
    # Deduplicate input keys for the query
    unique_keys = list({k for k in snap_keys})

    existing_keys: set[tuple] = set()
    # Query in chunks to stay within SQL parameter limits
    for i in range(0, len(unique_keys), 500):
        chunk = unique_keys[i : i + 500]
        rows = db.execute(
            select(
                AirportForecastSnapshotRow.icao,
                AirportForecastSnapshotRow.model,
                AirportForecastSnapshotRow.model_init_time,
                AirportForecastSnapshotRow.forecast_hour,
            ).where(
                tuple_(
                    AirportForecastSnapshotRow.icao,
                    AirportForecastSnapshotRow.model,
                    AirportForecastSnapshotRow.model_init_time,
                    AirportForecastSnapshotRow.forecast_hour,
                ).in_(chunk)
            )
        ).all()
        for r in rows:
            existing_keys.add(_normalize_key(*r))

    # Bulk executemany insert (#448): building 15–20K ORM objects and pushing
    # them through the unit-of-work cost ~1–2 min/cycle. The in-memory key
    # dedup above already guarantees uniqueness (uq_afs_key backs it up), so
    # plain dict rows through a Core insert are equivalent and far cheaper.
    now = datetime.now(timezone.utc)
    rows: list[dict] = []
    for snap in snapshots:
        key = _normalize_key(
            snap["icao"], snap["model"],
            snap["model_init_time"], snap["forecast_hour"],
        )
        if key in existing_keys:
            continue
        existing_keys.add(key)  # prevent duplicates within same batch

        rows.append({
            "icao": snap["icao"],
            "region": region,
            "model": snap["model"],
            "model_init_time": snap["model_init_time"],
            "forecast_hour": snap["forecast_hour"],
            "fetched_at": now,
            "temperature_2m_c": snap.get("temperature_2m_c"),
            "dewpoint_2m_c": snap.get("dewpoint_2m_c"),
            "visibility_m": snap.get("visibility_m"),
            "wind_speed_10m_kt": snap.get("wind_speed_10m_kt"),
            "wind_direction_10m_deg": snap.get("wind_direction_10m_deg"),
            "wind_gusts_10m_kt": snap.get("wind_gusts_10m_kt"),
            "precipitation_mm": snap.get("precipitation_mm"),
            "snowfall_cm": snap.get("snowfall_cm"),
            "precip_period_h": snap.get("precip_period_h"),
            "cape_jkg": snap.get("cape_jkg"),
            "cloud_cover_pct": snap.get("cloud_cover_pct"),
            "cloud_cover_low_pct": snap.get("cloud_cover_low_pct"),
            "nwp_ceiling_ft": snap.get("nwp_ceiling_ft"),
            "cloud_base_ft": snap.get("cloud_base_ft"),
            "lcl_ft": snap.get("lcl_ft"),
            "sounding_ceiling_ft": snap.get("sounding_ceiling_ft"),
            "sounding_cloud_base_ft": snap.get("sounding_cloud_base_ft"),
            "freezing_level_ft": snap.get("freezing_level_ft"),
            "sounding_cape_jkg": snap.get("sounding_cape_jkg"),
            "sounding_cin_jkg": snap.get("sounding_cin_jkg"),
            "sounding_lifted_index": snap.get("sounding_lifted_index"),
            "sounding_convective_risk": snap.get("sounding_convective_risk"),
        })

    snapshot_insert_ignore(db, rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Phase C: Fetch METAR + TAF
# ---------------------------------------------------------------------------

def _fetch_and_store_observations(
    airports: list[WatchlistAirport],
    airports_db_path: str,
    db,
) -> int:
    """Fetch current METARs for all watchlist airports and store them.

    Reuses the existing flight verification infrastructure.
    """
    from weatherbrief.tasks.verification import fetch_observations_batch, store_observations

    icaos = [a.icao for a in airports]
    observations = fetch_observations_batch(icaos, airports_db_path)
    return store_observations(observations, {}, db)


# ---------------------------------------------------------------------------
# Phase D: Score against stored forecasts
# ---------------------------------------------------------------------------

def _snapshot_to_hourly(snap: AirportForecastSnapshotRow):
    """Convert a DB snapshot row to a minimal HourlyForecast for scoring."""
    from weatherbrief.models.analysis import HourlyForecast, NWPCloudDiagnostics

    nwp_diag = None
    if snap.nwp_ceiling_ft is not None:
        nwp_diag = NWPCloudDiagnostics(ceiling_ft=snap.nwp_ceiling_ft)

    return HourlyForecast(
        time=snap.forecast_hour,
        temperature_2m_c=snap.temperature_2m_c,
        dewpoint_2m_c=snap.dewpoint_2m_c,
        visibility_m=snap.visibility_m,
        wind_speed_10m_kt=snap.wind_speed_10m_kt,
        wind_direction_10m_deg=snap.wind_direction_10m_deg,
        wind_gusts_10m_kt=snap.wind_gusts_10m_kt,
        precipitation_mm=snap.precipitation_mm,
        snowfall_cm=snap.snowfall_cm,
        # precip_period_h is deliberately not carried into HourlyForecast: that
        # model is shared with the briefing pipeline, where precipitation is
        # always hourly. Scoring still reads a 3h/6h ECMWF total as if it were
        # hourly — see #415; the window is on the row for that fix to use.
        cape_jkg=snap.cape_jkg,
        cloud_cover_pct=snap.cloud_cover_pct,
        cloud_cover_low_pct=snap.cloud_cover_low_pct,
        nwp_cloud_diagnostics=nwp_diag,
    )



def _score_cycle(
    cycle_time: datetime,
    airports: list[WatchlistAirport],
    airports_db_path: str,
    db,
) -> int:
    """Score observations at cycle_time against all matching forecast snapshots.

    Returns number of scores created.
    """
    from weatherbrief.airports import get_airport_elevations, get_runway_ends
    from weatherbrief.analysis.airport_conditions import (
        _nwp_ceiling_is_agl,
        to_agl_ceiling,
    )
    from weatherbrief.tasks.scoring import _score_model_vs_metar, _score_taf_vs_metar

    # Fetch all snapshots that predict within ±90 min of cycle_time
    window_start = cycle_time - timedelta(minutes=90)
    window_end = cycle_time + timedelta(minutes=90)

    matching_snapshots = db.execute(
        select(AirportForecastSnapshotRow)
        .where(AirportForecastSnapshotRow.forecast_hour.between(window_start, window_end))
        .order_by(
            AirportForecastSnapshotRow.icao,
            AirportForecastSnapshotRow.model,
            AirportForecastSnapshotRow.model_init_time,
        )
    ).scalars().all()

    if not matching_snapshots:
        logger.info("No forecast snapshots matching cycle time %s", cycle_time)
        return 0

    # Fetch observations around cycle_time
    obs_rows = db.execute(
        select(VerificationObservationRow)
        .where(VerificationObservationRow.observation_time.between(window_start, window_end))
    ).scalars().all()

    if not obs_rows:
        logger.info("No observations near cycle time %s", cycle_time)
        return 0

    # Build lookup: icao → observation (closest to cycle_time)
    def _ensure_utc(dt: datetime) -> datetime:
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    obs_by_icao: dict[str, VerificationObservationRow] = {}
    for obs in obs_rows:
        icao = obs.icao
        if icao not in obs_by_icao:
            obs_by_icao[icao] = obs
        else:
            # Keep the one closest to cycle_time
            existing_delta = abs((_ensure_utc(obs_by_icao[icao].observation_time) - cycle_time).total_seconds())
            new_delta = abs((_ensure_utc(obs.observation_time) - cycle_time).total_seconds())
            if new_delta < existing_delta:
                obs_by_icao[icao] = obs

    # Load runway data for wind advisory scoring
    unique_icaos = list(set(obs_by_icao.keys()))
    runway_map = get_runway_ends(unique_icaos, airports_db_path)
    # Field elevation for the ceiling AGL conversion, mirroring score_flight
    # (#441 #3). Enhancement, not required: on failure scoring stays datum-naive.
    try:
        elev_map = get_airport_elevations(unique_icaos, airports_db_path)
    except Exception:
        logger.warning("Elevation lookup failed; standalone scoring stays datum-naive", exc_info=True)
        elev_map = {}

    # Bulk-fetch existing score keys to avoid per-row duplicate checks
    def _strip_tz(dt: datetime) -> datetime:
        return dt.replace(tzinfo=None) if dt and dt.tzinfo else dt

    existing_score_keys: set[tuple] = set()
    score_key_rows = db.execute(
        select(
            VerificationScoreRow.icao,
            VerificationScoreRow.observation_time,
            VerificationScoreRow.model,
            VerificationScoreRow.model_init_time,
        ).where(
            VerificationScoreRow.source == "standalone",
            VerificationScoreRow.observation_time.between(window_start, window_end),
        )
    ).all()
    for r in score_key_rows:
        existing_score_keys.add((r[0], _strip_tz(r[1]), r[2], _strip_tz(r[3])))

    existing_taf_keys: set[tuple] = set()
    taf_key_rows = db.execute(
        select(
            TafVerificationScoreRow.icao,
            TafVerificationScoreRow.observation_time,
            TafVerificationScoreRow.taf_issue_time,
        ).where(
            TafVerificationScoreRow.source == "standalone",
            TafVerificationScoreRow.observation_time.between(window_start, window_end),
        )
    ).all()
    for r in taf_key_rows:
        existing_taf_keys.add((r[0], _strip_tz(r[1]), _strip_tz(r[2])))

    scores_created = 0

    for snap in matching_snapshots:
        obs = obs_by_icao.get(snap.icao)
        if obs is None:
            continue

        # Compute days_out
        snap_init = snap.model_init_time
        if snap_init.tzinfo is None:
            snap_init = snap_init.replace(tzinfo=timezone.utc)
        fh = snap.forecast_hour
        if fh.tzinfo is None:
            fh = fh.replace(tzinfo=timezone.utc)
        days_out = (fh.date() - snap_init.date()).days

        # Check duplicate via in-memory set (strip tz for SQLite compat)
        score_key = (snap.icao, _strip_tz(obs.observation_time), snap.model, _strip_tz(snap.model_init_time))
        if score_key in existing_score_keys:
            continue

        weather = json.loads(obs.weather) if obs.weather else []
        runway_ends = runway_map.get(snap.icao, [])

        hourly = _snapshot_to_hourly(snap)
        # Reconstruct sounding proxy from stored snapshot fields (no API call)
        sounding_proxy = _build_sounding_proxy(snap)
        score_row = _score_model_vs_metar(
            obs_row=obs,
            obs_weather=weather,
            sounding=sounding_proxy,
            hourly=hourly,
            runway_ends=runway_ends,
            model=snap.model,
            model_init_time=snap.model_init_time,
            days_out=days_out,
            source="standalone",
            field_elevation_ft=elev_map.get(snap.icao),
        )

        if score_row is not None:
            # Add cloud_base and LCL deltas, both against the AGL METAR ceiling.
            # cloud_base_ft carries the model's datum — MSL for GFS/ICON/HRRR,
            # AGL for ECMWF — so it is converted first; lcl_ft is the Espy
            # surface approximation, a height above the station and therefore
            # already AGL, so it differences directly. (#441 #3)
            if obs.ceiling_ft is not None:
                obs_ceiling_ft = float(obs.ceiling_ft)
                if snap.cloud_base_ft is not None:
                    cloud_base_agl = to_agl_ceiling(
                        snap.cloud_base_ft,
                        elev_map.get(snap.icao),
                        source_is_agl=_nwp_ceiling_is_agl(snap.model),
                    )
                    if cloud_base_agl is not None:
                        score_row.cloud_base_delta_ft = cloud_base_agl - obs_ceiling_ft
                if snap.lcl_ft is not None:
                    score_row.lcl_delta_ft = snap.lcl_ft - obs_ceiling_ft
            db.add(score_row)
            existing_score_keys.add(score_key)
            scores_created += 1

    # TAF scoring
    for obs in obs_by_icao.values():
        if obs.taf_issue_time is None:
            continue

        taf_key = (obs.icao, _strip_tz(obs.observation_time), _strip_tz(obs.taf_issue_time))
        if taf_key in existing_taf_keys:
            continue

        weather = json.loads(obs.weather) if obs.weather else []
        runway_ends = runway_map.get(obs.icao, [])

        taf_row = _score_taf_vs_metar(obs, weather, runway_ends, source="standalone")
        if taf_row is not None:
            db.add(taf_row)
            existing_taf_keys.add(taf_key)
            scores_created += 1

    db.flush()
    return scores_created


# ---------------------------------------------------------------------------
# Phase E: Prune old forecast snapshots
# ---------------------------------------------------------------------------

def _prune_old_snapshots(db, retention_days: int = 10) -> int:
    """Delete forecast snapshots older than retention_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = db.execute(
        AirportForecastSnapshotRow.__table__.delete().where(
            AirportForecastSnapshotRow.fetched_at < cutoff
        )
    )
    return result.rowcount


# ---------------------------------------------------------------------------
# Main cycle orchestrator
# ---------------------------------------------------------------------------

def run_standalone_cycle(
    airports: list[WatchlistAirport],
    airports_db_path: str,
    *,
    fetch_forecasts: bool = True,
    score_observations: bool = True,
    pool_soundings: bool = False,
    region: str = "eu",
) -> dict:
    """Run one standalone cycle.

    Three modes by flag combination:

      * ``fetch_forecasts=True, score_observations=True`` → ``"full"``: combined
        fetch + score (legacy; used by CLI and manual runs).
      * ``fetch_forecasts=True, score_observations=False`` → ``"forecast"``:
        fetch + store snapshots only. Used by the forecast-fetch loop, which
        fires after ECMWF deliveries land.
      * ``fetch_forecasts=False, score_observations=True`` → ``"light"``:
        Score-only. Reads observations already stored in the DB (populated
        by :func:`run_metar_ingest_cycle`) — does NOT call aviationweather.gov.

    The remaining ``(False, False)`` combination is rejected.

    Note: METAR fetch is owned by :func:`run_metar_ingest_cycle`, which fires
    every 30 min on its own loop. Scoring cycles only read from DB.

    ``pool_soundings`` opts sounding analysis into the decode process pool
    (#448 PR B). Only the standalone CLI passes ``True`` — its process owns
    a disposable pool. The scheduler's in-process fallback
    (``STANDALONE_SUBPROCESS=0``) keeps the default ``False``: it runs inside
    the uvicorn process, where pooling would stream ~560 BACKGROUND batch
    jobs into the web app's interactive decode pool for the whole fetch
    phase — far beyond the accepted handful of decode dispatches.

    ``region`` selects the model set (``STANDALONE_MODELS_BY_REGION``, EU default)
    and tags every stored snapshot row (EU/US seam). Callers pass a concrete
    region; only ``eu`` is onboarded today.

    Returns a summary dict with counts and timing.
    """
    if not fetch_forecasts and not score_observations:
        raise ValueError("must enable at least one of fetch_forecasts or score_observations")

    # Guard the region at the library boundary, not just in the CLI: an
    # unonboarded region would fetch the EU watchlist with the fallback EU model
    # set and tag every stored row with the caller's region string — silently
    # mislabeling data. Fail loudly for ANY caller (scheduler, admin hub, script,
    # test), not only cmd_standalone. Onboarding a region = add its model set here
    # (and a region-aware watchlist).
    if region not in STANDALONE_MODELS_BY_REGION:
        raise ValueError(
            f"region {region!r} is not onboarded — no model set in "
            f"STANDALONE_MODELS_BY_REGION (have {sorted(STANDALONE_MODELS_BY_REGION)}). "
            "Add its model set and a region-aware watchlist before running it."
        )

    from flyfun_common.db import SessionLocal
    from weatherbrief.fetch.grib import DecodePriority
    from weatherbrief.fetch.model_status import fetch_model_metadata

    if fetch_forecasts and score_observations:
        cycle_type = "full"
    elif fetch_forecasts:
        cycle_type = "forecast"
    else:
        cycle_type = "light"

    t_start = time.monotonic()
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    session = requests.Session()

    # Sample parent RSS + cgroup memory every 5s for the entire cycle so
    # transient peaks during fetch (e.g. concurrent in-flight Open-Meteo
    # chunks at ~2.5 GiB) are visible, not just the post-`del snapshots`
    # plateau the boundary log lines capture. Persisted in the cycle row.
    memory_sampler = MemorySampler(interval_seconds=5.0)
    memory_sampler.start()

    try:
        models_fetched = 0
        snapshots_stored = 0
        total_api_calls = 0
        obs_stored = 0
        scores_created = 0
        pruned = 0

        _rss_log(f"start ({cycle_type})")

        if fetch_forecasts:
            # EDR calibration accumulator (issue #221) no longer instantiated
            # (#448): it iterated every derived level of ~56K soundings per
            # cycle while collecting nothing — `analyze_sounding_lite` never
            # computes Richardson, so every level was dropped on arrival. The
            # `edr_acc` plumbing below is retained for a future subsampled
            # calibration design; passing None short-circuits it entirely.
            edr_acc = None

            # Phase A+B: Fetch and store forecasts per model. One model at a
            # time to bound memory — each model's snapshots are stored and
            # freed before the next fetch.
            models = STANDALONE_MODELS_BY_REGION.get(region, STANDALONE_MODELS)
            metadata = fetch_model_metadata(models)
            for model in models:
                meta = metadata.get(model)
                if meta is None:
                    logger.warning("No metadata for model %s, skipping", model)
                    continue

                om_init_time = datetime.fromtimestamp(meta.last_init_time, tz=timezone.utc)

                # ECMWF: prefer direct GRIB delivery when its init is at least as
                # fresh as Open-Meteo's. Open-Meteo republishes ECMWF IFS-HRES
                # with a 7–9h lag, so the 07/19Z fetch consistently saw the
                # prior 18Z bc-run; direct GRIB lands ~6h after init.
                map_days = MAP_FORECAST_DAYS.get(model, 4)

                grib_run_files = None
                if model == "ecmwf":
                    grib_run_files = _select_ecmwf_grib_run(om_init_time, map_days)

                if grib_run_files is not None:
                    init_time = grib_run_files[0].base_time
                    if init_time.tzinfo is None:
                        init_time = init_time.replace(tzinfo=timezone.utc)
                else:
                    init_time = om_init_time

                existing = db.execute(
                    select(AirportForecastSnapshotRow.id)
                    .where(AirportForecastSnapshotRow.model == model)
                    .where(AirportForecastSnapshotRow.model_init_time == init_time)
                    .limit(1)
                ).scalar_one_or_none()

                if existing is not None:
                    logger.info("Model %s init %s already stored, skipping fetch", model, init_time)
                    continue

                if grib_run_files is not None:
                    logger.info(
                        "Fetching ECMWF forecasts via GRIB (init %s) for %d airports",
                        init_time, len(airports),
                    )
                    snapshots = fetch_ecmwf_grib_snapshots(
                        grib_run_files, airports,
                        None,  # per-day grid: five hours near, three at the far day
                        map_days,
                        edr_acc=edr_acc,
                        # Standalone cycle decode stays deprioritised behind any
                        # interactive briefing. (The cycle ContextVar also
                        # resolves to BACKGROUND; explicit here for clarity.)
                        priority=DecodePriority.BACKGROUND,
                        pool_soundings=pool_soundings,
                    )
                    api_calls = 0
                    logger.info(
                        "Model ecmwf: %d snapshot values from GRIB (%d files)",
                        len(snapshots), len(grib_run_files),
                    )
                else:
                    logger.info("Fetching %s forecasts (init %s) for %d airports",
                                model, init_time, len(airports))
                    snapshots, api_calls = _fetch_forecasts_for_model(
                        model, init_time, airports, session, edr_acc=edr_acc,
                        days=map_days,
                        pool_soundings=pool_soundings,
                    )
                    total_api_calls += api_calls
                    logger.info("Model %s: %d snapshot values from Open-Meteo (%d API calls)",
                                model, len(snapshots), api_calls)

                    _enrich_with_grib(
                        snapshots, model, init_time, airports, session,
                        priority=DecodePriority.BACKGROUND,
                    )

                stored = _store_snapshots(snapshots, db, region=region)
                db.commit()
                snapshots_stored += stored
                logger.info("Model %s: stored %d snapshots", model, stored)
                del snapshots
                models_fetched += 1
                _rss_log(f"after {model}")

        else:
            logger.info("Skipping forecast fetch (score-only cycle)")

        t_fetch_done = time.monotonic()

        if score_observations:
            # Phase D: Score against snapshots + observations already in DB.
            # METAR fetch is owned by run_metar_ingest_cycle on its own loop —
            # this cycle is a pure-DB operation now.
            t_obs_done = time.monotonic()
            scores_created = _score_cycle(now, airports, airports_db_path, db)
            t_score_done = time.monotonic()
            logger.info("Created %d scores", scores_created)
        else:
            t_obs_done = t_fetch_done
            t_score_done = t_fetch_done
            logger.info("Skipping observations + scoring (fetch-only cycle)")

        # Phase E: Prune (always — cheap, keeps the table bounded).
        pruned = _prune_old_snapshots(db)
        if pruned:
            logger.info("Pruned %d old forecast snapshots", pruned)

        # Log API usage
        if total_api_calls > 0:
            from weatherbrief.api.usage import log_api_usage
            log_api_usage(
                db,
                service="open_meteo",
                pipeline="verification",
                api_calls=total_api_calls,
            )

        # Record cycle metrics
        t_end = time.monotonic()
        duration_ms = int((t_end - t_start) * 1000)

        # Stop the sampler before building the cycle row so peaks land in the
        # same DB transaction as the rest of the metrics.
        peaks = memory_sampler.stop()
        cycle_source = f"standalone_{cycle_type}"

        cycle_row = VerificationCycleRow(
            started_at=now,
            duration_ms=duration_ms,
            source=cycle_source,
            # fetch+store is interleaved per model, so combined into phase_fetch
            phase_fetch_ms=int((t_fetch_done - t_start) * 1000),
            phase_gather_ms=int((t_obs_done - t_fetch_done) * 1000),
            phase_score_ms=int((t_score_done - t_obs_done) * 1000),
            airports=len(airports),
            observations_stored=obs_stored,
            scored=scores_created,
            peak_rss_mb=peaks.peak_rss_mb,
            peak_cgroup_mb=peaks.peak_cgroup_mb,
        )
        db.add(cycle_row)
        db.commit()

        _rss_log(f"end ({cycle_type})")
        # The sampled peaks ride the boundary line rather than a bespoke log of
        # their own. This cycle had the windowed figures all along, yet its
        # boundary line still ended in the bare since-container-start peak —
        # the exact ambiguity #506 removes, left standing at the one call site
        # that could already have avoided it (PR #507 review).
        log_memory(
            f"standalone {cycle_type}", logger,
            task_peak_rss_mb=peaks.peak_rss_mb,
            task_peak_cgroup_mb=peaks.peak_cgroup_mb,
            task_peak_samples=peaks.samples,
        )

        # Anomaly check runs after commit so the current cycle's row exists
        # in the table — keeps the baseline query consistent across cycles.
        # Failures here must not propagate (they'd mark a successful cycle as
        # failed in `_record_failed_cycle`).
        try:
            _check_memory_anomaly(db, cycle_source, peaks, cycle_started_at=now)
        except Exception:
            logger.warning("Memory anomaly check failed", exc_info=True)

        return {
            "cycle_type": cycle_type,
            "models_fetched": models_fetched,
            "snapshots_stored": snapshots_stored,
            "observations_stored": obs_stored,
            "scores_created": scores_created,
            "pruned": pruned,
            "duration_ms": duration_ms,
        }

    except Exception:
        db.rollback()
        # Best-effort sampler stop on the error path so the daemon thread
        # doesn't outlive the cycle. Peaks aren't persisted in the failed
        # cycle row (kept simple — the DB-side _record_failed_cycle uses a
        # fresh session for isolation).
        try:
            memory_sampler.stop()
        except Exception:
            pass
        # Record the failure in a separate session so the error row survives
        _record_failed_cycle(now, t_start, cycle_type, len(airports))
        raise
    finally:
        session.close()
        db.close()


# ---------------------------------------------------------------------------
# METAR ingest cycle — fires every 30 min, decoupled from scoring
# ---------------------------------------------------------------------------


def run_metar_ingest_cycle(
    airports: list[WatchlistAirport],
    airports_db_path: str,
) -> dict:
    """Fetch METAR/TAF for the watchlist and upsert into verification_observations.

    Called from the 30-min METAR ingest loop. Does no forecast fetch and no
    scoring — those are owned by the forecast-fetch and verification loops
    respectively. The 30-min bucket dedup in :func:`store_observations`
    means cycles firing at HH:05 and HH:35 each insert at most one row per
    airper-bucket, so SPECIs land alongside regular reports.

    Returns a summary dict; persists a row in ``verification_cycles`` with
    ``source='metar_ingest'``.
    """
    from flyfun_common.db import SessionLocal

    t_start = time.monotonic()
    started_at = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        try:
            obs_stored = _fetch_and_store_observations(
                airports, airports_db_path, db,
            )
            db.commit()
        except Exception:
            db.rollback()
            _record_failed_cycle(
                started_at, t_start, "metar_ingest", len(airports),
            )
            raise

        duration_ms = int((time.monotonic() - t_start) * 1000)

        cycle_row = VerificationCycleRow(
            started_at=started_at,
            duration_ms=duration_ms,
            source="metar_ingest",
            phase_fetch_ms=duration_ms,
            airports=len(airports),
            observations_stored=obs_stored,
            scored=0,
        )
        db.add(cycle_row)
        db.commit()

        return {
            "cycle_type": "metar_ingest",
            "airports": len(airports),
            "observations_stored": obs_stored,
            "duration_ms": duration_ms,
        }
    finally:
        db.close()


def run_post_cycle_tasks(airports_db_path: str, cycle_type: str) -> None:
    """Daily-stats rollup + dashboard cache rebuild after a standalone cycle.

    Shared by the scheduler's in-process fallback path and the CLI
    (``--with-rollup``) so the subprocess path (#236) keeps post-cycle
    behaviour identical. Each step is independently fail-safe — a rollup
    failure must not block the cache rebuild, and neither failure marks
    the cycle itself as failed.

    Cycle-aware (#448): each cycle type only rebuilds what it can have
    changed. Forecast cycles write snapshots but create no scores, so the
    rollup and the stats/leaderboard caches are input-unchanged; light
    cycles score but don't touch snapshots, so the forecast_map cache is
    input-unchanged. The legacy combined ``full`` cycle rebuilds everything.

    Caveat: the "input-unchanged" invariant holds for standalone-source
    stats only. Flight-source scores accumulate continuously on the 10-min
    ``run_verification_loop``, so skipping the stats rebuild on forecast
    cycles means ``stats:flight:*`` refreshes on light cycles only (5x/day
    instead of 7x). Acceptable: ``is_stale()`` trips on the cached entry and
    the admin dashboard falls back to a live query, which is cheap for the
    small flight source.
    """
    from flyfun_common.db import SessionLocal

    include_score_stats = cycle_type != "forecast"
    include_forecast_map = cycle_type != "light"

    # Roll up freshly-scored days into verification_daily_stats BEFORE the
    # cache rebuild so the cache reflects today's scores. Idempotent — re-
    # rolls "yesterday" once per cycle, plus any older missing days. Cheap
    # (~12K rows/day, pure SQL).
    if include_score_stats:
        try:
            from weatherbrief.tasks.verification_daily_rollup import (
                rollup_today_and_pending,
            )

            rollup_db = SessionLocal()
            try:
                n_rows = rollup_today_and_pending(rollup_db)
                rollup_db.commit()
                if n_rows:
                    logger.info(
                        "Standalone %s cycle: %d daily-stats rows rolled up",
                        cycle_type, n_rows,
                    )
            except Exception:
                rollup_db.rollback()
                raise
            finally:
                rollup_db.close()
        except Exception:
            logger.error("verification_daily_stats rollup failed", exc_info=True)
    else:
        logger.info(
            "Standalone %s cycle: skipping rollup + stats/leaderboard cache "
            "(cycle creates no scores)",
            cycle_type,
        )

    try:
        from weatherbrief.tasks.cache_builder import rebuild_all

        cache_db = SessionLocal()
        try:
            cache_result = rebuild_all(
                cache_db, airports_db_path,
                include_forecast_map=include_forecast_map,
                include_score_stats=include_score_stats,
            )
            logger.info(
                "Standalone %s cycle: cache rebuilt (%dms)",
                cycle_type, cache_result["duration_ms"],
            )
        finally:
            cache_db.close()
    except Exception:
        logger.error("Cache rebuild failed", exc_info=True)


def _record_failed_cycle(
    started_at: datetime,
    t_start: float,
    cycle_type: str,
    airport_count: int,
    error_message: str | None = None,
) -> None:
    """Commit a VerificationCycleRow with error info using a fresh session.

    ``error_message`` overrides the default traceback capture — used by the
    scheduler's subprocess supervisor (#236), which records failures for
    child cycles that died without reaching their own exception path (e.g.
    SIGKILL from the OOM killer, or a supervisor timeout).
    """
    import traceback

    from flyfun_common.db import SessionLocal

    duration_ms = int((time.monotonic() - t_start) * 1000)
    error_msg = (error_message or traceback.format_exc())[-500:]  # last 500 chars

    # New metar_ingest cycle keeps its bare source name; the legacy
    # standalone cycles prefix it for backward-compat with existing queries.
    source = cycle_type if cycle_type == "metar_ingest" else f"standalone_{cycle_type}"

    try:
        err_db = SessionLocal()
        cycle_row = VerificationCycleRow(
            started_at=started_at,
            duration_ms=duration_ms,
            source=source,
            phase_fetch_ms=0,
            phase_find_ms=0,
            phase_gather_ms=0,
            phase_score_ms=0,
            phase_finalize_ms=0,
            airports=airport_count,
            observations_stored=0,
            scored=0,
            error=error_msg,
        )
        err_db.add(cycle_row)
        err_db.commit()
        err_db.close()
        logger.info("Recorded failed %s cycle in DB (%dms)", cycle_type, duration_ms)
    except Exception:
        logger.warning("Failed to record error cycle row", exc_info=True)
