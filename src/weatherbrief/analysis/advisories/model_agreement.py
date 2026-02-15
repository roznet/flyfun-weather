"""Model agreement advisory — forecast confidence from cross-model divergence."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import format_extent, pct_above_threshold, worst_status
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

        # Model agreement is cross-model — not per-model
        # We evaluate once and report on all models combined
        total = 0
        poor_count = 0
        moderate_count = 0

        for rpa in ctx.analyses:
            if not rpa.model_divergence:
                continue
            total += 1

            # Count as "poor" if any key variable has POOR agreement
            has_poor = any(
                d.agreement == AgreementLevel.POOR for d in rpa.model_divergence
            )
            has_moderate = any(
                d.agreement == AgreementLevel.MODERATE for d in rpa.model_divergence
            )

            if has_poor:
                poor_count += 1
            elif has_moderate:
                moderate_count += 1

        if total == 0:
            aggregate = AdvisoryStatus.UNAVAILABLE
            detail = "No model comparison data"
        elif poor_count == 0 and moderate_count == 0:
            aggregate = AdvisoryStatus.GREEN
            detail = "Good agreement across all models"
        else:
            aggregate = pct_above_threshold(poor_count, total, poor_pct_amber, poor_pct_red)
            if aggregate == AdvisoryStatus.GREEN and moderate_count > 0:
                ext = format_extent(moderate_count, total, ctx.total_distance_nm)
                detail = f"Mostly good agreement, moderate divergence over {ext}"
            else:
                ext = format_extent(poor_count, total, ctx.total_distance_nm)
                detail = f"Poor model agreement over {ext}"

        # Report as a single "model" result since agreement is cross-model
        per_model = [ModelAdvisoryResult(
            model="all",
            status=aggregate,
            detail=detail,
            affected_points=poor_count,
            total_points=total,
            affected_pct=100 * poor_count / total if total > 0 else 0,
            affected_nm=round(ctx.total_distance_nm * poor_count / total, 1) if total > 0 else 0,
            total_nm=round(ctx.total_distance_nm, 1),
        )]

        return RouteAdvisoryResult(
            advisory_id="model_agreement",
            aggregate_status=aggregate,
            aggregate_detail=detail,
            per_model=per_model,
            parameters_used=params,
        )
