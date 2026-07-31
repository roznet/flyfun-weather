"""HRRR fetch support (#457): S3 URLs, run selection, domain gate, window hours.

HRRR (High-Resolution Rapid Refresh) data is publicly available on Amazon S3 at
a flat ``conus/`` layout with 2-digit forecast hours — unlike GFS, which nests
under ``{HH}/atmos/`` and uses 3-digit hours:

    hrrr.{YYYYMMDD}/conus/hrrr.t{HH}z.wrfprsf{FF}.grib2

Companion .idx files are at the same URL with .idx appended; they share the GFS
line format, so the parametrized parsers in gfs_idx.py handle them.

Key operational differences from GFS that shape this module:
- Cycles run EVERY hour; only 00/06/12/18z extend to 48h, the rest reach 18h.
- Files appear progressively (~1h publish delay), so run selection must probe
  the LAST-NEEDED forecast hour's .idx — probing f000 would accept runs whose
  later hours are not published yet.

Live-feed facts (URL layout, grid constants) verified against the real NOAA
bucket on 2026-07-31.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

import requests

from weatherbrief.fetch.grib.gfs_idx import (
    HRRR_DIAG_VARIABLES,
    HRRR_SOUNDING_VARIABLES,
)

__all__ = [
    "HRRR_DIAG_VARIABLES",
    "HRRR_SOUNDING_VARIABLES",
    "HRRR_EXTENDED_CYCLES",
    "HRRR_GRID",
    "HRRR_HORIZON_LONG_H",
    "HRRR_HORIZON_SHORT_H",
    "HRRR_PUBLISH_DELAY_HOURS",
    "HRRR_S3_BASE",
    "HrrrGrid",
    "find_latest_hrrr_run",
    "hrrr_grib2_url",
    "hrrr_idx_url",
    "hrrr_projection",
    "hrrr_window_hours",
    "route_in_hrrr_domain",
]

logger = logging.getLogger(__name__)

HRRR_S3_BASE = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
HRRR_PUBLISH_DELAY_HOURS = 1.0
HRRR_EXTENDED_CYCLES = frozenset({0, 6, 12, 18})  # 48h; all other hourly cycles 18h
HRRR_HORIZON_LONG_H = 48
HRRR_HORIZON_SHORT_H = 18


class HrrrGrid(NamedTuple):
    """HRRR CONUS Lambert grid definition (from a real wrfprs message)."""

    nx: int
    ny: int
    dx: float
    dy: float
    lat0: float
    lon0: float  # first grid point (SW corner)
    lad: float
    lov: float
    latin1: float
    latin2: float


HRRR_GRID = HrrrGrid(
    nx=1799, ny=1059, dx=3000.0, dy=3000.0,
    lat0=21.138123, lon0=237.280472,
    lad=38.5, lov=262.5, latin1=38.5, latin2=38.5,
)


def hrrr_projection() -> "pyproj.Proj":
    """The HRRR Lambert conformal projection (spherical earth, 6371229 m)."""
    import pyproj
    return pyproj.Proj(
        proj="lcc", lat_0=HRRR_GRID.lad, lon_0=HRRR_GRID.lov,
        lat_1=HRRR_GRID.latin1, lat_2=HRRR_GRID.latin2,
        a=6371229.0, b=6371229.0,
    )


def hrrr_grib2_url(init_date: str, init_hour: int, forecast_hour: int) -> str:
    """Build the S3 URL for an HRRR GRIB2 file.

    Args:
        init_date: YYYYMMDD format.
        init_hour: 0–23 (HRRR cycles hourly).
        forecast_hour: Forecast hour (0–48).
    """
    return (
        f"{HRRR_S3_BASE}/hrrr.{init_date}/conus/"
        f"hrrr.t{init_hour:02d}z.wrfprsf{forecast_hour:02d}.grib2"
    )


def hrrr_idx_url(init_date: str, init_hour: int, forecast_hour: int) -> str:
    """Build the S3 URL for an HRRR .idx file."""
    return hrrr_grib2_url(init_date, init_hour, forecast_hour) + ".idx"


def find_latest_hrrr_run(
    target_time: datetime,
    cover_until: datetime | None = None,
    as_of_time: datetime | None = None,
    session: requests.Session | None = None,
) -> tuple[str, int] | None:
    """Find the freshest HRRR run covering a forecast window.

    Tries hourly cycles in reverse chronological order. A cycle is eligible
    when (a) it is past the publish delay, (b) its horizon (48h for
    00/06/12/18z, 18h otherwise) reaches ``cover_until``, and (c) the
    last-needed fhour's .idx answers a HEAD with 200. Probing the last-needed
    hour — not f000 — is what makes this safe against HRRR's progressive
    publication: f000 can exist hours before the hours we actually need.

    Args:
        target_time: The forecast target time (start of the window).
        cover_until: End of the window that must be covered; defaults to
            ``target_time``.
        as_of_time: If set, only consider runs initialized before this time
            (for historical "as-of" briefings). Uses ``now`` if None.
        session: Optional requests session.

    Returns:
        ``(init_date_YYYYMMDD, init_hour)`` or ``None``.
    """
    sess = session or requests.Session()
    reference_time = as_of_time or datetime.now(timezone.utc)
    need_until = cover_until or target_time
    for days_back in range(2):
        check_date = reference_time - timedelta(days=days_back)
        for cycle in range(23, -1, -1):           # hourly, freshest first
            init_time = check_date.replace(hour=cycle, minute=0, second=0, microsecond=0)
            if init_time > reference_time:
                continue
            if (reference_time - init_time).total_seconds() / 3600 < HRRR_PUBLISH_DELAY_HOURS:
                continue
            horizon = HRRR_HORIZON_LONG_H if cycle in HRRR_EXTENDED_CYCLES else HRRR_HORIZON_SHORT_H
            if init_time + timedelta(hours=horizon) < need_until:
                continue
            # Probe the LAST-NEEDED fhour's idx, not f000 (progressive publication).
            last_needed = min(
                math.ceil((need_until - init_time).total_seconds() / 3600), horizon,
            )
            try:
                resp = sess.head(
                    hrrr_idx_url(check_date.strftime("%Y%m%d"), cycle, last_needed),
                    timeout=10,
                )
                if resp.status_code == 200:
                    logger.info(
                        "Found HRRR run: %s %02dz", check_date.strftime("%Y%m%d"), cycle,
                    )
                    return check_date.strftime("%Y%m%d"), cycle
            except requests.RequestException:
                continue
    return None


def _grid_xy_axes() -> tuple["np.ndarray", "np.ndarray"]:
    """1-D projected x/y axes (m) of the HRRR grid, from the verified first point."""
    import numpy as np
    proj = hrrr_projection()
    x0, y0 = proj(HRRR_GRID.lon0, HRRR_GRID.lat0)
    return (
        x0 + np.arange(HRRR_GRID.nx) * HRRR_GRID.dx,
        y0 + np.arange(HRRR_GRID.ny) * HRRR_GRID.dy,
    )


def route_in_hrrr_domain(route_points: list) -> bool:
    """All-or-nothing: every route point projects inside the grid bounds.

    The HRRR grid is a Lambert conformal rectangle whose edges do NOT follow
    lat/lon lines, so a lat/lon bounding box would misclassify border points
    (e.g. Key West is inside the grid but south of the CONUS mainland box).
    Projecting each point onto the grid axes and reusing the decode-side
    fractional-index bounds check gives the exact answer.
    """
    from weatherbrief.fetch.grib.decode import _frac_grid_indices
    proj = hrrr_projection()
    x_axis, y_axis = _grid_xy_axes()
    for rp in route_points:
        x, y = proj(rp.lon, rp.lat)
        _, x_ok = _frac_grid_indices(x_axis, [x])
        _, y_ok = _frac_grid_indices(y_axis, [y])
        if not (bool(x_ok[0]) and bool(y_ok[0])):
            return False
    return True


def _snap_to_hrrr_grid(fhour: float) -> int:
    """Snap a fractional forecast hour to the HRRR temporal grid.

    HRRR wrfprs is 1-hourly through f48, so snapping is plain rounding
    clamped to the 48h horizon.
    """
    return min(round(fhour), HRRR_HORIZON_LONG_H)


def hrrr_window_hours(
    init_date: str,
    init_hour: int,
    departure_time: datetime,
    flight_duration_hours: float,
) -> list[int]:
    """Compute HRRR forecast hours covering a flight window.

    Same shape as grib_fetch.compute_flight_window_hours (GFS) but snapped to
    HRRR's 1-hourly grid through f48 (snap ``round``, floor-hour inclusion).

    Args:
        init_date: YYYYMMDD of model init.
        init_hour: 0–23 (HRRR cycles hourly).
        departure_time: Aware UTC datetime of flight departure.
        flight_duration_hours: Flight duration in hours.

    Returns:
        Sorted deduplicated list of HRRR forecast hours.
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
        fhours.add(_snap_to_hrrr_grid(delta))

    # Include the floor hour so non-round departure times get coverage
    if dep_dt.minute > 0:
        floor_utc = dep_dt.replace(minute=0, second=0, microsecond=0)
        floor_delta = (floor_utc - init_dt).total_seconds() / 3600
        if floor_delta >= 0:
            fhours.add(_snap_to_hrrr_grid(floor_delta))

    return sorted(fhours)
