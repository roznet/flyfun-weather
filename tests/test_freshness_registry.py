"""Unit tests for freshness schedule registry (issue #108)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.fetch.freshness.registry import (
    SOURCE_REGISTRY,
    cycle_init_for,
    expected_delivery_for_init,
    initial_marker_for,
    next_cycle_after,
    next_run_after,
    run_horizon,
)


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# next_run_after
# ---------------------------------------------------------------------------


class TestNextRunAfter:
    def test_ecmwf_direct_00z_to_06z(self):
        # 00Z init → next cycle 06Z + 6h40m offset
        nxt = next_run_after("ecmwf:direct", _utc(2026, 5, 3, 0))
        assert nxt == _utc(2026, 5, 3, 6) + timedelta(hours=6, minutes=40)

    def test_ecmwf_direct_18z_rolls_to_next_day(self):
        nxt = next_run_after("ecmwf:direct", _utc(2026, 5, 3, 18))
        assert nxt == _utc(2026, 5, 4, 0) + timedelta(hours=6, minutes=40)

    def test_gfs_noaa_12z_to_18z(self):
        nxt = next_run_after("gfs:noaa", _utc(2026, 5, 3, 12))
        assert nxt == _utc(2026, 5, 3, 18) + timedelta(hours=5)

    def test_icon_eu_dwd_3h_intermediate(self):
        # 06Z (main) → 09Z (intermediate) + 3h offset
        nxt = next_run_after("icon_eu:dwd", _utc(2026, 5, 3, 6))
        assert nxt == _utc(2026, 5, 3, 9) + timedelta(hours=3)

    def test_ecmwf_openmeteo_skips_06_18(self):
        # Registry only tracks ECMWF Open-Meteo 00/12 cycles.
        nxt = next_run_after("ecmwf:openmeteo", _utc(2026, 5, 3, 0))
        assert nxt == _utc(2026, 5, 3, 12) + timedelta(hours=8)

    def test_ecmwf_openmeteo_12z_rolls_to_next_day_00z(self):
        nxt = next_run_after("ecmwf:openmeteo", _utc(2026, 5, 3, 12))
        assert nxt == _utc(2026, 5, 4, 0) + timedelta(hours=8)


class TestNextCycleAfter:
    def test_skip_one_cycle(self):
        # If 06Z is slipping past cap, jump to expected delivery of 12Z
        nxt = next_cycle_after("ecmwf:direct", _utc(2026, 5, 3, 6))
        assert nxt == _utc(2026, 5, 3, 12) + timedelta(hours=6, minutes=40)


# ---------------------------------------------------------------------------
# run_horizon
# ---------------------------------------------------------------------------


class TestRunHorizon:
    def test_ecmwf_direct_00z_is_168h(self):
        assert run_horizon("ecmwf:direct", _utc(2026, 5, 3, 0)) == timedelta(hours=168)

    def test_ecmwf_direct_06z_is_medium_only_90h(self):
        assert run_horizon("ecmwf:direct", _utc(2026, 5, 3, 6)) == timedelta(hours=90)

    def test_icon_eu_main_120h(self):
        for h in (0, 6, 12, 18):
            assert run_horizon("icon_eu:dwd", _utc(2026, 5, 3, h)) == timedelta(hours=120)

    def test_icon_eu_intermediate_78h(self):
        for h in (3, 9, 15, 21):
            assert run_horizon("icon_eu:dwd", _utc(2026, 5, 3, h)) == timedelta(hours=78)

    def test_gfs_noaa_uniform_384h(self):
        assert run_horizon("gfs:noaa", _utc(2026, 5, 3, 0)) == timedelta(hours=384)


# ---------------------------------------------------------------------------
# cycle_init_for / expected_delivery_for_init
# ---------------------------------------------------------------------------


class TestCycleMath:
    def test_cycle_init_floors_to_most_recent(self):
        # 14:30Z → most recent ECMWF cycle is 12Z
        assert cycle_init_for("ecmwf:direct", _utc(2026, 5, 3, 14, 30)) == _utc(2026, 5, 3, 12)

    def test_cycle_init_handles_midnight_rollover(self):
        # 00:30Z → most recent ECMWF cycle is 00Z (today, not yesterday's 18Z)
        assert cycle_init_for("ecmwf:direct", _utc(2026, 5, 3, 0, 30)) == _utc(2026, 5, 3, 0)

    def test_expected_delivery_uses_per_cycle_offset(self):
        # ECMWF uses uniform offset, but verify the dispatch path
        d = expected_delivery_for_init("ecmwf:direct", _utc(2026, 5, 3, 0))
        assert d == _utc(2026, 5, 3, 6) + timedelta(minutes=40)


# ---------------------------------------------------------------------------
# initial_marker_for (bootstrap)
# ---------------------------------------------------------------------------


class TestInitialMarker:
    def test_bootstrap_uses_latest_delivered_cycle(self):
        # At 09:00Z, ECMWF 00Z cycle (delivered ~06:40Z) is the latest ready.
        # Next expected is 06Z cycle's delivery at 12:40Z.
        init, nxt = initial_marker_for("ecmwf:direct", now=_utc(2026, 5, 3, 9))
        assert init == _utc(2026, 5, 3, 0)
        assert nxt == _utc(2026, 5, 3, 6) + timedelta(hours=6, minutes=40)

    def test_bootstrap_falls_back_when_current_cycle_not_yet_due(self):
        # At 03:00Z, the 00Z cycle won't be ready until 06:40Z.
        # Fall back to yesterday's 18Z cycle.
        init, nxt = initial_marker_for("ecmwf:direct", now=_utc(2026, 5, 3, 3))
        assert init == _utc(2026, 5, 2, 18)
        assert nxt == _utc(2026, 5, 3, 0) + timedelta(hours=6, minutes=40)

    def test_bootstrap_for_each_registered_source(self):
        # Smoke: every registered source produces a valid (init, next_expected).
        now = _utc(2026, 5, 3, 12)
        for key in SOURCE_REGISTRY:
            init, nxt = initial_marker_for(key, now=now)
            assert init.tzinfo == timezone.utc
            assert nxt > init


# ---------------------------------------------------------------------------
# Registry coverage
# ---------------------------------------------------------------------------


def test_all_expected_sources_registered():
    expected = {
        "ecmwf:direct",
        "gfs:noaa",
        "icon_eu:dwd",
        "gfs:openmeteo",
        "ecmwf:openmeteo",
        "icon:openmeteo",
        "meteofrance:openmeteo",
        "ukmo:openmeteo",
    }
    assert set(SOURCE_REGISTRY.keys()) == expected


@pytest.mark.parametrize("key", list(SOURCE_REGISTRY.keys()))
def test_each_source_has_sane_offsets(key):
    cfg = SOURCE_REGISTRY[key]
    assert cfg.retry_interval > timedelta(0)
    assert cfg.max_slip_retries > 0
    # Offset should be at least 30 min and at most 12 h for any reasonable source.
    for cycle in cfg.cycles:
        off = cfg.offset_for(cycle)
        assert timedelta(minutes=30) <= off <= timedelta(hours=12)
        h = cfg.horizon_for(cycle)
        assert h >= timedelta(hours=24)
