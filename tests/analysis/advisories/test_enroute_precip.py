"""Tests for the en-route precipitation advisory and its VFR composite feed."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories import enroute_precip as enroute_precip_module
from weatherbrief.analysis.advisories import vfr_feasibility as vfr_module
from weatherbrief.analysis.advisories.enroute_precip import (
    EnroutePrecipEvaluator,
    classify_enroute_precip,
)
from weatherbrief.analysis.advisories.evidence import EvidenceSample, summarize_evidence
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


def _shared_assessment_stub(ctx: RouteContext):
    indices = {rpa.point_index for rpa in ctx.analyses}
    summary = summarize_evidence(
        route_points=ctx.analyses,
        total_distance_nm=ctx.total_distance_nm,
        evaluated_point_indices=indices,
        complete_point_indices=indices,
        affected_point_indices={0},
        evidence_samples=[
            EvidenceSample(
                point_index=0,
                severity=AdvisoryStatus.AMBER,
                reason_code="precip_visibility",
                metric_id="precipitation_mm",
                method_id="nwp_precipitation_profile",
            )
        ],
    )
    return SimpleNamespace(
        status=AdvisoryStatus.AMBER,
        detail="Shared precipitation assessment",
        summary=summary,
        has_signal=True,
        snow_point_indices=frozenset(),
        moderate_snow_point_indices=frozenset(),
        significant_rain_point_indices=frozenset({0}),
        light_point_indices=frozenset(),
    )


def test_assessment_exposes_point_sets_summary_and_compatibility_tuple():
    per_point = [
        _sounding(PrecipPhase.SNOW, PrecipIntensity.LIGHT),
        _sounding(PrecipPhase.SNOW, PrecipIntensity.MODERATE),
        _sounding(PrecipPhase.RAIN, PrecipIntensity.MODERATE),
        _sounding(PrecipPhase.RAIN, PrecipIntensity.LIGHT),
        *[_sounding() for _ in range(6)],
    ]
    ctx = _ctx(per_point)
    assessment = getattr(
        enroute_precip_module,
        "assess_enroute_precip",
    )(ctx, "gfs")

    assessment_type = getattr(enroute_precip_module, "EnroutePrecipAssessment")
    assert isinstance(assessment, assessment_type)
    assert assessment.snow_point_indices == frozenset({0, 1})
    assert assessment.moderate_snow_point_indices == frozenset({1})
    assert assessment.significant_rain_point_indices == frozenset({2})
    assert assessment.light_point_indices == frozenset({3})
    assert assessment.summary.affected_points == 4
    assert assessment.summary.total_points == 10
    assert assessment.summary.evidence_regions
    assert all(
        region.reason_code == "precip_visibility"
        and region.metric_id == "precipitation_mm"
        and region.method_id == "nwp_precipitation_profile"
        for region in assessment.summary.evidence_regions
    )
    assert classify_enroute_precip(ctx, "gfs") == (
        assessment.status,
        assessment.detail,
        assessment.summary.affected_points,
        assessment.summary.total_points,
        assessment.has_signal,
    )

    model = _evaluate(ctx).per_model[0]
    assert model.primary_method_id == "nwp_precipitation_profile"
    assert model.evidence_regions == assessment.summary.evidence_regions


def test_compatibility_wrapper_preserves_legacy_total_without_precip_signal():
    ctx = _ctx([_sounding(with_precip=False) for _ in range(10)])

    status, _, affected, total, has_signal = classify_enroute_precip(ctx, "gfs")

    assert status == AdvisoryStatus.UNAVAILABLE
    assert affected == 0
    assert total == 10
    assert has_signal is False


def test_standalone_and_vfr_consumers_call_shared_assessment(monkeypatch):
    ctx = _ctx([_sounding() for _ in range(10)])
    shared = _shared_assessment_stub(ctx)
    standalone_calls = []
    vfr_calls = []

    def fake_standalone(ctx_arg, model, params=None):
        standalone_calls.append((ctx_arg, model, params))
        return shared

    def fake_vfr(ctx_arg, model, params=None):
        vfr_calls.append((ctx_arg, model, params))
        return shared

    monkeypatch.setattr(
        enroute_precip_module,
        "assess_enroute_precip",
        fake_standalone,
        raising=False,
    )
    monkeypatch.setattr(
        vfr_module,
        "assess_enroute_precip",
        fake_vfr,
        raising=False,
    )

    standalone = _evaluate(ctx)
    vfr_entry = VFRFeasibilityEvaluator.catalog_entry()
    vfr_defaults = {parameter.key: parameter.default for parameter in vfr_entry.parameters}
    vfr = VFRFeasibilityEvaluator.evaluate(ctx, vfr_defaults)

    assert standalone_calls == [(ctx, "gfs", {
        "snow_pct_amber": 5,
        "snow_moderate_pct_red": 25,
        "rain_pct_amber": 30,
    })]
    assert vfr_calls == [(ctx, "gfs", None)]
    assert standalone.per_model[0].detail == shared.detail
    assert standalone.aggregate_status == AdvisoryStatus.AMBER
    assert vfr.per_model[0].detail.endswith(shared.detail)
    assert vfr.aggregate_status == AdvisoryStatus.AMBER


def test_partial_clear_precipitation_is_unavailable_not_clear():
    ctx = _ctx(
        [_sounding() for _ in range(9)]
        + [_sounding(with_precip=False)]
    )
    model = _evaluate(ctx).per_model[0]
    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.UNAVAILABLE


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
