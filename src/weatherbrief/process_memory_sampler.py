"""Periodic memory sampler for long-running pipeline cycles (issue #137).

Daemon thread that wakes every ``interval_seconds`` and records the max of:

- **Parent process RSS** (``current_rss_mb()``) — what the in-process
  bookkeeping has been logging at cycle boundaries.
- **Cgroup current** (``/sys/fs/cgroup/memory.current``) — total memory
  attributed to the container by the kernel, covering parent uvicorn,
  GRIB decode worker pool processes, healthcheck subprocesses, etc.
  This is the value the OOM-killer compares against the cgroup limit,
  so it's the right number to track for regression detection.

Cgroup v1 fallback to ``/sys/fs/cgroup/memory/memory.usage_in_bytes`` is
also handled. Outside a container (dev/macOS), cgroup reads return
``None`` and only the parent RSS is tracked.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from weatherbrief.process_rss import current_rss_mb

logger = logging.getLogger(__name__)


_CGROUP_V2_PATHS = (
    "/sys/fs/cgroup/memory.current",
)
_CGROUP_V1_PATHS = (
    "/sys/fs/cgroup/memory/memory.usage_in_bytes",
)


def current_cgroup_memory_mb() -> float | None:
    """Read the container's current cgroup memory usage in MB.

    Returns ``None`` outside a container or on platforms without cgroup
    memory accounting (macOS dev, exotic kernels). ``memory.current``
    is cgroup v2; ``memory.usage_in_bytes`` is the v1 equivalent.
    """
    for path in _CGROUP_V2_PATHS + _CGROUP_V1_PATHS:
        try:
            with open(path) as f:
                return int(f.read().strip()) / (1024 * 1024)
        except OSError:
            continue
    return None


@dataclass
class MemoryPeaks:
    """Snapshot of peak memory observed during a sampling window."""

    peak_rss_mb: int | None
    peak_cgroup_mb: int | None
    samples: int


class MemorySampler:
    """Background sampler — call ``start()`` then ``stop()`` to collect peaks.

    Intended scope is a single pipeline cycle. The sampler runs in a daemon
    thread so an unhandled exception in the host code can't leave it
    dangling — interpreter shutdown will reap it.

    Sampling is best-effort: if a tick fails (e.g. cgroup file disappeared),
    we log once at DEBUG and continue. Persistent failure leaves the peak
    fields ``None``, which the caller treats as "no data" rather than 0.
    """

    def __init__(self, interval_seconds: float = 5.0) -> None:
        self._interval = max(0.1, float(interval_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._peak_rss_mb: float | None = None
        self._peak_cgroup_mb: float | None = None
        self._samples = 0
        self._read_failures_logged = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("MemorySampler already started")
        self._stop_event.clear()
        # Sample once synchronously so the caller has at least one data
        # point even if start()/stop() bracket a sub-interval window.
        self._tick()
        self._thread = threading.Thread(
            target=self._run, name="MemorySampler", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> MemoryPeaks:
        if self._thread is None:
            return MemoryPeaks(peak_rss_mb=None, peak_cgroup_mb=None, samples=0)
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        # Final synchronous sample so a fast-finishing cycle doesn't miss
        # a late peak between the last interval tick and stop().
        self._tick()
        return MemoryPeaks(
            peak_rss_mb=int(self._peak_rss_mb) if self._peak_rss_mb is not None else None,
            peak_cgroup_mb=int(self._peak_cgroup_mb) if self._peak_cgroup_mb is not None else None,
            samples=self._samples,
        )

    # ------------------------------------------------------------------ thread

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval):
            self._tick()

    def _tick(self) -> None:
        try:
            rss = current_rss_mb()
            cgroup = current_cgroup_memory_mb()
        except Exception:
            if not self._read_failures_logged:
                logger.debug("MemorySampler tick failed", exc_info=True)
                self._read_failures_logged = True
            return
        if rss is not None:
            if self._peak_rss_mb is None or rss > self._peak_rss_mb:
                self._peak_rss_mb = rss
        if cgroup is not None:
            if self._peak_cgroup_mb is None or cgroup > self._peak_cgroup_mb:
                self._peak_cgroup_mb = cgroup
        self._samples += 1
