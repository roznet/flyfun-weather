"""GRIB2 enrichment for cloud liquid water and ice mixing ratio.

Public API: enrich_forecasts() adds CLWMR/ICMR data and cloud diagnostics to
existing cross-section forecasts from GFS and ICON-EU GRIB2 data.
"""

from __future__ import annotations

import contextvars
import gc
import logging
import threading
import time as _time_mod
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import requests
from requests.adapters import HTTPAdapter

from weatherbrief.process_rss import current_rss_mb as _read_rss_mb

from weatherbrief.fetch.grib.cache import (
    cache_dir_for_run,
    cache_key,  # route-independent: keyed by (fhour, variable) only
    get_cached,
    is_cached,
    purge_old_runs,
    put_cached,
)
from weatherbrief.fetch.grib.gfs_idx import plan_byte_ranges, plan_cloud_diag_byte_ranges
from weatherbrief.fetch.grib.grib_fetch import (
    fetch_byte_ranges,
    fetch_cloud_diag_ranges,
    fetch_idx,
    find_latest_run,
)
from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    NWPCloudDiagnostics,
    RouteCrossSection,
    RoutePoint,
    WaypointForecast,
)

logger = logging.getLogger(__name__)

# GRIB downloads use 8 workers; pool_maxsize must be at least that large
# to avoid urllib3 "Connection pool is full, discarding connection" warnings.
_POOL_MAXSIZE = 12

_M_TO_FT = 3.28084


# ---------------------------------------------------------------------------
# Sub-stage timing + memory instrumentation for refresh-pipeline profiling.
#
# State lives on a per-call _GribTimer instance, propagated to inner functions
# (and into Phase-1 ThreadPoolExecutor workers) via a ContextVar. This makes
# the instrumentation correct under concurrent refreshes — the API runs
# refresh pipelines in a ``ThreadPoolExecutor(max_workers=2)``, so two
# enrich_forecasts() can be in flight at once without their counters mixing.
#
# Worker threads spawned by Phase-1 ThreadPoolExecutor must run inside the
# parent's contextvars copy — see _submit_with_context() below.
# ---------------------------------------------------------------------------


class _GribTimer:
    """Per-enrich_forecasts call: timing, gc, and RSS accumulators."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.timings: dict[str, float] = {}
        self.timing_counts: dict[str, int] = {}
        self.gc_seconds: float = 0.0
        self.gc_count: int = 0
        self.rss_baseline: float | None = _read_rss_mb()
        self.rss_max: dict[str, float] = {}
        self.rss_count: dict[str, int] = {}

    def _record_time(self, label: str, secs: float) -> None:
        with self._lock:
            self.timings[label] = self.timings.get(label, 0.0) + secs
            self.timing_counts[label] = self.timing_counts.get(label, 0) + 1

    @contextmanager
    def time(self, label: str):
        t0 = _time_mod.perf_counter()
        try:
            yield
        finally:
            self._record_time(label, _time_mod.perf_counter() - t0)

    def gc(self) -> None:
        """gc.collect() with cumulative timing accounting."""
        t0 = _time_mod.perf_counter()
        gc.collect()
        elapsed = _time_mod.perf_counter() - t0
        with self._lock:
            self.gc_seconds += elapsed
            self.gc_count += 1

    def rss_mark(self, label: str) -> None:
        """Record current RSS under *label*. Keeps max-per-label across calls."""
        rss = _read_rss_mb()
        if rss is None:
            return
        with self._lock:
            prior = self.rss_max.get(label)
            self.rss_max[label] = rss if prior is None else max(prior, rss)
            self.rss_count[label] = self.rss_count.get(label, 0) + 1

    def log_summary(self) -> None:
        # Snapshot under lock, format outside — avoids holding the lock through
        # logger formatting and matches the locking discipline used for writes.
        with self._lock:
            timings = dict(self.timings)
            counts = dict(self.timing_counts)
            gc_secs = self.gc_seconds
            gc_n = self.gc_count
            rss_max = dict(self.rss_max)
            rss_count = dict(self.rss_count)
            baseline = self.rss_baseline

        if timings:
            items = sorted(timings.items(), key=lambda kv: -kv[1])
            parts = [f"{label}={secs:.2f}s/{counts.get(label, 0)}" for label, secs in items]
            parts.append(f"gc={gc_secs:.2f}s/{gc_n}")
            logger.info("GRIB timing: %s", " ".join(parts))

        if rss_max:
            items = sorted(rss_max.items(), key=lambda kv: -kv[1])
            parts = []
            if baseline is not None:
                parts.append(f"baseline={int(baseline)}MB")
            for label, rss in items:
                n = rss_count.get(label, 0)
                if baseline is not None:
                    parts.append(f"{label}={int(rss)}MB(+{int(rss - baseline)}/{n})")
                else:
                    parts.append(f"{label}={int(rss)}MB/{n}")
            logger.info("GRIB RSS: %s", " ".join(parts))


_GRIB_TIMER: contextvars.ContextVar[_GribTimer | None] = contextvars.ContextVar(
    "_grib_timer", default=None,
)


def _timer() -> _GribTimer | None:
    return _GRIB_TIMER.get()


@contextmanager
def _grib_time(label: str):
    """Time a block under the active per-call timer; no-op if none is active."""
    t = _timer()
    if t is None:
        yield
        return
    with t.time(label):
        yield


def _grib_gc() -> None:
    """gc.collect() with cumulative timing accounting (or plain gc.collect if no timer)."""
    t = _timer()
    if t is None:
        gc.collect()
        return
    t.gc()


def _grib_rss_mark(label: str) -> None:
    t = _timer()
    if t is not None:
        t.rss_mark(label)


def _submit_with_context(pool: ThreadPoolExecutor, fn, /, *args, **kwargs):
    """Submit *fn* to *pool* with the caller's ContextVars copied in.

    ``ThreadPoolExecutor.submit`` does **not** propagate contextvars in any
    current Python (verified on 3.13.11: a plain ``pool.submit`` worker sees
    ContextVar defaults, not the caller's bound values; nested submits lose
    them at every level). Without this wrapper, worker threads see
    ``_GRIB_TIMER`` as the default ``None`` and ``_grib_time``/``_grib_gc``
    /``_grib_rss_mark`` calls become silent no-ops — losing the per-fhour
    sub-stage timings the instrumentation exists to capture.

    Apply at *every* ``submit`` site that needs the timer, including nested
    pools (e.g. the inner GFS pool inside an outer Phase-1 worker).
    """
    ctx = contextvars.copy_context()
    return pool.submit(ctx.run, fn, *args, **kwargs)


def _grib_session() -> requests.Session:
    """Create a requests session with a connection pool sized for parallel GRIB downloads."""
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=_POOL_MAXSIZE)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _apply_cloud_diagnostics(hourly: HourlyForecast, diag: NWPCloudDiagnostics) -> None:
    """Attach NWP cloud diagnostics. Open-Meteo cloud_cover_*_pct fields are
    preserved — they provide hourly-interpolated coverage that is more temporally
    accurate than forward-filled GRIB values on non-native hours."""
    hourly.nwp_cloud_diagnostics = diag
    # ECMWF deg0l: model-native freezing level overrides Open-Meteo's value.
    if diag.freezing_level_ft is not None:
        hourly.freezing_level_m = diag.freezing_level_ft / _M_TO_FT


def _forecast_hour_to_utc(init_date: str, init_hour: int, fhour: int) -> datetime:
    """Convert a GRIB run + forecast hour to an aware UTC datetime."""
    init_dt = datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc,
    )
    return init_dt + timedelta(hours=fhour)


def _run_info_to_timestamp(init_date: str, init_hour: int) -> int:
    """Convert GRIB run info (date string + hour) to a Unix timestamp."""
    return int(
        datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


def _matches_valid_time(hourly_time: datetime, valid_utc: datetime | None) -> bool:
    """Check if a forecast hourly entry matches the target valid time.

    Compares both date and hour to avoid cross-day collisions when GRIB
    steps span multiple days (e.g. ECMWF steps out to 192h).
    """
    if valid_utc is None:
        return True
    return hourly_time.date() == valid_utc.date() and hourly_time.hour == valid_utc.hour


def enrich_forecasts(
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    departure_time: datetime,
    *,
    data_dir: Path,
    flight_duration_hours: float = 0.0,
    progress_callback: Callable[[str, str | None], None] | None = None,
    as_of_time: datetime | None = None,
) -> tuple[dict[str, int], dict[str, str]]:
    """Enrich cross-section forecasts with cloud water from GRIB2 sources.

    Enriches GFS cross-sections with CLWMR/ICMR and cloud diagnostics.
    Enriches ICON cross-sections with QC/QI if route is within ICON-EU domain.

    This modifies PressureLevelData and HourlyForecast objects in-place.

    Args:
        cross_sections: Route cross-sections to enrich (modified in-place).
        all_forecasts: Waypoint forecasts (also enriched in-place).
        route_points: Route points for spatial interpolation.
        departure_time: Aware UTC datetime of flight departure.
        data_dir: Base data directory for caching.
        flight_duration_hours: Flight duration for per-hour enrichment.
        as_of_time: If set, only use model runs initialized before this time.

    Returns:
        Tuple of (grib_init_times, grib_skip_reasons):
        - grib_init_times: model name → GRIB init Unix timestamp.
        - grib_skip_reasons: model name → skip reason string (e.g. "out_of_range").
    """
    grib_init_times: dict[str, int] = {}
    grib_skip_reasons: dict[str, str] = {}

    timer = _GribTimer()
    token = _GRIB_TIMER.set(timer)
    try:
        return _enrich_forecasts_inner(
            timer, cross_sections, all_forecasts, route_points,
            departure_time, data_dir=data_dir,
            flight_duration_hours=flight_duration_hours,
            progress_callback=progress_callback,
            as_of_time=as_of_time,
        )
    finally:
        timer.log_summary()
        _GRIB_TIMER.reset(token)


def _enrich_forecasts_inner(
    timer: _GribTimer,
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    departure_time: datetime,
    *,
    data_dir: Path,
    flight_duration_hours: float = 0.0,
    progress_callback: Callable[[str, str | None], None] | None = None,
    as_of_time: datetime | None = None,
) -> tuple[dict[str, int], dict[str, str]]:
    """Inner body of enrich_forecasts; assumes _GRIB_TIMER is set to *timer*."""
    grib_init_times: dict[str, int] = {}
    grib_skip_reasons: dict[str, str] = {}

    timer.rss_mark("enrich_start")

    # Download GFS and ICON-EU in parallel (network-bound), but decode
    # sequentially (memory-bound). ICON-EU decode peaks at ~270MB per
    # variable; overlapping with GFS decode caused OOM.
    # ECMWF GRIB is local disk I/O — runs in parallel with network fetches.
    if progress_callback is not None:
        progress_callback("grib_enrichment", "GFS + ICON-EU + ECMWF GRIB")

    gfs_ts: int | None = None
    icon_ts: int | None = None
    icon_skip: str | None = None

    # Prepare ICON-EU context (run discovery, domain check, etc.)
    with _grib_time("icon_prepare"):
        icon_ctx = _prepare_icon_eu(
            cross_sections, route_points, departure_time,
            data_dir=data_dir,
            flight_duration_hours=flight_duration_hours,
            as_of_time=as_of_time,
        )

    # Phase 1: Download/decode in parallel — GFS (download+decode),
    # ICON-EU (download-only), ECMWF GRIB (local disk decode).
    # Workers must run inside the parent's contextvars copy so timing/RSS
    # marks land on this call's timer, not on whichever refresh happens to
    # be running in the sibling pipeline thread.
    with _grib_time("phase1_parallel"):
        with ThreadPoolExecutor(max_workers=3) as pool:
            gfs_future = _submit_with_context(
                pool, _enrich_gfs,
                cross_sections, all_forecasts, route_points,
                departure_time, data_dir=data_dir,
                flight_duration_hours=flight_duration_hours,
                as_of_time=as_of_time,
            )
            ecmwf_future = _submit_with_context(
                pool, _enrich_ecmwf,
                cross_sections, all_forecasts, route_points,
                departure_time,
                flight_duration_hours=flight_duration_hours,
                as_of_time=as_of_time,
            )
            if icon_ctx is not None:
                icon_dl_future = _submit_with_context(
                    pool, _prefetch_icon_eu_data, icon_ctx,
                )
            else:
                icon_dl_future = None

            gfs_ts = gfs_future.result()
            ecmwf_grib_ts = ecmwf_future.result()
            if icon_dl_future is not None:
                icon_dl_future.result()  # ensure downloads are cached
    timer.rss_mark("after_phase1")

    if ecmwf_grib_ts is not None:
        grib_init_times["ecmwf"] = ecmwf_grib_ts

    # Phase 2: Decode ICON-EU sequentially (memory-heavy, GFS is done).
    with _grib_time("phase2_icon_decode"):
        if icon_ctx is not None:
            icon_ts, icon_skip = _decode_and_merge_icon_eu(
                icon_ctx, cross_sections, all_forecasts, route_points,
            )
        else:
            icon_skip = icon_ctx  # None
    timer.rss_mark("after_phase2")

    if gfs_ts is not None:
        grib_init_times["gfs"] = gfs_ts
    if icon_ts is not None:
        grib_init_times["icon"] = icon_ts
    elif icon_skip is not None:
        grib_skip_reasons["icon"] = icon_skip

    # Forward-fill all GRIB-enriched fields to interpolated hours
    with _grib_time("propagate_all"):
        from weatherbrief.fetch.grib.fill import propagate_all
        propagate_all(cross_sections, all_forecasts)

    timer.rss_mark("enrich_end")
    return grib_init_times, grib_skip_reasons


# ---------------------------------------------------------------------------
# ECMWF GRIB enrichment (local disk)
# ---------------------------------------------------------------------------


def _enrich_ecmwf(
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    departure_time: datetime,
    *,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
    ecmwf_data_dir: Path | None = None,
) -> int | None:
    """Enrich ECMWF cross-sections with GRIB pressure-level and surface data.

    Reads ECMWF GRIB files from the ECPDS delivery directory. Uses
    ``ecmwf_data_dir`` if provided, otherwise falls back to the
    ``ECMWF_GRIB_DIR`` env var (separate from the shared ``data_dir``
    because ECMWF data is delivered to its own volume).

    Returns:
        GRIB init Unix timestamp, or None if no ECMWF data found.
    """
    with _grib_time("ecmwf_total"):
        return _enrich_ecmwf_inner(
            cross_sections, all_forecasts, route_points,
            departure_time,
            flight_duration_hours=flight_duration_hours,
            as_of_time=as_of_time,
            ecmwf_data_dir=ecmwf_data_dir,
        )


def _enrich_ecmwf_inner(
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    departure_time: datetime,
    *,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
    ecmwf_data_dir: Path | None = None,
) -> int | None:
    from weatherbrief.fetch.grib.decode import (
        build_ecmwf_cloud_diagnostics,
        build_ecmwf_surface_snapshot,
        decode_ecmwf_pressure_per_point,
        decode_ecmwf_surface_per_point,
    )
    from weatherbrief.fetch.grib.ecmwf_fetch import (
        ecmwf_grib_dir,
        find_best_ecmwf_run,
        scan_ecmwf_files,
    )

    # Only enrich ECMWF model cross-sections
    ecmwf_sections = [cs for cs in cross_sections if cs.model == ModelSource.ECMWF]
    if not ecmwf_sections:
        return None

    grib_dir = ecmwf_data_dir or ecmwf_grib_dir()
    with _grib_time("ecmwf_scan"):
        all_files = scan_ecmwf_files(grib_dir)
    if not all_files:
        logger.info("No ECMWF GRIB data available in %s", grib_dir)
        return None

    # Filter to runs initialized before as_of_time (replay/backtest support)
    if as_of_time is not None:
        all_files = [f for f in all_files if f.base_time <= as_of_time]
        if not all_files:
            logger.info("No ECMWF GRIB runs before as_of_time=%s", as_of_time)
            return None

    # Pick the best run — prefers latest, but falls back to an earlier
    # run with a longer observed horizon when the latest can't cover the
    # full flight window (e.g. a 06/18z short-cutoff run vs an earlier 00/12z).
    flight_end = departure_time + timedelta(hours=max(flight_duration_hours, 1))
    run_files = find_best_ecmwf_run(
        all_files, cover_until=flight_end, data_dir=grib_dir,
    )
    if not run_files:
        logger.info("ECMWF GRIB: no suitable run found")
        return None
    latest_bt = run_files[0].base_time

    # Group files by step_hours, separate a1 (surface) and a2 (pressure)
    files_by_step: dict[int, dict[str, Path]] = {}
    for f in run_files:
        part = "a2" if f.is_pressure_level else "a1" if f.is_surface else None
        if part is not None:
            files_by_step.setdefault(f.step_hours, {})[part] = f.path

    # Compute which forecast steps cover the flight window
    flight_start = departure_time
    flight_end = departure_time + timedelta(hours=max(flight_duration_hours, 1))

    point_lats = [rp.lat for rp in route_points]
    point_lons = [rp.lon for rp in route_points]

    # State for step-difference of accumulated surface fields (tp, sf) across
    # consecutive a1 files. None = no prior step seen yet for that point.
    n_points = len(route_points)
    prev_tp_per_point: list[float | None] = [None] * n_points
    prev_sf_per_point: list[float | None] = [None] * n_points
    prev_a1_valid_utc: datetime | None = None

    enriched_steps = 0
    for step_hours, parts in sorted(files_by_step.items()):
        valid_time = latest_bt + timedelta(hours=step_hours)
        # Only process steps within the flight window (with some margin)
        margin = timedelta(hours=3)
        if valid_time < flight_start - margin or valid_time > flight_end + margin:
            continue

        # Decode pressure levels (a2)
        if "a2" in parts:
            with _grib_time("ecmwf_a2_decode"):
                pl_data, pl_covered = decode_ecmwf_pressure_per_point(
                    parts["a2"], point_lats, point_lons,
                )
            # Only merge covered points — set uncovered to empty
            for i, cov in enumerate(pl_covered):
                if not cov:
                    pl_data[i] = {}

            replaced = _replace_pressure_levels_from_grib(
                ecmwf_sections, all_forecasts, route_points,
                pl_data, valid_utc=valid_time,
            )
            if replaced > 0:
                enriched_steps += 1

        # Decode surface diagnostics (a1)
        if "a1" in parts:
            with _grib_time("ecmwf_a1_decode"):
                sfc_data, sfc_covered = decode_ecmwf_surface_per_point(
                    parts["a1"], point_lats, point_lons,
                )
            diagnostics = [
                build_ecmwf_cloud_diagnostics(raw) if cov else None
                for raw, cov in zip(sfc_data, sfc_covered)
            ]
            _apply_cloud_diagnostics_to_sections(
                ecmwf_sections, all_forecasts, route_points,
                diagnostics, "ecmwf", valid_utc=valid_time,
            )
            # Surface scalars (T/dewpoint, wind/gust, vis, CAPE, MSLP, precip,
            # snow) onto HourlyForecast. Coupled with cloud-diag application —
            # both run together at the same valid_utc so that the forward-fill
            # in fill.py can use ``nwp_cloud_diagnostics is not None`` as the
            # GRIB-anchor detector.
            _apply_ecmwf_surface_to_hourly(
                ecmwf_sections, all_forecasts, route_points,
                sfc_data, sfc_covered,
                valid_utc=valid_time,
                prev_valid_utc=prev_a1_valid_utc,
                prev_tp_per_point=prev_tp_per_point,
                prev_sf_per_point=prev_sf_per_point,
            )
            # Update step-difference state from this step's cumulative values.
            for i, (raw, cov) in enumerate(zip(sfc_data, sfc_covered)):
                if not cov or not raw:
                    continue
                tp = raw.get("total_precip_m")
                if tp is not None:
                    prev_tp_per_point[i] = tp
                sf = raw.get("snowfall_m_we")
                if sf is not None:
                    prev_sf_per_point[i] = sf
            prev_a1_valid_utc = valid_time

    if enriched_steps > 0:
        logger.info(
            "ECMWF GRIB full sounding replacement applied (%d steps, base %s)",
            enriched_steps, latest_bt.isoformat(),
        )
        return int(latest_bt.timestamp())

    logger.info("ECMWF GRIB: no matching steps for flight window")
    return None


# ---------------------------------------------------------------------------
# GFS enrichment
# ---------------------------------------------------------------------------


def _enrich_gfs(
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    departure_time: datetime,
    *,
    data_dir: Path,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
) -> int | None:
    """Enrich GFS cross-sections with CLWMR/ICMR and cloud diagnostics.

    Returns the GRIB init Unix timestamp, or None if enrichment was skipped.
    """
    with _grib_time("gfs_total"):
        return _enrich_gfs_inner(
            cross_sections, all_forecasts, route_points,
            departure_time, data_dir=data_dir,
            flight_duration_hours=flight_duration_hours,
            as_of_time=as_of_time,
        )


def _enrich_gfs_inner(
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    departure_time: datetime,
    *,
    data_dir: Path,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
) -> int | None:
    from weatherbrief.fetch.grib.grib_fetch import compute_flight_window_hours

    gfs_sections = [cs for cs in cross_sections if cs.model == ModelSource.GFS]
    if not gfs_sections:
        logger.info("No GFS cross-sections to enrich")
        return None

    session = _grib_session()

    with _grib_time("gfs_find_run"):
        run_info = find_latest_run(departure_time, session=session, as_of_time=as_of_time)
    if run_info is None:
        logger.warning("No GFS model run found for enrichment")
        return None

    init_date, init_hour = run_info
    forecast_hours = compute_flight_window_hours(
        init_date, init_hour, departure_time, flight_duration_hours,
    )

    purge_old_runs(data_dir, model="gfs")
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="gfs")

    point_lats = [rp.lat for rp in route_points]
    point_lons = [rp.lon for rp in route_points]

    # Fetch .idx text (shared by both enrichment paths)
    with _grib_time("gfs_idx_fetch"):
        idx_by_fhour: dict[int, str] = {}
        for fhour in forecast_hours:
            try:
                idx_by_fhour[fhour] = fetch_idx(init_date, init_hour, fhour, session=session)
            except Exception:
                logger.warning("Failed to fetch .idx for f%03d", fhour, exc_info=True)

    if not idx_by_fhour:
        logger.warning("No .idx files retrieved for enrichment")
        return None

    # Run both enrichment paths in parallel — they write to different fields
    # (CLWMR/ICMR on PressureLevelData vs nwp_cloud_diagnostics on HourlyForecast).
    # Use _submit_with_context so per-fhour _grib_time(...) calls in the inner
    # workers see the same _GRIB_TIMER as the outer enrich_forecasts call.
    with _grib_time("gfs_workers"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            _submit_with_context(
                pool, _enrich_clwmr_icmr,
                gfs_sections, all_forecasts, route_points,
                init_date, init_hour, forecast_hours,
                run_dir, idx_by_fhour, point_lats, point_lons, session,
            )
            _submit_with_context(
                pool, _enrich_cloud_diagnostics,
                gfs_sections, all_forecasts, route_points,
                init_date, init_hour, forecast_hours,
                run_dir, idx_by_fhour, point_lats, point_lons, session,
            )
            # ThreadPoolExecutor.__exit__ waits for all futures to complete

    return _run_info_to_timestamp(init_date, init_hour)


def _fetch_clwmr_icmr_for_fhour(
    init_date: str,
    init_hour: int,
    fhour: int,
    target_levels: list[int],
    run_dir: Path,
    idx_by_fhour: dict[int, str],
    point_lats: list[float],
    point_lons: list[float],
    session: requests.Session,
) -> list[dict[int, dict[str, float]]] | None:
    """Fetch, cache, decode CLWMR/ICMR for a single GFS forecast hour."""
    from weatherbrief.fetch.grib.decode import decode_grib_per_point

    ck = cache_key(fhour, "CLWMR_ICMR")
    grib_bytes = get_cached(run_dir, ck)
    if grib_bytes is None:
        idx_text = idx_by_fhour.get(fhour)
        if idx_text is None:
            return None
        try:
            ranges = plan_byte_ranges(idx_text, target_levels=target_levels)
            if not ranges:
                logger.warning("No CLWMR/ICMR found in .idx for f%03d", fhour)
                return None
            with _grib_time("gfs_clwmr_download"):
                grib_bytes = fetch_byte_ranges(
                    init_date, init_hour, fhour, ranges, session=session,
                )
            if not grib_bytes:
                return None
            put_cached(run_dir, ck, grib_bytes)
            logger.info(
                "Downloaded GRIB2 f%03d: %d ranges, %.1f KB",
                fhour, len(ranges), len(grib_bytes) / 1024,
            )
        except Exception:
            logger.warning("Failed to fetch GRIB2 f%03d", fhour, exc_info=True)
            return None

    with _grib_time("gfs_clwmr_decode"):
        return decode_grib_per_point(grib_bytes, point_lats, point_lons)


def _enrich_clwmr_icmr(
    gfs_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    run_dir: Path,
    idx_by_fhour: dict[int, str],
    point_lats: list[float],
    point_lons: list[float],
    session: requests.Session,
) -> None:
    """Enrich pressure-level data with CLWMR/ICMR from GFS GRIB2."""
    # Extract target pressure levels from existing forecasts
    target_levels: list[int] = []
    for cs in gfs_sections:
        for pf in cs.point_forecasts:
            for h in pf.hourly:
                for pl in h.pressure_levels:
                    if pl.pressure_hpa not in target_levels:
                        target_levels.append(pl.pressure_hpa)
                break
            break
    target_levels.sort(reverse=True)

    total_enriched = 0
    for fhour in forecast_hours:
        decoded_points = _fetch_clwmr_icmr_for_fhour(
            init_date, init_hour, fhour, target_levels,
            run_dir, idx_by_fhour, point_lats, point_lons, session,
        )
        if not decoded_points:
            continue

        valid_utc = _forecast_hour_to_utc(init_date, init_hour, fhour)
        total_enriched += _merge_cloud_water_into_sections(
            gfs_sections, all_forecasts, route_points, decoded_points, "gfs",
            valid_utc=valid_utc,
        )
        del decoded_points
        _grib_gc()

    if total_enriched:
        logger.info(
            "GRIB2 GFS enrichment: %d pressure levels enriched with cloud water",
            total_enriched,
        )
    else:
        logger.warning("No GRIB2 CLWMR/ICMR data retrieved for enrichment")


def _fetch_cloud_diag_for_fhour(
    init_date: str,
    init_hour: int,
    fhour: int,
    run_dir: Path,
    idx_by_fhour: dict[int, str],
    point_lats: list[float],
    point_lons: list[float],
    session: requests.Session,
) -> list[dict[str, float]] | None:
    """Fetch, cache, decode cloud diagnostics for a single GFS forecast hour."""
    from weatherbrief.fetch.grib.decode import decode_cloud_diag_per_point

    ck = cache_key(fhour, "CLOUD_DIAG")
    grib_bytes = get_cached(run_dir, ck)
    if grib_bytes is None:
        idx_text = idx_by_fhour.get(fhour)
        if idx_text is None:
            return None
        try:
            ranges = plan_cloud_diag_byte_ranges(idx_text)
            if not ranges:
                logger.warning("No cloud diag found in .idx for f%03d", fhour)
                return None
            with _grib_time("gfs_cloud_diag_download"):
                grib_bytes = fetch_cloud_diag_ranges(
                    init_date, init_hour, fhour, ranges, session=session,
                )
            if not grib_bytes:
                return None
            put_cached(run_dir, ck, grib_bytes)
            logger.info(
                "Downloaded cloud diag f%03d: %d ranges, %.1f KB",
                fhour, len(ranges), len(grib_bytes) / 1024,
            )
        except Exception:
            logger.warning("Failed to fetch cloud diag f%03d", fhour, exc_info=True)
            return None

    with _grib_time("gfs_cloud_diag_decode"):
        return decode_cloud_diag_per_point(grib_bytes, point_lats, point_lons)


def _apply_cloud_diagnostics_to_sections(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    diagnostics_per_point: list[NWPCloudDiagnostics | None],
    model_value: str,
    valid_utc: datetime | None = None,
) -> int:
    """Merge cloud diagnostics into cross-section and waypoint forecasts.

    Args:
        valid_utc: If set, only enrich hourly entries matching this UTC hour.

    Returns:
        Number of hourly entries enriched.
    """
    enriched_count = 0
    for cs in sections:
        for point_idx, wf in enumerate(cs.point_forecasts):
            if point_idx >= len(diagnostics_per_point):
                break
            diag = diagnostics_per_point[point_idx]
            if diag is None:
                continue
            for hourly in wf.hourly:
                if not _matches_valid_time(hourly.time, valid_utc):
                    continue
                _apply_cloud_diagnostics(hourly, diag)
                enriched_count += 1

    # Also enrich waypoint-only forecasts
    wp_diag_lookup: dict[str, NWPCloudDiagnostics] = {}
    for rp, diag in zip(route_points, diagnostics_per_point):
        if rp.waypoint_icao and diag is not None:
            wp_diag_lookup[rp.waypoint_icao] = diag

    for wf in all_forecasts:
        if wf.model.value != model_value:
            continue
        diag = wp_diag_lookup.get(wf.waypoint.icao)
        if diag is None:
            continue
        for hourly in wf.hourly:
            if not _matches_valid_time(hourly.time, valid_utc):
                continue
            _apply_cloud_diagnostics(hourly, diag)

    return enriched_count


# Instantaneous surface fields written from GRIB at the matching valid_utc.
# Forward-fill in ``fill.py`` propagates these into intermediate hours.
_ECMWF_HOURLY_INSTANT_FIELDS: tuple[str, ...] = (
    "temperature_2m_c",
    "dewpoint_2m_c",
    "wind_speed_10m_kt",
    "wind_direction_10m_deg",
    "wind_gusts_10m_kt",
    "visibility_m",
    "cape_jkg",
    "surface_pressure_hpa",
)

# Window-average surface fields. ECMWF a1 delivers these as cumulative-since-init,
# so we step-difference against the prior a1 file and distribute the per-hour
# rate across every hour in the window — no forward-fill needed.
_ECMWF_HOURLY_RATE_FIELDS: tuple[str, ...] = (
    "precipitation_mm",
    "snowfall_cm",
)


def _copy_fields(hourly: HourlyForecast, snap: dict, fields: tuple[str, ...]) -> None:
    for f in fields:
        v = snap.get(f)
        if v is not None:
            setattr(hourly, f, v)


def _apply_ecmwf_surface_to_hourly(
    ecmwf_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    sfc_data: list[dict[str, float]],
    sfc_covered: list[bool],
    *,
    valid_utc: datetime,
    prev_valid_utc: datetime | None,
    prev_tp_per_point: list[float | None],
    prev_sf_per_point: list[float | None],
) -> None:
    """Write decoded ECMWF a1 surface fields onto matching ``HourlyForecast``s.

    Reuses :func:`build_ecmwf_surface_snapshot` for the unit-conversion logic so
    the standalone-verification path and the briefing path stay in sync.

    Instantaneous fields (T/dewpoint, wind/gust, vis, CAPE, MSLP) are written
    only at the hour matching ``valid_utc``. Linear interpolation closes the
    gap to intermediate hours later in :mod:`fetch.grib.fill`, anchored by
    the hours where ``nwp_cloud_diagnostics`` was set in the same loop
    iteration as the surface write — so the two writes must stay coupled.

    Accumulated fields (``tp``, ``sf``) are step-differenced against the prior
    a1 step's cumulative values and distributed evenly as a per-hour rate
    across every hour in ``(prev_valid_utc, valid_utc]``. When no prior step
    is available (first a1 in the processed window), precip/snow are left
    untouched — Open-Meteo's value remains.

    Mutates ``HourlyForecast`` instances in place. Uncovered points (route
    extends outside the ECMWF grid) are skipped, leaving Open-Meteo data.
    """
    from weatherbrief.fetch.grib.decode import build_ecmwf_surface_snapshot

    n = len(sfc_data)
    if n == 0:
        return

    window_hours: float | None = None
    if prev_valid_utc is not None:
        delta_h = (valid_utc - prev_valid_utc).total_seconds() / 3600.0
        if delta_h > 0:
            window_hours = delta_h

    # Build per-point snapshots (instantaneous + per-hour rate). Empty dicts
    # for uncovered/missing points so the indexing stays aligned with route_points.
    inst_snaps: list[dict] = []
    rate_snaps: list[dict] = []
    for i in range(n):
        raw = sfc_data[i]
        if i >= len(sfc_covered) or not sfc_covered[i] or not raw:
            inst_snaps.append({})
            rate_snaps.append({})
            continue

        # Instantaneous: zero out the cumulative fields so the snapshot
        # builder doesn't emit precip/snow values for the per-hour write.
        inst_raw = dict(raw)
        inst_raw["total_precip_m"] = None
        inst_raw["snowfall_m_we"] = None
        inst_snaps.append(build_ecmwf_surface_snapshot(inst_raw))

        rate_raw: dict[str, float] = {}
        if window_hours is not None:
            tp = raw.get("total_precip_m")
            ptp = prev_tp_per_point[i] if i < len(prev_tp_per_point) else None
            if tp is not None and ptp is not None:
                rate_raw["total_precip_m"] = max(0.0, (tp - ptp) / window_hours)
            sf = raw.get("snowfall_m_we")
            psf = prev_sf_per_point[i] if i < len(prev_sf_per_point) else None
            if sf is not None and psf is not None:
                rate_raw["snowfall_m_we"] = max(0.0, (sf - psf) / window_hours)
        rate_snaps.append(build_ecmwf_surface_snapshot(rate_raw) if rate_raw else {})

    def _aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def _write(hourly_list: list[HourlyForecast], inst: dict, rate: dict) -> None:
        for h in hourly_list:
            if inst and _matches_valid_time(h.time, valid_utc):
                _copy_fields(h, inst, _ECMWF_HOURLY_INSTANT_FIELDS)
            if rate and prev_valid_utc is not None:
                ht = _aware(h.time)
                if prev_valid_utc < ht <= valid_utc:
                    _copy_fields(h, rate, _ECMWF_HOURLY_RATE_FIELDS)

    # Cross-section route points
    for cs in ecmwf_sections:
        for point_idx, wf in enumerate(cs.point_forecasts):
            if point_idx >= n:
                break
            inst = inst_snaps[point_idx]
            rate = rate_snaps[point_idx]
            if not inst and not rate:
                continue
            _write(wf.hourly, inst, rate)

    # Waypoint-only forecasts (used by per-airport sounding analysis)
    wp_idx_lookup: dict[str, int] = {}
    for rp_idx, rp in enumerate(route_points):
        if rp.waypoint_icao and rp_idx < n:
            wp_idx_lookup[rp.waypoint_icao] = rp_idx

    for wf in all_forecasts:
        if wf.model.value != "ecmwf":
            continue
        idx = wp_idx_lookup.get(wf.waypoint.icao)
        if idx is None:
            continue
        inst = inst_snaps[idx]
        rate = rate_snaps[idx]
        if not inst and not rate:
            continue
        _write(wf.hourly, inst, rate)


def _replace_pressure_levels_from_grib(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    decoded_points: list[dict[int, dict[str, float]]],
    valid_utc: datetime | None = None,
    model_source: ModelSource = ModelSource.ECMWF,
) -> int:
    """Replace pressure_levels on hourly forecasts with full GRIB sounding.

    Unlike _merge_cloud_water_into_sections which patches individual fields
    onto existing levels, this builds complete PressureLevelData objects from
    the GRIB data and replaces the entire pressure_levels list.

    Works for both ECMWF and ICON-EU GRIB data — the decoded dict format
    is model-agnostic after vertical interpolation to pressure levels.

    Returns:
        Number of hourly entries whose pressure levels were replaced.
    """
    from weatherbrief.fetch.grib.decode import build_pressure_levels_from_grib

    replaced_count = 0
    for cs in sections:
        for point_idx, wf in enumerate(cs.point_forecasts):
            if point_idx >= len(decoded_points):
                break
            point_data = decoded_points[point_idx]
            if not point_data:
                continue

            for hourly in wf.hourly:
                if not _matches_valid_time(hourly.time, valid_utc):
                    continue
                new_levels = build_pressure_levels_from_grib(point_data)
                if new_levels:
                    hourly.pressure_levels = new_levels
                    replaced_count += 1

    # Also replace for waypoint-only forecasts
    wp_data_lookup: dict[str, dict[int, dict[str, float]]] = {}
    for rp, pd in zip(route_points, decoded_points):
        if rp.waypoint_icao and pd:
            wp_data_lookup[rp.waypoint_icao] = pd

    for wf in all_forecasts:
        if wf.model != model_source:
            continue
        point_data = wp_data_lookup.get(wf.waypoint.icao)
        if not point_data:
            continue
        for hourly in wf.hourly:
            if not _matches_valid_time(hourly.time, valid_utc):
                continue
            new_levels = build_pressure_levels_from_grib(point_data)
            if new_levels:
                hourly.pressure_levels = new_levels
                replaced_count += 1

    return replaced_count





def _enrich_cloud_diagnostics(
    gfs_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    run_dir: Path,
    idx_by_fhour: dict[int, str],
    point_lats: list[float],
    point_lons: list[float],
    session: requests.Session,
) -> None:
    """Enrich forecasts with GFS cloud layer diagnostics."""
    from weatherbrief.fetch.grib.decode import build_cloud_diagnostics

    total_enriched = 0
    for fhour in forecast_hours:
        decoded_points = _fetch_cloud_diag_for_fhour(
            init_date, init_hour, fhour,
            run_dir, idx_by_fhour, point_lats, point_lons, session,
        )
        if not decoded_points:
            continue

        diagnostics_per_point = [build_cloud_diagnostics(raw) for raw in decoded_points]
        valid_utc = _forecast_hour_to_utc(init_date, init_hour, fhour)
        total_enriched += _apply_cloud_diagnostics_to_sections(
            gfs_sections, all_forecasts, route_points,
            diagnostics_per_point, "gfs", valid_utc=valid_utc,
        )
        del decoded_points
        del diagnostics_per_point
        _grib_gc()

    if total_enriched:
        logger.info("GRIB2 enrichment: %d hourly entries enriched with cloud diagnostics", total_enriched)
    else:
        logger.warning("No cloud diagnostic GRIB2 data retrieved")


# ---------------------------------------------------------------------------
# ICON-EU enrichment
# ---------------------------------------------------------------------------


class _IconEuContext:
    """Holds resolved ICON-EU run info for split download/decode phases."""

    __slots__ = (
        "init_date", "init_hour", "forecast_hours", "run_dir",
        "levels", "point_lats", "point_lons", "session",
    )

    def __init__(
        self, init_date: str, init_hour: int, forecast_hours: list[int],
        run_dir: Path, levels: list[int],
        point_lats: list[float], point_lons: list[float],
        session: requests.Session,
    ):
        self.init_date = init_date
        self.init_hour = init_hour
        self.forecast_hours = forecast_hours
        self.run_dir = run_dir
        self.levels = levels
        self.point_lats = point_lats
        self.point_lons = point_lons
        self.session = session


def _prepare_icon_eu(
    cross_sections: list[RouteCrossSection],
    route_points: list[RoutePoint],
    departure_time: datetime,
    *,
    data_dir: Path,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
) -> _IconEuContext | None:
    """Resolve ICON-EU run info and check eligibility. Returns None to skip."""
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU_MODEL_LEVEL_MAX,
        ICON_EU_MODEL_LEVEL_MIN,
        compute_icon_eu_flight_window_hours,
        find_latest_icon_eu_run,
        route_in_icon_eu_domain,
    )

    icon_sections = [cs for cs in cross_sections if cs.model == ModelSource.ICON]
    if not icon_sections:
        logger.debug("No ICON cross-sections to enrich")
        return None

    if not route_in_icon_eu_domain(route_points):
        logger.info("Route outside ICON-EU domain, skipping ICON-EU enrichment")
        return None

    session = _grib_session()

    cover_until = departure_time + timedelta(hours=flight_duration_hours)
    try:
        run_info = find_latest_icon_eu_run(
            departure_time, session=session, as_of_time=as_of_time,
            cover_until=cover_until,
        )
    except Exception:
        logger.warning("Failed to find ICON-EU model run", exc_info=True)
        return None

    if run_info is None:
        logger.info("No ICON-EU run found that covers the flight window")
        return None

    init_date, init_hour = run_info

    forecast_hours = compute_icon_eu_flight_window_hours(
        init_date, init_hour, departure_time, flight_duration_hours,
    )

    purge_old_runs(data_dir, model="icon-eu")
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="icon-eu")
    levels = list(range(ICON_EU_MODEL_LEVEL_MIN, ICON_EU_MODEL_LEVEL_MAX + 1))

    return _IconEuContext(
        init_date=init_date, init_hour=init_hour,
        forecast_hours=forecast_hours, run_dir=run_dir, levels=levels,
        point_lats=[rp.lat for rp in route_points],
        point_lons=[rp.lon for rp in route_points],
        session=session,
    )


def _prefetch_icon_eu_data(ctx: _IconEuContext) -> None:
    """Download ICON-EU GRIB2 data and cache to disk (no decode).

    Runs in a background thread while GFS enrichment proceeds.
    """
    with _grib_time("icon_prefetch"):
        _prefetch_icon_eu_data_inner(ctx)


def _prefetch_icon_eu_data_inner(ctx: _IconEuContext) -> None:
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU_VARIABLES,
        fetch_icon_eu_per_variable,
        fetch_icon_eu_single_level,
    )

    for fhour in ctx.forecast_hours:
        # Model-level data (P, QC, QI) — per variable
        legacy_ck = cache_key(fhour, "ICON_EU_QC_QI_P")
        if is_cached(ctx.run_dir, legacy_ck):
            continue  # legacy cache hit, skip per-var download

        for var in ICON_EU_VARIABLES:
            ck = cache_key(fhour, f"ICON_EU_{var.upper()}")
            if is_cached(ctx.run_dir, ck):
                continue
            try:
                with _grib_time("icon_prefetch_var"):
                    per_var = fetch_icon_eu_per_variable(
                        ctx.init_date, ctx.init_hour, fhour,
                        levels=ctx.levels,
                        variables=[var],
                        session=ctx.session,
                    )
                data = per_var.get(var)
                if data:
                    put_cached(ctx.run_dir, ck, data)
            except Exception:
                logger.warning("Prefetch ICON-EU f%03d %s failed", fhour, var, exc_info=True)

        # Single-level cloud diagnostics
        diag_ck = cache_key(fhour, "ICON_EU_CLOUD_DIAG")
        if not is_cached(ctx.run_dir, diag_ck):
            try:
                with _grib_time("icon_prefetch_cloud_diag"):
                    fetched = fetch_icon_eu_single_level(
                        ctx.init_date, ctx.init_hour, [fhour], session=ctx.session,
                    )
                grib_bytes = fetched.get(fhour)
                if grib_bytes:
                    put_cached(ctx.run_dir, diag_ck, grib_bytes)
            except Exception:
                logger.warning("Prefetch ICON-EU cloud diag f%03d failed", fhour, exc_info=True)


def _decode_and_merge_icon_eu(
    ctx: _IconEuContext,
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
) -> tuple[int | None, str | None]:
    """Decode cached ICON-EU data and merge into cross-sections.

    Called after prefetch has cached all data to disk.
    """
    from weatherbrief.fetch.grib.decode import decode_icon_eu_per_point, decode_icon_eu_per_point_chunked
    from weatherbrief.fetch.grib.icon_eu_fetch import ICON_EU_VARIABLES

    icon_sections = [cs for cs in cross_sections if cs.model == ModelSource.ICON]

    # Collect CLC-derived cloud layers across forecast hours.
    # Use the last non-empty result per point (layers are time-invariant for
    # a given ICON run, so any forecast hour's CLC works).
    n_points = len(ctx.point_lats)
    clc_layers_per_point: list[dict[str, float]] = [{} for _ in range(n_points)]

    def _decode_fhour(
        fhour: int,
    ) -> tuple[list[dict[int, dict[str, float]]] | None, list[dict[str, float]]]:
        empty_clc = [{} for _ in range(n_points)]
        # Check for legacy combined cache first
        legacy_ck = cache_key(fhour, "ICON_EU_QC_QI_P")
        legacy_bytes = get_cached(ctx.run_dir, legacy_ck)
        if legacy_bytes is not None:
            with _grib_time("icon_legacy_decode"):
                decoded = decode_icon_eu_per_point(legacy_bytes, ctx.point_lats, ctx.point_lons)
            del legacy_bytes
            _grib_gc()
            return decoded, empty_clc

        # Load per-variable data from cache (already downloaded by prefetch)
        var_bytes: dict[str, bytes] = {}
        for var in ICON_EU_VARIABLES:
            ck = cache_key(fhour, f"ICON_EU_{var.upper()}")
            cached = get_cached(ctx.run_dir, ck)
            if cached is not None:
                var_bytes[var] = cached

        if not var_bytes:
            return None, empty_clc

        with _grib_time("icon_chunked_decode"):
            decoded, clc_layers = decode_icon_eu_per_point_chunked(
                var_bytes, ctx.point_lats, ctx.point_lons,
            )
        del var_bytes
        _grib_gc()
        return decoded, clc_layers

    total_enriched = 0
    for fhour in ctx.forecast_hours:
        _grib_rss_mark("icon_fhour_pre")
        decoded_points, clc_layers = _decode_fhour(fhour)
        _grib_rss_mark("icon_fhour_decoded")
        if not decoded_points:
            continue

        # Keep CLC-derived layers (first non-empty wins per point)
        for i, layers in enumerate(clc_layers):
            if layers and not clc_layers_per_point[i]:
                clc_layers_per_point[i] = layers

        valid_utc = _forecast_hour_to_utc(ctx.init_date, ctx.init_hour, fhour)
        replaced = _replace_pressure_levels_from_grib(
            icon_sections, all_forecasts, route_points, decoded_points,
            valid_utc=valid_utc, model_source=ModelSource.ICON,
        )
        total_enriched += replaced
        del decoded_points
        _grib_gc()
        _grib_rss_mark("icon_fhour_post_gc")

    if not total_enriched:
        logger.warning("No ICON-EU GRIB2 data retrieved for enrichment")
        return None, None

    logger.info(
        "GRIB2 ICON full sounding replacement: %d hourly entries replaced",
        total_enriched,
    )

    # Cloud diagnostics (ceiling, convective base/top) from single-level files.
    # Pass CLC-derived layer boundaries to fill missing NWP base/top.
    _enrich_icon_eu_cloud_diagnostics(
        icon_sections, all_forecasts, route_points,
        ctx.init_date, ctx.init_hour, ctx.forecast_hours,
        ctx.run_dir, ctx.point_lats, ctx.point_lons, ctx.session,
        clc_layers_per_point=clc_layers_per_point,
    )

    return _run_info_to_timestamp(ctx.init_date, ctx.init_hour), None


def _enrich_icon_eu_cloud_diagnostics(
    icon_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    run_dir: Path,
    point_lats: list[float],
    point_lons: list[float],
    session: requests.Session,
    *,
    clc_layers_per_point: list[dict[str, float]] | None = None,
) -> None:
    """Enrich ICON forecasts with single-level cloud diagnostics (ceiling, etc.).

    If *clc_layers_per_point* is provided (CLC-derived cloud layer boundaries
    from model-level data), missing ``base_ft``/``top_ft`` on low/mid/high
    NWPCloudLayerDiag are filled from it.
    """
    from weatherbrief.fetch.grib.decode import (
        build_icon_cloud_diagnostics,
        decode_icon_eu_cloud_diag_per_point,
    )
    from weatherbrief.fetch.grib.icon_eu_fetch import fetch_icon_eu_single_level

    total_enriched = 0
    for fhour in forecast_hours:
        ck = cache_key(fhour, "ICON_EU_CLOUD_DIAG")
        grib_bytes = get_cached(run_dir, ck)
        if grib_bytes is None:
            try:
                fetched = fetch_icon_eu_single_level(
                    init_date, init_hour, [fhour], session=session,
                )
                grib_bytes = fetched.get(fhour)
                if grib_bytes:
                    put_cached(run_dir, ck, grib_bytes)
            except Exception:
                logger.warning("Failed to fetch ICON-EU cloud diagnostics f%03d", fhour, exc_info=True)
                continue
        if not grib_bytes:
            continue

        with _grib_time("icon_cloud_diag_decode"):
            decoded_points = decode_icon_eu_cloud_diag_per_point(
                grib_bytes, point_lats, point_lons,
            )
        del grib_bytes
        if not decoded_points:
            continue

        diagnostics_per_point = [build_icon_cloud_diagnostics(raw) for raw in decoded_points]

        # Fill missing layer base/top from CLC-derived boundaries
        if clc_layers_per_point:
            for pt_idx, diag in enumerate(diagnostics_per_point):
                if diag is None or pt_idx >= len(clc_layers_per_point):
                    continue
                clc = clc_layers_per_point[pt_idx]
                if not clc:
                    continue
                for band in ("low", "mid", "high"):
                    layer = getattr(diag, band)
                    if layer.base_ft is None and f"{band}_base_ft" in clc:
                        layer.base_ft = clc[f"{band}_base_ft"]
                    if layer.top_ft is None and f"{band}_top_ft" in clc:
                        layer.top_ft = clc[f"{band}_top_ft"]

        valid_utc = _forecast_hour_to_utc(init_date, init_hour, fhour)

        # Use _apply_cloud_diagnostics_to_sections with GFS-priority guard
        for cs in icon_sections:
            for point_idx, wf in enumerate(cs.point_forecasts):
                if point_idx >= len(diagnostics_per_point):
                    break
                diag = diagnostics_per_point[point_idx]
                if diag is None:
                    continue
                for hourly in wf.hourly:
                    if not _matches_valid_time(hourly.time, valid_utc):
                        continue
                    if hourly.nwp_cloud_diagnostics is None:
                        _apply_cloud_diagnostics(hourly, diag)
                        total_enriched += 1

        # Also enrich waypoint-only forecasts
        wp_diag_lookup: dict[str, NWPCloudDiagnostics] = {}
        for rp, diag in zip(route_points, diagnostics_per_point):
            if rp.waypoint_icao and diag is not None:
                wp_diag_lookup[rp.waypoint_icao] = diag

        for wf in all_forecasts:
            if wf.model.value != "icon":
                continue
            diag = wp_diag_lookup.get(wf.waypoint.icao)
            if diag is None:
                continue
            for hourly in wf.hourly:
                if not _matches_valid_time(hourly.time, valid_utc):
                    continue
                if hourly.nwp_cloud_diagnostics is None:
                    _apply_cloud_diagnostics(hourly, diag)

        del decoded_points
        del diagnostics_per_point
        del wp_diag_lookup
        _grib_gc()

    if total_enriched:
        logger.info(
            "ICON-EU enrichment: %d hourly entries enriched with cloud diagnostics",
            total_enriched,
        )
    else:
        logger.debug("No ICON-EU cloud diagnostic GRIB2 data retrieved")


# ---------------------------------------------------------------------------
# Shared merge logic
# ---------------------------------------------------------------------------


def _merge_cloud_water_into_sections(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    decoded_points: list[dict[int, dict[str, float]]],
    model_value: str,
    valid_utc: datetime | None = None,
) -> int:
    """Merge decoded cloud water data into cross-section and waypoint forecasts.

    Used by GFS enrichment (cloud-only, no full sounding replacement).

    Args:
        valid_utc: If set, only enrich hourly entries whose time matches
            this UTC hour. None enriches all hours (backward-compatible).

    Returns:
        Number of pressure levels enriched.
    """
    enriched_count = 0
    for cs in sections:
        for point_idx, wf in enumerate(cs.point_forecasts):
            if point_idx >= len(decoded_points):
                break
            point_data = decoded_points[point_idx]
            if not point_data:
                continue

            for hourly in wf.hourly:
                if not _matches_valid_time(hourly.time, valid_utc):
                    continue
                for pl in hourly.pressure_levels:
                    level_data = point_data.get(pl.pressure_hpa)
                    if level_data is None:
                        continue

                    clwmr = level_data.get("cloud_liquid_water_kg_kg")
                    if clwmr is not None:
                        pl.cloud_liquid_water_kg_kg = clwmr
                        enriched_count += 1

                    icmr = level_data.get("ice_mixing_ratio_kg_kg")
                    if icmr is not None:
                        pl.ice_mixing_ratio_kg_kg = icmr

                    clc = level_data.get("cloud_area_fraction_pct")
                    if clc is not None:
                        pl.cloud_area_fraction_pct = clc

    # Also enrich waypoint-only forecasts
    wp_data_lookup: dict[str, dict[int, dict[str, float]]] = {}
    for rp, pd in zip(route_points, decoded_points):
        if rp.waypoint_icao and pd:
            wp_data_lookup[rp.waypoint_icao] = pd

    for wf in all_forecasts:
        if wf.model.value != model_value:
            continue
        wp_icao = wf.waypoint.icao
        point_data = wp_data_lookup.get(wp_icao)
        if not point_data:
            continue
        for hourly in wf.hourly:
            if not _matches_valid_time(hourly.time, valid_utc):
                continue
            for pl in hourly.pressure_levels:
                level_data = point_data.get(pl.pressure_hpa)
                if level_data is None:
                    continue
                clwmr = level_data.get("cloud_liquid_water_kg_kg")
                if clwmr is not None:
                    pl.cloud_liquid_water_kg_kg = clwmr
                icmr = level_data.get("ice_mixing_ratio_kg_kg")
                if icmr is not None:
                    pl.ice_mixing_ratio_kg_kg = icmr
                clc = level_data.get("cloud_area_fraction_pct")
                if clc is not None:
                    pl.cloud_area_fraction_pct = clc

    return enriched_count
