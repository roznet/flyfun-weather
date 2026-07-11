"""Model agreement advisory — forecast confidence from cross-model divergence."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import pct_above_threshold
from weatherbrief.analysis.advisories.evidence import EvidenceSample, summarize_evidence
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.analysis.comparison import DIVERGENCE_THRESHOLDS
from weatherbrief.models import (
    AgreementLevel,
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
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
            default_enabled=False,
            parameters=[
                AdvisoryParameterDef(
                    key="min_poor_vars",
                    label="Min poor variables",
                    description="Number of variables that must be POOR to flag a waypoint",
                    type="number",
                    unit="",
                    default=3,
                    min=1,
                    max=8,
                    step=1,
                ),
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
        min_poor_vars = int(params.get("min_poor_vars", 3))
        poor_pct_amber = params.get("poor_pct_amber", 25)
        poor_pct_red = params.get("poor_pct_red", 50)

        # Model agreement is cross-model — evaluated once, not per-model
        evaluated: set[int] = set()
        complete: set[int] = set()
        poor_points: set[int] = set()
        moderate_points: set[int] = set()
        samples: list[EvidenceSample] = []

        for rpa in ctx.analyses:
            if not rpa.model_divergence:
                continue
            point_index = rpa.point_index
            evaluated.add(point_index)
            complete.add(point_index)

            poor = [
                divergence
                for divergence in rpa.model_divergence
                if divergence.agreement == AgreementLevel.POOR
            ]
            moderate = [
                divergence
                for divergence in rpa.model_divergence
                if divergence.agreement == AgreementLevel.MODERATE
            ]
            has_poor = len(poor) >= min_poor_vars

            if has_poor:
                poor_points.add(point_index)
                samples.extend(
                    EvidenceSample(
                        point_index=point_index,
                        severity=AdvisoryStatus.RED,
                        reason_code="poor_model_agreement",
                        metric_id=(
                            divergence.variable
                            if divergence.variable in DIVERGENCE_THRESHOLDS
                            else None
                        ),
                        method_id="model_divergence",
                    )
                    for divergence in poor
                )
            elif moderate:
                moderate_points.add(point_index)
                samples.extend(
                    EvidenceSample(
                        point_index=point_index,
                        severity=AdvisoryStatus.AMBER,
                        reason_code="moderate_model_agreement",
                        metric_id=(
                            divergence.variable
                            if divergence.variable in DIVERGENCE_THRESHOLDS
                            else None
                        ),
                        method_id="model_divergence",
                    )
                    for divergence in moderate
                )

        summary = summarize_evidence(
            route_points=ctx.analyses,
            total_distance_nm=ctx.total_distance_nm,
            evaluated_point_indices=evaluated,
            complete_point_indices=complete,
            affected_point_indices=poor_points,
            evidence_samples=samples,
            moderate_point_indices=moderate_points,
        )
        total = summary.total_points
        poor_count = summary.affected_points
        moderate_count = summary.affected_mod_points

        loc = ctx.locale
        if total == 0:
            status = AdvisoryStatus.UNAVAILABLE
            detail = adv_t("model_agreement.no_data", loc)
        elif poor_count == 0 and moderate_count == 0:
            status = AdvisoryStatus.GREEN
            detail = adv_t("model_agreement.good", loc)
        else:
            status = pct_above_threshold(poor_count, total, poor_pct_amber, poor_pct_red)
            if status == AdvisoryStatus.GREEN and moderate_count > 0:
                detail = adv_t(
                    "model_agreement.mostly_good",
                    loc,
                    extent=summary.format_mod_extent(),
                )
            else:
                detail = adv_t(
                    "model_agreement.poor",
                    loc,
                    extent=summary.format_extent(),
                )

        missing_detail = adv_t(
            "model_agreement.no_data"
            if summary.data_state == "unavailable"
            else "partial_data",
            loc,
        )
        per_model = [
            summary.build_result(
                model="all",
                status=status,
                detail=detail,
                unavailable_detail=missing_detail,
                primary_method_id="model_divergence",
            )
        ]

        return RouteAdvisoryResult.from_per_model("model_agreement", per_model, params)
