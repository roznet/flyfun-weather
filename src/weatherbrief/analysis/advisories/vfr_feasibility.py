"""VFR feasibility advisory — overall VFR flight viability assessment.

Composite advisory combining airport conditions, en-route cloud clearance,
and VMC compliance into a single go/no-go style assessment for VFR flights.
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import format_extent
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    CloudCoverage,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)
from weatherbrief.models.airport_conditions import FlightCategory


def _worst_status(*statuses: AdvisoryStatus) -> AdvisoryStatus:
    """Return the most severe status from the given values."""
    return AdvisoryStatus.worst(list(statuses))


def _check_airport_vfr(
    ctx: RouteContext,
    model: str,
) -> tuple[AdvisoryStatus, str]:
    """Check departure and arrival airport conditions for VFR feasibility.

    Uses the flight category (VFR/MVFR/IFR/LIFR) which already encodes
    ceiling and visibility thresholds per aviation standards.

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

    for label, icao, cond in [("Dep", dep.icao, dep_cond), ("Arr", arr.icao, arr_cond)]:
        if cond is None:
            continue
        cat = cond.flight_category
        if cat in (FlightCategory.IFR, FlightCategory.LIFR):
            worst = _worst_status(worst, AdvisoryStatus.RED)
            parts.append(f"{label} {icao} {cat.value.upper()}")
        elif cat == FlightCategory.MVFR:
            worst = _worst_status(worst, AdvisoryStatus.AMBER)
            parts.append(f"{label} {icao} MVFR")

    detail = " | ".join(parts) if parts else ""
    return worst, detail


def _check_enroute_vfr(
    ctx: RouteContext,
    model: str,
    cloud_clearance_ft: float,
) -> tuple[int, int, int, int]:
    """Check en-route cloud clearance for VFR.

    Returns (total, imc_count, marginal_count, clear_count).
    - imc_count: points where cruise is inside BKN/OVC cloud
    - marginal_count: points where cloud clearance < threshold (but not in cloud)
    """
    total = 0
    imc_count = 0
    marginal_count = 0
    clear_count = 0
    cruise = ctx.cruise_altitude_ft

    for rpa in ctx.analyses:
        sounding = rpa.sounding.get(model)
        if sounding is None:
            continue
        total += 1

        in_cloud = False
        marginal = False

        for cl in sounding.cloud_layers:
            if cl.coverage not in (CloudCoverage.BKN, CloudCoverage.OVC):
                continue
            # Check if cruise altitude is inside the cloud layer
            if cl.base_ft <= cruise <= cl.top_ft:
                in_cloud = True
                break
            # Check vertical clearance from cloud base or top
            dist_to_base = abs(cruise - cl.base_ft)
            dist_to_top = abs(cruise - cl.top_ft)
            min_dist = min(dist_to_base, dist_to_top)
            if min_dist < cloud_clearance_ft:
                marginal = True

        if in_cloud:
            imc_count += 1
        elif marginal:
            marginal_count += 1
        else:
            clear_count += 1

    return total, imc_count, marginal_count, clear_count


@register
class VFRFeasibilityEvaluator:
    """Evaluates overall feasibility of VFR flight along the route.

    Combines airport flight category, en-route cloud clearance, and VMC
    compliance into a single advisory. GREEN means VFR conditions throughout;
    AMBER means marginal conditions; RED means IFR conditions detected.
    """

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="vfr_feasibility",
            name="VFR Feasibility",
            short_description="Overall VFR flight viability",
            description=(
                "Composite assessment of VFR flight feasibility. Checks airport "
                "conditions (VFR/MVFR/IFR), en-route cloud clearance relative to "
                "cruise altitude, and minimum cloud separation. RED indicates IFR "
                "conditions or inadequate cloud clearance along a significant "
                "portion of the route."
            ),
            category="flight_rules",
            parameters=[
                AdvisoryParameterDef(
                    key="cloud_clearance_ft",
                    label="Cloud clearance",
                    description="Minimum vertical separation from BKN/OVC cloud layers",
                    type="altitude",
                    unit="ft",
                    default=1000,
                    min=500,
                    max=2000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="imc_pct_amber",
                    label="IMC % (amber)",
                    description="Route percentage in IMC or marginal clearance for amber",
                    type="percent",
                    unit="%",
                    default=15,
                    min=5,
                    max=50,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="imc_pct_red",
                    label="IMC % (red)",
                    description="Route percentage in IMC for red",
                    type="percent",
                    unit="%",
                    default=30,
                    min=10,
                    max=80,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        cloud_clearance_ft = params.get("cloud_clearance_ft", 1000)
        imc_pct_amber = params.get("imc_pct_amber", 15)
        imc_pct_red = params.get("imc_pct_red", 30)

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            # 1. Airport conditions
            airport_status, airport_detail = _check_airport_vfr(ctx, model)

            # 2. En-route cloud clearance
            total, imc_count, marginal_count, _ = _check_enroute_vfr(
                ctx, model, cloud_clearance_ft
            )

            if total == 0 and airport_status == AdvisoryStatus.GREEN and not airport_detail:
                per_model.append(ModelAdvisoryResult.build(
                    model=model, status=AdvisoryStatus.UNAVAILABLE,
                    detail="No data", affected=0, total=0,
                    total_distance_nm=ctx.total_distance_nm,
                ))
                continue

            # 3. Determine en-route status
            affected = imc_count + marginal_count
            enroute_status = AdvisoryStatus.GREEN
            enroute_detail = ""

            if total > 0:
                imc_pct = 100.0 * imc_count / total
                affected_pct = 100.0 * affected / total

                ext = format_extent(affected, total, ctx.total_distance_nm)

                if imc_pct >= imc_pct_red:
                    enroute_status = AdvisoryStatus.RED
                    enroute_detail = f"IMC over {ext}"
                elif affected_pct >= imc_pct_amber:
                    enroute_status = AdvisoryStatus.AMBER
                    if marginal_count > 0 and imc_count > 0:
                        enroute_detail = f"IMC/marginal clearance over {ext}"
                    elif imc_count > 0:
                        enroute_detail = f"IMC over {ext}"
                    else:
                        enroute_detail = f"Marginal cloud clearance over {ext}"
                elif affected > 0:
                    enroute_detail = (
                        f"Minor clearance issues over "
                        f"{format_extent(affected, total, ctx.total_distance_nm)}"
                    )

            # 4. Combine airport + en-route
            status = _worst_status(airport_status, enroute_status)

            detail_parts = []
            if airport_detail:
                detail_parts.append(airport_detail)
            if enroute_detail:
                detail_parts.append(enroute_detail)

            if not detail_parts:
                if total > 0:
                    detail = "VFR conditions throughout"
                else:
                    detail = "Airports VFR, no en-route data"
            else:
                detail = " | ".join(detail_parts)

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("vfr_feasibility", per_model, params)
