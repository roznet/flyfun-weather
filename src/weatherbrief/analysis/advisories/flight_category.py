"""Airport weather (flight category) advisory evaluator."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)
from weatherbrief.models.airport_conditions import AirportModelCondition


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
            short_description="Flight category at departure and arrival",
            description=(
                "Checks visibility and ceiling at departure and arrival airports "
                "against configurable thresholds. Defaults match standard MVFR/IFR "
                "boundaries: amber when ceiling < 3000ft or visibility < 5sm, "
                "red when ceiling < 1000ft or visibility < 3sm."
            ),
            category="airport",
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
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        amber_ceiling_ft = params.get("amber_ceiling_ft", 3000)
        amber_vis_sm = params.get("amber_vis_sm", 5)
        red_ceiling_ft = params.get("red_ceiling_ft", 1000)
        red_vis_sm = params.get("red_vis_sm", 3)

        per_model: list[ModelAdvisoryResult] = []

        if ctx.airport_conditions is None:
            return RouteAdvisoryResult.from_per_model("flight_category", [], params)

        dep = ctx.airport_conditions.departure
        arr = ctx.airport_conditions.arrival

        for model in ctx.models:
            dep_cond = dep.condition_for_model(model)
            arr_cond = arr.condition_for_model(model)

            loc = ctx.locale
            if dep_cond is None and arr_cond is None:
                per_model.append(ModelAdvisoryResult.build(
                    model=model, status=AdvisoryStatus.UNAVAILABLE,
                    detail=adv_t("no_data", loc), affected=0, total=0,
                    total_distance_nm=ctx.total_distance_nm,
                ))
                continue

            parts = []
            worst = AdvisoryStatus.GREEN
            for label_key, icao, cond in [
                ("airport.dep", dep.icao, dep_cond),
                ("airport.arr", arr.icao, arr_cond),
            ]:
                if cond is None:
                    continue
                status = _classify_conditions(
                    cond, amber_ceiling_ft, amber_vis_sm,
                    red_ceiling_ft, red_vis_sm,
                )
                cat_label = cond.flight_category.value
                label = adv_t(label_key, loc)
                parts.append(f"{label} {icao}: {cat_label}")
                worst = AdvisoryStatus.worst([worst, status])

            detail = " | ".join(parts)

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=worst, detail=detail,
                affected=1 if worst != AdvisoryStatus.GREEN else 0,
                total=1,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("flight_category", per_model, params)
