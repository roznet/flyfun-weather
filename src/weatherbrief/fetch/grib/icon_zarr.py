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
import threading
from contextlib import contextmanager
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


# TODO(#705 follow-up): drop once every venv has been reinstalled with the
# zarr dependency; only the transition needs it.
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
    import zarr
    from zarr.codecs import BloscCodec

    from weatherbrief.fetch.grib.grib_reader import iter_messages

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


class StoreCorrupt(Exception):
    """The store itself is unreadable: metadata, attributes or a shard.

    Raised only around the zarr reads, never around the interpolation, so a
    bug in the reader's own arithmetic cannot be mistaken for a bad store and
    get every store a briefing touches deleted. I/O and memory errors pass
    through unwrapped: they are transient and say nothing about the store.
    """


@contextmanager
def _store_reads(store_path):
    try:
        yield
    except (OSError, MemoryError):
        raise
    except Exception as e:
        raise StoreCorrupt(f"{store_path}: {e!r}") from e


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

    with _store_reads(store_path):
        arr = zarr.open_array(str(store_path), mode="r")
        attrs = arr.attrs
        if attrs.get("schema") != STORE_SCHEMA:
            raise ValueError(f"unknown store schema {attrs.get('schema')!r}")
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
        with _store_reads(store_path):
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
    run_dir: Path, filename: str, data: bytes, *, priority=None, wait: bool = True,
) -> Path:
    """Cache one ICON-EU whole-column blob and convert it to zarr.

    Writes the GRIB with ``put_cached`` first, so it is a valid cache entry
    whatever happens to the conversion. The conversion runs in a decode-pool
    worker (CPU and ~360 MB peak, kept off the uvicorn process) and deletes
    the GRIB once the store is in place. ICON is re-downloadable for about a
    day, so the GRIB is not kept as a backup. With no decode pool there is no
    conversion at all.

    ``wait=True`` (the warm loop) blocks until the store is written.
    ``wait=False`` (a briefing prefetch) queues the conversion and returns at
    once: the briefing decodes the GRIB exactly as before #705, so a cold
    briefing never waits on a conversion, and the store serves the next one.
    Pass ``priority=BACKGROUND`` with it so the queued job only takes idle pool
    time. ``priority`` otherwise defaults to the caller's (the ContextVar).

    The job is safe under the dispatcher's at-least-once delivery: a re-run
    finds the store and returns at once, and the GRIB is deleted here in the
    parent, never in the job.

    Returns the cache entry that holds the data now (the GRIB when the
    conversion was only queued).
    """
    from weatherbrief.fetch.grib import (
        _dispatch_decode,
        _get_dispatcher,
        _priority_enabled,
        _resolve_priority,
        decode_pool_enabled,
    )
    from weatherbrief.fetch.grib.cache import put_cached

    grib_path = put_cached(run_dir, filename, data)
    # No pool (GRIB_DECODE_WORKERS=0, the kill switch / some dev setups): the
    # dispatcher would run the conversion inline, putting a ~360 MB job on the
    # app process. Keep the GRIB instead; the decoder reads it as before.
    if not zarr_enabled() or not decode_pool_enabled():
        return grib_path
    store_path = run_dir / store_name(filename)
    args = (str(grib_path), str(store_path))
    # One conversion per store at a time: two writers that downloaded the
    # same variable together (two briefings, or a briefing and the warm loop)
    # would otherwise each build a ~360 MB cube on separate workers. The
    # second just leaves its (identical) GRIB for the first job to delete.
    if not _claim(store_path):
        return grib_path

    if not wait:
        # The legacy FIFO path (GRIB_DECODE_PRIORITY_ENABLED=0) has no
        # non-blocking submit; a rollback switch, so it just keeps the GRIB.
        if not _priority_enabled():
            return grib_path
        try:
            future = _get_dispatcher().submit_one(
                "convert_icon_zarr", args, _resolve_priority(priority),
            )
        except BaseException:
            _release(store_path)
            raise
        future.add_done_callback(
            lambda f: _finish_conversion(f, grib_path, store_path, filename),
        )
        return grib_path

    try:
        ok = _dispatch_decode("convert_icon_zarr", *args, priority=priority)
    except Exception:
        logger.warning("ICON zarr conversion failed for %s, keeping GRIB", filename, exc_info=True)
        ok = False
    finally:
        _release(store_path)
    return _after_conversion(ok, grib_path, store_path)


_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: set[str] = set()


def _claim(store_path: Path) -> bool:
    """Mark a store's conversion as queued or running; False if it already is."""
    key = str(store_path)
    with _INFLIGHT_LOCK:
        if key in _INFLIGHT:
            return False
        _INFLIGHT.add(key)
        return True


def _release(store_path: Path) -> None:
    with _INFLIGHT_LOCK:
        _INFLIGHT.discard(str(store_path))


def _after_conversion(ok: bool, grib_path: Path, store_path: Path) -> Path:
    """Drop the GRIB once its store exists; return the entry now holding it.

    Also checks the store when the job failed: a racing writer may have
    finished it (and deleted the GRIB) while this conversion read the file.
    """
    if ok or (store_path / "zarr.json").exists():
        grib_path.unlink(missing_ok=True)
        return store_path
    return grib_path


def _finish_conversion(future, grib_path: Path, store_path: Path, filename: str) -> None:
    """Done-callback of a queued conversion. Never raises (dispatcher thread)."""
    _release(store_path)
    try:
        try:
            ok = bool(future.result())
        except Exception:
            # Includes a dispatcher shutdown failing the pending job: the GRIB
            # stays and decodes as before.
            logger.warning("ICON zarr conversion failed for %s, keeping GRIB", filename, exc_info=True)
            ok = False
        _after_conversion(ok, grib_path, store_path)
    except Exception:  # pragma: no cover - a callback must not raise
        logger.warning("ICON zarr post-conversion step failed for %s", filename, exc_info=True)
