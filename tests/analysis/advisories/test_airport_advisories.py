"""Tests for airport advisory evaluators (flight_category and airport_wind)."""

from __future__ import annotations

from datetime import datetime

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.flight_category import FlightCategoryEvaluator
from weatherbrief.analysis.advisories.airport_wind import AirportWindEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    RoutePointAnalysis,
)
from weatherbrief.models.airport_conditions import (
    AirportConditions,
    AirportConditionsSummary,
    AirportModelCondition,
    FlightCategory,
    RunwayEnd,
    RunwayWind,
)


def _make_ctx(
    airport_conditions: AirportConditions | None,
    models: list[str] | None = None,
) -> RouteContext:
    """Create a minimal RouteContext with airport conditions."""
    return RouteContext(
        analyses=[],
        cross_sections=[],
        elevation=None,
        models=models or ["gfs", "ecmwf"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
        airport_conditions=airport_conditions,
    )


# Default ceiling/visibility values per flight category for test fixtures
_CAT_DEFAULTS: dict[FlightCategory, tuple[int, float]] = {
    FlightCategory.VFR: (5000, 10.0),
    FlightCategory.MVFR: (2000, 4.0),
    FlightCategory.IFR: (800, 2.0),
    FlightCategory.LIFR: (300, 0.5),
}


def _make_airport_conditions(
    dep_cats: dict[str, FlightCategory],
    arr_cats: dict[str, FlightCategory],
    dep_wind: dict[str, RunwayWind | None] | None = None,
    arr_wind: dict[str, RunwayWind | None] | None = None,
    dep_gusts: dict[str, float | None] | None = None,
    arr_gusts: dict[str, float | None] | None = None,
    wind_speed_kt: float = 15.0,
    wind_direction_deg: float = 270.0,
    dep_ceiling: dict[str, int] | None = None,
    dep_vis: dict[str, float] | None = None,
    arr_ceiling: dict[str, int] | None = None,
    arr_vis: dict[str, float] | None = None,
) -> AirportConditions:
    """Build airport conditions from flight categories and optional wind."""
    dep_wind = dep_wind or {}
    arr_wind = arr_wind or {}
    dep_gusts = dep_gusts or {}
    arr_gusts = arr_gusts or {}
    dep_ceiling = dep_ceiling or {}
    dep_vis = dep_vis or {}
    arr_ceiling = arr_ceiling or {}
    arr_vis = arr_vis or {}

    dep_conds = [
        AirportModelCondition(
            model=m,
            flight_category=cat,
            ceiling_ft=dep_ceiling.get(m, _CAT_DEFAULTS[cat][0]),
            visibility_sm=dep_vis.get(m, _CAT_DEFAULTS[cat][1]),
            best_runway=dep_wind.get(m),
            all_runways=[dep_wind[m]] if dep_wind.get(m) else [],
            wind_gust_kt=dep_gusts.get(m),
            wind_speed_kt=wind_speed_kt if dep_wind.get(m) else None,
            wind_direction_deg=wind_direction_deg if dep_wind.get(m) else None,
        )
        for m, cat in dep_cats.items()
    ]
    arr_conds = [
        AirportModelCondition(
            model=m,
            flight_category=cat,
            ceiling_ft=arr_ceiling.get(m, _CAT_DEFAULTS[cat][0]),
            visibility_sm=arr_vis.get(m, _CAT_DEFAULTS[cat][1]),
            best_runway=arr_wind.get(m),
            all_runways=[arr_wind[m]] if arr_wind.get(m) else [],
            wind_gust_kt=arr_gusts.get(m),
            wind_speed_kt=wind_speed_kt if arr_wind.get(m) else None,
            wind_direction_deg=wind_direction_deg if arr_wind.get(m) else None,
        )
        for m, cat in arr_cats.items()
    ]

    return AirportConditions(
        departure=AirportConditionsSummary(icao="LFPG", name="Paris CDG", conditions=dep_conds),
        arrival=AirportConditionsSummary(icao="EGLL", name="London Heathrow", conditions=arr_conds),
    )


# --- FlightCategoryEvaluator ---

class TestFlightCategoryEvaluator:

    def test_catalog_entry(self):
        entry = FlightCategoryEvaluator.catalog_entry()
        assert entry.id == "flight_category"
        assert entry.category == "airport"
        assert len(entry.parameters) == 4

    def test_vfr_both_airports(self):
        ac = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.VFR, "ecmwf": FlightCategory.VFR},
            arr_cats={"gfs": FlightCategory.VFR, "ecmwf": FlightCategory.VFR},
        )
        ctx = _make_ctx(ac)
        result = FlightCategoryEvaluator.evaluate(ctx, {})
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_mvfr_at_arrival(self):
        ac = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.VFR, "ecmwf": FlightCategory.VFR},
            arr_cats={"gfs": FlightCategory.MVFR, "ecmwf": FlightCategory.MVFR},
        )
        ctx = _make_ctx(ac)
        result = FlightCategoryEvaluator.evaluate(ctx, {})
        assert result.aggregate_status == AdvisoryStatus.AMBER

    def test_ifr_at_departure(self):
        ac = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.IFR, "ecmwf": FlightCategory.VFR},
            arr_cats={"gfs": FlightCategory.VFR, "ecmwf": FlightCategory.VFR},
        )
        ctx = _make_ctx(ac)
        result = FlightCategoryEvaluator.evaluate(ctx, {})
        # GFS has IFR → RED, worst across models is RED
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_lifr_is_red(self):
        ac = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.VFR},
            arr_cats={"gfs": FlightCategory.LIFR},
        )
        ctx = _make_ctx(ac, models=["gfs"])
        result = FlightCategoryEvaluator.evaluate(ctx, {})
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_no_airport_conditions(self):
        ctx = _make_ctx(None)
        result = FlightCategoryEvaluator.evaluate(ctx, {})
        assert len(result.per_model) == 0

    def test_per_model_detail(self):
        ac = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.VFR},
            arr_cats={"gfs": FlightCategory.MVFR},
        )
        ctx = _make_ctx(ac, models=["gfs"])
        result = FlightCategoryEvaluator.evaluate(ctx, {})
        assert "LFPG" in result.per_model[0].detail
        assert "EGLL" in result.per_model[0].detail
        assert "VFR" in result.per_model[0].detail
        assert "MVFR" in result.per_model[0].detail


# --- AirportWindEvaluator ---

class TestAirportWindEvaluator:

    def test_catalog_entry(self):
        entry = AirportWindEvaluator.catalog_entry()
        assert entry.id == "airport_wind"
        assert entry.category == "airport"
        assert len(entry.parameters) == 4

    def test_low_crosswind_green(self):
        rwy = RunwayWind(runway_id="09L", heading_deg=90.0, crosswind_kt=8.0, headwind_kt=15.0)
        ac = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.VFR},
            arr_cats={"gfs": FlightCategory.VFR},
            dep_wind={"gfs": rwy},
            arr_wind={"gfs": rwy},
        )
        ctx = _make_ctx(ac, models=["gfs"])
        result = AirportWindEvaluator.evaluate(ctx, {})
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_high_crosswind_amber(self):
        rwy = RunwayWind(runway_id="09L", heading_deg=90.0, crosswind_kt=18.0, headwind_kt=10.0)
        ac = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.VFR},
            arr_cats={"gfs": FlightCategory.VFR},
            dep_wind={"gfs": rwy},
            arr_wind={"gfs": rwy},
        )
        ctx = _make_ctx(ac, models=["gfs"])
        result = AirportWindEvaluator.evaluate(ctx, {})
        assert result.aggregate_status == AdvisoryStatus.AMBER

    def test_very_high_crosswind_red(self):
        rwy = RunwayWind(runway_id="09L", heading_deg=90.0, crosswind_kt=30.0, headwind_kt=5.0)
        ac = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.VFR},
            arr_cats={"gfs": FlightCategory.VFR},
            dep_wind={"gfs": rwy},
            arr_wind={"gfs": rwy},
        )
        ctx = _make_ctx(ac, models=["gfs"])
        result = AirportWindEvaluator.evaluate(ctx, {})
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_gust_amber(self):
        rwy = RunwayWind(runway_id="09L", heading_deg=90.0, crosswind_kt=5.0, headwind_kt=15.0)
        ac = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.VFR},
            arr_cats={"gfs": FlightCategory.VFR},
            dep_wind={"gfs": rwy},
            arr_wind={"gfs": rwy},
            dep_gusts={"gfs": 28.0},
            arr_gusts={"gfs": None},
        )
        ctx = _make_ctx(ac, models=["gfs"])
        result = AirportWindEvaluator.evaluate(ctx, {})
        assert result.aggregate_status == AdvisoryStatus.AMBER

    def test_gust_red(self):
        rwy = RunwayWind(runway_id="09L", heading_deg=90.0, crosswind_kt=5.0, headwind_kt=15.0)
        ac = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.VFR},
            arr_cats={"gfs": FlightCategory.VFR},
            dep_wind={"gfs": rwy},
            arr_wind={"gfs": rwy},
            dep_gusts={"gfs": 40.0},
        )
        ctx = _make_ctx(ac, models=["gfs"])
        result = AirportWindEvaluator.evaluate(ctx, {})
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_no_airport_conditions(self):
        ctx = _make_ctx(None, models=["gfs"])
        result = AirportWindEvaluator.evaluate(ctx, {})
        assert len(result.per_model) == 0

    def test_custom_thresholds(self):
        rwy = RunwayWind(runway_id="09L", heading_deg=90.0, crosswind_kt=12.0, headwind_kt=15.0)
        ac = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.VFR},
            arr_cats={"gfs": FlightCategory.VFR},
            dep_wind={"gfs": rwy},
            arr_wind={"gfs": rwy},
        )
        ctx = _make_ctx(ac, models=["gfs"])
        # With lower thresholds, 12kt crosswind should be amber
        result = AirportWindEvaluator.evaluate(ctx, {"crosswind_green_kt": 10})
        assert result.aggregate_status == AdvisoryStatus.AMBER

    def test_per_model_detail_contains_runway(self):
        rwy = RunwayWind(runway_id="09L", heading_deg=90.0, crosswind_kt=8.0, headwind_kt=15.0)
        ac = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.VFR},
            arr_cats={"gfs": FlightCategory.VFR},
            dep_wind={"gfs": rwy},
            arr_wind={"gfs": rwy},
        )
        ctx = _make_ctx(ac, models=["gfs"])
        result = AirportWindEvaluator.evaluate(ctx, {})
        assert "09L" in result.per_model[0].detail
        assert "LFPG" in result.per_model[0].detail
