"""Tests for the task-boundary memory log + WARN escalation."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from weatherbrief import process_rss


def _status_blob(rss_kib: int, hwm_kib: int, swap_kib: int = 0) -> str:
    """Mimic the subset of /proc/self/status that log_memory reads."""
    return (
        "Name:\tpytest\n"
        f"VmRSS:\t{rss_kib} kB\n"
        f"VmHWM:\t{hwm_kib} kB\n"
        f"VmSwap:\t{swap_kib} kB\n"
        "Threads:\t1\n"
    )


@pytest.fixture(autouse=True)
def _reset_warn_state():
    """Each test starts with a fresh baseline so they're order-independent."""
    process_rss._reset_warn_state_for_tests()
    yield
    process_rss._reset_warn_state_for_tests()


def _patch_status(blob: str | None, cgroup: dict[str, str] | None = None):
    """Patch ``open()`` for /proc/self/status and the cgroup v2 files.

    Cgroup paths not present in ``cgroup`` raise OSError, i.e. "not in a
    container" — which keeps the log line compact for the tests that only
    care about the /proc fields.
    """
    from io import StringIO
    real_open = open
    cgroup_files = cgroup or {}

    def fake_open(path, *a, **kw):
        path = str(path)
        if path == "/proc/self/status":
            if blob is None:
                raise OSError("not linux")
            return StringIO(blob)
        if path.startswith("/sys/fs/cgroup/"):
            if path in cgroup_files:
                return StringIO(cgroup_files[path])
            raise OSError("no cgroup")
        return real_open(path, *a, **kw)

    return patch("builtins.open", side_effect=fake_open)


_MIB = 1024 * 1024


def _cgroup_blobs(
    *,
    current_mib: int,
    peak_mib: int,
    max_mib: int,
    anon_mib: int,
    file_mib: int,
    slab_mib: int = 0,
    swap_mib: int = 0,
) -> dict[str, str]:
    """Mimic the cgroup v2 memory files log_memory reads."""
    return {
        "/sys/fs/cgroup/memory.current": f"{current_mib * _MIB}\n",
        "/sys/fs/cgroup/memory.peak": f"{peak_mib * _MIB}\n",
        "/sys/fs/cgroup/memory.max": f"{max_mib * _MIB}\n",
        "/sys/fs/cgroup/memory.swap.current": f"{swap_mib * _MIB}\n",
        # Real memory.stat has ~40 keys; include a couple we ignore so the
        # parser is exercised against noise rather than a curated file.
        "/sys/fs/cgroup/memory.stat": (
            f"anon {anon_mib * _MIB}\n"
            f"file {file_mib * _MIB}\n"
            "kernel_stack 16384\n"
            "pagetables 8192\n"
            f"slab {slab_mib * _MIB}\n"
            "sock 0\n"
        ),
    }


class TestLogFormat:
    def test_emits_info_line_with_rss_hwm_swap(self, caplog):
        with _patch_status(_status_blob(rss_kib=1024 * 1024, hwm_kib=2048 * 1024, swap_kib=128 * 1024)):
            with caplog.at_level(logging.INFO, logger="test_logmem"):
                process_rss.log_memory("briefing", logging.getLogger("test_logmem"))
        msgs = [r.getMessage() for r in caplog.records]
        assert any("Memory after briefing" in m for m in msgs)
        assert any("rss=1024" in m for m in msgs)
        assert any("hwm=2048" in m for m in msgs)
        assert any("swap=128" in m for m in msgs)

    def test_no_proc_status_silently_skips(self, caplog):
        with _patch_status(None):
            with caplog.at_level(logging.INFO, logger="test_logmem"):
                process_rss.log_memory("standalone", logging.getLogger("test_logmem"))
        assert caplog.records == []  # nothing logged on non-Linux


class TestCgroupFields:
    """The cgroup half of the line (#506).

    Every number here exists to answer a question the old
    ``cgroup=current/peak of max`` form could not: is the container's memory
    the kind that has to fit, and what did THIS task actually do?
    """

    def _log(self, caplog, **log_kwargs) -> str:
        blobs = _cgroup_blobs(
            current_mib=5654, peak_mib=6146, max_mib=6144,
            anon_mib=3100, file_mib=2400, slab_mib=90, swap_mib=512,
        )
        with _patch_status(
            _status_blob(rss_kib=2126 * 1024, hwm_kib=2202 * 1024, swap_kib=65 * 1024),
            cgroup=blobs,
        ):
            with caplog.at_level(logging.INFO, logger="test_logmem"):
                process_rss.log_memory(
                    "briefing refresh", logging.getLogger("test_logmem"),
                    **log_kwargs,
                )
        return "\n".join(r.getMessage() for r in caplog.records)

    def test_anon_file_split_is_logged(self, caplog):
        """The one distinction the old total could not make."""
        msg = self._log(caplog)
        assert "anon=3100" in msg
        assert "file=2400" in msg
        assert "slab=90" in msg
        assert "swap=512" in msg

    def test_current_is_shown_against_the_limit(self, caplog):
        assert "cgroup=5654 of 6144 MB" in self._log(caplog)

    def test_cumulative_peak_is_labelled_as_such(self, caplog):
        """memory.peak is a since-boot high-water mark, and must read as one.

        The old ``cgroup=<current>/<peak>`` form invited exactly the reading
        that produced #501's table: a number nothing ever resets, attributed
        to whichever task happened to log it.
        """
        msg = self._log(caplog)
        assert "cgroup peak since container start=6146 MB" in msg
        assert "5654/6146" not in msg, "the ambiguous current/peak form is gone"

    def test_task_peak_reported_when_the_caller_measured_one(self, caplog):
        msg = self._log(caplog, task_peak_rss_mb=2200, task_peak_cgroup_mb=5900)
        assert "this task peaked at rss=2200 cgroup=5900 MB" in msg

    def test_task_peak_omitted_when_not_measured(self, caplog):
        assert "this task peaked at" not in self._log(caplog)

    def test_zero_is_reported_not_dropped(self, caplog):
        """A measured 0 is data; only an unmeasured None is absent."""
        blobs = _cgroup_blobs(
            current_mib=600, peak_mib=740, max_mib=6144,
            anon_mib=590, file_mib=62, swap_mib=0,
        )
        with _patch_status(_status_blob(rss_kib=400 * 1024, hwm_kib=410 * 1024), cgroup=blobs):
            with caplog.at_level(logging.INFO, logger="test_logmem"):
                process_rss.log_memory(
                    "standalone", logging.getLogger("test_logmem"),
                    task_peak_cgroup_mb=0,
                )
        msg = "\n".join(r.getMessage() for r in caplog.records)
        assert "swap=0" in msg
        assert "slab=0" in msg
        assert "this task peaked at cgroup=0 MB" in msg


class TestWarnEscalation:
    def test_first_call_silent_baseline(self, caplog):
        with _patch_status(_status_blob(rss_kib=1024 * 1024, hwm_kib=3000 * 1024)):
            with caplog.at_level(logging.WARNING, logger="test_logmem"):
                process_rss.log_memory("first", logging.getLogger("test_logmem"))
        # INFO line is emitted but no WARN — first call only seeds.
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warns == [], "first call must not warn (baseline-only)"

    def test_growth_below_step_does_not_warn(self, caplog):
        # First call sets baseline at 3000 MiB. Second at 3200 MiB is below
        # default warn_step_mib=500 — no warn.
        with _patch_status(_status_blob(rss_kib=1024 * 1024, hwm_kib=3000 * 1024)):
            process_rss.log_memory("first", logging.getLogger("test_logmem"))
        with _patch_status(_status_blob(rss_kib=1024 * 1024, hwm_kib=3200 * 1024)):
            with caplog.at_level(logging.WARNING, logger="test_logmem"):
                process_rss.log_memory("second", logging.getLogger("test_logmem"))
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warns == []

    def test_growth_above_step_warns_once(self, caplog):
        # Baseline 3000 → 3600 (+600) should WARN. Then 3700 (+100 above
        # new baseline 3600) should NOT WARN.
        with _patch_status(_status_blob(rss_kib=1024 * 1024, hwm_kib=3000 * 1024)):
            process_rss.log_memory("first", logging.getLogger("test_logmem"))
        with _patch_status(_status_blob(rss_kib=1024 * 1024, hwm_kib=3600 * 1024)):
            with caplog.at_level(logging.WARNING, logger="test_logmem"):
                process_rss.log_memory("second", logging.getLogger("test_logmem"))
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 1, f"expected one WARN, got {[w.getMessage() for w in warns]}"
        assert "3000" in warns[0].getMessage() and "3600" in warns[0].getMessage()

        caplog.clear()
        with _patch_status(_status_blob(rss_kib=1024 * 1024, hwm_kib=3700 * 1024)):
            with caplog.at_level(logging.WARNING, logger="test_logmem"):
                process_rss.log_memory("third", logging.getLogger("test_logmem"))
        assert [r for r in caplog.records if r.levelno == logging.WARNING] == []

    def test_second_step_warns_after_baseline_advances(self, caplog):
        # 3000 (baseline) → 3600 (WARN, baseline=3600) → 4200 (WARN again,
        # baseline=4200). Catches "the leak keeps going" rather than
        # warning only the first time.
        with _patch_status(_status_blob(rss_kib=1024 * 1024, hwm_kib=3000 * 1024)):
            process_rss.log_memory("first", logging.getLogger("test_logmem"))
        with _patch_status(_status_blob(rss_kib=1024 * 1024, hwm_kib=3600 * 1024)):
            process_rss.log_memory("second", logging.getLogger("test_logmem"))
        caplog.clear()
        with _patch_status(_status_blob(rss_kib=1024 * 1024, hwm_kib=4200 * 1024)):
            with caplog.at_level(logging.WARNING, logger="test_logmem"):
                process_rss.log_memory("third", logging.getLogger("test_logmem"))
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 1, "third call should warn once on the new step"
        assert "3600" in warns[0].getMessage() and "4200" in warns[0].getMessage()

    def test_custom_warn_step(self, caplog):
        # +100 MiB step: 3000 → 3150 should WARN.
        with _patch_status(_status_blob(rss_kib=1024 * 1024, hwm_kib=3000 * 1024)):
            process_rss.log_memory("first", logging.getLogger("test_logmem"), warn_step_mib=100)
        with _patch_status(_status_blob(rss_kib=1024 * 1024, hwm_kib=3150 * 1024)):
            with caplog.at_level(logging.WARNING, logger="test_logmem"):
                process_rss.log_memory("second", logging.getLogger("test_logmem"), warn_step_mib=100)
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 1
