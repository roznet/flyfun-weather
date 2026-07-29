"""Unit tests for freshness schedule registry (issue #108)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.fetch.freshness.registry import (
    SOURCE_REGISTRY,
    cycle_init_for,
    expected_delivery_for_init,
    initial_marker_for,
    max_horizon,
    next_cycle_init_after,
    next_full_horizon_run,
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


class TestNextCycleInitAfter:
    def test_returns_next_cycle_init_without_offset(self):
        # 00Z + 1 = 06Z (no offset added — caller composes with
        # expected_delivery_for_init when delivery wallclock is needed).
        nxt = next_cycle_init_after("ecmwf:direct", _utc(2026, 5, 3, 0))
        assert nxt == _utc(2026, 5, 3, 6)

    def test_rolls_to_next_day(self):
        nxt = next_cycle_init_after("ecmwf:direct", _utc(2026, 5, 3, 18))
        assert nxt == _utc(2026, 5, 4, 0)


# ---------------------------------------------------------------------------
# run_horizon
# ---------------------------------------------------------------------------


class TestRunHorizon:
    def test_ecmwf_direct_00z_is_168h(self):
        assert run_horizon("ecmwf:direct", _utc(2026, 5, 3, 0)) == timedelta(hours=168)

    def test_ecmwf_direct_06z_is_medium_only_144h(self):
        # 06/18z are the short cut-off cycles and stop short of the 168h that
        # 00/12z reach — but they do run to 144h, per the delivery manifest and
        # the files on disk.  Not 90h: that is where the hourly step cadence
        # ends, which is a different fact.
        assert run_horizon("ecmwf:direct", _utc(2026, 5, 3, 6)) == timedelta(hours=144)

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
        "icon_d2:dwd",
        "gfs:openmeteo",
        "ecmwf:openmeteo",
        "icon:openmeteo",
        "meteofrance:openmeteo",
        "ukmo:openmeteo",
        "gem:openmeteo",
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


# ---------------------------------------------------------------------------
# max_horizon / next_full_horizon_run (issue #192)
# ---------------------------------------------------------------------------


class TestMaxHorizon:
    def test_ecmwf_direct_is_full_168h(self):
        # 00/12Z reach 168h; 06/18Z only 90h — the max is 168h.
        assert max_horizon("ecmwf:direct") == timedelta(hours=168)

    def test_uniform_horizon_source(self):
        assert max_horizon("gfs:noaa") == timedelta(hours=384)


class TestNextFullHorizonRun:
    """The big-run selector the email scheduler waits on (ECMWF 00/12Z)."""

    def test_excludes_medium_cycles_picks_next_00z(self):
        # A 06:00 slot: the imminent full-horizon run is 00Z (delivers 06:40),
        # NOT the 06Z medium cycle (90h, delivers 12:40).
        init, delivery = next_full_horizon_run("ecmwf:direct", _utc(2026, 3, 1, 6))
        assert init == _utc(2026, 3, 1, 0)
        assert delivery == _utc(2026, 3, 1, 6, 40)

    def test_after_morning_delivery_next_is_12z(self):
        # An 08:00 slot is past the 06:40 (00Z) delivery; the next full-horizon
        # run is 12Z (delivers 18:40) — the 06Z medium run is skipped.
        init, delivery = next_full_horizon_run("ecmwf:direct", _utc(2026, 3, 1, 8))
        assert init == _utc(2026, 3, 1, 12)
        assert delivery == _utc(2026, 3, 1, 18, 40)

    def test_evening_slot_picks_12z(self):
        init, delivery = next_full_horizon_run("ecmwf:direct", _utc(2026, 3, 1, 18))
        assert init == _utc(2026, 3, 1, 12)
        assert delivery == _utc(2026, 3, 1, 18, 40)

    def test_late_evening_rolls_to_next_day_00z(self):
        # After the 18:40 (12Z) delivery, the next full run is tomorrow's 00Z.
        init, delivery = next_full_horizon_run("ecmwf:direct", _utc(2026, 3, 1, 19))
        assert init == _utc(2026, 3, 2, 0)
        assert delivery == _utc(2026, 3, 2, 6, 40)

    def test_delivery_strictly_after_slot(self):
        # A slot exactly at a delivery time gets the *next* run, not that one.
        _, delivery = next_full_horizon_run("ecmwf:direct", _utc(2026, 3, 1, 6, 40))
        assert delivery == _utc(2026, 3, 1, 18, 40)

    def test_uniform_horizon_every_cycle_qualifies(self):
        # GFS has a single horizon, so every cycle is "full"; next delivery
        # after a 04:00 slot is the 00Z run (00Z + 5h = 05:00).
        init, delivery = next_full_horizon_run("gfs:noaa", _utc(2026, 3, 1, 4))
        assert init == _utc(2026, 3, 1, 0)
        assert delivery == _utc(2026, 3, 1, 5)
