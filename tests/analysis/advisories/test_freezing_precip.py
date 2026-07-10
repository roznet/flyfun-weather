"""Tests for the freezing precipitation advisory evaluator."""

from __future__ import annotations

from datetime import datetime

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories import freezing_precip as freezing_precip_module
from weatherbrief.analysis.advisories.freezing_precip import FreezingPrecipEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    DerivedLevel,
    PrecipPhase,
    PrecipitationAssessment,
    RoutePointAnalysis,
    SoundingAnalysis,
)


def _rpa(i: int, sounding: SoundingAnalysis) -> RoutePointAnalysis:
    return RoutePointAnalysis(
        point_index=i, lat=48.0, lon=2.0 + i * 0.5,
        distance_from_origin_nm=i * 20.0,
        interpolated_time=datetime(2026, 1, 10, 10, 0),
        forecast_hour=datetime(2026, 1, 10, 10, 0),
        track_deg=90.0,
        sounding={"gfs": sounding},
    )


def _ctx(soundings: list[SoundingAnalysis]) -> RouteContext:
    return RouteContext(
        analyses=[_rpa(i, s) for i, s in enumerate(soundings)],
        cross_sections=[], elevation=None, models=["gfs"],
        cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        total_distance_nm=20.0 * max(len(soundings) - 1, 1),
    )


def _dry() -> SoundingAnalysis:
    """Benign point: dry assessment, no warm nose in the profile."""
    return SoundingAnalysis(
        precipitation=PrecipitationAssessment(),
        derived_levels=[
            DerivedLevel(pressure_hpa=1000, altitude_ft=400, wet_bulb_c=4.0),
            DerivedLevel(pressure_hpa=900, altitude_ft=3200, wet_bulb_c=0.5),
            DerivedLevel(pressure_hpa=800, altitude_ft=6400, wet_bulb_c=-4.0),
        ],
    )


def _active_fzra() -> SoundingAnalysis:
    return SoundingAnalysis(
        precipitation=PrecipitationAssessment(
            surface_phase=PrecipPhase.FREEZING_RAIN,
            freezing_rain_risk=True,
            total_mm=1.2,
        ),
    )


def _primed() -> SoundingAnalysis:
    """No active precip, but warm nose over sub-zero surface wet-bulb."""
    return SoundingAnalysis(
        precipitation=PrecipitationAssessment(),  # dry hour
        derived_levels=[
            DerivedLevel(pressure_hpa=1000, altitude_ft=500, wet_bulb_c=-2.0),
            DerivedLevel(pressure_hpa=925, altitude_ft=3000, wet_bulb_c=1.0),
            DerivedLevel(pressure_hpa=900, altitude_ft=4000, wet_bulb_c=1.5),
            DerivedLevel(pressure_hpa=800, altitude_ft=6500, wet_bulb_c=-5.0),
        ],
    )


def test_active_freezing_rain_is_red():
    ctx = _ctx([_dry()] * 9 + [_active_fzra()])
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.RED
    assert "Freezing precipitation" in result.per_model[0].detail
    active = [
        region
        for region in result.per_model[0].evidence_regions
        if region.reason_code == "active_freezing_precip"
    ]
    assert active
    assert all(
        region.lower_altitude_ft is None and region.upper_altitude_ft is None
        for region in active
    )


def test_active_freezing_precip_uses_warm_nose_bounds():
    active = _active_fzra().model_copy(
        update={
            "precipitation": PrecipitationAssessment(
                surface_phase=PrecipPhase.FREEZING_RAIN,
                freezing_rain_risk=True,
                warm_nose_base_ft=3000,
                warm_nose_top_ft=5000,
                total_mm=1.2,
            )
        }
    )
    result = FreezingPrecipEvaluator.evaluate(
        _ctx([_dry()] * 9 + [active]),
        {"primed_pct_amber": 5},
    )
    model = result.per_model[0]
    active_regions = [
        region
        for region in model.evidence_regions
        if region.reason_code == "active_freezing_precip"
    ]
    assert model.status == AdvisoryStatus.RED
    assert model.primary_method_id == "nwp_precipitation_profile"
    assert active_regions
    assert all(region.metric_id == "sld_risk" for region in active_regions)
    assert all(
        region.lower_altitude_ft is not None
        and region.upper_altitude_ft is not None
        for region in active_regions
    )


def test_ice_pellets_is_red():
    sounding = SoundingAnalysis(
        precipitation=PrecipitationAssessment(
            surface_phase=PrecipPhase.ICE_PELLETS, total_mm=0.8,
        ),
    )
    ctx = _ctx([_dry()] * 9 + [sounding])
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.RED


def test_primed_profile_is_amber():
    ctx = _ctx([_dry()] * 8 + [_primed()] * 2)  # 20% primed >= 5% default
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.AMBER
    assert "profile" in result.per_model[0].detail.lower()
    primed = [
        region
        for region in result.per_model[0].evidence_regions
        if region.reason_code == "primed_freezing_rain_profile"
    ]
    assert primed
    assert all(
        (region.lower_altitude_ft, region.upper_altitude_ft) == (3000, 4000)
        for region in primed
    )


def test_warm_nose_detection_runs_once_per_available_point(monkeypatch):
    calls = []
    real_detect = freezing_precip_module.detect_warm_nose

    def recording_detect(levels):
        calls.append(levels)
        return real_detect(levels)

    monkeypatch.setattr(freezing_precip_module, "detect_warm_nose", recording_detect)
    FreezingPrecipEvaluator.evaluate(_ctx([_dry()] * 9 + [_active_fzra()]), {})
    assert len(calls) == 10


def test_primed_below_threshold_is_green():
    ctx = _ctx([_dry()] * 8 + [_primed()] * 2)
    result = FreezingPrecipEvaluator.evaluate(ctx, {"primed_pct_amber": 30})
    assert result.aggregate_status == AdvisoryStatus.GREEN


def test_all_dry_is_green():
    ctx = _ctx([_dry()] * 10)
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.GREEN


def test_no_precip_data_is_unavailable():
    bare = SoundingAnalysis()  # no precipitation, no derived levels
    ctx = _ctx([bare] * 10)
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.per_model[0].status == AdvisoryStatus.UNAVAILABLE


def test_freezing_precip_missing_signal_is_unavailable_not_clear():
    result = FreezingPrecipEvaluator.evaluate(_ctx([SoundingAnalysis()] * 10), {})
    model = result.per_model[0]
    assert model.status == AdvisoryStatus.UNAVAILABLE
    assert model.data_state == "unavailable"
