"""Tests for the winds-aloft / trip-impact advisory."""

from __future__ import annotations

from datetime import datetime

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.headwind import HeadwindEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
    WindComponent,
)


def _make_rpa(i: int, headwind_kt: float | None) -> RoutePointAnalysis:
    wind_components = {}
    if headwind_kt is not None:
        # Track 90°, wind FROM 90° at |headwind| kt → pure headwind (or FROM
        # 270° for a tailwind). The evaluator's cross-section path is not
        # exercised here (empty cross_sections → fallback to wind_components).
        wind_components["gfs"] = WindComponent(
            wind_speed_kt=abs(headwind_kt),
            wind_direction_deg=90.0 if headwind_kt >= 0 else 270.0,
            track_deg=90.0,
            headwind_kt=headwind_kt,
            crosswind_kt=0.0,
        )
    return RoutePointAnalysis(
        point_index=i,
        lat=48.0,
        lon=2.0 + i * 0.5,
        distance_from_origin_nm=i * 20.0,
        interpolated_time=datetime(2026, 3, 1, 10, 0),
        forecast_hour=datetime(2026, 3, 1, 9, 0),
        track_deg=90.0,
        wind_components=wind_components,
        sounding={"gfs": SoundingAnalysis(indices=ThermodynamicIndices())},
    )


def _ctx(
    headwinds: list[float | None],
    cruise_speed_ias_kt: float | None = None,
    flight_duration_hours: float = 0.0,
) -> RouteContext:
    return RouteContext(
        analyses=[_make_rpa(i, hw) for i, hw in enumerate(headwinds)],
        cross_sections=[],
        elevation=None,
        models=["gfs"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
        cruise_speed_ias_kt=cruise_speed_ias_kt,
        flight_duration_hours=flight_duration_hours,
    )


def _evaluate(ctx: RouteContext, params: dict | None = None):
    entry = HeadwindEvaluator.catalog_entry()
    defaults = {p.key: p.default for p in entry.parameters}
    return HeadwindEvaluator.evaluate(ctx, {**defaults, **(params or {})})


class TestHeadwindEvaluator:

    def test_catalog_entry(self):
        entry = HeadwindEvaluator.catalog_entry()
        assert entry.id == "headwind"
        assert entry.altitude_dependent is True

    def test_light_winds_green(self):
        result = _evaluate(_ctx([5.0] * 10))
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_strong_headwind_amber_with_time_impact(self):
        result = _evaluate(_ctx([25.0] * 10))
        assert result.aggregate_status == AdvisoryStatus.AMBER
        detail = result.per_model[0].detail
        assert "25kt" in detail
        # 200nm at 110 TAS: still 109.1 min, at GS 85 → 141.2 min → +32 min
        assert "+32 min" in detail

    def test_extreme_headwind_red(self):
        result = _evaluate(_ctx([45.0] * 10))
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_tailwind_green_with_gain(self):
        result = _evaluate(_ctx([-20.0] * 10))
        assert result.aggregate_status == AdvisoryStatus.GREEN
        assert "tailwind" in result.per_model[0].detail.lower()

    def test_no_wind_data_unavailable(self):
        result = _evaluate(_ctx([None] * 10))
        assert result.per_model[0].status == AdvisoryStatus.UNAVAILABLE

    def test_tunable_thresholds(self):
        result = _evaluate(_ctx([25.0] * 10), params={"mean_amber_kt": 30})
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_no_cruise_tas_parameter(self):
        """The cruise speed is resolved per flight — there is no user knob."""
        keys = {p.key for p in HeadwindEvaluator.catalog_entry().parameters}
        assert "cruise_tas_kt" not in keys

    def test_faster_aircraft_smaller_penalty(self):
        """Same wind, faster aircraft cruise speed → smaller time penalty."""
        slow = _evaluate(_ctx([25.0] * 10, cruise_speed_ias_kt=90))
        fast = _evaluate(_ctx([25.0] * 10, cruise_speed_ias_kt=180))
        assert slow.per_model[0].detail != fast.per_model[0].detail

    def test_aircraft_ias_converted_to_tas_at_altitude(self):
        """The aircraft cruise IAS is converted to TAS at the cruise altitude."""
        from weatherbrief.atmo import ias_to_tas_isa

        derived = _evaluate(_ctx([25.0] * 10, cruise_speed_ias_kt=150))
        # Equivalent to a flight whose planned speed is exactly that TAS.
        expected_tas = ias_to_tas_isa(150, 8000)
        dur = _evaluate(_ctx([25.0] * 10, flight_duration_hours=200 / expected_tas))
        assert derived.per_model[0].detail == dur.per_model[0].detail

    def test_duration_fallback_when_no_aircraft_speed(self):
        """With no aircraft/profile speed, fall back to the flight's own planned
        speed (distance ÷ duration)."""
        # 200 nm in 2.0 h → 100 kt TAS; distinct from the 110kt last-resort.
        dur = _evaluate(_ctx([25.0] * 10, flight_duration_hours=2.0))
        last_resort = _evaluate(_ctx([25.0] * 10))
        assert dur.per_model[0].detail != last_resort.per_model[0].detail

    def test_last_resort_default_when_nothing_known(self):
        """No aircraft speed and no usable duration → generic 110kt fallback."""
        no_info = _evaluate(_ctx([25.0] * 10))
        assert "+32 min" in no_info.per_model[0].detail  # the documented 110kt result

    def test_aircraft_speed_beats_duration(self):
        """Aircraft/profile speed takes precedence over the duration fallback."""
        both = _evaluate(_ctx([25.0] * 10, cruise_speed_ias_kt=150, flight_duration_hours=2.0))
        ac_only = _evaluate(_ctx([25.0] * 10, cruise_speed_ias_kt=150))
        assert both.per_model[0].detail == ac_only.per_model[0].detail
