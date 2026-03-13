"""Freezing level advisory — warm air available above terrain."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    format_extent,
    max_terrain_near_point,
    pct_above_threshold,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
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
                AdvisoryParameterDef(
                    key="min_route_pct",
                    label="Min route %",
                    description="Minimum percentage of route affected to trigger advisory",
                    type="percent",
                    unit="%",
                    default=15,
                    min=5,
                    max=50,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        margin_ft = params.get("margin_ft", 1000)
        tight_margin_ft = params.get("tight_margin_ft", 2000)
        min_route_pct = params.get("min_route_pct", 15)

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

            affected = below_margin + below_tight
            loc = ctx.locale
            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            elif below_margin > 0 and pct_above_threshold(
                below_margin, total, min_route_pct
            ) != AdvisoryStatus.GREEN:
                status = AdvisoryStatus.RED
                ext = format_extent(below_margin, total, ctx.total_distance_nm)
                detail = adv_t("freezing_level.below_margin", loc, margin=f"{margin_ft:.0f}", extent=ext)
                if min_clearance is not None:
                    detail += adv_t("freezing_level.min_clearance", loc, clearance=f"{min_clearance:.0f}")
            elif affected > 0 and pct_above_threshold(
                affected, total, min_route_pct
            ) != AdvisoryStatus.GREEN:
                status = AdvisoryStatus.AMBER
                ext = format_extent(affected, total, ctx.total_distance_nm)
                detail = adv_t("freezing_level.tight_margin", loc, extent=ext)
                if min_clearance is not None:
                    detail += adv_t("freezing_level.min_clearance", loc, clearance=f"{min_clearance:.0f}")
            else:
                status = AdvisoryStatus.GREEN
                if min_clearance is not None:
                    detail = adv_t("freezing_level.well_above", loc, clearance=f"{min_clearance:.0f}")
                else:
                    detail = adv_t("freezing_level.above_terrain", loc)

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("freezing_level", per_model, params)
