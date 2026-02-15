"""Freezing level advisory — warm air available above terrain."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import max_terrain_near_point, worst_status
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)


@register
class FreezingLevelEvaluator:
    """Evaluates freezing level relative to terrain along the route."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="freezing_level",
            name="Freezing Level",
            short_description="Warm air available above terrain",
            description=(
                "Compares freezing level to highest terrain plus margin. "
                "A low freezing level near high terrain means icing is "
                "unavoidable when crossing mountains."
            ),
            category="icing",
            parameters=[
                AdvisoryParameterDef(
                    key="margin_ft",
                    label="Safe margin",
                    description="Minimum clearance between freezing level and terrain",
                    type="altitude",
                    unit="ft",
                    default=1000,
                    min=500,
                    max=3000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="tight_margin_ft",
                    label="Tight margin",
                    description="Freezing level below this above terrain triggers amber",
                    type="altitude",
                    unit="ft",
                    default=2000,
                    min=1000,
                    max=5000,
                    step=500,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        margin_ft = params.get("margin_ft", 1000)
        tight_margin_ft = params.get("tight_margin_ft", 2000)

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            total = 0
            below_margin = 0
            below_tight = 0
            min_clearance: float | None = None

            for rpa in ctx.analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    continue
                total += 1

                fz_ft = None
                if sounding.indices and sounding.indices.freezing_level_ft is not None:
                    fz_ft = sounding.indices.freezing_level_ft

                terrain_ft = max_terrain_near_point(
                    ctx.elevation, rpa.distance_from_origin_nm
                )

                if fz_ft is None or terrain_ft is None:
                    continue

                clearance = fz_ft - terrain_ft
                if min_clearance is None or clearance < min_clearance:
                    min_clearance = clearance

                if clearance < margin_ft:
                    below_margin += 1
                elif clearance < tight_margin_ft:
                    below_tight += 1

            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = "No data"
            elif below_margin > 0:
                status = AdvisoryStatus.RED
                detail = f"Freezing level below terrain + {margin_ft:.0f}ft at {below_margin} point(s)"
                if min_clearance is not None:
                    detail += f" (min clearance {min_clearance:.0f}ft)"
            elif below_tight > 0:
                status = AdvisoryStatus.AMBER
                detail = f"Tight freezing level margin at {below_tight} point(s)"
                if min_clearance is not None:
                    detail += f" (min clearance {min_clearance:.0f}ft)"
            else:
                status = AdvisoryStatus.GREEN
                if min_clearance is not None:
                    detail = f"Freezing level well above terrain (min clearance {min_clearance:.0f}ft)"
                else:
                    detail = "Freezing level above terrain"

            per_model.append(ModelAdvisoryResult(
                model=model,
                status=status,
                detail=detail,
                affected_points=below_margin + below_tight,
                total_points=total,
                affected_pct=100 * (below_margin + below_tight) / total if total > 0 else 0,
            ))

        aggregate = worst_status([m.status for m in per_model])
        worst_model = next((m for m in per_model if m.status == aggregate), per_model[0] if per_model else None)

        return RouteAdvisoryResult(
            advisory_id="freezing_level",
            aggregate_status=aggregate,
            aggregate_detail=worst_model.detail if worst_model else "",
            per_model=per_model,
            parameters_used=params,
        )
