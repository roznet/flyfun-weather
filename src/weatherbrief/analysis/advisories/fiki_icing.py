"""FIKI icing advisory — icing manageable for FIKI-equipped aircraft."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import format_extent
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    IcingRisk,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)


@register
class FIKIIcingEvaluator:
    """Evaluates icing severity for FIKI-equipped aircraft.

    FIKI aircraft can transit icing but not loiter indefinitely.
    Evaluates layer thickness, severity, and SLD risk.
    """

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="fiki_icing",
            name="FIKI Icing",
            short_description="Icing manageable for FIKI-equipped",
            description=(
                "For FIKI-equipped aircraft. Evaluates icing layer thickness, "
                "severity, and SLD risk. FIKI can transit but not loiter — "
                "thick layers or severe icing are concerning."
            ),
            category="icing",
            default_enabled=False,  # opt-in for FIKI operators
            parameters=[
                AdvisoryParameterDef(
                    key="thickness_amber_ft",
                    label="Thickness amber",
                    description="Icing layer thickness for amber",
                    type="altitude",
                    unit="ft",
                    default=3000,
                    min=1000,
                    max=8000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="thickness_red_ft",
                    label="Thickness red",
                    description="Icing layer thickness for red",
                    type="altitude",
                    unit="ft",
                    default=5000,
                    min=2000,
                    max=10000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="severe_is_red",
                    label="Severe = RED",
                    description="Any severe icing triggers RED",
                    type="boolean",
                    default=1,
                    min=0,
                    max=1,
                    step=1,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        thickness_amber = params.get("thickness_amber_ft", 3000)
        thickness_red = params.get("thickness_red_ft", 5000)
        severe_is_red = params.get("severe_is_red", 1) > 0.5

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            total = 0
            affected = 0
            has_severe = False
            has_sld = False
            max_thickness = 0.0

            for rpa in ctx.analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    continue
                total += 1

                if not sounding.icing_zones:
                    continue

                affected += 1

                for zone in sounding.icing_zones:
                    thickness = zone.top_ft - zone.base_ft
                    if thickness > max_thickness:
                        max_thickness = thickness
                    if zone.risk == IcingRisk.SEVERE:
                        has_severe = True
                    if zone.sld_risk:
                        has_sld = True

            ext = format_extent(affected, total, ctx.total_distance_nm)
            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = "No data"
            elif has_sld:
                status = AdvisoryStatus.RED
                detail = "SLD risk detected — FIKI protection insufficient"
            elif has_severe and severe_is_red:
                status = AdvisoryStatus.RED
                detail = f"Severe icing over {ext}"
            elif max_thickness >= thickness_red:
                status = AdvisoryStatus.RED
                detail = f"Thick icing layer ({max_thickness:.0f}ft) over {ext}"
            elif max_thickness >= thickness_amber:
                status = AdvisoryStatus.AMBER
                detail = f"Moderate icing layer ({max_thickness:.0f}ft) over {ext}"
            elif affected > 0:
                status = AdvisoryStatus.GREEN
                detail = f"Manageable icing over {ext} (max {max_thickness:.0f}ft)"
            else:
                status = AdvisoryStatus.GREEN
                detail = "No icing along route"

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model("fiki_icing", per_model, params)
