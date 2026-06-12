"""Low-level wind shear (LLWS) advisory evaluator.

Approach/departure wind shear from the vertical wind structure in the lowest
~1000 m at the departure and arrival airports — the hazard AirportWind cannot
see, because it only reads the 10 m surface wind against runway geometry.

The classic GA case is the nocturnal low-level jet: calm surface wind under a
surface-based inversion with 30+ kt just above it. The surface wind (and the
crosswind advisory) look benign; the approach then crosses the shear layer
with airspeed fluctuations at a few hundred feet AGL.

Two criteria per airport per model (OR logic):
- **0–1 km bulk shear** (already computed per sounding) against amber/red
  thresholds. A surface-based inversion is reported in the detail when
  present — it concentrates the shear in a thin layer (decoupled flow).
- **Gust factor** (gust − sustained from airport conditions): a large spread
  is surface-level shear/turbulence the sustained-wind grading misses.
"""

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
    RoutePointAnalysis,
    SoundingAnalysis,
)


def _surface_inversion_top_ft(sounding: SoundingAnalysis) -> float | None:
    """Top altitude of a surface-based inversion, or None."""
    for inv in sounding.inversion_layers:
        if inv.surface_based:
            return inv.top_ft
    return None


def _classify_llws(
    shear_kt: float | None,
    gust_factor_kt: float | None,
    shear_amber_kt: float,
    shear_red_kt: float,
    gust_factor_amber_kt: float,
) -> AdvisoryStatus | None:
    """Grade one airport. None when no input is available at all."""
    if shear_kt is None and gust_factor_kt is None:
        return None
    status = AdvisoryStatus.GREEN
    if shear_kt is not None:
        if shear_kt >= shear_red_kt:
            return AdvisoryStatus.RED
        if shear_kt >= shear_amber_kt:
            status = AdvisoryStatus.AMBER
    if gust_factor_kt is not None and gust_factor_kt >= gust_factor_amber_kt:
        status = AdvisoryStatus.worst([status, AdvisoryStatus.AMBER])
    return status


@register
class LLWSEvaluator:
    """Evaluates low-level wind shear at departure and arrival airports."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="llws",
            name="Low-Level Wind Shear",
            short_description="Wind shear in the lowest 1000m at departure and arrival",
            description=(
                "Grades approach/departure wind shear from the vertical wind "
                "structure at the airports: 0–1km bulk shear from the sounding "
                "(catches the nocturnal low-level jet — calm surface wind under "
                "an inversion with strong flow just above, invisible to the "
                "surface-wind advisory) and the gust factor (gust minus "
                "sustained). Expect airspeed fluctuations crossing the shear "
                "layer on final; consider a speed additive or different timing."
            ),
            category="airport",
            parameters=[
                AdvisoryParameterDef(
                    key="shear_amber_kt",
                    label="Amber 0-1km shear",
                    description="0-1km bulk shear above this triggers amber",
                    type="speed",
                    unit="kt",
                    default=20,
                    min=10,
                    max=40,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="shear_red_kt",
                    label="Red 0-1km shear",
                    description="0-1km bulk shear above this triggers red",
                    type="speed",
                    unit="kt",
                    default=30,
                    min=20,
                    max=60,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="gust_factor_amber_kt",
                    label="Amber gust factor",
                    description=(
                        "Gust minus sustained wind above this triggers amber "
                        "(surface-level shear/turbulence)"
                    ),
                    type="speed",
                    unit="kt",
                    default=15,
                    min=5,
                    max=30,
                    step=1,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        shear_amber_kt = params.get("shear_amber_kt", 20)
        shear_red_kt = params.get("shear_red_kt", 30)
        gust_factor_amber_kt = params.get("gust_factor_amber_kt", 15)

        per_model: list[ModelAdvisoryResult] = []

        if not ctx.analyses:
            return RouteAdvisoryResult.from_per_model("llws", [], params)

        dep_rpa: RoutePointAnalysis = ctx.analyses[0]
        arr_rpa: RoutePointAnalysis = ctx.analyses[-1]
        dep_icao = (
            ctx.airport_conditions.departure.icao if ctx.airport_conditions else "DEP"
        )
        arr_icao = (
            ctx.airport_conditions.arrival.icao if ctx.airport_conditions else "ARR"
        )

        for model in ctx.models:
            loc = ctx.locale
            parts: list[str] = []
            worst: AdvisoryStatus | None = None

            for label_key, icao, rpa, summary in [
                ("airport.dep", dep_icao, dep_rpa,
                 ctx.airport_conditions.departure if ctx.airport_conditions else None),
                ("airport.arr", arr_icao, arr_rpa,
                 ctx.airport_conditions.arrival if ctx.airport_conditions else None),
            ]:
                sounding = rpa.sounding.get(model)
                shear_kt = (
                    sounding.indices.bulk_shear_0_1km_kt
                    if sounding is not None and sounding.indices is not None
                    else None
                )

                gust_factor_kt: float | None = None
                cond = summary.condition_for_model(model) if summary else None
                if (
                    cond is not None
                    and cond.wind_gust_kt is not None
                    and cond.wind_speed_kt is not None
                ):
                    gust_factor_kt = max(cond.wind_gust_kt - cond.wind_speed_kt, 0.0)

                status = _classify_llws(
                    shear_kt, gust_factor_kt,
                    shear_amber_kt, shear_red_kt, gust_factor_amber_kt,
                )
                if status is None:
                    continue

                desc: list[str] = []
                if shear_kt is not None:
                    desc.append(f"0-1km shear {shear_kt:.0f}kt")
                inv_top = (
                    _surface_inversion_top_ft(sounding) if sounding is not None else None
                )
                if inv_top is not None and shear_kt is not None and shear_kt >= shear_amber_kt:
                    desc.append(f"surface inversion to {inv_top:.0f}ft")
                if gust_factor_kt is not None and gust_factor_kt >= gust_factor_amber_kt:
                    desc.append(f"gust factor {gust_factor_kt:.0f}kt")

                label = adv_t(label_key, loc)
                parts.append(f"{label} {icao}: {', '.join(desc) if desc else 'no shear signal'}")
                worst = status if worst is None else AdvisoryStatus.worst([worst, status])

            if worst is None:
                per_model.append(ModelAdvisoryResult.build(
                    model=model, status=AdvisoryStatus.UNAVAILABLE,
                    detail=adv_t("no_data", loc), affected=0, total=0,
                    total_distance_nm=ctx.total_distance_nm,
                ))
                continue

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=worst, detail=" | ".join(parts),
                affected=1 if worst != AdvisoryStatus.GREEN else 0,
                total=1,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("llws", per_model, params)
