"""ICON-EU model-level GRIB cached as chunked zarr (#705, phase 2 of #674).

The direct eccodes decoder (phase 1) is at the CCSDS unpack floor: every
forecast hour unpacks nine variables x 40 levels of the full 657 x 1377 grid
to read a few hundred route points. This module converts each downloaded
whole-column blob once, when it is written, into a zarr v3 store tiled
64 x 64 x all levels, so a briefing reads only the tiles under its route.

Layout. One store per cached blob, i.e. per (run, forecast hour, variable):
``f003_ICON_EU_T.grib2`` becomes ``f003_ICON_EU_T.zarr/`` beside it, then
the GRIB is deleted. A store is two files (``zarr.json`` + one shard), so a
forecast hour is ~18 files. Keeping the existing per-variable cache key
(rather than one store per hour holding every variable) means the prefetch,
which downloads each variable as its own unit, and the decode completeness
check (#478) keep working unchanged on "is this variable cached".

Values are float32, exactly as :class:`grib_reader.GribMessage` unpacks them
(bitmap-masked cells NaN), compressed with blosc zstd + bitshuffle. The GRIB
path rounds through float32 too, so the zarr read is bit-identical by
construction. The uint16 + per-level reference/step alternative from the issue
is ~17 % smaller on disk but has to reproduce eccodes' packing arithmetic to
stay exact; not worth it until disk says so.

The reader computes bilinear weights from the stored axes with the shared
``decode._bilinear_grid_weights`` (and so ``_align_lons_to_axis``) and combines
corners with ``grib_reader.combine_bilinear``: the same edge and NaN rules as
the GRIB path, pinned by ``tests/test_icon_zarr.py`` against the direct decoder.

``zarr`` is imported lazily. If it is missing, or ``WB_ICON_ZARR=0``,
conversion is skipped and the GRIB stays the cache entry, which the decoder
still reads; existing stores are always honoured.
"""

from __future__ import annotations

import functools
import logging
import math
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

STORE_SUFFIX = ".zarr"

# Bumped if the store layout or attributes change; the reader refuses others.
STORE_SCHEMA = "wb-icon-ml-zarr-v1"

# Horizontal tile edge. 64 x 64 x 40 levels x float32 = 640 KiB per tile
# uncompressed; a 154-point route touches a few dozen tiles (#674 benchmark).
TILE = 64

# Model-level typeOfLevel values, as ``decode._ICON_MODEL_LEVEL_TYPES``.
_MODEL_LEVEL_TYPES = {"generalVerticalLayer", "generalVertical", "hybrid"}


def zarr_enabled() -> bool:
    """Convert on write unless ``WB_ICON_ZARR=0`` (the rollback switch).

    Also False when the ``zarr`` package is not installed, so an environment
    without it keeps caching GRIB instead of failing every conversion.
    """
    if os.environ.get("WB_ICON_ZARR", "1").strip().lower() in ("0", "false", "no"):
        return False
    return _zarr_installed()


@functools.cache
def _zarr_installed() -> bool:
    import importlib.util

    if importlib.util.find_spec("zarr") is None:
        logger.warning("zarr not installed: ICON-EU model levels stay GRIB (#705)")
        return False
    return True


def store_name(grib_filename: str) -> str:
    """``f003_ICON_EU_T.grib2`` → ``f003_ICON_EU_T.zarr``."""
    stem = grib_filename[: -len(".grib2")] if grib_filename.endswith(".grib2") else grib_filename
    return stem + STORE_SUFFIX


def is_store_path(path: str | Path) -> bool:
    return str(path).rstrip("/").endswith(STORE_SUFFIX)


# ---------------------------------------------------------------------------
# Convert (runs in a decode-pool worker)
# ---------------------------------------------------------------------------


def _scan_levels(grib_path: Path):
    """Header pass: the single model-level series in ``grib_path``, or None.

    Returns ``(var, type_of_level, grid_key, levels)`` with ``levels`` sorted.
    Refuses (None) anything the direct decoder would not read as one clean
    column: more than one variable or grid, a level carrying two different
    headers (step/date/member), or fewer than two levels. Refused blobs stay
    GRIB, so the decoder treats them exactly as before.
    """
    from weatherbrief.fetch.grib.grib_reader import iter_messages

    ident = None
    headers: dict[float, tuple] = {}
    for msg in iter_messages(grib_path):
        if msg.type_of_level not in _MODEL_LEVEL_TYPES:
            continue
        key = (msg.var, msg.type_of_level, msg.grid_key)
        if ident is None:
            ident = key
        elif key != ident:
            logger.info("zarr convert: %s holds more than one series, keeping GRIB", grib_path.name)
            return None
        seen = headers.setdefault(msg.level, msg.header)
        if seen != msg.header:
            logger.info("zarr convert: %s has >1 step/member per level, keeping GRIB", grib_path.name)
            return None
    if ident is None or len(headers) < 2:
        return None
    return (*ident, sorted(headers))


def _fsync_path(path: str | Path, *, drop: bool = False) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
        if drop and hasattr(os, "posix_fadvise"):  # absent on macOS
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)


def _fsync_tree(root: Path) -> None:
    """fsync every file and directory under ``root``, dropping file pages
    from the page cache (see put_cached), so a store renamed into place after
    this never appears without its contents after a crash."""
    for dirpath, _, files in os.walk(root, topdown=False):
        for name in files:
            _fsync_path(os.path.join(dirpath, name), drop=True)
        _fsync_path(dirpath)


def convert_grib_to_store(grib_path: str | Path, store_path: str | Path) -> bool:
    """Convert one ICON model-level blob to a zarr store, atomically.

    Two passes over the file: headers first (no unpack) to size the cube, then
    one level at a time unpacked straight into it, so peak memory is one
    variable's cube (~145 MB float32 for 40 levels) plus one message. Built in
    a hidden temp directory beside the target and renamed into place; if a
    concurrent writer got there first, its store wins and this one is dropped.

    Returns True when ``store_path`` exists afterwards (written here or by a
    racing writer), False when the blob was refused or conversion failed.
    """
    import numcodecs.blosc
    import zarr
    from zarr.codecs import BloscCodec

    from weatherbrief.fetch.grib.grib_reader import iter_messages

    # One compression thread: this runs in a decode-pool worker, and the pool
    # size is what bounds GRIB CPU on the box. Blosc defaults to every core.
    # Process-global, which is fine: pool workers have no other blosc user.
    numcodecs.blosc.set_nthreads(1)

    grib_path = Path(grib_path)
    store_path = Path(store_path)
    if (store_path / "zarr.json").exists():
        return True

    scanned = _scan_levels(grib_path)
    if scanned is None:
        return False
    var, type_of_level, grid_key, levels = scanned
    index = {lev: k for k, lev in enumerate(levels)}

    cube = None
    grid = None
    filled = np.zeros(len(levels), dtype=bool)
    for msg in iter_messages(grib_path):
        if msg.type_of_level not in _MODEL_LEVEL_TYPES:
            continue
        k = index.get(msg.level)
        if k is None or filled[k]:
            continue  # first message per level wins, as in the direct decoder
        if grid is None:
            grid = msg.grid()
            if grid is None:
                logger.info("zarr convert: %s is not a regular lat/lon grid, keeping GRIB", grib_path.name)
                return False
            cube = np.empty((len(levels), *grid.shape), dtype=np.float32)
        values = msg.values(grid)
        if values is None:
            logger.warning("zarr convert: %s level %s value count != grid, keeping GRIB", grib_path.name, msg.level)
            return False
        cube[k] = values
        filled[k] = True
    if cube is None or not filled.all():
        return False

    ny, nx = grid.shape
    attrs = {
        "schema": STORE_SCHEMA,
        "var": var,
        "type_of_level": type_of_level,
        "grid_key": grid_key,
        "levels": [int(lev) for lev in levels],
        # float64 axes, exactly as eccodes presented them (JSON round-trips
        # a float64 exactly), so the reader's weights match the GRIB path's.
        "lats": [float(v) for v in grid.lats],
        "lons": [float(v) for v in grid.lons],
    }
    store_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{store_path.name}.", suffix=".tmp", dir=store_path.parent))
    try:
        arr = zarr.create_array(
            store=str(tmp),
            shape=cube.shape,
            dtype="float32",
            chunks=(len(levels), TILE, TILE),
            # One shard per row of tiles: a dozen files per store instead of
            # one per tile, and each band is encoded and written on its own,
            # so the write never holds a second copy of the whole variable.
            shards=(len(levels), TILE, math.ceil(nx / TILE) * TILE),
            compressors=BloscCodec(cname="zstd", clevel=3, shuffle="bitshuffle"),
            fill_value=np.nan,
            attributes=attrs,
            zarr_format=3,
        )
        for y0 in range(0, ny, TILE):
            arr[:, y0:y0 + TILE, :] = cube[:, y0:y0 + TILE, :]
        del cube
        _fsync_tree(tmp)
        try:
            os.rename(tmp, store_path)
        except OSError:
            if (store_path / "zarr.json").exists():
                shutil.rmtree(tmp, ignore_errors=True)
                return True  # a concurrent writer finished first
            raise
        _fsync_path(store_path.parent)  # make the rename itself durable
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return True


# ---------------------------------------------------------------------------
# Read (runs in a decode-pool worker)
# ---------------------------------------------------------------------------


def read_model_level_columns(
    store_path: str | Path,
    latitudes: list[float],
    longitudes: list[float],
) -> dict[int, list[float | None]]:
    """Per-point columns from a store, same shape as the direct GRIB decoder.

    ``{model_level: [value for each target, None where missing or outside]}``,
    identical to ``decode._decode_icon_eu_single_var_direct`` on the GRIB the
    store was made from. Only the tiles holding a bilinear corner are read.
    """
    import zarr

    from weatherbrief.fetch.grib.decode import _bilinear_grid_weights
    from weatherbrief.fetch.grib.grib_reader import combine_bilinear

    arr = zarr.open_array(str(store_path), mode="r")
    attrs = arr.attrs
    if attrs.get("schema") != STORE_SCHEMA:
        raise ValueError(f"{store_path}: unknown store schema {attrs.get('schema')!r}")
    levels = [int(v) for v in attrs["levels"]]
    lats = np.asarray(attrs["lats"], dtype=np.float64)
    lons = np.asarray(attrs["lons"], dtype=np.float64)
    n_points = len(latitudes)

    bw = _bilinear_grid_weights(
        lats, lons,
        np.asarray(latitudes, dtype=np.float64),
        np.asarray(longitudes, dtype=np.float64),
    )
    if bw is None:
        return {}
    if not bw.inb_idx.size:
        return {lev: [None] * n_points for lev in levels}

    # Every corner cell, deduplicated, then fetched tile by tile.
    _, ny, nx = arr.shape
    rows = np.concatenate((bw.i0, bw.i0, bw.i1, bw.i1))
    cols = np.concatenate((bw.j0, bw.j1, bw.j0, bw.j1))
    cells, inverse = np.unique(rows * nx + cols, return_inverse=True)
    cell_rows, cell_cols = cells // nx, cells % nx
    compact = np.empty((len(levels), cells.size), dtype=np.float32)
    tiles = (cell_rows // TILE) * math.ceil(nx / TILE) + (cell_cols // TILE)
    for tile in np.unique(tiles):
        sel = np.flatnonzero(tiles == tile)
        y0 = int(cell_rows[sel[0]] // TILE) * TILE
        x0 = int(cell_cols[sel[0]] // TILE) * TILE
        block = arr[:, y0:min(y0 + TILE, ny), x0:min(x0 + TILE, nx)]
        compact[:, sel] = block[:, cell_rows[sel] - y0, cell_cols[sel] - x0]

    m = bw.inb_idx.size
    corners = tuple(compact[:, inverse[c * m:(c + 1) * m]] for c in range(4))
    gathered = combine_bilinear(corners, bw)  # (levels, in-bounds points)

    out: dict[int, list[float | None]] = {}
    inb_idx = bw.inb_idx
    for k, lev in enumerate(levels):
        col: list[float | None] = [None] * n_points
        row = gathered[k]
        for p, pt in enumerate(inb_idx):
            v = float(row[p])
            if not math.isnan(v):
                col[pt] = v
        out[lev] = col
    return out


# ---------------------------------------------------------------------------
# Cache entry lookup and write-side hook (parent process)
# ---------------------------------------------------------------------------


def cached_model_level_entry(run_dir: Path, filename: str) -> Path | None:
    """The cache entry for one ICON-EU whole-column key, or None if absent.

    The zarr store when there is one, else the GRIB blob (written before
    #705, refused by the converter, or converted with ``WB_ICON_ZARR=0``).
    Both count as "cached" for the prefetch and decode completeness checks.
    """
    from weatherbrief.fetch.grib.cache import is_cached, is_store_cached

    store = store_name(filename)
    if is_store_cached(run_dir, store):
        return run_dir / store
    if is_cached(run_dir, filename):
        return run_dir / filename
    return None


def cache_model_level_blob(
    run_dir: Path, filename: str, data: bytes, *, priority=None,
) -> Path:
    """Cache one ICON-EU whole-column blob, converting it to zarr on write.

    Writes the GRIB with ``put_cached`` (so a failed conversion still leaves a
    valid cache entry), converts it in a decode-pool worker (the conversion is
    CPU and ~145 MB, so it stays off the uvicorn process, and the dispatcher's
    priority ordering lets interactive decodes overtake a warm-loop
    conversion), and deletes the GRIB once the store is in place. ICON is
    re-downloadable for about a day, so the GRIB is not kept as a backup.
    ``priority`` defaults to the caller's decode priority (the ContextVar).

    Returns the path of the cache entry that holds the data now.
    """
    from weatherbrief.fetch.grib import _dispatch_decode
    from weatherbrief.fetch.grib.cache import put_cached

    grib_path = put_cached(run_dir, filename, data)
    if not zarr_enabled():
        return grib_path
    store_path = run_dir / store_name(filename)
    try:
        ok = _dispatch_decode(
            "convert_icon_zarr", str(grib_path), str(store_path), priority=priority,
        )
    except Exception:
        logger.warning("ICON zarr conversion failed for %s, keeping GRIB", filename, exc_info=True)
        return grib_path
    if not ok:
        return grib_path
    grib_path.unlink(missing_ok=True)
    return store_path
