"""Process resident-set-size helper, shared by pipeline + GRIB instrumentation."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading


def current_rss_mb() -> float | None:
    """Return current process RSS in MB, or None if unavailable.

    Linux: reads /proc/self/status (cheap, accurate). macOS: shells out to ps.
    """
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    if sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["ps", "-o", "rss=", "-p", str(os.getpid())], timeout=1,
            )
            return int(out.strip()) / 1024
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Task-boundary memory summary
# ---------------------------------------------------------------------------
#
# ``log_memory`` is called at the end of long-running tasks (briefing refresh,
# standalone forecast cycle, Hewson precompute) to capture parent-process RSS
# and its high-water mark. Idle hours don't produce output — only real work
# does — so the journald trail stays sparse but tracks every meaningful peak.
#
# WARN escalation: VmHWM only grows. The first call sets a baseline silently
# (we want to detect *growth*, not absolute starting size). Each time HWM
# crosses ``warn_step_mib`` above the last-warned mark, emit a WARN and
# update the mark. A slow leak surfaces as a WARN the first time it crosses
# a new 500 MiB step, instead of having to grep logs to spot drift.

_WARN_STATE_LOCK = threading.Lock()
_LAST_WARNED_HWM_MIB = 0  # baseline; 0 until first call seeds it


def _read_proc_status_kib() -> dict[str, int] | None:
    """Return {VmRSS, VmHWM, VmSwap} in KiB from /proc/self/status, or None."""
    try:
        with open("/proc/self/status") as f:
            content = f.read()
    except OSError:
        return None
    fields: dict[str, int] = {}
    wanted = ("VmRSS:", "VmHWM:", "VmSwap:")
    for line in content.splitlines():
        if line.startswith(wanted):
            parts = line.split()
            try:
                fields[parts[0].rstrip(":")] = int(parts[1])
            except (IndexError, ValueError):
                continue
    return fields if fields else None


#: ``memory.stat`` keys worth logging, in the order they appear in the line.
#: ``anon`` first because it is the one that decides whether the container
#: lives; ``file`` next so the two can be compared at a glance.
_CGROUP_STAT_KEYS = ("anon", "file", "slab")


def _read_cgroup_memory_stat_mib() -> dict[str, int]:
    """Return {anon, file, slab} in MiB from cgroup v2 ``memory.stat``.

    ``memory.current`` is one total covering two things with very different
    consequences. ``anon`` has to fit: the kernel can only swap it or invoke
    the OOM killer. ``file`` is page cache it can drop on demand. A container
    sitting near its limit is in real trouble if that is anon and essentially
    fine if it is cache — and the total alone cannot tell you which, which is
    how a page-cache excursion and a genuine anon spike came to look identical
    in these logs (#506).

    Empty dict outside a container or on cgroup v1.
    """
    out: dict[str, int] = {}
    try:
        with open("/sys/fs/cgroup/memory.stat") as f:
            content = f.read()
    except OSError:
        return out
    for line in content.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in _CGROUP_STAT_KEYS:
            try:
                out[parts[0]] = int(parts[1]) // (1024 * 1024)
            except ValueError:
                continue
    return out


def _read_cgroup_memory_mib() -> dict[str, int]:
    """Return {current, peak, max, swap} in MiB from cgroup v2 files.

    Unset/unreadable keys are skipped. Returns an empty dict outside a
    container or on cgroup v1 (different layout — we don't bother reading it:
    the production droplet is v2 and dev on macOS has no cgroup memory
    accounting at all).

    ``peak`` is ``/sys/fs/cgroup/memory.peak``: a high-water mark since the
    cgroup was created that nothing here resets. It reports the loudest moment
    since container start, NOT anything about the task reading it.
    """
    out: dict[str, int] = {}
    for key, path in (
        ("current", "/sys/fs/cgroup/memory.current"),
        ("peak", "/sys/fs/cgroup/memory.peak"),
        ("max", "/sys/fs/cgroup/memory.max"),
        ("swap", "/sys/fs/cgroup/memory.swap.current"),
    ):
        try:
            with open(path) as f:
                raw = f.read().strip()
        except OSError:
            continue
        if raw == "max":  # cgroup v2 sentinel for "no limit"
            continue
        try:
            out[key] = int(raw) // (1024 * 1024)
        except ValueError:
            continue
    return out


def log_memory(
    label: str,
    logger: logging.Logger,
    *,
    warn_step_mib: int = 500,
    task_peak_rss_mb: int | None = None,
    task_peak_cgroup_mb: int | None = None,
) -> None:
    """Log memory state at a task boundary.

    One INFO line, whose fields answer deliberately different questions:

    - ``rss`` / ``hwm`` / ``swap`` — the PARENT process only. GRIB decode runs
      in a ``ProcessPoolExecutor``, so the decode workers are absent from
      these numbers: they under-report container pressure by construction.
    - ``cgroup=<current> of <max>`` with an ``anon``/``file``/``slab``/``swap``
      breakdown — the whole container, split into the part that has to fit and
      the part the kernel can reclaim.
    - ``this task peaked at`` — a windowed maximum the caller measured over
      the task. When present this is the ONLY field in the line that describes
      this task rather than a point-in-time or since-boot reading. Callers get
      it from :class:`weatherbrief.process_memory_sampler.MemorySampler`.
    - ``cgroup peak since container start`` — ``memory.peak``, a high-water
      mark nothing resets. Spelled out at length because the old
      ``cgroup=current/peak`` form read as a per-task peak, and a since-boot
      number then got attributed to whichever task happened to log it. That
      misreading is what #506 exists to end.

    Escalates to WARN when VmHWM crosses a ``warn_step_mib`` step above the
    previously-warned mark — first call seeds the baseline silently.

    No-op when /proc/self/status is unavailable (macOS dev, exotic kernels).
    Thread-safe: the WARN escalation state is guarded by a module lock.
    """
    status = _read_proc_status_kib()
    if status is None:
        return

    rss_mib = status.get("VmRSS", 0) // 1024
    hwm_mib = status.get("VmHWM", 0) // 1024
    swap_mib = status.get("VmSwap", 0) // 1024

    suffix: list[str] = []
    cgroup = _read_cgroup_memory_mib()
    if cgroup:
        cur = cgroup.get("current")
        mx = cgroup.get("max")
        cur_str = f"{cur}" if cur is not None else "n/a"
        mx_str = f"{mx}" if mx is not None else "n/a"
        suffix.append(f"; cgroup={cur_str} of {mx_str} MB")

        stat = _read_cgroup_memory_stat_mib()
        breakdown = [f"{key}={stat[key]}" for key in _CGROUP_STAT_KEYS if key in stat]
        if cgroup.get("swap") is not None:
            breakdown.append(f"swap={cgroup['swap']}")
        if breakdown:
            suffix.append(f" ({' '.join(breakdown)})")

    task_peaks = [
        f"{name}={value}"
        for name, value in (
            ("rss", task_peak_rss_mb), ("cgroup", task_peak_cgroup_mb),
        )
        if value is not None
    ]
    if task_peaks:
        suffix.append(f"; this task peaked at {' '.join(task_peaks)} MB")

    if cgroup.get("peak") is not None:
        suffix.append(f"; cgroup peak since container start={cgroup['peak']} MB")

    logger.info(
        "Memory after %s: rss=%d hwm=%d swap=%d MB%s",
        label, rss_mib, hwm_mib, swap_mib, "".join(suffix),
    )

    global _LAST_WARNED_HWM_MIB
    with _WARN_STATE_LOCK:
        if _LAST_WARNED_HWM_MIB == 0:
            _LAST_WARNED_HWM_MIB = hwm_mib  # silent baseline
        elif hwm_mib > _LAST_WARNED_HWM_MIB + warn_step_mib:
            prev = _LAST_WARNED_HWM_MIB
            _LAST_WARNED_HWM_MIB = hwm_mib
            logger.warning(
                "Memory high-water mark crossed +%d MB step: %d → %d MB after %s",
                warn_step_mib, prev, hwm_mib, label,
            )


def _reset_warn_state_for_tests() -> None:
    """Test-only: reset the WARN baseline so each test starts fresh."""
    global _LAST_WARNED_HWM_MIB
    with _WARN_STATE_LOCK:
        _LAST_WARNED_HWM_MIB = 0
