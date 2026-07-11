"""Evidence geometry and provenance tests for icing advisories."""

from dataclasses import replace

from weatherbrief.analysis.advisories.fiki_icing import FIKIIcingEvaluator
from weatherbrief.analysis.advisories.icing_escape import IcingEscapeEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    IcingRisk,
    IcingType,
    IcingZone,
    SfipZone,
)
from weatherbrief.tasks.advise import _resolve_analyses


_ICING_ESCAPE_PARAMS = {
    "terrain_margin_ft": 1000,
    "tight_margin_ft": 2000,
    "icing_altitude_buffer_ft": 2000,
    "icing_coverage_pct_amber": 5,
    "no_escape_pct_red": 15,
}


def test_icing_escape_regions_follow_actual_zones_and_route_cells(icing_context):
    sfip_zone = SfipZone(
        base_ft=6500,
        top_ft=8500,
        risk=IcingRisk.MODERATE,
        icing_type=IcingType.MIXED,
        mean_sfip_100=45,
        variant="full",
    )
    analyses = []
    for rpa in icing_context.analyses:
        sounding = rpa.sounding["gfs"].model_copy(
            update={"sfip_zones": [sfip_zone]}
        )
        analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
    resolved = _resolve_analyses(analyses, "sfip_nwp", None)
    ctx = replace(
        icing_context,
        analyses=resolved,
        models=["gfs"],
        icing_method="sfip_nwp",
    )

    result = IcingEscapeEvaluator.evaluate(
        ctx,
        _ICING_ESCAPE_PARAMS,
    )
    model = result.per_model[0]
    assert model.primary_method_id == "sfip"
    assert model.data_state == "complete"
    assert model.evidence_regions
    assert all(region.metric_id == "sfip_risk" for region in model.evidence_regions)
    exposure = [
        region
        for region in model.evidence_regions
        if region.reason_code == "icing_exposure"
    ]
    assert {
        (region.lower_altitude_ft, region.upper_altitude_ft)
        for region in exposure
    } == {(6500, 8500)}
    assert model.affected_nm <= model.total_nm


def test_fiki_single_point_sld_is_red_and_not_diluted(fiki_sld_context):
    result = FIKIIcingEvaluator.evaluate(fiki_sld_context, {})
    model = result.per_model[0]
    sld = [
        region
        for region in model.evidence_regions
        if region.reason_code
        in {
            "fiki_departure_transit",
            "fiki_arrival_transit",
        }
    ]
    assert model.status == AdvisoryStatus.RED
    assert model.primary_method_id == "ogimet_dd"
    assert sld
    assert any(region.severity == AdvisoryStatus.RED for region in sld)
    assert all(region.metric_id == "icing_risk" for region in sld)
    assert any(
        (region.lower_altitude_ft, region.upper_altitude_ft) == (3000, 7000)
        for region in sld
    )


def test_fiki_sld_does_not_raise_unrelated_transit_zone_severity(clear_context):
    sld_zone = IcingZone(
        base_ft=2000,
        top_ft=3000,
        risk=IcingRisk.LIGHT,
        icing_type=IcingType.CLEAR,
        sld_risk=True,
    )
    ordinary_zone = IcingZone(
        base_ft=5000,
        top_ft=5500,
        risk=IcingRisk.LIGHT,
        icing_type=IcingType.RIME,
    )
    analyses = []
    for rpa in clear_context.analyses:
        sounding = rpa.sounding["gfs"].model_copy(
            update={
                "icing_zones": (
                    [sld_zone, ordinary_zone]
                    if rpa.point_index in {0, 9}
                    else []
                )
            }
        )
        analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
    ctx = replace(clear_context, analyses=analyses, models=["gfs"])

    model = FIKIIcingEvaluator.evaluate(ctx, {}).per_model[0]
    transit = [
        region
        for region in model.evidence_regions
        if region.reason_code
        in {"fiki_departure_transit", "fiki_arrival_transit"}
    ]
    sld_regions = [
        region
        for region in transit
        if (region.lower_altitude_ft, region.upper_altitude_ft) == (2000, 3000)
    ]
    ordinary_regions = [
        region
        for region in transit
        if (region.lower_altitude_ft, region.upper_altitude_ft) == (5000, 5500)
    ]

    assert model.status == AdvisoryStatus.RED
    assert {region.reason_code for region in sld_regions} == {
        "fiki_departure_transit",
        "fiki_arrival_transit",
    }
    assert all(region.severity == AdvisoryStatus.RED for region in sld_regions)
    assert {region.reason_code for region in ordinary_regions} == {
        "fiki_departure_transit",
        "fiki_arrival_transit",
    }
    assert all(
        region.severity == AdvisoryStatus.GREEN for region in ordinary_regions
    )


def test_fiki_ogimet_nwp_missing_native_cloud_geometry_is_unavailable(clear_context):
    ctx = replace(clear_context, models=["gfs"], icing_method="ogimet_nwp")
    result = FIKIIcingEvaluator.evaluate(ctx, {})
    model = result.per_model[0]
    assert model.status == AdvisoryStatus.UNAVAILABLE
    assert model.data_state == "unavailable"


def test_ogimet_nwp_without_native_cloud_geometry_is_unavailable(clear_context):
    ctx = replace(clear_context, models=["gfs"], icing_method="ogimet_nwp")
    assert all(
        rpa.sounding["gfs"].nwp_cloud_layers is None for rpa in ctx.analyses
    )
    result = IcingEscapeEvaluator.evaluate(ctx, _ICING_ESCAPE_PARAMS)
    model = result.per_model[0]
    assert model.status == AdvisoryStatus.UNAVAILABLE
    assert model.data_state == "unavailable"


def test_ogimet_nwp_available_empty_geometry_is_assessed_clear(clear_context):
    analyses = []
    for rpa in clear_context.analyses:
        sounding = rpa.sounding["gfs"].model_copy(
            update={"nwp_cloud_layers": []}
        )
        analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
    ctx = replace(
        clear_context,
        analyses=analyses,
        models=["gfs"],
        icing_method="ogimet_nwp",
    )

    escape = IcingEscapeEvaluator.evaluate(ctx, _ICING_ESCAPE_PARAMS).per_model[0]
    fiki = FIKIIcingEvaluator.evaluate(ctx, {}).per_model[0]

    assert escape.status == AdvisoryStatus.GREEN
    assert escape.data_state == "complete"
    assert escape.primary_method_id == "ogimet_nwp"
    assert fiki.status == AdvisoryStatus.GREEN
    assert fiki.data_state == "complete"
    assert fiki.primary_method_id == "ogimet_nwp"


def test_ieng_evidence_uses_resolved_ieng_geometry(clear_context):
    dd_zone = IcingZone(
        base_ft=4000,
        top_ft=6000,
        risk=IcingRisk.LIGHT,
        icing_type=IcingType.RIME,
    )
    ieng_zone = IcingZone(
        base_ft=7000,
        top_ft=9000,
        risk=IcingRisk.MODERATE,
        icing_type=IcingType.MIXED,
    )
    analyses = []
    for rpa in clear_context.analyses:
        sounding = rpa.sounding["gfs"].model_copy(
            update={
                "icing_zones": [dd_zone],
                "ieng_icing_zones": [ieng_zone],
                "nwp_cloud_layers": [],
            }
        )
        analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
    resolved = _resolve_analyses(analyses, "ieng", None)
    ctx = replace(
        clear_context,
        analyses=resolved,
        models=["gfs"],
        icing_method="ieng",
    )

    model = IcingEscapeEvaluator.evaluate(ctx, _ICING_ESCAPE_PARAMS).per_model[0]
    exposure = [
        region
        for region in model.evidence_regions
        if region.reason_code == "icing_exposure"
    ]

    assert model.primary_method_id == "ieng"
    assert all(region.metric_id == "ieng_icing_risk" for region in exposure)
    assert {
        (region.lower_altitude_ft, region.upper_altitude_ft)
        for region in exposure
    } == {(7000, 9000)}


def test_icing_missing_escape_inputs_keeps_hazard_as_partial(icing_context):
    ctx = replace(icing_context, models=["gfs"], elevation=None)
    model = IcingEscapeEvaluator.evaluate(ctx, _ICING_ESCAPE_PARAMS).per_model[0]

    assert model.status == AdvisoryStatus.AMBER
    assert model.data_state == "partial"
    assert any(
        region.reason_code == "icing_exposure"
        for region in model.evidence_regions
    )
    assert not any(
        region.reason_code == "icing_no_warm_escape"
        for region in model.evidence_regions
    )


def test_icing_regions_split_on_missing_points_and_changed_bounds(icing_context):
    analyses = []
    for rpa in icing_context.analyses:
        sounding = rpa.sounding["gfs"]
        if rpa.point_index in {2, 3}:
            zones = []
        elif rpa.point_index == 4:
            zones = [
                sounding.icing_zones[0].model_copy(
                    update={"base_ft": 5000, "top_ft": 11000}
                )
            ]
        else:
            zones = sounding.icing_zones
        analyses.append(
            rpa.model_copy(
                update={
                    "sounding": {
                        "gfs": sounding.model_copy(update={"icing_zones": zones})
                    }
                }
            )
        )
    ctx = replace(icing_context, analyses=analyses, models=["gfs"])
    result = IcingEscapeEvaluator.evaluate(ctx, _ICING_ESCAPE_PARAMS)
    exposure = [
        region
        for region in result.per_model[0].evidence_regions
        if region.reason_code == "icing_exposure"
    ]
    assert [
        (region.start_point_index, region.end_point_index) for region in exposure
    ] == [(0, 1), (4, 4), (5, 9)]
    assert (
        exposure[1].lower_altitude_ft,
        exposure[1].upper_altitude_ft,
    ) == (5000, 11000)
