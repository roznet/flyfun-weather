"""Evidence geometry, provenance, and missing-data tests for convection."""

from dataclasses import replace

import pytest

from weatherbrief.analysis.advisories.convective import ConvectiveEvaluator
from weatherbrief.models import AdvisoryStatus, ConvectiveAssessment, ConvectiveRisk


_CONV_PARAMS = {
    "min_risk": 2,
    "affected_pct_amber": 20,
    "affected_pct_red": 50,
    "top_clearance_ft": 2000,
}


def _assessment(
    risk: ConvectiveRisk,
    *,
    base_ft: float | None = None,
    top_ft: float | None = None,
    method: str = "nwp",
) -> ConvectiveAssessment:
    return ConvectiveAssessment(
        risk_level=risk,
        base_ft=base_ft,
        top_ft=top_ft,
        method=method,
    )


def _with_assessments(
    ctx,
    active_by_model,
    *,
    thermo_by_model=None,
    selected_method: str = "nwp",
):
    thermo_by_model = thermo_by_model or {}
    analyses = []
    models = list(active_by_model)

    for rpa in ctx.analyses:
        soundings = {}
        for model in models:
            source = rpa.sounding.get(model, rpa.sounding["gfs"])
            active = active_by_model[model].get(rpa.point_index)
            thermo = thermo_by_model.get(model, {}).get(rpa.point_index)
            if thermo is None and active is not None and not active.method.startswith("nwp"):
                thermo = active
            nwp = (
                active
                if active is not None and active.method.startswith("nwp")
                else None
            )
            soundings[model] = source.model_copy(
                update={
                    "convective": active,
                    "convective_thermo": thermo,
                    "convective_nwp": nwp,
                }
            )
        analyses.append(rpa.model_copy(update={"sounding": soundings}))

    return replace(
        ctx,
        analyses=analyses,
        models=models,
        convective_method=selected_method,
    )


def test_dd_floor_emits_compound_provenance_with_thermo_geometry(clear_context):
    active = {
        index: _assessment(ConvectiveRisk.NONE)
        for index in range(len(clear_context.analyses))
    }
    thermo = {
        index: _assessment(ConvectiveRisk.NONE, method="thermo")
        for index in range(len(clear_context.analyses))
    }
    thermo[3] = _assessment(
        ConvectiveRisk.HIGH,
        base_ft=5000,
        top_ft=25000,
        method="thermo",
    )
    ctx = _with_assessments(
        clear_context,
        {"gfs": active},
        thermo_by_model={"gfs": thermo},
    )

    model = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS).per_model[0]
    floor_regions = [
        region
        for region in model.evidence_regions
        if region.reason_code == "convective_dd_floor"
    ]

    assert model.status == AdvisoryStatus.RED
    assert model.primary_method_id == "nwp_with_dd_floor"
    assert len(floor_regions) == 1
    assert floor_regions[0].method_id == "nwp_with_dd_floor"
    assert floor_regions[0].metric_id == "convective_risk"
    assert floor_regions[0].severity == AdvisoryStatus.RED
    assert (
        floor_regions[0].lower_altitude_ft,
        floor_regions[0].upper_altitude_ft,
    ) == (5000, 25000)


def test_disconnected_nwp_cells_remain_disconnected(clear_context):
    active = {
        index: _assessment(
            ConvectiveRisk.MODERATE if index in {1, 2, 4} else ConvectiveRisk.NONE,
            base_ft=5000,
            top_ft=25000,
        )
        for index in range(len(clear_context.analyses))
    }
    ctx = _with_assessments(clear_context, {"gfs": active})

    model = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS).per_model[0]

    assert [
        (region.start_point_index, region.end_point_index)
        for region in model.evidence_regions
    ] == [(1, 2), (4, 4)]
    assert all(
        region.reason_code == "convective_active"
        and region.metric_id == "nwp_convective_risk"
        and region.method_id == "nwp"
        for region in model.evidence_regions
    )
    assert model.affected_nm == 60.0
    assert model.affected_mod_nm == 60.0
    assert "60nm/200nm (30%)" in model.detail


def test_missing_active_assessments_are_partial_and_not_clear(clear_context):
    active = {
        index: _assessment(ConvectiveRisk.NONE)
        for index in {0, 1}
    }
    ctx = _with_assessments(clear_context, {"gfs": active})

    model = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS).per_model[0]

    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.UNAVAILABLE
    assert model.detail == "Partial data"
    assert model.total_points == 2


def test_aggregate_detail_is_owned_by_representative_model(clear_context):
    gfs = {
        index: _assessment(
            ConvectiveRisk.MODERATE if index in {1, 2, 4} else ConvectiveRisk.NONE,
            base_ft=5000,
            top_ft=25000,
        )
        for index in range(len(clear_context.analyses))
    }
    ecmwf = {
        index: _assessment(
            ConvectiveRisk.MODERATE if index in {0, 1, 2, 3} else ConvectiveRisk.NONE,
            base_ft=6000,
            top_ft=26000,
        )
        for index in range(len(clear_context.analyses))
    }
    ctx = _with_assessments(clear_context, {"gfs": gfs, "ecmwf": ecmwf})

    result = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)
    by_model = {model.model: model for model in result.per_model}

    assert by_model["gfs"].status == AdvisoryStatus.AMBER
    assert by_model["ecmwf"].status == AdvisoryStatus.AMBER
    assert by_model["gfs"].detail != by_model["ecmwf"].detail
    assert result.representative_model == "gfs"
    assert result.aggregate_detail == by_model["gfs"].detail


@pytest.mark.parametrize(
    ("active_method", "expected_method", "expected_metric"),
    [
        pytest.param("nwp_hybrid", "nwp", "nwp_convective_risk", id="native-nwp"),
        pytest.param("thermo", "thermo", "convective_risk", id="thermo"),
        pytest.param(
            "nwp_cape_fallback",
            "thermo",
            "convective_risk",
            id="cape-fallback",
        ),
    ],
)
def test_active_track_uses_exact_method_and_metric_ids(
    clear_context,
    active_method,
    expected_method,
    expected_metric,
):
    active = {
        index: _assessment(
            ConvectiveRisk.MODERATE if index == 1 else ConvectiveRisk.NONE,
            base_ft=5000,
            top_ft=25000,
            method=active_method,
        )
        for index in range(len(clear_context.analyses))
    }
    ctx = _with_assessments(
        clear_context,
        {"gfs": active},
        selected_method="thermo" if active_method == "thermo" else "nwp",
    )

    model = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS).per_model[0]

    assert model.primary_method_id == expected_method
    assert {region.reason_code for region in model.evidence_regions} == {
        "convective_active"
    }
    assert {region.method_id for region in model.evidence_regions} == {
        expected_method
    }
    assert {region.metric_id for region in model.evidence_regions} == {
        expected_metric
    }


def test_cape_fallback_floor_remains_thermo_provenance(clear_context):
    active = {
        index: _assessment(
            ConvectiveRisk.LOW if index == 1 else ConvectiveRisk.NONE,
            base_ft=5000,
            top_ft=25000,
            method="nwp_cape_fallback",
        )
        for index in range(len(clear_context.analyses))
    }
    thermo = {
        index: _assessment(
            ConvectiveRisk.HIGH if index == 1 else ConvectiveRisk.NONE,
            base_ft=5000,
            top_ft=25000,
            method="thermo",
        )
        for index in range(len(clear_context.analyses))
    }
    ctx = _with_assessments(
        clear_context,
        {"gfs": active},
        thermo_by_model={"gfs": thermo},
    )

    model = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS).per_model[0]

    assert model.status == AdvisoryStatus.RED
    assert model.primary_method_id == "thermo"
    assert {region.reason_code for region in model.evidence_regions} == {
        "convective_dd_floor"
    }
    assert {region.method_id for region in model.evidence_regions} == {"thermo"}
    assert {region.metric_id for region in model.evidence_regions} == {
        "convective_risk"
    }


def test_evidence_bounds_require_a_complete_base_top_pair(clear_context):
    active = {
        index: _assessment(ConvectiveRisk.NONE)
        for index in range(len(clear_context.analyses))
    }
    active[1] = _assessment(ConvectiveRisk.MODERATE, base_ft=5000)
    active[2] = _assessment(ConvectiveRisk.MODERATE, top_ft=25000)
    active[3] = _assessment(
        ConvectiveRisk.MODERATE,
        base_ft=5000,
        top_ft=25000,
    )
    ctx = _with_assessments(clear_context, {"gfs": active})

    regions = ConvectiveEvaluator.evaluate(
        ctx,
        _CONV_PARAMS,
    ).per_model[0].evidence_regions

    assert [
        (
            region.start_point_index,
            region.end_point_index,
            region.lower_altitude_ft,
            region.upper_altitude_ft,
        )
        for region in regions
    ] == [
        (1, 2, None, None),
        (3, 3, 5000, 25000),
    ]


def test_primary_and_moderate_extents_use_midpoint_route_cells(clear_context):
    route = replace(
        clear_context,
        analyses=[
            rpa.model_copy(update={"distance_from_origin_nm": distance})
            for rpa, distance in zip(
                clear_context.analyses[:4],
                [0.0, 10.0, 50.0, 100.0],
            )
        ],
        total_distance_nm=100.0,
    )
    active = {
        0: _assessment(ConvectiveRisk.NONE),
        1: _assessment(ConvectiveRisk.LOW, base_ft=5000, top_ft=25000),
        2: _assessment(ConvectiveRisk.MODERATE, base_ft=5000, top_ft=25000),
        3: _assessment(ConvectiveRisk.NONE),
    }
    ctx = _with_assessments(route, {"gfs": active})

    model = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS).per_model[0]

    assert model.affected_points == 2
    assert model.affected_nm == 70.0
    assert model.affected_mod_points == 1
    assert model.affected_mod_nm == 45.0
    assert "45nm/100nm (25%)" in model.detail


def test_below_cruise_dd_floor_falls_back_to_selected_nwp_method(clear_context):
    active = {
        index: _assessment(ConvectiveRisk.NONE)
        for index in range(len(clear_context.analyses))
    }
    thermo = {
        index: _assessment(ConvectiveRisk.NONE, method="thermo")
        for index in range(len(clear_context.analyses))
    }
    thermo[3] = _assessment(
        ConvectiveRisk.HIGH,
        base_ft=1000,
        top_ft=4000,
        method="thermo",
    )
    ctx = _with_assessments(
        clear_context,
        {"gfs": active},
        thermo_by_model={"gfs": thermo},
    )

    model = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS).per_model[0]

    assert model.status == AdvisoryStatus.GREEN
    assert model.evidence_regions == []
    assert model.primary_method_id == "nwp"


def test_partial_below_cruise_floor_never_claims_compound_primary(clear_context):
    active = {3: _assessment(ConvectiveRisk.NONE)}
    thermo = {
        3: _assessment(
            ConvectiveRisk.HIGH,
            base_ft=1000,
            top_ft=4000,
            method="thermo",
        )
    }
    ctx = _with_assessments(
        clear_context,
        {"gfs": active},
        thermo_by_model={"gfs": thermo},
    )

    model = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS).per_model[0]

    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.UNAVAILABLE
    assert model.evidence_regions == []
    assert model.primary_method_id == "nwp"


def test_floor_filtered_by_min_risk_falls_back_to_selected_nwp_method(clear_context):
    active = {
        index: _assessment(ConvectiveRisk.NONE)
        for index in range(len(clear_context.analyses))
    }
    thermo = {
        index: _assessment(ConvectiveRisk.NONE, method="thermo")
        for index in range(len(clear_context.analyses))
    }
    thermo[3] = _assessment(
        ConvectiveRisk.MODERATE,
        base_ft=5000,
        top_ft=25000,
        method="thermo",
    )
    ctx = _with_assessments(
        clear_context,
        {"gfs": active},
        thermo_by_model={"gfs": thermo},
    )
    params = {**_CONV_PARAMS, "min_risk": 4}

    model = ConvectiveEvaluator.evaluate(ctx, params).per_model[0]

    assert model.status == AdvisoryStatus.GREEN
    assert model.evidence_regions == []
    assert model.primary_method_id == "nwp"


def test_dd_floor_primary_when_floor_contributions_raise_route_grade(clear_context):
    active = {
        index: _assessment(
            ConvectiveRisk.MODERATE if index == 0 else ConvectiveRisk.NONE,
            base_ft=5000,
            top_ft=25000,
        )
        for index in range(len(clear_context.analyses))
    }
    thermo = {
        index: _assessment(
            ConvectiveRisk.MODERATE if 1 <= index <= 5 else ConvectiveRisk.NONE,
            base_ft=5000,
            top_ft=25000,
            method="thermo",
        )
        for index in range(len(clear_context.analyses))
    }
    ctx = _with_assessments(
        clear_context,
        {"gfs": active},
        thermo_by_model={"gfs": thermo},
    )

    model = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS).per_model[0]

    assert model.status == AdvisoryStatus.RED
    assert model.primary_method_id == "nwp_with_dd_floor"

    reversed_ctx = replace(ctx, analyses=list(reversed(ctx.analyses)))
    reversed_model = ConvectiveEvaluator.evaluate(
        reversed_ctx,
        _CONV_PARAMS,
    ).per_model[0]
    assert reversed_model.status == AdvisoryStatus.RED
    assert reversed_model.primary_method_id == "nwp_with_dd_floor"
    assert reversed_model.evidence_regions == model.evidence_regions


def test_nwp_primary_when_nwp_already_controls_route_grade(clear_context):
    active = {
        index: _assessment(
            ConvectiveRisk.MODERATE if index <= 5 else ConvectiveRisk.NONE,
            base_ft=5000,
            top_ft=25000,
        )
        for index in range(len(clear_context.analyses))
    }
    thermo = {
        index: _assessment(
            ConvectiveRisk.MODERATE if index == 6 else ConvectiveRisk.NONE,
            base_ft=5000,
            top_ft=25000,
            method="thermo",
        )
        for index in range(len(clear_context.analyses))
    }
    ctx = _with_assessments(
        clear_context,
        {"gfs": active},
        thermo_by_model={"gfs": thermo},
    )

    model = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS).per_model[0]

    assert model.status == AdvisoryStatus.RED
    assert model.primary_method_id == "nwp"


def test_nwp_primary_when_floor_only_raises_risk_not_route_grade(clear_context):
    active = {
        index: _assessment(
            ConvectiveRisk.MODERATE if index <= 5 else ConvectiveRisk.NONE,
            base_ft=5000,
            top_ft=25000,
        )
        for index in range(len(clear_context.analyses))
    }
    thermo = {
        index: _assessment(
            ConvectiveRisk.HIGH if index <= 5 else ConvectiveRisk.NONE,
            base_ft=5000,
            top_ft=25000,
            method="thermo",
        )
        for index in range(len(clear_context.analyses))
    }
    ctx = _with_assessments(
        clear_context,
        {"gfs": active},
        thermo_by_model={"gfs": thermo},
    )

    model = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS).per_model[0]

    assert model.status == AdvisoryStatus.RED
    assert model.primary_method_id == "nwp"
    assert {region.method_id for region in model.evidence_regions} == {
        "nwp_with_dd_floor"
    }


@pytest.mark.parametrize(
    ("base_ft", "top_ft"),
    [
        pytest.param(25000.0, 20000.0, id="reversed"),
        pytest.param(float("nan"), 25000.0, id="nan-base"),
        pytest.param(5000.0, float("nan"), id="nan-top"),
        pytest.param(float("inf"), 25000.0, id="positive-infinite-base"),
        pytest.param(float("-inf"), 25000.0, id="negative-infinite-base"),
        pytest.param(5000.0, float("inf"), id="positive-infinite-top"),
        pytest.param(5000.0, float("-inf"), id="negative-infinite-top"),
    ],
)
def test_invalid_altitude_pairs_preserve_hazard_without_geometry(
    clear_context,
    base_ft,
    top_ft,
):
    active = {
        index: _assessment(
            ConvectiveRisk.MODERATE,
            base_ft=base_ft,
            top_ft=top_ft,
        )
        for index in range(len(clear_context.analyses))
    }
    ctx = _with_assessments(clear_context, {"gfs": active})

    model = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS).per_model[0]

    assert model.status == AdvisoryStatus.RED
    assert len(model.evidence_regions) == 1
    assert model.evidence_regions[0].lower_altitude_ft is None
    assert model.evidence_regions[0].upper_altitude_ft is None


def test_sparse_partial_hazard_keeps_evaluated_point_denominator(clear_context):
    active = {
        0: _assessment(
            ConvectiveRisk.MODERATE,
            base_ft=5000,
            top_ft=25000,
        )
    }
    ctx = _with_assessments(clear_context, {"gfs": active})

    model = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS).per_model[0]

    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.RED
    assert model.total_points == 1
    assert model.affected_points == 1
    assert model.affected_pct == 100.0
    assert model.affected_nm == 10.0
    assert "10nm/200nm (100%)" in model.detail
