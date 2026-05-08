"""Disk cache for downloaded GRIB2 data.

Cache layout:
    {data_dir}/.cache/grib/{model}/{YYYYMMDD}_{HH}z/
        f{FFF}_{var}_{bbox_hash}.grib2

TTL: 7 hours — slightly more than one main-cycle gap (6 h) so the cache holds
essentially one main run at a time with a small overlap during rollover.
Shortened from 24 h once the precache loop (issue #126) started actively
warming the cache for each new main run.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Cache entries older than this are purged. 7 h ≈ one main-cycle gap (6 h)
# plus headroom — the precache loop refreshes the cache for each new main run.
# Tighter than the previous 24 h: if the precache loop stalls (server restart
# during a delayed publish, or briefings landing on a never-precached short
# cycle 03/09/15/21Z), entries fall out of cache faster. Acceptable margin
# under expected operation; widen back if telemetry shows stalls dominating.
CACHE_TTL_SECONDS = 7 * 3600


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
    if age > CACHE_TTL_SECONDS:
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
    if age > CACHE_TTL_SECONDS:
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


def purge_old_runs(data_dir: Path, model: str = "gfs") -> int:
    """Remove cache directories older than CACHE_TTL_SECONDS.

    Returns number of directories removed.
    """
    cache_root = data_dir / ".cache" / "grib" / model
    if not cache_root.exists():
        return 0

    removed = 0
    now = time.time()
    for run_dir in cache_root.iterdir():
        if not run_dir.is_dir():
            continue
        # Use directory mtime as proxy for age
        age = now - run_dir.stat().st_mtime
        if age > CACHE_TTL_SECONDS:
            import shutil
            shutil.rmtree(run_dir, ignore_errors=True)
            removed += 1
            logger.debug("Purged old cache: %s", run_dir)

    return removed
