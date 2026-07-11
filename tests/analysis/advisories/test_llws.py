"""Tests for the low-level wind shear (LLWS) advisory evaluator."""

from __future__ import annotations

from datetime import datetime

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.llws import LLWSEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    InversionLayer,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
)
from weatherbrief.models.airport_conditions import (
    AirportConditions,
    AirportConditionsSummary,
    AirportModelCondition,
    FlightCategory,
)


def _sounding(
    shear_0_1km_kt: float | None,
    surface_inversion: bool = False,
) -> SoundingAnalysis:
    inversions = []
    if surface_inversion:
        inversions = [InversionLayer(
            base_ft=400, top_ft=900, strength_c=4.0, surface_based=True,
        )]
    return SoundingAnalysis(
        indices=ThermodynamicIndices(bulk_shear_0_1km_kt=shear_0_1km_kt),
        inversion_layers=inversions,
    )


def _rpa(i: int, sounding: SoundingAnalysis) -> RoutePointAnalysis:
    return RoutePointAnalysis(
        point_index=i, lat=48.0, lon=2.0 + i,
        distance_from_origin_nm=i * 100.0,
        interpolated_time=datetime(2026, 6, 1, 5, 0),
        forecast_hour=datetime(2026, 6, 1, 5, 0),
        track_deg=90.0,
        sounding={"gfs": sounding},
    )


def _conditions(
    wind_speed_kt: float | None = 6.0,
    wind_gust_kt: float | None = 10.0,
) -> AirportConditions:
    cond = AirportModelCondition(
        model="gfs", flight_category=FlightCategory.VFR,
        wind_speed_kt=wind_speed_kt, wind_gust_kt=wind_gust_kt,
    )
    return AirportConditions(
        departure=AirportConditionsSummary(icao="EGTK", name="Oxford", conditions=[cond]),
        arrival=AirportConditionsSummary(icao="LSGS", name="Sion", conditions=[cond]),
    )


def _ctx(
    dep: SoundingAnalysis,
    arr: SoundingAnalysis,
    conditions: AirportConditions | None = None,
) -> RouteContext:
    return RouteContext(
        analyses=[_rpa(0, dep), _rpa(1, arr)],
        cross_sections=[], elevation=None, models=["gfs"],
        cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        total_distance_nm=100,
        airport_conditions=conditions if conditions is not None else _conditions(),
    )


def test_low_shear_is_green():
    result = LLWSEvaluator.evaluate(_ctx(_sounding(8), _sounding(12)), {})
    assert result.aggregate_status == AdvisoryStatus.GREEN
    assert result.per_model[0].data_state == "complete"
    assert result.per_model[0].primary_method_id == "llws_composite"


def test_amber_shear_at_arrival():
    result = LLWSEvaluator.evaluate(_ctx(_sounding(8), _sounding(24)), {})
    assert result.aggregate_status == AdvisoryStatus.AMBER
    assert "LSGS" in result.per_model[0].detail
    assert result.per_model[0].primary_method_id == "bulk_shear"


def test_red_shear():
    result = LLWSEvaluator.evaluate(_ctx(_sounding(34), _sounding(10)), {})
    assert result.aggregate_status == AdvisoryStatus.RED
    assert result.per_model[0].primary_method_id == "bulk_shear"


def test_surface_inversion_reported_with_shear():
    """Nocturnal-jet signature: inversion mentioned when shear is significant."""
    result = LLWSEvaluator.evaluate(
        _ctx(_sounding(25, surface_inversion=True), _sounding(8)), {},
    )
    assert result.aggregate_status == AdvisoryStatus.AMBER
    assert "inversion" in result.per_model[0].detail


def test_inversion_not_reported_without_shear():
    result = LLWSEvaluator.evaluate(
        _ctx(_sounding(8, surface_inversion=True), _sounding(8)), {},
    )
    assert "inversion" not in result.per_model[0].detail


def test_gust_factor_triggers_amber():
    conditions = _conditions(wind_speed_kt=10.0, wind_gust_kt=28.0)  # factor 18
    result = LLWSEvaluator.evaluate(
        _ctx(_sounding(8), _sounding(8), conditions), {},
    )
    assert result.aggregate_status == AdvisoryStatus.AMBER
    assert "gust factor" in result.per_model[0].detail
    assert result.per_model[0].data_state == "complete"
    assert result.per_model[0].primary_method_id == "gust_factor"


def test_benign_gust_factor_without_shear_is_partial_unavailable():
    conditions = _conditions(wind_speed_kt=10.0, wind_gust_kt=14.0)
    model = LLWSEvaluator.evaluate(
        _ctx(_sounding(None), _sounding(None), conditions), {},
    ).per_model[0]

    assert model.status == AdvisoryStatus.UNAVAILABLE
    assert model.data_state == "partial"
    assert model.primary_method_id == "gust_factor"


def test_hazardous_gust_factor_without_shear_preserves_amber():
    conditions = _conditions(wind_speed_kt=10.0, wind_gust_kt=28.0)
    model = LLWSEvaluator.evaluate(
        _ctx(_sounding(None), _sounding(None), conditions), {},
    ).per_model[0]

    assert model.status == AdvisoryStatus.AMBER
    assert model.data_state == "partial"
    assert model.primary_method_id == "gust_factor"


def test_equal_shear_and_gust_factor_grade_uses_composite_method():
    conditions = _conditions(wind_speed_kt=10.0, wind_gust_kt=28.0)
    model = LLWSEvaluator.evaluate(
        _ctx(_sounding(24), _sounding(24), conditions), {},
    ).per_model[0]

    assert model.status == AdvisoryStatus.AMBER
    assert model.data_state == "complete"
    assert model.primary_method_id == "llws_composite"


def test_unavailable_without_any_signal():
    conditions = _conditions(wind_speed_kt=None, wind_gust_kt=None)
    result = LLWSEvaluator.evaluate(
        _ctx(_sounding(None), _sounding(None), conditions), {},
    )
    assert result.per_model[0].status == AdvisoryStatus.UNAVAILABLE


def test_param_override():
    result = LLWSEvaluator.evaluate(
        _ctx(_sounding(24), _sounding(8)), {"shear_amber_kt": 28},
    )
    assert result.aggregate_status == AdvisoryStatus.GREEN
