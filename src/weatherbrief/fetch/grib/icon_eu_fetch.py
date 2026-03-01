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

# Variables to fetch: cloud liquid water, ice mixing ratio, pressure
ICON_EU_VARIABLES = ("qc", "qi", "p")

# Single-level cloud diagnostic variables
ICON_EU_CLOUD_DIAG_VARIABLES = (
    "ceiling", "hbas_con", "htop_con",
    "clcl", "clcm", "clch", "clct",
)

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
        failed = 0

        with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as pool:
            futures = {
                pool.submit(_download_one_file, url, sess): url
                for url in urls
            }
            for future in as_completed(futures):
                data = future.result()
                if data is not None:
                    buf.extend(data)
                    downloaded += 1
                else:
                    failed += 1

        if buf:
            result[fhour] = bytes(buf)
            logger.info(
                "ICON-EU single-level f%03d: downloaded %d/%d files (%.1f KB)",
                fhour, downloaded, downloaded + failed, len(buf) / 1024,
            )
        else:
            logger.debug(
                "ICON-EU single-level f%03d: all %d files failed", fhour, failed,
            )

    return result


def find_latest_icon_eu_run(
    target_time: datetime,
    session: requests.Session | None = None,
) -> tuple[str, int] | None:
    """Find the latest available ICON-EU model run.

    Tries cycles in reverse chronological order, checking that enough time
    has passed for publication. Probes with HEAD request on a known file.

    Returns:
        (init_date_YYYYMMDD, init_hour) or None if no run available.
    """
    sess = session or requests.Session()
    now = datetime.now(timezone.utc)

    for days_back in range(2):
        check_date = now - timedelta(days=days_back)
        date_str = check_date.strftime("%Y%m%d")
        for cycle in ICON_EU_CYCLES:
            init_time = check_date.replace(
                hour=cycle, minute=0, second=0, microsecond=0,
            )
            if init_time > now:
                continue
            hours_since_init = (now - init_time).total_seconds() / 3600
            if hours_since_init < ICON_EU_PUBLISH_DELAY_HOURS:
                continue

            # Probe: check if a level-74 P file for forecast hour 000 exists
            probe_url = icon_eu_file_url(date_str, cycle, 0, 74, "p")
            try:
                resp = sess.head(probe_url, timeout=10)
                if resp.status_code == 200:
                    logger.info("Found ICON-EU run: %s %02dz", date_str, cycle)
                    return date_str, cycle
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

    n_hours = max(1, math.ceil(flight_duration_hours) + 1)
    fhours: set[int] = set()
    for h in range(n_hours):
        utc = dep_dt + timedelta(hours=h)
        delta = (utc - init_dt).total_seconds() / 3600
        delta = max(0.0, delta)
        fhours.add(_snap_to_icon_eu_grid(delta))

    return sorted(fhours)


def _download_one_file(
    url: str,
    session: requests.Session,
) -> bytes | None:
    """Download and decompress a single bz2-compressed GRIB2 file."""
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            logger.debug("HTTP %d for %s", resp.status_code, url.split("/")[-1])
            return None
        return bz2.decompress(resp.content)
    except (requests.RequestException, OSError) as e:
        logger.debug("Download failed %s: %s", url.split("/")[-1], e)
        return None


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
    failed = 0

    with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as pool:
        futures = {
            pool.submit(_download_one_file, url, sess): url
            for url in urls
        }
        for future in as_completed(futures):
            data = future.result()
            if data is not None:
                result.extend(data)
                downloaded += 1
            else:
                failed += 1

    logger.info(
        "ICON-EU f%03d: downloaded %d/%d files (%.1f KB)",
        forecast_hour, downloaded, downloaded + failed, len(result) / 1024,
    )
    return bytes(result)
