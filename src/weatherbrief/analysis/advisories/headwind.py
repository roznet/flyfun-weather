"""Headwind / trip-impact advisory — winds aloft at cruise along the route.

The cruise-level headwind component is computed at every route point
(``RoutePointAnalysis.wind_components``) and drawn on the route graph, but
nothing aggregated it into the briefing. For GA fuel planning the question is
simple: how much longer does this trip take than still air, and is today a
day to reconsider altitude or add a fuel stop?

Informational by design — wind is a planning factor, not a hazard — so the
grade is GREEN/AMBER on the route-average headwind (RED only for genuinely
brutal days). Tailwinds report the time gained and stay GREEN.

Altitude-aware: winds are read from the cross-section at the context's cruise
altitude (so the altitude slider and altitude table show how winds change
with level — often the actual decision being made), falling back to the
precomputed cruise wind components on packs without cross-section winds.

The trip-time estimate needs a cruise TAS, resolved per flight (no user knob):
the aircraft cruise speed (IAS, aircraft→profile via
``weatherbrief.atmo.resolve_cruise_speed_ias``) converted to TAS at the
evaluated cruise altitude — so the minutes reflect the actual aeroplane and the
altitude table shows TAS rising with height; failing that, the flight's own
planned speed (distance ÷ duration); failing that, a generic fallback so the
estimate can never divide by an absent speed. The headwind numbers themselves
are model truth regardless of the TAS used.
"""

from __future__ import annotations

import math

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import wind_at_altitude
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.atmo import ias_to_tas_isa
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)

# Floor on per-point groundspeed for the time integral — keeps the estimate
# finite if winds put the headwind near TAS.
_MIN_GS_KT = 30.0

# Last-resort cruise TAS when a flight has neither an aircraft/profile speed nor
# a usable planned duration (see _resolve_cruise_tas). A generic light-GA cruise.
_DEFAULT_TAS_KT = 110.0


def _headwind_component(speed_kt: float, direction_deg: float, track_deg: float) -> float:
    """Along-track wind component: positive = headwind, negative = tailwind."""
    return speed_kt * math.cos(math.radians(direction_deg - track_deg))


def _resolve_cruise_tas(ctx: RouteContext) -> float:
    """Cruise TAS (kt) for the trip-time estimate — always returns a value.

    Precedence: the flight's aircraft/profile cruise speed (IAS → TAS at the
    *evaluated* cruise altitude, so the altitude table reflects TAS rising with
    height) → the flight's own planned speed (distance ÷ duration, already a
    TAS) → a generic fallback.
    """
    if ctx.cruise_speed_ias_kt:
        return ias_to_tas_isa(ctx.cruise_speed_ias_kt, ctx.cruise_altitude_ft)
    if ctx.flight_duration_hours and ctx.flight_duration_hours > 0 and ctx.total_distance_nm > 0:
        return ctx.total_distance_nm / ctx.flight_duration_hours
    return _DEFAULT_TAS_KT


@register
class HeadwindEvaluator:
    """Aggregates cruise-level headwind into a trip-impact advisory."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="headwind",
            name="Winds Aloft",
            short_description="Headwind at cruise and trip-time impact",
            description=(
                "Averages the cruise-level headwind component along the route "
                "and estimates the trip-time impact versus still air, using your "
                "aircraft's cruise speed (or the flight's planned speed). Mostly "
                "informational: amber when the average headwind exceeds the "
                "threshold (fuel-planning attention), red only for extreme "
                "days. Tailwinds report the time gained. Reads winds at the "
                "evaluated cruise altitude, so the altitude table shows how "
                "the wind trade changes with level."
            ),
            category="wind",
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="mean_amber_kt",
                    label="Avg headwind (amber)",
                    description="Route-average headwind above this triggers amber",
                    type="speed",
                    unit="kt",
                    default=20,
                    min=5,
                    max=50,
                    step=5,
                    audience="pilot",
                ),
                AdvisoryParameterDef(
                    key="mean_red_kt",
                    label="Avg headwind (red)",
                    description="Route-average headwind above this triggers red",
                    type="speed",
                    unit="kt",
                    default=40,
                    min=20,
                    max=80,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        tas_kt = _resolve_cruise_tas(ctx)
        mean_amber_kt = params.get("mean_amber_kt", 20)
        mean_red_kt = params.get("mean_red_kt", 40)

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            headwinds: list[float] = []
            affected = 0

            for rpa in ctx.analyses:
                hw: float | None = None
                wind = wind_at_altitude(
                    ctx.cross_sections, model, rpa.point_index,
                    ctx.cruise_altitude_ft, rpa.forecast_hour,
                )
                if wind is not None:
                    speed_kt, direction_deg = wind
                    hw = _headwind_component(speed_kt, direction_deg, rpa.track_deg)
                else:
                    # Pack without cross-section winds: precomputed component
                    # at the pack's original cruise level.
                    wc = rpa.wind_components.get(model)
                    if wc is not None:
                        hw = wc.headwind_kt
                if hw is None:
                    continue
                headwinds.append(hw)
                if hw >= mean_amber_kt:
                    affected += 1

            loc = ctx.locale
            total = len(headwinds)
            if total == 0:
                per_model.append(ModelAdvisoryResult.build(
                    model=model, status=AdvisoryStatus.UNAVAILABLE,
                    detail=adv_t("no_data", loc), affected=0, total=0,
                    total_distance_nm=ctx.total_distance_nm,
                ))
                continue

            mean_hw = sum(headwinds) / total
            max_hw = max(headwinds)

            # Trip time vs still air: equal-weight segments at per-point GS.
            # Approximation — route points are not always evenly spaced (denser
            # near waypoints), so using average spacing skews the time integral
            # when point density correlates with wind speed. Acceptable for an
            # informational advisory; a per-segment distance would be exact.
            seg_nm = ctx.total_distance_nm / total
            still_min = 60.0 * ctx.total_distance_nm / tas_kt
            wind_min = sum(
                60.0 * seg_nm / max(tas_kt - hw, _MIN_GS_KT) for hw in headwinds
            )
            delta_min = wind_min - still_min
            delta_pct = 100.0 * delta_min / still_min if still_min > 0 else 0.0

            if mean_hw >= mean_red_kt:
                status = AdvisoryStatus.RED
            elif mean_hw >= mean_amber_kt:
                status = AdvisoryStatus.AMBER
            else:
                status = AdvisoryStatus.GREEN

            if mean_hw <= -3:
                detail = adv_t(
                    "headwind.tailwind", loc,
                    mean=round(-mean_hw), delta=round(-delta_min),
                )
            elif abs(mean_hw) < 3 and abs(delta_min) < 3:
                detail = adv_t("headwind.neutral", loc)
            else:
                detail = adv_t(
                    "headwind.summary", loc,
                    mean=round(mean_hw), max=round(max_hw),
                    delta=round(delta_min), pct=round(delta_pct),
                )

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("headwind", per_model, params)
