"""Synthetic frames for the observed-cells tests.

Two levels: :func:`grid_frame` builds an in-memory ``GridFrame`` for the pure
analysis functions, and :func:`write_dbzh` writes a real ODIM_H5 composite into
a ``FrameStore`` for the runner, so the loop is exercised through the same
reader the archive uses.  Scenes are Gaussian blobs with a fixed random
texture that moves with them — a smooth blob alone gives the correlation
nothing to lock onto, real echoes are textured.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np

from weatherbrief.observed import opera
from weatherbrief.observed.frames import SOURCE_OPERA_DBZH, FrameStore, GridFrame
from weatherbrief.observed.grid import GridSpec, GridWindow

PROJ4 = (
    "+proj=laea +lat_0=55.0 +lon_0=10.0 +x_0=1950000.0 +y_0=-2100000.0 "
    "+units=m +ellps=WGS84 +no_defs"
)
SCALE = 2000.0
# Upper-left outer corner of the synthetic grid, in projected metres (northern
# France, so the fixtures sit inside the real OPERA domain and the MTG disc).
UL_X = 1_700_000.0
UL_Y = 1_100_000.0


def grid_spec(size: int) -> GridSpec:
    return GridSpec(PROJ4, size, size, UL_X + SCALE / 2, UL_Y - SCALE / 2, SCALE, -SCALE)


def scene(size: int, blobs, shift=(0.0, 0.0), seed: int = 7) -> np.ndarray:
    """dBZ field: ``blobs`` = [(row, col, peak_dbz, sigma_px)], moved by ``shift``."""
    rows, cols = np.mgrid[0:size, 0:size].astype(float)
    field = np.zeros((size, size))
    for r, c, peak, sigma in blobs:
        field += peak * np.exp(-((rows - r - shift[0]) ** 2 + (cols - c - shift[1]) ** 2) / (2 * sigma**2))
    rng = np.random.default_rng(seed)
    pad = 64
    texture = rng.normal(0.0, 3.0, (size + 2 * pad, size + 2 * pad))
    sr, sc = int(round(shift[0])), int(round(shift[1]))
    texture = texture[pad - sr: pad - sr + size, pad - sc: pad - sc + size]
    return field + texture * (field > 15)


def grid_frame(values: np.ndarray, valid_time: datetime, nodata: np.ndarray | None = None,
               floor_dbz: float = 5.0) -> GridFrame:
    size = values.shape[0]
    nodata = np.zeros(values.shape, dtype=bool) if nodata is None else nodata
    undetect = (values < floor_dbz) & ~nodata
    vals = np.where(nodata | undetect, np.nan, values).astype(np.float32)
    return GridFrame(SOURCE_OPERA_DBZH, "DBZH", "dBZ", valid_time, 10.0, grid_spec(size),
                     GridWindow(0, size, 0, size), vals, nodata, undetect)


def _corners(size: int) -> dict[str, tuple[float, float]]:
    from pyproj import CRS, Transformer

    inv = Transformer.from_crs(CRS.from_proj4(PROJ4), CRS.from_epsg(4326), always_xy=True)
    span = size * SCALE
    xy = {"UL": (UL_X, UL_Y), "UR": (UL_X + span, UL_Y),
          "LL": (UL_X, UL_Y - span), "LR": (UL_X + span, UL_Y - span)}
    return {k: inv.transform(x, y) for k, (x, y) in xy.items()}


def write_dbzh(store: FrameStore, valid_time: datetime, values: np.ndarray,
               nodata: np.ndarray | None = None, floor_dbz: float = 5.0) -> None:
    """Write an ODIM_H5 DBZH composite + sidecar, as the collector would."""
    import h5py

    size = values.shape[0]
    gain, offset = 0.5, -32.0
    raw = np.clip(np.round((values - offset) / gain), 1, 254).astype(np.uint8)
    raw[values < floor_dbz] = 0  # undetect
    if nodata is not None:
        raw[nodata] = 255
    path = store.payload_path(SOURCE_OPERA_DBZH, valid_time)
    path.parent.mkdir(parents=True, exist_ok=True)
    end = valid_time.strftime("%H%M%S").encode()
    day = valid_time.strftime("%Y%m%d").encode()
    with h5py.File(str(path), "w") as handle:
        what = handle.create_group("what")
        what.attrs["object"] = np.bytes_(b"COMP")
        what.attrs["date"] = np.bytes_(day)
        what.attrs["time"] = np.bytes_(end)
        what.attrs["source"] = np.bytes_(b"ORG:247")
        where = handle.create_group("where")
        where.attrs["projdef"] = np.bytes_(PROJ4.encode())
        where.attrs["xsize"] = np.int64(size)
        where.attrs["ysize"] = np.int64(size)
        where.attrs["xscale"] = np.float64(SCALE)
        where.attrs["yscale"] = np.float64(SCALE)
        for corner, (lon, lat) in _corners(size).items():
            where.attrs[f"{corner}_lon"] = np.float64(lon)
            where.attrs[f"{corner}_lat"] = np.float64(lat)
        how = handle.create_group("how")
        how.attrs["license"] = np.bytes_(b"synthetic test data")
        dataset = handle.create_group("dataset1")
        dwhat = dataset.create_group("what")
        dwhat.attrs["product"] = np.bytes_(b"COMP")
        dwhat.attrs["enddate"] = np.bytes_(day)
        dwhat.attrs["endtime"] = np.bytes_(end)
        data = dataset.create_group("data1")
        dgroup = data.create_group("what")
        dgroup.attrs["quantity"] = np.bytes_(b"DBZH")
        dgroup.attrs["gain"] = np.float64(gain)
        dgroup.attrs["offset"] = np.float64(offset)
        dgroup.attrs["nodata"] = np.float64(255.0)
        dgroup.attrs["undetect"] = np.float64(0.0)
        data.create_dataset("data", data=raw, compression="gzip")
    store.write_sidecar(SOURCE_OPERA_DBZH, valid_time, opera.read_metadata(path, "DBZH"))
