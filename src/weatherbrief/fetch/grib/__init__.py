"""GRIB2 enrichment for cloud liquid water and ice mixing ratio.

Public API: enrich_forecasts() adds CLWMR/ICMR data and cloud diagnostics to
existing cross-section forecasts from GFS and ICON-EU GRIB2 data.
"""

from __future__ import annotations

import contextvars
import gc
import logging
import multiprocessing
import os
import signal
import threading
import time as _time_mod
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import requests
from requests.adapters import HTTPAdapter

from weatherbrief.process_rss import current_rss_mb as _read_rss_mb

from weatherbrief.fetch.grib.cache import (
    cache_dir_for_run,
    cache_key,  # route-independent: keyed by (fhour, variable) only
    is_cached,
    purge_old_runs,
    put_cached,
)
from weatherbrief.fetch.grib.gfs_idx import plan_byte_ranges, plan_cloud_diag_byte_ranges
from weatherbrief.fetch.grib.grib_fetch import (
    fetch_byte_ranges,
    fetch_cloud_diag_ranges,
    fetch_idx,
    find_latest_run,
)
from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    NWPCloudDiagnostics,
    RouteCrossSection,
    RoutePoint,
    WaypointForecast,
)

logger = logging.getLogger(__name__)

# GFS enrichment fans out into two parallel branches (_enrich_clwmr_icmr +
# _enrich_cloud_diagnostics) that share one session, each running 8 download
# workers — peak 16 concurrent connections. Sized to 20 to leave headroom for
# concurrent .idx fetches and avoid urllib3 "Connection pool is full" warnings.
_POOL_MAXSIZE = 20

_M_TO_FT = 3.28084


# ---------------------------------------------------------------------------
# Sub-stage timing + memory instrumentation for refresh-pipeline profiling.
#
# State lives on a per-call _GribTimer instance, propagated to inner functions
# (and into Phase-1 ThreadPoolExecutor workers) via a ContextVar. This makes
# the instrumentation correct under concurrent refreshes — the API runs
# refresh pipelines in a ``ThreadPoolExecutor(max_workers=2)``, so two
# enrich_forecasts() can be in flight at once without their counters mixing.
#
# Worker threads spawned by Phase-1 ThreadPoolExecutor must run inside the
# parent's contextvars copy — see _submit_with_context() below.
# ---------------------------------------------------------------------------


class _GribTimer:
    """Per-enrich_forecasts call: timing, gc, and RSS accumulators."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.timings: dict[str, float] = {}
        self.timing_counts: dict[str, int] = {}
        self.gc_seconds: float = 0.0
        self.gc_count: int = 0
        self.rss_baseline: float | None = _read_rss_mb()
        self.rss_max: dict[str, float] = {}
        self.rss_count: dict[str, int] = {}

    def _record_time(self, label: str, secs: float) -> None:
        with self._lock:
            self.timings[label] = self.timings.get(label, 0.0) + secs
            self.timing_counts[label] = self.timing_counts.get(label, 0) + 1

    @contextmanager
    def time(self, label: str):
        t0 = _time_mod.perf_counter()
        try:
            yield
        finally:
            self._record_time(label, _time_mod.perf_counter() - t0)

    def gc(self) -> None:
        """gc.collect() with cumulative timing accounting."""
        t0 = _time_mod.perf_counter()
        gc.collect()
        elapsed = _time_mod.perf_counter() - t0
        with self._lock:
            self.gc_seconds += elapsed
            self.gc_count += 1

    def rss_mark(self, label: str) -> None:
        """Record current RSS under *label*. Keeps max-per-label across calls."""
        rss = _read_rss_mb()
        if rss is None:
            return
        with self._lock:
            prior = self.rss_max.get(label)
            self.rss_max[label] = rss if prior is None else max(prior, rss)
            self.rss_count[label] = self.rss_count.get(label, 0) + 1

    def log_summary(self) -> None:
        # Snapshot under lock, format outside — avoids holding the lock through
        # logger formatting and matches the locking discipline used for writes.
        with self._lock:
            timings = dict(self.timings)
            counts = dict(self.timing_counts)
            gc_secs = self.gc_seconds
            gc_n = self.gc_count
            rss_max = dict(self.rss_max)
            rss_count = dict(self.rss_count)
            baseline = self.rss_baseline

        if timings:
            items = sorted(timings.items(), key=lambda kv: -kv[1])
            parts = [f"{label}={secs:.2f}s/{counts.get(label, 0)}" for label, secs in items]
            parts.append(f"gc={gc_secs:.2f}s/{gc_n}")
            logger.info("GRIB timing: %s", " ".join(parts))

        if rss_max:
            items = sorted(rss_max.items(), key=lambda kv: -kv[1])
            parts = []
            if baseline is not None:
                parts.append(f"baseline={int(baseline)}MB")
            for label, rss in items:
                n = rss_count.get(label, 0)
                if baseline is not None:
                    parts.append(f"{label}={int(rss)}MB(+{int(rss - baseline)}/{n})")
                else:
                    parts.append(f"{label}={int(rss)}MB/{n}")
            logger.info("GRIB RSS: %s", " ".join(parts))


_GRIB_TIMER: contextvars.ContextVar[_GribTimer | None] = contextvars.ContextVar(
    "_grib_timer", default=None,
)


def _timer() -> _GribTimer | None:
    return _GRIB_TIMER.get()


@contextmanager
def _grib_time(label: str):
    """Time a block under the active per-call timer; no-op if none is active."""
    t = _timer()
    if t is None:
        yield
        return
    with t.time(label):
        yield


def _grib_gc() -> None:
    """gc.collect() with cumulative timing accounting (or plain gc.collect if no timer)."""
    t = _timer()
    if t is None:
        gc.collect()
        return
    t.gc()


def _grib_rss_mark(label: str) -> None:
    t = _timer()
    if t is not None:
        t.rss_mark(label)


def _submit_with_context(pool: ThreadPoolExecutor, fn, /, *args, **kwargs):
    """Submit *fn* to *pool* with the caller's ContextVars copied in.

    ``ThreadPoolExecutor.submit`` does **not** propagate contextvars in any
    current Python (verified on 3.13.11: a plain ``pool.submit`` worker sees
    ContextVar defaults, not the caller's bound values; nested submits lose
    them at every level). Without this wrapper, worker threads see
    ``_GRIB_TIMER`` as the default ``None`` and ``_grib_time``/``_grib_gc``
    /``_grib_rss_mark`` calls become silent no-ops — losing the per-fhour
    sub-stage timings the instrumentation exists to capture.

    Apply at *every* ``submit`` site that needs the timer, including nested
    pools (e.g. the inner GFS pool inside an outer Phase-1 worker).
    """
    ctx = contextvars.copy_context()
    return pool.submit(ctx.run, fn, *args, **kwargs)


# ---------------------------------------------------------------------------
# Process-pool dispatcher for GRIB decode (Phase B-3).
#
# GRIB decode (cfgrib + xarray + numpy interp) is GIL-bound — when two
# enrich_forecasts() calls run concurrently in uvicorn, the decode loops in
# their respective threads serialise on the GIL and per-step times balloon
# 3–6× even with multiple cores idle. Dispatching decode to a dedicated
# ProcessPoolExecutor gives each decode its own interpreter, removing the
# contention.
#
# Workers default to 2 (matches today's typical concurrent job count: the
# standalone forecast cycle plus one user refresh). Override via
# ``GRIB_DECODE_WORKERS`` env var. Set to ``0`` to disable the pool entirely
# and decode in-process — useful for tests, profiling, or as a kill switch.
#
# Per-dispatch timeout: ``GRIB_DECODE_TIMEOUT_S`` (default 300 s). A hung
# worker (cfgrib/ECCODES deadlock on a corrupt GRIB) would otherwise block
# its uvicorn thread indefinitely; the timeout converts this to a
# TimeoutError and resets the pool.
# ---------------------------------------------------------------------------

_DECODE_POOL: ProcessPoolExecutor | None = None
_DECODE_POOL_LOCK = threading.Lock()
_DECODE_TIMEOUT_DEFAULT_S = 300.0

# Hang-diag (2026-05-14): trackers used by the timeout-recovery path to
# distinguish among hypothesis classes for the 300 s stuck-decode incidents:
#   - max_tasks_per_child recycle race (CPython internals)
#   - cfgrib mmap deadlock on a file being written/replaced by ECPDS
#   - .idx sidecar race between concurrent decodes
#   - filesystem IO contention from ECPDS dissemination bursts
# Reset whenever the pool is created/torn down. Guarded by the diag lock so
# `_dispatch_decode_parallel` can update from multiple Phase-1 orchestrator
# threads without racing.
_DECODE_POOL_INIT_TIME: float | None = None
_DECODE_TASKS_DISPATCHED: int = 0
_DECODE_DIAG_LOCK = threading.Lock()


def _diag_reset_pool_counters() -> None:
    global _DECODE_POOL_INIT_TIME, _DECODE_TASKS_DISPATCHED
    with _DECODE_DIAG_LOCK:
        _DECODE_POOL_INIT_TIME = _time_mod.time()
        _DECODE_TASKS_DISPATCHED = 0


def _diag_record_dispatch(n: int = 1) -> int:
    global _DECODE_TASKS_DISPATCHED
    with _DECODE_DIAG_LOCK:
        _DECODE_TASKS_DISPATCHED += n
        return _DECODE_TASKS_DISPATCHED


def _diag_pool_summary(pool: ProcessPoolExecutor | None) -> str:
    """One-line summary of pool state for hang-diag log lines."""
    with _DECODE_DIAG_LOCK:
        init_t = _DECODE_POOL_INIT_TIME
        tasks = _DECODE_TASKS_DISPATCHED
    age = (_time_mod.time() - init_t) if init_t is not None else -1.0
    worker_pids: list[int] = []
    try:
        # ``ProcessPoolExecutor._processes`` is a private dict[pid, Process].
        # Stable across the CPython versions we run; the only practical way
        # to get worker PIDs from a stdlib pool.
        if pool is not None and hasattr(pool, "_processes"):
            worker_pids = sorted(pool._processes.keys())
    except Exception:
        pass
    return f"pool_age={age:.0f}s tasks_dispatched={tasks} workers={worker_pids}"


def _diag_snapshot_workers(
    pool: ProcessPoolExecutor | None,
    *,
    hang_context: str,
) -> None:
    """Dump diagnostic info about (presumed-hung) worker processes.

    Called from timeout-recovery paths **before** ``shutdown_decode_pool(wait=False)``.
    Best-effort — each step is wrapped so a broken /proc or missing capability
    can't turn a degraded request into a 500. Order is:

    1. Per-worker ``/proc/<pid>/{wchan,status,syscall,stack,fd}`` snapshot.
       ``wchan`` tells us the kernel function the worker is sleeping in
       (e.g. ``wait_on_page_bit_killable`` → mmap stall; ``futex_wait_queue`` →
       Python-level lock; empty → running).
    2. ``SIGUSR1`` to each worker — paired with the faulthandler registered
       in ``_worker_init``, this dumps the worker's Python stack to stderr
       (which goes to the container log). Useless if the worker is in ``D``
       state, but ``wchan`` already covers that case.
    3. ECMWF directory snapshot: file count + count of files mtime'd in the
       last 60 s. Confirms or refutes the "ECPDS write burst during hang"
       correlation that motivated this investigation.
    4. Brief sleep to let the SIGUSR1 dumps land in container logs before
       ``shutdown(wait=False)`` closes worker stdout.
    """
    try:
        if pool is None or not hasattr(pool, "_processes"):
            logger.error("GRIB hang diag [%s]: no pool to inspect", hang_context)
            return
        worker_pids = sorted(pool._processes.keys())
    except Exception as e:
        logger.error("GRIB hang diag: cannot enumerate workers: %s", e)
        return

    logger.error(
        "GRIB hang diag [%s]: %s",
        hang_context, _diag_pool_summary(pool),
    )

    for pid in worker_pids:
        # /proc text files — wchan/status/syscall are world-readable; stack
        # often needs CAP_SYS_PTRACE (docker default denies). Try anyway and
        # log the failure so we know whether to add the capability.
        for fname in ("wchan", "status", "syscall", "stack"):
            try:
                content = Path(f"/proc/{pid}/{fname}").read_text(errors="replace").strip()
                # Status is multi-line; first 4 lines (Name/Umask/State/Tgid)
                # are enough to spot D-state. wchan/syscall/stack: cap at 800
                # chars so a deep stack doesn't dominate the log.
                if fname == "status":
                    content = "\n".join(content.splitlines()[:4])
                logger.error(
                    "GRIB hang diag [pid=%d] %s: %s",
                    pid, fname, content[:800],
                )
            except OSError as e:
                logger.error(
                    "GRIB hang diag [pid=%d] %s: read failed (%s)",
                    pid, fname, e,
                )

        # Open fds — the GRIB file the worker is blocked on should appear here.
        try:
            fd_dir = Path(f"/proc/{pid}/fd")
            entries: list[str] = []
            for fd in sorted(fd_dir.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 9999):
                try:
                    entries.append(f"{fd.name}->{os.readlink(fd)}")
                except OSError:
                    pass
            logger.error(
                "GRIB hang diag [pid=%d] fds: %s",
                pid, "; ".join(entries[:30]),
            )
        except OSError as e:
            logger.error(
                "GRIB hang diag [pid=%d] fds: list failed (%s)",
                pid, e,
            )

        # SIGUSR1 → faulthandler dumps Python stack of all threads to stderr.
        # Delivered when worker returns to userspace; if it's in D state this
        # is a no-op until/unless the kernel wait completes.
        try:
            os.kill(pid, signal.SIGUSR1)
            logger.error(
                "GRIB hang diag [pid=%d] sent SIGUSR1 (Python stack dump should follow)",
                pid,
            )
        except OSError as e:
            logger.error(
                "GRIB hang diag [pid=%d] SIGUSR1 failed (%s)",
                pid, e,
            )

    # Per-worker memory snapshot (G): catches a worker that's bloated against
    # the cgroup limit and likely thrashing page cache.
    for pid in worker_pids:
        try:
            statm = Path(f"/proc/{pid}/statm").read_text().strip().split()
            # statm columns are in pages — convert via PAGESIZE. Cols: size,
            # resident, shared, text, lib(=0), data, dt(=0).
            pagesize = os.sysconf("SC_PAGE_SIZE")
            rss_mb = int(statm[1]) * pagesize / (1024 * 1024)
            size_mb = int(statm[0]) * pagesize / (1024 * 1024)
            logger.error(
                "GRIB hang diag [pid=%d] memory: rss=%.0fMB size=%.0fMB",
                pid, rss_mb, size_mb,
            )
        except (OSError, ValueError, IndexError) as e:
            logger.error("GRIB hang diag [pid=%d] memory: read failed (%s)", pid, e)

    # Filesystem snapshot (F): walk all GRIB-relevant dirs. ECMWF is ECPDS-
    # delivered (third-party writer); GFS/ICON live under DATA_DIR/.cache/grib
    # (our own writes, atomic via put_cached). Both today's ICON cloud-diag
    # hang and the ECMWF stuck events need coverage here.
    _diag_snapshot_dirs()

    # Process/cgroup memory + meminfo snapshot (G): catches container-wide
    # memory pressure that would force mmap reads to page-evict and stall.
    _diag_snapshot_memory()

    # Pool internals snapshot (H): catches a broken ProcessPoolExecutor where
    # the parent's queue-management thread has died or the IPC queues have
    # backed up — explains a hung first-job-on-fresh-pool (rules out cfgrib
    # at the file level).
    _diag_snapshot_pool_internals(pool)

    # Let the SIGUSR1 stack dumps land in container logs before the pool
    # shutdown closes worker stdout. 1 s is plenty for faulthandler write.
    try:
        _time_mod.sleep(1.0)
    except Exception:
        pass


def _diag_snapshot_dirs() -> None:
    """Walk both ECMWF dir and DATA_DIR/.cache/grib; log file count + recent.

    Recent = mtime within last 60 s — catches an active ECPDS push or a
    cache write in progress at hang time. Capped at 10 sampled names per
    dir so a chatty cache doesn't dominate the log.
    """
    dirs: list[tuple[str, Path]] = []
    try:
        dirs.append(("ecmwf", Path(os.environ.get("ECMWF_GRIB_DIR", "/data/ecmwf"))))
    except Exception:
        pass
    try:
        data_dir = Path(os.environ.get("DATA_DIR", "/app/data"))
        cache_root = data_dir / ".cache" / "grib"
        if cache_root.exists():
            # Each model has its own subdir tree — walk them all.
            for model_dir in cache_root.iterdir():
                if model_dir.is_dir():
                    dirs.append((f"cache/{model_dir.name}", model_dir))
    except Exception:
        pass

    now = _time_mod.time()
    for label, d in dirs:
        try:
            total = 0
            recent: list[tuple[float, str]] = []
            # rglob, not iterdir — cache dirs have a {model}/{init}z/ layout.
            for p in d.rglob("*"):
                if not p.is_file():
                    continue
                try:
                    mt = p.stat().st_mtime
                except OSError:
                    continue
                total += 1
                age = now - mt
                if age < 60.0:
                    recent.append((age, p.name))
            recent.sort()
            sample = ", ".join(f"{n}(@{a:.1f}s)" for a, n in recent[:10])
            logger.error(
                "GRIB hang diag fs[%s]: %s has %d files; %d mtime'd <60s ago; recent: %s",
                label, d, total, len(recent), sample,
            )
        except Exception as e:
            logger.error("GRIB hang diag fs[%s]: snapshot failed: %s", label, e)


def _diag_snapshot_memory() -> None:
    """Container-wide memory snapshot at hang time.

    Reads /proc/meminfo (host kernel view), /proc/self/status (parent VmRSS
    family), and cgroup v2 memory files (memory.current / max / swap.current).
    All best-effort — cgroup paths differ between v1 and v2 and may be absent.
    """
    # /proc/meminfo — key fields only.
    try:
        meminfo = Path("/proc/meminfo").read_text()
        wanted = {"MemTotal", "MemFree", "MemAvailable", "Cached", "Buffers", "Slab", "SwapFree"}
        kv: dict[str, str] = {}
        for line in meminfo.splitlines():
            key, _, rest = line.partition(":")
            if key in wanted:
                kv[key] = rest.strip()
        logger.error(
            "GRIB hang diag meminfo: %s",
            " ".join(f"{k}={v}" for k, v in kv.items()),
        )
    except OSError as e:
        logger.error("GRIB hang diag meminfo: read failed (%s)", e)

    # Parent process VmRSS / VmSize / VmPeak / VmSwap.
    try:
        status = Path("/proc/self/status").read_text()
        wanted_vm = {"VmPeak", "VmSize", "VmRSS", "VmHWM", "VmSwap", "Threads"}
        kv = {}
        for line in status.splitlines():
            key, _, rest = line.partition(":")
            if key in wanted_vm:
                kv[key] = rest.strip()
        logger.error(
            "GRIB hang diag parent: %s",
            " ".join(f"{k}={v}" for k, v in kv.items()),
        )
    except OSError as e:
        logger.error("GRIB hang diag parent status: read failed (%s)", e)

    # cgroup memory — v2 unified path is the prod target. v1 fallback for
    # completeness but unlikely to fire on the current droplet.
    for cg_label, cg_path in (
        ("v2.current", "/sys/fs/cgroup/memory.current"),
        ("v2.max", "/sys/fs/cgroup/memory.max"),
        ("v2.swap.current", "/sys/fs/cgroup/memory.swap.current"),
        ("v2.peak", "/sys/fs/cgroup/memory.peak"),
        ("v1.usage", "/sys/fs/cgroup/memory/memory.usage_in_bytes"),
        ("v1.limit", "/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ):
        try:
            val = Path(cg_path).read_text().strip()
            logger.error("GRIB hang diag cgroup[%s]: %s = %s", cg_label, cg_path, val)
        except OSError:
            # Silent: most paths won't exist on a given kernel/cgroup version.
            pass


def _diag_snapshot_pool_internals(pool: ProcessPoolExecutor | None) -> None:
    """Peek at private ProcessPoolExecutor state.

    These attributes are technically internal but have been stable across the
    CPython versions we run. If any disappears in a future Python release,
    the try/except keeps the diagnostic best-effort.

    What we want to see:
      - ``_pending_work_items`` size: jobs submitted but never started/finished.
      - executor manager thread ``is_alive()``: parent's queue-management
        thread (lives in concurrent.futures.process). Dead → the pool can
        accept submit() but never make progress. Attribute name changed
        from ``_queue_management_thread`` (≤3.10) to ``_executor_manager_thread``
        (3.11+); we try both so the diag works across versions.
      - ``_call_queue.qsize()`` / ``_result_queue.qsize()``: pipe depth from
        parent → workers and workers → parent. Backed up → IPC stall.
    """
    if pool is None:
        return
    try:
        pending = pool._pending_work_items  # type: ignore[attr-defined]
        logger.error(
            "GRIB hang diag pool: pending_work_items=%d", len(pending),
        )
    except Exception as e:
        logger.error("GRIB hang diag pool: pending_work_items read failed (%s)", e)

    thr = None
    thr_attr = None
    for attr in ("_executor_manager_thread", "_queue_management_thread"):
        try:
            thr = getattr(pool, attr)
        except AttributeError:
            continue
        thr_attr = attr
        break
    if thr_attr is None:
        logger.error(
            "GRIB hang diag pool: manager thread attr not found (tried "
            "_executor_manager_thread, _queue_management_thread)",
        )
    else:
        try:
            alive = thr.is_alive() if thr is not None else None
            logger.error(
                "GRIB hang diag pool: %s alive=%s name=%s",
                thr_attr, alive, getattr(thr, "name", "?"),
            )
        except Exception as e:
            logger.error("GRIB hang diag pool: %s read failed (%s)", thr_attr, e)

    for q_name in ("_call_queue", "_result_queue"):
        try:
            q = getattr(pool, q_name)
            qsize = q.qsize() if q is not None else -1
            logger.error("GRIB hang diag pool: %s qsize=%d", q_name, qsize)
        except (NotImplementedError, AttributeError) as e:
            # qsize() is unimplemented on macOS for multiprocessing.Queue —
            # not an issue on the Linux container but harmless to skip.
            logger.error("GRIB hang diag pool: %s qsize unavailable (%s)", q_name, e)
        except Exception as e:
            logger.error("GRIB hang diag pool: %s qsize read failed (%s)", q_name, e)


def _decode_pool_workers_default() -> int:
    """Default worker count when ``GRIB_DECODE_WORKERS`` is unset.

    Default of 2: gives a meaningful speed-up over the sequential-dispatch
    bug (issue #133) while keeping peak RSS bounded. Each ICON decode
    worker holds ~125 MB during decode, so 2 workers ≈ +125 MB extra peak
    vs. the prior single-worker behaviour. Raise via ``GRIB_DECODE_WORKERS``
    on hosts with spare RAM (the prod droplet has 4 vCPU but only 4 GiB
    cgroup, and concurrent refreshes already compete for it).
    """
    return 2


def _decode_pool_workers() -> int:
    raw = os.environ.get("GRIB_DECODE_WORKERS", "").strip()
    if not raw:
        return _decode_pool_workers_default()
    try:
        return max(0, int(raw))
    except ValueError:
        default = _decode_pool_workers_default()
        logger.warning(
            "Invalid GRIB_DECODE_WORKERS=%r, defaulting to %d", raw, default,
        )
        return default


def _decode_pool_max_tasks_per_child() -> int | None:
    """Worker recycle threshold; ``None`` disables recycling.

    cfgrib/ECCODES allocate native memory that Python GC cannot reclaim;
    recycling caps cumulative growth across many decodes (e.g. the standalone
    ECMWF fetch dispatches ~50 decodes per run). 0 or negative means disabled.
    """
    raw = os.environ.get("GRIB_DECODE_MAX_TASKS_PER_CHILD", "50").strip()
    try:
        v = int(raw)
        return v if v > 0 else None
    except ValueError:
        logger.warning(
            "Invalid GRIB_DECODE_MAX_TASKS_PER_CHILD=%r, defaulting to 50", raw,
        )
        return 50


def _decode_timeout_s() -> float:
    raw = os.environ.get("GRIB_DECODE_TIMEOUT_S", "").strip()
    if not raw:
        return _DECODE_TIMEOUT_DEFAULT_S
    try:
        v = float(raw)
        return v if v > 0 else _DECODE_TIMEOUT_DEFAULT_S
    except ValueError:
        logger.warning(
            "Invalid GRIB_DECODE_TIMEOUT_S=%r, defaulting to %.0fs",
            raw, _DECODE_TIMEOUT_DEFAULT_S,
        )
        return _DECODE_TIMEOUT_DEFAULT_S


def _get_decode_pool() -> ProcessPoolExecutor | None:
    """Lazy singleton process pool. Returns None when pool is disabled."""
    global _DECODE_POOL
    if _DECODE_POOL is not None:
        return _DECODE_POOL
    workers = _decode_pool_workers()
    if workers == 0:
        return None
    with _DECODE_POOL_LOCK:
        if _DECODE_POOL is not None:
            return _DECODE_POOL
        # Spawn (not fork) for macOS/Linux parity and to avoid inheriting
        # the parent's threads, locks, and open file handles. cfgrib does
        # its own ECCODES setup that isn't fork-safe under load.
        ctx = multiprocessing.get_context("spawn")
        from weatherbrief.fetch.grib.decode_worker import _worker_init
        max_tasks = _decode_pool_max_tasks_per_child()
        kwargs: dict[str, Any] = {
            "max_workers": workers,
            "mp_context": ctx,
            "initializer": _worker_init,
        }
        if max_tasks is not None:
            kwargs["max_tasks_per_child"] = max_tasks
        _DECODE_POOL = ProcessPoolExecutor(**kwargs)
        _diag_reset_pool_counters()
        logger.info(
            "GRIB decode pool started (workers=%d, mp=spawn, max_tasks_per_child=%s)",
            workers, max_tasks if max_tasks is not None else "off",
        )
    return _DECODE_POOL


def shutdown_decode_pool(*, wait: bool = True) -> None:
    """Shut down the decode pool. Safe to call repeatedly; idempotent.

    ``wait=False`` is for the hung-worker recovery path: a worker stuck
    in cfgrib/ECCODES will never return, so ``shutdown(wait=True)``
    would block forever. Trade-off: the orphaned worker process is left
    behind and reaped when the parent eventually exits — bounded leak
    (at most ``max_workers`` orphans per pool reset).
    """
    global _DECODE_POOL, _DECODE_POOL_INIT_TIME, _DECODE_TASKS_DISPATCHED
    with _DECODE_POOL_LOCK:
        pool = _DECODE_POOL
        _DECODE_POOL = None
    if pool is not None:
        pool.shutdown(wait=wait, cancel_futures=not wait)
        logger.info(
            "GRIB decode pool shut down (wait=%s, %s)",
            wait, _diag_pool_summary(pool),
        )
        # Counters belong to a specific pool lifetime — clear them so the
        # next `_diag_pool_summary` (after `_get_decode_pool` rebuilds) shows
        # a fresh `pool_age` instead of one from the dead pool.
        with _DECODE_DIAG_LOCK:
            _DECODE_POOL_INIT_TIME = None
            _DECODE_TASKS_DISPATCHED = 0


def _dispatch_decode(worker_fn_name: str, *args) -> Any:
    """Submit a decode job to the pool (or run in-process if disabled).

    Pool is the default; the in-process fallback exists for tests and as
    a kill switch via ``GRIB_DECODE_WORKERS=0``. Both paths return the
    raw decode result; timing/RSS observability stays on the parent-side
    ``_grib_time`` wraps that surround each call site.

    Two new failure modes vs in-process decode, both reset the pool so
    the next dispatch starts fresh — without this, one bad worker would
    poison every subsequent request until app restart:

    - ``BrokenProcessPool``: worker SIGKILL/OOM/cfgrib segfault. Workers
      are dead, so ``shutdown(wait=True)`` returns fast.
    - ``TimeoutError``: ``GRIB_DECODE_TIMEOUT_S`` (default 300 s)
      exceeded — likely a cfgrib/ECCODES deadlock on a corrupt GRIB.
      Workers are still alive but stuck, so we tear down with
      ``wait=False`` to avoid hanging the recovery path itself.
    """
    from weatherbrief.fetch.grib import decode_worker
    fn = getattr(decode_worker, worker_fn_name)
    pool = _get_decode_pool()
    if pool is None:
        return fn(*args)
    timeout = _decode_timeout_s()
    try:
        future = pool.submit(fn, *args)
        _diag_record_dispatch(1)
        return future.result(timeout=timeout)
    except BrokenProcessPool:
        logger.error(
            "GRIB decode pool broken (worker died); resetting for next call (%s)",
            _diag_pool_summary(pool),
        )
        shutdown_decode_pool()
        raise
    except TimeoutError:
        logger.error(
            "GRIB decode pool stuck (worker hung %.0fs on %s); resetting",
            timeout, worker_fn_name,
        )
        _diag_snapshot_workers(pool, hang_context=f"single:{worker_fn_name}")
        shutdown_decode_pool(wait=False)
        raise


def _dispatch_decode_parallel(jobs: list[tuple[str, tuple]]) -> list[Any]:
    """Submit a batch of decode jobs to the pool concurrently.

    Each job is ``(worker_fn_name, args_tuple)``; results are returned in the
    same order. Falls back to sequential in-process execution when the pool
    is disabled (``GRIB_DECODE_WORKERS=0``) so the call sites stay
    correctness-equivalent in tests / kill-switch scenarios.

    Why this exists: ``_dispatch_decode`` submits one job and waits for its
    result, so a sequential ``for`` loop of ``_dispatch_decode`` calls keeps
    only one pool worker busy at a time even when the pool has more. Fanning
    out the whole batch and harvesting results lets the worker count actually
    matter (issue #133).

    Failure modes mirror :func:`_dispatch_decode` — ``BrokenProcessPool`` and
    ``TimeoutError`` reset the pool. A single deadline applies across the
    batch: if the slowest job exceeds ``GRIB_DECODE_TIMEOUT_S``, we tear
    down. Any worker exception (including non-fatal ones like cfgrib parse
    errors) cancels not-yet-started jobs so the pool isn't left burning
    cycles on results no caller will read.
    """
    if not jobs:
        return []

    from weatherbrief.fetch.grib import decode_worker

    pool = _get_decode_pool()
    if pool is None:
        return [getattr(decode_worker, name)(*args) for name, args in jobs]

    timeout = _decode_timeout_s()
    deadline = _time_mod.perf_counter() + timeout

    # Keep ``pool.submit`` inside the try so a ``BrokenProcessPool`` raised
    # mid-batch (worker died between submits) still tears down the pool
    # global — otherwise every subsequent dispatch keeps hitting the same
    # broken pool until the process restarts.
    futures: list[Future[Any]] = []
    results: list[Any] = [None] * len(jobs)
    try:
        futures = [
            pool.submit(getattr(decode_worker, name), *args)
            for name, args in jobs
        ]
        _diag_record_dispatch(len(jobs))
        for i, fut in enumerate(futures):
            remaining = max(0.0, deadline - _time_mod.perf_counter())
            results[i] = fut.result(timeout=remaining)
    except BrokenProcessPool:
        for fut in futures:
            fut.cancel()
        logger.error(
            "GRIB decode pool broken during parallel dispatch (%d jobs); resetting (%s)",
            len(jobs), _diag_pool_summary(pool),
        )
        shutdown_decode_pool()
        raise
    except TimeoutError:
        # Note which futures finished before deadline vs are stuck — narrows
        # whether the whole pool stalled or just a single job dragged the
        # shared deadline past.
        done = sum(1 for f in futures if f.done())
        cancelled = 0
        for fut in futures:
            if fut.cancel():
                cancelled += 1
        names = ",".join({name for name, _ in jobs})
        logger.error(
            "GRIB decode pool stuck (%d jobs %s exceeded %.0fs; done=%d cancelled=%d); resetting",
            len(jobs), names, timeout, done, cancelled,
        )
        _diag_snapshot_workers(pool, hang_context=f"parallel:{names}({len(jobs)}j)")
        shutdown_decode_pool(wait=False)
        raise
    except Exception:
        # Non-fatal worker exception (e.g. cfgrib parse error). Cancel
        # pending futures so the pool doesn't keep decoding results the
        # caller will never read, but leave the pool itself intact — the
        # workers are still healthy.
        for fut in futures:
            fut.cancel()
        raise
    return results


def _grib_session() -> requests.Session:
    """Create a requests session with a connection pool sized for parallel GRIB downloads."""
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=_POOL_MAXSIZE)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _apply_cloud_diagnostics(hourly: HourlyForecast, diag: NWPCloudDiagnostics) -> None:
    """Attach NWP cloud diagnostics. Open-Meteo cloud_cover_*_pct fields are
    preserved — they provide hourly-interpolated coverage that is more temporally
    accurate than forward-filled GRIB values on non-native hours."""
    hourly.nwp_cloud_diagnostics = diag
    # ECMWF deg0l: model-native freezing level overrides Open-Meteo's value.
    if diag.freezing_level_ft is not None:
        hourly.freezing_level_m = diag.freezing_level_ft / _M_TO_FT


def _forecast_hour_to_utc(init_date: str, init_hour: int, fhour: int) -> datetime:
    """Convert a GRIB run + forecast hour to an aware UTC datetime."""
    init_dt = datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc,
    )
    return init_dt + timedelta(hours=fhour)


def _run_info_to_timestamp(init_date: str, init_hour: int) -> int:
    """Convert GRIB run info (date string + hour) to a Unix timestamp."""
    return int(
        datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


def _matches_valid_time(hourly_time: datetime, valid_utc: datetime | None) -> bool:
    """Check if a forecast hourly entry matches the target valid time.

    Compares both date and hour to avoid cross-day collisions when GRIB
    steps span multiple days (e.g. ECMWF steps out to 192h).
    """
    if valid_utc is None:
        return True
    return hourly_time.date() == valid_utc.date() and hourly_time.hour == valid_utc.hour


def enrich_forecasts(
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    departure_time: datetime,
    *,
    data_dir: Path,
    flight_duration_hours: float = 0.0,
    progress_callback: Callable[[str, str | None], None] | None = None,
    as_of_time: datetime | None = None,
) -> tuple[dict[str, int], dict[str, str]]:
    """Enrich cross-section forecasts with cloud water from GRIB2 sources.

    Enriches GFS cross-sections with CLWMR/ICMR and cloud diagnostics.
    Enriches ICON cross-sections with QC/QI if route is within ICON-EU domain.

    This modifies PressureLevelData and HourlyForecast objects in-place.

    Args:
        cross_sections: Route cross-sections to enrich (modified in-place).
        all_forecasts: Waypoint forecasts (also enriched in-place).
        route_points: Route points for spatial interpolation.
        departure_time: Aware UTC datetime of flight departure.
        data_dir: Base data directory for caching.
        flight_duration_hours: Flight duration for per-hour enrichment.
        as_of_time: If set, only use model runs initialized before this time.

    Returns:
        Tuple of (grib_init_times, grib_skip_reasons):
        - grib_init_times: model name → GRIB init Unix timestamp.
        - grib_skip_reasons: model name → skip reason string (e.g. "out_of_range").
    """
    grib_init_times: dict[str, int] = {}
    grib_skip_reasons: dict[str, str] = {}

    timer = _GribTimer()
    token = _GRIB_TIMER.set(timer)
    try:
        return _enrich_forecasts_inner(
            timer, cross_sections, all_forecasts, route_points,
            departure_time, data_dir=data_dir,
            flight_duration_hours=flight_duration_hours,
            progress_callback=progress_callback,
            as_of_time=as_of_time,
        )
    finally:
        timer.log_summary()
        _GRIB_TIMER.reset(token)


def _enrich_forecasts_inner(
    timer: _GribTimer,
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    departure_time: datetime,
    *,
    data_dir: Path,
    flight_duration_hours: float = 0.0,
    progress_callback: Callable[[str, str | None], None] | None = None,
    as_of_time: datetime | None = None,
) -> tuple[dict[str, int], dict[str, str]]:
    """Inner body of enrich_forecasts; assumes _GRIB_TIMER is set to *timer*."""
    grib_init_times: dict[str, int] = {}
    grib_skip_reasons: dict[str, str] = {}

    timer.rss_mark("enrich_start")

    # Download GFS and ICON-EU in parallel (network-bound), but decode
    # sequentially (memory-bound). ICON-EU decode peaks at ~270MB per
    # variable; overlapping with GFS decode caused OOM.
    # ECMWF GRIB is local disk I/O — runs in parallel with network fetches.
    if progress_callback is not None:
        progress_callback("grib_enrichment", None)

    gfs_ts: int | None = None
    icon_ts: int | None = None
    icon_skip: str | None = None

    # Prepare ICON-EU context (run discovery, domain check, etc.)
    with _grib_time("icon_prepare"):
        icon_ctx = _prepare_icon_eu(
            cross_sections, route_points, departure_time,
            data_dir=data_dir,
            flight_duration_hours=flight_duration_hours,
            as_of_time=as_of_time,
        )

    # Phase 1: Download/decode in parallel — GFS (download+decode),
    # ICON-EU (download-only), ECMWF GRIB (local disk decode).
    # Workers must run inside the parent's contextvars copy so timing/RSS
    # marks land on this call's timer, not on whichever refresh happens to
    # be running in the sibling pipeline thread.
    with _grib_time("phase1_parallel"):
        with ThreadPoolExecutor(max_workers=3) as pool:
            gfs_future = _submit_with_context(
                pool, _enrich_gfs,
                cross_sections, all_forecasts, route_points,
                departure_time, data_dir=data_dir,
                flight_duration_hours=flight_duration_hours,
                as_of_time=as_of_time,
            )
            ecmwf_future = _submit_with_context(
                pool, _enrich_ecmwf,
                cross_sections, all_forecasts, route_points,
                departure_time,
                flight_duration_hours=flight_duration_hours,
                as_of_time=as_of_time,
            )
            if icon_ctx is not None:
                icon_dl_future = _submit_with_context(
                    pool, _prefetch_icon_eu_data, icon_ctx,
                )
            else:
                icon_dl_future = None

            gfs_ts = gfs_future.result()
            ecmwf_grib_ts = ecmwf_future.result()
            if icon_dl_future is not None:
                icon_dl_future.result()  # ensure downloads are cached
    timer.rss_mark("after_phase1")

    if ecmwf_grib_ts is not None:
        grib_init_times["ecmwf"] = ecmwf_grib_ts

    # Phase 2: Decode ICON-EU sequentially (memory-heavy, GFS is done).
    with _grib_time("phase2_icon_decode"):
        if icon_ctx is not None:
            icon_ts, icon_skip = _decode_and_merge_icon_eu(
                icon_ctx, cross_sections, all_forecasts, route_points,
            )
        else:
            icon_skip = icon_ctx  # None
    timer.rss_mark("after_phase2")

    if gfs_ts is not None:
        grib_init_times["gfs"] = gfs_ts
    if icon_ts is not None:
        grib_init_times["icon"] = icon_ts
    elif icon_skip is not None:
        grib_skip_reasons["icon"] = icon_skip

    # Time-axis fill of all GRIB-enriched fields onto interpolated hours.
    # When the GFS init is known, the cloud-diag pass uses window-midpoint
    # interpolation for low/mid/high cover (issue #148 — averaged-window
    # phantom-layer fix) and the gate drops layers unsupported by RH or
    # condensate. Both are no-ops in the GFS=None fallback.
    gfs_init_dt: datetime | None = None
    if gfs_ts is not None:
        gfs_init_dt = datetime.fromtimestamp(gfs_ts, tz=timezone.utc)
    with _grib_time("propagate_all"):
        from weatherbrief.fetch.grib.fill import (
            apply_gfs_rh_condensate_gate,
            propagate_all,
        )
        propagate_all(cross_sections, all_forecasts, gfs_init=gfs_init_dt)
        apply_gfs_rh_condensate_gate(cross_sections, all_forecasts)

    timer.rss_mark("enrich_end")
    return grib_init_times, grib_skip_reasons


# ---------------------------------------------------------------------------
# ECMWF GRIB enrichment (local disk)
# ---------------------------------------------------------------------------


def _enrich_ecmwf(
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    departure_time: datetime,
    *,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
    ecmwf_data_dir: Path | None = None,
) -> int | None:
    """Enrich ECMWF cross-sections with GRIB pressure-level and surface data.

    Reads ECMWF GRIB files from the ECPDS delivery directory. Uses
    ``ecmwf_data_dir`` if provided, otherwise falls back to the
    ``ECMWF_GRIB_DIR`` env var (separate from the shared ``data_dir``
    because ECMWF data is delivered to its own volume).

    Returns:
        GRIB init Unix timestamp, or None if no ECMWF data found.
    """
    with _grib_time("ecmwf_total"):
        return _enrich_ecmwf_inner(
            cross_sections, all_forecasts, route_points,
            departure_time,
            flight_duration_hours=flight_duration_hours,
            as_of_time=as_of_time,
            ecmwf_data_dir=ecmwf_data_dir,
        )


def _enrich_ecmwf_inner(
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    departure_time: datetime,
    *,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
    ecmwf_data_dir: Path | None = None,
) -> int | None:
    from weatherbrief.fetch.grib.decode import (
        build_ecmwf_cloud_diagnostics,
        build_ecmwf_surface_snapshot,
    )
    from weatherbrief.fetch.grib.ecmwf_fetch import (
        ecmwf_grib_dir,
        find_best_ecmwf_run,
        scan_ecmwf_files,
    )

    # Only enrich ECMWF model cross-sections
    ecmwf_sections = [cs for cs in cross_sections if cs.model == ModelSource.ECMWF]
    if not ecmwf_sections:
        return None

    grib_dir = ecmwf_data_dir or ecmwf_grib_dir()
    with _grib_time("ecmwf_scan"):
        all_files = scan_ecmwf_files(grib_dir)
    if not all_files:
        logger.info("No ECMWF GRIB data available in %s", grib_dir)
        return None

    # Filter to runs initialized before as_of_time (replay/backtest support)
    if as_of_time is not None:
        all_files = [f for f in all_files if f.base_time <= as_of_time]
        if not all_files:
            logger.info("No ECMWF GRIB runs before as_of_time=%s", as_of_time)
            return None

    # Pick the best run — prefers latest, but falls back to an earlier
    # run with a longer observed horizon when the latest can't cover the
    # full flight window (e.g. a 06/18z short-cutoff run vs an earlier 00/12z).
    flight_end = departure_time + timedelta(hours=max(flight_duration_hours, 1))
    run_files = find_best_ecmwf_run(
        all_files, cover_until=flight_end, data_dir=grib_dir,
    )
    if not run_files:
        logger.info("ECMWF GRIB: no suitable run found")
        return None
    latest_bt = run_files[0].base_time

    # Group files by step_hours, separate a1 (surface) and a2 (pressure)
    files_by_step: dict[int, dict[str, Path]] = {}
    for f in run_files:
        part = "a2" if f.is_pressure_level else "a1" if f.is_surface else None
        if part is not None:
            files_by_step.setdefault(f.step_hours, {})[part] = f.path

    # Compute which forecast steps cover the flight window
    flight_start = departure_time
    flight_end = departure_time + timedelta(hours=max(flight_duration_hours, 1))

    point_lats = [rp.lat for rp in route_points]
    point_lons = [rp.lon for rp in route_points]

    # State for step-difference of accumulated surface fields (tp, sf) across
    # consecutive a1 files. None = no prior step seen yet for that point.
    n_points = len(route_points)
    prev_tp_per_point: list[float | None] = [None] * n_points
    prev_sf_per_point: list[float | None] = [None] * n_points
    prev_a1_valid_utc: datetime | None = None

    # Filter steps to the flight window (with margin) up-front so we can
    # fan out all decodes in parallel before merging.
    margin = timedelta(hours=3)
    window_steps: list[tuple[int, dict[str, Path], datetime]] = []
    for step_hours, parts in sorted(files_by_step.items()):
        valid_time = latest_bt + timedelta(hours=step_hours)
        if valid_time < flight_start - margin or valid_time > flight_end + margin:
            continue
        window_steps.append((step_hours, parts, valid_time))

    # Build the parallel job batch: one a2 (pressure) and/or one a1 (surface)
    # decode per step. Each step's two decodes are independent of each other,
    # and steps are independent of each other — fan the whole batch out so the
    # decode pool's workers actually parallelise (issue #133).
    job_keys: list[tuple[str, int]] = []  # ("a1"|"a2", step_hours)
    job_list: list[tuple[str, tuple]] = []
    for step_hours, parts, _ in window_steps:
        if "a2" in parts:
            job_keys.append(("a2", step_hours))
            job_list.append((
                "decode_ecmwf_pressure",
                (str(parts["a2"]), point_lats, point_lons),
            ))
        if "a1" in parts:
            job_keys.append(("a1", step_hours))
            job_list.append((
                "decode_ecmwf_surface",
                (str(parts["a1"]), point_lats, point_lons),
            ))

    if job_list:
        with _grib_time("ecmwf_decode_parallel"):
            raw_results = _dispatch_decode_parallel(job_list)
    else:
        raw_results = []

    a2_results: dict[int, tuple[list[dict[int, dict[str, float]]], list[bool]]] = {}
    a1_results: dict[int, tuple[list[dict[str, float]], list[bool]]] = {}
    for (kind, step_hours), result in zip(job_keys, raw_results):
        if kind == "a2":
            a2_results[step_hours] = result
        else:
            a1_results[step_hours] = result

    enriched_steps = 0
    for step_hours, parts, valid_time in window_steps:
        # Pressure levels (a2) — merge in step order. ``pop`` drops the
        # decoded data from the dict immediately so each step's RSS is
        # reclaimable during the rest of the merge loop, mirroring the
        # per-fhour cleanup in the ICON path. Without this, all decoded
        # steps stay resident until function exit.
        if step_hours in a2_results:
            with _grib_time("ecmwf_a2_merge"):
                pl_data, pl_covered = a2_results.pop(step_hours)
                # Only merge covered points — set uncovered to empty
                for i, cov in enumerate(pl_covered):
                    if not cov:
                        pl_data[i] = {}

                replaced = _replace_pressure_levels_from_grib(
                    ecmwf_sections, all_forecasts, route_points,
                    pl_data, valid_utc=valid_time,
                )
                if replaced > 0:
                    enriched_steps += 1
                del pl_data, pl_covered

        # Surface diagnostics (a1) — must run in step order so the
        # accumulated-precip step-difference state propagates correctly.
        if step_hours in a1_results:
            with _grib_time("ecmwf_a1_merge"):
                sfc_data, sfc_covered = a1_results.pop(step_hours)
                diagnostics = [
                    build_ecmwf_cloud_diagnostics(raw) if cov else None
                    for raw, cov in zip(sfc_data, sfc_covered)
                ]
                _apply_cloud_diagnostics_to_sections(
                    ecmwf_sections, all_forecasts, route_points,
                    diagnostics, "ecmwf", valid_utc=valid_time,
                )
                # Surface scalars (T/dewpoint, wind/gust, vis, CAPE, MSLP, precip,
                # snow) onto HourlyForecast. Coupled with cloud-diag application —
                # both run together at the same valid_utc so that the forward-fill
                # in fill.py can use ``nwp_cloud_diagnostics is not None`` as the
                # GRIB-anchor detector.
                _apply_ecmwf_surface_to_hourly(
                    ecmwf_sections, all_forecasts, route_points,
                    sfc_data, sfc_covered,
                    valid_utc=valid_time,
                    prev_valid_utc=prev_a1_valid_utc,
                    prev_tp_per_point=prev_tp_per_point,
                    prev_sf_per_point=prev_sf_per_point,
                )
                # Update step-difference state from this step's cumulative values.
                for i, (raw, cov) in enumerate(zip(sfc_data, sfc_covered)):
                    if not cov or not raw:
                        continue
                    tp = raw.get("total_precip_m")
                    if tp is not None:
                        prev_tp_per_point[i] = tp
                    sf = raw.get("snowfall_m_we")
                    if sf is not None:
                        prev_sf_per_point[i] = sf
                prev_a1_valid_utc = valid_time
                del sfc_data, sfc_covered, diagnostics
    _grib_gc()

    if enriched_steps > 0:
        logger.info(
            "ECMWF GRIB full sounding replacement applied (%d steps, base %s)",
            enriched_steps, latest_bt.isoformat(),
        )
        return int(latest_bt.timestamp())

    logger.info("ECMWF GRIB: no matching steps for flight window")
    return None


# ---------------------------------------------------------------------------
# GFS enrichment
# ---------------------------------------------------------------------------


def _enrich_gfs(
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    departure_time: datetime,
    *,
    data_dir: Path,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
) -> int | None:
    """Enrich GFS cross-sections with CLWMR/ICMR and cloud diagnostics.

    Returns the GRIB init Unix timestamp, or None if enrichment was skipped.
    """
    with _grib_time("gfs_total"):
        return _enrich_gfs_inner(
            cross_sections, all_forecasts, route_points,
            departure_time, data_dir=data_dir,
            flight_duration_hours=flight_duration_hours,
            as_of_time=as_of_time,
        )


def _enrich_gfs_inner(
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    departure_time: datetime,
    *,
    data_dir: Path,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
) -> int | None:
    from weatherbrief.fetch.grib.grib_fetch import compute_flight_window_hours

    gfs_sections = [cs for cs in cross_sections if cs.model == ModelSource.GFS]
    if not gfs_sections:
        logger.info("No GFS cross-sections to enrich")
        return None

    session = _grib_session()

    with _grib_time("gfs_find_run"):
        run_info = find_latest_run(departure_time, session=session, as_of_time=as_of_time)
    if run_info is None:
        logger.warning("No GFS model run found for enrichment")
        return None

    init_date, init_hour = run_info
    forecast_hours = compute_flight_window_hours(
        init_date, init_hour, departure_time, flight_duration_hours,
    )

    purge_old_runs(data_dir, model="gfs")
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="gfs")

    point_lats = [rp.lat for rp in route_points]
    point_lons = [rp.lon for rp in route_points]

    # Fetch .idx text (shared by both enrichment paths)
    with _grib_time("gfs_idx_fetch"):
        idx_by_fhour: dict[int, str] = {}
        for fhour in forecast_hours:
            try:
                idx_by_fhour[fhour] = fetch_idx(init_date, init_hour, fhour, session=session)
            except Exception:
                logger.warning("Failed to fetch .idx for f%03d", fhour, exc_info=True)

    if not idx_by_fhour:
        logger.warning("No .idx files retrieved for enrichment")
        return None

    # Run both enrichment paths in parallel — they write to different fields
    # (CLWMR/ICMR on PressureLevelData vs nwp_cloud_diagnostics on HourlyForecast).
    # Use _submit_with_context so per-fhour _grib_time(...) calls in the inner
    # workers see the same _GRIB_TIMER as the outer enrich_forecasts call.
    with _grib_time("gfs_workers"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            _submit_with_context(
                pool, _enrich_clwmr_icmr,
                gfs_sections, all_forecasts, route_points,
                init_date, init_hour, forecast_hours,
                run_dir, idx_by_fhour, point_lats, point_lons, session,
            )
            _submit_with_context(
                pool, _enrich_cloud_diagnostics,
                gfs_sections, all_forecasts, route_points,
                init_date, init_hour, forecast_hours,
                run_dir, idx_by_fhour, point_lats, point_lons, session,
            )
            # ThreadPoolExecutor.__exit__ waits for all futures to complete

    return _run_info_to_timestamp(init_date, init_hour)


def _fetch_clwmr_icmr_for_fhour(
    init_date: str,
    init_hour: int,
    fhour: int,
    target_levels: list[int],
    run_dir: Path,
    idx_by_fhour: dict[int, str],
    point_lats: list[float],
    point_lons: list[float],
    session: requests.Session,
) -> list[dict[int, dict[str, float]]] | None:
    """Fetch, cache, decode CLWMR/ICMR for a single GFS forecast hour.

    Decode runs in the worker pool (Phase B-3). The parent only ensures the
    file is on disk, then hands the path to the worker — bytes never live in
    the parent process for the decode call.
    """
    ck = cache_key(fhour, "CLWMR_ICMR")
    if not is_cached(run_dir, ck):
        idx_text = idx_by_fhour.get(fhour)
        if idx_text is None:
            return None
        try:
            ranges = plan_byte_ranges(idx_text, target_levels=target_levels)
            if not ranges:
                logger.warning("No CLWMR/ICMR found in .idx for f%03d", fhour)
                return None
            with _grib_time("gfs_clwmr_download"):
                grib_bytes = fetch_byte_ranges(
                    init_date, init_hour, fhour, ranges, session=session,
                )
            if not grib_bytes:
                return None
            put_cached(run_dir, ck, grib_bytes)
            logger.info(
                "Downloaded GRIB2 f%03d: %d ranges, %.1f KB",
                fhour, len(ranges), len(grib_bytes) / 1024,
            )
        except Exception:
            logger.warning("Failed to fetch GRIB2 f%03d", fhour, exc_info=True)
            return None

    cache_path = run_dir / ck
    with _grib_time("gfs_clwmr_decode"):
        return _dispatch_decode(
            "decode_gfs_pressure", str(cache_path), point_lats, point_lons,
        )


def _enrich_clwmr_icmr(
    gfs_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    run_dir: Path,
    idx_by_fhour: dict[int, str],
    point_lats: list[float],
    point_lons: list[float],
    session: requests.Session,
) -> None:
    """Enrich pressure-level data with CLWMR/ICMR from GFS GRIB2."""
    # Extract target pressure levels from existing forecasts
    target_levels: list[int] = []
    for cs in gfs_sections:
        for pf in cs.point_forecasts:
            for h in pf.hourly:
                for pl in h.pressure_levels:
                    if pl.pressure_hpa not in target_levels:
                        target_levels.append(pl.pressure_hpa)
                break
            break
    target_levels.sort(reverse=True)

    total_enriched = 0
    for fhour in forecast_hours:
        decoded_points = _fetch_clwmr_icmr_for_fhour(
            init_date, init_hour, fhour, target_levels,
            run_dir, idx_by_fhour, point_lats, point_lons, session,
        )
        if not decoded_points:
            continue

        valid_utc = _forecast_hour_to_utc(init_date, init_hour, fhour)
        total_enriched += _merge_cloud_water_into_sections(
            gfs_sections, all_forecasts, route_points, decoded_points, "gfs",
            valid_utc=valid_utc,
        )
        del decoded_points
        _grib_gc()

    if total_enriched:
        logger.info(
            "GRIB2 GFS enrichment: %d pressure levels enriched with cloud water",
            total_enriched,
        )
    else:
        logger.warning("No GRIB2 CLWMR/ICMR data retrieved for enrichment")


def _fetch_cloud_diag_for_fhour(
    init_date: str,
    init_hour: int,
    fhour: int,
    run_dir: Path,
    idx_by_fhour: dict[int, str],
    point_lats: list[float],
    point_lons: list[float],
    session: requests.Session,
) -> list[dict[str, float]] | None:
    """Fetch, cache, decode cloud diagnostics for a single GFS forecast hour."""
    ck = cache_key(fhour, "CLOUD_DIAG")
    if not is_cached(run_dir, ck):
        idx_text = idx_by_fhour.get(fhour)
        if idx_text is None:
            return None
        try:
            ranges = plan_cloud_diag_byte_ranges(idx_text)
            if not ranges:
                logger.warning("No cloud diag found in .idx for f%03d", fhour)
                return None
            with _grib_time("gfs_cloud_diag_download"):
                grib_bytes = fetch_cloud_diag_ranges(
                    init_date, init_hour, fhour, ranges, session=session,
                )
            if not grib_bytes:
                return None
            put_cached(run_dir, ck, grib_bytes)
            logger.info(
                "Downloaded cloud diag f%03d: %d ranges, %.1f KB",
                fhour, len(ranges), len(grib_bytes) / 1024,
            )
        except Exception:
            logger.warning("Failed to fetch cloud diag f%03d", fhour, exc_info=True)
            return None

    cache_path = run_dir / ck
    with _grib_time("gfs_cloud_diag_decode"):
        return _dispatch_decode(
            "decode_gfs_cloud_diag", str(cache_path), point_lats, point_lons,
        )


def _apply_cloud_diagnostics_to_sections(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    diagnostics_per_point: list[NWPCloudDiagnostics | None],
    model_value: str,
    valid_utc: datetime | None = None,
) -> int:
    """Merge cloud diagnostics into cross-section and waypoint forecasts.

    Args:
        valid_utc: If set, only enrich hourly entries matching this UTC hour.

    Returns:
        Number of hourly entries enriched.
    """
    enriched_count = 0
    for cs in sections:
        for point_idx, wf in enumerate(cs.point_forecasts):
            if point_idx >= len(diagnostics_per_point):
                break
            diag = diagnostics_per_point[point_idx]
            if diag is None:
                continue
            for hourly in wf.hourly:
                if not _matches_valid_time(hourly.time, valid_utc):
                    continue
                _apply_cloud_diagnostics(hourly, diag)
                enriched_count += 1

    # Also enrich waypoint-only forecasts
    wp_diag_lookup: dict[str, NWPCloudDiagnostics] = {}
    for rp, diag in zip(route_points, diagnostics_per_point):
        if rp.waypoint_icao and diag is not None:
            wp_diag_lookup[rp.waypoint_icao] = diag

    for wf in all_forecasts:
        if wf.model.value != model_value:
            continue
        diag = wp_diag_lookup.get(wf.waypoint.icao)
        if diag is None:
            continue
        for hourly in wf.hourly:
            if not _matches_valid_time(hourly.time, valid_utc):
                continue
            _apply_cloud_diagnostics(hourly, diag)

    return enriched_count


# Instantaneous surface fields written from GRIB at the matching valid_utc.
# Forward-fill in ``fill.py`` propagates these into intermediate hours.
_ECMWF_HOURLY_INSTANT_FIELDS: tuple[str, ...] = (
    "temperature_2m_c",
    "dewpoint_2m_c",
    "wind_speed_10m_kt",
    "wind_direction_10m_deg",
    "wind_gusts_10m_kt",
    "visibility_m",
    "cape_jkg",
    "surface_pressure_hpa",
)

# Window-average surface fields. ECMWF a1 delivers these as cumulative-since-init,
# so we step-difference against the prior a1 file and distribute the per-hour
# rate across every hour in the window — no forward-fill needed.
_ECMWF_HOURLY_RATE_FIELDS: tuple[str, ...] = (
    "precipitation_mm",
    "snowfall_cm",
)


def _copy_fields(hourly: HourlyForecast, snap: dict, fields: tuple[str, ...]) -> None:
    for f in fields:
        v = snap.get(f)
        if v is not None:
            setattr(hourly, f, v)


def _apply_ecmwf_surface_to_hourly(
    ecmwf_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    sfc_data: list[dict[str, float]],
    sfc_covered: list[bool],
    *,
    valid_utc: datetime,
    prev_valid_utc: datetime | None,
    prev_tp_per_point: list[float | None],
    prev_sf_per_point: list[float | None],
) -> None:
    """Write decoded ECMWF a1 surface fields onto matching ``HourlyForecast``s.

    Reuses :func:`build_ecmwf_surface_snapshot` for the unit-conversion logic so
    the standalone-verification path and the briefing path stay in sync.

    Instantaneous fields (T/dewpoint, wind/gust, vis, CAPE, MSLP) are written
    only at the hour matching ``valid_utc``. Linear interpolation closes the
    gap to intermediate hours later in :mod:`fetch.grib.fill`, anchored by
    the hours where ``nwp_cloud_diagnostics`` was set in the same loop
    iteration as the surface write — so the two writes must stay coupled.

    Accumulated fields (``tp``, ``sf``) are step-differenced against the prior
    a1 step's cumulative values and distributed evenly as a per-hour rate
    across every hour in ``(prev_valid_utc, valid_utc]``. When no prior step
    is available (first a1 in the processed window), precip/snow are left
    untouched — Open-Meteo's value remains.

    Mutates ``HourlyForecast`` instances in place. Uncovered points (route
    extends outside the ECMWF grid) are skipped, leaving Open-Meteo data.
    """
    from weatherbrief.fetch.grib.decode import build_ecmwf_surface_snapshot

    n = len(sfc_data)
    if n == 0:
        return

    window_hours: float | None = None
    if prev_valid_utc is not None:
        delta_h = (valid_utc - prev_valid_utc).total_seconds() / 3600.0
        if delta_h > 0:
            window_hours = delta_h

    # Build per-point snapshots (instantaneous + per-hour rate). Empty dicts
    # for uncovered/missing points so the indexing stays aligned with route_points.
    inst_snaps: list[dict] = []
    rate_snaps: list[dict] = []
    for i in range(n):
        raw = sfc_data[i]
        if i >= len(sfc_covered) or not sfc_covered[i] or not raw:
            inst_snaps.append({})
            rate_snaps.append({})
            continue

        # Instantaneous: zero out the cumulative fields so the snapshot
        # builder doesn't emit precip/snow values for the per-hour write.
        inst_raw = dict(raw)
        inst_raw["total_precip_m"] = None
        inst_raw["snowfall_m_we"] = None
        inst_snaps.append(build_ecmwf_surface_snapshot(inst_raw))

        rate_raw: dict[str, float] = {}
        if window_hours is not None:
            tp = raw.get("total_precip_m")
            ptp = prev_tp_per_point[i] if i < len(prev_tp_per_point) else None
            if tp is not None and ptp is not None:
                rate_raw["total_precip_m"] = max(0.0, (tp - ptp) / window_hours)
            sf = raw.get("snowfall_m_we")
            psf = prev_sf_per_point[i] if i < len(prev_sf_per_point) else None
            if sf is not None and psf is not None:
                rate_raw["snowfall_m_we"] = max(0.0, (sf - psf) / window_hours)
        rate_snaps.append(build_ecmwf_surface_snapshot(rate_raw) if rate_raw else {})

    def _aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def _write(hourly_list: list[HourlyForecast], inst: dict, rate: dict) -> None:
        for h in hourly_list:
            if inst and _matches_valid_time(h.time, valid_utc):
                _copy_fields(h, inst, _ECMWF_HOURLY_INSTANT_FIELDS)
            if rate and prev_valid_utc is not None:
                ht = _aware(h.time)
                if prev_valid_utc < ht <= valid_utc:
                    _copy_fields(h, rate, _ECMWF_HOURLY_RATE_FIELDS)

    # Cross-section route points
    for cs in ecmwf_sections:
        for point_idx, wf in enumerate(cs.point_forecasts):
            if point_idx >= n:
                break
            inst = inst_snaps[point_idx]
            rate = rate_snaps[point_idx]
            if not inst and not rate:
                continue
            _write(wf.hourly, inst, rate)

    # Waypoint-only forecasts (used by per-airport sounding analysis)
    wp_idx_lookup: dict[str, int] = {}
    for rp_idx, rp in enumerate(route_points):
        if rp.waypoint_icao and rp_idx < n:
            wp_idx_lookup[rp.waypoint_icao] = rp_idx

    for wf in all_forecasts:
        if wf.model.value != "ecmwf":
            continue
        idx = wp_idx_lookup.get(wf.waypoint.icao)
        if idx is None:
            continue
        inst = inst_snaps[idx]
        rate = rate_snaps[idx]
        if not inst and not rate:
            continue
        _write(wf.hourly, inst, rate)


def _replace_pressure_levels_from_grib(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    decoded_points: list[dict[int, dict[str, float]]],
    valid_utc: datetime | None = None,
    model_source: ModelSource = ModelSource.ECMWF,
) -> int:
    """Replace pressure_levels on hourly forecasts with full GRIB sounding.

    Unlike _merge_cloud_water_into_sections which patches individual fields
    onto existing levels, this builds complete PressureLevelData objects from
    the GRIB data and replaces the entire pressure_levels list.

    Works for both ECMWF and ICON-EU GRIB data — the decoded dict format
    is model-agnostic after vertical interpolation to pressure levels.

    Returns:
        Number of hourly entries whose pressure levels were replaced.
    """
    from weatherbrief.fetch.grib.decode import build_pressure_levels_from_grib

    replaced_count = 0
    for cs in sections:
        for point_idx, wf in enumerate(cs.point_forecasts):
            if point_idx >= len(decoded_points):
                break
            point_data = decoded_points[point_idx]
            if not point_data:
                continue

            for hourly in wf.hourly:
                if not _matches_valid_time(hourly.time, valid_utc):
                    continue
                new_levels = build_pressure_levels_from_grib(point_data)
                if new_levels:
                    hourly.pressure_levels = new_levels
                    replaced_count += 1

    # Also replace for waypoint-only forecasts
    wp_data_lookup: dict[str, dict[int, dict[str, float]]] = {}
    for rp, pd in zip(route_points, decoded_points):
        if rp.waypoint_icao and pd:
            wp_data_lookup[rp.waypoint_icao] = pd

    for wf in all_forecasts:
        if wf.model != model_source:
            continue
        point_data = wp_data_lookup.get(wf.waypoint.icao)
        if not point_data:
            continue
        for hourly in wf.hourly:
            if not _matches_valid_time(hourly.time, valid_utc):
                continue
            new_levels = build_pressure_levels_from_grib(point_data)
            if new_levels:
                hourly.pressure_levels = new_levels
                replaced_count += 1

    return replaced_count





def _enrich_cloud_diagnostics(
    gfs_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    run_dir: Path,
    idx_by_fhour: dict[int, str],
    point_lats: list[float],
    point_lons: list[float],
    session: requests.Session,
) -> None:
    """Enrich forecasts with GFS cloud layer diagnostics."""
    from weatherbrief.fetch.grib.decode import build_cloud_diagnostics

    total_enriched = 0
    for fhour in forecast_hours:
        decoded_points = _fetch_cloud_diag_for_fhour(
            init_date, init_hour, fhour,
            run_dir, idx_by_fhour, point_lats, point_lons, session,
        )
        if not decoded_points:
            continue

        diagnostics_per_point = [build_cloud_diagnostics(raw) for raw in decoded_points]
        valid_utc = _forecast_hour_to_utc(init_date, init_hour, fhour)
        total_enriched += _apply_cloud_diagnostics_to_sections(
            gfs_sections, all_forecasts, route_points,
            diagnostics_per_point, "gfs", valid_utc=valid_utc,
        )
        del decoded_points
        del diagnostics_per_point
        _grib_gc()

    if total_enriched:
        logger.info("GRIB2 enrichment: %d hourly entries enriched with cloud diagnostics", total_enriched)
    else:
        logger.warning("No cloud diagnostic GRIB2 data retrieved")


# ---------------------------------------------------------------------------
# ICON-EU enrichment
# ---------------------------------------------------------------------------


class _IconEuContext:
    """Holds resolved ICON-EU run info for split download/decode phases."""

    __slots__ = (
        "init_date", "init_hour", "forecast_hours", "run_dir",
        "levels", "point_lats", "point_lons", "session",
    )

    def __init__(
        self, init_date: str, init_hour: int, forecast_hours: list[int],
        run_dir: Path, levels: list[int],
        point_lats: list[float], point_lons: list[float],
        session: requests.Session,
    ):
        self.init_date = init_date
        self.init_hour = init_hour
        self.forecast_hours = forecast_hours
        self.run_dir = run_dir
        self.levels = levels
        self.point_lats = point_lats
        self.point_lons = point_lons
        self.session = session


def _prepare_icon_eu(
    cross_sections: list[RouteCrossSection],
    route_points: list[RoutePoint],
    departure_time: datetime,
    *,
    data_dir: Path,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
) -> _IconEuContext | None:
    """Resolve ICON-EU run info and check eligibility. Returns None to skip."""
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU_MODEL_LEVEL_MAX,
        ICON_EU_MODEL_LEVEL_MIN,
        compute_icon_eu_flight_window_hours,
        find_latest_icon_eu_run,
        route_in_icon_eu_domain,
    )

    icon_sections = [cs for cs in cross_sections if cs.model == ModelSource.ICON]
    if not icon_sections:
        logger.debug("No ICON cross-sections to enrich")
        return None

    if not route_in_icon_eu_domain(route_points):
        logger.info("Route outside ICON-EU domain, skipping ICON-EU enrichment")
        return None

    session = _grib_session()

    cover_until = departure_time + timedelta(hours=flight_duration_hours)
    try:
        run_info = find_latest_icon_eu_run(
            departure_time, session=session, as_of_time=as_of_time,
            cover_until=cover_until,
        )
    except Exception:
        logger.warning("Failed to find ICON-EU model run", exc_info=True)
        return None

    if run_info is None:
        logger.info("No ICON-EU run found that covers the flight window")
        return None

    init_date, init_hour = run_info

    forecast_hours = compute_icon_eu_flight_window_hours(
        init_date, init_hour, departure_time, flight_duration_hours,
    )

    purge_old_runs(data_dir, model="icon-eu")
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="icon-eu")
    levels = list(range(ICON_EU_MODEL_LEVEL_MIN, ICON_EU_MODEL_LEVEL_MAX + 1))

    return _IconEuContext(
        init_date=init_date, init_hour=init_hour,
        forecast_hours=forecast_hours, run_dir=run_dir, levels=levels,
        point_lats=[rp.lat for rp in route_points],
        point_lons=[rp.lon for rp in route_points],
        session=session,
    )


def _prefetch_icon_eu_data(ctx: _IconEuContext) -> None:
    """Download ICON-EU GRIB2 data and cache to disk (no decode).

    Runs in a background thread while GFS enrichment proceeds.
    """
    with _grib_time("icon_prefetch"):
        _prefetch_icon_eu_data_inner(ctx)


def _prefetch_icon_eu_data_inner(ctx: _IconEuContext) -> None:
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU_VARIABLES,
        fetch_icon_eu_per_variable,
        fetch_icon_eu_single_level,
    )

    for fhour in ctx.forecast_hours:
        # Model-level data (P, QC, QI) — per variable
        legacy_ck = cache_key(fhour, "ICON_EU_QC_QI_P")
        if is_cached(ctx.run_dir, legacy_ck):
            continue  # legacy cache hit, skip per-var download

        for var in ICON_EU_VARIABLES:
            ck = cache_key(fhour, f"ICON_EU_{var.upper()}")
            if is_cached(ctx.run_dir, ck):
                continue
            try:
                with _grib_time("icon_prefetch_var"):
                    per_var = fetch_icon_eu_per_variable(
                        ctx.init_date, ctx.init_hour, fhour,
                        levels=ctx.levels,
                        variables=[var],
                        session=ctx.session,
                    )
                data = per_var.get(var)
                if data:
                    put_cached(ctx.run_dir, ck, data)
            except Exception:
                logger.warning("Prefetch ICON-EU f%03d %s failed", fhour, var, exc_info=True)

        # Single-level cloud diagnostics
        diag_ck = cache_key(fhour, "ICON_EU_CLOUD_DIAG")
        if not is_cached(ctx.run_dir, diag_ck):
            try:
                with _grib_time("icon_prefetch_cloud_diag"):
                    fetched = fetch_icon_eu_single_level(
                        ctx.init_date, ctx.init_hour, [fhour], session=ctx.session,
                    )
                grib_bytes = fetched.get(fhour)
                if grib_bytes:
                    put_cached(ctx.run_dir, diag_ck, grib_bytes)
            except Exception:
                logger.warning("Prefetch ICON-EU cloud diag f%03d failed", fhour, exc_info=True)


def _decode_and_merge_icon_eu(
    ctx: _IconEuContext,
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
) -> tuple[int | None, str | None]:
    """Decode cached ICON-EU data and merge into cross-sections.

    Called after prefetch has cached all data to disk.
    """
    from weatherbrief.fetch.grib.icon_eu_fetch import ICON_EU_VARIABLES

    icon_sections = [cs for cs in cross_sections if cs.model == ModelSource.ICON]

    # Collect CLC-derived cloud layers across forecast hours.
    # Use the last non-empty result per point (layers are time-invariant for
    # a given ICON run, so any forecast hour's CLC works).
    n_points = len(ctx.point_lats)
    clc_layers_per_point: list[dict[str, float]] = [{} for _ in range(n_points)]

    # Build per-fhour decode jobs up-front. A given fhour either has the
    # legacy combined cache (one ``decode_icon_legacy`` job) or per-variable
    # caches (one ``decode_icon_chunked`` job); fhours with neither are
    # skipped. The decode bytes are read inside the worker.
    fhour_jobs: dict[int, tuple[str, tuple]] = {}
    for fhour in ctx.forecast_hours:
        legacy_ck = cache_key(fhour, "ICON_EU_QC_QI_P")
        if is_cached(ctx.run_dir, legacy_ck):
            fhour_jobs[fhour] = (
                "decode_icon_legacy",
                (str(ctx.run_dir / legacy_ck), ctx.point_lats, ctx.point_lons),
            )
            continue

        var_paths: dict[str, str] = {}
        for var in ICON_EU_VARIABLES:
            ck = cache_key(fhour, f"ICON_EU_{var.upper()}")
            if is_cached(ctx.run_dir, ck):
                var_paths[var] = str(ctx.run_dir / ck)
        if var_paths:
            fhour_jobs[fhour] = (
                "decode_icon_chunked",
                (var_paths, ctx.point_lats, ctx.point_lons),
            )

    # Fan out all fhour decodes in parallel — the dominant cost on this path.
    # Sequential dispatch was using only one pool worker even with workers>=2;
    # see issue #133 for the production timing analysis.
    fhours_with_jobs = list(fhour_jobs.keys())
    job_list = [fhour_jobs[fh] for fh in fhours_with_jobs]
    _grib_rss_mark("icon_fhour_pre")
    if job_list:
        with _grib_time("icon_parallel_decode"):
            raw_results = _dispatch_decode_parallel(job_list)
    else:
        raw_results = []
    _grib_rss_mark("icon_fhour_decoded")

    # Normalise: legacy returns just decoded points; chunked returns
    # (decoded, clc_layers). Build a fhour -> (decoded, clc) lookup.
    decoded_by_fhour: dict[int, tuple[
        list[dict[int, dict[str, float]]] | None,
        list[dict[str, float]],
    ] | None] = {}
    for fhour, (worker_name, _), raw in zip(fhours_with_jobs, job_list, raw_results):
        if worker_name == "decode_icon_legacy":
            # Fresh empty list per fhour — sharing a single sentinel would
            # alias all legacy fhours' clc_layers, a latent footgun if any
            # downstream merge ever writes back into the slot.
            decoded_by_fhour[fhour] = (raw, [{} for _ in range(n_points)])
        else:
            decoded, clc_layers = raw
            decoded_by_fhour[fhour] = (decoded, clc_layers)

    # Merge in forecast_hours order so the existing valid-time invariants hold.
    total_enriched = 0
    for fhour in ctx.forecast_hours:
        res = decoded_by_fhour.get(fhour)
        if res is None:
            continue
        decoded_points, clc_layers = res
        if not decoded_points:
            continue

        # Keep CLC-derived layers (first non-empty wins per point)
        for i, layers in enumerate(clc_layers):
            if layers and not clc_layers_per_point[i]:
                clc_layers_per_point[i] = layers

        valid_utc = _forecast_hour_to_utc(ctx.init_date, ctx.init_hour, fhour)
        replaced = _replace_pressure_levels_from_grib(
            icon_sections, all_forecasts, route_points, decoded_points,
            valid_utc=valid_utc, model_source=ModelSource.ICON,
        )
        total_enriched += replaced
        del decoded_points
        # Replace the tuple to release both the decoded_points reference
        # (already del'd locally) and the clc_layers reference (already
        # accumulated into clc_layers_per_point above). The local
        # ``clc_layers`` stays alive for the rest of this iteration; no
        # further reader needs the dict entry.
        decoded_by_fhour[fhour] = None
    _grib_gc()
    _grib_rss_mark("icon_fhour_post_gc")

    if not total_enriched:
        logger.warning("No ICON-EU GRIB2 data retrieved for enrichment")
        return None, None

    logger.info(
        "GRIB2 ICON full sounding replacement: %d hourly entries replaced",
        total_enriched,
    )

    # Cloud diagnostics (ceiling, convective base/top) from single-level files.
    # Pass CLC-derived layer boundaries to fill missing NWP base/top.
    _enrich_icon_eu_cloud_diagnostics(
        icon_sections, all_forecasts, route_points,
        ctx.init_date, ctx.init_hour, ctx.forecast_hours,
        ctx.run_dir, ctx.point_lats, ctx.point_lons, ctx.session,
        clc_layers_per_point=clc_layers_per_point,
    )

    return _run_info_to_timestamp(ctx.init_date, ctx.init_hour), None


def _enrich_icon_eu_cloud_diagnostics(
    icon_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    run_dir: Path,
    point_lats: list[float],
    point_lons: list[float],
    session: requests.Session,
    *,
    clc_layers_per_point: list[dict[str, float]] | None = None,
) -> None:
    """Enrich ICON forecasts with single-level cloud diagnostics (ceiling, etc.).

    If *clc_layers_per_point* is provided (CLC-derived cloud layer boundaries
    from model-level data), missing ``base_ft``/``top_ft`` on low/mid/high
    NWPCloudLayerDiag are filled from it.
    """
    from weatherbrief.fetch.grib.decode import build_icon_cloud_diagnostics
    from weatherbrief.fetch.grib.icon_eu_fetch import fetch_icon_eu_single_level

    total_enriched = 0
    for fhour in forecast_hours:
        ck = cache_key(fhour, "ICON_EU_CLOUD_DIAG")
        if not is_cached(run_dir, ck):
            try:
                fetched = fetch_icon_eu_single_level(
                    init_date, init_hour, [fhour], session=session,
                )
                grib_bytes = fetched.get(fhour)
                if grib_bytes:
                    put_cached(run_dir, ck, grib_bytes)
            except Exception:
                logger.warning("Failed to fetch ICON-EU cloud diagnostics f%03d", fhour, exc_info=True)
                continue
        if not is_cached(run_dir, ck):
            continue

        cache_path = run_dir / ck
        with _grib_time("icon_cloud_diag_decode"):
            decoded_points = _dispatch_decode(
                "decode_icon_cloud_diag",
                str(cache_path), point_lats, point_lons,
            )
        if not decoded_points:
            continue

        diagnostics_per_point = [build_icon_cloud_diagnostics(raw) for raw in decoded_points]

        # Fill missing layer base/top from CLC-derived boundaries
        if clc_layers_per_point:
            for pt_idx, diag in enumerate(diagnostics_per_point):
                if diag is None or pt_idx >= len(clc_layers_per_point):
                    continue
                clc = clc_layers_per_point[pt_idx]
                if not clc:
                    continue
                for band in ("low", "mid", "high"):
                    layer = getattr(diag, band)
                    if layer.base_ft is None and f"{band}_base_ft" in clc:
                        layer.base_ft = clc[f"{band}_base_ft"]
                    if layer.top_ft is None and f"{band}_top_ft" in clc:
                        layer.top_ft = clc[f"{band}_top_ft"]

        valid_utc = _forecast_hour_to_utc(init_date, init_hour, fhour)

        # Use _apply_cloud_diagnostics_to_sections with GFS-priority guard
        for cs in icon_sections:
            for point_idx, wf in enumerate(cs.point_forecasts):
                if point_idx >= len(diagnostics_per_point):
                    break
                diag = diagnostics_per_point[point_idx]
                if diag is None:
                    continue
                for hourly in wf.hourly:
                    if not _matches_valid_time(hourly.time, valid_utc):
                        continue
                    if hourly.nwp_cloud_diagnostics is None:
                        _apply_cloud_diagnostics(hourly, diag)
                        total_enriched += 1

        # Also enrich waypoint-only forecasts
        wp_diag_lookup: dict[str, NWPCloudDiagnostics] = {}
        for rp, diag in zip(route_points, diagnostics_per_point):
            if rp.waypoint_icao and diag is not None:
                wp_diag_lookup[rp.waypoint_icao] = diag

        for wf in all_forecasts:
            if wf.model.value != "icon":
                continue
            diag = wp_diag_lookup.get(wf.waypoint.icao)
            if diag is None:
                continue
            for hourly in wf.hourly:
                if not _matches_valid_time(hourly.time, valid_utc):
                    continue
                if hourly.nwp_cloud_diagnostics is None:
                    _apply_cloud_diagnostics(hourly, diag)

        del decoded_points
        del diagnostics_per_point
        del wp_diag_lookup
        _grib_gc()

    if total_enriched:
        logger.info(
            "ICON-EU enrichment: %d hourly entries enriched with cloud diagnostics",
            total_enriched,
        )
    else:
        logger.debug("No ICON-EU cloud diagnostic GRIB2 data retrieved")


# ---------------------------------------------------------------------------
# Shared merge logic
# ---------------------------------------------------------------------------


def _merge_cloud_water_into_sections(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    decoded_points: list[dict[int, dict[str, float]]],
    model_value: str,
    valid_utc: datetime | None = None,
) -> int:
    """Merge decoded cloud water data into cross-section and waypoint forecasts.

    Used by GFS enrichment (cloud-only, no full sounding replacement).

    Args:
        valid_utc: If set, only enrich hourly entries whose time matches
            this UTC hour. None enriches all hours (backward-compatible).

    Returns:
        Number of pressure levels enriched.
    """
    enriched_count = 0
    for cs in sections:
        for point_idx, wf in enumerate(cs.point_forecasts):
            if point_idx >= len(decoded_points):
                break
            point_data = decoded_points[point_idx]
            if not point_data:
                continue

            for hourly in wf.hourly:
                if not _matches_valid_time(hourly.time, valid_utc):
                    continue
                for pl in hourly.pressure_levels:
                    level_data = point_data.get(pl.pressure_hpa)
                    if level_data is None:
                        continue

                    clwmr = level_data.get("cloud_liquid_water_kg_kg")
                    if clwmr is not None:
                        pl.cloud_liquid_water_kg_kg = clwmr
                        enriched_count += 1

                    icmr = level_data.get("ice_mixing_ratio_kg_kg")
                    if icmr is not None:
                        pl.ice_mixing_ratio_kg_kg = icmr

                    clc = level_data.get("cloud_area_fraction_pct")
                    if clc is not None:
                        pl.cloud_area_fraction_pct = clc

    # Also enrich waypoint-only forecasts
    wp_data_lookup: dict[str, dict[int, dict[str, float]]] = {}
    for rp, pd in zip(route_points, decoded_points):
        if rp.waypoint_icao and pd:
            wp_data_lookup[rp.waypoint_icao] = pd

    for wf in all_forecasts:
        if wf.model.value != model_value:
            continue
        wp_icao = wf.waypoint.icao
        point_data = wp_data_lookup.get(wp_icao)
        if not point_data:
            continue
        for hourly in wf.hourly:
            if not _matches_valid_time(hourly.time, valid_utc):
                continue
            for pl in hourly.pressure_levels:
                level_data = point_data.get(pl.pressure_hpa)
                if level_data is None:
                    continue
                clwmr = level_data.get("cloud_liquid_water_kg_kg")
                if clwmr is not None:
                    pl.cloud_liquid_water_kg_kg = clwmr
                icmr = level_data.get("ice_mixing_ratio_kg_kg")
                if icmr is not None:
                    pl.ice_mixing_ratio_kg_kg = icmr
                clc = level_data.get("cloud_area_fraction_pct")
                if clc is not None:
                    pl.cloud_area_fraction_pct = clc

    return enriched_count
