"""Density altitude advisory evaluator.

High density altitude is a classic GA performance trap: a hot afternoon at a
moderate-elevation field can silently cost a large fraction of climb rate and
takeoff distance. This evaluator computes density altitude at the departure
and arrival airports per model, from the forecast surface temperature and QNH
at the expected departure/arrival times plus the field elevation from the
route's terrain profile.

Two criteria, either of which can trigger (OR logic):
- absolute density altitude (performance charts are DA-based);
- DA minus field elevation (the "surprise factor" — how much worse the air
  is than the field elevation suggests, relevant even at low-lying airports).
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import terrain_at_distance
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)

# ISA constants
_ISA_SEA_LEVEL_C = 15.0
_ISA_LAPSE_C_PER_1000FT = 1.9812
_ISA_QNH_HPA = 1013.25
_FT_PER_HPA = 27.3  # pressure altitude correction near sea level
_DA_FT_PER_DEG_C = 118.8  # density altitude per °C of ISA deviation


def compute_density_altitude_ft(
    elevation_ft: float,
    temperature_c: float,
    qnh_hpa: float | None,
) -> float:
    """Density altitude from field elevation, temperature, and QNH.

    ``PA = elev + (1013.25 − QNH) × 27.3``; ``DA = PA + 118.8 × (T − ISA(PA))``.
    Humidity is ignored (small effect, always increases DA slightly).
    When QNH is unknown, pressure altitude falls back to field elevation.
    """
    if qnh_hpa is not None:
        pressure_alt_ft = elevation_ft + (_ISA_QNH_HPA - qnh_hpa) * _FT_PER_HPA
    else:
        pressure_alt_ft = elevation_ft
    isa_temp_c = _ISA_SEA_LEVEL_C - _ISA_LAPSE_C_PER_1000FT * pressure_alt_ft / 1000.0
    return pressure_alt_ft + _DA_FT_PER_DEG_C * (temperature_c - isa_temp_c)


def _classify_da(
    da_ft: float,
    elevation_ft: float,
    da_amber_ft: float,
    da_red_ft: float,
    delta_amber_ft: float,
    delta_red_ft: float,
) -> AdvisoryStatus:
    """Grade density altitude on absolute DA or DA-above-field, whichever is worse."""
    delta = da_ft - elevation_ft
    if da_ft >= da_red_ft or delta >= delta_red_ft:
        return AdvisoryStatus.RED
    if da_ft >= da_amber_ft or delta >= delta_amber_ft:
        return AdvisoryStatus.AMBER
    return AdvisoryStatus.GREEN


@register
class DensityAltitudeEvaluator:
    """Evaluates density altitude at departure and arrival airports."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="density_altitude",
            name="Density Altitude",
            short_description="Aircraft performance at departure and arrival",
            description=(
                "Computes density altitude at the departure and arrival airports "
                "from forecast temperature and QNH at the expected times, using the "
                "field elevation from the route terrain profile. High DA reduces "
                "engine power, propeller efficiency and lift — longer takeoff roll, "
                "weaker climb. Amber/red when DA exceeds the absolute thresholds "
                "(performance charts are DA-based) OR exceeds the field elevation "
                "by the delta thresholds (hot-day performance loss, relevant even "
                "at low-lying airports). Status is the worst of departure and "
                "arrival; unavailable (not green) without forecast temperature or "
                "terrain data."
            ),
            category="airport",
            parameters=[
                AdvisoryParameterDef(
                    key="da_amber_ft",
                    label="Amber density altitude",
                    description="Density altitude above this triggers amber",
                    type="altitude",
                    unit="ft",
                    default=5000,
                    min=2000,
                    max=10000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="da_red_ft",
                    label="Red density altitude",
                    description="Density altitude above this triggers red",
                    type="altitude",
                    unit="ft",
                    default=8000,
                    min=4000,
                    max=14000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="delta_amber_ft",
                    label="Amber DA above field",
                    description="DA exceeding field elevation by this triggers amber",
                    type="altitude",
                    unit="ft",
                    default=3000,
                    min=1000,
                    max=6000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="delta_red_ft",
                    label="Red DA above field",
                    description="DA exceeding field elevation by this triggers red",
                    type="altitude",
                    unit="ft",
                    default=5000,
                    min=2000,
                    max=8000,
                    step=500,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        da_amber_ft = params.get("da_amber_ft", 5000)
        da_red_ft = params.get("da_red_ft", 8000)
        delta_amber_ft = params.get("delta_amber_ft", 3000)
        delta_red_ft = params.get("delta_red_ft", 5000)

        per_model: list[ModelAdvisoryResult] = []

        if ctx.airport_conditions is None:
            return RouteAdvisoryResult.from_per_model("density_altitude", [], params)

        dep = ctx.airport_conditions.departure
        arr = ctx.airport_conditions.arrival

        # Field elevations from the route terrain profile (SRTM at the
        # airport coordinates — close enough to published field elevation
        # for performance purposes).
        dep_elev = terrain_at_distance(ctx.elevation, 0.0)
        arr_elev = terrain_at_distance(ctx.elevation, ctx.total_distance_nm)

        for model in ctx.models:
            loc = ctx.locale
            parts: list[str] = []
            worst: AdvisoryStatus | None = None

            for label_key, icao, cond, elev in [
                ("airport.dep", dep.icao, dep.condition_for_model(model), dep_elev),
                ("airport.arr", arr.icao, arr.condition_for_model(model), arr_elev),
            ]:
                if cond is None or cond.temperature_c is None or elev is None:
                    continue
                da_ft = compute_density_altitude_ft(elev, cond.temperature_c, cond.qnh_hpa)
                status = _classify_da(
                    da_ft, elev, da_amber_ft, da_red_ft, delta_amber_ft, delta_red_ft,
                )
                label = adv_t(label_key, loc)
                parts.append(f"{label} {icao}: DA {da_ft:.0f}ft (field {elev:.0f}ft)")
                worst = status if worst is None else AdvisoryStatus.worst([worst, status])

            if worst is None:
                # No temperature or no terrain data — explicitly unavailable,
                # never silently green.
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

        return RouteAdvisoryResult.from_per_model("density_altitude", per_model, params)
