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

import requests
from sqlalchemy import select, tuple_

from weatherbrief.db.models import (
    AirportForecastSnapshotRow,
    TafVerificationScoreRow,
    VerificationCycleRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.process_memory_sampler import MemorySampler, MemoryPeaks
from weatherbrief.process_rss import current_rss_mb
from weatherbrief.tasks.airport_watchlist import WatchlistAirport

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
    db,
    source: str,
    peaks: MemoryPeaks,
) -> None:
    """Emit a WARN log if this cycle's peak RSS is anomalously high.

    Two complementary triggers:
    - **Relative**: peak > ``_RSS_ANOMALY_RELATIVE_FACTOR`` × median of the
      last 10 same-source cycles. Catches gradual creep.
    - **Absolute**: peak > ``_RSS_ANOMALY_ABSOLUTE_PCT_OF_CGROUP`` of the
      cgroup limit. Catches "we're getting too close regardless of trend."

    The relative check is skipped until at least
    ``_RSS_ANOMALY_MIN_BASELINE_SAMPLES`` populated rows exist for the same
    source (otherwise a single high outlier becomes its own baseline and
    suppresses warnings on subsequent runs).
    """
    if peaks.peak_rss_mb is None:
        return

    cgroup_limit_mb = _read_cgroup_limit_mb()
    absolute_threshold_mb: int | None = None
    if cgroup_limit_mb is not None:
        absolute_threshold_mb = int(cgroup_limit_mb * _RSS_ANOMALY_ABSOLUTE_PCT_OF_CGROUP)

    prev = db.execute(
        select(VerificationCycleRow.peak_rss_mb)
        .where(VerificationCycleRow.source == source)
        .where(VerificationCycleRow.peak_rss_mb.is_not(None))
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
    if relative_threshold_mb is not None and peaks.peak_rss_mb > relative_threshold_mb:
        triggered = True
    if absolute_threshold_mb is not None and peaks.peak_rss_mb > absolute_threshold_mb:
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
SAMPLE_HOURS_UTC = [6, 9, 12, 15, 18]  # verification target hours (cycle_time, forecast-bucket UI)
FULL_CYCLE_HOURS_UTC = {6, 18}  # legacy: combined fetch+score; kept for CLI/tests, no longer scheduled

# Fetch loop fires ~30 min after each ECMWF delivery (00Z lands ~06:35,
# 12Z lands ~18:35). Picks up the freshest GFS/ICON/ECMWF inits before the
# next verification cycle reads from DB.
FORECAST_FETCH_HOURS_UTC = [7, 19]

# Verification loop fires on the synoptic-bucket hours. cycle_time = wall
# clock, so METARs are pulled near each bucket and scored against whatever
# snapshots are already in DB (most recent fetch wins).
VERIFICATION_HOURS_UTC = [6, 9, 12, 15, 18]

# Model forecast horizon — 4 days is enough for actionable verification stats
MODEL_FORECAST_DAYS = {
    "gfs": 4,
    "icon": 4,
    "ecmwf": 4,
}

_OPEN_METEO_BATCH_SIZE = 100  # airports per Open-Meteo API call (also retry boundary)
_OPEN_METEO_CONCURRENCY = int(os.environ.get("STANDALONE_FETCH_CONCURRENCY", "4"))
_LCL_CONSTANT_FT = 400  # 400 * (T - Td) approximation for LCL in feet


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
) -> tuple[list[dict], int]:
    """Fetch Open-Meteo surface forecasts for all airports for one model.

    Returns (snapshots, api_call_count) — list of dicts with airport ICAO
    and per-sample-hour values filtered to SAMPLE_HOURS_UTC, plus the number
    of actual HTTP requests made.
    """
    from weatherbrief.models.analysis import ModelSource, RoutePoint
    from weatherbrief.fetch.open_meteo import OpenMeteoClient

    model_source = ModelSource(model)
    forecast_days = MODEL_FORECAST_DAYS.get(model, 7)

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
                forecasts = client.fetch_multi_point(
                    points, model_source,
                    start_date=start_date, end_date=end_date,
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
        chunk_results: list[dict] = []
        for airport, wpf in zip(chunk_list, forecasts):
            # Filter to sample hours only
            for hourly in wpf.hourly:
                utc_hour = hourly.time.hour if hasattr(hourly.time, 'hour') else None
                if utc_hour not in SAMPLE_HOURS_UTC:
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
                    "cape_jkg": hourly.cape_jkg,
                    "cloud_cover_pct": hourly.cloud_cover_pct,
                    "cloud_cover_low_pct": hourly.cloud_cover_low_pct,
                    "lcl_ft": lcl_ft,
                }

                # Run sounding analysis on pressure levels (already fetched)
                _enrich_with_sounding(snap, hourly, model)

                chunk_results.append(snap)
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


def _enrich_with_sounding(snap: dict, hourly, model: str) -> None:
    """Run sounding analysis on pressure-level data and store results in snap dict.

    Fails silently — surface data is preserved even if sounding analysis fails.
    """
    if not getattr(hourly, "pressure_levels", None):
        return

    try:
        from weatherbrief.analysis.sounding import analyze_sounding_lite
        from weatherbrief.models.analysis import CloudCoverage

        sounding = analyze_sounding_lite(
            hourly.pressure_levels, hourly, model_key=model,
        )
        if sounding is None:
            return

        # Thermodynamic indices
        if sounding.indices:
            snap["sounding_ceiling_ft"] = sounding.indices.sounding_ceiling_ft
            snap["freezing_level_ft"] = sounding.indices.freezing_level_ft
            snap["sounding_cape_jkg"] = sounding.indices.cape_surface_jkg
            snap["sounding_cin_jkg"] = sounding.indices.cin_surface_jkg
            snap["sounding_lifted_index"] = sounding.indices.lifted_index

        # Lowest BKN/OVC cloud layer base → sounding_cloud_base_ft
        bkn_ovc = [
            cl for cl in sounding.dd_cloud_layers
            if cl.coverage in (CloudCoverage.BKN, CloudCoverage.OVC)
        ]
        if bkn_ovc:
            lowest = min(bkn_ovc, key=lambda cl: cl.base_ft)
            snap["sounding_cloud_base_ft"] = lowest.base_ft

        # Convective risk
        if sounding.convective and sounding.convective.risk_level is not None:
            snap["sounding_convective_risk"] = sounding.convective.risk_level.value

    except Exception:
        logger.warning(
            "Sounding analysis failed for %s %s, surface data preserved",
            snap.get("icao"), model, exc_info=True,
        )


def _enrich_with_grib(
    snapshots: list[dict],
    model: str,
    init_time: datetime,
    airports: list[WatchlistAirport],
    session: requests.Session,
) -> None:
    """Enrich snapshot dicts with GRIB ceiling/cloud_base data in-place.

    Fetches GRIB cloud diagnostics for the model's forecast hours
    and maps nwp_ceiling_ft and cloud_base_ft onto the matching snapshot dicts.
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

    # Determine which forecast hours we need (as offsets from init)
    fhour_set: set[int] = set()
    for snap in snapshots:
        if snap["model"] != model:
            continue
        delta = snap["forecast_hour"] - init_time
        offset = int(delta.total_seconds() / 3600)
        if offset >= 0:
            fhour_set.add(offset)

    if not fhour_set:
        return

    forecast_hours = sorted(fhour_set)
    lats = [a.lat for a in airports]
    lons = [a.lon for a in airports]

    # Build ICAO → index mapping
    icao_to_idx = {a.icao: i for i, a in enumerate(airports)}

    fetch_fn = fetch_gfs_cloud_diag if model == "gfs" else fetch_icon_cloud_diag

    try:
        grib_data = fetch_fn(
            init_date, init_hour, forecast_hours, lats, lons, session=session,
        )
    except Exception:
        logger.warning("GRIB enrichment failed for %s", model, exc_info=True)
        return

    # Map GRIB data back to snapshots
    for snap in snapshots:
        if snap["model"] != model:
            continue
        delta = snap["forecast_hour"] - init_time
        fhour = int(delta.total_seconds() / 3600)
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
    sample_hours: list[int],
    days: int,
) -> list[dict]:
    """Decode an ECMWF run on disk into standalone-snapshot dicts.

    For each (day_offset × sample_hour) inside the run's horizon, opens the
    matching a1 (surface) and a2 (pressure-level) files once and extracts
    values for all ``airports``. Sounding-derived columns come from
    ``analyze_sounding_lite`` on the GRIB-native pressure-level profile.

    ``tp`` and ``sf`` arrive accumulated from init in the a1 GRIB, so this
    fetcher decodes the previous-hour step too and stores per-hour deltas
    to match Open-Meteo's hourly precipitation semantics.

    Args:
        run_files: ECMWFFileInfo list scoped to one run (single base_time).
        airports: Watchlist airports to decode for.
        sample_hours: UTC hours to sample (e.g. [6, 9, 12, 15, 18]).
        days: Forecast horizon in days from init.

    Returns:
        Snapshot dicts with the same shape as ``_fetch_forecasts_for_model``
        produces. Empty list when run_files has no usable steps.
    """
    from datetime import time as dt_time

    from weatherbrief.fetch.grib import _dispatch_decode
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

    # Cache decoded a1 dicts per step — step-diff for tp/sf reads the
    # previous-hour step, which is reused by the next iteration.
    a1_cache: dict[int, list[dict[str, float]]] = {}

    def _decode_a1(step_h: int) -> list[dict[str, float]]:
        if step_h in a1_cache:
            return a1_cache[step_h]
        a1_path = files_by_step.get(step_h, {}).get("a1")
        if a1_path is None:
            a1_cache[step_h] = empty
            return empty
        try:
            data, _ = _dispatch_decode(
                "decode_ecmwf_surface", str(a1_path), lats, lons,
            )
        except Exception:
            logger.warning("ECMWF a1 decode failed for step %dh", step_h, exc_info=True)
            data = empty
        a1_cache[step_h] = data
        return data

    snapshots: list[dict] = []
    max_step = max(files_by_step.keys())

    for day_offset in range(days + 1):
        target_date = (init_time + timedelta(days=day_offset)).date()
        for hour in sample_hours:
            target_dt = datetime.combine(
                target_date, dt_time(hour, 0, 0), tzinfo=timezone.utc,
            )
            step_h = int((target_dt - init_time).total_seconds() / 3600)
            if step_h <= 0 or step_h > max_step:
                continue
            if step_h not in files_by_step:
                continue

            cur_a1 = _decode_a1(step_h)
            prev_a1 = _decode_a1(step_h - 1) if (step_h - 1) > 0 else None

            a2_path = files_by_step[step_h].get("a2")
            pl_data: list[dict[int, dict[str, float]]] | None = None
            if a2_path is not None:
                try:
                    pl_data, _ = _dispatch_decode(
                        "decode_ecmwf_pressure", str(a2_path), lats, lons,
                    )
                except Exception:
                    logger.warning(
                        "ECMWF a2 decode failed for step %dh", step_h, exc_info=True,
                    )
                    pl_data = None

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
                        _enrich_with_sounding(snap, hourly, "ecmwf")

                snapshots.append(snap)

            # Drop the previous-step cache once we've moved past it; the
            # next iteration only re-reads (step_h, step_h-1).
            stale_keys = [k for k in a1_cache if k < step_h - 1]
            for k in stale_keys:
                a1_cache.pop(k, None)

    return snapshots


# ---------------------------------------------------------------------------
# Phase B: Store forecast snapshots in DB
# ---------------------------------------------------------------------------

def _normalize_key(icao: str, model: str, init_time: datetime, fhour: datetime) -> tuple:
    """Normalize a snapshot key by stripping tzinfo for consistent comparison.

    SQLite returns naive datetimes, so we strip tzinfo from all keys to match.
    """
    init_naive = init_time.replace(tzinfo=None) if init_time.tzinfo else init_time
    fhour_naive = fhour.replace(tzinfo=None) if fhour.tzinfo else fhour
    return (icao, model, init_naive, fhour_naive)


def _store_snapshots(snapshots: list[dict], db) -> int:
    """Insert new forecast snapshot rows, skipping duplicates. Returns count of new rows."""
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

    stored = 0
    now = datetime.now(timezone.utc)
    for snap in snapshots:
        key = _normalize_key(
            snap["icao"], snap["model"],
            snap["model_init_time"], snap["forecast_hour"],
        )
        if key in existing_keys:
            continue

        row = AirportForecastSnapshotRow(
            icao=snap["icao"],
            model=snap["model"],
            model_init_time=snap["model_init_time"],
            forecast_hour=snap["forecast_hour"],
            fetched_at=now,
            temperature_2m_c=snap.get("temperature_2m_c"),
            dewpoint_2m_c=snap.get("dewpoint_2m_c"),
            visibility_m=snap.get("visibility_m"),
            wind_speed_10m_kt=snap.get("wind_speed_10m_kt"),
            wind_direction_10m_deg=snap.get("wind_direction_10m_deg"),
            wind_gusts_10m_kt=snap.get("wind_gusts_10m_kt"),
            precipitation_mm=snap.get("precipitation_mm"),
            snowfall_cm=snap.get("snowfall_cm"),
            cape_jkg=snap.get("cape_jkg"),
            cloud_cover_pct=snap.get("cloud_cover_pct"),
            cloud_cover_low_pct=snap.get("cloud_cover_low_pct"),
            nwp_ceiling_ft=snap.get("nwp_ceiling_ft"),
            cloud_base_ft=snap.get("cloud_base_ft"),
            lcl_ft=snap.get("lcl_ft"),
            sounding_ceiling_ft=snap.get("sounding_ceiling_ft"),
            sounding_cloud_base_ft=snap.get("sounding_cloud_base_ft"),
            freezing_level_ft=snap.get("freezing_level_ft"),
            sounding_cape_jkg=snap.get("sounding_cape_jkg"),
            sounding_cin_jkg=snap.get("sounding_cin_jkg"),
            sounding_lifted_index=snap.get("sounding_lifted_index"),
            sounding_convective_risk=snap.get("sounding_convective_risk"),
        )
        db.add(row)
        existing_keys.add(key)  # prevent duplicates within same batch
        stored += 1

    db.flush()
    return stored


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
    from weatherbrief.airports import get_runway_ends
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
        )

        if score_row is not None:
            # Add cloud_base and LCL deltas
            if obs.ceiling_ft is not None:
                if snap.cloud_base_ft is not None:
                    score_row.cloud_base_delta_ft = snap.cloud_base_ft - float(obs.ceiling_ft)
                if snap.lcl_ft is not None:
                    score_row.lcl_delta_ft = snap.lcl_ft - float(obs.ceiling_ft)
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
) -> dict:
    """Run one standalone cycle.

    Three modes by flag combination:

      * ``fetch_forecasts=True, score_observations=True`` → ``"full"``: combined
        fetch + score (legacy; used by CLI and manual runs).
      * ``fetch_forecasts=True, score_observations=False`` → ``"forecast"``:
        fetch + store snapshots only. Used by the forecast-fetch loop, which
        fires after ECMWF deliveries land.
      * ``fetch_forecasts=False, score_observations=True`` → ``"light"``:
        METAR/TAF fetch + scoring only. Used by the verification loop, which
        scores observations against whatever snapshots are already in DB.

    The remaining ``(False, False)`` combination is rejected.

    Returns a summary dict with counts and timing.
    """
    if not fetch_forecasts and not score_observations:
        raise ValueError("must enable at least one of fetch_forecasts or score_observations")

    from flyfun_common.db import SessionLocal
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
            # Phase A+B: Fetch and store forecasts per model. One model at a
            # time to bound memory — each model's snapshots are stored and
            # freed before the next fetch.
            metadata = fetch_model_metadata(STANDALONE_MODELS)
            for model in STANDALONE_MODELS:
                meta = metadata.get(model)
                if meta is None:
                    logger.warning("No metadata for model %s, skipping", model)
                    continue

                om_init_time = datetime.fromtimestamp(meta.last_init_time, tz=timezone.utc)

                # ECMWF: prefer direct GRIB delivery when its init is at least as
                # fresh as Open-Meteo's. Open-Meteo republishes ECMWF IFS-HRES
                # with a 7–9h lag, so the 07/19Z fetch consistently saw the
                # prior 18Z bc-run; direct GRIB lands ~6h after init.
                grib_run_files = None
                if model == "ecmwf":
                    grib_run_files = _select_ecmwf_grib_run(
                        om_init_time, MODEL_FORECAST_DAYS.get(model, 4),
                    )

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
                        SAMPLE_HOURS_UTC,
                        MODEL_FORECAST_DAYS.get(model, 4),
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
                        model, init_time, airports, session,
                    )
                    total_api_calls += api_calls
                    logger.info("Model %s: %d snapshot values from Open-Meteo (%d API calls)",
                                model, len(snapshots), api_calls)

                    _enrich_with_grib(snapshots, model, init_time, airports, session)

                stored = _store_snapshots(snapshots, db)
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
            # Phase C: Fetch METAR/TAF
            obs_stored = _fetch_and_store_observations(airports, airports_db_path, db)
            t_obs_done = time.monotonic()
            logger.info("Stored %d new observations", obs_stored)

            # Phase D: Score against snapshots already in DB.
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
        if peaks.peak_rss_mb is not None or peaks.peak_cgroup_mb is not None:
            logger.info(
                "Standalone cycle peaks: rss=%sMB cgroup=%sMB samples=%d",
                peaks.peak_rss_mb if peaks.peak_rss_mb is not None else "n/a",
                peaks.peak_cgroup_mb if peaks.peak_cgroup_mb is not None else "n/a",
                peaks.samples,
            )

        # Anomaly check runs after commit so the current cycle's row exists
        # in the table — keeps the baseline query consistent across cycles.
        # Failures here must not propagate (they'd mark a successful cycle as
        # failed in `_record_failed_cycle`).
        try:
            _check_memory_anomaly(db, cycle_source, peaks)
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


def _record_failed_cycle(
    started_at: datetime,
    t_start: float,
    cycle_type: str,
    airport_count: int,
) -> None:
    """Commit a VerificationCycleRow with error info using a fresh session."""
    import traceback

    from flyfun_common.db import SessionLocal

    duration_ms = int((time.monotonic() - t_start) * 1000)
    error_msg = traceback.format_exc()[-500:]  # last 500 chars

    try:
        err_db = SessionLocal()
        cycle_row = VerificationCycleRow(
            started_at=started_at,
            duration_ms=duration_ms,
            source=f"standalone_{cycle_type}",
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
