"""Model agreement advisory — forecast confidence from cross-model divergence."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    EvidenceSample,
    format_extent,
    pct_above_threshold,
    summarize_evidence,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AgreementLevel,
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    HighlightSeverity,
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

        # Model agreement is cross-model — evaluated once, not per-model. One
        # evidence sample per route point (#393): ``affected`` = a POOR-agreement
        # point, so affected_nm is the geometry-accurate extent of the disagreeing
        # segment and coverage is derived from the same list. This advisory emits
        # no highlights (cross-model — the Compare view is its home), so the
        # ribbon severity is bookkeeping only.
        moderate_count = 0
        samples: list[EvidenceSample] = []

        for rpa in ctx.analyses:
            dist = rpa.distance_from_origin_nm or 0.0
            # A divergence with ``mean is None`` is "metric absent for every
            # model", which the scorer records as ``agreement=GOOD`` — "nothing
            # to disagree about", NOT "models agreed" (see models/analysis.py
            # ModelDivergence docstring). Branching on ``agreement`` alone counts
            # such a point as good agreement (#391); only real comparisons
            # (``mean is not None``) establish agreement, so a point with no real
            # comparison is unassessable and drops out of the denominator.
            real = [d for d in rpa.model_divergence if d.mean is not None] if rpa.model_divergence else []
            if not real:
                samples.append(EvidenceSample(
                    distance_nm=dist, assessed=False,
                    severity=HighlightSeverity.UNAVAILABLE,
                ))
                continue

            n_poor = sum(1 for d in real if d.agreement == AgreementLevel.POOR)
            has_poor = n_poor >= min_poor_vars
            has_moderate = any(d.agreement == AgreementLevel.MODERATE for d in real)

            moderate_only = has_moderate and not has_poor
            if moderate_only:
                moderate_count += 1
            samples.append(EvidenceSample(
                distance_nm=dist, assessed=True, affected=has_poor,
                severity=HighlightSeverity.AMBER if has_poor else HighlightSeverity.GREEN,
                # The "mostly good" sentence quotes the moderate-only points, a
                # different population from the grade's poor points — tagged so
                # it can measure its own geometry (#571 D1).
                tags=frozenset({"moderate"}) if moderate_only else frozenset(),
            ))

        summary = summarize_evidence(samples, ctx.total_distance_nm)
        total = summary.assessed
        poor_count = summary.affected
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
                "model_agreement.mostly_good", loc,
                extent=format_extent(summary.extent_of(lambda s: "moderate" in s.tags)),
            )
            else:
                detail = adv_t(
                "model_agreement.poor", loc, extent=format_extent(summary.extent),
            )

        # Coverage tolerance (#391): a "good agreement" verdict resting on real
        # comparisons at too few route points cannot vouch for the rest.
        if status == AdvisoryStatus.GREEN and summary.below_coverage:
            status = AdvisoryStatus.UNAVAILABLE
            detail = adv_t("model_agreement.no_data", loc)

        per_model = [ModelAdvisoryResult.build(
            model="all", status=status, detail=detail,
            affected=poor_count, total=total,
            total_distance_nm=ctx.total_distance_nm,
            affected_nm=summary.affected_nm,
        )]

        return RouteAdvisoryResult.from_per_model("model_agreement", per_model, params)
