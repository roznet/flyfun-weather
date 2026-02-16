"""Airport wind advisory evaluator — crosswind and gust assessment."""

from __future__ import annotations

import math

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)


def _format_wind_detail(cond, rwy_id: str) -> str:
    """Format wind like: 230@11G25 RW24 ↓11 →5."""
    from weatherbrief.models.airport_conditions import AirportModelCondition

    if cond.wind_direction_deg is None or cond.wind_speed_kt is None:
        return "calm"

    dir_rounded = round(cond.wind_direction_deg / 10) * 10
    dir_str = f"{dir_rounded:03.0f}"
    spd_str = f"{cond.wind_speed_kt:.0f}"
    gust_str = f"G{cond.wind_gust_kt:.0f}" if cond.wind_gust_kt else ""
    wind_text = f"{dir_str}@{spd_str}{gust_str}"

    if cond.best_runway is None:
        return wind_text

    rwy = cond.best_runway
    hw_arrow = "\u2193" if rwy.headwind_kt >= 0 else "\u2191"
    hw_val = f"{abs(rwy.headwind_kt):.0f}"

    rel = math.radians(cond.wind_direction_deg - rwy.heading_deg)
    xw_arrow = "\u2190" if math.sin(rel) >= 0 else "\u2192"
    xw_val = f"{rwy.crosswind_kt:.0f}"

    return f"{wind_text} RW{rwy_id} {hw_arrow}{hw_val} {xw_arrow}{xw_val}"


def _wind_status(
    crosswind_kt: float | None,
    gust_kt: float | None,
    xwind_green: float,
    xwind_red: float,
    gust_green: float,
    gust_red: float,
) -> AdvisoryStatus:
    """Determine wind status from crosswind and gust against thresholds."""
    status = AdvisoryStatus.GREEN

    if crosswind_kt is not None:
        if crosswind_kt >= xwind_red:
            status = AdvisoryStatus.RED
        elif crosswind_kt >= xwind_green:
            status = AdvisoryStatus.worst([status, AdvisoryStatus.AMBER])

    if gust_kt is not None:
        if gust_kt >= gust_red:
            status = AdvisoryStatus.RED
        elif gust_kt >= gust_green:
            status = AdvisoryStatus.worst([status, AdvisoryStatus.AMBER])

    return status


@register
class AirportWindEvaluator:
    """Evaluates crosswind and gust conditions at departure and arrival."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="airport_wind",
            name="Airport Wind",
            short_description="Crosswind and gusts at airports",
            description=(
                "Checks crosswind on the best runway and wind gusts at departure "
                "and arrival airports. Uses the runway with the lowest crosswind "
                "component. Thresholds are configurable for both crosswind and gusts."
            ),
            category="airport",
            parameters=[
                AdvisoryParameterDef(
                    key="crosswind_green_kt",
                    label="Crosswind (amber)",
                    description="Crosswind threshold for amber status",
                    type="speed", unit="kt",
                    default=15, min=5, max=30, step=5,
                ),
                AdvisoryParameterDef(
                    key="crosswind_red_kt",
                    label="Crosswind (red)",
                    description="Crosswind threshold for red status",
                    type="speed", unit="kt",
                    default=25, min=15, max=40, step=5,
                ),
                AdvisoryParameterDef(
                    key="gust_green_kt",
                    label="Gust (amber)",
                    description="Gust threshold for amber status",
                    type="speed", unit="kt",
                    default=25, min=10, max=40, step=5,
                ),
                AdvisoryParameterDef(
                    key="gust_red_kt",
                    label="Gust (red)",
                    description="Gust threshold for red status",
                    type="speed", unit="kt",
                    default=35, min=20, max=50, step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        xwind_green = params.get("crosswind_green_kt", 15)
        xwind_red = params.get("crosswind_red_kt", 25)
        gust_green = params.get("gust_green_kt", 25)
        gust_red = params.get("gust_red_kt", 35)

        per_model: list[ModelAdvisoryResult] = []

        if ctx.airport_conditions is None:
            return RouteAdvisoryResult.from_per_model("airport_wind", [], params)

        dep = ctx.airport_conditions.departure
        arr = ctx.airport_conditions.arrival

        for model in ctx.models:
            dep_cond = next((c for c in dep.conditions if c.model == model), None)
            arr_cond = next((c for c in arr.conditions if c.model == model), None)

            if dep_cond is None and arr_cond is None:
                per_model.append(ModelAdvisoryResult.build(
                    model=model, status=AdvisoryStatus.UNAVAILABLE,
                    detail="No data", affected=0, total=0,
                    total_distance_nm=ctx.total_distance_nm,
                ))
                continue

            statuses = []
            parts = []

            for label, icao, cond in [("Dep", dep.icao, dep_cond), ("Arr", arr.icao, arr_cond)]:
                if cond is None:
                    continue

                xwind = cond.best_runway.crosswind_kt if cond.best_runway else None
                rwy_id = cond.best_runway.runway_id if cond.best_runway else "N/A"

                s = _wind_status(xwind, cond.wind_gust_kt, xwind_green, xwind_red, gust_green, gust_red)
                statuses.append(s)

                wind_str = _format_wind_detail(cond, rwy_id)
                parts.append(f"{label} {icao}: {wind_str}")

            status = AdvisoryStatus.worst(statuses) if statuses else AdvisoryStatus.UNAVAILABLE
            detail = " | ".join(parts)

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=1 if status != AdvisoryStatus.GREEN else 0,
                total=1,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("airport_wind", per_model, params)
