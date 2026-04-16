"""Tests for weatherbrief.frontal.tracking — timeseries and clearance timing."""

import pytest

from weatherbrief.frontal.tracking import (
    find_frontal_clearance_time,
    find_clearance_times_all_models,
    compute_timing_spread,
)


def _make_timeseries(zone: str, entries: list[dict]) -> dict:
    """Build a zone_timeseries dict for one zone."""
    return {zone: entries}


def _entry(hour: int, present: bool, front_type: str | None = None) -> dict:
    return {
        "hour": hour,
        "present": present,
        "type": front_type,
        "intensity": 2.0 if present else None,
        "orientation": "NE-SW" if present else None,
    }


class TestFindFrontalClearanceTime:
    def test_simple_clearance(self):
        """Front present 0-12, clear from 13 → clearance at 13."""
        entries = [_entry(h, h <= 12) for h in range(24)]
        ts = _make_timeseries("zone_a", entries)
        result = find_frontal_clearance_time(ts, "zone_a", min_clear_hours=3)
        assert result == 13

    def test_false_clearance_filtered(self):
        """Front clears for 2h then returns → no clearance (min_clear=3)."""
        entries = []
        for h in range(30):
            if h < 10:
                entries.append(_entry(h, True))
            elif h < 12:
                entries.append(_entry(h, False))  # 2h gap
            elif h < 20:
                entries.append(_entry(h, True))  # returns
            else:
                entries.append(_entry(h, False))
        ts = _make_timeseries("zone_a", entries)
        result = find_frontal_clearance_time(ts, "zone_a", min_clear_hours=3)
        # Should skip the 2h gap and find clearance at hour 20
        assert result == 20

    def test_front_persists(self):
        """Front present through max_horizon → None."""
        entries = [_entry(h, True) for h in range(73)]
        ts = _make_timeseries("zone_a", entries)
        result = find_frontal_clearance_time(ts, "zone_a", max_horizon=72)
        assert result is None

    def test_never_frontal(self):
        """No front at all → clearance at hour 0."""
        entries = [_entry(h, False) for h in range(24)]
        ts = _make_timeseries("zone_a", entries)
        result = find_frontal_clearance_time(ts, "zone_a", min_clear_hours=3)
        assert result == 0

    def test_missing_zone(self):
        """Unknown zone → None."""
        ts = _make_timeseries("zone_a", [])
        result = find_frontal_clearance_time(ts, "zone_b")
        assert result is None

    def test_max_horizon_respected(self):
        """Front clears at hour 50 but max_horizon=48 → None."""
        entries = [_entry(h, h < 50) for h in range(72)]
        ts = _make_timeseries("zone_a", entries)
        result = find_frontal_clearance_time(
            ts, "zone_a", max_horizon=48, min_clear_hours=3,
        )
        assert result is None


class TestFindClearanceTimesAllModels:
    def test_multi_model(self):
        ts_gfs = _make_timeseries("zone_a", [_entry(h, h < 24) for h in range(48)])
        ts_ecmwf = _make_timeseries("zone_a", [_entry(h, h < 30) for h in range(48)])
        ts_icon = _make_timeseries("zone_a", [_entry(h, h < 18) for h in range(48)])

        all_ts = {"gfs": ts_gfs, "ecmwf": ts_ecmwf, "icon": ts_icon}
        result = find_clearance_times_all_models(all_ts, "zone_a")

        assert result["gfs"] == 24
        assert result["ecmwf"] == 30
        assert result["icon"] == 18


class TestComputeTimingSpread:
    def test_agreement(self):
        clearance = {"gfs": 24, "ecmwf": 26, "icon": 24}
        result = compute_timing_spread(clearance)
        assert result["spread_hours"] == 2
        assert result["agreement"] is True
        assert result["min_clearance"] == 24
        assert result["max_clearance"] == 26

    def test_disagreement(self):
        clearance = {"gfs": 24, "ecmwf": 36, "icon": 48}
        result = compute_timing_spread(clearance)
        assert result["spread_hours"] == 24
        assert result["agreement"] is False

    def test_one_model_persists(self):
        clearance = {"gfs": 24, "ecmwf": None, "icon": 30}
        result = compute_timing_spread(clearance)
        # Should compute spread from the 2 valid models
        assert result["spread_hours"] == 6
        assert result["agreement"] is True

    def test_insufficient_data(self):
        clearance = {"gfs": None, "ecmwf": None, "icon": 24}
        result = compute_timing_spread(clearance)
        assert result["spread_hours"] is None
        assert result["agreement"] is False
        assert result["note"] == "insufficient_data"
