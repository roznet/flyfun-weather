"""Disk cache for downloaded GRIB2 data.

Cache layout:
    {data_dir}/.cache/grib/{model}/{YYYYMMDD}_{HH}z/
        f{FFF}_{var}_{bbox_hash}.grib2

TTL: 24 hours — each model run is self-contained, no cross-run comparison needed.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Cache entries older than this are purged
CACHE_TTL_SECONDS = 24 * 3600  # 24 hours


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
    """Store GRIB2 data in the cache."""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / filename
    path.write_bytes(data)
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
