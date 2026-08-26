"""Grid geometry shared by every observed source.

Two very different projections feed the observed layers — OPERA's Lambert
azimuthal equal-area composite and MTG's geostationary full disc — but the
sampler must not care which it is looking at.  :class:`GridSpec` is the whole
of what it needs: a proj4 string plus the affine mapping from (row, col) to
projected metres.  Everything else (which HDF5 attribute held the corner,
whether the netCDF stored scan-angle radians) is the reader's problem.

Row order is carried in the *sign* of ``dy`` rather than a flag.  ODIM writes
its composites north-first (``dy < 0``); MTG's netCDF stores ``y`` increasing
northward (``dy > 0``).  Signed steps make both a plain affine, so no consumer
needs a branch.

**No GDAL, no rasterio.**  ``pyproj`` plus numpy covers the whole job, and the
droplet does not gain a C toolchain for it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

KM_PER_NM = 1.852
EARTH_RADIUS_KM = 6371.0088


@lru_cache(maxsize=16)
def _transformers(proj4: str):
    """Cached (wgs84→projected, projected→wgs84) transformer pair.

    Transformer construction dominates the cost of a small sample, and the
    sampler builds one per station sub-box; caching on the proj4 string keeps
    that to a single construction per grid per process.
    """
    from pyproj import CRS, Transformer

    crs = CRS.from_proj4(proj4)
    wgs84 = CRS.from_epsg(4326)
    fwd = Transformer.from_crs(wgs84, crs, always_xy=True)
    inv = Transformer.from_crs(crs, wgs84, always_xy=True)
    return fwd, inv


@dataclass(frozen=True)
class GridSpec:
    """Affine, projected grid.

    Attributes:
        proj4: Projection definition consumed by pyproj.
        nx: Column count of the *full* grid.
        ny: Row count of the *full* grid.
        x0: Projected x (metres) of the centre of column 0.
        y0: Projected y (metres) of the centre of row 0.
        dx: Signed projected x step per column, in metres.
        dy: Signed projected y step per row, in metres.  Negative when the
            grid is stored north-first, as ODIM composites are.
    """

    proj4: str
    nx: int
    ny: int
    x0: float
    y0: float
    dx: float
    dy: float

    @property
    def pixel_km(self) -> float:
        """Nominal pixel size in km — the larger of the two axes."""
        return max(abs(self.dx), abs(self.dy)) / 1000.0

    def lonlat_to_xy(self, lon, lat):
        fwd, _ = _transformers(self.proj4)
        return fwd.transform(lon, lat)

    def xy_to_lonlat(self, x, y):
        _, inv = _transformers(self.proj4)
        return inv.transform(x, y)

    def lonlat_to_colrow(self, lon: float, lat: float) -> tuple[float, float]:
        """Fractional (col, row) of a geographic point. May fall outside the grid."""
        x, y = self.lonlat_to_xy(lon, lat)
        return (x - self.x0) / self.dx, (y - self.y0) / self.dy

    def colrow_to_xy(self, cols, rows):
        return self.x0 + np.asarray(cols) * self.dx, self.y0 + np.asarray(rows) * self.dy

    def colrow_to_lonlat(self, cols, rows):
        """Vectorised (col, row) → (lon, lat).  Accepts arrays of any shape."""
        x, y = self.colrow_to_xy(cols, rows)
        return self.xy_to_lonlat(x, y)

    def mesh_lonlat(self, row0: int, row1: int, col0: int, col1: int):
        """(lon, lat) arrays for the half-open pixel block ``[row0:row1, col0:col1]``."""
        cols = np.arange(col0, col1)
        rows = np.arange(row0, row1)
        cc, rr = np.meshgrid(cols, rows)
        return self.colrow_to_lonlat(cc, rr)


@dataclass(frozen=True)
class GridWindow:
    """Half-open pixel block of a grid: ``[row0:row1, col0:col1]``.

    The reader slices exactly this out of the file, so the window is what
    bounds I/O.  ``full_width`` records that the caller widened the block to
    every column on purpose — for CTTH the netCDF chunks are ``[23, 5568]``
    full-width strips, so narrowing the column range buys nothing and costs a
    partial-chunk read.
    """

    row0: int
    row1: int
    col0: int
    col1: int
    full_width: bool = False

    @property
    def shape(self) -> tuple[int, int]:
        return self.row1 - self.row0, self.col1 - self.col0

    @property
    def size(self) -> int:
        rows, cols = self.shape
        return max(0, rows) * max(0, cols)

    def is_empty(self) -> bool:
        return self.row1 <= self.row0 or self.col1 <= self.col0

    def contains_full(self, grid: GridSpec) -> bool:
        return (
            self.row0 <= 0
            and self.col0 <= 0
            and self.row1 >= grid.ny
            and self.col1 >= grid.nx
        )


def compute_window(
    grid: GridSpec,
    lats,
    lons,
    *,
    radius_km: float,
    pad_km: float = 0.0,
    full_width: bool = False,
) -> GridWindow:
    """Smallest pixel block covering every station's sample radius.

    ``pad_km`` widens the block beyond ``radius_km``.  It is not decoration:
    for a parallax-corrected product the pixel whose cloud-top belongs over a
    station sits up to ~65 km away in the imagery, so a block sized to the
    corridor alone would simply not contain the pixels the sampler is about to
    look for.  See :mod:`weatherbrief.observed.sampler`.

    Returns an empty window when no station projects onto the grid at all.
    """
    lats = np.atleast_1d(np.asarray(lats, dtype=float))
    lons = np.atleast_1d(np.asarray(lons, dtype=float))
    if lats.size == 0:
        return GridWindow(0, 0, 0, 0, full_width=full_width)

    reach_m = (radius_km + pad_km) * 1000.0
    x, y = grid.lonlat_to_xy(lons, lats)
    x = np.atleast_1d(np.asarray(x, dtype=float))
    y = np.atleast_1d(np.asarray(y, dtype=float))
    finite = np.isfinite(x) & np.isfinite(y)
    if not finite.any():
        return GridWindow(0, 0, 0, 0, full_width=full_width)
    x, y = x[finite], y[finite]

    cols = (np.concatenate([x - reach_m, x + reach_m]) - grid.x0) / grid.dx
    rows = (np.concatenate([y - reach_m, y + reach_m]) - grid.y0) / grid.dy

    col0 = max(0, int(math.floor(cols.min())))
    col1 = min(grid.nx, int(math.ceil(cols.max())) + 1)
    row0 = max(0, int(math.floor(rows.min())))
    row1 = min(grid.ny, int(math.ceil(rows.max())) + 1)

    if full_width:
        col0, col1 = 0, grid.nx
    if row1 <= row0 or col1 <= col0:
        return GridWindow(0, 0, 0, 0, full_width=full_width)
    return GridWindow(row0, row1, col0, col1, full_width=full_width)


def haversine_km(lat0: float, lon0: float, lats, lons):
    """Great-circle distance in km from one point to an array of points."""
    lat0r = math.radians(lat0)
    lon0r = math.radians(lon0)
    latr = np.radians(np.asarray(lats, dtype=float))
    lonr = np.radians(np.asarray(lons, dtype=float))
    dlat = latr - lat0r
    dlon = lonr - lon0r
    a = np.sin(dlat / 2.0) ** 2 + math.cos(lat0r) * np.cos(latr) * np.sin(dlon / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def nm_to_km(nm: float) -> float:
    return nm * KM_PER_NM


def km_to_nm(km: float) -> float:
    return km / KM_PER_NM
