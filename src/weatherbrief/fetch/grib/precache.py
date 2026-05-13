"""Pre-cache ICON-EU / GFS GRIB byte ranges for airport-profile selectables.

Reuses the existing per-flight fetch primitives — same disk cache layout,
same dedup, same cleanup. The only new logic is the iteration over
(forecast hour × variable) combos that cover the ``/maps.html`` D-0..D-3
control range. The byte-range cache is shared with the flight-driven path,
so a warmed run also speeds up briefings whose flight window overlaps.

See issue #126 for the rationale + expected per-pass cost (~32 GB ICON-EU,
~6 GB GFS over the 64 unique forecast hours).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from weatherbrief.fetch.grib.cache import (
    cache_dir_for_run,
    cache_key,
    is_cached,
    put_cached,
)
from weatherbrief.fetch.grib.icon_eu_fetch import (
    ICON_EU_VARIABLES as ICON_EU_PRECACHE_VARS,
)

logger = logging.getLogger(__name__)


# /maps.html Forecast controls offer hour-options 06/09/12/15/18 Z, each
# spawning a 4-hour window. The union deduplicates to hours 06–21 Z per day.
PRECACHE_FORECAST_HOURS_PER_DAY = (
    6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21,
)

# Forecast controls cover D-0 through D-3.
PRECACHE_DAYS_AHEAD = 4

# Both ICON-EU main cycles and GFS main cycles publish to 120 h; cap there.
PRECACHE_MAX_FORECAST_HOUR = 120

# Only main cycles can cover D-3 (short cycles top out at 30 h horizon).
MAIN_CYCLE_HOURS = (0, 6, 12, 18)


def airport_profile_forecast_hours(init: datetime) -> list[int]:
    """Forecast-hour offsets (from ``init``) covering D-0..D-3 selectables.

    Returns sorted unique offsets within the 120 h main-cycle horizon. Hours
    in the past relative to the run init are skipped (a 12 Z run can't supply
    a 06 Z target on the same day).
    """
    hours: set[int] = set()
    for day_offset in range(PRECACHE_DAYS_AHEAD):
        for utc_hour in PRECACHE_FORECAST_HOURS_PER_DAY:
            target = (init + timedelta(days=day_offset)).replace(
                hour=utc_hour, minute=0, second=0, microsecond=0,
            )
            if target < init:
                continue
            offset = int((target - init).total_seconds() // 3600)
            if offset > PRECACHE_MAX_FORECAST_HOUR:
                continue
            hours.add(offset)
    return sorted(hours)


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "data"))


def precache_icon_eu_run(init: datetime) -> dict[str, int]:
    """Fetch every (var × forecast_hour) for D-0..D-3 from the given run.

    Idempotent: skips combos already on disk. Bytes land in the shared
    byte-range cache (``{data_dir}/.cache/grib/icon-eu/{run}/...``) so a
    flight briefing landing in the same window will hit the warm cache.
    """
    from weatherbrief.fetch.grib import _grib_session
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU_MODEL_LEVEL_MAX,
        ICON_EU_MODEL_LEVEL_MIN,
        fetch_icon_eu_per_variable,
        fetch_icon_eu_single_level,
    )

    init_date = init.strftime("%Y%m%d")
    init_hour = init.hour
    forecast_hours = airport_profile_forecast_hours(init)
    levels = list(range(ICON_EU_MODEL_LEVEL_MIN, ICON_EU_MODEL_LEVEL_MAX + 1))

    data_dir = _data_dir()
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="icon-eu")
    session = _grib_session()

    hours_fetched = 0
    vars_fetched = 0
    bytes_downloaded = 0

    for fhour in forecast_hours:
        # Legacy combined cache (briefings on older code paths) → counts as covered
        if is_cached(run_dir, cache_key(fhour, "ICON_EU_QC_QI_P")):
            hours_fetched += 1
            continue

        for var in ICON_EU_PRECACHE_VARS:
            ck = cache_key(fhour, f"ICON_EU_{var.upper()}")
            if is_cached(run_dir, ck):
                continue
            try:
                per_var = fetch_icon_eu_per_variable(
                    init_date, init_hour, fhour,
                    levels=levels, variables=[var], session=session,
                )
                data = per_var.get(var)
                if data:
                    put_cached(run_dir, ck, data)
                    vars_fetched += 1
                    bytes_downloaded += len(data)
            except Exception:
                logger.warning(
                    "Pre-cache ICON-EU f%03d %s failed", fhour, var, exc_info=True,
                )

        diag_ck = cache_key(fhour, "ICON_EU_CLOUD_DIAG")
        if not is_cached(run_dir, diag_ck):
            try:
                fetched = fetch_icon_eu_single_level(
                    init_date, init_hour, [fhour], session=session,
                )
                grib_bytes = fetched.get(fhour)
                if grib_bytes:
                    put_cached(run_dir, diag_ck, grib_bytes)
                    vars_fetched += 1
                    bytes_downloaded += len(grib_bytes)
            except Exception:
                logger.warning(
                    "Pre-cache ICON-EU cloud diag f%03d failed",
                    fhour, exc_info=True,
                )

        hours_fetched += 1

    return {
        "hours_fetched": hours_fetched,
        "vars_fetched": vars_fetched,
        "bytes_downloaded": bytes_downloaded,
        "forecast_hours_total": len(forecast_hours),
    }


def precache_gfs_run(init: datetime) -> dict[str, int]:
    """Pre-cache GFS CLWMR/ICMR + cloud diagnostics for D-0..D-3.

    Fetches the full pressure-level superset (``target_levels=None``) so any
    briefing's level subset will hit the cache.
    """
    from weatherbrief.fetch.grib import _grib_session
    from weatherbrief.fetch.grib.gfs_idx import (
        plan_byte_ranges,
        plan_cloud_diag_byte_ranges,
    )
    from weatherbrief.fetch.grib.grib_fetch import (
        fetch_byte_ranges,
        fetch_cloud_diag_ranges,
        fetch_idx,
    )

    init_date = init.strftime("%Y%m%d")
    init_hour = init.hour
    forecast_hours = airport_profile_forecast_hours(init)

    data_dir = _data_dir()
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="gfs")
    session = _grib_session()

    hours_fetched = 0
    vars_fetched = 0
    bytes_downloaded = 0

    for fhour in forecast_hours:
        clwmr_ck = cache_key(fhour, "CLWMR_ICMR")
        diag_ck = cache_key(fhour, "CLOUD_DIAG")
        clwmr_hit = is_cached(run_dir, clwmr_ck)
        diag_hit = is_cached(run_dir, diag_ck)
        if clwmr_hit and diag_hit:
            hours_fetched += 1
            continue

        try:
            idx_text = fetch_idx(init_date, init_hour, fhour, session=session)
        except Exception:
            logger.warning(
                "Pre-cache GFS .idx fetch failed f%03d", fhour, exc_info=True,
            )
            continue

        if not clwmr_hit:
            try:
                ranges = plan_byte_ranges(idx_text, target_levels=None)
                if ranges:
                    grib_bytes = fetch_byte_ranges(
                        init_date, init_hour, fhour, ranges, session=session,
                    )
                    if grib_bytes:
                        put_cached(run_dir, clwmr_ck, grib_bytes)
                        vars_fetched += 1
                        bytes_downloaded += len(grib_bytes)
            except Exception:
                logger.warning(
                    "Pre-cache GFS CLWMR/ICMR f%03d failed", fhour, exc_info=True,
                )

        if not diag_hit:
            try:
                ranges = plan_cloud_diag_byte_ranges(idx_text)
                if ranges:
                    grib_bytes = fetch_cloud_diag_ranges(
                        init_date, init_hour, fhour, ranges, session=session,
                    )
                    if grib_bytes:
                        put_cached(run_dir, diag_ck, grib_bytes)
                        vars_fetched += 1
                        bytes_downloaded += len(grib_bytes)
            except Exception:
                logger.warning(
                    "Pre-cache GFS cloud diag f%03d failed", fhour, exc_info=True,
                )

        hours_fetched += 1

    return {
        "hours_fetched": hours_fetched,
        "vars_fetched": vars_fetched,
        "bytes_downloaded": bytes_downloaded,
        "forecast_hours_total": len(forecast_hours),
    }
