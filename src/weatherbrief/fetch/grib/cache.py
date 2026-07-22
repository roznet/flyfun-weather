"""Disk cache for downloaded GRIB2 data.

Cache layout:
    {data_dir}/.cache/grib/{model}/{YYYYMMDD}_{HH}z/
        f{FFF}_{var}_{bbox_hash}.grib2

TTL is per-model: ICON-EU is precached on each main run so the previous run
is dead weight after a few hours (12 h); GFS isn't precached and is small
enough that 24 h costs almost nothing on disk, so it gets the more generous
window for fall-through to a prior run.

Runs are aged by the init time in the directory name, not the directory mtime
(which resets whenever a later briefing tops the run up with a new file). A
per-model size cap (:func:`cache_cap_bytes`) backstops the TTL: if the retained
set still exceeds the cap, the oldest-init runs are evicted whole until it fits,
never below a floor of runs. See issue #475.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Per-model TTL overrides. The model is recovered from the cache layout —
# ``run_dir.parent.name`` is the model key (see :func:`cache_dir_for_run`).
# Models not listed here fall back to :data:`CACHE_TTL_SECONDS`.
MODEL_TTL_SECONDS: dict[str, int] = {
    "gfs": 24 * 3600,       # no precache, small footprint (~0.5 GB/run)
    "icon-eu": 12 * 3600,   # precached each main run; previous run is fallback
    "icon-d2": 6 * 3600,    # 8 runs/day (every 3h); keep current + prior run
}

# Fallback TTL for models without an explicit entry. Currently unreachable
# (gfs + icon-eu are the only cache users and both are listed above); kept
# as a safe default for any future non-precached model added to the cache.
CACHE_TTL_SECONDS = 12 * 3600

_GIB = 1024 ** 3

# Per-model hard disk cap (see :func:`purge_old_runs`). Applied *after* the TTL
# rule as a backstop: TTL alone can't bound disk because retained size scales
# with whatever the flight set demands, and the flight count grows over time.
# Eviction is oldest-init-first, whole run dirs, and never drops below
# ``DEFAULT_CACHE_FLOOR_RUNS`` (so the current run + its prior-run fallback are
# preserved). Deliberately *not* LRU — a stale run is stale regardless of how
# recently it was read, so age (init time) is the right utility signal.
#
# ICON-D2 (issue #475): two full runs measure ~41.6 GiB, so a 45 GiB cap lets
# the normal current+prior pair sit while evicting a third run that TTL drift or
# a growing flight set would otherwise pile on (~64 GiB against ~52 GiB free).
# Env override per model: ``WB_GRIB_CACHE_CAP_GB_<MODEL>`` (e.g.
# ``WB_GRIB_CACHE_CAP_GB_ICON_D2=40``; ``0``/negative disables). Models without
# a default and no override are uncapped (GFS is cheap; ICON-EU is bounded by
# its 12 h TTL once init-time aging lands).
DEFAULT_CACHE_FLOOR_RUNS = 2

_DEFAULT_CACHE_CAP_GIB: dict[str, float] = {
    "icon-d2": 45.0,
}


def cache_cap_bytes(model: str) -> int | None:
    """Return the disk cap in bytes for ``model``, or ``None`` if uncapped.

    Reads the ``WB_GRIB_CACHE_CAP_GB_<MODEL>`` env override first (``0`` or a
    negative value disables the cap for that model), then the built-in
    :data:`_DEFAULT_CACHE_CAP_GIB` default.
    """
    env_key = "WB_GRIB_CACHE_CAP_GB_" + model.upper().replace("-", "_")
    raw = os.environ.get(env_key)
    if raw is not None and raw.strip():
        try:
            gb = float(raw)
        except ValueError:
            logger.warning("Invalid %s=%r, ignoring cap override", env_key, raw)
        else:
            return int(gb * _GIB) if gb > 0 else None
    gb = _DEFAULT_CACHE_CAP_GIB.get(model)
    return int(gb * _GIB) if gb else None


def _ttl_for(run_dir: Path) -> int:
    """Look up the TTL for the model owning ``run_dir``.

    The cache layout puts the model key one level above the run directory
    (``.cache/grib/{model}/{init}z``), so the model name is recoverable
    without threading it through every call site.
    """
    return MODEL_TTL_SECONDS.get(run_dir.parent.name, CACHE_TTL_SECONDS)


def cache_dir_for_run(
    data_dir: Path,
    init_date: str,
    init_hour: int,
    model: str = "gfs",
) -> Path:
    """Return the cache directory for a specific model run.

    Args:
        data_dir: Base data directory.
        init_date: Model init date as YYYYMMDD.
        init_hour: Model init hour (0, 6, 12, 18).
        model: Model identifier (e.g. "gfs", "icon-eu").

    Returns:
        Path like {data_dir}/.cache/grib/{model}/20231027_00z/
    """
    return data_dir / ".cache" / "grib" / model / f"{init_date}_{init_hour:02d}z"


def cache_key(
    forecast_hour: int,
    variable: str,
) -> str:
    """Generate a cache filename for a specific GRIB2 download.

    Route-independent: GRIB files cover the full model domain (GFS global,
    ICON-EU all-Europe), so the same cached file serves any route.

    Args:
        forecast_hour: Forecast hour (e.g. 6).
        variable: Variable name (e.g. "CLWMR").

    Returns:
        Filename like "f006_CLWMR.grib2"
    """
    return f"f{forecast_hour:03d}_{variable}.grib2"


def is_cached(
    run_dir: Path,
    filename: str,
) -> bool:
    """Check whether a cache entry exists and is not expired, without reading it.

    Use this for cache-hit short-circuits where the caller will skip work
    rather than consume the bytes — e.g. ``_prefetch_icon_eu_data`` skipping
    already-downloaded variables. Calling :func:`get_cached` for the same
    purpose pulls the entire file into memory just to check ``is not None``,
    which inflates RSS on warm-cache refreshes.

    Side effect: if the file is past TTL, it is unlinked here, matching
    :func:`get_cached` semantics. Returns ``False`` in that case.
    """
    path = run_dir / filename
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    if age > _ttl_for(run_dir):
        logger.debug("Cache expired: %s (%.0fh old)", path, age / 3600)
        path.unlink(missing_ok=True)
        return False
    return True


def get_cached(
    run_dir: Path,
    filename: str,
) -> bytes | None:
    """Retrieve cached GRIB2 data if it exists and is not expired."""
    path = run_dir / filename
    if not path.exists():
        return None

    age = time.time() - path.stat().st_mtime
    if age > _ttl_for(run_dir):
        logger.debug("Cache expired: %s (%.0fh old)", path, age / 3600)
        path.unlink(missing_ok=True)
        return None

    logger.debug("Cache hit: %s", path)
    return path.read_bytes()


def put_cached(
    run_dir: Path,
    filename: str,
    data: bytes,
) -> Path:
    """Store GRIB2 data in the cache atomically.

    Writes to a tempfile in the same directory and then ``os.replace``s it
    into place. The rename is atomic on POSIX and on NTFS, so concurrent
    callers racing on the same ``(run_dir, filename)`` either see the file
    not-yet-present or fully-written — never a half-written interleave.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / filename
    fd, tmp = tempfile.mkstemp(prefix=f".{filename}.", suffix=".tmp", dir=run_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    logger.debug("Cached: %s (%d bytes)", path, len(data))
    return path


def init_dt_from_run_dir(run_dir: Path) -> datetime | None:
    """Parse the model-run init time from a cache dir name.

    Cache dirs are named ``{YYYYMMDD}_{HH}z`` by :func:`cache_dir_for_run`, so
    the init time is recoverable from the name alone — no need to trust the
    directory mtime, which tracks file *creation/deletion* and so resets to
    "now" every time a later briefing tops the run up with a new
    ``(fhour, var, level)`` file (issue #475). Returns ``None`` for any name
    that doesn't parse (a stray/legacy dir), so the caller can fall back to
    mtime rather than pin it forever.
    """
    name = run_dir.name
    try:
        date_part, hour_part = name.split("_")
        if not hour_part.endswith("z"):
            return None
        return datetime.strptime(date_part, "%Y%m%d").replace(
            hour=int(hour_part[:-1]), tzinfo=timezone.utc,
        )
    except (ValueError, IndexError):
        return None


def _run_age_seconds(run_dir: Path, now_ts: float) -> float:
    """Age of ``run_dir`` in seconds, by init time (mtime fallback)."""
    init_dt = init_dt_from_run_dir(run_dir)
    if init_dt is not None:
        return now_ts - init_dt.timestamp()
    return now_ts - run_dir.stat().st_mtime


def _dir_size_bytes(path: Path) -> int:
    """Total size of all files under ``path`` (best-effort; skips vanished)."""
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _enforce_size_cap(
    cache_root: Path,
    model: str,
    cap_bytes: int,
    floor_runs: int,
    now_ts: float,
) -> int:
    """Evict oldest-init run dirs until the model total is under ``cap_bytes``.

    Whole run dirs only (never individual files: per-level re-download trades
    scarce bandwidth for disk — the wrong direction). Never drops below
    ``floor_runs`` so the current run and its prior-run fallback survive.
    Returns the number of directories evicted.
    """
    run_dirs = [d for d in cache_root.iterdir() if d.is_dir()]
    # Oldest init first; unparseable names sort by mtime.
    run_dirs.sort(key=lambda d: _run_age_seconds(d, now_ts), reverse=True)

    sizes = {d: _dir_size_bytes(d) for d in run_dirs}
    total = sum(sizes.values())

    removed = 0
    idx = 0
    while total > cap_bytes and (len(run_dirs) - removed) > floor_runs:
        victim = run_dirs[idx]
        reclaimed = sizes[victim]
        shutil.rmtree(victim, ignore_errors=True)
        total -= reclaimed
        removed += 1
        idx += 1
        logger.info(
            "GRIB cache cap: evicted %s run %s (%.1f MiB reclaimed, "
            "model total now %.1f MiB / cap %.1f MiB)",
            model, victim.name,
            reclaimed / (1024 * 1024),
            total / (1024 * 1024),
            cap_bytes / (1024 * 1024),
        )
    return removed


def purge_old_runs(
    data_dir: Path,
    model: str = "gfs",
    *,
    cap_bytes: int | None = None,
    floor_runs: int = DEFAULT_CACHE_FLOOR_RUNS,
    now: datetime | None = None,
) -> int:
    """Purge cache directories for ``model`` — by TTL, then by size cap.

    1. **TTL** — drop any run dir older than the model's TTL, aged by the init
       time encoded in the dir name (mtime fallback for unparseable names).
    2. **Size cap** — if a cap applies (``cap_bytes`` or the model default via
       :func:`cache_cap_bytes`), evict the oldest-init runs until the model
       total fits, never below ``floor_runs``.

    Returns the total number of directories removed.
    """
    cache_root = data_dir / ".cache" / "grib" / model
    if not cache_root.exists():
        return 0

    ttl = MODEL_TTL_SECONDS.get(model, CACHE_TTL_SECONDS)
    now_ts = time.time() if now is None else now.timestamp()
    removed = 0

    for run_dir in cache_root.iterdir():
        if not run_dir.is_dir():
            continue
        if _run_age_seconds(run_dir, now_ts) > ttl:
            shutil.rmtree(run_dir, ignore_errors=True)
            removed += 1
            logger.debug("Purged old cache: %s", run_dir)

    if cap_bytes is None:
        cap_bytes = cache_cap_bytes(model)
    if cap_bytes is not None:
        removed += _enforce_size_cap(
            cache_root, model, cap_bytes, floor_runs, now_ts,
        )

    return removed
