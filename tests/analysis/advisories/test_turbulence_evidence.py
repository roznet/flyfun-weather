"""Evidence and missing-data tests for the turbulence advisory."""

from dataclasses import replace

from weatherbrief.analysis.advisories.turbulence import TurbulenceEvaluator
from weatherbrief.models import AdvisoryStatus, CATRiskLevel


def test_turbulence_missing_vertical_motion_is_unavailable(clear_context):
    analyses = []
    for rpa in clear_context.analyses:
        sounding = rpa.sounding["gfs"].model_copy(update={"vertical_motion": None})
        analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
    ctx = replace(clear_context, analyses=analyses, models=["gfs"])
    result = TurbulenceEvaluator.evaluate(
        ctx, {"route_pct_amber": 20, "strong_w_fpm": 200},
    )
    model = result.per_model[0]
    assert model.status == AdvisoryStatus.UNAVAILABLE
    assert model.data_state == "unavailable"


def test_turbulence_partial_severe_evidence_remains_red(turbulent_context):
    analyses = []
    for rpa in turbulent_context.analyses:
        if rpa.point_index == 0:
            sounding = rpa.sounding["gfs"].model_copy(update={"vertical_motion": None})
            analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
        else:
            sounding = rpa.sounding["gfs"]
            vertical_motion = sounding.vertical_motion
            severe_layers = [
                layer.model_copy(update={"risk": CATRiskLevel.SEVERE})
                for layer in vertical_motion.cat_risk_layers
            ]
            analyses.append(
                rpa.model_copy(
                    update={
                        "sounding": {
                            "gfs": sounding.model_copy(
                                update={
                                    "vertical_motion": vertical_motion.model_copy(
                                        update={"cat_risk_layers": severe_layers}
                                    )
                                }
                            )
                        }
                    }
                )
            )
    ctx = replace(turbulent_context, analyses=analyses, models=["gfs"])
    result = TurbulenceEvaluator.evaluate(
        ctx, {"route_pct_amber": 20, "strong_w_fpm": 200},
    )
    assert result.per_model[0].data_state == "partial"
    assert result.per_model[0].status == AdvisoryStatus.RED


def test_turbulence_keeps_cat_bounds_and_motion_level(turbulent_context):
    result = TurbulenceEvaluator.evaluate(
        turbulent_context, {"route_pct_amber": 20, "strong_w_fpm": 200},
    )
    regions = result.per_model[0].evidence_regions
    cat = [r for r in regions if r.reason_code == "cat_at_cruise"]
    motion = [
        r
        for r in regions
        if r.reason_code == "strong_vertical_motion_near_cruise"
    ]
    assert [(r.lower_altitude_ft, r.upper_altitude_ft) for r in cat] == [
        (7000, 10000),
    ]
    assert [(r.lower_altitude_ft, r.upper_altitude_ft) for r in motion] == [
        (8000, 8000),
    ]


def test_turbulence_regions_do_not_bridge_missing_assessments(turbulent_context):
    analyses = []
    for rpa in turbulent_context.analyses:
        sounding = rpa.sounding["gfs"]
        if rpa.point_index in {2, 3}:
            sounding = sounding.model_copy(update={"vertical_motion": None})
        analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
    ctx = replace(turbulent_context, analyses=analyses)
    result = TurbulenceEvaluator.evaluate(
        ctx, {"route_pct_amber": 20, "strong_w_fpm": 200},
    )
    cat = [
        r
        for r in result.per_model[0].evidence_regions
        if r.reason_code == "cat_at_cruise"
    ]
    assert [(r.start_point_index, r.end_point_index) for r in cat] == [
        (0, 1),
        (4, 9),
    ]


def test_turbulence_evidence_uses_stable_ids_and_compound_provenance(
    turbulent_context,
):
    result = TurbulenceEvaluator.evaluate(
        turbulent_context, {"route_pct_amber": 20, "strong_w_fpm": 200},
    )
    model = result.per_model[0]
    by_reason = {region.reason_code: region for region in model.evidence_regions}
    assert by_reason["cat_at_cruise"].metric_id == "cat_risk"
    assert by_reason["cat_at_cruise"].method_id == "richardson_cat"
    assert by_reason["strong_vertical_motion_near_cruise"].metric_id is None
    assert (
        by_reason["strong_vertical_motion_near_cruise"].method_id
        == "vertical_motion"
    )
    assert model.primary_method_id == "cat_with_vertical_motion"


def test_turbulence_vertical_motion_is_primary_when_it_is_the_sole_trigger(
    turbulent_context,
):
    analyses = []
    for rpa in turbulent_context.analyses:
        sounding = rpa.sounding["gfs"]
        vertical_motion = sounding.vertical_motion.model_copy(
            update={"cat_risk_layers": []}
        )
        analyses.append(
            rpa.model_copy(
                update={
                    "sounding": {
                        "gfs": sounding.model_copy(
                            update={"vertical_motion": vertical_motion}
                        )
                    }
                }
            )
        )
    result = TurbulenceEvaluator.evaluate(
        replace(turbulent_context, analyses=analyses),
        {"route_pct_amber": 20, "strong_w_fpm": 200},
    )
    assert result.per_model[0].primary_method_id == "vertical_motion"


def test_turbulence_severe_cat_controls_primary_method(turbulent_context):
    analyses = []
    for rpa in turbulent_context.analyses:
        sounding = rpa.sounding["gfs"]
        vertical_motion = sounding.vertical_motion
        severe_layers = [
            layer.model_copy(update={"risk": CATRiskLevel.SEVERE})
            for layer in vertical_motion.cat_risk_layers
        ]
        analyses.append(
            rpa.model_copy(
                update={
                    "sounding": {
                        "gfs": sounding.model_copy(
                            update={
                                "vertical_motion": vertical_motion.model_copy(
                                    update={"cat_risk_layers": severe_layers}
                                )
                            }
                        )
                    }
                }
            )
        )
    result = TurbulenceEvaluator.evaluate(
        replace(turbulent_context, analyses=analyses),
        {"route_pct_amber": 20, "strong_w_fpm": 200},
    )
    model = result.per_model[0]
    assert model.status == AdvisoryStatus.RED
    assert model.primary_method_id == "richardson_cat"


def test_turbulence_severe_detail_uses_only_severe_cat_extent(turbulent_context):
    analyses = []
    for rpa in turbulent_context.analyses:
        sounding = rpa.sounding["gfs"]
        vertical_motion = sounding.vertical_motion
        if rpa.point_index == 0:
            cat_layers = [
                layer.model_copy(update={"risk": CATRiskLevel.SEVERE})
                for layer in vertical_motion.cat_risk_layers
            ]
            max_w_fpm = 0
        else:
            cat_layers = []
            max_w_fpm = 300
        updated_motion = vertical_motion.model_copy(
            update={
                "cat_risk_layers": cat_layers,
                "max_w_fpm": max_w_fpm,
            }
        )
        analyses.append(
            rpa.model_copy(
                update={
                    "sounding": {
                        "gfs": sounding.model_copy(
                            update={"vertical_motion": updated_motion}
                        )
                    }
                }
            )
        )

    result = TurbulenceEvaluator.evaluate(
        replace(turbulent_context, analyses=analyses),
        {"route_pct_amber": 20, "strong_w_fpm": 200},
    )

    assert result.per_model[0].detail == "Severe CAT over 10nm/200nm (10%)"


def test_turbulence_combined_detail_names_both_contributors(turbulent_context):
    result = TurbulenceEvaluator.evaluate(
        turbulent_context,
        {"route_pct_amber": 20, "strong_w_fpm": 200},
    )

    assert "MODERATE CAT + strong vertical motion" in result.per_model[0].detail
