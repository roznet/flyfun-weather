"""Mountain wind advisory — wind near terrain safe."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    format_extent,
    max_terrain_near_point,
    wind_at_altitude,
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
class MountainWindEvaluator:
    """Evaluates wind speed near terrain tops for mountain wave/rotor risk."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="mountain_wind",
            name="Mountain Wind",
            short_description="Wind near terrain safe",
            description=(
                "Evaluates wind speed near terrain tops at points where terrain "
                "exceeds a threshold. High winds near mountains indicate mountain "
                "wave and rotor risk."
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
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        terrain_threshold = params.get("terrain_threshold_ft", 3000)
        altitude_margin = params.get("altitude_margin_ft", 2000)
        wind_amber = params.get("wind_amber_kt", 20)
        wind_red = params.get("wind_red_kt", 40)

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            total_mountain = 0
            amber_count = 0
            red_count = 0
            max_wind = 0.0

            for rpa in ctx.analyses:
                terrain_ft = max_terrain_near_point(
                    ctx.elevation, rpa.distance_from_origin_nm
                )
                if terrain_ft is None or terrain_ft < terrain_threshold:
                    continue
                total_mountain += 1

                # Check wind near terrain tops
                target_alt = terrain_ft + altitude_margin
                wind = wind_at_altitude(
                    ctx.cross_sections, model, rpa.point_index, target_alt
                )
                if wind is None:
                    continue

                speed_kt, _ = wind
                if speed_kt > max_wind:
                    max_wind = speed_kt

                if speed_kt >= wind_red:
                    red_count += 1
                elif speed_kt >= wind_amber:
                    amber_count += 1

            affected = red_count + amber_count
            if total_mountain == 0:
                status = AdvisoryStatus.GREEN
                detail = "No significant terrain along route"
            elif red_count > 0:
                status = AdvisoryStatus.RED
                ext = format_extent(red_count, total_mountain, ctx.total_distance_nm)
                detail = f"Severe mountain wind ({max_wind:.0f}kt near terrain) over {ext}"
            elif amber_count > 0:
                status = AdvisoryStatus.AMBER
                ext = format_extent(amber_count, total_mountain, ctx.total_distance_nm)
                detail = f"Mountain wave risk ({max_wind:.0f}kt near terrain) over {ext}"
            else:
                status = AdvisoryStatus.GREEN
                detail = f"Light winds near terrain ({max_wind:.0f}kt)"

            per_model.append(ModelAdvisoryResult(
                model=model,
                status=status,
                detail=detail,
                affected_points=affected,
                total_points=total_mountain,
                affected_pct=100 * affected / total_mountain if total_mountain > 0 else 0,
                affected_nm=round(ctx.total_distance_nm * affected / total_mountain, 1) if total_mountain > 0 else 0,
                total_nm=round(ctx.total_distance_nm, 1),
            ))

        aggregate = worst_status([m.status for m in per_model])
        worst_model = next((m for m in per_model if m.status == aggregate), per_model[0] if per_model else None)

        return RouteAdvisoryResult(
            advisory_id="mountain_wind",
            aggregate_status=aggregate,
            aggregate_detail=worst_model.detail if worst_model else "",
            per_model=per_model,
            parameters_used=params,
        )
