"""Action metadata and missing-data contracts for context advisories."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.dd_nwp_agreement import (
    DDvsNWPAgreementEvaluator,
)
from weatherbrief.analysis.advisories.llws import LLWSEvaluator
from weatherbrief.analysis.advisories.model_agreement import ModelAgreementEvaluator
from weatherbrief.models import (
    AgreementLevel,
    AdvisoryStatus,
    EnhancedCloudLayer,
    ModelDivergence,
    NWPCloudDiagnostics,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
)


def _route_point(
    point_index: int,
    distance_nm: float,
    sounding: SoundingAnalysis,
) -> RoutePointAnalysis:
    return RoutePointAnalysis(
        point_index=point_index,
        lat=48.0 + point_index * 0.1,
        lon=2.0 + point_index * 0.1,
        distance_from_origin_nm=distance_nm,
        interpolated_time=datetime(2026, 6, 1, 12, 0),
        forecast_hour=datetime(2026, 6, 1, 12, 0),
        track_deg=90.0,
        sounding={"gfs": sounding},
    )


def _cloud_layer(base_ft: float, top_ft: float, source: str) -> EnhancedCloudLayer:
    return EnhancedCloudLayer(base_ft=base_ft, top_ft=top_ft, source=source)


def _dd_nwp_sounding(
    *,
    dd_freezing_ft: float = 4_000,
    nwp_freezing_ft: float = 4_000,
    dd_cloud: tuple[float, float] = (1_000, 3_000),
    nwp_cloud: tuple[float, float] = (1_000, 3_000),
) -> SoundingAnalysis:
    return SoundingAnalysis(
        indices=ThermodynamicIndices(
            freezing_level_ft=dd_freezing_ft,
            nwp_freezing_level_ft=nwp_freezing_ft,
        ),
        dd_cloud_layers=[_cloud_layer(*dd_cloud, source="dd")],
        nwp_cloud_layers=[_cloud_layer(*nwp_cloud, source="nwp_3d")],
    )


def _dd_nwp_context() -> RouteContext:
    analyses = [
        _route_point(0, 0.0, _dd_nwp_sounding()),
        _route_point(
            1,
            50.0,
            _dd_nwp_sounding(
                dd_freezing_ft=3_000,
                nwp_freezing_ft=7_000,
                dd_cloud=(1_000, 3_000),
                nwp_cloud=(6_000, 8_000),
            ),
        ),
        _route_point(2, 100.0, _dd_nwp_sounding()),
    ]
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=None,
        models=["gfs"],
        cruise_altitude_ft=8_000,
        flight_ceiling_ft=18_000,
        total_distance_nm=100.0,
    )


def test_model_agreement_is_one_cross_model_result_with_spatial_metadata(
    poor_agreement_context: RouteContext,
):
    result = ModelAgreementEvaluator.evaluate(
        poor_agreement_context,
        {"min_poor_vars": 3, "poor_pct_amber": 25, "poor_pct_red": 50},
    )

    assert result.aggregate_status == AdvisoryStatus.RED
    assert result.representative_model == "all"
    assert len(result.per_model) == 1
    model_result = result.per_model[0]
    assert model_result.model == "all"
    assert model_result.data_state == "complete"
    assert model_result.primary_method_id == "model_divergence"
    assert model_result.affected_points == 10
    assert model_result.affected_nm == 200.0
    assert {
        (region.reason_code, region.metric_id, region.method_id, region.severity)
        for region in model_result.evidence_regions
    } == {
        (
            "poor_model_agreement",
            "cloud_cover_pct",
            "model_divergence",
            AdvisoryStatus.RED,
        ),
        (
            "poor_model_agreement",
            "temperature_c",
            "model_divergence",
            AdvisoryStatus.RED,
        ),
        (
            "poor_model_agreement",
            "wind_speed_kt",
            "model_divergence",
            AdvisoryStatus.RED,
        ),
    }


def test_model_agreement_missing_route_divergence_is_unavailable(
    clear_context: RouteContext,
):
    result = ModelAgreementEvaluator.evaluate(clear_context, {})

    assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE
    assert [model.model for model in result.per_model] == ["all"]
    assert result.per_model[0].data_state == "unavailable"
    assert result.per_model[0].primary_method_id == "model_divergence"


def test_model_agreement_all_absent_metrics_are_unavailable_not_good(
    clear_context: RouteContext,
):
    absent = ModelDivergence(
        variable="cape_jkg",
        model_values={"gfs": None, "ecmwf": None},
        mean=None,
        spread=0.0,
        agreement=AgreementLevel.GOOD,
    )
    ctx = replace(
        clear_context,
        analyses=[
            point.model_copy(update={"model_divergence": [absent]})
            for point in clear_context.analyses
        ],
    )

    result = ModelAgreementEvaluator.evaluate(ctx, {}).per_model[0]

    assert result.status == AdvisoryStatus.UNAVAILABLE
    assert result.data_state == "unavailable"
    assert result.total_points == 0
    assert result.affected_points == 0
    assert result.evidence_regions == []


def test_model_agreement_mixed_valid_and_absent_metrics_is_partial(
    clear_context: RouteContext,
):
    valid = ModelDivergence(
        variable="temperature_c",
        model_values={"gfs": 10.0, "ecmwf": 13.0},
        mean=11.5,
        spread=3.0,
        agreement=AgreementLevel.MODERATE,
    )
    absent = ModelDivergence(
        variable="cape_jkg",
        model_values={"gfs": None, "ecmwf": None},
        mean=None,
        spread=0.0,
        agreement=AgreementLevel.GOOD,
    )
    ctx = replace(
        clear_context,
        analyses=[
            point.model_copy(update={"model_divergence": [valid, absent]})
            for point in clear_context.analyses
        ],
    )

    result = ModelAgreementEvaluator.evaluate(ctx, {}).per_model[0]

    assert result.status == AdvisoryStatus.UNAVAILABLE
    assert result.data_state == "partial"
    assert result.total_points == 10
    assert result.affected_points == 0
    assert result.affected_mod_points == 10
    assert result.affected_mod_pct == 100.0
    assert {
        (region.reason_code, region.metric_id)
        for region in result.evidence_regions
    } == {("moderate_model_agreement", "temperature_c")}


def test_model_agreement_numeric_mean_with_null_model_value_remains_complete(
    clear_context: RouteContext,
):
    divergence = ModelDivergence(
        variable="temperature_c",
        model_values={"gfs": 10.0, "ecmwf": 12.0, "icon": None},
        mean=11.0,
        spread=2.0,
        agreement=AgreementLevel.GOOD,
    )
    ctx = replace(
        clear_context,
        analyses=[
            point.model_copy(update={"model_divergence": [divergence]})
            for point in clear_context.analyses
        ],
    )

    result = ModelAgreementEvaluator.evaluate(ctx, {}).per_model[0]

    assert result.status == AdvisoryStatus.GREEN
    assert result.data_state == "complete"
    assert result.total_points == 10


def test_model_agreement_moderate_only_evidence_stays_outside_poor_extent(
    clear_context: RouteContext,
):
    moderate = ModelDivergence(
        variable="temperature_c",
        model_values={"gfs": 10.0, "ecmwf": 13.0},
        mean=11.5,
        spread=3.0,
        agreement=AgreementLevel.MODERATE,
    )
    ctx = replace(
        clear_context,
        analyses=[
            point.model_copy(update={"model_divergence": [moderate]})
            for point in clear_context.analyses
        ],
    )

    result = ModelAgreementEvaluator.evaluate(ctx, {}).per_model[0]

    assert result.status == AdvisoryStatus.GREEN
    assert result.affected_points == 0
    assert result.affected_nm == 0.0
    assert result.affected_mod_points == 10
    assert result.evidence_regions
    assert {region.severity for region in result.evidence_regions} == {
        AdvisoryStatus.AMBER,
    }
    assert {region.metric_id for region in result.evidence_regions} == {
        "temperature_c",
    }
    assert {region.method_id for region in result.evidence_regions} == {
        "model_divergence",
    }


def test_model_agreement_subthreshold_poor_variables_do_not_emit_evidence(
    clear_context: RouteContext,
):
    divergences = [
        ModelDivergence(
            variable="temperature_c",
            model_values={"gfs": 5.0, "ecmwf": 15.0},
            mean=10.0,
            spread=10.0,
            agreement=AgreementLevel.POOR,
        ),
        ModelDivergence(
            variable="wind_speed_kt",
            model_values={"gfs": 5.0, "ecmwf": 25.0},
            mean=15.0,
            spread=20.0,
            agreement=AgreementLevel.POOR,
        ),
    ]
    ctx = replace(
        clear_context,
        analyses=[
            point.model_copy(update={"model_divergence": divergences})
            for point in clear_context.analyses
        ],
    )

    result = ModelAgreementEvaluator.evaluate(
        ctx,
        {"min_poor_vars": 3},
    ).per_model[0]

    assert result.status == AdvisoryStatus.GREEN
    assert result.data_state == "complete"
    assert result.affected_points == 0
    assert result.evidence_regions == []


def test_model_agreement_subthreshold_poor_keeps_only_moderate_evidence(
    clear_context: RouteContext,
):
    divergences = [
        ModelDivergence(
            variable="temperature_c",
            model_values={"gfs": 5.0, "ecmwf": 15.0},
            mean=10.0,
            spread=10.0,
            agreement=AgreementLevel.POOR,
        ),
        ModelDivergence(
            variable="wind_speed_kt",
            model_values={"gfs": 5.0, "ecmwf": 25.0},
            mean=15.0,
            spread=20.0,
            agreement=AgreementLevel.POOR,
        ),
        ModelDivergence(
            variable="cloud_cover_pct",
            model_values={"gfs": 30.0, "ecmwf": 60.0},
            mean=45.0,
            spread=30.0,
            agreement=AgreementLevel.MODERATE,
        ),
    ]
    ctx = replace(
        clear_context,
        analyses=[
            point.model_copy(update={"model_divergence": divergences})
            for point in clear_context.analyses
        ],
    )

    result = ModelAgreementEvaluator.evaluate(
        ctx,
        {"min_poor_vars": 3},
    ).per_model[0]

    assert result.status == AdvisoryStatus.GREEN
    assert result.data_state == "complete"
    assert result.affected_points == 0
    assert result.affected_mod_points == 10
    assert {
        (
            region.reason_code,
            region.metric_id,
            region.method_id,
            region.severity,
        )
        for region in result.evidence_regions
    } == {
        (
            "moderate_model_agreement",
            "cloud_cover_pct",
            "model_divergence",
            AdvisoryStatus.AMBER,
        )
    }


def test_dd_nwp_disagreement_exposes_freezing_span_and_both_cloud_sources():
    result = DDvsNWPAgreementEvaluator.evaluate(_dd_nwp_context(), {})

    assert result.aggregate_status == AdvisoryStatus.AMBER
    model_result = result.per_model[0]
    assert model_result.data_state == "complete"
    assert model_result.primary_method_id == "dd_vs_nwp"
    assert model_result.affected_points == 1
    assert model_result.affected_nm == 50.0

    by_reason = {
        region.reason_code: region for region in model_result.evidence_regions
    }
    freezing = by_reason["freezing_level_disagreement"]
    assert (freezing.lower_altitude_ft, freezing.upper_altitude_ft) == (3_000, 7_000)
    assert freezing.metric_id == "freezing_level_ft"
    assert freezing.method_id == "dd_vs_nwp"
    assert freezing.severity == AdvisoryStatus.AMBER

    dd_cloud = by_reason["dd_cloud_disagreement"]
    assert (dd_cloud.lower_altitude_ft, dd_cloud.upper_altitude_ft) == (1_000, 3_000)
    assert dd_cloud.metric_id == "cloud_coverage"
    assert dd_cloud.method_id == "dewpoint_depression"
    assert dd_cloud.severity == AdvisoryStatus.AMBER

    nwp_cloud = by_reason["nwp_cloud_disagreement"]
    assert (nwp_cloud.lower_altitude_ft, nwp_cloud.upper_altitude_ft) == (6_000, 8_000)
    assert nwp_cloud.metric_id == "cloud_coverage"
    assert nwp_cloud.method_id == "nwp"
    assert nwp_cloud.severity == AdvisoryStatus.AMBER


def test_dd_nwp_binary_regions_are_retiered_to_final_raw_status():
    ctx = _dd_nwp_context()

    below_threshold = DDvsNWPAgreementEvaluator.evaluate(
        ctx,
        {"amber_pct": 50, "red_pct": 80},
    ).per_model[0]
    assert below_threshold.status == AdvisoryStatus.GREEN
    assert below_threshold.evidence_regions
    assert {region.severity for region in below_threshold.evidence_regions} == {
        AdvisoryStatus.GREEN,
    }

    red_by_extent = DDvsNWPAgreementEvaluator.evaluate(
        ctx,
        {"amber_pct": 10, "red_pct": 30},
    ).per_model[0]
    assert red_by_extent.status == AdvisoryStatus.RED
    assert red_by_extent.evidence_regions
    assert {region.severity for region in red_by_extent.evidence_regions} == {
        AdvisoryStatus.RED,
    }


def test_dd_nwp_cloud_disagreement_evaluates_without_thermodynamic_indices():
    ctx = RouteContext(
        analyses=[
            _route_point(
                0,
                0.0,
                SoundingAnalysis(
                    indices=None,
                    dd_cloud_layers=[_cloud_layer(1_000, 3_000, source="dd")],
                    nwp_cloud_layers=[
                        _cloud_layer(6_000, 8_000, source="nwp_3d")
                    ],
                ),
            ),
            _route_point(
                1,
                100.0,
                SoundingAnalysis(
                    indices=None,
                    dd_cloud_layers=[_cloud_layer(1_000, 3_000, source="dd")],
                    nwp_cloud_layers=[
                        _cloud_layer(1_000, 3_000, source="nwp_3d")
                    ],
                ),
            ),
        ],
        cross_sections=[],
        elevation=None,
        models=["gfs"],
        cruise_altitude_ft=8_000,
        flight_ceiling_ft=18_000,
        total_distance_nm=100.0,
    )

    result = DDvsNWPAgreementEvaluator.evaluate(ctx, {}).per_model[0]

    assert result.status == AdvisoryStatus.AMBER
    assert result.data_state == "partial"
    assert result.affected_points == 1
    by_reason = {region.reason_code: region for region in result.evidence_regions}
    assert set(by_reason) == {
        "dd_cloud_disagreement",
        "nwp_cloud_disagreement",
    }
    assert by_reason["dd_cloud_disagreement"].method_id == "dewpoint_depression"
    assert by_reason["nwp_cloud_disagreement"].method_id == "nwp"


def test_dd_nwp_none_cloud_geometry_is_missing_but_explicit_empty_is_clear():
    ctx = RouteContext(
        analyses=[
            _route_point(
                0,
                0.0,
                SoundingAnalysis(
                    indices=ThermodynamicIndices(
                        freezing_level_ft=4_000,
                        nwp_freezing_level_ft=4_000,
                    ),
                    dd_cloud_layers=[_cloud_layer(1_000, 3_000, source="dd")],
                    nwp_cloud_layers=None,
                    nwp_cloud_diagnostics=NWPCloudDiagnostics(),
                ),
            ),
        ],
        cross_sections=[],
        elevation=None,
        models=["gfs"],
        cruise_altitude_ft=8_000,
        flight_ceiling_ft=18_000,
        total_distance_nm=50.0,
    )

    result = DDvsNWPAgreementEvaluator.evaluate(ctx, {}).per_model[0]

    assert result.data_state == "partial"
    assert result.status == AdvisoryStatus.UNAVAILABLE
    assert result.affected_points == 0
    assert result.evidence_regions == []

    assessed_clear = ctx.analyses[0].model_copy(
        update={
            "sounding": {
                "gfs": SoundingAnalysis(
                    indices=ThermodynamicIndices(
                        freezing_level_ft=4_000,
                        nwp_freezing_level_ft=4_000,
                    ),
                    dd_cloud_layers=[],
                    nwp_cloud_layers=[],
                    nwp_cloud_diagnostics=NWPCloudDiagnostics(),
                )
            }
        }
    )
    clear_ctx = RouteContext(
        analyses=[assessed_clear],
        cross_sections=[],
        elevation=None,
        models=["gfs"],
        cruise_altitude_ft=8_000,
        flight_ceiling_ft=18_000,
        total_distance_nm=50.0,
    )

    clear_result = DDvsNWPAgreementEvaluator.evaluate(clear_ctx, {}).per_model[0]

    assert clear_result.data_state == "complete"
    assert clear_result.status == AdvisoryStatus.GREEN


def test_llws_missing_route_analysis_returns_each_requested_model_unavailable():
    ctx = RouteContext(
        analyses=[],
        cross_sections=[],
        elevation=None,
        models=["gfs", "ecmwf"],
        cruise_altitude_ft=8_000,
        flight_ceiling_ft=18_000,
        total_distance_nm=100.0,
    )

    result = LLWSEvaluator.evaluate(ctx, {})

    assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE
    assert [model.model for model in result.per_model] == ["gfs", "ecmwf"]
    assert all(model.status == AdvisoryStatus.UNAVAILABLE for model in result.per_model)
    assert all(model.data_state == "unavailable" for model in result.per_model)


def test_llws_partial_route_preserves_available_departure_hazard():
    ctx = RouteContext(
        analyses=[
            _route_point(
                0,
                0.0,
                SoundingAnalysis(
                    indices=ThermodynamicIndices(bulk_shear_0_1km_kt=35.0),
                ),
            ),
            _route_point(
                1,
                100.0,
                SoundingAnalysis(indices=ThermodynamicIndices()),
            ),
        ],
        cross_sections=[],
        elevation=None,
        models=["gfs"],
        cruise_altitude_ft=8_000,
        flight_ceiling_ft=18_000,
        total_distance_nm=100.0,
    )

    result = LLWSEvaluator.evaluate(ctx, {}).per_model[0]

    assert result.status == AdvisoryStatus.RED
    assert result.data_state == "partial"
    assert result.total_points == 1
    assert result.affected_points == 1
