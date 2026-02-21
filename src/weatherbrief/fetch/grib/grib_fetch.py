"""HTTP Range downloads from AWS S3 for GFS GRIB2 data.

GFS data is publicly available on Amazon S3 at:
    https://noaa-gfs-bdp-pds.s3.amazonaws.com/

Each model run is at:
    gfs.{YYYYMMDD}/{HH}/atmos/gfs.t{HH}z.pgrb2.0p25.f{FFF}

Companion .idx files are at the same URL with .idx appended.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

from weatherbrief.fetch.grib.gfs_idx import ByteRange, CloudDiagByteRange

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
) -> tuple[str, int] | None:
    """Find the latest available GFS model run for a target time.

    Tries model cycles (00z, 06z, 12z, 18z) in reverse chronological order,
    checking that enough time has passed for publication.

    Returns:
        (init_date_YYYYMMDD, init_hour) or None if no run available.
    """
    sess = session or requests.Session()
    now = datetime.now(timezone.utc)
    cycles = [18, 12, 6, 0]

    # Check up to 2 days back
    for days_back in range(3):
        check_date = now - timedelta(days=days_back)
        date_str = check_date.strftime("%Y%m%d")
        for cycle in cycles:
            init_time = check_date.replace(
                hour=cycle, minute=0, second=0, microsecond=0,
            )
            if init_time > now:
                continue
            hours_since_init = (now - init_time).total_seconds() / 3600
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


def fetch_byte_ranges(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    ranges: list[ByteRange],
    session: requests.Session | None = None,
) -> bytes:
    """Download specific byte ranges from a GFS GRIB2 file and concatenate them.

    Each ByteRange specifies a GRIB2 message. The messages are downloaded
    individually via HTTP Range requests and concatenated into a single
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
    result = bytearray()

    for br in ranges:
        range_end = br.end if br.end is not None else ""
        range_header = f"bytes={br.start}-{range_end}"
        logger.debug(
            "Fetching %s %d hPa: %s (%s)",
            br.variable, br.level_hpa, url.split("/")[-1], range_header,
        )
        resp = sess.get(
            url,
            headers={"Range": range_header},
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code not in (200, 206):
            logger.warning(
                "Failed to fetch range %s for %s %d hPa: HTTP %d",
                range_header, br.variable, br.level_hpa, resp.status_code,
            )
            continue
        result.extend(resp.content)

    return bytes(result)


def fetch_cloud_diag_ranges(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    ranges: list[CloudDiagByteRange],
    session: requests.Session | None = None,
) -> bytes:
    """Download cloud diagnostic byte ranges from a GFS GRIB2 file.

    Same logic as fetch_byte_ranges() but for CloudDiagByteRange type.
    """
    if not ranges:
        return b""

    sess = session or requests.Session()
    url = gfs_grib2_url(init_date, init_hour, forecast_hour)
    result = bytearray()

    for br in ranges:
        range_end = br.end if br.end is not None else ""
        range_header = f"bytes={br.start}-{range_end}"
        logger.debug(
            "Fetching cloud diag %s [%s]: %s (%s)",
            br.variable, br.level_str, url.split("/")[-1], range_header,
        )
        resp = sess.get(
            url,
            headers={"Range": range_header},
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code not in (200, 206):
            logger.warning(
                "Failed to fetch cloud diag range %s for %s [%s]: HTTP %d",
                range_header, br.variable, br.level_str, resp.status_code,
            )
            continue
        result.extend(resp.content)

    return bytes(result)
