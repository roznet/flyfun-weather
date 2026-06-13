"""Mountain wind advisory — wind near terrain, with wave-signature corroboration.

Wind speed near ridge level is a necessary but not sufficient condition for
mountain wave / rotor: the classical ingredients are strong cross-barrier
flow AND a stable layer near ridge top (Scorer parameter decreasing with
height). Speed alone cannot separate "windy ridge" from "rotor day", so this
evaluator corroborates the wind grade with two signatures already computed
per sounding:

- an **inversion layer near ridge top** (the stable layer that supports
  trapped lee waves and rotors beneath them);
- an **OSCILLATING vertical-motion classification** (alternating ascent /
  descent couplets in the omega profile — the model resolving the wave
  itself).

When either signature is present at a strong-wind mountain point, the RED
threshold drops from ``wind_red_kt`` (speed so high it's hazardous
regardless) to ``corroborated_red_kt``. Cross-ridge wind *direction* is
deliberately not assessed: the elevation profile is one-dimensional along
the route, so ridge orientation is unknown — pretending otherwise would be
false precision.
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    format_extent,
    max_terrain_near_point,
    wind_at_altitude,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
    SoundingAnalysis,
    VerticalMotionClass,
)

# Stable-layer search band around ridge top: an inversion this far below to
# this far above the local peak counts as the wave-supporting layer. Classical
# wave theory (Scorer parameter decreasing with height) places the critical
# stable layer at or just above ridge level, so the upper bound is kept tight
# (+2000 ft) to exclude elevated frontal inversions that carry no wave
# mechanism — a wider band false-positives on any day with a mid-level stable
# layer above a ridge. See designs/meteorology-decisions.md §11d.
_INV_BELOW_FT = 1000.0
_INV_ABOVE_FT = 2000.0


def _wave_signatures(
    sounding: SoundingAnalysis | None,
    terrain_ft: float,
) -> set[str]:
    """Wave-supporting signatures at one point: 'inversion' and/or 'oscillating'."""
    sigs: set[str] = set()
    if sounding is None:
        return sigs
    vm = sounding.vertical_motion
    if vm is not None and vm.classification == VerticalMotionClass.OSCILLATING:
        sigs.add("oscillating")
    lo = terrain_ft - _INV_BELOW_FT
    hi = terrain_ft + _INV_ABOVE_FT
    for inv in sounding.inversion_layers:
        if inv.top_ft > lo and inv.base_ft < hi:
            sigs.add("inversion")
            break
    return sigs


@register
class MountainWindEvaluator:
    """Evaluates wind speed near terrain tops for mountain wave/rotor risk."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="mountain_wind",
            name="Mountain Wind",
            short_description="Wind and wave signatures near terrain",
            description=(
                "Evaluates wind speed near terrain tops at points where terrain "
                "exceeds a threshold, corroborated by the classical wave "
                "ingredients: a stable layer (inversion) near ridge top or a "
                "wave-like oscillating vertical-motion profile in the model. "
                "Strong wind alone is amber; very strong wind is red; and "
                "moderately strong wind WITH a wave signature is also red at a "
                "lower threshold — that combination is the rotor-day setup, "
                "not just a windy ridge."
            ),
            category="turbulence",
            parameters=[
                AdvisoryParameterDef(
                    key="terrain_threshold_ft",
                    label="Terrain threshold",
                    description="Only evaluate points where terrain exceeds this altitude",
                    type="altitude",
                    unit="ft",
                    default=3000,
                    min=1000,
                    max=8000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="altitude_margin_ft",
                    label="Altitude margin",
                    description="Check wind within this margin above terrain tops",
                    type="altitude",
                    unit="ft",
                    default=2000,
                    min=500,
                    max=5000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="wind_amber_kt",
                    label="Wind amber (kt)",
                    description="Wind speed above terrain for amber",
                    type="speed",
                    unit="kt",
                    default=20,
                    min=10,
                    max=40,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="wind_red_kt",
                    label="Wind red (kt)",
                    description="Wind speed above terrain for red (severe rotor)",
                    type="speed",
                    unit="kt",
                    default=40,
                    min=20,
                    max=60,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="corroborated_red_kt",
                    label="Wind red w/ signature",
                    description=(
                        "Red threshold when a wave signature (ridge-top "
                        "inversion or oscillating vertical motion) is present "
                        "at the same point"
                    ),
                    type="speed",
                    unit="kt",
                    default=30,
                    min=15,
                    max=50,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        terrain_threshold = params.get("terrain_threshold_ft", 3000)
        altitude_margin = params.get("altitude_margin_ft", 2000)
        wind_amber = params.get("wind_amber_kt", 20)
        wind_red = params.get("wind_red_kt", 40)
        corroborated_red = params.get("corroborated_red_kt", 30)

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            total = 0  # mountain points only
            affected = 0
            max_wind = 0.0
            wave_red = False          # corroborated point above the lower red bar
            wave_sigs: set[str] = set()  # signatures seen at strong-wind points

            for rpa in ctx.analyses:
                terrain_ft = max_terrain_near_point(
                    ctx.elevation, rpa.distance_from_origin_nm
                )
                if terrain_ft is None or terrain_ft < terrain_threshold:
                    continue
                total += 1

                wind = wind_at_altitude(
                    ctx.cross_sections, model, rpa.point_index,
                    terrain_ft + altitude_margin, rpa.forecast_hour,
                )
                if wind is None:
                    continue

                speed_kt, _ = wind
                if speed_kt > max_wind:
                    max_wind = speed_kt

                if speed_kt >= wind_amber:
                    affected += 1
                    sigs = _wave_signatures(rpa.sounding.get(model), terrain_ft)
                    if sigs:
                        wave_sigs |= sigs
                        if speed_kt >= corroborated_red:
                            wave_red = True

            loc = ctx.locale
            ext = format_extent(affected, total, ctx.total_distance_nm)
            sig_text = " + ".join(
                adv_t(f"mountain_wind.sig_{s}", loc)
                for s in sorted(wave_sigs)
            )
            if total == 0:
                status = AdvisoryStatus.GREEN
                detail = adv_t("mountain_wind.no_terrain", loc)
            elif max_wind >= wind_red:
                status = AdvisoryStatus.RED
                detail = adv_t("mountain_wind.severe", loc, speed=f"{max_wind:.0f}", extent=ext)
                if sig_text:
                    detail += adv_t("mountain_wind.sig_suffix", loc, signature=sig_text)
            elif wave_red:
                status = AdvisoryStatus.RED
                detail = adv_t(
                    "mountain_wind.wave_confirmed", loc,
                    speed=f"{max_wind:.0f}", signature=sig_text, extent=ext,
                )
            elif max_wind >= wind_amber:
                status = AdvisoryStatus.AMBER
                detail = adv_t("mountain_wind.wave_risk", loc, speed=f"{max_wind:.0f}", extent=ext)
                if sig_text:
                    detail += adv_t("mountain_wind.sig_suffix", loc, signature=sig_text)
            else:
                status = AdvisoryStatus.GREEN
                detail = adv_t("mountain_wind.light", loc, speed=f"{max_wind:.0f}")

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("mountain_wind", per_model, params)
