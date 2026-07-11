"""Turbulence advisory — ride quality acceptable at cruise."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import pct_above_threshold
from weatherbrief.analysis.advisories.evidence import (
    EvidenceSample,
    summarize_evidence,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    CATRiskLevel,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
    VerticalMotionClass,
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
            altitude_dependent=True,
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
        ordered_analyses = sorted(
            ctx.analyses,
            key=lambda rpa: (rpa.distance_from_origin_nm, rpa.point_index),
        )

        cat_order = [
            CATRiskLevel.NONE,
            CATRiskLevel.LIGHT,
            CATRiskLevel.MODERATE,
            CATRiskLevel.SEVERE,
        ]
        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            evaluated: set[int] = set()
            complete: set[int] = set()
            affected: set[int] = set()
            cat_points: set[int] = set()
            motion_points: set[int] = set()
            samples: list[EvidenceSample] = []
            has_severe = False
            worst_cat = CATRiskLevel.NONE

            for rpa in ordered_analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    continue
                vm = sounding.vertical_motion
                if (
                    vm is None
                    or vm.classification == VerticalMotionClass.UNAVAILABLE
                ):
                    continue

                point_index = rpa.point_index
                evaluated.add(point_index)
                complete.add(point_index)

                for layer in vm.cat_risk_layers:
                    if not (
                        layer.base_ft <= cruise <= layer.top_ft
                        and layer.risk != CATRiskLevel.NONE
                    ):
                        continue
                    affected.add(point_index)
                    cat_points.add(point_index)
                    samples.append(
                        EvidenceSample(
                            point_index=point_index,
                            severity=(
                                AdvisoryStatus.RED
                                if layer.risk == CATRiskLevel.SEVERE
                                else AdvisoryStatus.AMBER
                            ),
                            reason_code="cat_at_cruise",
                            metric_id="cat_risk",
                            method_id="richardson_cat",
                            lower_altitude_ft=round(layer.base_ft),
                            upper_altitude_ft=round(layer.top_ft),
                        )
                    )
                    if cat_order.index(layer.risk) > cat_order.index(worst_cat):
                        worst_cat = layer.risk
                    if layer.risk == CATRiskLevel.SEVERE:
                        has_severe = True

                if (
                    vm.max_w_fpm is not None
                    and abs(vm.max_w_fpm) > strong_w_fpm
                    and vm.max_w_level_ft is not None
                    and abs(vm.max_w_level_ft - cruise) < 3000
                ):
                    affected.add(point_index)
                    motion_points.add(point_index)
                    motion_level_ft = round(vm.max_w_level_ft)
                    samples.append(
                        EvidenceSample(
                            point_index=point_index,
                            severity=AdvisoryStatus.AMBER,
                            reason_code="strong_vertical_motion_near_cruise",
                            metric_id=None,
                            method_id="vertical_motion",
                            lower_altitude_ft=motion_level_ft,
                            upper_altitude_ft=motion_level_ft,
                        )
                    )

            summary = summarize_evidence(
                route_points=ordered_analyses,
                total_distance_nm=ctx.total_distance_nm,
                evaluated_point_indices=evaluated,
                complete_point_indices=complete,
                affected_point_indices=affected,
                evidence_samples=samples,
            )
            loc = ctx.locale
            if summary.total_points == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            elif has_severe:
                status = AdvisoryStatus.RED
                detail = adv_t(
                    "turbulence.severe_over",
                    loc,
                    extent=summary.format_extent(),
                )
            elif summary.affected_points == 0:
                status = AdvisoryStatus.GREEN
                detail = adv_t("turbulence.smooth", loc)
            else:
                status = pct_above_threshold(
                    summary.affected_points,
                    summary.total_points,
                    route_pct_amber,
                    red_pct=50,
                )
                risk_label = (
                    worst_cat.value.upper()
                    if worst_cat != CATRiskLevel.NONE
                    else "Turbulence"
                )
                detail = adv_t(
                    "turbulence.risk_over",
                    loc,
                    risk=risk_label,
                    extent=summary.format_extent(),
                )

            if has_severe:
                primary_method_id = "richardson_cat"
            elif cat_points and motion_points:
                primary_method_id = "cat_with_vertical_motion"
            elif motion_points:
                primary_method_id = "vertical_motion"
            else:
                primary_method_id = "richardson_cat"

            missing_detail = adv_t(
                "no_data" if summary.data_state == "unavailable" else "partial_data",
                loc,
            )
            per_model.append(
                summary.build_result(
                    model=model,
                    status=status,
                    detail=detail,
                    unavailable_detail=missing_detail,
                    primary_method_id=primary_method_id,
                )
            )

        return RouteAdvisoryResult.from_per_model("turbulence", per_model, params)
