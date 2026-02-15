"""Icing escape advisory — can escape icing by descending to warm air (non-FIKI)."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    format_extent,
    max_terrain_near_point,
    pct_above_threshold,
    worst_status,
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

            for rpa in ctx.analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    continue
                total += 1

                # Check if this point has icing
                if not sounding.icing_zones:
                    continue

                affected += 1

                # Get freezing level
                fz_level_ft = None
                if sounding.indices and sounding.indices.freezing_level_ft is not None:
                    fz_level_ft = sounding.indices.freezing_level_ft

                # Get terrain elevation near this point
                terrain_ft = max_terrain_near_point(
                    ctx.elevation, rpa.distance_from_origin_nm
                )

                if fz_level_ft is None or terrain_ft is None:
                    # Can't determine escape — conservative
                    no_escape_count += 1
                    continue

                # Check if warm air below is reachable above terrain
                if fz_level_ft < terrain_ft + terrain_margin:
                    no_escape_count += 1

            # Determine model status
            ext = format_extent(affected, total, ctx.total_distance_nm)
            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = "No data"
            elif no_escape_count > 0:
                no_esc_ext = format_extent(no_escape_count, total, ctx.total_distance_nm)
                status = AdvisoryStatus.RED
                detail = f"No warm escape over {no_esc_ext}"
            elif affected == 0:
                status = AdvisoryStatus.GREEN
                detail = "No icing along route"
            else:
                # Icing present but escape viable — check percentage
                status = pct_above_threshold(affected, total, route_pct_amber)
                if status == AdvisoryStatus.GREEN:
                    detail = f"Icing over {ext}, warm escape available"
                else:
                    detail = f"Icing over {ext}"

                # Upgrade to amber if freezing level is tight
                if status == AdvisoryStatus.GREEN:
                    for rpa in ctx.analyses:
                        sounding = rpa.sounding.get(model)
                        if sounding is None or not sounding.icing_zones:
                            continue
                        fz = sounding.indices.freezing_level_ft if sounding.indices else None
                        terrain = max_terrain_near_point(ctx.elevation, rpa.distance_from_origin_nm)
                        if fz is not None and terrain is not None:
                            if fz < terrain + tight_margin:
                                status = AdvisoryStatus.AMBER
                                detail = f"Tight escape margin ({fz:.0f}ft freeze vs {terrain:.0f}ft terrain)"
                                break

            per_model.append(ModelAdvisoryResult(
                model=model,
                status=status,
                detail=detail,
                affected_points=affected,
                total_points=total,
                affected_pct=100 * affected / total if total > 0 else 0,
                affected_nm=round(ctx.total_distance_nm * affected / total, 1) if total > 0 else 0,
                total_nm=round(ctx.total_distance_nm, 1),
            ))

        aggregate = worst_status([m.status for m in per_model])
        worst_model = next((m for m in per_model if m.status == aggregate), per_model[0] if per_model else None)
        aggregate_detail = worst_model.detail if worst_model else ""

        return RouteAdvisoryResult(
            advisory_id="icing_escape",
            aggregate_status=aggregate,
            aggregate_detail=aggregate_detail,
            per_model=per_model,
            parameters_used=params,
        )
