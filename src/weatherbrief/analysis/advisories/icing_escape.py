"""Icing escape advisory — can escape icing by descending to warm air (non-FIKI)."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    format_extent,
    max_terrain_near_point,
    pct_above_threshold,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)


@register
class IcingEscapeEvaluator:
    """Evaluates whether icing can be escaped by descending to warm air."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="icing_escape",
            name="Icing Escape (non-FIKI)",
            short_description="Can escape icing by descending to warm air",
            description=(
                "For non-FIKI aircraft. Checks if the freezing level is above terrain "
                "plus a safety margin at each point with icing, so warm air below is "
                "reachable as an escape route."
            ),
            category="icing",
            parameters=[
                AdvisoryParameterDef(
                    key="terrain_margin_ft",
                    label="Terrain margin",
                    description="Minimum clearance above terrain for escape",
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
                    description="Freezing level below this margin above terrain triggers amber",
                    type="altitude",
                    unit="ft",
                    default=2000,
                    min=1000,
                    max=5000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="route_pct_amber",
                    label="Route % (amber)",
                    description="Percentage of route with icing to trigger amber",
                    type="percent",
                    unit="%",
                    default=20,
                    min=5,
                    max=80,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        terrain_margin = params.get("terrain_margin_ft", 1000)
        tight_margin = params.get("tight_margin_ft", 2000)
        route_pct_amber = params.get("route_pct_amber", 20)

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            total = 0
            affected = 0
            no_escape_count = 0
            has_tight_margin = False

            for rpa in ctx.analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    continue
                total += 1

                if not sounding.icing_zones:
                    continue

                affected += 1

                # Get freezing level and terrain
                fz_level_ft = None
                if sounding.indices and sounding.indices.freezing_level_ft is not None:
                    fz_level_ft = sounding.indices.freezing_level_ft

                terrain_ft = max_terrain_near_point(
                    ctx.elevation, rpa.distance_from_origin_nm
                )

                if fz_level_ft is None or terrain_ft is None:
                    no_escape_count += 1
                    continue

                if fz_level_ft < terrain_ft + terrain_margin:
                    no_escape_count += 1
                elif fz_level_ft < terrain_ft + tight_margin:
                    has_tight_margin = True

            # Determine model status
            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = "No data"
            elif no_escape_count > 0:
                status = AdvisoryStatus.RED
                detail = f"No warm escape over {format_extent(no_escape_count, total, ctx.total_distance_nm)}"
            elif affected == 0:
                status = AdvisoryStatus.GREEN
                detail = "No icing along route"
            else:
                status = pct_above_threshold(affected, total, route_pct_amber)
                ext = format_extent(affected, total, ctx.total_distance_nm)
                if status == AdvisoryStatus.GREEN and has_tight_margin:
                    status = AdvisoryStatus.AMBER
                    detail = f"Icing over {ext}, tight escape margin"
                elif status == AdvisoryStatus.GREEN:
                    detail = f"Icing over {ext}, warm escape available"
                else:
                    detail = f"Icing over {ext}"

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("icing_escape", per_model, params)
