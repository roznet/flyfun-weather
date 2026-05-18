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


def _read_cgroup_memory_mib() -> dict[str, int]:
    """Return {current, peak, max} in MiB from cgroup v2 files (skipping unset).

    Returns an empty dict outside a container or on cgroup v1 (different
    layout — we don't bother reading it: the production droplet is v2 and
    dev on macOS has no cgroup memory accounting at all).
    """
    out: dict[str, int] = {}
    for key, path in (
        ("current", "/sys/fs/cgroup/memory.current"),
        ("peak", "/sys/fs/cgroup/memory.peak"),
        ("max", "/sys/fs/cgroup/memory.max"),
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
) -> None:
    """Log parent-process memory state at a task boundary.

    Emits one INFO line summarising VmRSS / VmHWM / VmSwap and (when running
    in a cgroup-v2 container) the cgroup current/peak/max. Escalates to WARN
    when VmHWM crosses a ``warn_step_mib`` step above the previously-warned
    mark — first call seeds the baseline silently.

    No-op when /proc/self/status is unavailable (macOS dev, exotic kernels).
    Thread-safe: the WARN escalation state is guarded by a module lock.
    """
    status = _read_proc_status_kib()
    if status is None:
        return

    rss_mib = status.get("VmRSS", 0) // 1024
    hwm_mib = status.get("VmHWM", 0) // 1024
    swap_mib = status.get("VmSwap", 0) // 1024

    cgroup = _read_cgroup_memory_mib()
    if cgroup:
        cur = cgroup.get("current")
        pk = cgroup.get("peak")
        mx = cgroup.get("max")
        cur_str = f"{cur}" if cur is not None else "n/a"
        pk_str = f"{pk}" if pk is not None else "n/a"
        mx_str = f"{mx}" if mx is not None else "n/a"
        cgroup_str = f"; cgroup={cur_str}/{pk_str} of {mx_str} MB"
    else:
        cgroup_str = ""

    logger.info(
        "Memory after %s: rss=%d hwm=%d swap=%d MB%s",
        label, rss_mib, hwm_mib, swap_mib, cgroup_str,
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
