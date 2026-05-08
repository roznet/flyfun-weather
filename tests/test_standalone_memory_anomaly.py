"""Tests for the per-cycle memory anomaly check (issue #137).

Covers:
- Relative threshold (peak vs median of last 10 same-source cycles)
- Absolute threshold (peak vs % of cgroup limit) — using cgroup memory,
  not parent RSS, so workers count
- Insufficient baseline (skip relative check, fall back to absolute only)
- Current cycle's row is excluded from baseline lookup
- Cgroup limit reader edge cases
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from weatherbrief.db.models import Base, VerificationCycleRow
from weatherbrief.process_memory_sampler import MemoryPeaks
from weatherbrief.tasks.standalone_verification import (
    _check_memory_anomaly,
    _read_cgroup_limit_mb,
)

# Default ``cycle_started_at`` for the current-cycle marker. Seeded prior
# rows use earlier timestamps so they fall under the "started_at <
# cycle_started_at" baseline filter.
_NOW = datetime(2026, 5, 8, 19, 0, tzinfo=timezone.utc)


@pytest.fixture
def session():
    """In-memory SQLite session with the verification_cycles table."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _seed_cycles(
    session,
    source: str,
    peak_rss_values: list[int],
    *,
    base_time: datetime | None = None,
) -> None:
    """Insert N cycles with the given peak_rss_mb values, oldest first.

    By default the seeded rows are dated well before ``_NOW`` so they
    qualify as prior history relative to the simulated current cycle.
    """
    if base_time is None:
        base_time = _NOW - timedelta(days=2)
    for i, peak in enumerate(peak_rss_values):
        session.add(VerificationCycleRow(
            started_at=base_time + timedelta(hours=i),
            duration_ms=1000,
            source=source,
            peak_rss_mb=peak,
        ))
    session.commit()


def _call(session, source, peaks, *, cycle_started_at=_NOW):
    """Wrapper so each test reads as 'simulate a cycle ending at NOW'."""
    _check_memory_anomaly(session, source, peaks, cycle_started_at=cycle_started_at)


def test_no_anomaly_when_peak_under_thresholds(session, caplog):
    """Peak well below baseline + cgroup → silent."""
    _seed_cycles(session, "standalone_forecast", [1500, 1600, 1700, 1550, 1650])
    peaks = MemoryPeaks(peak_rss_mb=1700, peak_cgroup_mb=1900, samples=10)

    with patch(
        "weatherbrief.tasks.standalone_verification._read_cgroup_limit_mb",
        return_value=4096,
    ):
        with caplog.at_level(logging.WARNING):
            _call(session, "standalone_forecast", peaks)

    assert "memory anomaly" not in caplog.text.lower()


def test_relative_threshold_fires_on_creep(session, caplog):
    """Peak 1.5× baseline median → anomaly. Models the 5/7→5/8 drift pattern."""
    # Baseline median = 1600. 1.4× threshold = 2240.
    _seed_cycles(session, "standalone_forecast", [1500, 1600, 1700, 1550, 1650])
    peaks = MemoryPeaks(peak_rss_mb=2400, peak_cgroup_mb=2600, samples=10)

    with patch(
        "weatherbrief.tasks.standalone_verification._read_cgroup_limit_mb",
        return_value=4096,
    ):
        with caplog.at_level(logging.WARNING):
            _call(session, "standalone_forecast", peaks)

    assert "memory anomaly" in caplog.text.lower()
    assert "peak_rss=2400MB" in caplog.text


def test_absolute_threshold_uses_cgroup_not_rss(session, caplog):
    """The absolute check must fire when *cgroup* memory approaches the
    limit, even if parent RSS stays comfortably low.

    The OOM-killer compares the cgroup total against the limit, not the
    parent's RSS. A regression that grows the GRIB worker pool's memory
    (separate processes, parent RSS unchanged) must still fire this
    warning — otherwise we miss the very class of failure the absolute
    check is meant to catch.

    Setup: parent RSS = 1500 MB (well under any threshold), cgroup peak
    = 3500 MB (above 80% × 4096 = 3276), baseline cycles look normal.
    """
    _seed_cycles(session, "standalone_forecast", [1500, 1550, 1600, 1500, 1550])
    peaks = MemoryPeaks(peak_rss_mb=1500, peak_cgroup_mb=3500, samples=10)

    with patch(
        "weatherbrief.tasks.standalone_verification._read_cgroup_limit_mb",
        return_value=4096,
    ):
        with caplog.at_level(logging.WARNING):
            _call(session, "standalone_forecast", peaks)

    assert "memory anomaly" in caplog.text.lower(), (
        "absolute check should fire on high cgroup memory even with low parent RSS"
    )
    # Confirm the WARN line records the cgroup value (the metric that triggered).
    assert "peak_cgroup=3500" in caplog.text
    assert "absolute_threshold=3276" in caplog.text


def test_absolute_threshold_falls_back_to_rss_when_no_cgroup(session, caplog):
    """When cgroup readings are unavailable (e.g. dev outside a container),
    fall back to comparing parent RSS against the limit. Avoids losing the
    absolute check entirely on platforms without cgroup support."""
    _seed_cycles(session, "standalone_forecast", [1500, 1550, 1600, 1500, 1550])
    # Cgroup unavailable; RSS itself is high enough to trip absolute (3500 > 3276).
    peaks = MemoryPeaks(peak_rss_mb=3500, peak_cgroup_mb=None, samples=10)

    with patch(
        "weatherbrief.tasks.standalone_verification._read_cgroup_limit_mb",
        return_value=4096,
    ):
        with caplog.at_level(logging.WARNING):
            _call(session, "standalone_forecast", peaks)

    assert "memory anomaly" in caplog.text.lower()


def test_relative_check_skipped_when_baseline_too_small(session, caplog):
    """Fewer than 3 *prior* populated rows → relative check disabled. Only
    the absolute trigger can fire. Prevents a single high outlier from
    becoming its own baseline and suppressing future warnings."""
    _seed_cycles(session, "standalone_forecast", [2000, 2100])  # only 2 prior rows
    peaks = MemoryPeaks(peak_rss_mb=2900, peak_cgroup_mb=2950, samples=10)

    # cgroup 4096, 80% = 3276. peak_cgroup 2950 < 3276 → absolute doesn't fire.
    # Relative would have fired (2900 > 1.4 × ~2050 = 2870), but is skipped.
    with patch(
        "weatherbrief.tasks.standalone_verification._read_cgroup_limit_mb",
        return_value=4096,
    ):
        with caplog.at_level(logging.WARNING):
            _call(session, "standalone_forecast", peaks)

    assert "memory anomaly" not in caplog.text.lower()


def test_current_cycle_row_excluded_from_baseline(session, caplog):
    """If the current cycle's row was already inserted (anomaly check runs
    after commit), it must not contribute to its own baseline.

    Without exclusion, a self-referential drift dampens the warning: each
    elevated cycle pulls the median upward, raising the threshold for the
    next cycle, masking gradual creep — exactly what the relative check
    is meant to catch.
    """
    # Two prior rows at 1500-ish, plus a "current cycle" row already in DB
    # at 2400 (simulating that the cycle just committed before checking).
    _seed_cycles(session, "standalone_forecast", [1500, 1600])  # pre-history
    session.add(VerificationCycleRow(
        started_at=_NOW,  # same instant as the simulated current cycle
        duration_ms=1000,
        source="standalone_forecast",
        peak_rss_mb=2400,  # the elevated current value
    ))
    session.commit()

    peaks = MemoryPeaks(peak_rss_mb=2400, peak_cgroup_mb=2400, samples=10)

    with patch(
        "weatherbrief.tasks.standalone_verification._read_cgroup_limit_mb",
        return_value=4096,
    ):
        with caplog.at_level(logging.WARNING):
            _call(session, "standalone_forecast", peaks)

    # With exclusion: only 2 prior rows visible (median ~1550), relative
    # check skipped (need ≥ 3 prior). Absolute check: 2400 < 3276 → no fire.
    # Without exclusion the bug suppresses the check below; if the median
    # were biased upward by the self-referential 2400 the threshold would
    # be 1.4 × ~1600 = 2240 and would fire, but the test asserts silence
    # because we only have 2 *prior* rows — relative is correctly skipped.
    assert "memory anomaly" not in caplog.text.lower()

    # Now add a third prior row to enable relative check, and re-run.
    # The current row should still be excluded; baseline should reflect
    # only prior history.
    session.add(VerificationCycleRow(
        started_at=_NOW - timedelta(hours=12),
        duration_ms=1000,
        source="standalone_forecast",
        peak_rss_mb=1550,
    ))
    session.commit()
    caplog.clear()

    with patch(
        "weatherbrief.tasks.standalone_verification._read_cgroup_limit_mb",
        return_value=4096,
    ):
        with caplog.at_level(logging.WARNING):
            _call(session, "standalone_forecast", peaks)

    # With exclusion: prior baseline = median(1500, 1550, 1600) = 1550;
    # 1.4 × 1550 = 2170; peak 2400 > 2170 → fires.
    # Without exclusion the bug includes 2400 in the median, raising it
    # to 1575 and threshold to 2205 — still fires here, but the test
    # demonstrates exclusion works correctly.
    assert "memory anomaly" in caplog.text.lower()
    # Baseline reported should be 1550, not skewed by the elevated current row.
    assert "baseline=1550" in caplog.text


def test_per_source_baselining(session, caplog):
    """``standalone_forecast`` baseline must not be polluted by lighter
    ``standalone_light`` cycles."""
    _seed_cycles(session, "standalone_light", [500, 600, 700, 550, 650])
    _seed_cycles(session, "standalone_forecast", [1600, 1700, 1800, 1650, 1750])

    peaks = MemoryPeaks(peak_rss_mb=1800, peak_cgroup_mb=2000, samples=10)
    with patch(
        "weatherbrief.tasks.standalone_verification._read_cgroup_limit_mb",
        return_value=4096,
    ):
        with caplog.at_level(logging.WARNING):
            _call(session, "standalone_forecast", peaks)

    # 1800 against forecast median 1700 → 1.06×, well under 1.4× → no warn.
    # If the light cycles were mixed in, median would drop dramatically
    # and trigger a false positive.
    assert "memory anomaly" not in caplog.text.lower()


def test_skips_when_both_peaks_unavailable(session, caplog):
    """No memory readings → no comparison, no warning."""
    _seed_cycles(session, "standalone_forecast", [1500, 1600, 1700])
    peaks = MemoryPeaks(peak_rss_mb=None, peak_cgroup_mb=None, samples=0)

    with caplog.at_level(logging.WARNING):
        _call(session, "standalone_forecast", peaks)

    assert "memory anomaly" not in caplog.text.lower()


def test_no_cgroup_limit_disables_absolute_check(session, caplog):
    """Outside a container, cgroup limit reads as None → absolute check
    is disabled entirely. Only the relative check remains."""
    _seed_cycles(session, "standalone_forecast", [1500, 1600, 1700])
    # Peak above the would-be absolute threshold but cgroup limit unknown.
    peaks = MemoryPeaks(peak_rss_mb=2500, peak_cgroup_mb=None, samples=10)

    with patch(
        "weatherbrief.tasks.standalone_verification._read_cgroup_limit_mb",
        return_value=None,
    ):
        with caplog.at_level(logging.WARNING):
            _call(session, "standalone_forecast", peaks)

    # Relative threshold = 1600 × 1.4 = 2240; peak 2500 > 2240 → fires.
    assert "memory anomaly" in caplog.text.lower()
    assert "absolute_threshold=n/a" in caplog.text


def test_read_cgroup_limit_handles_unset(tmp_path, monkeypatch):
    """cgroup v2 prints the literal string ``max`` when no limit is set;
    must return ``None`` rather than raise on int() parse."""
    v2 = tmp_path / "memory.max"
    v2.write_text("max\n")

    real_open = open

    def fake_open(path, *args, **kwargs):
        if str(path) == "/sys/fs/cgroup/memory.max":
            return real_open(str(v2))
        raise OSError(f"no such file: {path}")

    monkeypatch.setattr("builtins.open", fake_open)
    assert _read_cgroup_limit_mb() is None
