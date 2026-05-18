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


def _patch_status(blob: str | None):
    """Patch ``open()`` so /proc/self/status reads return ``blob``."""
    from io import StringIO
    real_open = open

    def fake_open(path, *a, **kw):
        if path == "/proc/self/status":
            if blob is None:
                raise OSError("not linux")
            return StringIO(blob)
        # Cgroup paths in this test default to "absent" (OSError) so the
        # log line stays compact — we test cgroup parsing separately.
        if str(path).startswith("/sys/fs/cgroup/"):
            raise OSError("no cgroup")
        return real_open(path, *a, **kw)

    return patch("builtins.open", side_effect=fake_open)


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
