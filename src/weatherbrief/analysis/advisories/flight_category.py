"""Airport weather (flight category + terminal convective) advisory evaluator.

Besides ceiling/visibility category, this grades convective risk in the
terminal area: the en-route convective advisory dilutes a single cell by
percentage-of-route (one MODERATE cell over the destination is ~5% of a
20-point route → GREEN), but at the airports a deviation is not an option —
the cell must not be there at ETD/ETA. Terminal convection is therefore
graded per airport with no coverage threshold: MODERATE within the terminal
radius is AMBER, HIGH/EXTREME is RED, and no altitude filter applies (climb
and approach traverse every level).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.evidence import build_non_spatial_result
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
from weatherbrief.models.airport_conditions import AirportModelCondition

_CONV_ORDER = [
    ConvectiveRisk.NONE,
    ConvectiveRisk.MARGINAL,
    ConvectiveRisk.LOW,
    ConvectiveRisk.MODERATE,
    ConvectiveRisk.HIGH,
    ConvectiveRisk.EXTREME,
]
# Severity rank for O(1) comparison — avoids repeated _CONV_ORDER.index()
# scans across models × route points × two airports per call.
_CONV_RANK: dict[ConvectiveRisk, int] = {r: i for i, r in enumerate(_CONV_ORDER)}


@dataclass(frozen=True)
class _TerminalConvectiveAssessment:
    risk: ConvectiveRisk
    any_evaluated: bool
    all_evaluated: bool


def _terminal_convective_risk(
    ctx: RouteContext,
    model: str,
    end: Literal["dep", "arr"],
    radius_nm: float,
) -> _TerminalConvectiveAssessment:
    """Worst convective risk within *radius_nm* of one route end ("dep"/"arr")."""
    worst = ConvectiveRisk.NONE
    expected_points = 0
    evaluated_points = 0
    for rpa in ctx.analyses:
        dist = rpa.distance_from_origin_nm
        in_terminal = (
            dist <= radius_nm if end == "dep"
            else dist >= ctx.total_distance_nm - radius_nm
        )
        if not in_terminal:
            continue
        expected_points += 1
        sounding = rpa.sounding.get(model)
        conv = sounding.convective if sounding is not None else None
        if conv is None:
            continue
        evaluated_points += 1
        if _CONV_RANK[conv.risk_level] > _CONV_RANK[worst]:
            worst = conv.risk_level
    return _TerminalConvectiveAssessment(
        risk=worst,
        any_evaluated=evaluated_points > 0,
        all_evaluated=(
            expected_points > 0 and evaluated_points == expected_points
        ),
    )


def _terminal_convective_status(risk: ConvectiveRisk) -> AdvisoryStatus:
    """No coverage threshold at the terminal: MODERATE→AMBER, HIGH+→RED."""
    if risk in (ConvectiveRisk.HIGH, ConvectiveRisk.EXTREME):
        return AdvisoryStatus.RED
    if risk == ConvectiveRisk.MODERATE:
        return AdvisoryStatus.AMBER
    return AdvisoryStatus.GREEN


def _classify_conditions(
    cond: AirportModelCondition,
    amber_ceiling_ft: float,
    amber_vis_sm: float,
    red_ceiling_ft: float,
    red_vis_sm: float,
) -> AdvisoryStatus:
    """Classify airport conditions against ceiling/visibility thresholds."""
    ceiling = cond.ceiling_ft
    vis = cond.visibility_sm

    # RED if either ceiling or visibility is below the red threshold
    if (ceiling is not None and ceiling < red_ceiling_ft) or (
        vis is not None and vis < red_vis_sm
    ):
        return AdvisoryStatus.RED

    # AMBER if either is below the amber threshold
    if (ceiling is not None and ceiling < amber_ceiling_ft) or (
        vis is not None and vis < amber_vis_sm
    ):
        return AdvisoryStatus.AMBER

    return AdvisoryStatus.GREEN


@register
class FlightCategoryEvaluator:
    """Evaluates flight category at departure and arrival airports."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="flight_category",
            name="Airport Weather",
            short_description="Flight category and convective risk at departure and arrival",
            description=(
                "Checks visibility and ceiling at departure and arrival airports "
                "against configurable thresholds. Defaults match standard MVFR/IFR "
                "boundaries: amber when ceiling < 3000ft or visibility < 5sm, "
                "red when ceiling < 1000ft or visibility < 3sm. Also grades "
                "convective risk within the terminal radius of each airport with "
                "no coverage dilution — a deviation is not an option on climb-out "
                "or approach, so MODERATE convective risk near either airport is "
                "amber and HIGH or above is red, regardless of how small a "
                "fraction of the route it covers."
            ),
            category="airport",
            timing_class="cheap",
            timing_hint=True,
            parameters=[
                AdvisoryParameterDef(
                    key="amber_ceiling_ft",
                    label="Amber ceiling",
                    description="Ceiling below this triggers amber (default: MVFR boundary)",
                    type="altitude",
                    unit="ft",
                    default=3000,
                    min=500,
                    max=5000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="amber_vis_sm",
                    label="Amber visibility",
                    description="Visibility below this triggers amber (default: MVFR boundary)",
                    type="number",
                    unit="sm",
                    default=5,
                    min=1,
                    max=10,
                    step=1,
                ),
                AdvisoryParameterDef(
                    key="red_ceiling_ft",
                    label="Red ceiling",
                    description="Ceiling below this triggers red (default: IFR boundary)",
                    type="altitude",
                    unit="ft",
                    default=1000,
                    min=100,
                    max=3000,
                    step=100,
                ),
                AdvisoryParameterDef(
                    key="red_vis_sm",
                    label="Red visibility",
                    description="Visibility below this triggers red (default: IFR boundary)",
                    type="number",
                    unit="sm",
                    default=3,
                    min=0.5,
                    max=5,
                    step=0.5,
                ),
                AdvisoryParameterDef(
                    key="conv_radius_nm",
                    label="Terminal radius",
                    description=(
                        "Convective risk is checked within this distance of "
                        "departure and arrival (MODERATE = amber, HIGH+ = red)"
                    ),
                    type="number",
                    unit="nm",
                    default=25,
                    min=10,
                    max=50,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        amber_ceiling_ft = params.get("amber_ceiling_ft", 3000)
        amber_vis_sm = params.get("amber_vis_sm", 5)
        red_ceiling_ft = params.get("red_ceiling_ft", 1000)
        red_vis_sm = params.get("red_vis_sm", 3)
        conv_radius_nm = params.get("conv_radius_nm", 25)

        per_model: list[ModelAdvisoryResult] = []

        if ctx.airport_conditions is None:
            per_model = [
                build_non_spatial_result(
                    model=model,
                    status=AdvisoryStatus.UNAVAILABLE,
                    detail=adv_t("no_data", ctx.locale),
                    unavailable_detail=adv_t("no_data", ctx.locale),
                    expected_entities={"departure", "arrival"},
                    evaluated_entities=set(),
                    complete_entities=set(),
                    affected_entities=set(),
                    primary_method_id="airport_conditions",
                )
                for model in ctx.models
            ]
            return RouteAdvisoryResult.from_per_model(
                "flight_category", per_model, params,
            )

        dep = ctx.airport_conditions.departure
        arr = ctx.airport_conditions.arrival

        for model in ctx.models:
            dep_cond = dep.condition_for_model(model)
            arr_cond = arr.condition_for_model(model)

            loc = ctx.locale
            parts = []
            statuses: list[AdvisoryStatus] = []
            evaluated: set[str] = set()
            complete: set[str] = set()
            affected: set[str] = set()
            for entity, label_key, icao, cond, end in [
                ("departure", "airport.dep", dep.icao, dep_cond, "dep"),
                ("arrival", "airport.arr", arr.icao, arr_cond, "arr"),
            ]:
                ceiling_available = cond is not None and (
                    cond.ceiling_evaluated or cond.ceiling_ft is not None
                )
                visibility_available = (
                    cond is not None and cond.visibility_sm is not None
                )
                condition_available = ceiling_available or visibility_available
                conv = _terminal_convective_risk(
                    ctx,
                    model,
                    end,
                    conv_radius_nm,
                )
                if not condition_available and not conv.any_evaluated:
                    continue
                evaluated.add(entity)
                if (
                    ceiling_available
                    and visibility_available
                    and conv.all_evaluated
                ):
                    complete.add(entity)

                status = AdvisoryStatus.GREEN
                label = adv_t(label_key, loc)
                part = f"{label} {icao}"
                if condition_available:
                    assert cond is not None
                    status = _classify_conditions(
                        cond, amber_ceiling_ft, amber_vis_sm,
                        red_ceiling_ft, red_vis_sm,
                    )
                    part += f": {cond.flight_category.value}"

                conv_status = _terminal_convective_status(conv.risk)
                if conv.any_evaluated and conv_status != AdvisoryStatus.GREEN:
                    part += adv_t(
                        "flight_category.conv", loc,
                        risk=conv.risk.value.upper(),
                    )
                    status = AdvisoryStatus.worst([status, conv_status])

                if condition_available or conv_status != AdvisoryStatus.GREEN:
                    parts.append(part)
                statuses.append(status)
                if status != AdvisoryStatus.GREEN:
                    affected.add(entity)

            detail = " | ".join(parts)
            worst = AdvisoryStatus.worst(statuses)

            per_model.append(
                build_non_spatial_result(
                    model=model,
                    status=worst,
                    detail=detail,
                    unavailable_detail=adv_t(
                        "no_data" if not evaluated else "partial_data",
                        loc,
                    ),
                    expected_entities={"departure", "arrival"},
                    evaluated_entities=evaluated,
                    complete_entities=complete,
                    affected_entities=affected,
                    primary_method_id="airport_conditions",
                )
            )

        return RouteAdvisoryResult.from_per_model("flight_category", per_model, params)
