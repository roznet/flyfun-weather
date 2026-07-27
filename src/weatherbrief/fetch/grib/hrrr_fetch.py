"""NOAA HRRR CONUS GRIB2 access via AWS S3 (issue #457).

HRRR (High-Resolution Rapid Refresh, 3 km, convection-allowing,
radar-assimilating) upgrades the ``gfs`` model slot in place when the whole
route fits the CONUS domain and the flight window is within the selected
run's horizon — the short-range quality upgrade for US routes, same product
decision as the ICON-D2 upgrade of the icon slot (#456). Unlike the GFS
patch-onto-Open-Meteo style, HRRR does a FULL sounding replacement
(ICON/ECMWF pattern): a genuinely different model (WRF-ARW regional vs GFS
FV3 global), so the upgrade is visibly badged ``GFS (HRRR)``.

Data layout (verified against the live bucket):
    https://noaa-hrrr-bdp-pds.s3.amazonaws.com/
        hrrr.{YYYYMMDD}/conus/hrrr.t{HH}z.wrfprsf{FF}.grib2      (FF is 2-digit)
        ... + companion .idx files, same format as GFS.

Everything needed lives in the single ``wrfprs`` file per forecast hour:
the full sounding on 40 pressure levels at 25 hPa spacing (TMP, DPT direct,
RH, UGRD/VGRD, VVEL already Pa/s, HGT direct), microphysics (CLMR +
``CIMIXR`` — HRRR's name for ice mixing ratio, NOT ``ICMR``), and
instantaneous cloud diagnostics (band covers, ceiling, cloud base) plus
mixed-layer CAPE/CIN.

Cycles run HOURLY, but only 00/06/12/18z extend to 48 h — the other cycles
stop at 18 h. Run selection prefers the freshest cycle whose horizon covers
the flight window, which usually means an extended cycle for anything beyond
a few hours out.

The grid is Lambert-projected (not a lat/lon rectangle), so the domain gate
transforms route points into grid x/y with the same pyproj projection the
decoder uses and checks they land within grid bounds — exact and free once
the projection exists. Grid constants below are from the live GRIB header
(2026-07-26 12z) and are stable product properties; the decode path reads
them from each file's own attributes, so a grid change degrades to a decode
miss, never to wrong positions.

Out of scope (#457): HRRR-Alaska, sub-hourly output, HRRR ensemble. RRFS
(NOAA's successor model) should be mostly a bucket/path + constants swap
within this module.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

from weatherbrief.fetch.grib.gfs_idx import (
    ByteRange,
    CloudDiagByteRange,
    plan_byte_ranges,
    plan_cloud_diag_byte_ranges,
)
from weatherbrief.fetch.grib.grib_fetch import (
    MAX_DOWNLOAD_WORKERS,
    _fetch_one_byte_range,
    _fetch_one_cloud_diag_range,
)

logger = logging.getLogger(__name__)

HRRR_S3_BASE_URL = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"

# Full wrfprs file set completes ~45–60 min after init; 75 min with margin.
# The run-finder HEAD-probes the actual last-needed forecast hour anyway, so
# a small miss just walks back one cycle.
HRRR_PUBLISH_DELAY_HOURS = 1.25

# Only the synoptic cycles extend to 48 h; every other hourly cycle stops at 18 h.
HRRR_EXTENDED_CYCLES = frozenset({0, 6, 12, 18})
HRRR_HORIZON_EXTENDED_H = 48
HRRR_HORIZON_SHORT_H = 18

# Sounding variables in the .idx (wgrib2 names). DPT and HGT are direct — no
# Magnus derivation, no hypsometric fallback needed. VVEL is already Pa/s (no
# omega conversion). CIMIXR is HRRR's name for ice mixing ratio (the CLMR/CLWMR
# style naming quirk the decode map also handles). Bonus species (RWMR, SNMR,
# GRLE) are deliberately not fetched — no consumer yet.
HRRR_SOUNDING_VARIABLES = frozenset(
    {"TMP", "DPT", "RH", "UGRD", "VGRD", "VVEL", "HGT", "CLMR", "CIMIXR"}
)

# Skip 50–125 hPa: above the GA envelope and above the 150 hPa top the
# Open-Meteo GFS sounding already uses. Expressed as a floor rather than an
# explicit level list so a level-set change upstream degrades gracefully.
HRRR_SOUNDING_MIN_LEVEL_HPA = 150

# Cloud diagnostics + convective indices, all instantaneous ("N hour fcst" —
# HRRR publishes no averaged forms, so none of the GFS averaged-window
# machinery applies). Level strings verified against the live .idx:
# - band covers + total cover (entire atmosphere)
# - HGT at cloud ceiling / overall cloud base (per-band base/top/temp geometry
#   does NOT exist in HRRR — the diagnostics shape is closer to ECMWF's than
#   GFS's). ``HGT:cloud top`` / ``PRES:cloud base|top`` exist upstream but have
#   no NWPCloudDiagnostics home, so they are not fetched.
# - CAPE/CIN: only the 90-0 mb mixed layer (≈ ECMWF's lowest-100-hPa mixed
#   layer feeding ml_cape/ml_cin). Fetching the other layers (surface, 180-0,
#   255-0 mb) would decode into the SAME cfgrib (cape, pressureFromGroundLayer)
#   key and make the decode ambiguous.
HRRR_CLOUD_DIAG_VARIABLES: dict[str, set[str]] = {
    "LCDC": {"low cloud layer"},
    "MCDC": {"middle cloud layer"},
    "HCDC": {"high cloud layer"},
    "TCDC": {"entire atmosphere"},
    "HGT": {"cloud ceiling", "cloud base"},
    "CAPE": {"90-0 mb above ground"},
    "CIN": {"90-0 mb above ground"},
}

_HRRR_CLOUD_DIAG_PAIRS: set[tuple[str, str]] = {
    (var, lev)
    for var, levels in HRRR_CLOUD_DIAG_VARIABLES.items()
    for lev in levels
}

_REQUEST_TIMEOUT = 60  # seconds


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def hrrr_grib2_url(init_date: str, init_hour: int, forecast_hour: int) -> str:
    """Build the S3 URL for an HRRR CONUS pressure-file (wrfprs).

    Note the 2-digit forecast hour — GFS uses 3 digits.
    """
    return (
        f"{HRRR_S3_BASE_URL}/hrrr.{init_date}/conus/"
        f"hrrr.t{init_hour:02d}z.wrfprsf{forecast_hour:02d}.grib2"
    )


def hrrr_idx_url(init_date: str, init_hour: int, forecast_hour: int) -> str:
    """Build the S3 URL for an HRRR .idx file."""
    return hrrr_grib2_url(init_date, init_hour, forecast_hour) + ".idx"


# ---------------------------------------------------------------------------
# CONUS domain gate (Lambert grid)
# ---------------------------------------------------------------------------

# HRRR CONUS grid definition, read off the live GRIB header (2026-07-26 12z).
# Lambert conformal, tangent at 38.5°N, central meridian 262.5°E, on the
# GRIB2 spherical earth (shapeOfTheEarth=6, R=6371229 m — cfgrib does not
# expose the radius as an attribute, so the constant is pinned here).
HRRR_GRID_NX = 1799
HRRR_GRID_NY = 1059
HRRR_GRID_DX_M = 3000.0
HRRR_GRID_DY_M = 3000.0
HRRR_GRID_LAT_FIRST = 21.138123
HRRR_GRID_LON_FIRST = 237.280472
HRRR_GRID_LAD = 38.5
HRRR_GRID_LOV = 262.5
HRRR_GRID_LATIN1 = 38.5
HRRR_GRID_LATIN2 = 38.5
HRRR_EARTH_RADIUS_M = 6371229.0


def _hrrr_projection():
    """The HRRR CONUS Lambert projection (pyproj, lazy import)."""
    import pyproj

    return pyproj.Proj(
        proj="lcc",
        lat_1=HRRR_GRID_LATIN1,
        lat_2=HRRR_GRID_LATIN2,
        lat_0=HRRR_GRID_LAD,
        lon_0=HRRR_GRID_LOV,
        R=HRRR_EARTH_RADIUS_M,
    )


def hrrr_grid_fractional_indices(
    latitudes: list[float],
    longitudes: list[float],
) -> "tuple[list[float], list[float]]":
    """Transform lat/lon points into fractional HRRR grid indices (fx, fy).

    ``fx`` runs along the x (west→east) axis 0..Nx-1, ``fy`` along y
    (south→north) 0..Ny-1; values outside those ranges are off-grid. Same
    math as the decode-time interpolation, so the domain gate and the
    decoder can never disagree about what "inside CONUS" means.
    """
    proj = _hrrr_projection()
    x0, y0 = proj(HRRR_GRID_LON_FIRST, HRRR_GRID_LAT_FIRST)
    xs, ys = proj(longitudes, latitudes)
    try:
        fx = [(x - x0) / HRRR_GRID_DX_M for x in xs]
        fy = [(y - y0) / HRRR_GRID_DY_M for y in ys]
    except TypeError:
        # Single scalar in, single scalar out (pyproj collapses 1-lists).
        fx = [(xs - x0) / HRRR_GRID_DX_M]
        fy = [(ys - y0) / HRRR_GRID_DY_M]
    return fx, fy


def route_in_hrrr_domain(route_points: list) -> bool:
    """Check whether ALL route points fall inside the HRRR CONUS grid.

    All-or-nothing (#457 gate, same product rule as ICON-D2): one point
    outside means the whole gfs slot stays on plain GFS — never a mixed
    HRRR/GFS briefing.
    """
    if not route_points:
        return False
    fx, fy = hrrr_grid_fractional_indices(
        [rp.lat for rp in route_points], [rp.lon for rp in route_points],
    )
    return all(
        0.0 <= x <= HRRR_GRID_NX - 1 and 0.0 <= y <= HRRR_GRID_NY - 1
        for x, y in zip(fx, fy)
    )


# ---------------------------------------------------------------------------
# Run selection (hourly cycles, per-cycle horizon)
# ---------------------------------------------------------------------------


def hrrr_run_horizon_hours(init_hour: int) -> int:
    """Forecast horizon for the cycle starting at ``init_hour`` UTC."""
    if init_hour in HRRR_EXTENDED_CYCLES:
        return HRRR_HORIZON_EXTENDED_H
    return HRRR_HORIZON_SHORT_H


def _last_needed_fhour(init_time: datetime, need_until: datetime) -> int:
    """Last forecast hour the run must have published to cover ``need_until``."""
    delta_h = (need_until - init_time).total_seconds() / 3600.0
    return max(0, math.ceil(delta_h))


def find_latest_hrrr_run(
    target_time: datetime,
    session: requests.Session | None = None,
    as_of_time: datetime | None = None,
    cover_until: datetime | None = None,
    extended_cycles_only: bool = False,
) -> tuple[str, int] | None:
    """Find the latest HRRR run covering the flight window.

    Thin wrapper around :func:`find_latest_hrrr_run_with_response` for
    callers that don't need the HEAD response.
    """
    found = find_latest_hrrr_run_with_response(
        target_time, session, as_of_time, cover_until, extended_cycles_only,
    )
    return (found[0], found[1]) if found is not None else None


def find_latest_hrrr_run_with_response(
    target_time: datetime,
    session: requests.Session | None = None,
    as_of_time: datetime | None = None,
    cover_until: datetime | None = None,
    extended_cycles_only: bool = False,
) -> tuple[str, int, requests.Response] | None:
    """Find the latest HRRR run + return the matching probe HEAD response.

    Walks hourly cycles in reverse chronological order, skipping cycles that
    are not yet expected published or whose horizon (48 h extended /
    18 h otherwise) doesn't reach ``cover_until``. The publication probe
    HEADs the .idx of the LAST forecast hour the flight needs — HRRR
    publishes forecast hours progressively over the delivery window, so
    probing f00 could select a run whose later hours aren't up yet.

    Args:
        target_time: Flight departure time.
        session: Optional requests session.
        as_of_time: If set, only consider runs initialized before this time
            (historical "as-of" briefings). Uses ``now`` if None.
        cover_until: Latest time the run must cover (typically departure +
            flight duration). Defaults to ``target_time``.
        extended_cycles_only: Only consider 00/06/12/18z cycles. Used by the
            freshness readiness check so staleness tracks the 6-hourly
            extended cadence rather than flagging every hourly cycle (which
            would auto-refresh short-lead US flights every hour).

    Returns:
        ``(init_date_YYYYMMDD, init_hour, head_response)`` or ``None``.
    """
    sess = session or requests.Session()
    reference_time = as_of_time or datetime.now(timezone.utc)
    need_until = cover_until or target_time

    start = reference_time.replace(minute=0, second=0, microsecond=0)
    # 2 days of hourly cycles is enough: even the longest-horizon flight a
    # publishable run can cover was initialized < 48 h ago.
    for hours_back in range(48):
        init_time = start - timedelta(hours=hours_back)
        cycle = init_time.hour
        if extended_cycles_only and cycle not in HRRR_EXTENDED_CYCLES:
            continue
        hours_since_init = (reference_time - init_time).total_seconds() / 3600
        if hours_since_init < HRRR_PUBLISH_DELAY_HOURS:
            continue

        horizon_h = hrrr_run_horizon_hours(cycle)
        if init_time + timedelta(hours=horizon_h) < need_until:
            continue

        probe_fhour = min(_last_needed_fhour(init_time, need_until), horizon_h)
        date_str = init_time.strftime("%Y%m%d")
        try:
            resp = sess.head(
                hrrr_idx_url(date_str, cycle, probe_fhour), timeout=10,
            )
            if resp.status_code == 200:
                logger.info(
                    "Found HRRR run: %s %02dz (horizon %dh, probed f%02d)",
                    date_str, cycle, horizon_h, probe_fhour,
                )
                return date_str, cycle, resp
        except requests.RequestException:
            continue

    return None


def hrrr_window_out_of_range(
    target_time: datetime,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
) -> bool:
    """True when no publishable HRRR run has enough horizon for the flight.

    Deterministic (no network): mirrors the cycle/delay walk of
    :func:`find_latest_hrrr_run_with_response`. Distinguishes the expected
    "flight beyond the 48 h horizon" skip from a genuine probe failure.
    """
    reference_time = as_of_time or datetime.now(timezone.utc)
    need_until = target_time + timedelta(hours=flight_duration_hours)
    start = reference_time.replace(minute=0, second=0, microsecond=0)
    for hours_back in range(48):
        init_time = start - timedelta(hours=hours_back)
        hours_since_init = (reference_time - init_time).total_seconds() / 3600
        if hours_since_init < HRRR_PUBLISH_DELAY_HOURS:
            continue
        horizon_h = hrrr_run_horizon_hours(init_time.hour)
        if init_time + timedelta(hours=horizon_h) >= need_until:
            return False
    return True


def compute_hrrr_flight_window_hours(
    init_date: str,
    init_hour: int,
    departure_time: datetime,
    flight_duration_hours: float,
) -> list[int]:
    """Compute HRRR forecast hours covering a flight window.

    Same shape as the GFS helper, but HRRR output is hourly across the whole
    horizon so no temporal-grid snapping is needed — just the covering set
    of integer forecast hours, clamped to the cycle's horizon.
    """
    init_dt = datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc,
    )
    horizon_h = hrrr_run_horizon_hours(init_hour)
    dep_dt = departure_time

    extra = 1 if dep_dt.minute > 0 else 0
    n_hours = max(1, math.ceil(flight_duration_hours) + 1 + extra)
    fhours: set[int] = set()
    for h in range(n_hours):
        utc = dep_dt + timedelta(hours=h)
        delta = (utc - init_dt).total_seconds() / 3600
        delta = max(0.0, min(delta, float(horizon_h)))
        fhours.add(round(delta))

    # Include the floor hour so non-round departure times get coverage.
    if dep_dt.minute > 0:
        floor_utc = dep_dt.replace(minute=0, second=0, microsecond=0)
        floor_delta = (floor_utc - init_dt).total_seconds() / 3600
        if floor_delta >= 0:
            fhours.add(min(int(floor_delta), horizon_h))

    return sorted(fhours)


# ---------------------------------------------------------------------------
# Idx planning + byte-range fetch
# ---------------------------------------------------------------------------


def fetch_hrrr_idx(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    session: requests.Session | None = None,
) -> str:
    """Download an HRRR .idx file (same format as GFS)."""
    sess = session or requests.Session()
    url = hrrr_idx_url(init_date, init_hour, forecast_hour)
    logger.debug("Fetching HRRR .idx: %s", url)
    resp = sess.get(url, timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def plan_hrrr_sounding_byte_ranges(idx_text: str) -> list[ByteRange]:
    """Byte ranges for the full-sounding replacement set (~190 MB/fhour).

    All :data:`HRRR_SOUNDING_VARIABLES` on every integer pressure level at or
    below (altitude-wise) :data:`HRRR_SOUNDING_MIN_LEVEL_HPA`.
    """
    return plan_byte_ranges(
        idx_text,
        variables=HRRR_SOUNDING_VARIABLES,
        min_level_hpa=HRRR_SOUNDING_MIN_LEVEL_HPA,
    )


def plan_hrrr_cloud_diag_byte_ranges(idx_text: str) -> list[CloudDiagByteRange]:
    """Byte ranges for the cloud-diagnostics + CAPE/CIN set (~10 MB/fhour)."""
    return plan_cloud_diag_byte_ranges(idx_text, pairs=_HRRR_CLOUD_DIAG_PAIRS)


def fetch_hrrr_byte_ranges(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    ranges: list[ByteRange],
    session: requests.Session | None = None,
) -> bytes:
    """Download sounding byte ranges from an HRRR wrfprs file in parallel.

    Same reassembly semantics as the GFS fetcher: messages are concatenated
    in byte-offset order into a single valid GRIB2 stream.
    """
    if not ranges:
        return b""
    sess = session or requests.Session()
    url = hrrr_grib2_url(init_date, init_hour, forecast_hour)

    results: list[tuple[int, bytes | None]] = []
    with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_one_byte_range, url, br, sess): br
            for br in ranges
        }
        for future in as_completed(futures):
            results.append(future.result())

    out = bytearray()
    for _offset, data in sorted(results, key=lambda r: r[0]):
        if data is not None:
            out.extend(data)
    return bytes(out)


def fetch_hrrr_cloud_diag_ranges(
    init_date: str,
    init_hour: int,
    forecast_hour: int,
    ranges: list[CloudDiagByteRange],
    session: requests.Session | None = None,
) -> bytes:
    """Download cloud diagnostic byte ranges from an HRRR wrfprs file."""
    if not ranges:
        return b""
    sess = session or requests.Session()
    url = hrrr_grib2_url(init_date, init_hour, forecast_hour)

    results: list[tuple[int, bytes | None]] = []
    with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_one_cloud_diag_range, url, br, sess): br
            for br in ranges
        }
        for future in as_completed(futures):
            results.append(future.result())

    out = bytearray()
    for _offset, data in sorted(results, key=lambda r: r[0]):
        if data is not None:
            out.extend(data)
    return bytes(out)
