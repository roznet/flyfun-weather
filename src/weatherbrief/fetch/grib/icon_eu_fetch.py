"""DWD ICON-EU GRIB2 download from opendata.dwd.de.

ICON-EU provides cloud liquid water (QC) and ice mixing ratio (QI) on model
levels, covering Europe at ~6.5 km resolution on a regular lat-lon grid.

Data structure differs from GFS:
- Individual bz2-compressed files per variable/level/timestep
- Model levels (not pressure levels) — need P field for vertical interpolation
- Separate files per level (no .idx companion files)
"""

from __future__ import annotations

import bz2
import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

# DWD Open Data base URL
DWD_BASE_URL = "https://opendata.dwd.de/weather/nwp/icon-eu/grib"

# ICON-EU model-level horizon depends on the run cycle (verified empirically
# against opendata.dwd.de directory listings):
# - Main runs (00z, 06z, 12z, 18z): hourly to 78h, 3-hourly to 120h
# - Short runs (03z, 09z, 15z, 21z): hourly to 30h, then 6-hourly to 48h
#
# We cap short-run usage at 30h so the run-picker falls back to the prior main
# run for any flight extending past +30h. This keeps every briefing on a
# uniform hourly grid (short run hourly 0-30h, OR main run hourly 0-78h) and
# avoids 404s on f031–f035/f037–f041/f043–f047 which are not published.
ICON_EU_MAIN_CYCLES = {0, 6, 12, 18}
ICON_EU_MODEL_LEVEL_MAX_HOUR_MAIN = 120
ICON_EU_MODEL_LEVEL_MAX_HOUR_SHORT = 30


def icon_eu_model_level_max_hour(init_hour: int) -> int:
    """Return the model-level forecast horizon for the given ICON-EU cycle."""
    if init_hour in ICON_EU_MAIN_CYCLES:
        return ICON_EU_MODEL_LEVEL_MAX_HOUR_MAIN
    return ICON_EU_MODEL_LEVEL_MAX_HOUR_SHORT


def icon_eu_window_out_of_range(
    target_time: datetime,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
) -> bool:
    """True when no publishable ICON-EU run has enough horizon for the flight.

    Deterministic (no network): walks the same cycles and publication-delay
    logic as :func:`find_latest_icon_eu_run_with_response` and checks whether
    any run available as-of *as_of_time* reaches ``target_time +
    flight_duration_hours``. ICON-EU's model-level horizon is only 120h (5
    days), so any flight departing >~5 days out is beyond it.

    Used to distinguish the *expected* "flight beyond ICON-EU horizon" skip
    (an info-level condition, common for flights 5–9 days out) from a genuine
    upstream/probe failure — the run-finder returns ``None`` for both.
    """
    reference_time = as_of_time or datetime.now(timezone.utc)
    need_until = target_time + timedelta(hours=flight_duration_hours)
    for days_back in range(2):
        check_date = reference_time - timedelta(days=days_back)
        for cycle in ICON_EU_CYCLES:
            init_time = check_date.replace(
                hour=cycle, minute=0, second=0, microsecond=0,
            )
            if init_time > reference_time:
                continue
            hours_since_init = (reference_time - init_time).total_seconds() / 3600
            if hours_since_init < ICON_EU_PUBLISH_DELAY_HOURS:
                continue
            horizon = init_time + timedelta(hours=icon_eu_model_level_max_hour(cycle))
            if horizon >= need_until:
                # At least one publishable run reaches the flight window.
                return False
    return True

# ICON-EU domain bounds (regular lat-lon grid)
ICON_EU_LAT_MIN = 29.5
ICON_EU_LAT_MAX = 70.5
ICON_EU_LON_MIN = -23.5
ICON_EU_LON_MAX = 62.5

# ICON-EU cycles: every 3 hours
ICON_EU_CYCLES = [21, 18, 15, 12, 9, 6, 3, 0]

# Publication delay: ICON-EU is typically available ~3h after init
ICON_EU_PUBLISH_DELAY_HOURS = 3

# Model levels covering surface (~74) to ~FL280 (~level 35).
# Level 74 is near surface, level 1 is top of atmosphere.
# Levels 35-74 cover approximately 300-1000 hPa.
ICON_EU_MODEL_LEVEL_MIN = 35
ICON_EU_MODEL_LEVEL_MAX = 74

# Variables to fetch: sounding + cloud microphysics + pressure for vertical interpolation.
# Note: "qv" (specific humidity) is used instead of "relhum" because relhum is only
# available on pressure levels, not model levels on DWD Open Data.
# "fi" (geopotential) is NOT available on model levels — only on pressure levels.
# Geopotential height is instead derived from pressure via the hypsometric equation
# in the sounding analysis (or omitted — not critical for core analysis).
ICON_EU_VARIABLES = ("qc", "qi", "clc", "p", "t", "qv", "u", "v", "w")

# Single-level cloud diagnostic variables.
# cape_ml / cin_ml (mixed-layer CAPE/CIN, instantaneous) added for the
# native convective track (#283 Phase 2). rain_con (convective rain,
# accumulated since init) added in #421 so the convective firing gate and
# native corroboration can evaluate ICON towers — the rate is de-accumulated
# in the enrichment loop, not here.
ICON_EU_CLOUD_DIAG_VARIABLES = (
    "ceiling", "hbas_con", "htop_con",
    "clcl", "clcm", "clch", "clct",
    "cape_ml", "cin_ml",
    "rain_con",
)

# Cache-key label for the single-level cloud-diagnostic blob. Bumped to V2 in
# #421 when rain_con was added: the blob is cached under ONE key for all
# variables, so adding a variable changes the blob's *content* but not its key.
# Bumping the label forces existing (rain_con-less) cached blobs to re-fetch —
# without it a warm cache would silently keep the pre-#421 gate-less behaviour.
# Referenced everywhere the blob is cached (prefetch, enrichment, precache,
# standalone verification) so the four call sites can never drift apart.
ICON_EU_CLOUD_DIAG_CACHE_KEY = "ICON_EU_CLOUD_DIAG_V2"

# Parallel download settings
MAX_DOWNLOAD_WORKERS = 8
REQUEST_TIMEOUT = 30  # seconds per file


def route_in_icon_eu_domain(route_points: list) -> bool:
    """Check if all route points fall within the ICON-EU domain.

    All-or-nothing: returns False if any point is outside.
    """
    for rp in route_points:
        if not (ICON_EU_LAT_MIN <= rp.lat <= ICON_EU_LAT_MAX):
            return False
        if not (ICON_EU_LON_MIN <= rp.lon <= ICON_EU_LON_MAX):
            return False
    return True


def icon_eu_file_url(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    level: int,
    variable: str,
) -> str:
    """Build URL for a single ICON-EU GRIB2 bz2 file.

    Example:
        https://opendata.dwd.de/weather/nwp/icon-eu/grib/00/qc/
        icon-eu_europe_regular-lat-lon_model-level_2026022100_000_35_QC.grib2.bz2
    """
    var_lower = variable.lower()
    var_upper = variable.upper()
    return (
        f"{DWD_BASE_URL}/{init_hour:02d}/{var_lower}/"
        f"icon-eu_europe_regular-lat-lon_model-level_"
        f"{init_date}{init_hour:02d}_{forecast_hour:03d}_{level:02d}_{var_upper}"
        f".grib2.bz2"
    )


def icon_eu_single_level_url(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    variable: str,
) -> str:
    """Build URL for a single ICON-EU GRIB2 bz2 file (no level number).

    Example:
        https://opendata.dwd.de/weather/nwp/icon-eu/grib/00/ceiling/
        icon-eu_europe_regular-lat-lon_single-level_2026022100_006_CEILING.grib2.bz2
    """
    var_lower = variable.lower()
    var_upper = variable.upper()
    return (
        f"{DWD_BASE_URL}/{init_hour:02d}/{var_lower}/"
        f"icon-eu_europe_regular-lat-lon_single-level_"
        f"{init_date}{init_hour:02d}_{forecast_hour:03d}_{var_upper}"
        f".grib2.bz2"
    )


def fetch_icon_eu_single_level(
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    variables: list[str] | None = None,
    session: requests.Session | None = None,
    max_workers: int = MAX_DOWNLOAD_WORKERS,
) -> dict[int, bytes]:
    """Download ICON-EU single-level GRIB2 fields and return concatenated bytes per fhour.

    Downloads single-level cloud diagnostic files (no level dimension)
    using the same parallel pattern as model-level fetch.

    Args:
        init_date: YYYYMMDD format.
        init_hour: Cycle hour (0, 3, 6, ..., 21).
        forecast_hours: Forecast hours to download.
        variables: Variable names (defaults to ICON_EU_CLOUD_DIAG_VARIABLES).
        session: Optional requests session.
        max_workers: Per-call download thread count. Callers that already
            run several fetches concurrently pass a smaller value so the
            total connection count stays bounded.

    Returns:
        Dict of {forecast_hour: concatenated decompressed GRIB2 bytes}.
    """
    if variables is None:
        variables = list(ICON_EU_CLOUD_DIAG_VARIABLES)

    sess = session or requests.Session()
    result: dict[int, bytes] = {}

    for fhour in forecast_hours:
        urls = [
            icon_eu_single_level_url(init_date, init_hour, fhour, var)
            for var in variables
        ]

        buf = bytearray()
        downloaded = 0
        failures: dict[int | str, int] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_download_one_file, url, sess): url
                for url in urls
            }
            for future in as_completed(futures):
                data, status = future.result()
                if data is not None:
                    buf.extend(data)
                    downloaded += 1
                else:
                    failures[status] = failures.get(status, 0) + 1

        total_failed = sum(failures.values())
        if buf:
            result[fhour] = bytes(buf)
            logger.info(
                "ICON-EU single-level f%03d: downloaded %d/%d files (%.1f KB)%s",
                fhour, downloaded, downloaded + total_failed, len(buf) / 1024,
                _format_failure_summary(failures),
            )
        elif total_failed:
            logger.warning(
                "ICON-EU single-level f%03d: all %d files failed%s",
                fhour, total_failed, _format_failure_summary(failures),
            )

    return result


def find_latest_icon_eu_run(
    target_time: datetime,
    session: requests.Session | None = None,
    as_of_time: datetime | None = None,
    cover_until: datetime | None = None,
) -> tuple[str, int] | None:
    """Find the latest available ICON-EU model run whose horizon covers the flight.

    Thin wrapper around :func:`find_latest_icon_eu_run_with_response` for
    callers that don't need the HEAD response.
    """
    found = find_latest_icon_eu_run_with_response(
        target_time, session, as_of_time, cover_until,
    )
    return (found[0], found[1]) if found is not None else None


def find_latest_icon_eu_run_with_response(
    target_time: datetime,
    session: requests.Session | None = None,
    as_of_time: datetime | None = None,
    cover_until: datetime | None = None,
) -> tuple[str, int, requests.Response] | None:
    """Find the latest ICON-EU run + return the matching probe HEAD response.

    Tries cycles in reverse chronological order, checking that enough time
    has passed for publication and that the run's model-level horizon
    reaches ``cover_until``.  If ``cover_until`` is None, any run that
    covers ``target_time`` is accepted.

    The response is returned so the freshness dispatch can read its
    ``Last-Modified`` header without re-issuing the same HEAD upstream.

    Args:
        target_time: Flight departure time.
        session: Optional requests session.
        as_of_time: If set, only consider runs initialized before this time
            (for historical "as-of" briefings). Uses ``now`` if None.
        cover_until: Latest time the run must cover (typically departure +
            flight duration).  Runs whose model-level horizon falls short
            are skipped in favour of an older main run with longer range.

    Returns:
        ``(init_date_YYYYMMDD, init_hour, head_response)`` or ``None``.
    """
    sess = session or requests.Session()
    reference_time = as_of_time or datetime.now(timezone.utc)
    need_until = cover_until or target_time

    for days_back in range(2):
        check_date = reference_time - timedelta(days=days_back)
        date_str = check_date.strftime("%Y%m%d")
        for cycle in ICON_EU_CYCLES:
            init_time = check_date.replace(
                hour=cycle, minute=0, second=0, microsecond=0,
            )
            if init_time > reference_time:
                continue
            hours_since_init = (reference_time - init_time).total_seconds() / 3600
            if hours_since_init < ICON_EU_PUBLISH_DELAY_HOURS:
                continue

            # Check if this run's horizon covers the flight
            max_hour = icon_eu_model_level_max_hour(cycle)
            horizon = init_time + timedelta(hours=max_hour)
            if horizon < need_until:
                logger.debug(
                    "ICON-EU %s %02dz: horizon %dh doesn't reach flight end, skipping",
                    date_str, cycle, max_hour,
                )
                continue

            # Probe: check if a level-74 P file for forecast hour 000 exists
            probe_url = icon_eu_file_url(date_str, cycle, 0, 74, "p")
            try:
                resp = sess.head(probe_url, timeout=10)
                if resp.status_code == 200:
                    logger.info("Found ICON-EU run: %s %02dz (horizon %dh)", date_str, cycle, max_hour)
                    return date_str, cycle, resp
            except requests.RequestException:
                continue

    return None


def bracket_icon_eu_forecast_hours(
    init_date: str,
    init_hour: int,
    target_time: datetime,
) -> tuple[int, int]:
    """Find the two forecast hours that bracket the target time.

    ICON-EU has hourly forecasts for 0-78h, 3-hourly for 78-120h.

    Returns:
        (f_prev, f_next) bracketing the target.
    """
    init_dt = datetime.strptime(
        f"{init_date}{init_hour:02d}", "%Y%m%d%H",
    ).replace(tzinfo=timezone.utc)
    delta_hours = (target_time - init_dt).total_seconds() / 3600
    delta_hours = max(0, delta_hours)

    if delta_hours <= 78:
        # Hourly region
        f_prev = int(delta_hours)
        f_next = f_prev + 1
    else:
        # 3-hourly region
        base = int((delta_hours - 78) / 3)
        f_prev = 78 + base * 3
        f_next = f_prev + 3

    # Clamp to max forecast hour
    f_prev = min(f_prev, 120)
    f_next = min(f_next, 120)

    return f_prev, f_next


def _snap_to_icon_eu_grid(fhour: float) -> int:
    """Snap a fractional forecast hour to the nearest ICON-EU grid point.

    ICON-EU: 1-hourly for 0–78h, 3-hourly for 78–120h.
    """
    if fhour <= 78:
        return round(fhour)
    base = 78 + round((fhour - 78) / 3) * 3
    return min(base, 120)


def _snap_to_icon_eu_grid_floor(fhour: float) -> int:
    """Snap DOWN to nearest ICON-EU grid point (floor, not round).

    ICON-EU: 1-hourly for 0–78h, 3-hourly for 78–120h.
    """
    if fhour <= 78:
        return int(fhour)
    base = 78 + int((fhour - 78) / 3) * 3
    return min(base, 120)


def icon_eu_previous_step(fhour: int) -> int | None:
    """Return the ICON-EU forecast hour immediately preceding *fhour*.

    Respects the temporal grid — 1-hourly at or below 78h (step −1), 3-hourly
    above it (step −3) — mirroring :func:`_snap_to_icon_eu_grid`. Returns
    ``None`` for ``fhour == 0``: accumulation is 0 at init by definition, so
    there is no earlier step to difference an accumulated field against.

    Used to prepend one leading single-level step to the on-demand cloud-diag
    fetch so the first flight-window hour has a predecessor to de-accumulate
    ``rain_con`` against. ICON is downloaded exactly on the window hours (no
    ±margin like the locally-mirrored ECMWF run), so without this the first
    hour would have no rate (#421).
    """
    if fhour <= 0:
        return None
    if fhour <= 78:
        return fhour - 1
    return fhour - 3


def icon_eu_conv_rain_rate_mm_h(
    rain_con: float | None,
    prev_rain_con: float | None,
    window_h: float | None,
) -> float | None:
    """De-accumulate ICON ``rain_con`` into a convective-precip rate (mm/h).

    ``rain_con`` is accumulated since init in kg/m² ≡ mm — **already mm**, so
    there is NO ×1000 (that conversion is only for ECMWF ``cp``, which is m
    water equivalent). Clamped at 0 so a decreasing accumulation (new run /
    GRIB glitch) yields 0 rather than a negative rate.

    Returns ``None`` — not ``0.0`` — when any input is missing (no predecessor
    step, uncovered point, or non-positive window). ``None`` = unknown, which
    the firing gate treats as missing-data-safe; ``0.0`` would actively hold a
    tower down. The two are **not** interchangeable (#421).
    """
    if rain_con is None or prev_rain_con is None or window_h is None or window_h <= 0:
        return None
    return max(0.0, (rain_con - prev_rain_con) / window_h)


def compute_icon_eu_flight_window_hours(
    init_date: str,
    init_hour: int,
    departure_time: datetime,
    flight_duration_hours: float,
) -> list[int]:
    """Compute ICON-EU forecast hours covering a flight window.

    Same logic as GFS but snapped to the ICON-EU temporal grid.
    """
    init_dt = datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc,
    )
    dep_dt = departure_time

    extra = 1 if dep_dt.minute > 0 else 0
    n_hours = max(1, math.ceil(flight_duration_hours) + 1 + extra)
    fhours: set[int] = set()
    for h in range(n_hours):
        utc = dep_dt + timedelta(hours=h)
        delta = (utc - init_dt).total_seconds() / 3600
        delta = max(0.0, delta)
        fhours.add(_snap_to_icon_eu_grid(delta))

    # Include the floor hour so non-round departure times get coverage
    if dep_dt.minute > 0:
        floor_utc = dep_dt.replace(minute=0, second=0, microsecond=0)
        floor_delta = (floor_utc - init_dt).total_seconds() / 3600
        if floor_delta >= 0:
            fhours.add(_snap_to_icon_eu_grid(floor_delta))

    # Include the floor native hour before departure for forward-fill coverage.
    # In the 3-hourly region (>78h), rounding may skip the preceding native
    # hour, leaving interpolated hours without GRIB diagnostics.
    dep_delta = (dep_dt - init_dt).total_seconds() / 3600
    if dep_delta > 0:
        fhours.add(_snap_to_icon_eu_grid_floor(dep_delta))

    return sorted(fhours)


def _format_failure_summary(failures: dict[int | str, int]) -> str:
    """Format a per-status failure summary for log lines, e.g. '404=40'."""
    if not failures:
        return ""
    parts = [f"{k}={v}" for k, v in sorted(failures.items(), key=lambda kv: str(kv[0]))]
    return " (" + ",".join(parts) + ")"


def _download_one_file(
    url: str,
    session: requests.Session,
) -> tuple[bytes | None, int | str]:
    """Download and decompress a single bz2-compressed GRIB2 file.

    Returns ``(bytes_or_None, status_or_error_label)`` so callers can
    aggregate failure modes (HTTP 404 vs network errors).
    """
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            logger.debug("HTTP %d for %s", resp.status_code, url.split("/")[-1])
            return None, resp.status_code
        return bz2.decompress(resp.content), 200
    except requests.RequestException as e:
        logger.debug("Download failed %s: %s", url.split("/")[-1], e)
        return None, "network"
    except OSError as e:
        logger.debug("Decompress failed %s: %s", url.split("/")[-1], e)
        return None, "decompress"


def fetch_icon_eu_fields(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    levels: list[int],
    variables: list[str],
    session: requests.Session | None = None,
) -> bytes:
    """Download ICON-EU GRIB2 fields in parallel and return concatenated bytes.

    Downloads individual bz2-compressed files for each variable/level
    combination using a thread pool, decompresses, and concatenates into
    a single GRIB2 byte stream.

    Args:
        init_date: YYYYMMDD format.
        init_hour: Cycle hour (0, 3, 6, ..., 21).
        forecast_hour: Forecast hour.
        levels: Model level numbers to download.
        variables: Variable names (e.g. ["qc", "qi", "p"]).
        session: Optional requests session.

    Returns:
        Concatenated decompressed GRIB2 bytes.
    """
    sess = session or requests.Session()
    urls: list[str] = []
    for var in variables:
        for level in levels:
            urls.append(icon_eu_file_url(init_date, init_hour, forecast_hour, level, var))

    result = bytearray()
    downloaded = 0
    failures: dict[int | str, int] = {}

    with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as pool:
        futures = {
            pool.submit(_download_one_file, url, sess): url
            for url in urls
        }
        for future in as_completed(futures):
            data, status = future.result()
            if data is not None:
                result.extend(data)
                downloaded += 1
            else:
                failures[status] = failures.get(status, 0) + 1

    total_failed = sum(failures.values())
    logger.info(
        "ICON-EU f%03d: downloaded %d/%d files (%.1f KB)%s",
        forecast_hour, downloaded, downloaded + total_failed, len(result) / 1024,
        _format_failure_summary(failures),
    )
    return bytes(result)


def fetch_icon_eu_per_variable(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    levels: list[int],
    variables: list[str],
    session: requests.Session | None = None,
    max_workers: int = MAX_DOWNLOAD_WORKERS,
) -> dict[str, bytes]:
    """Download ICON-EU GRIB2 fields per variable for memory-efficient decoding.

    Same as fetch_icon_eu_fields but returns separate bytes per variable,
    so callers can decode one variable at a time and free memory between.

    ``max_workers`` bounds this call's download threads; callers running
    several per-variable fetches concurrently pass a smaller value so the
    total connection count stays bounded.

    Returns:
        {variable_name: concatenated_decompressed_grib2_bytes}.
    """
    sess = session or requests.Session()
    result: dict[str, bytes] = {}

    for var in variables:
        urls = [
            icon_eu_file_url(init_date, init_hour, forecast_hour, level, var)
            for level in levels
        ]

        buf = bytearray()
        downloaded = 0
        failures: dict[int | str, int] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_download_one_file, url, sess): url
                for url in urls
            }
            for future in as_completed(futures):
                data, status = future.result()
                if data is not None:
                    buf.extend(data)
                    downloaded += 1
                else:
                    failures[status] = failures.get(status, 0) + 1

        total_failed = sum(failures.values())
        if buf:
            result[var] = bytes(buf)
            logger.info(
                "ICON-EU f%03d %s: downloaded %d/%d levels (%.1f KB)%s",
                forecast_hour, var, downloaded, downloaded + total_failed,
                len(buf) / 1024, _format_failure_summary(failures),
            )
        else:
            logger.warning(
                "ICON-EU f%03d %s: all %d files failed%s",
                forecast_hour, var, total_failed, _format_failure_summary(failures),
            )

    return result
