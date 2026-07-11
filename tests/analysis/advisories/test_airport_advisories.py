"""Tests for airport advisory evaluators (flight_category and airport_wind)."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.airport_wind import (
    AirportWindEvaluator,
    _wind_status,
)
from weatherbrief.analysis.advisories.flight_category import FlightCategoryEvaluator
from weatherbrief.analysis.airport_conditions import compute_runway_winds
from weatherbrief.models import (
    AdvisoryStatus,
    ConvectiveAssessment,
    ConvectiveRisk,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
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
    terminal_convective_risk: ConvectiveRisk | None = ConvectiveRisk.NONE,
) -> RouteContext:
    """Create a minimal RouteContext with airport conditions."""
    selected_models = models or ["gfs", "ecmwf"]
    analyses = []
    for point_index, distance_nm in enumerate((0.0, 200.0)):
        soundings = {
            model: SoundingAnalysis(
                indices=ThermodynamicIndices(),
                convective=(
                    ConvectiveAssessment(risk_level=terminal_convective_risk)
                    if terminal_convective_risk is not None
                    else None
                ),
            )
            for model in selected_models
        }
        analyses.append(
            RoutePointAnalysis(
                point_index=point_index,
                lat=48.0,
                lon=2.0 + point_index,
                distance_from_origin_nm=distance_nm,
                interpolated_time=datetime(2026, 6, 1, 12, 0),
                forecast_hour=datetime(2026, 6, 1, 12, 0),
                track_deg=90.0,
                sounding=soundings,
            )
        )
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=None,
        models=selected_models,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
        airport_conditions=airport_conditions,
    )


def _without_ceiling_visibility(
    conditions: AirportConditions,
    *endpoints: str,
) -> AirportConditions:
    updates = {}
    for endpoint in endpoints:
        summary = getattr(conditions, endpoint)
        updates[endpoint] = summary.model_copy(
            update={
                "conditions": [
                    condition.model_copy(
                        update={
                            "ceiling_ft": None,
                            "ceiling_evaluated": False,
                            "visibility_sm": None,
                        }
                    )
                    for condition in summary.conditions
                ]
            }
        )
    return conditions.model_copy(update=updates)


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
    dep_ceiling: dict[str, int | None] | None = None,
    dep_ceiling_evaluated: dict[str, bool] | None = None,
    dep_vis: dict[str, float] | None = None,
    arr_ceiling: dict[str, int | None] | None = None,
    arr_ceiling_evaluated: dict[str, bool] | None = None,
    arr_vis: dict[str, float] | None = None,
) -> AirportConditions:
    """Build airport conditions from flight categories and optional wind."""
    dep_wind = dep_wind or {}
    arr_wind = arr_wind or {}
    dep_gusts = dep_gusts or {}
    arr_gusts = arr_gusts or {}
    dep_ceiling = dep_ceiling or {}
    dep_ceiling_evaluated = dep_ceiling_evaluated or {}
    dep_vis = dep_vis or {}
    arr_ceiling = arr_ceiling or {}
    arr_ceiling_evaluated = arr_ceiling_evaluated or {}
    arr_vis = arr_vis or {}

    dep_conds = [
        AirportModelCondition(
            model=m,
            flight_category=cat,
            ceiling_ft=dep_ceiling.get(m, _CAT_DEFAULTS[cat][0]),
            ceiling_evaluated=dep_ceiling_evaluated.get(m, False),
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
            ceiling_evaluated=arr_ceiling_evaluated.get(m, False),
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
        assert len(entry.parameters) == 5

    def test_vfr_both_airports(self):
        ac = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.VFR, "ecmwf": FlightCategory.VFR},
            arr_cats={"gfs": FlightCategory.VFR, "ecmwf": FlightCategory.VFR},
        )
        ctx = _make_ctx(ac)
        result = FlightCategoryEvaluator.evaluate(ctx, {})
        assert result.aggregate_status == AdvisoryStatus.GREEN
        assert result.per_model[0].data_state == "complete"
        assert result.per_model[0].primary_method_id == "airport_conditions"
        assert result.per_model[0].total_points == 2

    def test_missing_condition_sources_with_clear_convection_is_partial(self):
        conditions = _without_ceiling_visibility(
            _make_airport_conditions(
                dep_cats={"gfs": FlightCategory.VFR},
                arr_cats={"gfs": FlightCategory.VFR},
            ),
            "departure",
            "arrival",
        )

        model = FlightCategoryEvaluator.evaluate(
            _make_ctx(conditions, models=["gfs"]),
            {},
        ).per_model[0]

        assert model.status == AdvisoryStatus.UNAVAILABLE
        assert model.data_state == "partial"
        assert "VFR" not in model.detail

    def test_assessed_clear_ceiling_with_vfr_visibility_is_complete_green(self):
        conditions = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.VFR},
            arr_cats={"gfs": FlightCategory.VFR},
            dep_ceiling={"gfs": None},
            dep_ceiling_evaluated={"gfs": True},
            dep_vis={"gfs": 10.0},
            arr_ceiling={"gfs": None},
            arr_ceiling_evaluated={"gfs": True},
            arr_vis={"gfs": 10.0},
        )

        model = FlightCategoryEvaluator.evaluate(
            _make_ctx(conditions, models=["gfs"]),
            {},
        ).per_model[0]

        assert model.status == AdvisoryStatus.GREEN
        assert model.data_state == "complete"

    def test_missing_terminal_convection_with_vfr_conditions_is_partial(self):
        conditions = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.VFR},
            arr_cats={"gfs": FlightCategory.VFR},
        )

        model = FlightCategoryEvaluator.evaluate(
            _make_ctx(
                conditions,
                models=["gfs"],
                terminal_convective_risk=None,
            ),
            {},
        ).per_model[0]

        assert model.status == AdvisoryStatus.UNAVAILABLE
        assert model.data_state == "partial"

    def test_condition_hazard_survives_missing_terminal_convection(self):
        conditions = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.IFR},
            arr_cats={"gfs": FlightCategory.VFR},
        )

        model = FlightCategoryEvaluator.evaluate(
            _make_ctx(
                conditions,
                models=["gfs"],
                terminal_convective_risk=None,
            ),
            {},
        ).per_model[0]

        assert model.status == AdvisoryStatus.RED
        assert model.data_state == "partial"
        assert model.affected_points == 1

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

    def test_hazardous_departure_survives_missing_arrival_model_condition(self):
        conditions = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.IFR},
            arr_cats={},
        )

        result = FlightCategoryEvaluator.evaluate(
            _make_ctx(conditions, models=["gfs"]),
            {},
        ).per_model[0]

        assert result.status == AdvisoryStatus.RED
        assert result.data_state == "partial"
        assert result.total_points == 2
        assert result.affected_points == 1

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
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE
        assert [model.model for model in result.per_model] == ["gfs", "ecmwf"]
        assert all(model.status == AdvisoryStatus.UNAVAILABLE for model in result.per_model)
        assert all(model.data_state == "unavailable" for model in result.per_model)

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
        assert result.per_model[0].data_state == "complete"
        assert result.per_model[0].primary_method_id == "runway_components"
        assert result.per_model[0].total_points == 2

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
        ctx = _make_ctx(None)
        result = AirportWindEvaluator.evaluate(ctx, {})
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE
        assert [model.model for model in result.per_model] == ["gfs", "ecmwf"]
        assert all(model.status == AdvisoryStatus.UNAVAILABLE for model in result.per_model)
        assert all(model.data_state == "unavailable" for model in result.per_model)

    def test_condition_without_crosswind_or_gust_is_missing_not_calm(self):
        conditions = _make_airport_conditions(
            dep_cats={"gfs": FlightCategory.VFR},
            arr_cats={"gfs": FlightCategory.VFR},
        )

        result = AirportWindEvaluator.evaluate(
            _make_ctx(conditions, models=["gfs"]),
            {},
        )

        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE
        assert result.per_model[0].data_state == "unavailable"
        assert "calm" not in result.per_model[0].detail.lower()

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


def test_gust_vector_crosswind_is_not_recalibrated_without_evidence():
    # A223-09 characterization only: pins the current policy; this is not an
    # endorsement or a gust-vector crosswind recalibration.
    status = _wind_status(
        crosswind_kt=12.0,
        gust_kt=28.0,
        xwind_green=15.0,
        xwind_red=25.0,
        gust_green=25.0,
        gust_red=35.0,
    )
    assert status == AdvisoryStatus.AMBER


def test_public_airport_wind_keeps_gust_and_crosswind_as_separate_axes():
    # A223-09 characterization only: pins the public evaluator's current policy;
    # this is not an endorsement or a gust-vector crosswind recalibration.
    # The prior hand-populated components contradicted the stored wind direction,
    # so a direction-based gust-vector implementation could still pass the test.
    runway_end = RunwayEnd(id="09L", heading_deg=90.0)
    mean_crosswind_kt = 12.0
    mean_headwind_kt = 5.0
    wind_speed_kt = math.hypot(mean_crosswind_kt, mean_headwind_kt)
    wind_direction_deg = (
        runway_end.heading_deg
        + math.degrees(math.atan2(mean_crosswind_kt, mean_headwind_kt))
    ) % 360
    runway = compute_runway_winds(
        [runway_end],
        wind_speed_kt=wind_speed_kt,
        wind_direction_deg=wind_direction_deg,
    )[0]
    assert runway.crosswind_kt == pytest.approx(mean_crosswind_kt, abs=0.1)
    assert runway.headwind_kt == pytest.approx(mean_headwind_kt, abs=0.1)

    gust_kt = 28.0
    conditions = _make_airport_conditions(
        dep_cats={"gfs": FlightCategory.VFR},
        arr_cats={"gfs": FlightCategory.VFR},
        dep_wind={"gfs": runway},
        arr_wind={"gfs": runway},
        dep_gusts={"gfs": gust_kt},
        wind_speed_kt=wind_speed_kt,
        wind_direction_deg=wind_direction_deg,
    )
    departure = conditions.departure.condition_for_model("gfs")
    assert departure is not None
    assert departure.best_runway is not None
    assert departure.wind_direction_deg is not None
    assert departure.wind_gust_kt is not None
    relative_wind_rad = math.radians(
        departure.wind_direction_deg - departure.best_runway.heading_deg
    )
    direction_based_gust_crosswind_kt = abs(
        departure.wind_gust_kt * math.sin(relative_wind_rad)
    )
    assert direction_based_gust_crosswind_kt > 25.0

    result = AirportWindEvaluator.evaluate(
        _make_ctx(conditions, models=["gfs"]),
        {
            "crosswind_green_kt": 15.0,
            "crosswind_red_kt": 25.0,
            "gust_green_kt": 30.0,
            "gust_red_kt": 40.0,
        },
    )

    assert result.aggregate_status == AdvisoryStatus.GREEN


# ---------------------------------------------------------------------------
# DensityAltitudeEvaluator
# ---------------------------------------------------------------------------

from weatherbrief.analysis.advisories.density_altitude import (
    DensityAltitudeEvaluator,
    compute_density_altitude_ft,
)
from weatherbrief.models import ElevationPoint, ElevationProfile


def _flat_elevation(elev_ft: float, total_nm: float = 200) -> ElevationProfile:
    points = [
        ElevationPoint(distance_nm=d, elevation_ft=elev_ft, lat=46.0, lon=7.0)
        for d in (0.0, total_nm / 2, total_nm)
    ]
    return ElevationProfile(
        route_name="test", points=points,
        max_elevation_ft=elev_ft, total_distance_nm=total_nm,
    )


def _da_ctx(
    temperature_c: float | None,
    qnh_hpa: float | None = 1013.25,
    elev_ft: float = 1500,
    elevation: ElevationProfile | None = "default",
) -> RouteContext:
    cond = AirportModelCondition(
        model="gfs", flight_category=FlightCategory.VFR,
        temperature_c=temperature_c, qnh_hpa=qnh_hpa,
    )
    conditions = AirportConditions(
        departure=AirportConditionsSummary(icao="LSGS", name="Sion", conditions=[cond]),
        arrival=AirportConditionsSummary(icao="LSZH", name="Zurich", conditions=[cond]),
    )
    if elevation == "default":
        elevation = _flat_elevation(elev_ft)
    return RouteContext(
        analyses=[], cross_sections=[], elevation=elevation,
        models=["gfs"], cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        total_distance_nm=200, airport_conditions=conditions,
    )


def test_density_altitude_isa_sea_level_is_zero():
    da = compute_density_altitude_ft(0.0, 15.0, 1013.25)
    assert abs(da) < 1.0


def test_density_altitude_hot_high_field():
    # 2000 ft field, +30°C, no QNH → ISA at 2000 ft = 11.04°C
    # DA = 2000 + 118.8 × 18.96 ≈ 4253 ft
    da = compute_density_altitude_ft(2000.0, 30.0, None)
    assert da == pytest.approx(4253, abs=10)


def test_density_altitude_green_cool_day():
    result = DensityAltitudeEvaluator.evaluate(_da_ctx(temperature_c=10.0), {})
    assert result.aggregate_status == AdvisoryStatus.GREEN
    assert result.per_model[0].data_state == "complete"
    assert result.per_model[0].primary_method_id == "density_altitude"
    assert result.per_model[0].total_points == 2


def test_density_altitude_amber_hot_day():
    # 1500 ft field, +35°C → DA ≈ 1500 + 118.8 × (35 − 12.03) ≈ 4229 ft
    # below absolute amber (5000) but +2729 above field... below delta 3000.
    # Push to +38°C → DA ≈ 4586, delta ≈ 3086 → AMBER via delta criterion.
    result = DensityAltitudeEvaluator.evaluate(_da_ctx(temperature_c=38.0), {})
    assert result.aggregate_status == AdvisoryStatus.AMBER


def test_density_altitude_red_mountain_heat():
    # 5000 ft field, +32°C → DA ≈ 5000 + 118.8 × (32 − 5.09) ≈ 8197 ft → RED
    result = DensityAltitudeEvaluator.evaluate(
        _da_ctx(temperature_c=32.0, elev_ft=5000), {},
    )
    assert result.aggregate_status == AdvisoryStatus.RED


def test_density_altitude_unavailable_without_temperature():
    result = DensityAltitudeEvaluator.evaluate(_da_ctx(temperature_c=None), {})
    assert result.per_model[0].status == AdvisoryStatus.UNAVAILABLE


def test_density_altitude_unavailable_without_elevation():
    result = DensityAltitudeEvaluator.evaluate(
        _da_ctx(temperature_c=30.0, elevation=None), {},
    )
    assert result.per_model[0].status == AdvisoryStatus.UNAVAILABLE


def test_density_altitude_missing_airports_returns_each_requested_model_unavailable():
    result = DensityAltitudeEvaluator.evaluate(_make_ctx(None), {})

    assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE
    assert [model.model for model in result.per_model] == ["gfs", "ecmwf"]
    assert all(model.status == AdvisoryStatus.UNAVAILABLE for model in result.per_model)
    assert all(model.data_state == "unavailable" for model in result.per_model)


# --- FlightCategoryEvaluator: terminal convective aspect ---

def _conv_rpa(i: int, distance_nm: float, risk) -> RoutePointAnalysis:
    conv = ConvectiveAssessment(risk_level=risk) if risk is not None else None
    return RoutePointAnalysis(
        point_index=i,
        lat=48.0,
        lon=2.0 + i * 0.5,
        distance_from_origin_nm=distance_nm,
        interpolated_time=datetime(2026, 6, 1, 12, 0),
        forecast_hour=datetime(2026, 6, 1, 12, 0),
        track_deg=90.0,
        sounding={"gfs": SoundingAnalysis(indices=ThermodynamicIndices(), convective=conv)},
    )


def _terminal_conv_ctx(dep_risk, arr_risk, mid_risk=None) -> RouteContext:
    """200nm route, 10 points: convective risk at the endpoints and optionally mid-route."""
    analyses = []
    for i in range(10):
        d = i * 200.0 / 9
        if d <= 25:
            risk = dep_risk
        elif d >= 175:
            risk = arr_risk
        else:
            risk = mid_risk
        analyses.append(_conv_rpa(i, d, risk))
    conditions = _make_airport_conditions(
        {"gfs": FlightCategory.VFR}, {"gfs": FlightCategory.VFR},
    )
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=None,
        models=["gfs"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
        airport_conditions=conditions,
    )


class TestTerminalConvective:

    def test_quiet_terminals_green(self):
        from weatherbrief.models import ConvectiveRisk

        ctx = _terminal_conv_ctx(ConvectiveRisk.LOW, ConvectiveRisk.NONE)
        result = FlightCategoryEvaluator.evaluate(ctx, {})
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_moderate_at_arrival_amber_despite_low_route_coverage(self):
        from weatherbrief.models import ConvectiveRisk

        # One MODERATE zone only at the arrival end — % -of-route dilution must
        # not apply at the terminal.
        ctx = _terminal_conv_ctx(ConvectiveRisk.NONE, ConvectiveRisk.MODERATE)
        result = FlightCategoryEvaluator.evaluate(ctx, {})
        assert result.aggregate_status == AdvisoryStatus.AMBER
        assert "convective MODERATE" in result.per_model[0].detail

    def test_high_at_departure_red(self):
        from weatherbrief.models import ConvectiveRisk

        ctx = _terminal_conv_ctx(ConvectiveRisk.HIGH, ConvectiveRisk.NONE)
        result = FlightCategoryEvaluator.evaluate(ctx, {})
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_mid_route_convection_not_attributed_to_terminals(self):
        from weatherbrief.models import ConvectiveRisk

        ctx = _terminal_conv_ctx(
            ConvectiveRisk.NONE, ConvectiveRisk.NONE, mid_risk=ConvectiveRisk.HIGH,
        )
        result = FlightCategoryEvaluator.evaluate(ctx, {})
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_radius_parameter_widens_terminal(self):
        from weatherbrief.models import ConvectiveRisk

        # HIGH at ~89nm (mid-route point 4) is outside default 25nm radius but
        # inside a 120nm one.
        ctx = _terminal_conv_ctx(
            ConvectiveRisk.NONE, ConvectiveRisk.NONE, mid_risk=ConvectiveRisk.HIGH,
        )
        result = FlightCategoryEvaluator.evaluate(ctx, {"conv_radius_nm": 120})
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_terminal_hazard_survives_missing_condition_object(self):
        ctx = _terminal_conv_ctx(ConvectiveRisk.HIGH, ConvectiveRisk.NONE)
        conditions = _make_airport_conditions(
            dep_cats={},
            arr_cats={"gfs": FlightCategory.VFR},
        )

        model = FlightCategoryEvaluator.evaluate(
            replace(ctx, airport_conditions=conditions),
            {},
        ).per_model[0]

        assert model.status == AdvisoryStatus.RED
        assert model.data_state == "partial"
        assert model.affected_points == 1
        assert "convective HIGH" in model.detail
