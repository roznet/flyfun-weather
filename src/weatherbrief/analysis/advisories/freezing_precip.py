"""Freezing precipitation (FZRA/PL) advisory evaluator.

Freezing rain is the most severe icing hazard a GA aircraft can encounter:
snow melts in a warm nose aloft and falls as supercooled rain into sub-zero
air below, accreting clear ice at rates beyond any deice capability. Crucially
it happens *below* cloud — in air every in-cloud icing method (and the
cross-section cloud bands) shows as clear — and it is the one icing scenario
where the standard escape (descend into warmer/clearer air) is wrong.

Two tiers per route point per model:
- **Active** (RED): the precipitation assessment reports a freezing-rain or
  ice-pellet surface phase (warm-nose profile WITH active precipitation).
  Ice pellets are graded the same — they mean the refreeze completed before
  the ground, i.e. freezing rain exists in the layer above.
- **Primed** (AMBER): the profile has the freezing-rain shape (sub-zero
  surface wet-bulb under a warm nose, via ``detect_warm_nose``) but no
  precipitation is falling at that hour — precip onset or a small timing
  shift turns it active.
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import format_extent
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.analysis.sounding.precipitation import detect_warm_nose
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ModelAdvisoryResult,
    PrecipPhase,
    RouteAdvisoryResult,
)

_ACTIVE_PHASES = (PrecipPhase.FREEZING_RAIN, PrecipPhase.ICE_PELLETS)


@register
class FreezingPrecipEvaluator:
    """Evaluates freezing rain / ice pellet exposure along the route."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="freezing_precip",
            name="Freezing Precipitation",
            short_description="Freezing rain / ice pellets along the route",
            description=(
                "Detects freezing rain and ice pellets: a warm layer aloft over a "
                "sub-zero surface layer turns falling rain into supercooled drops "
                "that freeze on the airframe as clear ice. This happens BELOW "
                "cloud — where the in-cloud icing methods and cloud bands show "
                "nothing — and it is the one icing scenario where descending does "
                "NOT escape. Red when any model shows active freezing rain or ice "
                "pellets at any point (deliberately no coverage threshold: one "
                "transit exceeds all icing certification, FIKI included; ice "
                "pellets prove freezing rain in the layer above). Amber when a "
                "freezing-rain-shaped profile exists without active precipitation "
                "yet (primed — onset or a timing shift turns it active) over "
                "enough of the route. Unavailable (not green) on packs without "
                "precipitation data."
            ),
            category="icing",
            parameters=[
                AdvisoryParameterDef(
                    key="primed_pct_amber",
                    label="Primed profile amber",
                    description=(
                        "Percent of route with a freezing-rain-shaped profile "
                        "(no active precip) that triggers amber"
                    ),
                    type="percent",
                    unit="%",
                    default=5,
                    min=0,
                    max=50,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        primed_pct_amber = params.get("primed_pct_amber", 5)

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            total = 0
            active_pts = 0
            primed_pts = 0
            has_signal_source = False
            loc = ctx.locale

            for rpa in ctx.analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    continue
                total += 1

                precip = sounding.precipitation
                if precip is not None:
                    has_signal_source = True
                    if (
                        precip.surface_phase in _ACTIVE_PHASES
                        or precip.freezing_rain_risk
                    ):
                        active_pts += 1
                        continue

                # Primed: freezing-rain profile shape without active precip.
                if sounding.derived_levels:
                    has_signal_source = True
                    fz_risk, _, _, ice_pellets = detect_warm_nose(
                        sounding.derived_levels
                    )
                    if fz_risk or ice_pellets:
                        primed_pts += 1

            if total == 0 or not has_signal_source:
                per_model.append(ModelAdvisoryResult.build(
                    model=model, status=AdvisoryStatus.UNAVAILABLE,
                    detail=adv_t("no_data", loc), affected=0, total=max(total, 0),
                    total_distance_nm=ctx.total_distance_nm,
                ))
                continue

            primed_pct = 100 * primed_pts / total if total else 0

            if active_pts > 0:
                status = AdvisoryStatus.RED
                detail = (
                    "Freezing precipitation "
                    f"{format_extent(active_pts, total, ctx.total_distance_nm)}"
                )
                if primed_pts:
                    detail += (
                        f"; primed profile "
                        f"{format_extent(primed_pts, total, ctx.total_distance_nm)}"
                    )
            elif primed_pts > 0 and primed_pct >= primed_pct_amber:
                status = AdvisoryStatus.AMBER
                detail = (
                    "Freezing-rain profile (no active precip) "
                    f"{format_extent(primed_pts, total, ctx.total_distance_nm)}"
                )
            else:
                status = AdvisoryStatus.GREEN
                detail = "No freezing precipitation signature"

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=active_pts + primed_pts, total=total,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("freezing_precip", per_model, params)
