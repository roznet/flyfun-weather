"""Tests for ``weatherbrief.process_memory_sampler`` (issue #137)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from weatherbrief.process_memory_sampler import (
    MemoryPeaks,
    MemorySampler,
    current_cgroup_memory_mb,
)


def test_sampler_captures_peak_when_values_grow():
    """Peak tracking: feed monotonically increasing readings and verify the
    sampler returns the maximum, not the latest or first."""
    rss_sequence = iter([100.0, 200.0, 350.0, 280.0, 310.0])
    cgroup_sequence = iter([150.0, 250.0, 400.0, 380.0, 360.0])

    def fake_rss():
        return next(rss_sequence)

    def fake_cgroup():
        return next(cgroup_sequence)

    sampler = MemorySampler(interval_seconds=0.05)
    with (
        patch("weatherbrief.process_memory_sampler.current_rss_mb", side_effect=fake_rss),
        patch("weatherbrief.process_memory_sampler.current_cgroup_memory_mb", side_effect=fake_cgroup),
    ):
        sampler.start()
        # Wait for the iterators to drain (5 ticks × 0.05s + buffer).
        time.sleep(0.4)
        peaks = sampler.stop()

    assert peaks.peak_rss_mb == 350
    assert peaks.peak_cgroup_mb == 400
    assert peaks.samples >= 1


def test_sampler_returns_none_when_readings_unavailable():
    """On macOS dev or non-cgroup envs both helpers return ``None``; the
    sampler must propagate that as ``None`` peaks (not 0) so the caller
    can persist NULL rather than misleading zeros."""
    sampler = MemorySampler(interval_seconds=0.05)
    with (
        patch("weatherbrief.process_memory_sampler.current_rss_mb", return_value=None),
        patch("weatherbrief.process_memory_sampler.current_cgroup_memory_mb", return_value=None),
    ):
        sampler.start()
        time.sleep(0.15)
        peaks = sampler.stop()

    assert peaks.peak_rss_mb is None
    assert peaks.peak_cgroup_mb is None


def test_sampler_rss_only_when_cgroup_unavailable():
    """Mixed availability — common case on macOS dev (rss via ps, no cgroup)."""
    sampler = MemorySampler(interval_seconds=0.05)
    with (
        patch("weatherbrief.process_memory_sampler.current_rss_mb", return_value=512.0),
        patch("weatherbrief.process_memory_sampler.current_cgroup_memory_mb", return_value=None),
    ):
        sampler.start()
        time.sleep(0.1)
        peaks = sampler.stop()

    assert peaks.peak_rss_mb == 512
    assert peaks.peak_cgroup_mb is None


def test_sampler_double_start_raises():
    sampler = MemorySampler(interval_seconds=0.05)
    with patch("weatherbrief.process_memory_sampler.current_rss_mb", return_value=100.0):
        sampler.start()
        try:
            with pytest.raises(RuntimeError, match="already started"):
                sampler.start()
        finally:
            sampler.stop()


def test_sampler_stop_without_start_returns_empty():
    """Defensive: a caller that errors before ``start()`` should still be
    able to call ``stop()`` cleanly (e.g. in a finally block)."""
    sampler = MemorySampler()
    peaks = sampler.stop()
    assert peaks == MemoryPeaks(peak_rss_mb=None, peak_cgroup_mb=None, samples=0)


def test_sampler_thread_is_daemon():
    """A non-daemon thread would block interpreter shutdown if the host
    code crashed before stop() ran."""
    sampler = MemorySampler(interval_seconds=0.1)
    with patch("weatherbrief.process_memory_sampler.current_rss_mb", return_value=100.0):
        sampler.start()
        try:
            assert sampler._thread is not None
            assert sampler._thread.daemon is True
        finally:
            sampler.stop()


def test_current_cgroup_memory_mb_returns_none_off_linux(tmp_path, monkeypatch):
    """Outside a container the cgroup files don't exist; helper must return
    None rather than raising."""
    # Force open() to fail by pointing the candidate paths at non-existent files.
    monkeypatch.setattr(
        "weatherbrief.process_memory_sampler._CGROUP_V2_PATHS",
        (str(tmp_path / "missing_v2"),),
    )
    monkeypatch.setattr(
        "weatherbrief.process_memory_sampler._CGROUP_V1_PATHS",
        (str(tmp_path / "missing_v1"),),
    )
    assert current_cgroup_memory_mb() is None


def test_current_cgroup_memory_mb_reads_v2(tmp_path, monkeypatch):
    """When the v2 file exists, parse bytes → MB."""
    v2 = tmp_path / "memory.current"
    v2.write_text("2147483648\n")  # 2 GiB
    monkeypatch.setattr(
        "weatherbrief.process_memory_sampler._CGROUP_V2_PATHS", (str(v2),),
    )
    monkeypatch.setattr(
        "weatherbrief.process_memory_sampler._CGROUP_V1_PATHS", (),
    )
    assert current_cgroup_memory_mb() == 2048.0
