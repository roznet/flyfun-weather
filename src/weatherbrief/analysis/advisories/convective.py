"""Convective advisory — can fly around convective activity."""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    extent_min_nm_param,
    build_regions,
    build_ribbon,
    driving_method_id,
    format_extent,
)
from weatherbrief.analysis.advisories.convective_grading import (
    CONVECTIVE_PARAM_DEFAULTS,
    RISK_ORDER as _RISK_ORDER,
    grade_convective_model,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.analysis.sounding.convective import _FIRING_PRECIP_MM_H
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryHighlights,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ConvectiveRisk,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)


def _scheme_realized(max_precip_mm_h: float | None) -> bool:
    """True when the model's own scheme is actually precipitating convectively.

    Shares the sounding layer's firing floor so the advisory copy and the tier
    that produced it can't drift apart: above that floor the ``nwp_precip``
    ladder has already fired (MARGINAL/LOW/MODERATE), so the model is realizing
    convection even though its tier may sit below MODERATE — that ladder is
    capped because depth is unknown from rate alone, not because the scheme is
    quiet. ``None`` (no native precip channel at all, e.g. a model whose
    diagnostics don't carry it) is NOT realization — unknown is not firing.
    """
    return max_precip_mm_h is not None and max_precip_mm_h > _FIRING_PRECIP_MM_H


def _coverage_suffix(max_cover_pct: float | None) -> str:
    """Append coverage label when NWP convective cover data is available."""
    if max_cover_pct is None:
        return ""
    if max_cover_pct >= 75:
        label = "extensive"
    elif max_cover_pct >= 50:
        label = "widespread"
    elif max_cover_pct >= 25:
        label = "scattered"
    else:
        label = "isolated"
    return f", {label} ({int(max_cover_pct)}% cover)"


@register
class ConvectiveEvaluator:
    """Evaluates convective activity along the route."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="convective",
            name="Convective Activity",
            short_description="Can fly around convective activity",
            description=(
                "Uses convective risk assessment per point. "
                "Points where tops are below cruise altitude (with clearance margin) are ignored. "
                "High/Extreme risk at any point triggers RED."
            ),
            category="convective",
            timing_class="scan",
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="min_risk",
                    label="Min risk level",
                    description="Minimum risk level that counts (0=NONE, 1=MARGINAL, 2=LOW, 3=MODERATE)",
                    type="number",
                    default=CONVECTIVE_PARAM_DEFAULTS["min_risk"],
                    min=1,
                    max=4,
                    step=1,
                ),
                AdvisoryParameterDef(
                    key="extent_pct_amber",
                    label="% of route with convection (amber)",
                    description="Route percentage affected for amber",
                    type="percent",
                    unit="%",
                    default=CONVECTIVE_PARAM_DEFAULTS["extent_pct_amber"],
                    min=5,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="extent_pct_red",
                    label="% of route with convection (red)",
                    description="Route percentage affected for red",
                    type="percent",
                    unit="%",
                    default=CONVECTIVE_PARAM_DEFAULTS["extent_pct_red"],
                    min=10,
                    max=100,
                    step=5,
                ),
                extent_min_nm_param(),
                AdvisoryParameterDef(
                    key="top_clearance_ft",
                    label="Top clearance (ft)",
                    description="Ignore convection whose tops are this far below cruise altitude",
                    type="number",
                    unit="ft",
                    default=CONVECTIVE_PARAM_DEFAULTS["top_clearance_ft"],
                    min=0,
                    max=5000,
                    step=500,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        per_model: list[ModelAdvisoryResult] = []
        peak_by_model: dict[str, ConvectiveRisk] = {}
        # Did each model's own scheme actually precipitate? Mirrors the
        # per-model wording choice into the cross-model headline below.
        realized_by_model: dict[str, bool] = {}

        for model in ctx.models:
            # The colour, the extents and the cross-check all come from the
            # shared formula (meteorology-decisions §18/§22) — this evaluator
            # owns only the locale wording built from them. ifr_feasibility
            # grades its convective axis through the SAME call, so the two can
            # no longer diverge.
            grade = grade_convective_model(ctx, model, params)
            total = grade.total
            affected = grade.affected
            affected_mod = grade.affected_mod
            worst_risk = grade.worst_risk
            below_cruise_count = grade.below_cruise_count
            max_cover_pct = grade.max_cover_pct
            max_precip_mm_h = grade.max_precip_mm_h
            status = grade.status
            cross_check = grade.cross_check

            # Each string quotes the extent of the population it names (#571
            # D1): ``ext_mod`` is the MODERATE+ geometry, not a share of the
            # any-risk union scaled to look like one.
            ext = format_extent(grade.extent)
            ext_mod = format_extent(grade.extent_mod)
            loc = ctx.locale
            cover_suffix = _coverage_suffix(max_cover_pct)
            # Wording only — the colour was decided by ``grade_convective_model``.
            if total == 0:
                detail = adv_t("no_data", loc)
            elif affected == 0:
                if below_cruise_count > 0:
                    detail = adv_t("convective.below_cruise", loc, count=below_cruise_count)
                else:
                    detail = adv_t("convective.none", loc)
            else:
                # Headline anchors on the MODERATE+ extent + named peak, so the
                # severity word (peak) and the coverage (MODERATE+ extent) are
                # never conflated. When nothing reaches MODERATE (LOW-only floor),
                # fall back to "primed, not firing" so favorable-but-quiet CAPE
                # doesn't masquerade as active convection (#300).
                if affected_mod > 0:
                    detail = adv_t(
                        "convective.risk_over_mod", loc,
                        extent=ext_mod, peak=worst_risk.value.upper(),
                    ) + cover_suffix
                elif _scheme_realized(max_precip_mm_h):
                    # The scheme IS precipitating convectively — it just can't
                    # say how deep, so the nwp_precip ladder capped its tier
                    # below MODERATE. Saying "primed, not firing" here is
                    # factually wrong and reads as "this model sees nothing":
                    # observed on EGTF→BIG→LFAT→LFQA 2026-07-17, where ECMWF was
                    # the HARDEST-raining model on the route (1.96 mm/h native
                    # convective precip at LFQA, 5x ICON's 0.40) and was
                    # described to the pilot as not firing, while ICON graded
                    # RED off a diagnosed tower. Realization, not tier, picks
                    # the wording.
                    detail = adv_t(
                        "convective.realized_low", loc, extent=ext,
                    ) + cover_suffix
                else:
                    # Genuinely quiet scheme with favourable CAPE (#300).
                    # Calibrated for the default LOW floor (min_risk=2): "primed,
                    # not firing" fits a LOW peak. Under a non-default min_risk=1
                    # a MARGINAL-only route also lands here and the wording is a
                    # slight overstatement — accepted (rare, non-default; #302
                    # review).
                    detail = adv_t("convective.favorability", loc, extent=ext) + cover_suffix

            # Build highlights only when the model has data (total > 0).
            highlights = None
            if total > 0:
                highlights = AdvisoryHighlights(
                    ribbon=build_ribbon(grade.ribbon_points, ctx.total_distance_nm),
                    regions=build_regions(grade.region_cells, ctx.total_distance_nm),
                    peak_dist_nm=grade.peak_dist_nm,
                )

            per_model.append(ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=affected, total=total,
                total_distance_nm=ctx.total_distance_nm,
                affected_mod=affected_mod,
                extent=grade.extent,
                extent_mod=grade.extent_mod,
                cross_check=cross_check,
                highlights=highlights,
                primary_method_id=driving_method_id(highlights, status),
            ))
            peak_by_model[model] = worst_risk
            realized_by_model[model] = _scheme_realized(max_precip_mm_h)

        result = RouteAdvisoryResult.from_per_model("convective", per_model, params)
        # Override the generic representative-model detail with a cross-model
        # MODERATE+ range + peak, built here where the locale is available
        # (from_per_model is locale-agnostic). GREEN/UNAVAILABLE keep the
        # representative wording (none / below-cruise / no-data). (#300)
        # The per-model cover_suffix is intentionally dropped from this aggregate
        # line: a single representative model's cover is arbitrary across a
        # cross-model range. Cover stays visible in the per-model breakdowns
        # (#302 review).
        agg = result.aggregate_status
        if agg in (AdvisoryStatus.AMBER, AdvisoryStatus.RED):
            loc = ctx.locale
            matching = [m for m in per_model if m.status == agg]
            mod_models = [m for m in matching if m.affected_mod_points > 0]
            if mod_models:
                peak = max(
                    (peak_by_model[m.model] for m in mod_models),
                    key=lambda r: _RISK_ORDER.index(r),
                ).value.upper()
                pcts = sorted(round(m.affected_mod_pct) for m in mod_models)
                lo, hi = pcts[0], pcts[-1]
                if lo == hi:
                    result.aggregate_detail = adv_t(
                        "convective.risk_over_mod_pct", loc, pct=lo, peak=peak
                    )
                else:
                    result.aggregate_detail = adv_t(
                        "convective.risk_over_range", loc, min=lo, max=hi, peak=peak
                    )
            else:
                # LOW-only across every supporting model — favorability range.
                pcts = sorted(
                    round(m.affected_pct) for m in matching if m.total_points > 0
                )
                if pcts:
                    lo, hi = pcts[0], pcts[-1]
                    # Same realization-not-tier rule as the per-model detail: if
                    # ANY supporting model is actually raining convectively, the
                    # cross-model headline must not say "not firing" either.
                    realized = any(
                        realized_by_model.get(m.model, False)
                        for m in matching if m.total_points > 0
                    )
                    stem = "realized_low" if realized else "favorability"
                    if lo == hi:
                        result.aggregate_detail = adv_t(
                            f"convective.{stem}_pct", loc, pct=lo
                        )
                    else:
                        result.aggregate_detail = adv_t(
                            f"convective.{stem}_range", loc, min=lo, max=hi
                        )
        return result
