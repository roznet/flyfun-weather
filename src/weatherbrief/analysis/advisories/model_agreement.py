"""Model agreement advisory — forecast confidence from cross-model divergence."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import format_extent, pct_above_threshold
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.models import (
    AgreementLevel,
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)


@register
class ModelAgreementEvaluator:
    """Evaluates forecast confidence from existing model divergence data."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="model_agreement",
            name="Forecast Confidence",
            short_description="Models agree on conditions",
            description=(
                "Re-uses the existing model divergence scores computed at each "
                "route point. POOR agreement means models disagree significantly "
                "on key variables, reducing forecast confidence."
            ),
            category="model",
            parameters=[
                AdvisoryParameterDef(
                    key="poor_pct_amber",
                    label="Poor % (amber)",
                    description="Route percentage with POOR agreement for amber",
                    type="percent",
                    unit="%",
                    default=25,
                    min=5,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="poor_pct_red",
                    label="Poor % (red)",
                    description="Route percentage with POOR agreement for red",
                    type="percent",
                    unit="%",
                    default=50,
                    min=10,
                    max=100,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        poor_pct_amber = params.get("poor_pct_amber", 25)
        poor_pct_red = params.get("poor_pct_red", 50)

        # Model agreement is cross-model — evaluated once, not per-model
        total = 0
        poor_count = 0
        moderate_count = 0

        for rpa in ctx.analyses:
            if not rpa.model_divergence:
                continue
            total += 1

            has_poor = any(d.agreement == AgreementLevel.POOR for d in rpa.model_divergence)
            has_moderate = any(d.agreement == AgreementLevel.MODERATE for d in rpa.model_divergence)

            if has_poor:
                poor_count += 1
            elif has_moderate:
                moderate_count += 1

        if total == 0:
            status = AdvisoryStatus.UNAVAILABLE
            detail = "No model comparison data"
        elif poor_count == 0 and moderate_count == 0:
            status = AdvisoryStatus.GREEN
            detail = "Good agreement across all models"
        else:
            status = pct_above_threshold(poor_count, total, poor_pct_amber, poor_pct_red)
            if status == AdvisoryStatus.GREEN and moderate_count > 0:
                detail = f"Mostly good agreement, moderate divergence over {format_extent(moderate_count, total, ctx.total_distance_nm)}"
            else:
                detail = f"Poor model agreement over {format_extent(poor_count, total, ctx.total_distance_nm)}"

        per_model = [ModelAdvisoryResult.build(
            model="all", status=status, detail=detail,
            affected=poor_count, total=total,
            total_distance_nm=ctx.total_distance_nm,
        )]

        return RouteAdvisoryResult.from_per_model("model_agreement", per_model, params)
