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
import os
import time as _time
from contextlib import contextmanager
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

    # Hang-diag (2026-05-14): SIGUSR1 → Python stack dump for all threads.
    # Parent sends this to each worker on the timeout-recovery path so we can
    # see where cfgrib/eccodes is actually stuck. Delivered only when worker
    # returns to userspace — pair with /proc/<pid>/wchan from the parent for
    # cases where the worker is in uninterruptible kernel wait.
    import faulthandler
    import signal
    faulthandler.enable()
    try:
        faulthandler.register(signal.SIGUSR1, all_threads=True)
    except (AttributeError, RuntimeError, ValueError) as e:
        logger.warning("faulthandler.register(SIGUSR1) failed: %s", e)

    import cfgrib  # noqa: F401
    import numpy  # noqa: F401
    import xarray  # noqa: F401
    import weatherbrief.fetch.grib.decode  # noqa: F401

    # One-line baseline so we know which cfgrib / eccodes versions a hang
    # report came from, and whether idx caching is on.
    try:
        eccodes_ver = "?"
        try:
            from eccodes import codes_get_api_version
            eccodes_ver = str(codes_get_api_version())
        except Exception:
            pass
        logger.info(
            "GRIB worker ready: cfgrib=%s eccodes=%s CFGRIB_USE_INDEXPATH=%s",
            getattr(cfgrib, "__version__", "?"),
            eccodes_ver,
            os.environ.get("CFGRIB_USE_INDEXPATH", "<unset>"),
        )
    except Exception as e:  # pragma: no cover — diagnostic must not break init
        logger.warning("worker baseline log failed: %s", e)


@contextmanager
def _log_decode(fn_name: str, file_path: str):
    """Pair entry + exit logs for a decode call (hang-diag, 2026-05-14).

    Entry log captures file path/size/mtime-age + idx sidecar state — on a
    300 s hang, the worker's last log line tells us exactly which file was
    being decoded and what shape the file + its idx were in. Exit log
    confirms the call returned (or completes the pair on a Python-level
    exception); together they make "started but never ended" grep-able.

    Costs <1 ms per call; both branches are try/except'd so the diagnostic
    can never break the hot path.
    """
    pid = os.getpid()
    fname = file_path
    try:
        fname = Path(file_path).name
    except Exception:
        pass
    start = _time.perf_counter()

    # Entry
    try:
        p = Path(file_path)
        try:
            st = p.stat()
            file_info = f"size={st.st_size} age={_time.time() - st.st_mtime:.1f}s"
        except OSError as e:
            file_info = f"stat_failed={e!s}"
        idx_info = "idx=none"
        try:
            # cfgrib idx files: <file>.<hash>.idx (hash is content-derived).
            idx_candidates = list(p.parent.glob(f"{p.name}.*.idx"))
            if idx_candidates:
                idx = idx_candidates[0]
                ist = idx.stat()
                idx_info = (
                    f"idx={idx.name} idx_size={ist.st_size} "
                    f"idx_age={_time.time() - ist.st_mtime:.1f}s"
                )
        except OSError:
            pass
        logger.info(
            "%s start: file=%s %s %s pid=%d",
            fn_name, fname, file_info, idx_info, pid,
        )
    except Exception:
        pass

    try:
        yield
    finally:
        try:
            dur_ms = (_time.perf_counter() - start) * 1000
            logger.info(
                "%s end: file=%s dur=%.1fms pid=%d",
                fn_name, fname, dur_ms, pid,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def decode_ecmwf_pressure(
    file_path: str,
    latitudes: list[float],
    longitudes: list[float],
) -> tuple[list[dict[int, dict[str, float]]], list[bool]]:
    with _log_decode("decode_ecmwf_pressure", file_path):
        from weatherbrief.fetch.grib.decode import decode_ecmwf_pressure_per_point
        return decode_ecmwf_pressure_per_point(Path(file_path), latitudes, longitudes)


def decode_ecmwf_surface(
    file_path: str,
    latitudes: list[float],
    longitudes: list[float],
) -> tuple[list[dict[str, float]], list[bool]]:
    with _log_decode("decode_ecmwf_surface", file_path):
        from weatherbrief.fetch.grib.decode import decode_ecmwf_surface_per_point
        return decode_ecmwf_surface_per_point(Path(file_path), latitudes, longitudes)


def decode_gfs_pressure(
    file_path: str,
    latitudes: list[float],
    longitudes: list[float],
) -> list[dict[int, dict[str, float]]]:
    """Read GFS CLWMR/ICMR bytes from cache and decode per-point."""
    with _log_decode("decode_gfs_pressure", file_path):
        from weatherbrief.fetch.grib.decode import decode_grib_per_point
        grib_bytes = Path(file_path).read_bytes()
        return decode_grib_per_point(grib_bytes, latitudes, longitudes)


def decode_gfs_cloud_diag(
    file_path: str,
    latitudes: list[float],
    longitudes: list[float],
) -> list[dict[str, float]]:
    with _log_decode("decode_gfs_cloud_diag", file_path):
        from weatherbrief.fetch.grib.decode import decode_cloud_diag_per_point
        grib_bytes = Path(file_path).read_bytes()
        return decode_cloud_diag_per_point(grib_bytes, latitudes, longitudes)


def decode_icon_chunked(
    var_paths: dict[str, list[str]],
    latitudes: list[float],
    longitudes: list[float],
) -> tuple[list[dict[int, dict[str, float]]], list[dict[str, float]]]:
    """Read ICON per-variable bytes from cache and decode chunked.

    Reading the bytes inside the worker keeps the parent process from
    holding ~70 MB × 8 vars = ~560 MB per fhour — that was the dominant
    parent-RSS contributor before this change.

    ``var_paths`` maps each variable to a LIST of cache-file paths, which are
    concatenated into that variable's GRIB2 byte stream. A whole-column blob
    (ICON-EU, or a legacy ICON-D2 hit) is a one-element list; the per-level
    cache (ICON-D2, #469) is one path per model level. cfgrib indexes messages
    by their level coordinate, so concatenation order is irrelevant.
    """
    # ICON takes a dict of var→paths; log the first var's first file as a rep.
    fn_label = "decode_icon_chunked"
    rep_path = ""
    if var_paths:
        first_var = next(iter(var_paths))
        fn_label = f"decode_icon_chunked[{first_var}+{len(var_paths)-1}]"
        first_paths = var_paths[first_var]
        rep_path = first_paths[0] if first_paths else ""
    with _log_decode(fn_label, rep_path):
        from weatherbrief.fetch.grib.decode import decode_icon_eu_per_point_chunked
        var_bytes = {
            var: b"".join(Path(p).read_bytes() for p in paths)
            for var, paths in var_paths.items()
        }
        return decode_icon_eu_per_point_chunked(var_bytes, latitudes, longitudes)


def decode_icon_legacy(
    file_path: str,
    latitudes: list[float],
    longitudes: list[float],
) -> list[dict[int, dict[str, float]]]:
    with _log_decode("decode_icon_legacy", file_path):
        from weatherbrief.fetch.grib.decode import decode_icon_eu_per_point
        grib_bytes = Path(file_path).read_bytes()
        return decode_icon_eu_per_point(grib_bytes, latitudes, longitudes)


def decode_icon_cloud_diag(
    file_path: str,
    latitudes: list[float],
    longitudes: list[float],
) -> list[dict[str, float]]:
    with _log_decode("decode_icon_cloud_diag", file_path):
        from weatherbrief.fetch.grib.decode import decode_icon_eu_cloud_diag_per_point
        grib_bytes = Path(file_path).read_bytes()
        return decode_icon_eu_cloud_diag_per_point(grib_bytes, latitudes, longitudes)


def decode_icon_d2_explicit_conv(
    var_paths: dict[str, str],
    latitudes: list[float],
    longitudes: list[float],
) -> list[dict]:
    """Read per-variable ICON-D2 explicit-convection bytes and decode corridor extrema (#462)."""
    fn_label = "decode_icon_d2_explicit_conv"
    rep_path = next(iter(var_paths.values()), "")
    if var_paths:
        fn_label = f"decode_icon_d2_explicit_conv[{len(var_paths)}v]"
    with _log_decode(fn_label, rep_path):
        from weatherbrief.fetch.grib.decode import (
            decode_icon_d2_explicit_conv_per_point,
        )
        var_bytes = {var: Path(p).read_bytes() for var, p in var_paths.items()}
        return decode_icon_d2_explicit_conv_per_point(var_bytes, latitudes, longitudes)


def analyze_sounding_batch(items: list[dict]) -> list[dict]:
    """Lite sounding analysis for a batch of serialised profiles (#448 PR B).

    The first non-decode entry point: the standalone forecast cycle ships
    ~50–100 profiles per job (plain dicts — levels + surface fields + model
    key) and gets back the snapshot scalar fields per profile. Pure and
    idempotent, so the dispatcher may retry it freely. MetPy imports lazily
    on the first batch a worker sees (~1–2 s once per worker); not added to
    ``_worker_init`` so the web app's decode pool doesn't pay it.
    """
    pid = os.getpid()
    start = _time.perf_counter()
    from weatherbrief.analysis.sounding.snapshot_fields import (
        analyze_sounding_batch_items,
    )
    results = analyze_sounding_batch_items(items)
    logger.info(
        "analyze_sounding_batch: %d profiles dur=%.1fms pid=%d",
        len(items), (_time.perf_counter() - start) * 1000, pid,
    )
    return results


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
