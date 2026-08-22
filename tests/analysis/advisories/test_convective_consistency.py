"""The convective colour is graded once and consumed everywhere (§22).

Before this, ``ifr_feasibility`` carried its own re-derivation of "how bad is the
convection?" — its own ``convective_min_risk`` floor, its own 10 % red threshold,
no tops-below-cruise filter, and no awareness of the #442 DD-trigger amber. The
two advisories therefore disagreed *in both directions* on the same sounding, and
on the motivating flight the disagreement inverted the headline: `convective`
AMBER (3 models amber, 2 red) next to `ifr_feasibility` RED (2 green, 1 amber,
2 red — a majority tie broken to worst).

The invariant these tests defend: **for every model, the convective axis of
ifr_feasibility is the convective advisory's status for that model** — never a
second opinion. Exactly two deviations are sanctioned:

1. **Abstention** — the #391 thin-coverage guard may turn a would-be-GREEN
   convective axis UNAVAILABLE, which contributes nothing to the composite
   rather than grading convection differently.
2. **The EMBEDDED escalation** (§22) — character EMBEDDED bumps an AMBER
   convective axis to RED, and nothing else does. That is information the
   convective advisory does not measure (the deck hiding the cells) and the one
   band genuinely worse under IFR than VFR.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.convective import ConvectiveEvaluator
from weatherbrief.analysis.advisories.convective_grading import (
    CONVECTIVE_PARAM_DEFAULTS,
    grade_convective_model,
    resolve_convective_params,
)
from weatherbrief.analysis.advisories.ifr_feasibility import IFRFeasibilityEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    CloudCoverage,
    EnhancedCloudLayer,
    ConvectiveAssessment,
    ConvectiveRisk,
    NWPCloudDiagnostics,
    RoutePointAnalysis,
    SoundingAnalysis,
)
from weatherbrief.tasks.advise import _resolve_analyses

_MODELS = ["gfs"]


def _defaults(evaluator) -> dict:
    return {p.key: p.default for p in evaluator.catalog_entry().parameters}


def _ctx(
    soundings: list[SoundingAnalysis],
    *,
    cruise_ft: int = 8000,
    advisory_params: dict[str, dict[str, float]] | None = None,
) -> RouteContext:
    analyses = [
        RoutePointAnalysis(
            point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
            interpolated_time=datetime(2026, 3, 1, 10, 0),
            forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
            sounding={"gfs": s},
        )
        for i, s in enumerate(soundings)
    ]
    resolved = _resolve_analyses(
        analyses, icing_method=None, cloud_source=None, convective_method="nwp",
    )
    return RouteContext(
        analyses=resolved, cross_sections=[], elevation=None, models=_MODELS,
        cruise_altitude_ft=cruise_ft, flight_ceiling_ft=cruise_ft + 4000,
        total_distance_nm=len(soundings) * 20.0,
        advisory_params=advisory_params or {},
    )


def _quiet_nwp_loaded_dd(thermo_risk: ConvectiveRisk) -> SoundingAnalysis:
    """The loaded-gun point: model's own scheme quiet, thermodynamics loaded.

    This is the shape that produced the whole bug — #442 grades it AMBER via
    ``dd_trigger``, and the old ifr_feasibility (reading the raw quiet NWP track)
    saw nothing at all.
    """
    return SoundingAnalysis(
        convective_nwp=ConvectiveAssessment(
            risk_level=ConvectiveRisk.NONE, cape_jkg=None, base_ft=None, top_ft=None,
        ),
        convective_thermo=ConvectiveAssessment(
            risk_level=thermo_risk, cape_jkg=2500, base_ft=3000, top_ft=30000,
        ),
    )


def _nwp_cell(nwp_risk: ConvectiveRisk, *, top_ft: int = 30000) -> SoundingAnalysis:
    return SoundingAnalysis(
        convective_nwp=ConvectiveAssessment(
            risk_level=nwp_risk, cape_jkg=2500, base_ft=3000, top_ft=top_ft,
        ),
        convective_thermo=ConvectiveAssessment(
            risk_level=nwp_risk, cape_jkg=2500, base_ft=3000, top_ft=top_ft,
        ),
    )


def _quiet() -> SoundingAnalysis:
    return SoundingAnalysis(
        convective_nwp=ConvectiveAssessment(
            risk_level=ConvectiveRisk.NONE, cape_jkg=None, base_ft=None, top_ft=None,
        ),
        convective_thermo=ConvectiveAssessment(
            risk_level=ConvectiveRisk.NONE, cape_jkg=10, base_ft=None, top_ft=None,
        ),
    )


def _conv_status(ctx: RouteContext) -> AdvisoryStatus:
    res = ConvectiveEvaluator.evaluate(ctx, _defaults(ConvectiveEvaluator))
    return next(m for m in res.per_model if m.model == "gfs").status


def _ifr_status(ctx: RouteContext) -> AdvisoryStatus:
    res = IFRFeasibilityEvaluator.evaluate(ctx, _defaults(IFRFeasibilityEvaluator))
    return next(m for m in res.per_model if m.model == "gfs").status


# Scenarios spanning every branch of the grading: the DD-trigger amber that the
# old IFR path missed entirely, a real NWP cell, a HIGH red, a cell that tops out
# below cruise (which only the convective advisory used to filter), and quiet air.
_SCENARIOS = {
    "dd_trigger_amber": [_quiet_nwp_loaded_dd(ConvectiveRisk.HIGH)] * 6,
    "dd_trigger_sparse": [_quiet_nwp_loaded_dd(ConvectiveRisk.MODERATE)] + [_quiet()] * 5,
    "nwp_moderate": [_nwp_cell(ConvectiveRisk.MODERATE)] + [_quiet()] * 5,
    "nwp_high_red": [_nwp_cell(ConvectiveRisk.HIGH)] * 6,
    "nwp_high_sparse": [_nwp_cell(ConvectiveRisk.HIGH)] + [_quiet()] * 5,
    "tops_below_cruise": [_nwp_cell(ConvectiveRisk.HIGH, top_ft=4000)] * 6,
    "all_quiet": [_quiet()] * 6,
}


@pytest.mark.parametrize("name", sorted(_SCENARIOS))
def test_ifr_convective_axis_equals_convective_advisory(name: str):
    """The invariant: same sounding ⇒ same convective verdict on both advisories.

    Here no icing and no airport conditions exist, so ifr_feasibility's composite
    IS its convective axis — any divergence is a real divergence, not another
    axis winning the ``worst``.
    """
    ctx = _ctx(_SCENARIOS[name])
    conv = _conv_status(ctx)
    ifr = _ifr_status(ctx)
    assert ifr == conv, f"{name}: ifr_feasibility says {ifr}, convective says {conv}"


def test_dd_trigger_amber_reaches_ifr_feasibility():
    """The specific regression: the loaded-gun amber used to be invisible to IFR.

    Pre-§22 this route graded convective AMBER (dd_trigger) and ifr_feasibility
    GREEN — the divergence that, aggregated across five models, flipped the
    briefing headline from AMBER to RED.
    """
    ctx = _ctx(_SCENARIOS["dd_trigger_amber"])
    assert _conv_status(ctx) == AdvisoryStatus.AMBER
    assert _ifr_status(ctx) == AdvisoryStatus.AMBER


def test_dd_alone_never_reds_ifr_feasibility():
    """#442's core promise now holds through the composite too.

    A quiet scheme under loaded thermodynamics caps at AMBER on the convective
    advisory; IFR feasibility must not be the back door that turns it red.
    """
    ctx = _ctx([_quiet_nwp_loaded_dd(ConvectiveRisk.EXTREME)] * 6)
    assert _conv_status(ctx) == AdvisoryStatus.AMBER
    assert _ifr_status(ctx) == AdvisoryStatus.AMBER


def test_ifr_honours_tops_below_cruise_filter():
    """IFR had no altitude filter at all, despite ``altitude_dependent=True``.

    A HIGH cell topping out at 4,000 ft is not a hazard at FL80 — the convective
    advisory ignored it and ifr_feasibility went RED on it.
    """
    ctx = _ctx([_nwp_cell(ConvectiveRisk.HIGH, top_ft=4000)] * 6, cruise_ft=8000)
    assert _conv_status(ctx) == AdvisoryStatus.GREEN
    assert _ifr_status(ctx) == AdvisoryStatus.GREEN


def test_ifr_convective_follows_user_tuning_of_the_convective_advisory():
    """Tuning lives in one place: raise the convective floor and IFR follows.

    ``min_risk=4`` (HIGH) means a MODERATE cell no longer counts — and because
    the composite grades through the convective advisory's parameters, that one
    edit moves both cards. Under the old duplicated thresholds it moved neither.
    """
    soundings = [_nwp_cell(ConvectiveRisk.MODERATE)] * 6
    tuned = {"convective": {**CONVECTIVE_PARAM_DEFAULTS, "min_risk": 4}}
    ctx = _ctx(soundings, advisory_params=tuned)

    # Untuned baseline: MODERATE at 6/6 points is 100 % coverage, past the 50 %
    # red threshold on the model's own track — RED.
    assert _conv_status(_ctx(soundings)) == AdvisoryStatus.RED
    # The convective advisory is invoked with its own params by evaluate_all; the
    # composite resolves them off the context.
    conv_tuned = ConvectiveEvaluator.evaluate(ctx, tuned["convective"])
    assert conv_tuned.per_model[0].status == AdvisoryStatus.GREEN
    assert _ifr_status(ctx) == AdvisoryStatus.GREEN


def test_resolve_convective_params_falls_back_to_catalog_defaults():
    """A disabled convective advisory must not read as "no convection".

    With no overrides on the context the composite still grades convection, using
    the catalog defaults — abstaining here would be the #391 false-GREEN.
    """
    ctx = _ctx([_quiet()] * 3)
    assert resolve_convective_params(ctx) == CONVECTIVE_PARAM_DEFAULTS


def test_grade_geometry_is_index_aligned_with_analyses():
    """The composite zips the grade's per-point lists against its own pass.

    If the grading ever stopped emitting exactly one entry per route point, the
    composite would silently attribute convection to the wrong point.
    """
    ctx = _ctx([_nwp_cell(ConvectiveRisk.MODERATE), _quiet(), _nwp_cell(ConvectiveRisk.HIGH)])
    grade = grade_convective_model(ctx, "gfs", resolve_convective_params(ctx))
    assert len(grade.ribbon_points) == len(ctx.analyses)
    assert len(grade.region_cells) == len(ctx.analyses)


# ---------------------------------------------------------------------------
# The EMBEDDED escalation (§22) — the one sanctioned way IFR may exceed
# convective, and the bands that must NOT move it.
# ---------------------------------------------------------------------------


def _cell_under_deck(
    nwp_risk: ConvectiveRisk = ConvectiveRisk.MODERATE,
) -> SoundingAnalysis:
    """A realized cell with an OVC deck beneath cruise — the embedded geometry.

    ``realized`` needs a firing signal: the native convective-precip channel
    supplies it (and resolved base/top geometry would too). The OVC layer based
    below cruise, with high bulk low cover, is what ``_point_embedded`` reads.
    """
    return SoundingAnalysis(
        convective_nwp=ConvectiveAssessment(
            risk_level=nwp_risk, cape_jkg=1200, base_ft=6000, top_ft=28000,
            convective_precip_mm_h=1.5,
        ),
        convective_thermo=ConvectiveAssessment(
            risk_level=nwp_risk, cape_jkg=1200, base_ft=6000, top_ft=28000,
        ),
        cloud_layers=[
            EnhancedCloudLayer(base_ft=2000, top_ft=7000, coverage=CloudCoverage.OVC),
        ],
        cloud_cover_low_pct=90.0,
        nwp_cloud_diagnostics=NWPCloudDiagnostics(convective_precip_mm_h=1.5),
    )


def _cell_in_clear_air(
    nwp_risk: ConvectiveRisk = ConvectiveRisk.MODERATE,
) -> SoundingAnalysis:
    """The same realized cell with no deck — isolated/scattered, not embedded."""
    return SoundingAnalysis(
        convective_nwp=ConvectiveAssessment(
            risk_level=nwp_risk, cape_jkg=1200, base_ft=6000, top_ft=28000,
            convective_precip_mm_h=1.5,
        ),
        convective_thermo=ConvectiveAssessment(
            risk_level=nwp_risk, cape_jkg=1200, base_ft=6000, top_ft=28000,
        ),
        cloud_layers=[],
        nwp_cloud_diagnostics=NWPCloudDiagnostics(convective_precip_mm_h=1.5),
    )


def _character(ctx: RouteContext):
    from weatherbrief.analysis.advisories.convective_character import (
        classify_route_character,
        resolve_character_params,
    )
    return classify_route_character(ctx, "gfs", resolve_character_params(ctx))


def test_embedded_escalates_amber_convective_to_red_ifr():
    """Cells you cannot see, in cloud, without radar: worse under IFR than VFR."""
    from weatherbrief.models import ConvectiveCharacter

    # Three cells in sixteen points: MODERATE, so convective floors at AMBER
    # (18.75 % is under the 20 % amber coverage threshold) rather than crossing
    # the 50 % red one. Three is the minimum cell population the embedded
    # fraction is evaluated over (#568, ``CHAR_EMBED_MIN_CELLS``) — below it the
    # 100 %-embedded fraction describes an extent too small to call the route.
    ctx = _ctx([_cell_under_deck()] * 3 + [_quiet()] * 13)
    assert _character(ctx) is ConvectiveCharacter.EMBEDDED
    assert _conv_status(ctx) == AdvisoryStatus.AMBER
    assert _ifr_status(ctx) == AdvisoryStatus.RED


def test_non_embedded_character_never_escalates_ifr():
    """ISOLATED/SCATTERED are see-and-avoid statements — they do not transfer to IMC.

    Same cell, same convective grade, no deck: the composite must track the
    convective advisory exactly.
    """
    from weatherbrief.models import ConvectiveCharacter

    ctx = _ctx([_cell_in_clear_air()] + [_quiet()] * 5)
    assert _character(ctx) is not ConvectiveCharacter.EMBEDDED
    assert _conv_status(ctx) == AdvisoryStatus.AMBER
    assert _ifr_status(ctx) == AdvisoryStatus.AMBER


def test_embedded_does_not_lift_a_green_convective_axis():
    """A GREEN convective axis has no cells reaching cruise for a deck to hide.

    The escalation is one step from AMBER only — it must never invent a hazard
    where the severity formula found none.
    """
    ctx = _ctx([_quiet()] * 6)
    assert _conv_status(ctx) == AdvisoryStatus.GREEN
    assert _ifr_status(ctx) == AdvisoryStatus.GREEN


def test_embedded_escalation_is_named_in_the_detail():
    """A red the convective card does not show must say why."""
    res = IFRFeasibilityEvaluator.evaluate(
        _ctx([_cell_under_deck()] * 3 + [_quiet()] * 13),
        _defaults(IFRFeasibilityEvaluator),
    )
    detail = next(m for m in res.per_model if m.model == "gfs").detail
    assert "embedded in cloud" in detail


# ---------------------------------------------------------------------------
# Params of a DISABLED advisory must not govern a composite (§22 review).
# ---------------------------------------------------------------------------


def test_disabled_convective_params_do_not_reach_the_composite():
    """A profile can hold params for an advisory the pilot switched off.

    ``enabled`` and ``params`` are independent maps in the profile, and the
    settings page renders a disabled advisory's inputs greyed out — so those
    values read as inert to the pilot. If ``evaluate_all`` published them anyway,
    IFR Feasibility would be silently graded against thresholds the UI says have
    no effect: the §22 divergence rebuilt between what the pilot sees and what
    the pilot configured.

    Here ``min_risk: 4`` (HIGH) would green out a route of MODERATE cells. With
    `convective` DISABLED that tuning must be ignored and the catalog defaults
    apply, so IFR still grades the convection.
    """
    from weatherbrief.analysis.advisories import evaluate_all

    ctx = _ctx([_nwp_cell(ConvectiveRisk.MODERATE)] * 6)
    user_params = {"convective": {**CONVECTIVE_PARAM_DEFAULTS, "min_risk": 4}}

    # convective enabled → its tuning applies to both cards.
    on = evaluate_all(
        ctx, enabled_ids={"convective", "ifr_feasibility"}, user_params=user_params,
    )
    ifr_on = next(r for r in on if r.advisory_id == "ifr_feasibility")
    assert ifr_on.per_model[0].status == AdvisoryStatus.GREEN

    # convective disabled → the stale tuning is NOT published; defaults grade it.
    off = evaluate_all(
        ctx, enabled_ids={"ifr_feasibility"}, user_params=user_params,
    )
    ifr_off = next(r for r in off if r.advisory_id == "ifr_feasibility")
    assert ifr_off.per_model[0].status == AdvisoryStatus.RED


def test_enabled_advisory_params_still_reach_the_composite():
    """The filter must not throw the baby out: enabled ⇒ tuning still applies.

    4 MODERATE cells in 6 points = 67 % coverage. Under the default 50 % red
    threshold that is RED; raising the threshold to 95 % drops it to AMBER. Both
    cards must move together — which also proves the tuned value (not the
    catalog default) reached the composite.
    """
    from weatherbrief.analysis.advisories import evaluate_all

    soundings = [_nwp_cell(ConvectiveRisk.MODERATE)] * 4 + [_quiet()] * 2
    ids = {"convective", "ifr_feasibility"}

    baseline = evaluate_all(_ctx(soundings), enabled_ids=ids, user_params={})
    assert next(
        r for r in baseline if r.advisory_id == "convective"
    ).per_model[0].status == AdvisoryStatus.RED

    tuned = evaluate_all(
        _ctx(soundings), enabled_ids=ids,
        user_params={"convective": {**CONVECTIVE_PARAM_DEFAULTS, "affected_pct_red": 95}},
    )
    conv = next(r for r in tuned if r.advisory_id == "convective")
    ifr = next(r for r in tuned if r.advisory_id == "ifr_feasibility")
    assert conv.per_model[0].status == AdvisoryStatus.AMBER
    assert ifr.per_model[0].status == AdvisoryStatus.AMBER


@pytest.mark.parametrize("name", sorted(_SCENARIOS))
def test_affected_equals_flagged_cell_count(name: str):
    """``grade.affected`` IS the flagged-cell count — pinned, because IFR relies on it.

    ``_check_enroute_hazards`` used to re-walk ``region_cells`` to count the
    convective points for its detail extent; it now reads ``grade.affected``
    directly. That is correct only because ``grade_convective_model`` appends a
    ``FlaggedCell`` for exactly the points it increments ``affected`` on, in the
    same block after every filter. If the two ever diverged, the IFR card's
    convective extent would silently misreport.
    """
    ctx = _ctx(_SCENARIOS[name])
    grade = grade_convective_model(ctx, "gfs", resolve_convective_params(ctx))
    cells = sum(1 for _, cell in grade.region_cells if cell is not None)
    assert cells == grade.affected
