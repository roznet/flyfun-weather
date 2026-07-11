"""IFR feasibility advisory — overall IFR flight viability assessment.

Composite advisory combining airport conditions, en-route icing exposure,
and convective activity into a single go/no-go style assessment for IFR flights.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import min_icing_clearance
from weatherbrief.analysis.advisories.evidence import (
    DataState,
    EvidenceSample,
    combine_data_states,
    convective_method_id,
    data_state_from_domains,
    icing_metric_id,
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
    ConvectiveRisk,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)
from weatherbrief.models.airport_conditions import FlightCategory

# Convective risk levels ordered by severity
_CONVECTIVE_SEVERITY = [
    ConvectiveRisk.NONE,
    ConvectiveRisk.MARGINAL,
    ConvectiveRisk.LOW,
    ConvectiveRisk.MODERATE,
    ConvectiveRisk.HIGH,
    ConvectiveRisk.EXTREME,
]
_CONVECTIVE_SEVERITY_INDEX = {r: i for i, r in enumerate(_CONVECTIVE_SEVERITY)}

# Default icing-exposure thresholds (route % with icing near cruise altitude).
# Single source of truth shared by catalog_entry() parameter defaults and the
# evaluate() fallbacks so the two cannot drift. They previously disagreed —
# catalog advertised 20/50 while evaluate() fell back to 15/30 — meaning a call
# without explicit params graded icing more aggressively than the UI showed.
_ICING_PCT_AMBER_DEFAULT = 20
_ICING_PCT_RED_DEFAULT = 50

@dataclass(frozen=True)
class IFRPointAssessment:
    """One route point's supported IFR hazard axes and display evidence."""

    point_index: int
    icing_available: bool
    convective_available: bool
    icing_affected: bool
    convective_affected: bool
    convective_risk: ConvectiveRisk
    icing_method_id: str | None
    convective_method_id: str | None
    icing_samples: tuple[EvidenceSample, ...]
    convective_samples: tuple[EvidenceSample, ...]


@dataclass(frozen=True)
class _IFRAirportAssessment:
    status: AdvisoryStatus
    detail: str
    data_state: DataState
    has_evaluated_endpoint: bool


def _finite_altitude_bounds(
    base_ft: float | None,
    top_ft: float | None,
) -> tuple[int | None, int | None]:
    """Return safe evidence bounds, or an unknown envelope for invalid data."""
    if (
        base_ft is None
        or top_ft is None
        or not math.isfinite(base_ft)
        or not math.isfinite(top_ft)
        or base_ft > top_ft
    ):
        return None, None
    return round(base_ft), round(top_ft)


def _worst_status(*statuses: AdvisoryStatus) -> AdvisoryStatus:
    """Return the most severe status from the given values."""
    return AdvisoryStatus.worst(list(statuses))


def _check_airport_ifr(
    ctx: RouteContext,
    model: str,
    min_dep_ceiling_ft: float,
    min_arr_ceiling_ft: float,
) -> _IFRAirportAssessment:
    """Check departure and arrival airport conditions for IFR feasibility.

    IFR flights accept IFR conditions, but LIFR triggers amber and
    ceilings below configurable minimums trigger red.
    """
    expected = {"departure", "arrival"}
    if ctx.airport_conditions is None:
        return _IFRAirportAssessment(
            status=AdvisoryStatus.UNAVAILABLE,
            detail="",
            data_state=data_state_from_domains(
                expected=expected,
                evaluated=set(),
                complete=set(),
            ),
            has_evaluated_endpoint=False,
        )

    dep = ctx.airport_conditions.departure
    arr = ctx.airport_conditions.arrival
    dep_cond = dep.condition_for_model(model)
    arr_cond = arr.condition_for_model(model)

    loc = ctx.locale
    parts: list[str] = []
    worst = AdvisoryStatus.GREEN
    evaluated: set[str] = set()
    complete: set[str] = set()

    for endpoint, label_key, icao, cond, min_ceil in [
        ("departure", "airport.dep", dep.icao, dep_cond, min_dep_ceiling_ft),
        ("arrival", "airport.arr", arr.icao, arr_cond, min_arr_ceiling_ft),
    ]:
        if cond is None:
            continue
        ceiling_available = cond.ceiling_evaluated or cond.ceiling_ft is not None
        visibility_available = cond.visibility_sm is not None
        if not (ceiling_available or visibility_available):
            continue
        evaluated.add(endpoint)
        if ceiling_available and visibility_available:
            complete.add(endpoint)
        label = adv_t(label_key, loc)
        cat = cond.flight_category

        # LIFR is concerning for IFR
        if cat == FlightCategory.LIFR:
            # Check against minimum ceiling
            if cond.ceiling_ft is not None and cond.ceiling_ft < min_ceil:
                worst = _worst_status(worst, AdvisoryStatus.RED)
                parts.append(adv_t(
                    "ifr.lifr_below_min", loc,
                    label=label, icao=icao,
                    ceiling=cond.ceiling_ft, min=int(min_ceil),
                ))
            else:
                worst = _worst_status(worst, AdvisoryStatus.AMBER)
                parts.append(adv_t("ifr.lifr", loc, label=label, icao=icao))

    detail = " | ".join(parts) if parts else ""
    state = data_state_from_domains(
        expected=expected,
        evaluated=evaluated,
        complete=complete,
    )
    return _IFRAirportAssessment(
        status=worst if evaluated else AdvisoryStatus.UNAVAILABLE,
        detail=detail,
        data_state=state,
        has_evaluated_endpoint=bool(evaluated),
    )


def _assess_enroute_hazards(
    ctx: RouteContext,
    model: str,
    convective_min_risk_idx: int,
    cruise_altitude_ft: float,
    icing_altitude_buffer_ft: float,
) -> list[IFRPointAssessment]:
    """Assess both route hazard axes once, retaining availability and evidence."""
    ordered_analyses = sorted(
        ctx.analyses,
        key=lambda rpa: (rpa.distance_from_origin_nm, rpa.point_index),
    )
    selected_icing_method = icing_method_id(ctx.icing_method)
    selected_icing_metric = icing_metric_id(selected_icing_method)
    assessments: list[IFRPointAssessment] = []

    for rpa in ordered_analyses:
        sounding = rpa.sounding.get(model)
        icing_available = icing_method_is_available(sounding, ctx.icing_method)
        icing_samples: list[EvidenceSample] = []
        if icing_available:
            assert sounding is not None
            for zone in sounding.icing_zones:
                if (
                    min_icing_clearance([zone], cruise_altitude_ft)
                    >= icing_altitude_buffer_ft
                ):
                    continue
                lower_altitude_ft, upper_altitude_ft = _finite_altitude_bounds(
                    zone.base_ft,
                    zone.top_ft,
                )
                icing_samples.append(
                    EvidenceSample(
                        point_index=rpa.point_index,
                        severity=AdvisoryStatus.AMBER,
                        reason_code="ifr_icing_exposure",
                        metric_id=selected_icing_metric,
                        method_id=selected_icing_method,
                        lower_altitude_ft=lower_altitude_ft,
                        upper_altitude_ft=upper_altitude_ft,
                    )
                )

        conv = sounding.convective if sounding is not None else None
        convective_available = conv is not None
        convective_affected = False
        convective_risk = ConvectiveRisk.NONE
        convective_method: str | None = None
        convective_samples: list[EvidenceSample] = []
        if conv is not None:
            convective_risk = conv.risk_level
            convective_method = convective_method_id(conv.method)
            risk_idx = _CONVECTIVE_SEVERITY_INDEX.get(conv.risk_level, 0)
            if risk_idx >= convective_min_risk_idx:
                convective_affected = True
                lower_altitude_ft, upper_altitude_ft = _finite_altitude_bounds(
                    conv.base_ft,
                    conv.top_ft,
                )
                convective_samples.append(
                    EvidenceSample(
                        point_index=rpa.point_index,
                        severity=(
                            AdvisoryStatus.RED
                            if conv.risk_level
                            in (ConvectiveRisk.HIGH, ConvectiveRisk.EXTREME)
                            else AdvisoryStatus.AMBER
                        ),
                        reason_code="ifr_convective_exposure",
                        metric_id=(
                            "nwp_convective_risk"
                            if convective_method == "nwp"
                            else "convective_risk"
                        ),
                        method_id=convective_method,
                        lower_altitude_ft=lower_altitude_ft,
                        upper_altitude_ft=upper_altitude_ft,
                    )
                )

        assessments.append(
            IFRPointAssessment(
                point_index=rpa.point_index,
                icing_available=icing_available,
                convective_available=convective_available,
                icing_affected=bool(icing_samples),
                convective_affected=convective_affected,
                convective_risk=convective_risk,
                icing_method_id=(
                    selected_icing_method if icing_available else None
                ),
                convective_method_id=convective_method,
                icing_samples=tuple(icing_samples),
                convective_samples=tuple(convective_samples),
            )
        )

    return assessments


@register
class IFRFeasibilityEvaluator:
    """Evaluates overall feasibility of IFR flight along the route.

    Combines airport conditions (LIFR triggers amber, below-minimums triggers
    red), en-route icing exposure, and convective activity into a single
    advisory. Designed for instrument-rated pilots assessing IFR mission
    viability.
    """

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="ifr_feasibility",
            name="IFR Feasibility",
            short_description="Overall IFR flight viability",
            description=(
                "Composite assessment of IFR flight feasibility. Checks airport "
                "conditions (LIFR triggers amber, below-minimums triggers red), "
                "en-route icing exposure percentage, and convective activity. "
                "IFR or better at airports is GREEN. Icing exceeding the "
                "threshold percentage or significant convective activity "
                "triggers RED."
            ),
            category="flight_rules",
            timing_class="scan",
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="min_dep_ceiling_ft",
                    label="Min dep ceiling",
                    description="Minimum departure ceiling for IFR operations",
                    type="altitude",
                    unit="ft",
                    default=200,
                    min=0,
                    max=1000,
                    step=100,
                ),
                AdvisoryParameterDef(
                    key="min_arr_ceiling_ft",
                    label="Min arr ceiling",
                    description="Minimum arrival ceiling for IFR approach",
                    type="altitude",
                    unit="ft",
                    default=400,
                    min=100,
                    max=1500,
                    step=100,
                ),
                AdvisoryParameterDef(
                    key="icing_pct_amber",
                    label="Icing % (amber)",
                    description="Route percentage with icing near cruise for amber",
                    type="percent",
                    unit="%",
                    default=_ICING_PCT_AMBER_DEFAULT,
                    min=5,
                    max=50,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="icing_pct_red",
                    label="Icing % (red)",
                    description="Route percentage with icing near cruise for red",
                    type="percent",
                    unit="%",
                    default=_ICING_PCT_RED_DEFAULT,
                    min=10,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="icing_altitude_buffer_ft",
                    label="Icing alt buffer",
                    description=(
                        "Icing above cruise + buffer is ignored "
                        "(irrelevant altitude)"
                    ),
                    type="altitude",
                    unit="ft",
                    default=2000,
                    min=500,
                    max=5000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="convective_min_risk",
                    label="Convective min",
                    description=(
                        "Minimum convective risk to flag "
                        "(2=LOW, 3=MODERATE, 4=HIGH)"
                    ),
                    type="number",
                    default=3,
                    min=1,
                    max=5,
                    step=1,
                ),
                AdvisoryParameterDef(
                    key="convective_pct_red",
                    label="Convective % (red)",
                    description="Route percentage with convective activity for red",
                    type="percent",
                    unit="%",
                    default=10,
                    min=5,
                    max=50,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        min_dep_ceiling_ft = params.get("min_dep_ceiling_ft", 200)
        min_arr_ceiling_ft = params.get("min_arr_ceiling_ft", 400)
        icing_pct_amber = params.get("icing_pct_amber", _ICING_PCT_AMBER_DEFAULT)
        icing_pct_red = params.get("icing_pct_red", _ICING_PCT_RED_DEFAULT)
        icing_altitude_buffer_ft = params.get("icing_altitude_buffer_ft", 2000)
        convective_min_risk = int(params.get("convective_min_risk", 3))
        convective_pct_red = params.get("convective_pct_red", 10)

        per_model: list[ModelAdvisoryResult] = []
        ordered_analyses = sorted(
            ctx.analyses,
            key=lambda rpa: (rpa.distance_from_origin_nm, rpa.point_index),
        )

        for model in ctx.models:
            # 1. Airport conditions
            airport = _check_airport_ifr(
                ctx, model, min_dep_ceiling_ft, min_arr_ceiling_ft
            )

            # 2. En-route hazards. One point pass owns grading, counts, data
            # completeness, and the evidence regions for both route axes.
            point_assessments = _assess_enroute_hazards(
                ctx,
                model,
                convective_min_risk,
                ctx.cruise_altitude_ft,
                icing_altitude_buffer_ft,
            )

            icing_evaluated = {
                point.point_index
                for point in point_assessments
                if point.icing_available
            }
            convective_evaluated = {
                point.point_index
                for point in point_assessments
                if point.convective_available
            }
            icing_points = {
                point.point_index
                for point in point_assessments
                if point.icing_affected
            }
            convective_points = {
                point.point_index
                for point in point_assessments
                if point.convective_affected
            }
            affected_points = icing_points | convective_points
            route_evaluated = icing_evaluated | convective_evaluated
            route_complete = icing_evaluated & convective_evaluated
            evidence_samples = [
                sample
                for point in point_assessments
                for sample in (*point.icing_samples, *point.convective_samples)
            ]

            route_summary = summarize_evidence(
                route_points=ordered_analyses,
                total_distance_nm=ctx.total_distance_nm,
                evaluated_point_indices=route_evaluated,
                complete_point_indices=route_complete,
                affected_point_indices=affected_points,
                evidence_samples=evidence_samples,
            )
            icing_summary = summarize_evidence(
                route_points=ordered_analyses,
                total_distance_nm=ctx.total_distance_nm,
                evaluated_point_indices=icing_evaluated,
                complete_point_indices=icing_evaluated,
                affected_point_indices=icing_points,
                evidence_samples=(),
            )
            convective_summary = summarize_evidence(
                route_points=ordered_analyses,
                total_distance_nm=ctx.total_distance_nm,
                evaluated_point_indices=convective_evaluated,
                complete_point_indices=convective_evaluated,
                affected_point_indices=convective_points,
                evidence_samples=(),
            )

            loc = ctx.locale

            # 3. Determine icing status
            icing_status = (
                AdvisoryStatus.GREEN
                if icing_summary.total_points > 0
                else AdvisoryStatus.UNAVAILABLE
            )
            icing_detail = ""
            if icing_summary.affected_points > 0:
                icing_pct = icing_summary.affected_pct
                if icing_pct >= icing_pct_red:
                    icing_status = AdvisoryStatus.RED
                elif icing_pct >= icing_pct_amber:
                    icing_status = AdvisoryStatus.AMBER
                if icing_status != AdvisoryStatus.GREEN:
                    icing_detail = adv_t(
                        "ifr.icing_over", loc,
                        extent=icing_summary.format_extent(),
                    )

            # 4. Determine convective status
            conv_status = (
                AdvisoryStatus.GREEN
                if convective_summary.total_points > 0
                else AdvisoryStatus.UNAVAILABLE
            )
            conv_detail = ""
            affected_convective = [
                point
                for point in point_assessments
                if point.convective_affected
            ]
            worst_conv_risk = max(
                (point.convective_risk for point in affected_convective),
                key=lambda risk: _CONVECTIVE_SEVERITY_INDEX.get(risk, 0),
                default=ConvectiveRisk.NONE,
            )
            if convective_summary.affected_points > 0:
                # HIGH/EXTREME at any point is always red
                if worst_conv_risk in (ConvectiveRisk.HIGH, ConvectiveRisk.EXTREME):
                    conv_status = AdvisoryStatus.RED
                elif convective_summary.affected_pct >= convective_pct_red:
                    conv_status = AdvisoryStatus.RED
                else:
                    conv_status = AdvisoryStatus.AMBER
                conv_detail = adv_t(
                    "ifr.conv_over", loc,
                    risk=worst_conv_risk.value.upper(),
                    extent=convective_summary.format_extent(),
                )

            # 5. Combine all factors
            status = _worst_status(airport.status, icing_status, conv_status)
            combined_state = combine_data_states(
                route_summary.data_state,
                airport.data_state,
            )
            axis_status_by_reason = {
                "ifr_icing_exposure": icing_status,
                "ifr_convective_exposure": conv_status,
            }
            summary = replace(
                route_summary,
                data_state=combined_state,
                evidence_regions=[
                    region.model_copy(
                        update={
                            "severity": axis_status_by_reason.get(
                                region.reason_code,
                                region.severity,
                            )
                        }
                    )
                    for region in route_summary.evidence_regions
                ],
            )

            detail_parts = []
            if airport.detail:
                detail_parts.append(airport.detail)
            if icing_detail:
                detail_parts.append(icing_detail)
            if conv_detail:
                detail_parts.append(conv_detail)

            if not detail_parts:
                detail = adv_t("ifr.acceptable", loc)
            else:
                detail = " | ".join(detail_parts)

            # 6. Attribute the headline to all distinct methods tied at its raw
            # status. A composite must never claim icing or convection alone when
            # both independently control the same verdict.
            controlling_methods: set[str] = set()
            if (
                airport.has_evaluated_endpoint
                and airport.status == status
                and status != AdvisoryStatus.UNAVAILABLE
            ):
                controlling_methods.add("airport_conditions")
            if (
                icing_summary.total_points > 0
                and icing_status == status
                and status != AdvisoryStatus.UNAVAILABLE
            ):
                controlling_methods.update(
                    point.icing_method_id
                    for point in point_assessments
                    if point.icing_method_id is not None
                )
            if (
                convective_summary.total_points > 0
                and conv_status == status
                and status != AdvisoryStatus.UNAVAILABLE
            ):
                if conv_status == AdvisoryStatus.RED:
                    severe_methods = {
                        point.convective_method_id
                        for point in affected_convective
                        if point.convective_risk
                        in (ConvectiveRisk.HIGH, ConvectiveRisk.EXTREME)
                        and point.convective_method_id is not None
                    }
                    convective_methods = severe_methods or {
                        point.convective_method_id
                        for point in affected_convective
                        if point.convective_method_id is not None
                    }
                elif conv_status == AdvisoryStatus.AMBER:
                    convective_methods = {
                        point.convective_method_id
                        for point in affected_convective
                        if point.convective_method_id is not None
                    }
                else:
                    convective_methods = {
                        point.convective_method_id
                        for point in point_assessments
                        if point.convective_available
                        and point.convective_method_id is not None
                    }
                controlling_methods.update(convective_methods)

            if len(controlling_methods) == 1:
                primary_method_id = next(iter(controlling_methods))
            elif len(controlling_methods) > 1:
                primary_method_id = "ifr_composite"
            else:
                primary_method_id = None

            missing_detail = adv_t(
                "no_data" if combined_state == "unavailable" else "partial_data",
                loc,
            )
            per_model.append(
                summary.build_result(
                    model=model,
                    status=status,
                    detail=detail,
                    unavailable_detail=missing_detail,
                    primary_method_id=primary_method_id,
                )
            )

        return RouteAdvisoryResult.from_per_model("ifr_feasibility", per_model, params)
