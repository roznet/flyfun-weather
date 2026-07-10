from dataclasses import replace

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
