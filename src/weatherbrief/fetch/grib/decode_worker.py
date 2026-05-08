"""Process-pool worker entry points for GRIB decode.

Phase B-3 of the refresh-pipeline performance plan: GRIB decode is GIL-bound
in cfgrib + xarray + numpy interp loops, so two concurrent enrich_forecasts()
calls in the uvicorn process bottleneck on the GIL even with multiple cores
idle. Moving decode to a worker pool gives each decode its own interpreter
and removes the contention.

The wrappers here are deliberately thin:

- They accept only serialisable args (path strings, lat/lon lists, ints).
  GRIB bytes are read from disk inside the worker rather than pickled across
  the IPC boundary — the per-fhour ICON dict alone can be ~500 MB, far more
  than the 50–150 ms cfgrib decode they wrap.
- They return only serialisable results (lists of dicts of primitives, plus
  optional coverage masks). Anything cfgrib-internal (Datasets, file handles)
  must stay inside the worker.

Module-level state is constants only. Each worker process is fork-safe in
spawn mode (the only mode we use, for macOS/Linux parity).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _worker_init() -> None:
    """Called once per worker process at pool startup.

    Eagerly imports cfgrib + xarray + the decode module so the first decode
    dispatched to a fresh worker doesn't pay the import cost (~1–2 s on
    cold disk). Spawn mode means each worker is a fresh interpreter, so
    these imports happen on every pool boot.
    """
    # Worker logs are interleaved with the parent's stdout under uvicorn,
    # so prefix with the worker PID. Same field set as the parent format
    # otherwise; the prefix is what lets you tell which worker emitted a
    # cfgrib warning during concurrent decodes.
    logging.basicConfig(
        level=logging.INFO,
        format="[grib-worker %(process)d] %(levelname)s:%(name)s:%(message)s",
    )
    import cfgrib  # noqa: F401
    import numpy  # noqa: F401
    import xarray  # noqa: F401
    import weatherbrief.fetch.grib.decode  # noqa: F401


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def decode_ecmwf_pressure(
    file_path: str,
    latitudes: list[float],
    longitudes: list[float],
) -> tuple[list[dict[int, dict[str, float]]], list[bool]]:
    from weatherbrief.fetch.grib.decode import decode_ecmwf_pressure_per_point
    return decode_ecmwf_pressure_per_point(Path(file_path), latitudes, longitudes)


def decode_ecmwf_surface(
    file_path: str,
    latitudes: list[float],
    longitudes: list[float],
) -> tuple[list[dict[str, float]], list[bool]]:
    from weatherbrief.fetch.grib.decode import decode_ecmwf_surface_per_point
    return decode_ecmwf_surface_per_point(Path(file_path), latitudes, longitudes)


def decode_gfs_pressure(
    file_path: str,
    latitudes: list[float],
    longitudes: list[float],
) -> list[dict[int, dict[str, float]]]:
    """Read GFS CLWMR/ICMR bytes from cache and decode per-point."""
    from weatherbrief.fetch.grib.decode import decode_grib_per_point
    grib_bytes = Path(file_path).read_bytes()
    return decode_grib_per_point(grib_bytes, latitudes, longitudes)


def decode_gfs_cloud_diag(
    file_path: str,
    latitudes: list[float],
    longitudes: list[float],
) -> list[dict[str, float]]:
    from weatherbrief.fetch.grib.decode import decode_cloud_diag_per_point
    grib_bytes = Path(file_path).read_bytes()
    return decode_cloud_diag_per_point(grib_bytes, latitudes, longitudes)


def decode_icon_chunked(
    var_paths: dict[str, str],
    latitudes: list[float],
    longitudes: list[float],
) -> tuple[list[dict[int, dict[str, float]]], list[dict[str, float]]]:
    """Read ICON-EU per-variable bytes from cache and decode chunked.

    Reading the bytes inside the worker keeps the parent process from
    holding ~70 MB × 8 vars = ~560 MB per fhour — that was the dominant
    parent-RSS contributor before this change.
    """
    from weatherbrief.fetch.grib.decode import decode_icon_eu_per_point_chunked
    var_bytes = {var: Path(p).read_bytes() for var, p in var_paths.items()}
    return decode_icon_eu_per_point_chunked(var_bytes, latitudes, longitudes)


def decode_icon_legacy(
    file_path: str,
    latitudes: list[float],
    longitudes: list[float],
) -> list[dict[int, dict[str, float]]]:
    from weatherbrief.fetch.grib.decode import decode_icon_eu_per_point
    grib_bytes = Path(file_path).read_bytes()
    return decode_icon_eu_per_point(grib_bytes, latitudes, longitudes)


def decode_icon_cloud_diag(
    file_path: str,
    latitudes: list[float],
    longitudes: list[float],
) -> list[dict[str, float]]:
    from weatherbrief.fetch.grib.decode import decode_icon_eu_cloud_diag_per_point
    grib_bytes = Path(file_path).read_bytes()
    return decode_icon_eu_cloud_diag_per_point(grib_bytes, latitudes, longitudes)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
#
# These are only used by tests/test_grib_pool.py, but they live here (not in
# the test module) because spawn-mode workers re-import the function's home
# module by name — and pytest test modules aren't always importable from a
# fresh interpreter.


def _test_echo(payload, sleep: float = 0.0, raise_exc: str | None = None):
    import time as _t
    if raise_exc is not None:
        raise RuntimeError(raise_exc)
    if sleep > 0:
        _t.sleep(sleep)
    return payload


def _test_busy_loop(seconds: float) -> int:
    """CPU-bound loop — used to verify two workers actually run in parallel."""
    import time as _t
    end = _t.perf_counter() + seconds
    n = 0
    while _t.perf_counter() < end:
        n += 1
    return n


def _test_crash() -> None:
    import os
    import signal
    os.kill(os.getpid(), signal.SIGKILL)


def _test_pid() -> int:
    """Return the worker process PID — used to verify worker recycling."""
    import os
    return os.getpid()


def _test_hang(seconds: float = 60.0) -> None:
    """Sleep for *seconds* — used to verify the dispatcher's timeout path.

    A real cfgrib/ECCODES deadlock would be uninterruptible from outside;
    sleep is a close-enough analogue for testing that ``future.result(timeout=)``
    fires and the recovery path runs.
    """
    import time as _t
    _t.sleep(seconds)
