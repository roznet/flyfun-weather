"""GRIB2 enrichment for cloud liquid water and ice mixing ratio.

Public API: enrich_forecasts() adds CLWMR/ICMR data and cloud diagnostics to
existing cross-section forecasts from GFS and ICON-EU GRIB2 data.
"""

from __future__ import annotations

import contextvars
import gc
import heapq
import logging
import multiprocessing
import os
import random
import signal
import threading
import time as _time_mod
from collections import deque
from concurrent.futures import CancelledError, Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from enum import IntEnum
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

# PID → Process refs we've observed in the current pool. ProcessPoolExecutor's
# manager removes entries from ``_processes`` once it has joined a dead worker,
# losing the only handle that still carries the worker's exit status. Keeping
# our own reference lets ``_diag_snapshot_workers`` read ``Process.exitcode``
# (negative = killed by signal -N) for workers that have vanished from the
# pool — the missing signal in the workers=[] hang pattern observed in prod.
_DECODE_WORKER_REGISTRY: dict[int, multiprocessing.Process] = {}

# Every worker Process ever observed in this interpreter — unlike
# _DECODE_WORKER_REGISTRY it is NEVER cleared on pool rebuild. This is what
# lets a force teardown (#448 PR B) reach workers orphaned by an earlier pool
# replacement: dispatcher timeout recovery abandons a wedged worker and lazily
# builds a new pool, so the current pool's ``_processes`` no longer knows the
# orphan. Bounded by workers-per-pool × pool rebuilds in one process lifetime
# (a handful per day in the web app; 1–2 pools in a cycle child).
_ALL_DECODE_WORKERS: dict[int, multiprocessing.Process] = {}


def _diag_reset_pool_counters() -> None:
    global _DECODE_POOL_INIT_TIME, _DECODE_TASKS_DISPATCHED
    with _DECODE_DIAG_LOCK:
        _DECODE_POOL_INIT_TIME = _time_mod.time()
        _DECODE_TASKS_DISPATCHED = 0
        _DECODE_WORKER_REGISTRY.clear()


def _diag_register_workers(pool: ProcessPoolExecutor | None) -> None:
    """Snapshot ``pool._processes`` into ``_DECODE_WORKER_REGISTRY``.

    Called opportunistically from each dispatch — cheap dict copy + setdefault.
    The first-seen ``Process`` object for a given PID wins, so if the pool
    recycles via ``max_tasks_per_child`` we still hold the original handle and
    can read its final ``exitcode``.
    """
    if pool is None:
        return
    try:
        procs = dict(getattr(pool, "_processes", {}))
    except Exception:
        return
    with _DECODE_DIAG_LOCK:
        for pid, proc in procs.items():
            _DECODE_WORKER_REGISTRY.setdefault(pid, proc)
            _ALL_DECODE_WORKERS.setdefault(pid, proc)


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

    # Vanished workers (I): PIDs we've observed in this pool but the manager
    # has since dropped from ``_processes``. ``Process.exitcode`` encodes how
    # the worker died — negative values are signals (-11=SIGSEGV, -9=SIGKILL,
    # -6=SIGABRT). This is the missing piece in the workers=[] hang pattern.
    _diag_snapshot_vanished_workers(pool, worker_pids)

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


def _diag_snapshot_vanished_workers(
    pool: ProcessPoolExecutor | None,
    current_pids: list[int],
) -> None:
    """Log exit status of workers that vanished from the pool.

    Reads ``Process.exitcode`` for every PID in ``_DECODE_WORKER_REGISTRY``
    that isn't currently listed in ``pool._processes``. Exit codes:

    * ``None`` — never started or not yet reaped (unexpected here; the manager
      thread joins dead workers before dropping them).
    * ``0`` — clean exit (e.g. retired by ``max_tasks_per_child``).
    * ``N > 0`` — ``sys.exit(N)`` from the worker.
    * ``N < 0`` — killed by signal ``-N``. SIGSEGV/SIGABRT in a worker
      indicates a native crash in cfgrib/ECCODES; SIGKILL indicates an
      external kill (cgroup OOM, ``docker kill``).
    """
    current = set(current_pids)
    with _DECODE_DIAG_LOCK:
        # Take snapshot under the lock; release before doing per-pid I/O so we
        # don't block dispatch threads waiting on /proc reads.
        registry_snapshot = list(_DECODE_WORKER_REGISTRY.items())

    if not registry_snapshot:
        logger.error(
            "GRIB hang diag vanished-workers: registry empty "
            "(no dispatch has registered workers yet)",
        )
        return

    vanished = [(pid, proc) for pid, proc in registry_snapshot if pid not in current]
    if not vanished:
        logger.error(
            "GRIB hang diag vanished-workers: none (all %d tracked PIDs still in pool)",
            len(registry_snapshot),
        )
        return

    for pid, proc in vanished:
        try:
            ec = proc.exitcode
        except Exception as e:
            logger.error(
                "GRIB hang diag vanished-worker pid=%d: exitcode read failed (%s)",
                pid, e,
            )
            continue
        signame = ""
        if isinstance(ec, int) and ec < 0:
            try:
                signame = f" signal={signal.Signals(-ec).name}"
            except (ValueError, AttributeError):
                signame = f" signal=#{-ec}"
        try:
            alive = proc.is_alive()
        except Exception:
            alive = "?"
        proc_exists = Path(f"/proc/{pid}").exists()
        logger.error(
            "GRIB hang diag vanished-worker pid=%d exitcode=%s%s "
            "is_alive=%s /proc/%d exists=%s",
            pid, ec, signame, alive, pid, proc_exists,
        )


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


def _decode_workers_ecmwf() -> int | None:
    """Optional per-task concurrency window for the ECMWF standalone decode (#459).

    Returns an int ``max_inflight`` window for ``_dispatch_decode_parallel``
    when ``GRIB_DECODE_WORKERS_ECMWF`` is set, else ``None`` (submit all; the
    pool bounds concurrency).

    Because there is a **single** decode pool sized by ``GRIB_DECODE_WORKERS``,
    this can only throttle the ECMWF loop *down* from the pool size — the pool
    is the hard ceiling. To give ECMWF *more* workers, raise
    ``GRIB_DECODE_WORKERS`` itself.

    Rationale: concurrent ECMWF decode holds full GRIB grids in RAM, heavier
    per-worker than the sounding batches the pool was tuned for. On a
    memory-constrained fallback host (the prod droplet), set
    ``GRIB_DECODE_WORKERS_ECMWF=1`` to keep the ECMWF leg serial while leaving
    the shared pool wide for everything else.
    """
    raw = os.environ.get("GRIB_DECODE_WORKERS_ECMWF", "").strip()
    if not raw:
        return None
    try:
        v = int(raw)
    except ValueError:
        logger.warning(
            "Invalid GRIB_DECODE_WORKERS_ECMWF=%r, ignoring (using full pool)", raw,
        )
        return None
    return v if v > 0 else None


def decode_pool_enabled() -> bool:
    """Public: is the decode process pool available (workers > 0)?

    Call sites that can batch work for the pool (e.g. the standalone cycle's
    pooled sounding analysis, #448 PR B) use this to choose between dispatch
    and the inline fallback without touching private config helpers.
    """
    return _decode_pool_workers() > 0


def _decode_pool_max_tasks_per_child() -> int | None:
    """Worker recycle threshold; ``None`` disables recycling (default).

    Disabled by default after a 2026-05-17 prod incident: with 2 workers and
    ``max_tasks_per_child=50``, both workers retire near-simultaneously around
    task ~100 and ``ProcessPoolExecutor`` fails to spawn replacements while
    pending work sits in ``_call_queue`` (CPython recycle race). Hang diag
    confirmed vanished workers exited with ``exitcode=0`` — clean retire, not
    a native crash. Set ``GRIB_DECODE_MAX_TASKS_PER_CHILD=<N>`` to re-enable
    if cfgrib/ECCODES native-memory growth becomes a problem again.
    """
    raw = os.environ.get("GRIB_DECODE_MAX_TASKS_PER_CHILD", "0").strip()
    try:
        v = int(raw)
        return v if v > 0 else None
    except ValueError:
        logger.warning(
            "Invalid GRIB_DECODE_MAX_TASKS_PER_CHILD=%r, disabling recycling", raw,
        )
        return None


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


# ECMWF enrichment decodes steps within the flight window ± this margin (see
# the step filter in ``_enrich_ecmwf_inner``). Module-level so the timing-scan
# coverage detector (``tasks/time_scan.py``) reads the SAME value instead of
# mirroring a magic number that could drift.
ECMWF_FLIGHT_WINDOW_MARGIN = timedelta(hours=3)


# ---------------------------------------------------------------------------
# Priority signal + dispatcher configuration (issue #171).
# ---------------------------------------------------------------------------


class DecodePriority(IntEnum):
    """Decode-job priority. Lower value = higher priority (Unix nice-style).

    Heap pops the smallest first, so INTERACTIVE jobs jump ahead of queued
    BACKGROUND work for the next freed worker slot. Values are spread so finer
    levels (e.g. ``30`` = "expedited background") can be inserted later without
    renumbering. Helpers accept ``int | DecodePriority``; do **not** add
    priority arithmetic (aging, dynamic boosting) — that complexity is
    deliberately deferred (see issue #171 escalation paths).
    """

    INTERACTIVE = 10  # user refresh, airport profile
    SCHEDULED = 50    # auto-refresh
    BACKGROUND = 90   # standalone cycle, precache


# Propagated like ``_GRIB_TIMER``: set by entry points / enrich_forecasts, read
# by the dispatch helpers. ``None`` means "unset" → resolves to SCHEDULED.
_DECODE_PRIORITY: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "_decode_priority", default=None,
)


def _resolve_priority(priority: int | DecodePriority | None) -> int:
    """Resolve an effective priority: explicit arg → ContextVar → SCHEDULED."""
    if priority is not None:
        return int(priority)
    ctx_val = _DECODE_PRIORITY.get()
    if ctx_val is not None:
        return int(ctx_val)
    return int(DecodePriority.SCHEDULED)


def _priority_name(priority: int) -> str:
    """Human-readable label for a priority int (enum name, or ``p<n>`` if custom)."""
    try:
        return DecodePriority(priority).name
    except ValueError:
        return f"p{priority}"


def set_decode_priority(priority: int | DecodePriority) -> None:
    """Publish *priority* on the decode-priority ContextVar for this context.

    Entry points call this to establish the decode priority for an in-progress
    operation that will reach ``enrich_forecasts`` / ``_dispatch_decode``
    synchronously (e.g. inside ``run_pipeline``, which runs past a
    non-context-copying ``run_in_executor`` boundary). Keeps callers off the
    private ``_DECODE_PRIORITY`` ContextVar. No reset token: the entry points
    own their context for its full lifetime (a dedicated executor thread, or a
    copied context under ``asyncio.to_thread``).
    """
    _DECODE_PRIORITY.set(int(priority))


def _int_env(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, defaulting to %d", name, raw, default)
        return default
    if minimum is not None and v < minimum:
        return minimum
    return v


def _float_env(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, defaulting to %s", name, raw, default)
        return default
    if minimum is not None and v < minimum:
        return minimum
    return v


def _priority_enabled() -> bool:
    """Whether the priority dispatcher is on. Off = current FIFO (rollback)."""
    raw = os.environ.get("GRIB_DECODE_PRIORITY_ENABLED", "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _retry_cap() -> int:
    """Max reschedules of a single crash-interrupted job before dead-lettering."""
    return _int_env("GRIB_DECODE_RETRY_CAP", 2, minimum=0)


def _retry_budget() -> int:
    """Max retries process-wide within ``_retry_window_s`` (retry-rate cap)."""
    return _int_env("GRIB_DECODE_RETRY_BUDGET", 5, minimum=0)


def _retry_window_s() -> float:
    return _float_env("GRIB_DECODE_RETRY_WINDOW_S", 120.0, minimum=0.0)


def _backoff_base_s() -> float:
    return _float_env("GRIB_DECODE_BACKOFF_BASE_S", 0.5, minimum=0.0)


def _jittered_backoff(retries: int) -> float:
    """Exponential backoff with equal jitter for crash-collateral retries.

    Returns ``0.0`` when the base is ``0`` (jitter disabled). Otherwise the
    delay is always ``>= ceiling/2 > 0`` so a crash retry is observably
    delayed (timeout-collateral retries pass ``0.0`` and skip this).
    """
    base = _backoff_base_s()
    if base <= 0:
        return 0.0
    ceiling = base * (2 ** max(0, retries - 1))
    half = ceiling / 2.0
    return half + random.uniform(0.0, half)


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


def _force_kill_workers(pool: ProcessPoolExecutor | None) -> int:
    """TERM → join(2 s) → KILL every known decode worker; return count signalled.

    Covers the current pool's workers AND orphans from replaced pools (via
    ``_ALL_DECODE_WORKERS``). SIGTERM is a courtesy to healthy collateral
    workers; a worker wedged in cfgrib/ECCODES native code never runs Python
    signal handlers, so the SIGKILL rung is the one that actually ends it.
    (Python 3.14 adds public ``ProcessPoolExecutor.kill_workers()``, but it
    only reaches the current pool — the orphan case still needs the
    accumulated registry, so we keep one manual path for all versions.)
    """
    handles: dict[int, multiprocessing.Process] = {}
    if pool is not None:
        try:
            handles.update(dict(getattr(pool, "_processes", {}) or {}))
        except Exception:
            pass
    with _DECODE_DIAG_LOCK:
        handles.update(_ALL_DECODE_WORKERS)

    alive = [p for p in handles.values() if p.is_alive()]
    for p in alive:
        try:
            p.terminate()
        except Exception:
            pass
    deadline = _time_mod.monotonic() + 2.0
    for p in alive:
        try:
            p.join(max(0.0, deadline - _time_mod.monotonic()))
        except Exception:
            pass
    for p in alive:
        if p.is_alive():
            try:
                p.kill()
            except Exception:
                pass
    return len(alive)


def shutdown_decode_pool(
    *, wait: bool = True, drain_dispatcher: bool = False, force: bool = False,
) -> None:
    """Shut down the decode pool. Safe to call repeatedly; idempotent.

    ``wait=False`` is for the hung-worker recovery path: a worker stuck
    in cfgrib/ECCODES will never return, so ``shutdown(wait=True)``
    would block forever. Trade-off: the orphaned worker process is left
    behind and reaped when the parent eventually exits — bounded leak
    (at most ``max_workers`` orphans per pool reset).

    ``drain_dispatcher=True`` is the **app-shutdown** path: it also drains
    the :class:`PriorityDecodeDispatcher` so any caller blocked on a pending
    or in-flight job is released with an error rather than left waiting on a
    pool that's going away. The default is ``False`` because the fault-recovery
    path (``_handle_fault``) reuses this teardown and must **not** touch the
    dispatcher's durable pending heap — that heap is what lets interrupted work
    be rescheduled on the rebuilt pool.

    ``force=True`` (#448 PR B) additionally TERM→KILLs every known worker —
    current pool AND orphans from earlier pool replacements — before the
    executor shutdown. Without it, ``wait=False`` merely *abandons* a wedged
    worker: at interpreter exit, concurrent.futures' atexit hook joins the
    executor management thread, which never finishes while a worker lives in
    native code — hanging the process. Used by the disposable standalone
    child's exit path, where any surviving worker is useless by definition.
    The web app's fault-recovery path deliberately does NOT force (see the
    bounded-leak trade-off above; revisiting that is tracked separately).
    """
    if drain_dispatcher:
        _drain_dispatcher_for_shutdown()
    global _DECODE_POOL, _DECODE_POOL_INIT_TIME, _DECODE_TASKS_DISPATCHED
    with _DECODE_POOL_LOCK:
        pool = _DECODE_POOL
        _DECODE_POOL = None
    if force:
        n = _force_kill_workers(pool)
        if n:
            logger.info("GRIB decode pool force-terminated %d worker(s)", n)
    if pool is not None:
        pool.shutdown(wait=wait and not force, cancel_futures=force or not wait)
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


# ---------------------------------------------------------------------------
# Priority decode dispatcher (issue #171).
#
# A process-wide admission layer in front of the existing decode pool. It does
# NOT replace the pool lifecycle (`_get_decode_pool`/`shutdown_decode_pool`) or
# the hang-diagnostics (`_diag_snapshot_*`) — it *wraps* and reuses them, adding
# only priority ordering + fault-tolerant rescheduling. See the class docstring
# for the load-bearing idempotency invariant.
# ---------------------------------------------------------------------------

_FAULT_CRASH = "crash"
_FAULT_TIMEOUT = "timeout"

# Watchdog idle poll: how long it parks when nothing is in flight. A poll
# (rather than an indefinite wait) is a cheap liveness backstop; submissions
# notify the condition immediately, so this never gates real work.
_WATCHDOG_IDLE_POLL_S = 30.0

# Process-wide dead-letter counters, by reason. The DLQ-equivalent metric: a
# corrupt GRIB becomes an observable counter, not a silently-degraded briefing.
_DEAD_LETTER_COUNTS: dict[str, int] = {}
_DEAD_LETTER_LOCK = threading.Lock()


def _record_dead_letter(reason: str) -> None:
    with _DEAD_LETTER_LOCK:
        _DEAD_LETTER_COUNTS[reason] = _DEAD_LETTER_COUNTS.get(reason, 0) + 1


def decode_dead_letter_counts() -> dict[str, int]:
    """Snapshot of dead-letter counts per reason (observability / tests)."""
    with _DEAD_LETTER_LOCK:
        return dict(_DEAD_LETTER_COUNTS)


class DecodeDispatchError(RuntimeError):
    """Raised on a caller future when its decode job is dead-lettered.

    Wraps the give-up *reason* (``decode_hung`` / ``retry_cap_exhausted`` /
    ``retry_budget_exhausted`` / ``dispatcher_shutdown``) and the last
    underlying exception. Existing call sites already degrade on a decode
    exception (e.g. ``tasks/fetch.py`` drops to Open-Meteo-only), so they keep
    degrading exactly as before — just far less often, because most interrupted
    work is now transparently retried instead of failing.
    """

    def __init__(
        self, reason: str, worker_fn_name: str, last_exc: BaseException | None = None,
    ) -> None:
        self.reason = reason
        self.worker_fn_name = worker_fn_name
        self.last_exc = last_exc
        msg = f"decode job {worker_fn_name!r} dead-lettered: {reason}"
        if last_exc is not None:
            msg += f" (last error: {last_exc!r})"
        super().__init__(msg)


@dataclass(eq=False)
class _JobHandle:
    """One logical decode operation. Carries the caller-facing future across
    any number of internal pool futures (retries are transparent to callers).

    ``eq=False`` keeps identity equality/hash so handles can live in a set
    (``_delayed_handles``); the heap orders by ``(priority, seq)`` and never
    compares handles."""

    worker_fn_name: str
    fn: Callable[..., Any]
    args: tuple
    caller_future: Future
    priority: int
    seq: int
    retries: int = 0
    deadline: float = 0.0  # monotonic; set when a pool future is created
    enqueued_at: float = 0.0  # monotonic; set when first pushed to pending
    last_exc: BaseException | None = None


def _default_worker_resolver(name: str) -> Callable[..., Any]:
    from weatherbrief.fetch.grib import decode_worker
    return getattr(decode_worker, name)


def _summarize_args(args: tuple) -> str:
    """Compact, log-safe summary of a job's args (file path / var set)."""
    if not args:
        return "()"
    first = args[0]
    if isinstance(first, str):
        try:
            return Path(first).name
        except Exception:
            return first[:80]
    if isinstance(first, dict):
        return "{" + ",".join(sorted(str(k) for k in first)) + "}"
    return repr(first)[:80]


class PriorityDecodeDispatcher:
    """Process-wide priority admission layer over the GRIB decode pool.

    Orders pending decode jobs by priority (INTERACTIVE > SCHEDULED >
    BACKGROUND, FIFO within a level), bounds in-flight jobs to the pool's
    worker count, and survives pool faults by **keeping completed work,
    rescheduling interrupted work, and dead-lettering poison jobs**.

    INVARIANT — IDEMPOTENCY ONLY. Auto-rescheduling interrupted work is only
    safe because dispatched jobs are **pure**: read a GRIB file, return data,
    no side effects (at-least-once delivery with idempotent consumers). Any
    future side-effecting job MUST NOT use this dispatcher, or MUST carry its
    own dedup — otherwise a crash/timeout reschedule will silently double-apply
    the effect.

    Non-preemptive: a running job is never killed. Priority only decides which
    *pending* job takes the next freed worker slot. Head-of-line blocking is
    therefore possible (a long BACKGROUND decode can hold a worker while an
    INTERACTIVE job waits, bounded by job runtime); a hard interactive SLA
    would need a reserved-worker bulkhead (deferred — see issue #171).

    The pending priority heap is the durable structure: a pool fault tears the
    pool down but never touches pending, so after the pool is rebuilt the
    highest-priority waiting job is fed first.

    Event-driven: no dedicated dispatch thread. ``submit_*`` and the pool's
    done-callbacks drive a locked ``_pump``; a single daemon watchdog enforces
    per-job timeouts. Pool lifecycle and hang-diagnostics are the existing
    module primitives, reused (not reimplemented).
    """

    def __init__(
        self,
        *,
        worker_resolver: Callable[[str], Callable[..., Any]] | None = None,
        pool_factory: Callable[[], ProcessPoolExecutor | None] | None = None,
        pool_teardown: Callable[..., None] | None = None,
        workers_fn: Callable[[], int] | None = None,
        timeout_fn: Callable[[], float] | None = None,
    ) -> None:
        # Injectable seams (default to the module primitives) so the dispatcher
        # is unit-testable with a fake pool + fake worker_fn, per issue #171.
        self._resolve_worker = worker_resolver or _default_worker_resolver
        self._pool_factory = pool_factory or _get_decode_pool
        self._pool_teardown = pool_teardown or shutdown_decode_pool
        self._workers_fn = workers_fn or _decode_pool_workers
        self._timeout_fn = timeout_fn or _decode_timeout_s

        self._cond = threading.Condition(threading.Lock())
        self._pending: list[tuple[int, int, _JobHandle]] = []  # min-heap
        self._inflight: dict[Future, _JobHandle] = {}
        self._seq = 0
        self._draining = False  # a fault teardown owns the pool right now
        self._closed = False    # app shutdown; reject + release everything
        self._retry_times: deque[float] = deque()  # monotonic ts of recent retries
        # Crash-retry handles waiting out a backoff timer: in neither _pending
        # nor _inflight, so drain() must fail them explicitly or their callers
        # would hang on the timer that no-ops once _closed.
        self._delayed_handles: set[_JobHandle] = set()
        self._watchdog: threading.Thread | None = None

    # -- public submission API ----------------------------------------------

    def submit_one(
        self, worker_fn_name: str, args: tuple, priority: int,
    ) -> Future:
        """Queue one decode job. Returns a caller-facing future to block on."""
        if self._workers_fn() == 0:
            return self._run_inline(worker_fn_name, args)
        caller: Future = Future()
        # Resolve the worker (a lazy import on first use) OFF the lock — keeps
        # the critical section free of imports / arbitrary resolver work.
        try:
            fn = self._resolve_worker(worker_fn_name)
        except Exception as exc:  # bad name → fail just this caller
            caller.set_exception(exc)
            return caller
        with self._cond:
            if self._closed:
                # Drain raced ahead of us: fail fast rather than push a handle
                # that _pump (now a no-op) would never resolve.
                caller.set_exception(
                    DecodeDispatchError("dispatcher_shutdown", worker_fn_name),
                )
                return caller
            self._ensure_watchdog_locked()
            self._push_new_locked(worker_fn_name, fn, args, caller, priority)
        self._pump()
        return caller

    def submit_batch(
        self, jobs: list[tuple[str, tuple]], priority: int,
    ) -> list[Future]:
        """Queue a batch at one priority. Returns caller futures in input order."""
        if self._workers_fn() == 0:
            return [self._run_inline(name, args) for name, args in jobs]
        # Resolve workers off the lock; a bad name fails just that job.
        prepared: list[tuple[str, Callable[..., Any] | None, tuple, Future, Exception | None]] = []
        callers: list[Future] = []
        for name, args in jobs:
            caller: Future = Future()
            callers.append(caller)
            try:
                prepared.append((name, self._resolve_worker(name), args, caller, None))
            except Exception as exc:
                prepared.append((name, None, args, caller, exc))
        with self._cond:
            closed = self._closed
            if not closed:
                self._ensure_watchdog_locked()
            for name, fn, args, caller, exc in prepared:
                if exc is not None:
                    caller.set_exception(exc)
                elif closed:
                    caller.set_exception(DecodeDispatchError("dispatcher_shutdown", name))
                else:
                    self._push_new_locked(name, fn, args, caller, priority)
        self._pump()
        return callers

    def drain(self) -> None:
        """App-shutdown drain: release every blocked caller and stop pumping.

        Pending is normally the durable structure; this is the *only* path that
        empties it, because at process teardown blocked callers must be failed
        rather than left waiting on a pool that's going away.
        """
        with self._cond:
            self._closed = True
            handles = (
                [h for _, _, h in self._pending]
                + list(self._inflight.values())
                + list(self._delayed_handles)  # crash-retries mid-backoff
            )
            self._pending.clear()
            self._inflight.clear()
            self._delayed_handles.clear()
            self._cond.notify_all()
        for h in handles:
            self._fail_caller(h, "dispatcher_shutdown")

    # -- internals ----------------------------------------------------------

    def _fail_caller(self, handle: _JobHandle, reason: str) -> None:
        if not handle.caller_future.done():
            handle.caller_future.set_exception(
                DecodeDispatchError(reason, handle.worker_fn_name),
            )

    def _run_inline(self, worker_fn_name: str, args: tuple) -> Future:
        """In-process fallback (``GRIB_DECODE_WORKERS=0``) — priority is moot."""
        fut: Future = Future()
        try:
            fut.set_result(self._resolve_worker(worker_fn_name)(*args))
        except Exception as exc:  # mirror ordinary decode errors to the future
            fut.set_exception(exc)
        except BaseException:
            # KeyboardInterrupt / SystemExit must propagate, not be absorbed
            # into a future the caller might never inspect.
            raise
        return fut

    def _next_seq_locked(self) -> int:
        self._seq += 1
        return self._seq

    def _push_new_locked(
        self, worker_fn_name: str, fn: Callable[..., Any], args: tuple,
        caller: Future, priority: int,
    ) -> None:
        handle = _JobHandle(
            worker_fn_name=worker_fn_name, fn=fn, args=tuple(args),
            caller_future=caller, priority=int(priority), seq=self._next_seq_locked(),
            enqueued_at=_time_mod.monotonic(),
        )
        heapq.heappush(self._pending, (handle.priority, handle.seq, handle))

    def _ensure_watchdog_locked(self) -> None:
        if self._watchdog is not None and self._watchdog.is_alive():
            return
        t = threading.Thread(
            target=self._watchdog_loop, name="grib-decode-watchdog", daemon=True,
        )
        self._watchdog = t
        t.start()

    def _pump(self) -> None:
        """Fill freed worker slots with the highest-priority pending jobs."""
        fault = False
        new_futures: list[Future] = []
        failed: list[tuple[Future, BaseException]] = []
        dispatched: list[tuple[str, int, float, int, int]] = []
        with self._cond:
            if self._draining or self._closed:
                return
            workers = self._workers_fn()
            timeout = self._timeout_fn()
            while len(self._inflight) < workers and self._pending:
                _, _, handle = heapq.heappop(self._pending)
                if handle.caller_future.cancelled():
                    continue
                pool = self._pool_factory()
                if pool is None:
                    # Workers dropped to 0 mid-flight; restore and stop.
                    heapq.heappush(self._pending, (handle.priority, handle.seq, handle))
                    break
                try:
                    pf = pool.submit(handle.fn, *handle.args)
                except BrokenProcessPool:
                    heapq.heappush(self._pending, (handle.priority, handle.seq, handle))
                    fault = True
                    break
                except Exception as exc:  # noqa: BLE001 — e.g. unpicklable arg
                    # Defer set_exception until the lock is released: it fires
                    # done-callbacks synchronously, and a caller callback that
                    # re-entered _cond would deadlock.
                    failed.append((handle.caller_future, exc))
                    continue
                now = _time_mod.monotonic()
                _diag_record_dispatch(1)
                _diag_register_workers(pool)
                handle.deadline = now + timeout
                self._inflight[pf] = handle
                new_futures.append(pf)
                waited = now - handle.enqueued_at if handle.enqueued_at else 0.0
                dispatched.append((
                    handle.worker_fn_name, handle.priority, waited,
                    len(self._inflight), len(self._pending),
                ))
            self._cond.notify_all()  # wake watchdog to recompute earliest deadline
        # Resolve failures and attach callbacks OUTSIDE the lock: both fire
        # done-callbacks synchronously in this thread, and _on_done re-acquires
        # the (non-reentrant) lock — doing either under the lock would deadlock.
        for caller, exc in failed:
            if not caller.done():
                caller.set_exception(exc)
        for fn_name, prio, waited, inflight_n, pending_n in dispatched:
            # INFO only under contention (jobs still queued) so a higher-priority
            # job taking the slot is visible in prod logs; DEBUG otherwise.
            logger.log(
                logging.INFO if pending_n else logging.DEBUG,
                "GRIB dispatch: fn=%s prio=%s waited=%.0fms inflight=%d pending=%d",
                fn_name, _priority_name(prio), waited * 1000.0, inflight_n, pending_n,
            )
        for pf in new_futures:
            pf.add_done_callback(self._on_done)
        if fault:
            self._spawn_recovery(_FAULT_CRASH)

    def _on_done(self, pf: Future) -> None:
        do_fault = False
        with self._cond:
            if self._draining or self._closed:
                return  # a teardown / shutdown owns this future
            handle = self._inflight.get(pf)
            if handle is None:
                return
            try:
                exc = pf.exception()
            except CancelledError:
                self._inflight.pop(pf, None)
                return
            if isinstance(exc, BrokenProcessPool):
                # Leave the handle in _inflight so the fault snapshot includes
                # it; _handle_fault decides reschedule-vs-dead-letter uniformly.
                handle.last_exc = exc
                do_fault = True
            else:
                self._inflight.pop(pf, None)
                self._resolve_caller(handle, pf, exc)
        if do_fault:
            # _on_done runs on the pool's executor-manager thread; recovery
            # tears that pool down (joining that very thread), so it must run
            # elsewhere — see _spawn_recovery.
            self._spawn_recovery(_FAULT_CRASH)
        else:
            self._pump()

    def _resolve_caller(
        self, handle: _JobHandle, pf: Future, exc: BaseException | None,
    ) -> None:
        caller = handle.caller_future
        if caller.done():
            return
        if exc is not None:
            caller.set_exception(exc)
            return
        try:
            caller.set_result(pf.result())
        except BaseException as e:  # noqa: BLE001
            caller.set_exception(e)

    def _watchdog_loop(self) -> None:
        while True:
            victim: _JobHandle | None = None
            with self._cond:
                if self._closed:
                    return
                now = _time_mod.monotonic()
                earliest: float | None = None
                for h in self._inflight.values():
                    if h.deadline <= now:
                        victim = h
                        break
                    if earliest is None or h.deadline < earliest:
                        earliest = h.deadline
                if victim is None:
                    wait_s = (
                        _WATCHDOG_IDLE_POLL_S if earliest is None
                        else max(0.0, earliest - now)
                    )
                    self._cond.wait(timeout=wait_s)
                    continue
                victim.last_exc = TimeoutError(
                    f"decode {victim.worker_fn_name!r} exceeded "
                    f"{self._timeout_fn():.0f}s",
                )
            # Release the lock before the (slow) fault handling. The watchdog
            # is its own thread, so a synchronous teardown here is safe (unlike
            # the _on_done path — see _spawn_recovery).
            self._handle_fault(_FAULT_TIMEOUT, victim=victim)

    def _spawn_recovery(self, reason: str, victim: _JobHandle | None = None) -> None:
        """Run fault recovery on a fresh daemon thread.

        Crash faults are detected in ``_on_done`` / a ``_pump`` submit, both of
        which can run on the pool's *executor-manager thread*. Recovery tears
        that pool down — ``shutdown(wait=True)`` joins the manager thread — so
        doing it inline would self-join (RuntimeError) and wedge the dispatcher
        with ``draining`` stuck True. Off-thread teardown sidesteps that. The
        ``draining`` guard in ``_handle_fault`` dedups concurrent spawns.
        """
        t = threading.Thread(
            target=self._handle_fault, args=(reason, victim),
            name="grib-decode-recovery", daemon=True,
        )
        t.start()

    def _handle_fault(self, reason: str, victim: _JobHandle | None = None) -> None:
        """Recover from a pool fault: keep completed, reschedule interrupted,
        dead-letter poison. The single place that decides each job's fate."""
        with self._cond:
            if self._draining or self._closed:
                return  # already being handled / shut down
            if victim is not None and victim not in self._inflight.values():
                # The victim's decode completed in the gap between the watchdog
                # releasing the lock and us acquiring it — _on_done already
                # resolved its caller and reaped it. There is no hang, so a
                # teardown would needlessly disrupt healthy concurrent jobs.
                return
            self._draining = True
            snapshot = list(self._inflight.values())
            self._inflight.clear()

        try:
            # Reuse the existing hang-diagnostics on the live (hung) pool before
            # we tear it down — output is unchanged from the legacy timeout path.
            # Read under the pool lock for consistency with every other access.
            with _DECODE_POOL_LOCK:
                pool = _DECODE_POOL
            if reason == _FAULT_TIMEOUT:
                names = ",".join(sorted({h.worker_fn_name for h in snapshot})) or "?"
                try:
                    _diag_snapshot_workers(pool, hang_context=f"dispatcher:{names}")
                except Exception:  # pragma: no cover — diag must never break recovery
                    logger.warning("dispatcher: hang-diag snapshot failed", exc_info=True)

            # Tear the pool down. TIMEOUT: workers are alive-but-stuck, wait=False
            # so recovery doesn't block on them. CRASH: workers are dead, wait=True
            # joins fast and avoids orphan accumulation.
            try:
                self._pool_teardown(wait=(reason != _FAULT_TIMEOUT))
            except Exception:  # pragma: no cover — never leave draining stuck True
                logger.warning("dispatcher: pool teardown failed", exc_info=True)

            rescheduled = 0
            dead = 0
            for h in snapshot:
                if self._closed:
                    # A drain() began mid-recovery: these jobs can't be
                    # rescheduled onto a pool that's going away. Fail them with
                    # the shutdown reason rather than a misleading retry_* code,
                    # so monitoring that discriminates on reason sees the truth.
                    self._dead_letter(h, "dispatcher_shutdown")
                    dead += 1
                elif victim is not None and h is victim:
                    # An identifiable hang: re-running re-hangs, so do NOT retry —
                    # this is what breaks the infinite-teardown loop a corrupt
                    # GRIB would otherwise cause.
                    self._dead_letter(h, "decode_hung")
                    dead += 1
                elif not self._retry_budget_ok():
                    self._dead_letter(h, "retry_budget_exhausted")
                    dead += 1
                elif h.retries >= _retry_cap():
                    self._dead_letter(h, "retry_cap_exhausted")
                    dead += 1
                else:
                    h.retries += 1
                    # Crash collateral may recur → back off with jitter. Timeout
                    # collateral was healthy (a sibling hung) → retry immediately
                    # on the fresh pool.
                    delay = 0.0 if reason == _FAULT_TIMEOUT else _jittered_backoff(h.retries)
                    self._reenqueue(h, after=delay)
                    rescheduled += 1

            logger.warning(
                "GRIB decode dispatcher fault (%s): victim=%s rescheduled=%d "
                "dead_lettered=%d (%s)",
                reason,
                victim.worker_fn_name if victim is not None else "none",
                rescheduled, dead, _diag_pool_summary(pool),
            )
        finally:
            with self._cond:
                self._draining = False
        self._pump()  # rebuilds the pool lazily; resumes pending in priority order

    def _retry_budget_ok(self) -> bool:
        """Sliding-window cap on retry RATE process-wide. Consumes one unit of
        budget when it returns True. Single-threaded: only ``_handle_fault``
        calls it, and the ``_draining`` flag serialises fault handling."""
        budget = _retry_budget()
        if budget <= 0:
            return False
        window = _retry_window_s()
        now = _time_mod.monotonic()
        while self._retry_times and (now - self._retry_times[0]) > window:
            self._retry_times.popleft()
        if len(self._retry_times) >= budget:
            return False
        self._retry_times.append(now)
        return True

    def _reenqueue(self, handle: _JobHandle, *, after: float) -> None:
        """Return an interrupted job to the pending heap (optionally delayed).

        A new ``seq`` puts the retry at the back of its priority level so a
        retried job can't starve fresh same-priority work. If the dispatcher is
        closing, the caller is failed instead of left waiting — both the
        immediate and the delayed path check ``_closed`` under the lock, and a
        backed-off handle is tracked in ``_delayed_handles`` so ``drain()`` can
        release it without waiting for the timer."""
        if after <= 0:
            with self._cond:
                closed = self._closed
                if not closed:
                    handle.seq = self._next_seq_locked()
                    heapq.heappush(self._pending, (handle.priority, handle.seq, handle))
                    self._cond.notify_all()
            if closed:
                self._fail_caller(handle, "dispatcher_shutdown")
            return

        with self._cond:
            closed = self._closed
            if not closed:
                self._delayed_handles.add(handle)
        if closed:
            self._fail_caller(handle, "dispatcher_shutdown")
            return

        def _delayed_push() -> None:
            with self._cond:
                self._delayed_handles.discard(handle)
                closed = self._closed
                if not closed:
                    handle.seq = self._next_seq_locked()
                    heapq.heappush(self._pending, (handle.priority, handle.seq, handle))
                    self._cond.notify_all()
            if closed:
                self._fail_caller(handle, "dispatcher_shutdown")
                return
            self._pump()

        t = threading.Timer(after, _delayed_push)
        t.daemon = True
        t.start()

    def _dead_letter(self, handle: _JobHandle, reason: str) -> None:
        """Give up on a job: fail the caller future + emit a structured WARNING
        and a per-reason counter. The DLQ-equivalent — never a silent drop."""
        _record_dead_letter(reason)
        logger.warning(
            "GRIB decode dead-letter: fn=%s reason=%s retries=%d args=%s last_exc=%r",
            handle.worker_fn_name, reason, handle.retries,
            _summarize_args(handle.args), handle.last_exc,
        )
        if not handle.caller_future.done():
            handle.caller_future.set_exception(
                DecodeDispatchError(reason, handle.worker_fn_name, handle.last_exc),
            )


_DISPATCHER: PriorityDecodeDispatcher | None = None
_DISPATCHER_LOCK = threading.Lock()


def _get_dispatcher() -> PriorityDecodeDispatcher:
    """Lazy process-wide dispatcher singleton (recreated if a prior one was
    drained at shutdown). ``_closed`` is written under the instance's ``_cond``
    but read here under ``_DISPATCHER_LOCK`` — a cross-lock read that is safe
    because a bool load is atomic under the GIL and ``submit_*`` recheck
    ``_closed`` under ``_cond``, so a returned-then-closed instance fails fast."""
    global _DISPATCHER
    with _DISPATCHER_LOCK:
        if _DISPATCHER is None or _DISPATCHER._closed:
            _DISPATCHER = PriorityDecodeDispatcher()
        return _DISPATCHER


def _drain_dispatcher_for_shutdown() -> None:
    with _DISPATCHER_LOCK:
        d = _DISPATCHER
    if d is not None:
        d.drain()


def _dispatch_decode(
    worker_fn_name: str, *args, priority: int | DecodePriority | None = None,
) -> Any:
    """Submit one decode job; block on its result (call-site shape unchanged).

    Routes through the :class:`PriorityDecodeDispatcher` by default. With
    ``GRIB_DECODE_PRIORITY_ENABLED=0`` it falls back to the legacy FIFO path.
    ``priority`` defaults to the ``_DECODE_PRIORITY`` ContextVar (else
    SCHEDULED).
    """
    if not _priority_enabled():
        return _dispatch_decode_legacy(worker_fn_name, *args)
    eff = _resolve_priority(priority)
    return _get_dispatcher().submit_one(worker_fn_name, args, eff).result()


def _dispatch_decode_parallel(
    jobs: list[tuple[str, tuple]],
    *,
    priority: int | DecodePriority | None = None,
    return_exceptions: bool = False,
    max_inflight: int | None = None,
) -> list[Any]:
    """Submit a batch of decode jobs; return results in input order.

    Routes through the dispatcher by default (per-job timeout + fault
    rescheduling). With ``GRIB_DECODE_PRIORITY_ENABLED=0`` it falls back to the
    legacy FIFO batch path (one shared deadline).

    ``return_exceptions=True`` (asyncio.gather-style) isolates per-job
    failures: a job that ultimately fails (post-retry dead-letter) yields its
    exception instance in that slot instead of raising and discarding its
    siblings' results. On the legacy path, whose fault handling is shared-fate
    by design (one deadline, batch-wide cancel), a failure is represented as
    the same exception in every slot.

    ``max_inflight`` (#459) throttles concurrency **below** the pool for a
    memory-heavy loop: ``None`` submits the whole batch and lets the pool size
    (``GRIB_DECODE_WORKERS``) bound concurrency; an int keeps at most that many
    jobs in flight, submitting the next as each completes (sliding window). The
    pool is always the hard ceiling — a window ``>=`` the pool size is a no-op.
    Ignored on the legacy FIFO path (a rollback switch); there the pool size
    alone bounds concurrency.
    """
    if not jobs:
        return []
    if not _priority_enabled():
        if not return_exceptions:
            return _dispatch_decode_parallel_legacy(jobs)
        try:
            return _dispatch_decode_parallel_legacy(jobs)
        except Exception as exc:
            return [exc for _ in jobs]
    eff = _resolve_priority(priority)
    dispatcher = _get_dispatcher()
    if max_inflight is not None and 0 < max_inflight < len(jobs):
        return _collect_windowed(dispatcher, jobs, eff, max_inflight, return_exceptions)
    futures = dispatcher.submit_batch(jobs, eff)
    if not return_exceptions:
        return [f.result() for f in futures]
    results: list[Any] = []
    for f in futures:
        try:
            results.append(f.result())
        except Exception as exc:
            results.append(exc)
    return results


def _collect_windowed(
    dispatcher: PriorityDecodeDispatcher,
    jobs: list[tuple[str, tuple]],
    priority: int,
    max_inflight: int,
    return_exceptions: bool,
) -> list[Any]:
    """Sliding-window batch dispatch (below the pool ceiling).

    Keeps at most ``max_inflight`` caller futures outstanding at once,
    submitting the next job as each completes. Results are returned in input
    order. This bounds the number of full GRIB grids decoded concurrently for a
    memory-heavy loop even when the pool (``GRIB_DECODE_WORKERS``) is wider.

    ``return_exceptions`` mirrors :func:`_dispatch_decode_parallel`: on
    ``False`` the first failing job re-raises (siblings still in flight are
    abandoned, exactly as ``[f.result() for f in futures]`` would); on ``True``
    each failure lands as an exception instance in its slot.
    """
    from concurrent.futures import FIRST_COMPLETED
    from concurrent.futures import wait as _futures_wait

    results: list[Any] = [None] * len(jobs)
    fut_to_idx: dict[Future, int] = {}
    live: set[Future] = set()
    next_job = 0

    def _submit(idx: int) -> None:
        name, args = jobs[idx]
        fut = dispatcher.submit_one(name, args, priority)
        fut_to_idx[fut] = idx
        live.add(fut)

    while next_job < len(jobs) and len(live) < max_inflight:
        _submit(next_job)
        next_job += 1

    while live:
        done, live = _futures_wait(live, return_when=FIRST_COMPLETED)
        for fut in done:
            idx = fut_to_idx[fut]
            try:
                results[idx] = fut.result()
            except Exception as exc:
                if not return_exceptions:
                    raise
                results[idx] = exc
            if next_job < len(jobs):
                _submit(next_job)
                next_job += 1
    return results


def _dispatch_decode_legacy(worker_fn_name: str, *args) -> Any:
    """FIFO single-job dispatch — the pre-#171 path (rollback / kill switch).

    Reached when ``GRIB_DECODE_PRIORITY_ENABLED=0``. Submits straight to the
    pool with a per-call ``GRIB_DECODE_TIMEOUT_S`` and resets the pool on
    fault. The priority dispatcher (default) wraps this same pool but adds
    priority ordering + fault rescheduling on top.

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
        _diag_register_workers(pool)
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


def _dispatch_decode_parallel_legacy(jobs: list[tuple[str, tuple]]) -> list[Any]:
    """FIFO batch dispatch — the pre-#171 path (rollback / kill switch).

    Reached when ``GRIB_DECODE_PRIORITY_ENABLED=0``.

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
        _diag_register_workers(pool)
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
    priority: int | DecodePriority | None = None,
) -> tuple[dict[str, int], dict[str, str], dict[str, str]]:
    """Enrich cross-section forecasts with cloud water from GRIB2 sources.

    Enriches GFS cross-sections with CLWMR/ICMR and cloud diagnostics.
    Enriches ICON cross-sections with QC/QI when the route is within a DWD ICON
    domain — from ICON-D2 (2.2 km) when the whole route fits the D2 domain and
    the flight window is within 48h, otherwise ICON-EU (issue #456).

    This modifies PressureLevelData and HourlyForecast objects in-place.

    Args:
        cross_sections: Route cross-sections to enrich (modified in-place).
        all_forecasts: Waypoint forecasts (also enriched in-place).
        route_points: Route points for spatial interpolation.
        departure_time: Aware UTC datetime of flight departure.
        data_dir: Base data directory for caching.
        flight_duration_hours: Flight duration for per-hour enrichment.
        as_of_time: If set, only use model runs initialized before this time.
        priority: Decode priority for this call's GRIB jobs. ``None`` (default)
            resolves to the ``_DECODE_PRIORITY`` ContextVar set by the entry
            point (INTERACTIVE for user refresh / airport profile, SCHEDULED
            for auto-refresh), else SCHEDULED. The value is published on the
            ContextVar for the duration so Phase-1 worker threads (which inherit
            the context via ``_submit_with_context``) dispatch at the right
            level.

    Returns:
        Tuple of (grib_init_times, grib_skip_reasons, grib_sources):
        - grib_init_times: model name → GRIB init Unix timestamp.
        - grib_skip_reasons: model name → skip reason string (e.g. "out_of_range").
        - grib_sources: model name → freshness source key actually used
          (e.g. ``"icon"`` → ``"icon_eu:dwd"`` or ``"icon_d2:dwd"``). Lets pack
          building attribute the icon slot to the right variant.
    """
    timer = _GribTimer()
    token = _GRIB_TIMER.set(timer)
    ptoken = _DECODE_PRIORITY.set(_resolve_priority(priority))
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
        _DECODE_PRIORITY.reset(ptoken)
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
) -> tuple[dict[str, int], dict[str, str], dict[str, str]]:
    """Inner body of enrich_forecasts; assumes _GRIB_TIMER is set to *timer*."""
    grib_init_times: dict[str, int] = {}
    grib_skip_reasons: dict[str, str] = {}
    # model → freshness source key actually used for the direct-GRIB run. The
    # icon slot varies (icon_eu:dwd vs icon_d2:dwd) so it can't be inferred from
    # the model name alone — pack recording reads this (issue #456).
    grib_sources: dict[str, str] = {}

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

    # Prepare ICON-EU context (run discovery, domain check, etc.). When the
    # context is None, icon_prepare_skip classifies why (out_of_domain /
    # out_of_range / None) so the fetch stage can emit an accurate diagnostic.
    with _grib_time("icon_prepare"):
        icon_ctx, icon_prepare_skip = _prepare_icon_eu(
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
        grib_sources["ecmwf"] = "ecmwf:direct"

    # Phase 2: Decode ICON sequentially (memory-heavy, GFS is done).
    active_icon_variant = icon_ctx.variant if icon_ctx is not None else None
    with _grib_time("phase2_icon_decode"):
        if icon_ctx is not None:
            icon_ts, icon_skip = _decode_and_merge_icon_eu(
                icon_ctx, cross_sections, all_forecasts, route_points,
            )
            # Fallback robustness (#456): if ICON-D2 was selected but produced
            # NO enrichment at all (D2 feed hiccup / decode failure), re-run the
            # whole icon slot on ICON-EU rather than leave it un-enriched — never
            # a half-D2 pack. A partial D2 success (some hours) keeps D2 and lets
            # the normal time/spatial fill cover gaps, same as ICON-EU today.
            if icon_ts is None and active_icon_variant is not None \
                    and active_icon_variant.slug == "icon-d2":
                from weatherbrief.fetch.grib.icon_eu_fetch import ICON_EU
                logger.warning(
                    "ICON-D2 enrichment produced nothing; falling back to ICON-EU",
                )
                eu_ctx, eu_skip = _prepare_icon_eu(
                    cross_sections, route_points, departure_time,
                    data_dir=data_dir, flight_duration_hours=flight_duration_hours,
                    as_of_time=as_of_time, force_variant=ICON_EU,
                )
                if eu_ctx is not None:
                    _prefetch_icon_eu_data(eu_ctx)
                    icon_ts, icon_skip = _decode_and_merge_icon_eu(
                        eu_ctx, cross_sections, all_forecasts, route_points,
                    )
                    active_icon_variant = eu_ctx.variant
                elif eu_skip is not None:
                    icon_skip = eu_skip
        else:
            icon_ts, icon_skip = None, icon_prepare_skip
    timer.rss_mark("after_phase2")

    if gfs_ts is not None:
        grib_init_times["gfs"] = gfs_ts
        grib_sources["gfs"] = "gfs:noaa"
    if icon_ts is not None:
        grib_init_times["icon"] = icon_ts
        if active_icon_variant is not None:
            grib_sources["icon"] = active_icon_variant.source_key
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
    return grib_init_times, grib_skip_reasons, grib_sources


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

    # State for step-difference of accumulated surface fields (tp, sf, conv
    # precip cp) across consecutive a1 files. None = no prior step for that point.
    n_points = len(route_points)
    prev_tp_per_point: list[float | None] = [None] * n_points
    prev_sf_per_point: list[float | None] = [None] * n_points
    prev_cp_per_point: list[float | None] = [None] * n_points  # conv precip (#283)
    prev_a1_valid_utc: datetime | None = None

    # Filter steps to the flight window (with margin) up-front so we can
    # fan out all decodes in parallel before merging.
    margin = ECMWF_FLIGHT_WINDOW_MARGIN
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
                # Convective precip rate (#283): cp is accumulated since init, so
                # difference it against the previous a1 step (mind the variable
                # step cadence) and inject the mm/h rate onto the just-built
                # diagnostics so it rides the same forward-fill / spatial-interp
                # path as the rest of the cloud diagnostics.
                cp_window_h: float | None = None
                if prev_a1_valid_utc is not None:
                    _dh = (valid_time - prev_a1_valid_utc).total_seconds() / 3600.0
                    if _dh > 0:
                        cp_window_h = _dh
                if cp_window_h is not None:
                    # diagnostics, sfc_data and prev_cp_per_point are all sized to
                    # n_points (route_points), so index alignment is guaranteed —
                    # the real per-point guard is the cov/raw/diag_i check below.
                    for i, (raw, cov) in enumerate(zip(sfc_data, sfc_covered)):
                        diag_i = diagnostics[i]
                        if not cov or not raw or diag_i is None:
                            continue
                        cp = raw.get("conv_precip_m")
                        pcp = prev_cp_per_point[i]
                        if cp is not None and pcp is not None:
                            # Reconstruct rather than mutate in place so the
                            # immutability contract of NWPCloudDiagnostics holds
                            # even if it is ever frozen (review #284).
                            diagnostics[i] = diag_i.model_copy(
                                update={
                                    "convective_precip_mm_h":
                                        max(0.0, (cp - pcp) / cp_window_h) * 1000.0
                                }
                            )
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
                    cp = raw.get("conv_precip_m")
                    if cp is not None:
                        prev_cp_per_point[i] = cp
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
    "nwp_k_index",
    "nwp_total_totals",
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
    """Holds resolved ICON run info (EU or D2) for split download/decode phases."""

    __slots__ = (
        "init_date", "init_hour", "forecast_hours", "run_dir",
        "levels", "point_lats", "point_lons", "session", "variant",
    )

    def __init__(
        self, init_date: str, init_hour: int, forecast_hours: list[int],
        run_dir: Path, levels: list[int],
        point_lats: list[float], point_lons: list[float],
        session: requests.Session, variant,
    ):
        self.init_date = init_date
        self.init_hour = init_hour
        self.forecast_hours = forecast_hours
        self.run_dir = run_dir
        self.levels = levels
        self.point_lats = point_lats
        self.point_lons = point_lons
        self.session = session
        # IconVariant (ICON_EU / ICON_D2) chosen for this briefing's icon slot.
        self.variant = variant


def _d2_corridor_mask_ok(
    route_points: list[RoutePoint],
    init_date: str,
    init_hour: int,
    *,
    data_dir: Path,
    session: requests.Session,
    variant,
) -> bool:
    """Domain-gate hardening (#462): corridor-buffer check against the D2 bitmap.

    The D2 regular-lat-lon product masks ~17% of cells (native domain is not a
    lat/lon rectangle, and the delivered files carry no rotated-pole metadata),
    so a route can pass the bbox gate yet clip masked corner cells. This builds
    a validity mask ONCE from a delivered message's bitmap (an invariant of the
    product — any field carries the same bitmap), caches it beside the GRIB
    cache, and requires the route's ENTIRE corridor buffer (not just the
    centreline) to lie in valid cells.

    Returns False only when the mask POSITIVELY shows the corridor clipping
    masked cells (→ caller falls back to ICON-EU, all-or-nothing). Any failure
    to obtain the mask fails OPEN (True, logged): behaviour is then no worse
    than the pre-#462 bbox gate — corner points decode as unavailable rather
    than wrong — and a DWD hiccup on one probe file can't flip the whole slot.
    """
    import numpy as np

    from weatherbrief.fetch.grib.decode import (
        build_d2_validity_mask,
        d2_corridor_fully_valid,
    )
    from weatherbrief.fetch.grib.icon_eu_fetch import fetch_icon_eu_single_level

    mask_path = data_dir / ".cache" / "grib" / f"{variant.slug}-validity-mask-v1.npz"
    mask: dict | None = None
    try:
        if mask_path.exists():
            with np.load(mask_path) as npz:
                mask = {"lats": npz["lats"], "lons": npz["lons"], "valid": npz["valid"]}
    except Exception:
        logger.warning("Failed to load cached D2 validity mask", exc_info=True)
        mask = None

    if mask is None:
        try:
            fetched = fetch_icon_eu_single_level(
                init_date, init_hour, [0], variables=["ceiling"],
                session=session, variant=variant,
            )
            grib_bytes = fetched.get(0)
            if grib_bytes:
                mask = build_d2_validity_mask(grib_bytes)
            if mask is not None:
                mask_path.parent.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(
                    mask_path,
                    lats=mask["lats"], lons=mask["lons"], valid=mask["valid"],
                )
        except Exception:
            logger.warning("Failed to build D2 validity mask", exc_info=True)
            mask = None

    if mask is None:
        logger.info("D2 validity mask unavailable; keeping bbox-gate behaviour")
        return True

    ok = d2_corridor_fully_valid(
        mask,
        [rp.lat for rp in route_points],
        [rp.lon for rp in route_points],
    )
    if not ok:
        logger.info(
            "Route corridor clips masked ICON-D2 cells; falling back to ICON-EU",
        )
    return ok


def _prepare_icon_eu(
    cross_sections: list[RouteCrossSection],
    route_points: list[RoutePoint],
    departure_time: datetime,
    *,
    data_dir: Path,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
    force_variant=None,
) -> tuple[_IconEuContext | None, str | None]:
    """Resolve the ICON run (EU or D2) for the icon slot and check eligibility.

    The ``icon`` slot is served by **ICON-D2** (2.2 km, convection-permitting)
    when the *whole* route fits the D2 domain AND a complete D2 run's 48h
    horizon reaches the flight-window end (issue #456 all-or-nothing gate);
    otherwise by **ICON-EU** exactly as before. Never a per-point mix.

    ``force_variant`` pins the variant (used by the total-D2-failure fallback to
    re-run cleanly on ICON-EU) and skips the D2 gate.

    Returns ``(context, skip_reason)``. When the context is None the enrichment
    is skipped; ``skip_reason`` classifies *why* so the fetch stage can emit an
    accurate diagnostic instead of the generic "unavailable for this model"
    warning:

    - ``"out_of_domain"`` — route lies outside the chosen grid (expected for
      non-European routes on the EU fallback).
    - ``"out_of_range"`` — flight window is beyond the chosen model's horizon
      (EU 120h / D2 48h).
    - ``None`` — no ICON sections, or a genuine failure (run-finder raised, or
      a run should exist but couldn't be probed); the generic warning stands.
    """
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_D2,
        ICON_EU,
        compute_icon_eu_flight_window_hours,
        find_latest_icon_eu_run,
        icon_eu_window_out_of_range,
        route_in_icon_eu_domain,
    )

    icon_sections = [cs for cs in cross_sections if cs.model == ModelSource.ICON]
    if not icon_sections:
        logger.debug("No ICON cross-sections to enrich")
        return None, None

    session = _grib_session()
    cover_until = departure_time + timedelta(hours=flight_duration_hours)

    # Variant selection. Prefer ICON-D2 when the whole route fits its domain and
    # a complete D2 run covers the window; the run is resolved here (not
    # re-probed) so we never pick D2 without a usable run. Otherwise ICON-EU.
    variant = force_variant
    run_info: tuple[str, int] | None = None
    if variant is None:
        variant = ICON_EU
        if route_in_icon_eu_domain(route_points, ICON_D2):
            try:
                d2_run = find_latest_icon_eu_run(
                    departure_time, session=session, as_of_time=as_of_time,
                    cover_until=cover_until, variant=ICON_D2,
                )
            except Exception:
                logger.warning("Failed to find ICON-D2 run; using ICON-EU", exc_info=True)
                d2_run = None
            if d2_run is not None and not _d2_corridor_mask_ok(
                route_points, d2_run[0], d2_run[1],
                data_dir=data_dir, session=session, variant=ICON_D2,
            ):
                # Bbox passed but the corridor buffer clips bitmap-masked
                # corner cells (#462 domain-gate hardening) → same
                # all-or-nothing rule as the bbox gate: the whole slot runs
                # on ICON-EU, never a per-point mix.
                d2_run = None
            if d2_run is not None:
                variant, run_info = ICON_D2, d2_run
                logger.info(
                    "ICON slot sourced from ICON-D2 (route in D2 domain, run %s %02dz)",
                    d2_run[0], d2_run[1],
                )

    if not route_in_icon_eu_domain(route_points, variant):
        logger.info("Route outside %s domain, skipping ICON enrichment", variant.slug)
        return None, "out_of_domain"

    if run_info is None:
        try:
            run_info = find_latest_icon_eu_run(
                departure_time, session=session, as_of_time=as_of_time,
                cover_until=cover_until, variant=variant,
            )
        except Exception:
            logger.warning("Failed to find %s model run", variant.slug, exc_info=True)
            return None, None

    if run_info is None:
        # The run-finder returns None both when the window is past the model's
        # horizon and when a run should exist but the probe failed. Only the
        # former is an expected "out of range" skip — classify deterministically.
        if icon_eu_window_out_of_range(
            departure_time, flight_duration_hours, as_of_time, variant,
        ):
            logger.info("Flight window beyond %s horizon, skipping ICON enrichment", variant.slug)
            return None, "out_of_range"
        logger.info("No %s run found that covers the flight window", variant.slug)
        return None, None

    init_date, init_hour = run_info

    forecast_hours = compute_icon_eu_flight_window_hours(
        init_date, init_hour, departure_time, flight_duration_hours, variant,
    )

    purge_old_runs(data_dir, model=variant.slug)
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model=variant.slug)
    levels = list(range(variant.level_min, variant.level_max + 1))

    return _IconEuContext(
        init_date=init_date, init_hour=init_hour,
        forecast_hours=forecast_hours, run_dir=run_dir, levels=levels,
        point_lats=[rp.lat for rp in route_points],
        point_lons=[rp.lon for rp in route_points],
        session=session, variant=variant,
    ), None


def _prefetch_icon_eu_data(ctx: _IconEuContext) -> None:
    """Download ICON-EU GRIB2 data and cache to disk (no decode).

    Runs in a background thread while GFS enrichment proceeds.
    """
    with _grib_time("icon_prefetch"):
        _prefetch_icon_eu_data_inner(ctx)


def _icon_prefetch_workers() -> int:
    """Concurrent (fhour, variable) prefetch units. ``GRIB_ICON_PREFETCH_WORKERS``
    overrides; ``1`` restores the previous strictly-serial behaviour."""
    raw = os.environ.get("GRIB_ICON_PREFETCH_WORKERS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            logger.warning("Invalid GRIB_ICON_PREFETCH_WORKERS=%r, defaulting to 4", raw)
    return 4


def _prefetch_icon_eu_data_inner(ctx: _IconEuContext) -> None:
    """Download all uncached ICON-EU units for this run.

    Cold-cache fetch tail fix: the per-(fhour, variable) downloads used to run
    strictly one after another — 45 sequential units of ~1-3.5s each put the
    fetch stage's 130-226s prod tail almost entirely in this loop. Units are
    independent (distinct cache keys, atomic ``put_cached``), so they now run
    on a small outer pool. Each unit keeps its own inner per-level download
    pool, sized down so outer x inner stays within the session's connection
    pool (_POOL_MAXSIZE) for any worker setting. Memory stays bounded: at
    most ``outer`` per-variable buffers in flight, written to disk on
    completion.
    """
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU_VARIABLES,
        fetch_icon_eu_per_variable,
        fetch_icon_eu_single_level,
        icon_cloud_diag_cache_key,
        icon_eu_previous_step,
        icon_explicit_conv_cache_key,
    )

    variant = ctx.variant
    prefix = variant.cache_prefix
    diag_key = icon_cloud_diag_cache_key(variant)

    outer = _icon_prefetch_workers()
    # Keep outer x inner within the session's connection pool for ALL outer
    # values (default 4 x 5 = 20), not just the default — a user-set
    # GRIB_ICON_PREFETCH_WORKERS must not silently exceed _POOL_MAXSIZE.
    inner = max(1, _POOL_MAXSIZE // outer) if outer > 1 else 8

    def _fetch_var(fhour: int, var: str, ck: str) -> None:
        try:
            with _grib_time("icon_prefetch_var"):
                per_var = fetch_icon_eu_per_variable(
                    ctx.init_date, ctx.init_hour, fhour,
                    levels=ctx.levels,
                    variables=[var],
                    session=ctx.session,
                    max_workers=inner,
                    variant=variant,
                )
            data = per_var.get(var)
            if data:
                put_cached(ctx.run_dir, ck, data)
        except Exception:
            logger.warning("Prefetch %s f%03d %s failed", variant.slug, fhour, var, exc_info=True)

    def _fetch_diag(fhour: int, ck: str) -> None:
        try:
            with _grib_time("icon_prefetch_cloud_diag"):
                fetched = fetch_icon_eu_single_level(
                    ctx.init_date, ctx.init_hour, [fhour],
                    session=ctx.session,
                    max_workers=inner,
                    variant=variant,
                )
            grib_bytes = fetched.get(fhour)
            if grib_bytes:
                put_cached(ctx.run_dir, ck, grib_bytes)
        except Exception:
            logger.warning("Prefetch %s cloud diag f%03d failed", variant.slug, fhour, exc_info=True)

    def _fetch_expl(fhour: int, var: str, ck: str) -> None:
        # Explicit-convection fields (#462) are fetched one variable per blob:
        # the decoder must know which physical field each blob holds (several
        # are multi-message sub-hourly files selected by stepRange, and DWD's
        # eccodes shortName mappings are not trustworthy for matching).
        try:
            with _grib_time("icon_prefetch_explicit_conv"):
                fetched = fetch_icon_eu_single_level(
                    ctx.init_date, ctx.init_hour, [fhour],
                    variables=[var],
                    session=ctx.session,
                    max_workers=inner,
                    variant=variant,
                )
            grib_bytes = fetched.get(fhour)
            if grib_bytes:
                put_cached(ctx.run_dir, ck, grib_bytes)
        except Exception:
            logger.warning(
                "Prefetch %s explicit-conv %s f%03d failed",
                variant.slug, var, fhour, exc_info=True,
            )

    def _explicit_jobs(fhour: int) -> list[tuple]:
        return [
            (_fetch_expl, fhour, var, ck)
            for var in variant.explicit_conv_variables
            for ck in [cache_key(fhour, icon_explicit_conv_cache_key(var, variant))]
            if not is_cached(ctx.run_dir, ck)
        ]

    jobs: list[tuple] = []
    for fhour in ctx.forecast_hours:
        # Model-level data (P, QC, QI) — per variable
        legacy_ck = cache_key(fhour, f"{prefix}_QC_QI_P")
        if not is_cached(ctx.run_dir, legacy_ck):  # legacy cache hit skips per-var download
            for var in ICON_EU_VARIABLES:
                ck = cache_key(fhour, f"{prefix}_{var.upper()}")
                if not is_cached(ctx.run_dir, ck):
                    jobs.append((_fetch_var, fhour, var, ck))

        # Single-level cloud diagnostics
        diag_ck = cache_key(fhour, diag_key)
        if not is_cached(ctx.run_dir, diag_ck):
            jobs.append((_fetch_diag, fhour, diag_ck))

        # Explicit-convection storm diagnostics (D2 only, #462)
        jobs.extend(_explicit_jobs(fhour))

    # One leading single-level step so the first window hour has a predecessor
    # (variant-level flag, #421/#462): rain_con (EU) needs it to de-accumulate
    # the first hour, and the D2 hourly echo top needs the previous file's
    # three quarter-hour windows. Single-level fetches only —
    # the model-level sounding list above is untouched. Each list is fetched
    # for the lead step only when that list is what needs the predecessor.
    if ctx.forecast_hours and variant.needs_predecessor_step:
        lead = icon_eu_previous_step(min(ctx.forecast_hours), variant)
        if lead is not None and lead not in ctx.forecast_hours:
            if "rain_con" in variant.cloud_diag_variables:
                lead_ck = cache_key(lead, diag_key)
                if not is_cached(ctx.run_dir, lead_ck):
                    jobs.append((_fetch_diag, lead, lead_ck))
            jobs.extend(_explicit_jobs(lead))

    if not jobs:
        return
    if outer == 1 or len(jobs) == 1:
        for fn, *args in jobs:
            fn(*args)
        return

    with ThreadPoolExecutor(max_workers=outer, thread_name_prefix="icon-prefetch") as pool:
        futures = [_submit_with_context(pool, fn, *args) for fn, *args in jobs]
        for fut in futures:
            fut.result()  # job functions never raise; .result() surfaces bugs


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

    variant = ctx.variant
    prefix = variant.cache_prefix
    icon_sections = [cs for cs in cross_sections if cs.model == ModelSource.ICON]

    # CLC-derived cloud layers are a time-VARYING forecast field, so keep each
    # forecast hour's own geometry (keyed by fhour) instead of collapsing to a
    # single per-point set. Each valid time is enriched with its own layers
    # below — reusing one hour's clouds for every hour was stale. (#441 #4)
    n_points = len(ctx.point_lats)
    clc_layers_by_fhour: dict[int, list[dict[str, float]]] = {}

    # Build per-fhour decode jobs up-front. A given fhour either has the
    # legacy combined cache (one ``decode_icon_legacy`` job) or per-variable
    # caches (one ``decode_icon_chunked`` job); fhours with neither are
    # skipped. The decode bytes are read inside the worker.
    fhour_jobs: dict[int, tuple[str, tuple]] = {}
    for fhour in ctx.forecast_hours:
        legacy_ck = cache_key(fhour, f"{prefix}_QC_QI_P")
        if is_cached(ctx.run_dir, legacy_ck):
            fhour_jobs[fhour] = (
                "decode_icon_legacy",
                (str(ctx.run_dir / legacy_ck), ctx.point_lats, ctx.point_lons),
            )
            continue

        var_paths: dict[str, str] = {}
        for var in ICON_EU_VARIABLES:
            ck = cache_key(fhour, f"{prefix}_{var.upper()}")
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

        # Keep THIS forecast hour's own CLC-derived layers (per valid time).
        if any(clc_layers):
            clc_layers_by_fhour[fhour] = clc_layers

        valid_utc = _forecast_hour_to_utc(ctx.init_date, ctx.init_hour, fhour)
        replaced = _replace_pressure_levels_from_grib(
            icon_sections, all_forecasts, route_points, decoded_points,
            valid_utc=valid_utc, model_source=ModelSource.ICON,
        )
        total_enriched += replaced
        del decoded_points
        # Release the decoded_points reference (already del'd locally). The
        # clc_layers list is now owned by ``clc_layers_by_fhour`` (small: a few
        # float bases/tops per point), so it stays alive until enrichment.
        decoded_by_fhour[fhour] = None
    _grib_gc()
    _grib_rss_mark("icon_fhour_post_gc")

    if not total_enriched:
        logger.warning("No %s GRIB2 data retrieved for enrichment", variant.slug)
        return None, None

    logger.info(
        "GRIB2 ICON full sounding replacement (%s): %d hourly entries replaced",
        variant.slug, total_enriched,
    )

    # Cloud diagnostics (ceiling, convective base/top) from single-level files.
    # Pass CLC-derived layer boundaries to fill missing NWP base/top.
    _enrich_icon_eu_cloud_diagnostics(
        icon_sections, all_forecasts, route_points,
        ctx.init_date, ctx.init_hour, ctx.forecast_hours,
        ctx.run_dir, ctx.point_lats, ctx.point_lons, ctx.session,
        clc_layers_by_fhour=clc_layers_by_fhour, variant=variant,
    )

    # Explicit-convection storm diagnostics (ICON-D2 only, #462). Failure here
    # never triggers the total-failure EU fallback: the D2 sounding replacement
    # above already succeeded, and a missing explicit channel is an honest
    # "explicit assessment unavailable" per hour, not a broken slot.
    if variant.explicit_conv_variables:
        _enrich_icon_d2_explicit_convective(
            icon_sections, all_forecasts, route_points,
            ctx.init_date, ctx.init_hour, ctx.forecast_hours,
            ctx.run_dir, ctx.point_lats, ctx.point_lons, ctx.session,
            variant=variant,
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
    clc_layers_by_fhour: dict[int, list[dict[str, float]]] | None = None,
    variant=None,
) -> None:
    """Enrich ICON forecasts with single-level cloud diagnostics (ceiling, etc.).

    If *clc_layers_by_fhour* is provided (CLC-derived cloud layer boundaries
    from model-level data, keyed by forecast hour), missing ``base_ft``/
    ``top_ft`` on low/mid/high NWPCloudLayerDiag are filled from THAT forecast
    hour's geometry — clouds evolve over time, so each valid time uses its own.

    *variant* selects ICON-EU vs ICON-D2 URL/cache conventions (defaults to EU).
    """
    from weatherbrief.fetch.grib.decode import build_icon_cloud_diagnostics
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU,
        fetch_icon_eu_single_level,
        icon_cloud_diag_cache_key,
        icon_eu_conv_rain_rate_mm_h,
        icon_eu_previous_step,
    )

    if variant is None:
        variant = ICON_EU
    diag_key = icon_cloud_diag_cache_key(variant)

    # Prepend one leading step so the first window hour has a predecessor to
    # de-accumulate rain_con against, then walk steps in sorted order carrying
    # the previous accumulated value + valid time — a direct port of the ECMWF
    # a1 loop (#421). The leading step is a harmless no-op for enrichment:
    # _matches_valid_time never matches an out-of-window hourly, so it only
    # seeds the de-accumulation state.
    steps = sorted(set(forecast_hours))
    if steps and "rain_con" in variant.cloud_diag_variables:
        lead = icon_eu_previous_step(steps[0], variant)
        if lead is not None and lead not in steps:
            steps.insert(0, lead)

    n_points = len(point_lats)
    # None = no prior step for that point (unknown, missing-data-safe for the
    # firing gate). Not 0.0 — a real 0.0 would actively hold a tower down.
    prev_rain_con_per_point: list[float | None] = [None] * n_points
    prev_valid_utc: datetime | None = None

    total_enriched = 0
    for fhour in steps:
        ck = cache_key(fhour, diag_key)
        if not is_cached(run_dir, ck):
            try:
                fetched = fetch_icon_eu_single_level(
                    init_date, init_hour, [fhour], session=session,
                    variant=variant,
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

        valid_utc = _forecast_hour_to_utc(init_date, init_hour, fhour)

        # Convective precip rate (#421): rain_con is accumulated since init
        # (kg/m² ≡ mm), so difference it against the previous step and inject the
        # mm/h rate onto the just-built diagnostics so it rides the same
        # forward-fill / spatial-interp path as the rest of the cloud
        # diagnostics. Compute the window from the two valid times — ICON drops
        # to 3-hourly past +78h. The rate helper does the mm/h conversion
        # (already mm — NO ×1000, unlike ECMWF `cp`) and the None-vs-0.0
        # missing-data handling.
        window_h: float | None = None
        if prev_valid_utc is not None:
            _dh = (valid_utc - prev_valid_utc).total_seconds() / 3600.0
            if _dh > 0:
                window_h = _dh
        for i, raw in enumerate(decoded_points):
            diag_i = diagnostics_per_point[i]
            if diag_i is None:
                continue
            rate = icon_eu_conv_rain_rate_mm_h(
                raw.get("conv_rain_kg_m2"), prev_rain_con_per_point[i], window_h,
            )
            if rate is not None:
                # Reconstruct rather than mutate so the NWPCloudDiagnostics
                # immutability contract holds (review #284).
                diagnostics_per_point[i] = diag_i.model_copy(
                    update={"convective_precip_mm_h": rate}
                )
        # Carry this step's cumulative rain_con + valid time forward. Assign
        # unconditionally: a point that lacks rain_con at this step (e.g. the DWD
        # rain_con file failed for this hour while the other cloud-diag vars
        # succeeded, or the point is uncovered) resets its predecessor to None,
        # so the NEXT step differences against nothing → None (missing-data-safe)
        # rather than dividing a multi-step accumulation delta by a single-step
        # window and silently inflating the rate. prev_valid_utc advances every
        # processed step, so a stale predecessor value would otherwise desync
        # from the window (review on 6c5555a).
        for i, raw in enumerate(decoded_points):
            prev_rain_con_per_point[i] = raw.get("conv_rain_kg_m2")
        prev_valid_utc = valid_utc

        # Fill missing layer base/top from THIS forecast hour's CLC geometry.
        clc_for_hour = clc_layers_by_fhour.get(fhour) if clc_layers_by_fhour else None
        if clc_for_hour:
            for pt_idx, diag in enumerate(diagnostics_per_point):
                if diag is None or pt_idx >= len(clc_for_hour):
                    continue
                clc = clc_for_hour[pt_idx]
                if not clc:
                    continue
                for band in ("low", "mid", "high"):
                    layer = getattr(diag, band)
                    if layer.base_ft is None and f"{band}_base_ft" in clc:
                        layer.base_ft = clc[f"{band}_base_ft"]
                    if layer.top_ft is None and f"{band}_top_ft" in clc:
                        layer.top_ft = clc[f"{band}_top_ft"]

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
# ICON-D2 explicit-convection enrichment (#462)
# ---------------------------------------------------------------------------


def _echo_top_pa_to_ft(pa: float, hourly: HourlyForecast) -> float:
    """Convert an echo-top pressure (Pa) to feet using the hour's own column.

    Height is linear in log-pressure between the two column levels bracketing
    the target. A deep D2 echo can sit ABOVE the aviation pressure slice, so
    the nearest two levels carry a short extrapolation rather than silently
    switching the datum to ISA pressure altitude mid-field (PR #465's
    approach, adopted here): a metre-datum column and an ISA column disagree
    by hundreds of feet, and flipping between them across route points would
    make echo tops incomparable. ISA remains the fallback only when the hour
    has fewer than two usable levels — the ICON model-level replacement
    derives its heights later, in sounding analysis.

    The echo top is a depth/character detail, never a clearance input, so the
    extrapolation's error at the extremes is acceptable and documented.
    """
    import math as _math

    from weatherbrief.models.analysis import pressure_pa_to_altitude_ft

    hpa = pa / 100.0
    levels = sorted(
        (
            lv for lv in hourly.pressure_levels
            if lv.geopotential_height_m is not None and lv.pressure_hpa > 0
        ),
        key=lambda lv: lv.pressure_hpa,  # ascending hPa (high→low altitude)
    )
    if hpa > 0 and len(levels) >= 2:
        if hpa <= levels[0].pressure_hpa:
            pair = (levels[0], levels[1])          # above the slice: extrapolate up
        elif hpa >= levels[-1].pressure_hpa:
            pair = (levels[-2], levels[-1])        # below the slice: extrapolate down
        else:
            pair = next(
                (
                    (above, below)
                    for above, below in zip(levels, levels[1:])
                    if above.pressure_hpa <= hpa <= below.pressure_hpa
                ),
                (levels[0], levels[1]),
            )
        above, below = pair
        span = _math.log(below.pressure_hpa) - _math.log(above.pressure_hpa)
        # Duplicate pressures would divide by zero. Today's level constants are
        # strictly distinct, but this runs inside the per-hour attach loop with
        # no try/except above it, so a degenerate column must not abort the
        # whole enrichment pass (PR #463 review round 2).
        if span != 0.0:
            frac = (_math.log(hpa) - _math.log(above.pressure_hpa)) / span
            z_m = above.geopotential_height_m + frac * (
                below.geopotential_height_m - above.geopotential_height_m
            )
            return round(z_m * _M_TO_FT)
    return round(pressure_pa_to_altitude_ft(pa))


def _enrich_icon_d2_explicit_convective(
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
    variant,
) -> None:
    """Attach :class:`NWPExplicitConvectiveDiagnostics` to ICON-D2 hours (#462).

    Walks forecast hours in order with one leading step prepended (the
    predecessor file provides the three quarter-hour echo-top windows of the
    hour before the window's first hour), decoding each hour's per-variable
    explicit-conv blobs via the corridor-extremum decoder, then:

    - constructs the HOURLY echo top for valid hour H as the minimum pressure
      across the four 15-min windows ending at H−45, H−30, H−15 and H minutes
      — three from file f(H−1), one from f(H). A missing/invalid quarter
      degrades ``echo_top_complete`` and yields ``echo_top_18dbz_ft=None``
      (never a partial min presented as the hourly value);
    - converts the echo-top pressure to feet against the hour's own column.

    Interval-max semantics: every value attached at hour H describes the
    ``(H−1, H]`` window. Per-hour channel failures produce ``None`` +
    completeness flags — deliberately NO time-axis or spatial fill for these
    fields: a 1-hour interval maximum from a failed hour has no covering
    interval to hold over (contrast the ECMWF gust, whose window spans the
    gap), and corridor extrema are already spatial reductions, so generic
    per-point interpolation must not touch them.
    """
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        fetch_icon_eu_single_level,
        icon_eu_previous_step,
        icon_explicit_conv_cache_key,
    )
    from weatherbrief.models import NWPExplicitConvectiveDiagnostics

    steps = sorted(set(forecast_hours))
    if not steps:
        return
    lead = icon_eu_previous_step(steps[0], variant)
    if lead is not None and lead not in steps:
        steps.insert(0, lead)

    n_points = len(point_lats)
    # Per-point quarter-window state from the previous step. ``{}`` = no usable
    # predecessor (missing-data-safe: the first hour then reads echo_top
    # incomplete rather than a fabricated value).
    prev_quarters: list[dict[int, tuple[float | None, bool]]] = [
        {} for _ in range(n_points)
    ]
    prev_fhour: int | None = None

    total_enriched = 0
    for fhour in steps:
        var_paths: dict[str, str] = {}
        for var in variant.explicit_conv_variables:
            ck = cache_key(fhour, icon_explicit_conv_cache_key(var, variant))
            if not is_cached(run_dir, ck):
                # Prefetch normally covers this; fetch on demand as a fallback
                # (mirrors the cloud-diag loop).
                try:
                    fetched = fetch_icon_eu_single_level(
                        init_date, init_hour, [fhour], variables=[var],
                        session=session, variant=variant,
                    )
                    grib_bytes = fetched.get(fhour)
                    if grib_bytes:
                        put_cached(run_dir, ck, grib_bytes)
                except Exception:
                    logger.warning(
                        "Failed to fetch ICON-D2 explicit-conv %s f%03d",
                        var, fhour, exc_info=True,
                    )
            if is_cached(run_dir, ck):
                var_paths[var] = str(run_dir / ck)

        if var_paths:
            with _grib_time("icon_d2_explicit_conv_decode"):
                decoded_points = _dispatch_decode(
                    "decode_icon_d2_explicit_conv",
                    var_paths, point_lats, point_lons,
                )
        else:
            decoded_points = None
        if not decoded_points:
            decoded_points = [{"echotop_quarters": {}} for _ in range(n_points)]

        valid_utc = _forecast_hour_to_utc(init_date, init_hour, fhour)
        contiguous = prev_fhour is not None and fhour - prev_fhour == 1

        # The four quarter windows covering (H−1, H], as end-minutes since init.
        hour_end_m = fhour * 60
        prev_file_quarter_minutes = (hour_end_m - 45, hour_end_m - 30, hour_end_m - 15)

        payloads: list[NWPExplicitConvectiveDiagnostics | None] = []
        echo_pa_per_point: list[float | None] = []
        for i in range(n_points):
            point = decoded_points[i] if i < len(decoded_points) else {}
            dbz_val, dbz_valid = point.get("dbz_ctmax", (None, False))
            lpi_val, lpi_valid = point.get("lpi_max", (None, False))
            w_val, w_valid = point.get("w_ctmax", (None, False))
            uh_val, uh_valid = point.get("uh_max", (None, False))

            # Hourly echo top: min over exactly the four quarters ending in
            # (H−1, H]. Quarters must all be present AND corridor-valid;
            # value None with valid=True is a real "no echo" quarter.
            quarters: list[tuple[float | None, bool]] = []
            if contiguous:
                quarters.extend(
                    prev_quarters[i].get(m, (None, False))
                    for m in prev_file_quarter_minutes
                )
            else:
                quarters.extend((None, False) for _ in prev_file_quarter_minutes)
            quarters.append(
                point.get("echotop_quarters", {}).get(hour_end_m, (None, False))
            )
            echo_complete = all(valid for _v, valid in quarters)
            echo_pa: float | None = None
            if echo_complete:
                echoes = [v for v, _valid in quarters if v is not None]
                if echoes:
                    echo_pa = min(echoes)

            lpi = lpi_val if lpi_valid else None
            w = w_val if w_valid else None
            uh = uh_val if uh_valid else None
            # ALWAYS attach a payload — even when every channel failed (all
            # None, all completeness flags False). Payload presence is the
            # explicit-convection mode signal: with no payload the analysis
            # layer would silently fall back to the parameterized
            # assess_convective_nwp path, which on D2 (no scheme fields
            # fetched, #461) structurally reads as a QUIET scheme — turning a
            # one-hour fetch hiccup into a fake-quiet convective read instead
            # of "explicit assessment unavailable" (#463 review, Critical 1).
            payloads.append(NWPExplicitConvectiveDiagnostics(
                source="icon_d2",
                reflectivity_hour_max_dbz=dbz_val if dbz_valid else None,
                lightning_potential_hour_max_jkg=lpi,
                updraft_hour_max_ms=w,
                updraft_helicity_2_8km_hour_max_m2s2=uh,
                detection_complete=dbz_valid,
                strength_complete=(lpi is not None and w is not None),
                echo_top_complete=echo_complete,
            ))
            echo_pa_per_point.append(echo_pa)

        # Carry state forward BEFORE the attach filter so the lead step (and
        # any out-of-window step) still seeds the quarter windows.
        for i in range(n_points):
            point = decoded_points[i] if i < len(decoded_points) else {}
            prev_quarters[i] = dict(point.get("echotop_quarters", {}))
        prev_fhour = fhour

        if fhour not in forecast_hours:
            continue  # lead step: state seeding only, never attached

        def _attach(hourly: HourlyForecast, idx: int) -> bool:
            payload = payloads[idx]
            if payload is None or hourly.explicit_convective_diagnostics is not None:
                return False
            echo_pa = echo_pa_per_point[idx]
            if echo_pa is not None:
                payload = payload.model_copy(update={
                    "echo_top_18dbz_ft": _echo_top_pa_to_ft(echo_pa, hourly),
                })
            hourly.explicit_convective_diagnostics = payload
            return True

        for cs in icon_sections:
            for point_idx, wf in enumerate(cs.point_forecasts):
                if point_idx >= len(payloads):
                    break
                for hourly in wf.hourly:
                    if _matches_valid_time(hourly.time, valid_utc):
                        if _attach(hourly, point_idx):
                            total_enriched += 1

        wp_idx_lookup: dict[str, int] = {
            rp.waypoint_icao: idx
            for idx, rp in enumerate(route_points)
            if rp.waypoint_icao
        }
        for wf in all_forecasts:
            if wf.model.value != "icon":
                continue
            idx = wp_idx_lookup.get(wf.waypoint.icao)
            if idx is None:
                continue
            for hourly in wf.hourly:
                if _matches_valid_time(hourly.time, valid_utc):
                    _attach(hourly, idx)

        del decoded_points
        _grib_gc()

    if total_enriched:
        logger.info(
            "ICON-D2 explicit-convection enrichment: %d hourly entries",
            total_enriched,
        )
    else:
        logger.info("ICON-D2 explicit-convection enrichment produced no entries")


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
