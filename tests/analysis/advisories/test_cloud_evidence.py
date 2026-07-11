from dataclasses import replace

import pytest

from weatherbrief.analysis.advisories.cloud_top import CloudTopEvaluator
from weatherbrief.analysis.advisories.vmc_cruise import VMCCruiseEvaluator
from weatherbrief.models import AdvisoryStatus, CloudCoverage, EnhancedCloudLayer


def _with_clouds(ctx, layers_by_index):
    analyses = []
    for rpa in ctx.analyses:
        sounding = rpa.sounding["gfs"].model_copy(
            update={
                "cloud_layers": layers_by_index.get(rpa.point_index, []),
                "cloud_method_effective": "nwp",
            }
        )
        analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
    return replace(ctx, analyses=analyses, models=["gfs"], cloud_method="square_nwp")


def _with_methods(ctx, methods_by_index):
    analyses = []
    for rpa in ctx.analyses:
        sounding = rpa.sounding["gfs"].model_copy(
            update={
                "cloud_method_effective": methods_by_index.get(
                    rpa.point_index,
                    "unknown",
                )
            }
        )
        analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
    return replace(ctx, analyses=analyses)


def test_cloud_top_emits_disconnected_model_specific_regions(clear_context):
    hazard = EnhancedCloudLayer(
        base_ft=5000,
        top_ft=15000,
        coverage=CloudCoverage.OVC,
        source="grib",
    )
    ctx = _with_clouds(clear_context, {1: [hazard], 2: [hazard], 4: [hazard]})
    ctx = replace(ctx, flight_ceiling_ft=12000)
    result = CloudTopEvaluator.evaluate(ctx, {"margin_ft": 1000, "pct_amber": 5})
    model = result.per_model[0]
    assert model.data_state == "complete"
    assert model.primary_method_id == "nwp"
    assert [(r.start_point_index, r.end_point_index) for r in model.evidence_regions] == [
        (1, 2),
        (4, 4),
    ]
    assert all(r.reason_code == "cloud_top_exceeds_ceiling" for r in model.evidence_regions)
    assert model.affected_nm > 0


def test_vmc_regions_split_when_vertical_bounds_change(clear_context):
    first = EnhancedCloudLayer(
        base_ft=5000,
        top_ft=9000,
        coverage=CloudCoverage.BKN,
        source="grib",
    )
    second = EnhancedCloudLayer(
        base_ft=4000,
        top_ft=10000,
        coverage=CloudCoverage.BKN,
        source="grib",
    )
    ctx = _with_clouds(clear_context, {1: [first], 2: [second]})
    result = VMCCruiseEvaluator.evaluate(
        ctx,
        {"bkn_pct_amber": 5, "ovc_pct_red": 50},
    )
    regions = result.per_model[0].evidence_regions
    assert [(r.start_point_index, r.end_point_index) for r in regions] == [(1, 1), (2, 2)]
    assert [(r.lower_altitude_ft, r.upper_altitude_ft) for r in regions] == [
        (5000, 9000),
        (4000, 10000),
    ]


def test_vmc_red_detail_uses_ovc_only_midpoint_extent(clear_context):
    ovc = EnhancedCloudLayer(
        base_ft=5000,
        top_ft=9000,
        coverage=CloudCoverage.OVC,
        source="grib",
    )
    bkn = EnhancedCloudLayer(
        base_ft=5000,
        top_ft=9000,
        coverage=CloudCoverage.BKN,
        source="grib",
    )
    layers = {index: [ovc if index < 6 else bkn] for index in range(10)}
    result = VMCCruiseEvaluator.evaluate(
        _with_clouds(clear_context, layers),
        {"bkn_pct_amber": 25, "ovc_pct_red": 50},
    )
    model = result.per_model[0]
    assert model.status == AdvisoryStatus.RED
    assert model.affected_points == 10
    assert model.affected_mod_points == 6
    assert model.detail == "OVC at cruise over 110nm/200nm (60%)"


def test_synthesized_nwp_clouds_keep_explicit_compound_provenance(clear_context):
    hazard = EnhancedCloudLayer(
        base_ft=5000,
        top_ft=15000,
        coverage=CloudCoverage.OVC,
        source="synthesized",
    )
    ctx = _with_clouds(clear_context, {1: [hazard], 2: [hazard]})
    analyses = []
    for rpa in ctx.analyses:
        sounding = rpa.sounding["gfs"].model_copy(
            update={"cloud_method_effective": "nwp_synthesized"}
        )
        analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
    result = CloudTopEvaluator.evaluate(
        replace(ctx, analyses=analyses, flight_ceiling_ft=12000),
        {"margin_ft": 1000, "pct_amber": 5},
    )
    assert result.per_model[0].primary_method_id == "nwp_synthesized"


def test_cloud_top_partial_clear_becomes_unavailable(clear_context):
    ctx = _with_clouds(clear_context, {})
    analyses = [
        rpa if rpa.point_index != 3 else rpa.model_copy(update={"sounding": {}})
        for rpa in ctx.analyses
    ]
    model = CloudTopEvaluator.evaluate(
        replace(ctx, analyses=analyses, locale="en"),
        {"margin_ft": 1000, "pct_amber": 25},
    ).per_model[0]

    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.UNAVAILABLE
    assert model.detail == "Partial data"


def test_cloud_partial_clear_becomes_unavailable(clear_context):
    ctx = _with_clouds(clear_context, {})
    analyses = [
        rpa if rpa.point_index != 3 else rpa.model_copy(update={"sounding": {}})
        for rpa in ctx.analyses
    ]
    result = VMCCruiseEvaluator.evaluate(
        replace(ctx, analyses=analyses),
        {"bkn_pct_amber": 25, "ovc_pct_red": 50},
    )
    model = result.per_model[0]
    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.UNAVAILABLE
    assert model.detail == "Partial data"


def test_cloud_partial_hazard_preserves_amber(clear_context):
    hazard = EnhancedCloudLayer(
        base_ft=5000,
        top_ft=9000,
        coverage=CloudCoverage.BKN,
        source="grib",
    )
    ctx = _with_clouds(clear_context, {0: [hazard], 1: [hazard]})
    analyses = [
        rpa if rpa.point_index != 3 else rpa.model_copy(update={"sounding": {}})
        for rpa in ctx.analyses
    ]
    result = VMCCruiseEvaluator.evaluate(
        replace(ctx, analyses=analyses),
        {"bkn_pct_amber": 5, "ovc_pct_red": 50},
    )
    model = result.per_model[0]
    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.AMBER


@pytest.mark.parametrize(
    ("evaluator", "params"),
    [
        pytest.param(
            CloudTopEvaluator,
            {"margin_ft": 1000, "pct_amber": 5},
            id="cloud_top",
        ),
        pytest.param(
            VMCCruiseEvaluator,
            {"bkn_pct_amber": 5, "ovc_pct_red": 50},
            id="vmc_cruise",
        ),
    ],
)
def test_primary_method_uses_canonical_controlling_sample(
    clear_context,
    evaluator,
    params,
):
    hazard = EnhancedCloudLayer(
        base_ft=5000,
        top_ft=15000,
        coverage=CloudCoverage.OVC,
        source="grib",
    )
    ctx = _with_clouds(clear_context, {0: [hazard], 1: [hazard]})
    ctx = _with_methods(ctx, {0: "nwp", 1: "nwp_synthesized"})
    ctx = replace(ctx, flight_ceiling_ft=12000)

    forward = evaluator.evaluate(ctx, params).per_model[0]
    reversed_result = evaluator.evaluate(
        replace(ctx, analyses=list(reversed(ctx.analyses))),
        params,
    ).per_model[0]

    assert forward.primary_method_id == "nwp"
    assert reversed_result.primary_method_id == "nwp"


@pytest.mark.parametrize(
    ("evaluator", "params"),
    [
        pytest.param(
            CloudTopEvaluator,
            {"margin_ft": 1000, "pct_amber": 25},
            id="cloud_top",
        ),
        pytest.param(
            VMCCruiseEvaluator,
            {"bkn_pct_amber": 25, "ovc_pct_red": 50},
            id="vmc_cruise",
        ),
    ],
)
def test_primary_method_fallback_uses_canonical_evaluated_point(
    clear_context,
    evaluator,
    params,
):
    ctx = _with_methods(
        _with_clouds(clear_context, {}),
        {0: "nwp", 1: "nwp_synthesized"},
    )

    forward = evaluator.evaluate(ctx, params).per_model[0]
    reversed_result = evaluator.evaluate(
        replace(ctx, analyses=list(reversed(ctx.analyses))),
        params,
    ).per_model[0]

    assert forward.primary_method_id == "nwp"
    assert reversed_result.primary_method_id == "nwp"


@pytest.mark.parametrize(
    ("evaluator", "params"),
    [
        pytest.param(
            CloudTopEvaluator,
            {"margin_ft": 1000, "pct_amber": 25},
            id="cloud_top",
        ),
        pytest.param(
            VMCCruiseEvaluator,
            {"bkn_pct_amber": 25, "ovc_pct_red": 50},
            id="vmc_cruise",
        ),
    ],
)
def test_all_missing_cloud_model_uses_localized_no_data(
    clear_context,
    evaluator,
    params,
):
    analyses = [rpa.model_copy(update={"sounding": {}}) for rpa in clear_context.analyses]
    ctx = replace(clear_context, analyses=analyses, models=["gfs"], locale="fr")
    model = evaluator.evaluate(ctx, params).per_model[0]
    assert model.data_state == "unavailable"
    assert model.status == AdvisoryStatus.UNAVAILABLE
    assert model.detail == "Pas de données"


def test_cloud_top_below_threshold_regions_are_green(clear_context):
    hazard = EnhancedCloudLayer(
        base_ft=5000,
        top_ft=15000,
        coverage=CloudCoverage.OVC,
        source="grib",
    )
    ctx = replace(
        _with_clouds(clear_context, {1: [hazard]}),
        flight_ceiling_ft=12000,
    )
    model = CloudTopEvaluator.evaluate(
        ctx,
        {"margin_ft": 1000, "pct_amber": 25},
    ).per_model[0]
    assert model.status == AdvisoryStatus.GREEN
    assert {region.severity for region in model.evidence_regions} == {
        AdvisoryStatus.GREEN
    }


def test_cloud_top_configured_amber_boundary_is_inclusive(clear_context):
    hazard = EnhancedCloudLayer(
        base_ft=5000,
        top_ft=15000,
        coverage=CloudCoverage.OVC,
        source="grib",
    )
    ctx = replace(
        _with_clouds(clear_context, {0: [hazard], 1: [hazard]}),
        flight_ceiling_ft=12000,
    )
    model = CloudTopEvaluator.evaluate(
        ctx,
        {"margin_ft": 1000, "pct_amber": 20},
    ).per_model[0]
    assert model.status == AdvisoryStatus.AMBER


def test_cloud_top_red_boundary_is_inclusive(clear_context):
    hazard = EnhancedCloudLayer(
        base_ft=5000,
        top_ft=15000,
        coverage=CloudCoverage.OVC,
        source="grib",
    )
    ctx = replace(
        _with_clouds(clear_context, {index: [hazard] for index in range(6)}),
        flight_ceiling_ft=12000,
    )
    model = CloudTopEvaluator.evaluate(
        ctx,
        {"margin_ft": 1000, "pct_amber": 25},
    ).per_model[0]
    assert model.status == AdvisoryStatus.RED


def test_vmc_bkn_amber_boundary_is_inclusive(clear_context):
    bkn = EnhancedCloudLayer(
        base_ft=5000,
        top_ft=9000,
        coverage=CloudCoverage.BKN,
        source="grib",
    )
    model = VMCCruiseEvaluator.evaluate(
        _with_clouds(clear_context, {0: [bkn], 1: [bkn]}),
        {"bkn_pct_amber": 20, "ovc_pct_red": 50},
    ).per_model[0]
    assert model.status == AdvisoryStatus.AMBER


def test_vmc_ovc_red_boundary_is_inclusive(clear_context):
    ovc = EnhancedCloudLayer(
        base_ft=5000,
        top_ft=9000,
        coverage=CloudCoverage.OVC,
        source="grib",
    )
    model = VMCCruiseEvaluator.evaluate(
        _with_clouds(
            clear_context,
            {index: [ovc] for index in range(5)},
        ),
        {"bkn_pct_amber": 25, "ovc_pct_red": 50},
    ).per_model[0]
    assert model.status == AdvisoryStatus.RED
