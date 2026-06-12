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


def _ctx(headwinds: list[float | None]) -> RouteContext:
    return RouteContext(
        analyses=[_make_rpa(i, hw) for i, hw in enumerate(headwinds)],
        cross_sections=[],
        elevation=None,
        models=["gfs"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
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

    def test_tas_changes_time_estimate(self):
        slow = _evaluate(_ctx([25.0] * 10), params={"cruise_tas_kt": 80})
        fast = _evaluate(_ctx([25.0] * 10), params={"cruise_tas_kt": 160})
        # Same wind, slower aircraft → larger time penalty in the detail.
        assert slow.per_model[0].detail != fast.per_model[0].detail
