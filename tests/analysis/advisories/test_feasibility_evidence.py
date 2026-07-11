"""Evidence and completeness contracts for VFR/IFR composites."""

from __future__ import annotations

from dataclasses import replace

from weatherbrief.analysis.advisories.ifr_feasibility import (
    IFRFeasibilityEvaluator,
)
from weatherbrief.analysis.advisories.vfr_feasibility import (
    VFRFeasibilityEvaluator,
    _assess_enroute_vfr,
    _corridor_points,
)
from weatherbrief.models import (
    AdvisoryStatus,
    CloudCoverage,
    ConvectiveAssessment,
    ConvectiveRisk,
    EnhancedCloudLayer,
    IcingRisk,
    IcingType,
    IcingZone,
    PrecipIntensity,
    PrecipPhase,
    PrecipitationAssessment,
)
from weatherbrief.models.airport_conditions import FlightCategory


def _with_airport_condition_updates(ctx, *, departure=None, arrival=None):
    airport_conditions = ctx.airport_conditions
    assert airport_conditions is not None
    endpoint_updates = {}
    for endpoint, condition_updates in (
        ("departure", departure),
        ("arrival", arrival),
    ):
        if condition_updates is None:
            continue
        summary = getattr(airport_conditions, endpoint)
        endpoint_updates[endpoint] = summary.model_copy(
            update={
                "conditions": [
                    condition.model_copy(update=condition_updates)
                    for condition in summary.conditions
                ]
            }
        )
    return replace(
        ctx,
        airport_conditions=airport_conditions.model_copy(update=endpoint_updates),
    )


def _with_precipitation(ctx, by_index=None, *, model="gfs"):
    by_index = by_index or {}
    analyses = []
    for rpa in ctx.analyses:
        soundings = dict(rpa.sounding)
        sounding = soundings.get(model)
        if sounding is not None:
            soundings[model] = sounding.model_copy(
                update={
                    "precipitation": by_index.get(
                        rpa.point_index,
                        PrecipitationAssessment(),
                    )
                }
            )
        analyses.append(rpa.model_copy(update={"sounding": soundings}))
    return replace(ctx, analyses=analyses)


def _with_convective_none(ctx, *, model="gfs", nwp_cloud_layers=None):
    analyses = []
    for rpa in ctx.analyses:
        soundings = dict(rpa.sounding)
        sounding = soundings.get(model)
        if sounding is not None:
            soundings[model] = sounding.model_copy(
                update={
                    "convective": ConvectiveAssessment(
                        risk_level=ConvectiveRisk.NONE,
                    ),
                    "nwp_cloud_layers": nwp_cloud_layers,
                }
            )
        analyses.append(rpa.model_copy(update={"sounding": soundings}))
    return replace(ctx, analyses=analyses)


def test_vfr_missing_airport_domain_and_clear_route_is_unavailable(clear_context):
    ctx = _with_precipitation(
        replace(clear_context, airport_conditions=None, models=["gfs"])
    )

    model = VFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.UNAVAILABLE


def test_vfr_missing_airport_source_fields_is_partial_unavailable(
    vfr_clear_context,
):
    missing_sources = {
        "ceiling_ft": None,
        "ceiling_evaluated": False,
        "visibility_sm": None,
    }
    ctx = _with_airport_condition_updates(
        replace(vfr_clear_context, models=["gfs"]),
        departure=missing_sources,
        arrival=missing_sources,
    )

    model = VFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.status == AdvisoryStatus.UNAVAILABLE
    assert model.data_state == "partial"


def test_vfr_assessed_clear_ceiling_is_complete_green(vfr_clear_context):
    ctx = replace(vfr_clear_context, models=["gfs"])
    airport_conditions = ctx.airport_conditions
    assert airport_conditions is not None
    assert all(
        condition.ceiling_ft is None
        for summary in (
            airport_conditions.departure,
            airport_conditions.arrival,
        )
        for condition in summary.conditions
        if condition.model == "gfs"
    )

    model = VFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.status == AdvisoryStatus.GREEN
    assert model.data_state == "complete"


def test_vfr_known_airport_hazard_survives_missing_other_source(
    vfr_clear_context,
):
    ctx = _with_airport_condition_updates(
        replace(vfr_clear_context, models=["gfs"]),
        departure={
            "flight_category": FlightCategory.IFR,
            "ceiling_ft": 800,
            "ceiling_evaluated": False,
            "visibility_sm": None,
        },
    )

    model = VFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.status == AdvisoryStatus.RED
    assert model.data_state == "partial"


def test_vfr_partial_red_route_evidence_is_preserved(vfr_imc_enroute_context):
    ctx = replace(
        vfr_imc_enroute_context,
        airport_conditions=None,
        models=["gfs"],
    )

    model = VFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.RED


def test_vfr_cruise_imc_emits_cloud_method_and_geometry(vfr_imc_enroute_context):
    ctx = _with_precipitation(vfr_imc_enroute_context)

    model = VFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.status == AdvisoryStatus.RED
    assert model.primary_method_id == "dewpoint_depression"
    assert {
        (
            region.reason_code,
            region.metric_id,
            region.method_id,
            region.lower_altitude_ft,
            region.upper_altitude_ft,
        )
        for region in model.evidence_regions
    } == {
        (
            "vfr_cruise_imc",
            "cloud_coverage",
            "dewpoint_depression",
            6000,
            12000,
        )
    }


def test_vfr_corridor_evidence_does_not_change_headline_count(
    vfr_ovc_corridor_context,
):
    ctx = _with_precipitation(vfr_ovc_corridor_context)

    model = VFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.status == AdvisoryStatus.RED
    assert model.affected_points == 0
    assert model.primary_method_id == "dewpoint_depression"
    assert {
        (
            region.reason_code,
            region.method_id,
            region.lower_altitude_ft,
            region.upper_altitude_ft,
        )
        for region in model.evidence_regions
    } == {
        ("vfr_climb_deck", "dewpoint_depression", 3500, 5000)
    }


def test_vfr_red_corridor_primary_uses_only_ovc_method(
    vfr_ovc_corridor_context,
):
    bkn_deck = EnhancedCloudLayer(
        base_ft=3500,
        top_ft=5000,
        coverage=CloudCoverage.BKN,
    )
    first, *middle, last = vfr_ovc_corridor_context.analyses
    first_sounding = first.sounding["gfs"].model_copy(
        update={"cloud_method_effective": "nwp"}
    )
    last_sounding = last.sounding["gfs"].model_copy(
        update={
            "cloud_layers": [bkn_deck],
            "cloud_method_effective": "dd",
        }
    )
    ctx = replace(
        vfr_ovc_corridor_context,
        analyses=[
            first.model_copy(update={"sounding": {"gfs": first_sounding}}),
            *middle,
            last.model_copy(update={"sounding": {"gfs": last_sounding}}),
        ],
    )

    model = VFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.data_state == "complete"
    assert model.status == AdvisoryStatus.RED
    assert model.primary_method_id == "nwp"
    assert {
        (region.reason_code, region.severity, region.method_id)
        for region in model.evidence_regions
    } == {
        ("vfr_climb_deck", AdvisoryStatus.RED, "nwp"),
        ("vfr_descent_deck", AdvisoryStatus.AMBER, "dewpoint_depression"),
    }


def test_vfr_assessment_records_carry_effective_cloud_method(
    vfr_ovc_corridor_context,
):
    first, *rest = vfr_ovc_corridor_context.analyses
    sounding = first.sounding["gfs"].model_copy(
        update={"cloud_method_effective": "nwp"}
    )
    ctx = replace(
        vfr_ovc_corridor_context,
        analyses=[
            first.model_copy(update={"sounding": {"gfs": sounding}}),
            *rest,
        ],
    )

    corridor_records = tuple(_corridor_points(ctx, "gfs", 5.0, "climb"))
    route_records = _assess_enroute_vfr(
        ctx,
        "gfs",
        cloud_clearance_ft=1000.0,
        altitude_ft=ctx.cruise_altitude_ft,
    )

    assert corridor_records[0].method_id == "nwp"
    assert route_records[0].method_id == "nwp"


def test_vfr_precip_evidence_is_capped_and_outside_headline_count(
    vfr_clear_context,
):
    snow = PrecipitationAssessment(
        surface_phase=PrecipPhase.SNOW,
        surface_intensity=PrecipIntensity.MODERATE,
        snow_cm=1.0,
        total_mm=1.0,
    )
    ctx = _with_precipitation(
        replace(vfr_clear_context, models=["gfs"]),
        {1: snow},
    )

    model = VFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.data_state == "complete"
    assert model.status == AdvisoryStatus.AMBER
    assert model.affected_points == 0
    assert model.primary_method_id == "nwp_precipitation_profile"
    assert {
        (
            region.reason_code,
            region.severity,
            region.metric_id,
            region.method_id,
        )
        for region in model.evidence_regions
    } == {
        (
            "vfr_precip_visibility",
            AdvisoryStatus.AMBER,
            "precipitation_mm",
            "nwp_precipitation_profile",
        )
    }


def test_vfr_light_rain_stays_green_without_precip_evidence(vfr_clear_context):
    light_rain = PrecipitationAssessment(
        surface_phase=PrecipPhase.RAIN,
        surface_intensity=PrecipIntensity.LIGHT,
        total_mm=0.2,
    )
    ctx = _with_precipitation(
        replace(vfr_clear_context, models=["gfs"]),
        {1: light_rain},
    )

    model = VFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.status == AdvisoryStatus.GREEN
    assert "vfr_precip_visibility" not in {
        region.reason_code for region in model.evidence_regions
    }


def test_vfr_tied_airport_and_cloud_methods_use_composite_primary(
    vfr_marginal_clearance_context,
    vfr_mvfr_airport_context,
):
    ctx = replace(
        vfr_marginal_clearance_context,
        airport_conditions=vfr_mvfr_airport_context.airport_conditions,
    )
    ctx = _with_precipitation(ctx)

    model = VFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.status == AdvisoryStatus.AMBER
    assert model.primary_method_id == "vfr_composite"
    assert {region.reason_code for region in model.evidence_regions} == {
        "vfr_cloud_clearance"
    }


def test_vfr_primary_ignores_clear_points_using_another_cloud_method(
    vfr_clear_context,
):
    deck = EnhancedCloudLayer(
        base_ft=6000,
        top_ft=12000,
        coverage=CloudCoverage.OVC,
    )
    analyses = []
    for rpa in vfr_clear_context.analyses:
        soundings = dict(rpa.sounding)
        sounding = soundings["gfs"]
        hazardous = rpa.point_index < 4
        soundings["gfs"] = sounding.model_copy(
            update={
                "cloud_layers": [deck] if hazardous else [],
                "cloud_method_effective": "nwp" if hazardous else "dd",
            }
        )
        analyses.append(rpa.model_copy(update={"sounding": soundings}))
    ctx = replace(vfr_clear_context, analyses=analyses, models=["gfs"])

    model = VFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.status == AdvisoryStatus.RED
    assert model.primary_method_id == "nwp"


def test_vfr_one_missing_airport_endpoint_is_partial(vfr_clear_context):
    airport_conditions = vfr_clear_context.airport_conditions
    assert airport_conditions is not None
    airport_conditions = airport_conditions.model_copy(
        update={
            "arrival": airport_conditions.arrival.model_copy(
                update={"conditions": []}
            )
        }
    )
    ctx = replace(vfr_clear_context, airport_conditions=airport_conditions)

    model = VFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.UNAVAILABLE


def test_vfr_missing_descent_corridor_phase_is_partial(vfr_clear_context):
    ctx = replace(
        vfr_clear_context,
        analyses=vfr_clear_context.analyses[:-1],
        models=["gfs"],
    )

    model = VFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.UNAVAILABLE


def test_ifr_icing_and_convection_same_point_count_once(ifr_normal_context):
    first = ifr_normal_context.analyses[0]
    icing = IcingZone(
        base_ft=4000,
        top_ft=10000,
        risk=IcingRisk.MODERATE,
        icing_type=IcingType.MIXED,
    )
    sounding = first.sounding["gfs"].model_copy(
        update={
            "icing_zones": [icing],
            "convective": ConvectiveAssessment(
                risk_level=ConvectiveRisk.MODERATE,
                base_ft=4000,
                top_ft=18000,
            ),
        }
    )
    analyses = [
        first.model_copy(update={"sounding": {"gfs": sounding}}),
        *[
            rpa.model_copy(update={"sounding": {"gfs": rpa.sounding["gfs"]}})
            for rpa in ifr_normal_context.analyses[1:]
        ],
    ]
    ctx = replace(
        ifr_normal_context,
        analyses=analyses,
        models=["gfs"],
    )

    model = IFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.affected_points == 1
    assert {region.reason_code for region in model.evidence_regions} == {
        "ifr_icing_exposure",
        "ifr_convective_exposure",
    }


def test_ifr_icing_evidence_uses_selected_method_and_zone_geometry(
    ifr_heavy_icing_context,
):
    model = IFRFeasibilityEvaluator.evaluate(
        ifr_heavy_icing_context,
        {},
    ).per_model[0]

    assert model.status == AdvisoryStatus.RED
    assert model.data_state == "complete"
    assert model.primary_method_id == "ogimet_dd"
    assert {
        (
            region.reason_code,
            region.severity,
            region.metric_id,
            region.method_id,
            region.lower_altitude_ft,
            region.upper_altitude_ft,
        )
        for region in model.evidence_regions
    } == {
        (
            "ifr_icing_exposure",
            AdvisoryStatus.RED,
            "icing_risk",
            "ogimet_dd",
            4000,
            10000,
        )
    }


def test_ifr_convective_evidence_uses_active_method_and_geometry(
    ifr_convective_context,
):
    model = IFRFeasibilityEvaluator.evaluate(
        ifr_convective_context,
        {},
    ).per_model[0]

    assert model.status == AdvisoryStatus.RED
    assert model.primary_method_id == "thermo"
    assert {region.reason_code for region in model.evidence_regions} == {
        "ifr_convective_exposure"
    }
    assert {region.method_id for region in model.evidence_regions} == {"thermo"}


def test_ifr_tied_icing_and_convection_use_composite_primary(ifr_normal_context):
    icing = IcingZone(
        base_ft=4000,
        top_ft=10000,
        risk=IcingRisk.MODERATE,
        icing_type=IcingType.MIXED,
    )
    analyses = []
    for rpa in ifr_normal_context.analyses:
        sounding = rpa.sounding["gfs"].model_copy(
            update={
                "icing_zones": [icing],
                "convective": ConvectiveAssessment(
                    risk_level=ConvectiveRisk.MODERATE,
                    base_ft=4000,
                    top_ft=18000,
                ),
            }
        )
        analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
    ctx = replace(ifr_normal_context, analyses=analyses, models=["gfs"])

    model = IFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.status == AdvisoryStatus.RED
    assert model.affected_points == len(ctx.analyses)
    assert model.primary_method_id == "ifr_composite"
    assert {
        (region.reason_code, region.severity)
        for region in model.evidence_regions
    } == {
        ("ifr_icing_exposure", AdvisoryStatus.RED),
        ("ifr_convective_exposure", AdvisoryStatus.RED),
    }


def test_ifr_missing_selected_icing_geometry_is_partial_not_clear(
    ifr_normal_context,
):
    ctx = _with_convective_none(
        replace(ifr_normal_context, models=["gfs"], icing_method="ogimet_nwp"),
        nwp_cloud_layers=None,
    )

    model = IFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.UNAVAILABLE


def test_ifr_available_clear_selected_icing_geometry_is_complete(
    ifr_normal_context,
):
    ctx = _with_convective_none(
        replace(ifr_normal_context, models=["gfs"], icing_method="ogimet_nwp"),
        nwp_cloud_layers=[],
    )

    model = IFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.data_state == "complete"
    assert model.status == AdvisoryStatus.GREEN


def test_ifr_partial_convective_red_is_preserved(ifr_convective_context):
    ctx = replace(
        ifr_convective_context,
        icing_method="ogimet_nwp",
    )

    model = IFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.RED


def test_ifr_missing_active_convection_is_partial_not_clear(ifr_normal_context):
    analyses = []
    for rpa in ifr_normal_context.analyses:
        soundings = dict(rpa.sounding)
        soundings["gfs"] = soundings["gfs"].model_copy(
            update={"convective": None}
        )
        analyses.append(rpa.model_copy(update={"sounding": soundings}))
    ctx = replace(ifr_normal_context, analyses=analyses, models=["gfs"])

    model = IFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.UNAVAILABLE


def test_ifr_one_missing_airport_endpoint_is_partial(ifr_normal_context):
    airport_conditions = ifr_normal_context.airport_conditions
    assert airport_conditions is not None
    airport_conditions = airport_conditions.model_copy(
        update={
            "arrival": airport_conditions.arrival.model_copy(
                update={"conditions": []}
            )
        }
    )
    ctx = replace(
        ifr_normal_context,
        airport_conditions=airport_conditions,
        models=["gfs"],
    )

    model = IFRFeasibilityEvaluator.evaluate(ctx, {}).per_model[0]

    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.UNAVAILABLE
