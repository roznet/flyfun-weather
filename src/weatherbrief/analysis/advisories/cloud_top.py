"""Cloud top advisory — can fly above cloud tops."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import pct_above_threshold, worst_status
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)


@register
class CloudTopEvaluator:
    """Evaluates whether the aircraft can fly above cloud tops."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="cloud_top",
            name="Cloud Tops",
            short_description="Can fly above cloud tops",
            description=(
                "Checks highest cloud tops against flight ceiling. "
                "Cloud tops above ceiling means the pilot cannot get on top."
            ),
            category="cloud",
            parameters=[
                AdvisoryParameterDef(
                    key="margin_ft",
                    label="Margin above tops",
                    description="Required clearance above cloud tops",
                    type="altitude",
                    unit="ft",
                    default=1000,
                    min=500,
                    max=3000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="pct_amber",
                    label="Route % (amber)",
                    description="Route percentage with tops above ceiling for amber",
                    type="percent",
                    unit="%",
                    default=25,
                    min=5,
                    max=80,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        margin_ft = params.get("margin_ft", 1000)
        pct_amber = params.get("pct_amber", 25)
        ceiling = ctx.flight_ceiling_ft

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            total = 0
            above_ceiling = 0
            max_top = 0.0

            for rpa in ctx.analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    continue
                total += 1

                if not sounding.cloud_layers:
                    continue

                highest_top = max(cl.top_ft for cl in sounding.cloud_layers)
                if highest_top > max_top:
                    max_top = highest_top

                if highest_top + margin_ft > ceiling:
                    above_ceiling += 1

            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = "No data"
            elif above_ceiling == 0:
                status = AdvisoryStatus.GREEN
                if max_top > 0:
                    detail = f"Cloud tops reachable (max {max_top:.0f}ft, ceiling {ceiling}ft)"
                else:
                    detail = "No significant cloud layers"
            else:
                status = pct_above_threshold(above_ceiling, total, pct_amber, red_pct=60)
                pct = 100 * above_ceiling / total
                detail = f"Cloud tops above ceiling at {above_ceiling}/{total} points ({pct:.0f}%, max {max_top:.0f}ft)"

            per_model.append(ModelAdvisoryResult(
                model=model,
                status=status,
                detail=detail,
                affected_points=above_ceiling,
                total_points=total,
                affected_pct=100 * above_ceiling / total if total > 0 else 0,
            ))

        aggregate = worst_status([m.status for m in per_model])
        worst_model = next((m for m in per_model if m.status == aggregate), per_model[0] if per_model else None)

        return RouteAdvisoryResult(
            advisory_id="cloud_top",
            aggregate_status=aggregate,
            aggregate_detail=worst_model.detail if worst_model else "",
            per_model=per_model,
            parameters_used=params,
        )
