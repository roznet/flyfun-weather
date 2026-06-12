"""Tests for the en-route precipitation advisory and its VFR composite feed."""

from __future__ import annotations

from datetime import datetime

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.enroute_precip import (
    EnroutePrecipEvaluator,
    classify_enroute_precip,
)
from weatherbrief.analysis.advisories.vfr_feasibility import VFRFeasibilityEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    PrecipIntensity,
    PrecipPhase,
    PrecipitationAssessment,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
)


def _make_rpa(i: int, sounding: dict[str, SoundingAnalysis]) -> RoutePointAnalysis:
    return RoutePointAnalysis(
        point_index=i,
        lat=48.0 + i * 0.5,
        lon=2.0 + i * 0.5,
        distance_from_origin_nm=i * 20.0,
        interpolated_time=datetime(2026, 3, 1, 10, 0),
        forecast_hour=datetime(2026, 3, 1, 9, 0),
        track_deg=90.0,
        sounding=sounding,
    )


def _sounding(
    phase: PrecipPhase = PrecipPhase.DRY,
    intensity: PrecipIntensity = PrecipIntensity.NONE,
    with_precip: bool = True,
) -> SoundingAnalysis:
    return SoundingAnalysis(
        indices=ThermodynamicIndices(),
        precipitation=(
            PrecipitationAssessment(surface_phase=phase, surface_intensity=intensity)
            if with_precip else None
        ),
    )


def _ctx(per_point: list[SoundingAnalysis]) -> RouteContext:
    analyses = [_make_rpa(i, {"gfs": s}) for i, s in enumerate(per_point)]
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=None,
        models=["gfs"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
    )


def _evaluate(ctx: RouteContext, params: dict | None = None):
    entry = EnroutePrecipEvaluator.catalog_entry()
    defaults = {p.key: p.default for p in entry.parameters}
    return EnroutePrecipEvaluator.evaluate(ctx, {**defaults, **(params or {})})


class TestEnroutePrecipEvaluator:

    def test_dry_route_is_green(self):
        ctx = _ctx([_sounding() for _ in range(10)])
        result = _evaluate(ctx)
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_no_precip_data_is_unavailable(self):
        ctx = _ctx([_sounding(with_precip=False) for _ in range(10)])
        result = _evaluate(ctx)
        assert result.per_model[0].status == AdvisoryStatus.UNAVAILABLE

    def test_light_rain_stays_green(self):
        per_point = [
            _sounding(PrecipPhase.RAIN, PrecipIntensity.LIGHT) if i < 4 else _sounding()
            for i in range(10)
        ]
        result = _evaluate(_ctx(per_point))
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_light_snow_is_amber(self):
        # 2/10 points with light snow > 5% default amber threshold
        per_point = [
            _sounding(PrecipPhase.SNOW, PrecipIntensity.LIGHT) if i < 2 else _sounding()
            for i in range(10)
        ]
        result = _evaluate(_ctx(per_point))
        assert result.aggregate_status == AdvisoryStatus.AMBER
        assert "Snow" in result.per_model[0].detail

    def test_widespread_moderate_snow_is_red(self):
        per_point = [
            _sounding(PrecipPhase.SNOW, PrecipIntensity.MODERATE) if i < 4 else _sounding()
            for i in range(10)
        ]
        result = _evaluate(_ctx(per_point))
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_widespread_moderate_rain_is_amber(self):
        per_point = [
            _sounding(PrecipPhase.RAIN, PrecipIntensity.MODERATE) if i < 4 else _sounding()
            for i in range(10)
        ]
        result = _evaluate(_ctx(per_point))
        assert result.aggregate_status == AdvisoryStatus.AMBER

    def test_freezing_rain_counts_as_significant_extent(self):
        # FZRA over 40% of the route exceeds the rain amber threshold (30%).
        # Severity (RED) is owned by the freezing_precip advisory.
        per_point = [
            _sounding(PrecipPhase.FREEZING_RAIN, PrecipIntensity.LIGHT) if i < 4 else _sounding()
            for i in range(10)
        ]
        result = _evaluate(_ctx(per_point))
        assert result.aggregate_status == AdvisoryStatus.AMBER

    def test_tunable_snow_threshold(self):
        per_point = [
            _sounding(PrecipPhase.SNOW, PrecipIntensity.LIGHT) if i < 2 else _sounding()
            for i in range(10)
        ]
        result = _evaluate(_ctx(per_point), params={"snow_pct_amber": 30})
        assert result.aggregate_status == AdvisoryStatus.GREEN


class TestVFRPrecipFeed:

    def test_vfr_composite_capped_at_amber_on_snow(self):
        # Widespread moderate snow REDs the standalone advisory but only
        # AMBERs the VFR composite.
        per_point = [
            _sounding(PrecipPhase.SNOW, PrecipIntensity.MODERATE) if i < 4 else _sounding()
            for i in range(10)
        ]
        ctx = _ctx(per_point)

        status, _, _, _, signal = classify_enroute_precip(ctx, "gfs")
        assert signal and status == AdvisoryStatus.RED

        entry = VFRFeasibilityEvaluator.catalog_entry()
        defaults = {p.key: p.default for p in entry.parameters}
        result = VFRFeasibilityEvaluator.evaluate(ctx, defaults)
        assert result.aggregate_status == AdvisoryStatus.AMBER
        assert "Snow" in result.per_model[0].detail

    def test_vfr_composite_unaffected_by_missing_precip_data(self):
        ctx = _ctx([_sounding(with_precip=False) for _ in range(10)])
        entry = VFRFeasibilityEvaluator.catalog_entry()
        defaults = {p.key: p.default for p in entry.parameters}
        result = VFRFeasibilityEvaluator.evaluate(ctx, defaults)
        assert result.per_model[0].status == AdvisoryStatus.GREEN
