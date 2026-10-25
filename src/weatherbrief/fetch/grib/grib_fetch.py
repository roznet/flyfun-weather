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
from weatherbrief.fetch.grib.http_retry import get_with_retry

MAX_DOWNLOAD_WORKERS = 8

logger = logging.getLogger(__name__)

S3_BASE_URL = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
GFS_PUBLISH_DELAY_HOURS = 5  # GFS typically available ~4.5h after init

_REQUEST_TIMEOUT = 60  # seconds


def gfs_grib2_url(
    init_date: str, init_hour: int, forecast_hour: int, product: str = "pgrb2",
) -> str:
    """Build the S3 URL for a GFS GRIB2 file.

    Args:
        init_date: YYYYMMDD format.
        init_hour: 0, 6, 12, or 18.
        forecast_hour: Forecast hour (0–384).
        product: ``pgrb2`` (the main file) or ``pgrb2b``, which holds the
            in-between pressure levels (875, 825 … 525 hPa) the GRIB-primary
            sounding needs (#913).
    """
    return (
        f"{S3_BASE_URL}/gfs.{init_date}/{init_hour:02d}/atmos/"
        f"gfs.t{init_hour:02d}z.{product}.0p25.f{forecast_hour:03d}"
    )


def gfs_idx_url(
    init_date: str, init_hour: int, forecast_hour: int, product: str = "pgrb2",
) -> str:
    """Build the S3 URL for a GFS .idx file."""
    return gfs_grib2_url(init_date, init_hour, forecast_hour, product) + ".idx"


def find_latest_run(
    target_time: datetime,
    session: requests.Session | None = None,
    as_of_time: datetime | None = None,
) -> tuple[str, int] | None:
    """Find the latest available GFS model run for a target time.

    Thin wrapper around :func:`find_latest_run_with_response` for callers
    that don't need the HEAD response.
    """
    found = find_latest_run_with_response(target_time, session, as_of_time)
    return (found[0], found[1]) if found is not None else None


def find_latest_run_with_response(
    target_time: datetime,
    session: requests.Session | None = None,
    as_of_time: datetime | None = None,
) -> tuple[str, int, requests.Response] | None:
    """Find the latest available GFS model run + return the matching HEAD response.

    Tries model cycles (00z, 06z, 12z, 18z) in reverse chronological order,
    checking that enough time has passed for publication.

    The response is returned so the freshness dispatch can read its
    ``Last-Modified`` header without re-issuing the same HEAD upstream.

    Args:
        target_time: The forecast target time.
        session: Optional requests session.
        as_of_time: If set, only consider runs initialized before this time
            (for historical "as-of" briefings). Uses ``now`` if None.

    Returns:
        ``(init_date_YYYYMMDD, init_hour, head_response)`` or ``None``.
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
                    return date_str, cycle, resp
            except requests.RequestException:
                continue

    return None


GFS_HORIZON_H = 384


def gfs_window_out_of_range(
    target_time: datetime,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
) -> bool:
    """True when the newest publishable GFS run cannot reach the window end.

    Deterministic (no network), the GFS counterpart of
    ``icon_eu_window_out_of_range``: the newest cycle past
    :data:`GFS_PUBLISH_DELAY_HOURS` as-of *as_of_time*, whose 384 h horizon
    must reach ``target_time + flight_duration_hours``. Every GFS cycle runs
    to 384 h, so the newest run is the one that reaches furthest.
    """
    reference_time = as_of_time or datetime.now(timezone.utc)
    need_until = target_time + timedelta(hours=max(flight_duration_hours, 0.0))
    newest = reference_time - timedelta(hours=GFS_PUBLISH_DELAY_HOURS)
    init_time = newest.replace(
        hour=(newest.hour // 6) * 6, minute=0, second=0, microsecond=0,
    )
    return init_time + timedelta(hours=GFS_HORIZON_H) < need_until


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


def contiguous_window_forecast_hours(
    init_dt: datetime,
    departure_time: datetime,
    flight_duration_hours: float,
    *,
    hourly_to_h: int,
    coarse_step_h: int,
    horizon_h: int,
) -> list[int]:
    """Forecast hours covering ``departure .. departure + ceil(duration)``.

    The CONTIGUOUS inclusive range ``floor(departure) .. ceil(end)`` in
    hours-from-init (HRRR's shape, ``compute_hrrr_flight_window_hours``),
    mapped onto a grid that is hourly up to ``hourly_to_h`` and every
    ``coarse_step_h`` hours beyond it, clamped to ``[0, horizon_h]``.

    Deliberately NOT sample-``departure + h``-and-``round()``: ``round()`` is
    ties-to-even, so a :30 departure collapsed consecutive half-hour offsets
    onto the same even hour and silently dropped every other forecast hour
    (PR #508 review for HRRR; back-ported to GFS/ICON in #807). In the coarse
    region each in-window hour snaps DOWN to its grid step, and the step at or
    after the window end is added, so the window is always bracketed by
    native steps (the forward-fill / interpolation anchors either side).
    """
    end_dt = departure_time + timedelta(
        hours=math.ceil(max(flight_duration_hours, 0.0))
    )
    first = math.floor((departure_time - init_dt).total_seconds() / 3600)
    last = math.ceil((end_dt - init_dt).total_seconds() / 3600)
    first = max(0, first)
    last = max(first, last)

    def _floor_snap(h: int) -> int:
        if h <= hourly_to_h:
            return h
        return hourly_to_h + ((h - hourly_to_h) // coarse_step_h) * coarse_step_h

    def _ceil_snap(h: int) -> int:
        if h <= hourly_to_h:
            return h
        return hourly_to_h + math.ceil((h - hourly_to_h) / coarse_step_h) * coarse_step_h

    fhours = {min(_floor_snap(h), horizon_h) for h in range(first, last + 1)}
    fhours.add(min(_ceil_snap(last), horizon_h))
    return sorted(fhours)


def compute_flight_window_hours(
    init_date: str,
    init_hour: int,
    departure_time: datetime,
    flight_duration_hours: float,
) -> list[int]:
    """Compute GFS forecast hours covering a flight window.

    Every native GFS step from ``floor(departure)`` through
    ``ceil(departure + ceil(duration))`` — see
    :func:`contiguous_window_forecast_hours`. GFS is 1-hourly to f120 and
    3-hourly to f384.

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
    return contiguous_window_forecast_hours(
        init_dt, departure_time, flight_duration_hours,
        hourly_to_h=120, coarse_step_h=3, horizon_h=384,
    )


def fetch_idx(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    session: requests.Session | None = None,
    product: str = "pgrb2",
) -> str:
    """Download a GFS .idx file."""
    sess = session or requests.Session()
    url = gfs_idx_url(init_date, init_hour, forecast_hour, product)
    logger.debug("Fetching .idx: %s", url)
    resp = get_with_retry(sess, url, timeout=_REQUEST_TIMEOUT)
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
        resp = get_with_retry(
            session, url,
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
        resp = get_with_retry(
            session, url,
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
