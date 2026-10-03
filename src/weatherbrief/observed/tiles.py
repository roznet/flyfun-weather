"""Europe-wide map tiles for the gridded radar sources (#652).

The corridor overlay (``imagery.render_overlay``) answers "what is on my route";
it stops at the corridor box and re-projects the frame on every request.  Tiles
draw the same paint over the whole radar domain, so the map can be panned, and
they split the work in two:

* **Once per frame** (the collector, right after the frame lands): project the
  frame into a Web Mercator *value canvas* at ``CANVAS_ZOOM``, one byte per
  pixel, and store it beside the frame as ``{stamp}.canvas.npz``.  This is the
  expensive part (a pyproj transform per pixel); the source-pixel lookup for a
  grid is computed once per process and reused for every later frame.
* **Per tile** (the API): slice the canvas, smooth it (``imagery.sample_smooth``
  above the canvas zoom, a max-pool below it), and paint it with the same
  ``imagery.paint_smooth`` the corridor overlay uses.

Everything is keyed by the frame stamp, so a tile URL never changes meaning and
can be cached for as long as the frame exists.  That is also what makes a loop
(#653) a URL change rather than a new pipeline: retention already bounds how
many canvases exist, and `purge` removes a canvas with its frame.

Canvas encoding, per source (one byte per pixel):

    0          undetect — looked, saw nothing
    255        nodata   — the radar does not see here (or outside the grid)
    1..254     a detection, linear (dBZ) or log10 (mm/h) — see `_CODECS`

Measured on the MacBook against a real OPERA frame (2026-10-03): canvas
3579 x 4430 px, ~0.7 MB compressed on disk; tiles ~10 ms and 10-20 KB each.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from .frames import (
    SOURCE_OPERA_DBZH,
    SOURCE_OPERA_RATE,
    SOURCE_SPECS,
    FrameStore,
    frame_stamp,
)
from .grid import GridSpec
from .imagery import (
    NODATA_RGBA,
    encode_png,
    inverse_mercator_y,
    mercator_y,
    paint_smooth,
    sample_smooth,
)

logger = logging.getLogger(__name__)

#: Sources drawn as tiles.  Cloud tops stay on the corridor overlay: a CTTH
#: granule is ~54 MB and its read peaks ~1.1 GB RSS, which is not worth paying
#: every 10 minutes for a layer the satellite IR underlay mostly replaces.
TILE_SOURCES = (SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE)

TILE_SIZE = 256
#: Zoom the canvas is stored at.  z6 is ~2.4 km per pixel at the equator,
#: ~1.7 km at 45°N and ~1.4 km at 55°N — at or finer than OPERA's 2 km grid
#: everywhere it covers, so the canvas loses nothing a tile could show.
CANVAS_ZOOM = 6
#: Tile zooms served.  Below MIN the radar is a smear over a continent; above
#: MAX the map scales the z10 tile, which is already ~10 canvas pixels per
#: source pixel of interpolation.
MIN_TILE_ZOOM = 3
MAX_TILE_ZOOM = 10

#: Bumped when the canvas encoding changes; a stale canvas is rebuilt.
CANVAS_VERSION = 1

_UNDETECT = 0
_NODATA = 255


@dataclass(frozen=True)
class _Codec:
    """Byte encoding of one source's values (codes 1..254)."""

    lowest: float  # value of code 1 (log10 for a log codec)
    step: float
    log: bool

    def encode(self, values: np.ndarray) -> np.ndarray:
        """Codes rounding UP: a decoded value is never below the measured one.

        Rounding to nearest would decode 0.5 mm/h — the light floor — as
        0.47, one class down.  Rounding up costs at most one step the other
        way, which is the safe direction (meteorology-decisions §33).
        """
        with np.errstate(divide="ignore", invalid="ignore"):
            x = np.log10(np.maximum(values, 1e-9)) if self.log else values
            codes = np.ceil((x - self.lowest) / self.step - 1e-6) + 1
        return np.clip(np.nan_to_num(codes, nan=1), 1, 254).astype(np.uint8)

    def decode(self, codes: np.ndarray) -> np.ndarray:
        x = self.lowest + (codes.astype(np.float32) - 1) * self.step
        return np.power(10.0, x) if self.log else x


# 0.5 dBZ from -32 (ODIM's own DBZH convention), 0.025 decade from 0.001 mm/h.
# Both at or finer than the paint's own bins (`imagery._VALUE_BIN`), and aligned
# with them, so the encoding never moves a value across a class floor.
_CODECS = {
    SOURCE_OPERA_DBZH: _Codec(lowest=-32.0, step=0.5, log=False),
    SOURCE_OPERA_RATE: _Codec(lowest=-3.0, step=0.025, log=True),
}


def tiles_enabled() -> bool:
    """Collector-side switch.  On unless ``WB_OBSERVED_TILES=0``.

    The API builds a missing canvas on first request anyway, so turning this
    off moves the cost to the first viewer of each frame rather than losing
    the layer.
    """
    return os.environ.get("WB_OBSERVED_TILES", "1").strip().lower() not in ("0", "false", "no")


def canvas_path(store: FrameStore, source: str, valid_time: datetime) -> Path:
    return store.canvas_path(source, valid_time)


# --- Canvas geometry ------------------------------------------------------------


def _world_px(zoom: int) -> int:
    return TILE_SIZE * (2 ** zoom)


def lonlat_to_world_px(lon, lat, zoom: int) -> tuple[np.ndarray, np.ndarray]:
    """Global Web Mercator pixel coordinates (XYZ convention, y down)."""
    size = _world_px(zoom)
    x = (np.asarray(lon, dtype=float) + 180.0) / 360.0 * size
    y = (1.0 - mercator_y(lat) / np.pi) / 2.0 * size
    return x, y


def world_px_to_lonlat(x, y, zoom: int) -> tuple[np.ndarray, np.ndarray]:
    size = _world_px(zoom)
    lon = np.asarray(x, dtype=float) / size * 360.0 - 180.0
    lat = inverse_mercator_y(np.pi * (1.0 - 2.0 * np.asarray(y, dtype=float) / size))
    return lon, lat


@dataclass(frozen=True)
class _Lookup:
    """Nearest source pixel for every canvas pixel, for one grid."""

    x0: int  # canvas origin, world px at CANVAS_ZOOM
    y0: int
    #: Flat source index (row * nx + col) per canvas pixel, int32.  Pixels
    #: outside the grid point at ``nx * ny``, one past the end, where the
    #: gather appends a nodata sentinel — no masks or int64 temporaries.
    flat: np.ndarray


_LOOKUPS: dict[tuple, _Lookup] = {}
_LOOKUP_LOCK = threading.Lock()


def _grid_key(grid: GridSpec) -> tuple:
    return (grid.proj4, grid.nx, grid.ny, grid.x0, grid.y0, grid.dx, grid.dy)


def _lookup_for(grid: GridSpec) -> _Lookup:
    """Canvas extent and source-pixel lookup for ``grid``, cached per process.

    OPERA's grid does not change between frames, so this pays the pyproj
    transform of ~16 M points once.  Held as one int32 array (~63 MB).
    """
    key = _grid_key(grid)
    with _LOOKUP_LOCK:
        cached = _LOOKUPS.get(key)
        if cached is not None:
            return cached
        lookup = _build_lookup(grid)
        _LOOKUPS.clear()  # one grid at a time is all a deployment has
        _LOOKUPS[key] = lookup
        return lookup


def _build_lookup(grid: GridSpec) -> _Lookup:
    # Extent: the grid's perimeter in lon/lat, as world px at the canvas zoom.
    # A projected grid's edge is curved in Mercator, so the perimeter is
    # sampled, not just its corners.
    steps = 64
    cols = np.concatenate([
        np.linspace(0, grid.nx - 1, steps), np.full(steps, grid.nx - 1),
        np.linspace(0, grid.nx - 1, steps), np.zeros(steps),
    ])
    rows = np.concatenate([
        np.zeros(steps), np.linspace(0, grid.ny - 1, steps),
        np.full(steps, grid.ny - 1), np.linspace(0, grid.ny - 1, steps),
    ])
    lon, lat = grid.colrow_to_lonlat(cols, rows)
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    ok = np.isfinite(lon) & np.isfinite(lat)
    px, py = lonlat_to_world_px(lon[ok], lat[ok], CANVAS_ZOOM)
    x0, x1 = int(np.floor(px.min())), int(np.ceil(px.max()))
    y0, y1 = int(np.floor(py.min())), int(np.ceil(py.max()))
    width, height = x1 - x0, y1 - y0

    outside_index = grid.nx * grid.ny
    flat = np.full((height, width), outside_index, dtype=np.int32)
    centres_x = x0 + np.arange(width) + 0.5
    # Row chunks bound the float64 temporaries of the transform.
    chunk = 256
    for start in range(0, height, chunk):
        stop = min(height, start + chunk)
        gx, gy = np.meshgrid(centres_x, y0 + np.arange(start, stop) + 0.5)
        lon_c, lat_c = world_px_to_lonlat(gx, gy, CANVAS_ZOOM)
        x, y = grid.lonlat_to_xy(lon_c, lat_c)
        with np.errstate(invalid="ignore"):
            c = np.rint((np.asarray(x, dtype=float) - grid.x0) / grid.dx)
            r = np.rint((np.asarray(y, dtype=float) - grid.y0) / grid.dy)
        c = np.nan_to_num(c, nan=-1, posinf=-1, neginf=-1)
        r = np.nan_to_num(r, nan=-1, posinf=-1, neginf=-1)
        inside = (r >= 0) & (r < grid.ny) & (c >= 0) & (c < grid.nx)
        flat[start:stop] = np.where(inside, r * grid.nx + c, outside_index).astype(np.int32)
    return _Lookup(x0=x0, y0=y0, flat=flat)


# --- Canvas build / store ----------------------------------------------------------


@dataclass(frozen=True)
class Canvas:
    """One frame projected onto the Web Mercator canvas, byte-encoded."""

    source: str
    x0: int
    y0: int
    codes: np.ndarray  # uint8, see module docstring

    @property
    def shape(self) -> tuple[int, int]:
        return self.codes.shape


def build_canvas(source: str, path: Path) -> Canvas:
    """Project the frame at ``path`` onto the canvas.

    Streams the composite in row blocks (``opera.iter_row_blocks``) straight
    into a byte array, then gathers through the cached lookup: peak memory is
    one block plus the byte grid plus the canvas, not the decoded composite.
    """
    from . import opera

    if source not in TILE_SOURCES:
        raise ValueError(f"{source} is not drawn as tiles")
    spec = SOURCE_SPECS[source]
    codec = _CODECS[source]
    grid = opera.read_grid(path)
    lookup = _lookup_for(grid)

    # One extra cell at the end: the nodata sentinel `_Lookup.flat` points
    # outside pixels at.
    codes_src = np.full(grid.nx * grid.ny + 1, _NODATA, dtype=np.uint8)
    for row0, values, nodata, undetect in opera.iter_row_blocks(path, spec.quantity):
        block = np.full(values.shape, _UNDETECT, dtype=np.uint8)
        detected = ~nodata & ~undetect
        block[detected] = codec.encode(values[detected])
        block[nodata] = _NODATA
        codes_src[row0 * grid.nx : row0 * grid.nx + block.size] = block.ravel()
    codes = codes_src[lookup.flat]
    return Canvas(source=source, x0=lookup.x0, y0=lookup.y0, codes=codes)


def write_canvas(store: FrameStore, source: str, valid_time: datetime) -> Path:
    """Build and atomically store the canvas for one stored frame."""
    payload = store.payload_path(source, valid_time)
    canvas = build_canvas(source, payload)
    target = canvas_path(store, source, valid_time)
    # Straight to a temp file (ending in .npz, so numpy keeps the name), then
    # renamed: a reader never sees a half-written canvas, and the compressed
    # bytes are never buffered in memory alongside the canvas.
    tmp = target.with_name(f".tmp-{target.name}")
    try:
        np.savez_compressed(
            tmp,
            codes=canvas.codes,
            origin=np.array([canvas.x0, canvas.y0], dtype=np.int64),
            zoom=np.array(CANVAS_ZOOM),
            version=np.array(CANVAS_VERSION),
        )
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


# Canvases by (path, mtime): the newest one or two are hit by every tile of a
# map view.  ~11 MB each; a loop (#653) will want this larger.
_CANVAS_CACHE: "OrderedDict[tuple[str, int], Canvas]" = OrderedDict()
_CANVAS_CACHE_SIZE = 4
_CANVAS_LOCK = threading.Lock()
_BUILD_LOCK = threading.Lock()

#: How long a tile request waits for another request's canvas build before
#: giving up with `CanvasBusy`.  A build is ~0.5-2 s warm; the bound stops a
#: cold lookup (several seconds, ~180 MB) from pinning a threadpool full of
#: tile requests behind one lock.
BUILD_WAIT_SECONDS = 5.0
#: A frame whose canvas build failed is not retried for this long: a corrupt
#: payload stays corrupt, and every tile of the map would otherwise re-read it.
FAILED_BUILD_TTL_SECONDS = 600.0
_FAILED_BUILDS: dict[tuple[str, str], float] = {}


class CanvasBusy(RuntimeError):
    """Another request is building this canvas; ask again shortly."""


class CanvasUnavailable(RuntimeError):
    """The frame's canvas cannot be built (corrupt or unreadable frame)."""


def has_canvas(store: FrameStore, source: str, valid_time: datetime) -> bool:
    return canvas_path(store, source, valid_time).exists()


def load_canvas(store: FrameStore, source: str, valid_time: datetime) -> Canvas:
    """The canvas for a stored frame, building it if the collector did not.

    Raises ``FileNotFoundError`` when the frame itself is not in the store,
    ``CanvasBusy`` when another request's build did not finish within
    ``BUILD_WAIT_SECONDS``, and ``CanvasUnavailable`` when the build failed
    (remembered for ``FAILED_BUILD_TTL_SECONDS``).
    """
    if not store.has(source, valid_time):
        raise FileNotFoundError(f"no {source} frame at {frame_stamp(valid_time)}")
    path = canvas_path(store, source, valid_time)
    canvas = _read_canvas(source, path)
    if canvas is not None:
        return canvas
    key = (source, frame_stamp(valid_time))
    failed_at = _FAILED_BUILDS.get(key)
    if failed_at is not None and time.monotonic() - failed_at < FAILED_BUILD_TTL_SECONDS:
        raise CanvasUnavailable(f"canvas build for {key} failed recently")
    # Frames stored before tiles existed, or a collector run with
    # WB_OBSERVED_TILES=0.  One build at a time, and a bounded wait for it:
    # concurrent tile requests for the same new frame must neither all project
    # it nor all sit on the threadpool behind the one that does.
    if not _BUILD_LOCK.acquire(timeout=BUILD_WAIT_SECONDS):
        raise CanvasBusy(f"canvas for {key} is being built")
    try:
        canvas = _read_canvas(source, path)
        if canvas is None:
            try:
                write_canvas(store, source, valid_time)
            except Exception as exc:
                _FAILED_BUILDS[key] = time.monotonic()
                raise CanvasUnavailable(f"canvas build for {key} failed: {exc}") from exc
            canvas = _read_canvas(source, path)
    finally:
        _BUILD_LOCK.release()
    if canvas is None:
        _FAILED_BUILDS[key] = time.monotonic()
        raise CanvasUnavailable(f"canvas for {key} unreadable after build")
    return canvas


def _read_canvas(source: str, path: Path) -> Canvas | None:
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        return None
    key = (str(path), mtime)
    with _CANVAS_LOCK:
        cached = _CANVAS_CACHE.get(key)
        if cached is not None:
            _CANVAS_CACHE.move_to_end(key)
            return cached
    try:
        with np.load(path) as data:
            if int(data["version"]) != CANVAS_VERSION or int(data["zoom"]) != CANVAS_ZOOM:
                return None
            origin = data["origin"]
            canvas = Canvas(source=source, x0=int(origin[0]), y0=int(origin[1]), codes=data["codes"])
    except Exception:
        logger.warning("Unreadable observed canvas %s; rebuilding", path, exc_info=True)
        return None
    with _CANVAS_LOCK:
        _CANVAS_CACHE[key] = canvas
        while len(_CANVAS_CACHE) > _CANVAS_CACHE_SIZE:
            _CANVAS_CACHE.popitem(last=False)
    return canvas


# --- Tile rendering ------------------------------------------------------------------


def valid_tile(z: int, x: int, y: int) -> bool:
    if not (MIN_TILE_ZOOM <= z <= MAX_TILE_ZOOM):
        return False
    n = 2 ** z
    return 0 <= x < n and 0 <= y < n


def render_tile_rgba(canvas: Canvas, z: int, x: int, y: int) -> np.ndarray:
    """RGBA for XYZ tile (z, x, y) from a canvas."""
    if z >= CANVAS_ZOOM:
        values, coverage, nodata = _upsampled(canvas, z, x, y)
    else:
        values, coverage, nodata = _pooled(canvas, z, x, y)
    return paint_smooth(canvas.source, values, coverage, nodata)


def render_tile(canvas: Canvas, z: int, x: int, y: int) -> bytes:
    return encode_png(render_tile_rgba(canvas, z, x, y))


def _decoded(canvas: Canvas, codes: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    detected = (codes != _UNDETECT) & (codes != _NODATA)
    values = np.where(detected, _CODECS[canvas.source].decode(codes), np.nan)
    return values, detected, codes == _NODATA


def _upsampled(canvas: Canvas, z: int, x: int, y: int):
    """Tile at or above the canvas zoom: bilinear over canvas pixel centres."""
    scale = 2.0 ** (CANVAS_ZOOM - z)  # canvas px per tile px (<= 1)
    offsets = np.arange(TILE_SIZE) + 0.5
    # Canvas coordinates of tile pixel centres; integers are canvas centres.
    cx = (x * TILE_SIZE + offsets) * scale - canvas.x0 - 0.5
    cy = (y * TILE_SIZE + offsets) * scale - canvas.y0 - 0.5

    # Only the canvas block the tile touches, plus one pixel for the bilinear
    # neighbours — never the whole canvas.
    h, w = canvas.shape
    r_lo = int(np.clip(np.floor(cy.min()) - 1, 0, h))
    r_hi = int(np.clip(np.ceil(cy.max()) + 2, 0, h))
    c_lo = int(np.clip(np.floor(cx.min()) - 1, 0, w))
    c_hi = int(np.clip(np.ceil(cx.max()) + 2, 0, w))
    if r_hi <= r_lo or c_hi <= c_lo:
        return _empty_fields()
    values, detected, nodata = _decoded(canvas, canvas.codes[r_lo:r_hi, c_lo:c_hi])

    cols_mesh, rows_mesh = np.meshgrid(cx - c_lo, cy - r_lo)
    sampled, coverage = sample_smooth(values, detected, rows_mesh, cols_mesh)

    near_r = np.rint(rows_mesh).astype(int)
    near_c = np.rint(cols_mesh).astype(int)
    inside = (
        (near_r >= 0) & (near_r < nodata.shape[0]) & (near_c >= 0) & (near_c < nodata.shape[1])
    )
    hole = np.ones(sampled.shape, dtype=bool)
    hole[inside] = nodata[near_r[inside], near_c[inside]]
    return sampled, coverage, hole


def _pooled(canvas: Canvas, z: int, x: int, y: int):
    """Tile below the canvas zoom: each tile pixel is a block of canvas pixels.

    The block's MAXIMUM detection is drawn, not its mean — at a continental
    zoom a convective core is a few canvas pixels, and averaging would erase
    exactly the cell a pilot needs to see.
    """
    factor = 2 ** (CANVAS_ZOOM - z)
    span = TILE_SIZE * factor
    gx0 = x * span - canvas.x0
    gy0 = y * span - canvas.y0
    h, w = canvas.shape
    if not (gy0 < h and gy0 + span > 0 and gx0 < w and gx0 + span > 0):
        return _empty_fields()
    pooled = np.full((TILE_SIZE, TILE_SIZE), np.nan)
    coverage = np.zeros((TILE_SIZE, TILE_SIZE))
    hole = np.ones((TILE_SIZE, TILE_SIZE), dtype=bool)
    # In strips of output rows: a z3 tile covers 2048 x 2048 canvas pixels,
    # and decoding that in one go costs ~50 MB of temporaries per uncached
    # tile, several of which can run at once on a small droplet (measured:
    # 48 MB in one strip, see `_POOL_STRIP_PIXELS`).
    strip = max(1, _POOL_STRIP_PIXELS // (span * factor))
    for out_r0 in range(0, TILE_SIZE, strip):
        out_r1 = min(TILE_SIZE, out_r0 + strip)
        rows = out_r1 - out_r0
        block = np.full((rows * factor, span), _NODATA, dtype=np.uint8)
        src_r0 = gy0 + out_r0 * factor
        r0, r1 = max(0, src_r0), min(h, src_r0 + rows * factor)
        c0, c1 = max(0, gx0), min(w, gx0 + span)
        if r1 > r0 and c1 > c0:
            block[r0 - src_r0 : r1 - src_r0, c0 - gx0 : c1 - gx0] = canvas.codes[r0:r1, c0:c1]
        values, detected, nodata = _decoded(canvas, block)
        shape = (rows, factor, TILE_SIZE, factor)
        detected_share = detected.reshape(shape).mean(axis=(1, 3))
        with np.errstate(invalid="ignore"):
            peak = np.max(np.where(detected, values, -np.inf).reshape(shape), axis=(1, 3))
        any_hit = detected_share > 0
        pooled[out_r0:out_r1] = np.where(any_hit, peak, np.nan)
        # Any detection in the block draws (coverage 1); a block that is
        # mostly unseen and has nothing in it is a coverage hole.
        coverage[out_r0:out_r1] = np.where(any_hit, 1.0, 0.0)
        hole[out_r0:out_r1] = (nodata.reshape(shape).mean(axis=(1, 3)) > 0.5) & ~any_hit
    return pooled, coverage, hole


#: Canvas pixels decoded per pooling strip.  Decoding costs ~12 bytes of
#: temporaries per pixel (float values, masks), so 512 K pixels is ~6 MB;
#: a z3 tile (2048 x 2048 canvas pixels) then runs in 8 strips, not ~50 MB.
_POOL_STRIP_PIXELS = 1 << 19


def _empty_fields():
    shape = (TILE_SIZE, TILE_SIZE)
    return np.full(shape, np.nan), np.zeros(shape), np.ones(shape, dtype=bool)


_NODATA_TILE: bytes | None = None


def nodata_tile() -> bytes:
    """A tile entirely outside radar coverage: the coverage-hole wash."""
    global _NODATA_TILE
    if _NODATA_TILE is None:
        rgba = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)
        rgba[:] = NODATA_RGBA
        _NODATA_TILE = encode_png(rgba)
    return _NODATA_TILE


# Rendered tiles by (source, stamp, z, x, y).  A tile's bytes never change for
# a stamp (the URL says which frame), so the only invalidation is eviction.
_TILE_CACHE: "OrderedDict[tuple[str, str, int, int, int], bytes]" = OrderedDict()
_TILE_CACHE_SIZE = 1024  # ~10-20 KB each
_TILE_LOCK = threading.Lock()


def tile_png(store: FrameStore, source: str, valid_time: datetime, z: int, x: int, y: int) -> bytes:
    """PNG for one tile of one stored frame, cached.

    Raises ``FileNotFoundError`` when the frame is not (or no longer) stored.
    """
    key = (source, frame_stamp(valid_time), z, x, y)
    with _TILE_LOCK:
        cached = _TILE_CACHE.get(key)
        if cached is not None:
            _TILE_CACHE.move_to_end(key)
            return cached
    canvas = load_canvas(store, source, valid_time)
    rgba = render_tile_rgba(canvas, z, x, y)
    png = nodata_tile() if _all_nodata(rgba) else encode_png(rgba)
    with _TILE_LOCK:
        _TILE_CACHE[key] = png
        while len(_TILE_CACHE) > _TILE_CACHE_SIZE:
            _TILE_CACHE.popitem(last=False)
    return png


def _all_nodata(rgba: np.ndarray) -> bool:
    return bool((rgba == np.asarray(NODATA_RGBA, dtype=np.uint8)).all())
