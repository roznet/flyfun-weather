"""Tests for the per-cycle memory anomaly check (issue #137).

Covers the three trigger paths in ``_check_memory_anomaly``:
- Relative threshold (peak vs median of last 10 same-source cycles)
- Absolute threshold (peak vs % of cgroup limit)
- Insufficient baseline (skip relative check, fall back to absolute only)

Plus the cgroup-limit reader edge cases.
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


@pytest.fixture
def session():
    """In-memory SQLite session with the verification_cycles table."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _seed_cycles(session, source: str, peak_rss_values: list[int]) -> None:
    """Insert N standalone cycles with the given peak_rss_mb values, oldest first."""
    base_time = datetime(2026, 5, 1, 6, 0, tzinfo=timezone.utc)
    for i, peak in enumerate(peak_rss_values):
        session.add(VerificationCycleRow(
            started_at=base_time + timedelta(hours=i),
            duration_ms=1000,
            source=source,
            peak_rss_mb=peak,
        ))
    session.commit()


def test_no_anomaly_when_peak_under_thresholds(session, caplog):
    """Peak well below baseline + cgroup → silent."""
    _seed_cycles(session, "standalone_forecast", [1500, 1600, 1700, 1550, 1650])
    peaks = MemoryPeaks(peak_rss_mb=1700, peak_cgroup_mb=1900, samples=10)

    with patch(
        "weatherbrief.tasks.standalone_verification._read_cgroup_limit_mb",
        return_value=4096,
    ):
        with caplog.at_level(logging.WARNING):
            _check_memory_anomaly(session, "standalone_forecast", peaks)

    assert "memory anomaly" not in caplog.text.lower()


def test_relative_threshold_fires_on_creep(session, caplog):
    """Peak 1.5× baseline median → anomaly."""
    # Baseline median = 1600. 1.4× threshold = 2240.
    _seed_cycles(session, "standalone_forecast", [1500, 1600, 1700, 1550, 1650])
    peaks = MemoryPeaks(peak_rss_mb=2400, peak_cgroup_mb=2600, samples=10)

    with patch(
        "weatherbrief.tasks.standalone_verification._read_cgroup_limit_mb",
        return_value=4096,
    ):
        with caplog.at_level(logging.WARNING):
            _check_memory_anomaly(session, "standalone_forecast", peaks)

    assert "memory anomaly" in caplog.text.lower()
    assert "peak_rss=2400MB" in caplog.text


def test_absolute_threshold_fires_near_cgroup_limit(session, caplog):
    """Peak above 80% of cgroup, regardless of baseline → anomaly."""
    # Baseline median = 3000 (high but stable), cgroup = 4096, 80% = 3276.
    # Peak 3500 > 3276, BUT peak < 1.4*3000=4200, so only absolute fires.
    _seed_cycles(session, "standalone_forecast", [2900, 3000, 3100, 2950, 3050])
    peaks = MemoryPeaks(peak_rss_mb=3500, peak_cgroup_mb=3700, samples=10)

    with patch(
        "weatherbrief.tasks.standalone_verification._read_cgroup_limit_mb",
        return_value=4096,
    ):
        with caplog.at_level(logging.WARNING):
            _check_memory_anomaly(session, "standalone_forecast", peaks)

    assert "memory anomaly" in caplog.text.lower()
    assert "absolute_threshold=3276" in caplog.text


def test_relative_check_skipped_when_baseline_too_small(session, caplog):
    """Fewer than 3 prior populated rows → relative check disabled. Only
    absolute trigger can fire. This prevents a single high outlier from
    becoming its own baseline and suppressing future warnings."""
    _seed_cycles(session, "standalone_forecast", [2000, 2100])  # only 2 rows
    peaks = MemoryPeaks(peak_rss_mb=2900, peak_cgroup_mb=3000, samples=10)

    # cgroup 4096, 80% = 3276. Peak 2900 < 3276, so absolute doesn't fire.
    # Relative would have fired (2900 > 1.4 × ~2050 = 2870), but is skipped.
    with patch(
        "weatherbrief.tasks.standalone_verification._read_cgroup_limit_mb",
        return_value=4096,
    ):
        with caplog.at_level(logging.WARNING):
            _check_memory_anomaly(session, "standalone_forecast", peaks)

    assert "memory anomaly" not in caplog.text.lower()


def test_per_source_baselining(session, caplog):
    """``standalone_forecast`` baseline must not be polluted by lighter
    ``standalone_light`` cycles. Otherwise a forecast-cycle peak that's
    perfectly normal looks anomalous against the much-lower light average."""
    # Light cycles are small (peak ~600 MB).
    _seed_cycles(session, "standalone_light", [500, 600, 700, 550, 650])
    # Forecast cycles are larger (peak ~1700 MB) — that's their normal.
    _seed_cycles(session, "standalone_forecast", [1600, 1700, 1800, 1650, 1750])

    peaks = MemoryPeaks(peak_rss_mb=1800, peak_cgroup_mb=2000, samples=10)
    with patch(
        "weatherbrief.tasks.standalone_verification._read_cgroup_limit_mb",
        return_value=4096,
    ):
        with caplog.at_level(logging.WARNING):
            _check_memory_anomaly(session, "standalone_forecast", peaks)

    # 1800 against forecast median 1700 → 1.06×, well under 1.4× → no warn.
    # If the light cycles were mixed in, median would drop dramatically and trigger.
    assert "memory anomaly" not in caplog.text.lower()


def test_skips_when_peak_rss_unavailable(session, caplog):
    """No RSS reading → no comparison, no warning. Avoids spurious warnings
    on platforms without RSS sampling support."""
    _seed_cycles(session, "standalone_forecast", [1500, 1600, 1700])
    peaks = MemoryPeaks(peak_rss_mb=None, peak_cgroup_mb=None, samples=0)

    with caplog.at_level(logging.WARNING):
        _check_memory_anomaly(session, "standalone_forecast", peaks)

    assert "memory anomaly" not in caplog.text.lower()


def test_no_cgroup_limit_disables_absolute_check(session, caplog):
    """Outside a container, cgroup limit reads as None → absolute check is
    disabled, only relative check remains."""
    _seed_cycles(session, "standalone_forecast", [1500, 1600, 1700])
    peaks = MemoryPeaks(peak_rss_mb=2500, peak_cgroup_mb=None, samples=10)

    with patch(
        "weatherbrief.tasks.standalone_verification._read_cgroup_limit_mb",
        return_value=None,
    ):
        with caplog.at_level(logging.WARNING):
            _check_memory_anomaly(session, "standalone_forecast", peaks)

    # Relative threshold = 1600 × 1.4 = 2240; peak 2500 > 2240 → fires.
    assert "memory anomaly" in caplog.text.lower()
    assert "absolute_threshold=n/a" in caplog.text


def test_read_cgroup_limit_handles_unset(tmp_path, monkeypatch):
    """cgroup v2 prints the literal string 'max' when no limit is set —
    must return None, not raise on int() parse."""
    v2 = tmp_path / "memory.max"
    v2.write_text("max\n")
    monkeypatch.setattr("builtins.open", lambda p, *a, **kw: open(str(v2)) if "memory.max" in str(p) else (_ for _ in ()).throw(OSError()))
    # Simpler: use the real function but ensure no real cgroup file exists,
    # then point one of the candidate paths at our 'max' file.
    # Easier still: read the function directly with a mocked file path.

    # Since the function hardcodes paths, easiest to validate via patching open.
    real_open = open

    def fake_open(path, *args, **kwargs):
        if str(path) == "/sys/fs/cgroup/memory.max":
            return real_open(str(v2))
        raise OSError(f"no such file: {path}")

    monkeypatch.setattr("builtins.open", fake_open)
    assert _read_cgroup_limit_mb() is None
