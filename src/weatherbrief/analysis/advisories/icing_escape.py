"""Icing escape advisory — can escape icing by descending to warm air (non-FIKI)."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    format_extent,
    has_relevant_icing,
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
class IcingEscapeEvaluator:
    """Evaluates whether icing can be escaped by descending to warm air."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="icing_escape",
            name="Icing Escape (non-FIKI)",
            short_description="Icing at cruise and warm-air escape viability",
            description=(
                "For non-FIKI aircraft. First detects icing at or below cruise "
                "altitude, then checks whether descending to warm air above terrain "
                "is viable as an escape route. The reported percentage reflects "
                "icing coverage along the route."
            ),
            category="icing",
            altitude_dependent=True,
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
                    key="icing_altitude_buffer_ft",
                    label="Icing alt buffer",
                    description=(
                        "Icing above cruise + buffer is ignored "
                        "(irrelevant altitude)"
                    ),
                    type="altitude",
                    unit="ft",
                    default=2000,
                    min=500,
                    max=5000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="icing_coverage_pct_amber",
                    label="Icing extent (amber)",
                    description=(
                        "Percentage of route with icing (but escape available) "
                        "to trigger amber. Only applies when warm-air descent "
                        "is viable everywhere along the route."
                    ),
                    type="percent",
                    unit="%",
                    default=20,
                    min=5,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="no_escape_pct_red",
                    label="No escape (red)",
                    description=(
                        "Percentage of route where icing exists but descending "
                        "to warm air is blocked by terrain. Any no-escape point "
                        "triggers amber; exceeding this threshold escalates to red."
                    ),
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
        terrain_margin = params.get("terrain_margin_ft", 1000)
        tight_margin = params.get("tight_margin_ft", 2000)
        icing_altitude_buffer_ft = params.get("icing_altitude_buffer_ft", 2000)
        # Accept both new and legacy param names for backwards compatibility
        icing_coverage_pct_amber = params.get(
            "icing_coverage_pct_amber",
            params.get("route_pct_amber", 20),
        )
        no_escape_pct_red = params.get(
            "no_escape_pct_red",
            params.get("min_route_pct", 15),
        )

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

                if not has_relevant_icing(
                    sounding.icing_zones,
                    ctx.cruise_altitude_ft,
                    icing_altitude_buffer_ft,
                ):
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
            loc = ctx.locale
            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = adv_t("no_data", loc)
            elif no_escape_count > 0 and pct_above_threshold(
                no_escape_count, total, no_escape_pct_red,
            ) != AdvisoryStatus.GREEN:
                status = AdvisoryStatus.RED
                ext = format_extent(affected, total, ctx.total_distance_nm)
                detail = adv_t("icing_escape.no_escape", loc, extent=ext, count=no_escape_count)
            elif no_escape_count > 0:
                status = AdvisoryStatus.AMBER
                ext = format_extent(affected, total, ctx.total_distance_nm)
                detail = adv_t("icing_escape.no_escape", loc, extent=ext, count=no_escape_count)
            elif affected == 0:
                status = AdvisoryStatus.GREEN
                detail = adv_t("icing_escape.no_icing", loc)
            else:
                status = pct_above_threshold(affected, total, icing_coverage_pct_amber)
                ext = format_extent(affected, total, ctx.total_distance_nm)
                if status == AdvisoryStatus.GREEN and has_tight_margin:
                    status = AdvisoryStatus.AMBER
                    detail = adv_t("icing_escape.tight_margin", loc, extent=ext)
                elif status == AdvisoryStatus.GREEN:
                    detail = adv_t("icing_escape.warm_escape", loc, extent=ext)
                else:
                    detail = adv_t("icing_escape.warm_escape", loc, extent=ext)

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("icing_escape", per_model, params)
