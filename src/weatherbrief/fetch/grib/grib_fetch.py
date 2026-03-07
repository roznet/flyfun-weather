"""HTTP Range downloads from AWS S3 for GFS GRIB2 data.

GFS data is publicly available on Amazon S3 at:
    https://noaa-gfs-bdp-pds.s3.amazonaws.com/

Each model run is at:
    gfs.{YYYYMMDD}/{HH}/atmos/gfs.t{HH}z.pgrb2.0p25.f{FFF}

Companion .idx files are at the same URL with .idx appended.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

from weatherbrief.fetch.grib.gfs_idx import ByteRange, CloudDiagByteRange

MAX_DOWNLOAD_WORKERS = 8

logger = logging.getLogger(__name__)

S3_BASE_URL = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
GFS_PUBLISH_DELAY_HOURS = 5  # GFS typically available ~4.5h after init

_REQUEST_TIMEOUT = 60  # seconds


def gfs_grib2_url(init_date: str, init_hour: int, forecast_hour: int) -> str:
    """Build the S3 URL for a GFS GRIB2 file.

    Args:
        init_date: YYYYMMDD format.
        init_hour: 0, 6, 12, or 18.
        forecast_hour: Forecast hour (0–384).
    """
    return (
        f"{S3_BASE_URL}/gfs.{init_date}/{init_hour:02d}/atmos/"
        f"gfs.t{init_hour:02d}z.pgrb2.0p25.f{forecast_hour:03d}"
    )


def gfs_idx_url(init_date: str, init_hour: int, forecast_hour: int) -> str:
    """Build the S3 URL for a GFS .idx file."""
    return gfs_grib2_url(init_date, init_hour, forecast_hour) + ".idx"


def find_latest_run(
    target_time: datetime,
    session: requests.Session | None = None,
    as_of_time: datetime | None = None,
) -> tuple[str, int] | None:
    """Find the latest available GFS model run for a target time.

    Tries model cycles (00z, 06z, 12z, 18z) in reverse chronological order,
    checking that enough time has passed for publication.

    Args:
        target_time: The forecast target time.
        session: Optional requests session.
        as_of_time: If set, only consider runs initialized before this time
            (for historical "as-of" briefings). Uses ``now`` if None.

    Returns:
        (init_date_YYYYMMDD, init_hour) or None if no run available.
    """
    sess = session or requests.Session()
    reference_time = as_of_time or datetime.now(timezone.utc)
    cycles = [18, 12, 6, 0]

    # Check up to 2 days back
    for days_back in range(3):
        check_date = reference_time - timedelta(days=days_back)
        date_str = check_date.strftime("%Y%m%d")
        for cycle in cycles:
            init_time = check_date.replace(
                hour=cycle, minute=0, second=0, microsecond=0,
            )
            if init_time > reference_time:
                continue
            hours_since_init = (reference_time - init_time).total_seconds() / 3600
            if hours_since_init < GFS_PUBLISH_DELAY_HOURS:
                continue

            # Check if .idx file exists (lightweight HEAD request)
            idx_url = gfs_idx_url(date_str, cycle, 0)
            try:
                resp = sess.head(idx_url, timeout=10)
                if resp.status_code == 200:
                    logger.info("Found GFS run: %s %02dz", date_str, cycle)
                    return date_str, cycle
            except requests.RequestException:
                continue

    return None


def bracket_forecast_hours(
    init_date: str,
    init_hour: int,
    target_time: datetime,
) -> tuple[int, int]:
    """Find the two forecast hours that bracket the target time.

    GFS has 1-hourly forecasts for f000–f120, 3-hourly for f120–f384.

    Returns:
        (f_prev, f_next) such that init_time + f_prev <= target <= init_time + f_next.
    """
    init_dt = datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc,
    )
    delta_hours = (target_time - init_dt).total_seconds() / 3600
    delta_hours = max(0, delta_hours)

    if delta_hours <= 120:
        # 1-hourly region
        f_prev = int(delta_hours)
        f_next = f_prev + 1
    else:
        # 3-hourly region
        base = int((delta_hours - 120) / 3)
        f_prev = 120 + base * 3
        f_next = f_prev + 3

    # Clamp
    f_prev = min(f_prev, 384)
    f_next = min(f_next, 384)

    return f_prev, f_next


def _snap_to_gfs_grid(fhour: float) -> int:
    """Snap a fractional forecast hour to the nearest GFS grid point.

    GFS: 1-hourly for f000–f120, 3-hourly for f120–f384.
    """
    if fhour <= 120:
        return round(fhour)
    base = 120 + round((fhour - 120) / 3) * 3
    return min(base, 384)


def _snap_to_gfs_grid_floor(fhour: float) -> int:
    """Snap DOWN to nearest GFS grid point (floor, not round).

    GFS: 1-hourly for f000–f120, 3-hourly for f120–f384.
    """
    if fhour <= 120:
        return int(fhour)
    base = 120 + int((fhour - 120) / 3) * 3
    return min(base, 384)


def compute_flight_window_hours(
    init_date: str,
    init_hour: int,
    departure_time: datetime,
    flight_duration_hours: float,
) -> list[int]:
    """Compute GFS forecast hours covering a flight window.

    Returns one forecast hour per UTC hour from departure through
    departure + ceil(duration), snapped to the GFS temporal grid.

    Args:
        init_date: YYYYMMDD of model init.
        init_hour: 0, 6, 12, or 18.
        departure_time: Aware UTC datetime of flight departure.
        flight_duration_hours: Flight duration in hours.

    Returns:
        Sorted deduplicated list of GFS forecast hours.
    """
    init_dt = datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc,
    )
    dep_dt = departure_time

    # Extra hour needed when departure has non-zero minutes, to bracket the
    # end of the flight window (e.g. 09:15 + 2h → need 09,10,11,12 not just 09,10,11)
    extra = 1 if dep_dt.minute > 0 else 0
    n_hours = max(1, math.ceil(flight_duration_hours) + 1 + extra)
    fhours: set[int] = set()
    for h in range(n_hours):
        utc = dep_dt + timedelta(hours=h)
        delta = (utc - init_dt).total_seconds() / 3600
        delta = max(0.0, delta)
        fhours.add(_snap_to_gfs_grid(delta))

    # Include the floor hour so non-round departure times get coverage
    if dep_dt.minute > 0:
        floor_utc = dep_dt.replace(minute=0, second=0, microsecond=0)
        floor_delta = (floor_utc - init_dt).total_seconds() / 3600
        if floor_delta >= 0:
            fhours.add(_snap_to_gfs_grid(floor_delta))

    # Include the floor native hour before departure for forward-fill coverage.
    # In the 3-hourly region (>120h), rounding may skip the preceding native
    # hour, leaving interpolated hours without GRIB diagnostics.
    dep_delta = (dep_dt - init_dt).total_seconds() / 3600
    if dep_delta > 0:
        fhours.add(_snap_to_gfs_grid_floor(dep_delta))

    return sorted(fhours)


def fetch_idx(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    session: requests.Session | None = None,
) -> str:
    """Download a GFS .idx file."""
    sess = session or requests.Session()
    url = gfs_idx_url(init_date, init_hour, forecast_hour)
    logger.debug("Fetching .idx: %s", url)
    resp = sess.get(url, timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _fetch_one_byte_range(
    url: str,
    br: ByteRange,
    session: requests.Session,
) -> tuple[int, bytes | None]:
    """Download a single byte range. Returns (index, data) for ordered reassembly."""
    range_end = br.end if br.end is not None else ""
    range_header = f"bytes={br.start}-{range_end}"
    try:
        resp = session.get(
            url,
            headers={"Range": range_header},
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code not in (200, 206):
            logger.warning(
                "Failed to fetch range %s for %s %d hPa: HTTP %d",
                range_header, br.variable, br.level_hpa, resp.status_code,
            )
            return (br.start, None)
        return (br.start, resp.content)
    except requests.RequestException:
        logger.warning("Request failed for %s %d hPa", br.variable, br.level_hpa, exc_info=True)
        return (br.start, None)


def fetch_byte_ranges(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    ranges: list[ByteRange],
    session: requests.Session | None = None,
) -> bytes:
    """Download specific byte ranges from a GFS GRIB2 file in parallel.

    Each ByteRange specifies a GRIB2 message. The messages are downloaded
    concurrently via HTTP Range requests and concatenated into a single
    valid GRIB2 file (GRIB2 files are simply concatenated messages).

    Args:
        init_date: YYYYMMDD.
        init_hour: 0, 6, 12, or 18.
        forecast_hour: Forecast hour.
        ranges: Byte ranges to download.
        session: Optional requests session.

    Returns:
        Concatenated GRIB2 bytes.
    """
    if not ranges:
        return b""

    sess = session or requests.Session()
    url = gfs_grib2_url(init_date, init_hour, forecast_hour)

    # Download all ranges in parallel
    results: list[tuple[int, bytes | None]] = []
    with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_one_byte_range, url, br, sess): br
            for br in ranges
        }
        for future in as_completed(futures):
            results.append(future.result())

    # Reassemble in original byte-offset order (GRIB2 messages are concatenated)
    result = bytearray()
    for _offset, data in sorted(results, key=lambda r: r[0]):
        if data is not None:
            result.extend(data)

    return bytes(result)


def _fetch_one_cloud_diag_range(
    url: str,
    br: CloudDiagByteRange,
    session: requests.Session,
) -> tuple[int, bytes | None]:
    """Download a single cloud diagnostic byte range."""
    range_end = br.end if br.end is not None else ""
    range_header = f"bytes={br.start}-{range_end}"
    try:
        resp = session.get(
            url,
            headers={"Range": range_header},
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code not in (200, 206):
            logger.warning(
                "Failed to fetch cloud diag range %s for %s [%s]: HTTP %d",
                range_header, br.variable, br.level_str, resp.status_code,
            )
            return (br.start, None)
        return (br.start, resp.content)
    except requests.RequestException:
        logger.warning("Request failed for cloud diag %s [%s]", br.variable, br.level_str, exc_info=True)
        return (br.start, None)


def fetch_cloud_diag_ranges(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    ranges: list[CloudDiagByteRange],
    session: requests.Session | None = None,
) -> bytes:
    """Download cloud diagnostic byte ranges from a GFS GRIB2 file in parallel.

    Same logic as fetch_byte_ranges() but for CloudDiagByteRange type.
    """
    if not ranges:
        return b""

    sess = session or requests.Session()
    url = gfs_grib2_url(init_date, init_hour, forecast_hour)

    results: list[tuple[int, bytes | None]] = []
    with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_one_cloud_diag_range, url, br, sess): br
            for br in ranges
        }
        for future in as_completed(futures):
            results.append(future.result())

    result = bytearray()
    for _offset, data in sorted(results, key=lambda r: r[0]):
        if data is not None:
            result.extend(data)

    return bytes(result)
