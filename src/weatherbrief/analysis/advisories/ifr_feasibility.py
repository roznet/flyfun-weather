"""IFR feasibility advisory — overall IFR flight viability assessment.

Composite advisory combining airport conditions, en-route icing exposure,
and convective activity into a single go/no-go style assessment for IFR flights.
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import format_extent, min_icing_clearance
from weatherbrief.analysis.advisories.registry import register
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


def _worst_status(*statuses: AdvisoryStatus) -> AdvisoryStatus:
    """Return the most severe status from the given values."""
    return AdvisoryStatus.worst(list(statuses))


def _check_airport_ifr(
    ctx: RouteContext,
    model: str,
    min_dep_ceiling_ft: float,
    min_arr_ceiling_ft: float,
) -> tuple[AdvisoryStatus, str]:
    """Check departure and arrival airport conditions for IFR feasibility.

    IFR flights accept IFR conditions, but LIFR triggers amber and
    ceilings below configurable minimums trigger red.

    Returns (status, detail_fragment).
    """
    if ctx.airport_conditions is None:
        return AdvisoryStatus.GREEN, ""

    dep = ctx.airport_conditions.departure
    arr = ctx.airport_conditions.arrival
    dep_cond = dep.condition_for_model(model)
    arr_cond = arr.condition_for_model(model)

    parts: list[str] = []
    worst = AdvisoryStatus.GREEN

    for label, icao, cond, min_ceil in [
        ("Dep", dep.icao, dep_cond, min_dep_ceiling_ft),
        ("Arr", arr.icao, arr_cond, min_arr_ceiling_ft),
    ]:
        if cond is None:
            continue
        cat = cond.flight_category

        # LIFR is concerning for IFR
        if cat == FlightCategory.LIFR:
            # Check against minimum ceiling
            if cond.ceiling_ft is not None and cond.ceiling_ft < min_ceil:
                worst = _worst_status(worst, AdvisoryStatus.RED)
                parts.append(
                    f"{label} {icao} LIFR ceiling {cond.ceiling_ft}ft "
                    f"< {int(min_ceil)}ft min"
                )
            else:
                worst = _worst_status(worst, AdvisoryStatus.AMBER)
                parts.append(f"{label} {icao} LIFR")

    detail = " | ".join(parts) if parts else ""
    return worst, detail


def _check_enroute_hazards(
    ctx: RouteContext,
    model: str,
    convective_min_risk_idx: int,
    cruise_altitude_ft: float,
    icing_altitude_buffer_ft: float,
) -> tuple[int, int, int, int, ConvectiveRisk]:
    """Check en-route icing and convective hazards in a single pass.

    Uses the same clearance-based icing check as the FIKI advisory:
    a point counts as "icing affected" only when an icing zone is within
    *icing_altitude_buffer_ft* of cruise altitude.

    Returns (total, affected, icing_count, conv_count, worst_conv_risk).
    - affected: number of unique points with icing OR convective risk
    - icing_count / conv_count: individual counts for detail messages
    """
    total = 0
    affected = 0
    icing_count = 0
    conv_count = 0
    worst_conv_risk = ConvectiveRisk.NONE

    for rpa in ctx.analyses:
        sounding = rpa.sounding.get(model)
        if sounding is None:
            continue
        total += 1

        has_icing = (
            bool(sounding.icing_zones)
            and min_icing_clearance(sounding.icing_zones, cruise_altitude_ft)
            < icing_altitude_buffer_ft
        )
        has_convective = False

        conv = sounding.convective
        if conv is not None:
            risk_idx = _CONVECTIVE_SEVERITY_INDEX.get(conv.risk_level, 0)
            if risk_idx >= convective_min_risk_idx:
                has_convective = True
                if risk_idx > _CONVECTIVE_SEVERITY_INDEX.get(worst_conv_risk, 0):
                    worst_conv_risk = conv.risk_level

        if has_icing:
            icing_count += 1
        if has_convective:
            conv_count += 1
        if has_icing or has_convective:
            affected += 1

    return total, affected, icing_count, conv_count, worst_conv_risk


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
                    default=20,
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
                    default=50,
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
        icing_pct_amber = params.get("icing_pct_amber", 15)
        icing_pct_red = params.get("icing_pct_red", 30)
        icing_altitude_buffer_ft = params.get("icing_altitude_buffer_ft", 2000)
        convective_min_risk = int(params.get("convective_min_risk", 3))
        convective_pct_red = params.get("convective_pct_red", 10)

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            # 1. Airport conditions
            airport_status, airport_detail = _check_airport_ifr(
                ctx, model, min_dep_ceiling_ft, min_arr_ceiling_ft
            )

            # 2. En-route hazards (single pass — no double-counting)
            total, affected, icing_count, conv_count, worst_conv_risk = (
                _check_enroute_hazards(
                    ctx, model, convective_min_risk,
                    ctx.cruise_altitude_ft, icing_altitude_buffer_ft,
                )
            )

            if (
                total == 0
                and airport_status == AdvisoryStatus.GREEN
                and not airport_detail
            ):
                per_model.append(ModelAdvisoryResult.build(
                    model=model, status=AdvisoryStatus.UNAVAILABLE,
                    detail="No data", affected=0, total=0,
                    total_distance_nm=ctx.total_distance_nm,
                ))
                continue

            # 3. Determine icing status
            icing_status = AdvisoryStatus.GREEN
            icing_detail = ""
            if total > 0 and icing_count > 0:
                icing_pct = 100.0 * icing_count / total
                if icing_pct >= icing_pct_red:
                    icing_status = AdvisoryStatus.RED
                elif icing_pct >= icing_pct_amber:
                    icing_status = AdvisoryStatus.AMBER
                if icing_status != AdvisoryStatus.GREEN:
                    icing_detail = (
                        f"Icing over "
                        f"{format_extent(icing_count, total, ctx.total_distance_nm)}"
                    )

            # 4. Determine convective status
            conv_status = AdvisoryStatus.GREEN
            conv_detail = ""
            if total > 0 and conv_count > 0:
                conv_pct = 100.0 * conv_count / total
                # HIGH/EXTREME at any point is always red
                if worst_conv_risk in (ConvectiveRisk.HIGH, ConvectiveRisk.EXTREME):
                    conv_status = AdvisoryStatus.RED
                elif conv_pct >= convective_pct_red:
                    conv_status = AdvisoryStatus.RED
                else:
                    conv_status = AdvisoryStatus.AMBER
                conv_detail = (
                    f"{worst_conv_risk.value.upper()} convective over "
                    f"{format_extent(conv_count, total, ctx.total_distance_nm)}"
                )

            # 5. Combine all factors
            status = _worst_status(airport_status, icing_status, conv_status)

            detail_parts = []
            if airport_detail:
                detail_parts.append(airport_detail)
            if icing_detail:
                detail_parts.append(icing_detail)
            if conv_detail:
                detail_parts.append(conv_detail)

            if not detail_parts:
                detail = "IFR conditions acceptable throughout"
            else:
                detail = " | ".join(detail_parts)

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("ifr_feasibility", per_model, params)
