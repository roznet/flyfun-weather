"""FIKI icing advisory — icing manageable for FIKI-equipped aircraft.

Evaluates three flight phases:
- Departure: total icing thickness to transit during climb (near origin)
- Cruise: proportion of route with clear air at cruise altitude
- Arrival: total icing thickness to transit during descent (near destination)
"""

from __future__ import annotations

from dataclasses import dataclass

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import min_icing_clearance
from weatherbrief.analysis.advisories.evidence import (
    EvidenceSample,
    icing_method_id,
    icing_method_is_available,
    summarize_evidence,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    IcingRisk,
    IcingZone,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)

_RISK_ORDER = [IcingRisk.NONE, IcingRisk.LIGHT, IcingRisk.MODERATE, IcingRisk.SEVERE]

_ICING_METRIC_BY_METHOD = {
    "ogimet_dd": "icing_risk",
    "ogimet_nwp": "icing_ogimet_nwp_risk",
    "sfip": "sfip_risk",
    "ieng": "ieng_icing_risk",
}


@dataclass(frozen=True)
class _FIKIPointAssessment:
    point_index: int
    distance_nm: float
    available: bool
    zones: tuple[IcingZone, ...]
    transit_thickness_ft: float
    transit_worst: IcingRisk
    transit_sld: bool
    cruise_clear: bool


def _worst_risk(a: IcingRisk, b: IcingRisk) -> IcingRisk:
    """Return the more severe of two icing risks."""
    return a if _RISK_ORDER.index(a) >= _RISK_ORDER.index(b) else b


def _transit_icing(
    zones: list[IcingZone],
    cruise_alt_ft: float,
) -> tuple[float, IcingRisk, bool]:
    """Total icing thickness between surface and cruise altitude at one point.

    Returns (thickness_ft, worst_severity, has_sld).
    """
    thickness = 0.0
    worst = IcingRisk.NONE
    sld = False
    for zone in zones:
        clipped_base = max(zone.base_ft, 0)
        clipped_top = min(zone.top_ft, cruise_alt_ft)
        if clipped_top > clipped_base:
            thickness += clipped_top - clipped_base
            worst = _worst_risk(worst, zone.risk)
            if zone.sld_risk:
                sld = True
    return thickness, worst, sld


_min_icing_clearance = min_icing_clearance  # local alias for backward compat


@register
class FIKIIcingEvaluator:
    """Evaluates icing severity for FIKI-equipped aircraft.

    FIKI aircraft can transit icing but not loiter indefinitely.
    Analyses departure climb, cruise clear-air proportion, and arrival descent.
    """

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="fiki_icing",
            name="FIKI Icing",
            short_description="Icing manageable for FIKI-equipped",
            description=(
                "For FIKI-equipped aircraft. Evaluates icing across three flight "
                "phases: climb-out near departure, clear-air cruise proportion, "
                "and descent near arrival. FIKI can transit icing layers but "
                "should minimise exposure — thick transit layers or extended "
                "cruise in icing are concerning."
            ),
            category="icing",
            timing_class="scan",
            default_enabled=False,  # opt-in for FIKI operators
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="proximity_nm",
                    label="Proximity radius",
                    description=(
                        "Distance from origin/destination to assess "
                        "climb/descent icing"
                    ),
                    type="number",
                    unit="nm",
                    default=50,
                    min=10,
                    max=100,
                    step=10,
                ),
                AdvisoryParameterDef(
                    key="cruise_icing_buffer_ft",
                    label="Cruise icing buffer",
                    description=(
                        "Minimum vertical clearance from cruise altitude to "
                        "nearest icing layer for clear-air cruise"
                    ),
                    type="altitude",
                    unit="ft",
                    default=2000,
                    min=500,
                    max=5000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="transit_thickness_amber_ft",
                    label="Transit thickness amber",
                    description="Total icing thickness during climb/descent for amber",
                    type="altitude",
                    unit="ft",
                    default=3000,
                    min=1000,
                    max=8000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="transit_thickness_red_ft",
                    label="Transit thickness red",
                    description="Total icing thickness during climb/descent for red",
                    type="altitude",
                    unit="ft",
                    default=5000,
                    min=2000,
                    max=10000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="clear_cruise_amber_pct",
                    label="Clear cruise amber",
                    description="Below this clear-air percentage triggers amber",
                    type="percent",
                    unit="%",
                    default=80,
                    min=0,
                    max=100,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="clear_cruise_red_pct",
                    label="Clear cruise red",
                    description="Below this clear-air percentage triggers red",
                    type="percent",
                    unit="%",
                    default=50,
                    min=0,
                    max=100,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="severe_is_red",
                    label="Severe = RED",
                    description="Any severe icing in transit triggers RED",
                    type="boolean",
                    default=1,
                    min=0,
                    max=1,
                    step=1,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        proximity_nm = params.get("proximity_nm", 50)
        cruise_buffer_ft = params.get("cruise_icing_buffer_ft", 2000)
        transit_amber = params.get("transit_thickness_amber_ft", 3000)
        transit_red = params.get("transit_thickness_red_ft", 5000)
        clear_amber = params.get("clear_cruise_amber_pct", 80)
        clear_red = params.get("clear_cruise_red_pct", 50)
        severe_is_red = params.get("severe_is_red", 1) > 0.5

        cruise_alt = ctx.cruise_altitude_ft
        total_dist = ctx.total_distance_nm
        ordered_analyses = sorted(
            ctx.analyses,
            key=lambda rpa: (rpa.distance_from_origin_nm, rpa.point_index),
        )

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            method_id = icing_method_id(ctx.icing_method)
            metric_id = _ICING_METRIC_BY_METHOD.get(method_id or "")
            point_assessments: list[_FIKIPointAssessment] = []

            for rpa in ordered_analyses:
                sounding = rpa.sounding.get(model)
                if not icing_method_is_available(sounding, ctx.icing_method):
                    point_assessments.append(
                        _FIKIPointAssessment(
                            point_index=rpa.point_index,
                            distance_nm=rpa.distance_from_origin_nm,
                            available=False,
                            zones=(),
                            transit_thickness_ft=0.0,
                            transit_worst=IcingRisk.NONE,
                            transit_sld=False,
                            cruise_clear=False,
                        )
                    )
                    continue
                assert sounding is not None
                zones = tuple(sounding.icing_zones)
                thickness, worst, sld = _transit_icing(list(zones), cruise_alt)
                point_assessments.append(
                    _FIKIPointAssessment(
                        point_index=rpa.point_index,
                        distance_nm=rpa.distance_from_origin_nm,
                        available=True,
                        zones=zones,
                        transit_thickness_ft=thickness,
                        transit_worst=worst,
                        transit_sld=sld,
                        cruise_clear=(
                            _min_icing_clearance(list(zones), cruise_alt)
                            >= cruise_buffer_ft
                        ),
                    )
                )

            available = [point for point in point_assessments if point.available]
            departure = [
                point for point in available if point.distance_nm <= proximity_nm
            ]
            arrival = [
                point
                for point in available
                if point.distance_nm >= total_dist - proximity_nm
            ]

            dep_max_thickness = max(
                (point.transit_thickness_ft for point in departure),
                default=0.0,
            )
            dep_worst = IcingRisk.NONE
            for point in departure:
                dep_worst = _worst_risk(dep_worst, point.transit_worst)
            dep_sld = any(point.transit_sld for point in departure)

            arr_max_thickness = max(
                (point.transit_thickness_ft for point in arrival),
                default=0.0,
            )
            arr_worst = IcingRisk.NONE
            for point in arrival:
                arr_worst = _worst_risk(arr_worst, point.transit_worst)
            arr_sld = any(point.transit_sld for point in arrival)

            cruise_total = len(available)
            cruise_clear = sum(point.cruise_clear for point in available)
            cruise_affected = {
                point.point_index for point in available if not point.cruise_clear
            }
            samples: list[EvidenceSample] = []

            def aggregate_non_sld_severity(
                point: _FIKIPointAssessment,
            ) -> AdvisoryStatus:
                if severe_is_red and point.transit_worst == IcingRisk.SEVERE:
                    return AdvisoryStatus.RED
                if point.transit_thickness_ft >= transit_red:
                    return AdvisoryStatus.RED
                if point.transit_thickness_ft >= transit_amber:
                    return AdvisoryStatus.AMBER
                return AdvisoryStatus.GREEN

            for point in available:
                transit_zones = [
                    zone
                    for zone in point.zones
                    if min(zone.top_ft, cruise_alt) > max(zone.base_ft, 0)
                ]
                if point.distance_nm <= proximity_nm:
                    local_severity = aggregate_non_sld_severity(point)
                    for zone in transit_zones:
                        samples.append(
                            EvidenceSample(
                                point_index=point.point_index,
                                severity=(
                                    AdvisoryStatus.RED
                                    if zone.sld_risk
                                    else local_severity
                                ),
                                reason_code="fiki_departure_transit",
                                metric_id=metric_id,
                                method_id=method_id,
                                lower_altitude_ft=round(zone.base_ft),
                                upper_altitude_ft=round(zone.top_ft),
                            )
                        )
                if point.distance_nm >= total_dist - proximity_nm:
                    local_severity = aggregate_non_sld_severity(point)
                    for zone in transit_zones:
                        samples.append(
                            EvidenceSample(
                                point_index=point.point_index,
                                severity=(
                                    AdvisoryStatus.RED
                                    if zone.sld_risk
                                    else local_severity
                                ),
                                reason_code="fiki_arrival_transit",
                                metric_id=metric_id,
                                method_id=method_id,
                                lower_altitude_ft=round(zone.base_ft),
                                upper_altitude_ft=round(zone.top_ft),
                            )
                        )
                for zone in point.zones:
                    if _min_icing_clearance([zone], cruise_alt) >= cruise_buffer_ft:
                        continue
                    samples.append(
                        EvidenceSample(
                            point_index=point.point_index,
                            severity=(
                                AdvisoryStatus.RED
                                if zone.sld_risk
                                else AdvisoryStatus.AMBER
                            ),
                            reason_code="fiki_cruise_icing",
                            metric_id=metric_id,
                            method_id=method_id,
                            lower_altitude_ft=round(zone.base_ft),
                            upper_altitude_ft=round(zone.top_ft),
                        )
                    )

            evaluated_indices = {point.point_index for point in available}
            summary = summarize_evidence(
                route_points=ordered_analyses,
                total_distance_nm=total_dist,
                evaluated_point_indices=evaluated_indices,
                complete_point_indices=evaluated_indices,
                affected_point_indices=cruise_affected,
                evidence_samples=samples,
            )

            # --- derive severity from the three metrics ---
            clear_pct = (
                100.0 * cruise_clear / cruise_total if cruise_total > 0 else 100.0
            )

            loc = ctx.locale
            statuses: list[AdvisoryStatus] = []
            detail_parts: list[str] = []

            if summary.total_points == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            else:
                # SLD — always RED
                if dep_sld or arr_sld:
                    statuses.append(AdvisoryStatus.RED)
                    where = " & ".join(
                        [
                            x
                            for x in [
                                "dep" if dep_sld else "",
                                "arr" if arr_sld else "",
                            ]
                            if x
                        ]
                    )
                    detail_parts.append(adv_t("fiki.sld_risk", loc, where=where))

                # Severe icing in transit
                if severe_is_red and (
                    dep_worst == IcingRisk.SEVERE
                    or arr_worst == IcingRisk.SEVERE
                ):
                    statuses.append(AdvisoryStatus.RED)
                    where = " & ".join(
                        [
                            x
                            for x in [
                                "dep" if dep_worst == IcingRisk.SEVERE else "",
                                "arr" if arr_worst == IcingRisk.SEVERE else "",
                            ]
                            if x
                        ]
                    )
                    detail_parts.append(
                        adv_t("fiki.severe_icing", loc, where=where)
                    )

                # Transit thickness (worst of departure / arrival)
                worst_transit = max(dep_max_thickness, arr_max_thickness)
                if worst_transit >= transit_red:
                    statuses.append(AdvisoryStatus.RED)
                elif worst_transit >= transit_amber:
                    statuses.append(AdvisoryStatus.AMBER)
                else:
                    statuses.append(AdvisoryStatus.GREEN)

                transit_parts = []
                if dep_max_thickness > 0:
                    transit_parts.append(
                        adv_t(
                            "fiki.dep_transit",
                            loc,
                            thickness=f"{dep_max_thickness:.0f}",
                        )
                    )
                if arr_max_thickness > 0:
                    transit_parts.append(
                        adv_t(
                            "fiki.arr_transit",
                            loc,
                            thickness=f"{arr_max_thickness:.0f}",
                        )
                    )
                if transit_parts:
                    detail_parts.append(
                        adv_t(
                            "fiki.transit",
                            loc,
                            parts=", ".join(transit_parts),
                        )
                    )

                # Clear cruise
                if clear_pct < clear_red:
                    statuses.append(AdvisoryStatus.RED)
                elif clear_pct < clear_amber:
                    statuses.append(AdvisoryStatus.AMBER)
                else:
                    statuses.append(AdvisoryStatus.GREEN)
                detail_parts.append(
                    adv_t("fiki.cruise_clear", loc, pct=f"{clear_pct:.0f}")
                )

                status = AdvisoryStatus.worst(statuses)
                if (
                    status == AdvisoryStatus.GREEN
                    and worst_transit == 0
                    and clear_pct >= 100
                ):
                    detail = adv_t("fiki.no_icing", loc)
                else:
                    detail = " | ".join(detail_parts)

            missing_detail = adv_t(
                "no_data" if summary.data_state == "unavailable" else "partial_data",
                loc,
            )
            per_model.append(
                summary.build_result(
                    model=model,
                    status=status,
                    detail=detail,
                    unavailable_detail=missing_detail,
                    primary_method_id=method_id,
                )
            )

        return RouteAdvisoryResult.from_per_model("fiki_icing", per_model, params)
