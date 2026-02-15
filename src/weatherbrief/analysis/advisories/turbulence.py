"""Turbulence advisory — ride quality acceptable at cruise."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import format_extent, pct_above_threshold
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    CATRiskLevel,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)


@register
class TurbulenceEvaluator:
    """Evaluates ride quality based on CAT risk and vertical motion at cruise."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="turbulence",
            name="Turbulence",
            short_description="Ride quality acceptable at cruise",
            description=(
                "Checks CAT risk layers and vertical motion at cruise altitude. "
                "Any severe CAT triggers RED regardless of route percentage."
            ),
            category="turbulence",
            parameters=[
                AdvisoryParameterDef(
                    key="route_pct_amber",
                    label="Route % (amber)",
                    description="Route percentage with turbulence for amber",
                    type="percent",
                    unit="%",
                    default=20,
                    min=5,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="strong_w_fpm",
                    label="Strong w threshold",
                    description="Vertical velocity above this is significant",
                    type="speed",
                    unit="ft/min",
                    default=200,
                    min=100,
                    max=500,
                    step=50,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        route_pct_amber = params.get("route_pct_amber", 20)
        strong_w_fpm = params.get("strong_w_fpm", 200)
        cruise = ctx.cruise_altitude_ft

        _CAT_ORDER = [CATRiskLevel.NONE, CATRiskLevel.LIGHT, CATRiskLevel.MODERATE, CATRiskLevel.SEVERE]
        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            total = 0
            affected = 0
            has_severe = False
            worst_cat = CATRiskLevel.NONE

            for rpa in ctx.analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    continue
                total += 1

                point_affected = False
                vm = sounding.vertical_motion

                if vm is not None:
                    for layer in vm.cat_risk_layers:
                        if layer.base_ft <= cruise <= layer.top_ft and layer.risk != CATRiskLevel.NONE:
                            point_affected = True
                            if _CAT_ORDER.index(layer.risk) > _CAT_ORDER.index(worst_cat):
                                worst_cat = layer.risk
                            if layer.risk == CATRiskLevel.SEVERE:
                                has_severe = True

                    if vm.max_w_fpm is not None and abs(vm.max_w_fpm) > strong_w_fpm:
                        if vm.max_w_level_ft is not None and abs(vm.max_w_level_ft - cruise) < 3000:
                            point_affected = True

                if point_affected:
                    affected += 1

            ext = format_extent(affected, total, ctx.total_distance_nm)
            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = "No data"
            elif has_severe:
                status = AdvisoryStatus.RED
                detail = f"Severe CAT over {ext}"
            elif affected == 0:
                status = AdvisoryStatus.GREEN
                detail = "Smooth ride expected"
            else:
                status = pct_above_threshold(affected, total, route_pct_amber, red_pct=50)
                risk_label = worst_cat.value.upper() if worst_cat != CATRiskLevel.NONE else "Turbulence"
                detail = f"{risk_label} over {ext}"

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("turbulence", per_model, params)
