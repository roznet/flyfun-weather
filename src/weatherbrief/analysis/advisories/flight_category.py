"""Airport weather (flight category) advisory evaluator."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryStatus,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)
from weatherbrief.models.airport_conditions import FlightCategory

_CAT_TO_STATUS = {
    FlightCategory.VFR: AdvisoryStatus.GREEN,
    FlightCategory.MVFR: AdvisoryStatus.AMBER,
    FlightCategory.IFR: AdvisoryStatus.RED,
    FlightCategory.LIFR: AdvisoryStatus.RED,
}


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
                "to determine flight category (VFR, MVFR, IFR, LIFR). "
                "Uses standard aviation thresholds: VFR requires ceiling >= 3000ft "
                "and visibility >= 5sm. Categories are based on the most restrictive "
                "condition at either airport."
            ),
            category="airport",
            parameters=[],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        per_model: list[ModelAdvisoryResult] = []

        if ctx.airport_conditions is None:
            return RouteAdvisoryResult.from_per_model("flight_category", [], params)

        dep = ctx.airport_conditions.departure
        arr = ctx.airport_conditions.arrival

        for model in ctx.models:
            dep_cond = dep.condition_for_model(model)
            arr_cond = arr.condition_for_model(model)

            if dep_cond is None and arr_cond is None:
                per_model.append(ModelAdvisoryResult.build(
                    model=model, status=AdvisoryStatus.UNAVAILABLE,
                    detail="No data", affected=0, total=0,
                    total_distance_nm=ctx.total_distance_nm,
                ))
                continue

            cats = []
            parts = []
            if dep_cond:
                cats.append(dep_cond.flight_category)
                parts.append(f"Dep {dep.icao}: {dep_cond.flight_category.value.upper()}")
            if arr_cond:
                cats.append(arr_cond.flight_category)
                parts.append(f"Arr {arr.icao}: {arr_cond.flight_category.value.upper()}")

            worst_cat = FlightCategory.worst(cats)
            status = _CAT_TO_STATUS[worst_cat]
            detail = " | ".join(parts)

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=1 if status != AdvisoryStatus.GREEN else 0,
                total=1,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("flight_category", per_model, params)
