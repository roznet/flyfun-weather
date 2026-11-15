"""Tests for the freezing precipitation advisory evaluator."""

from __future__ import annotations

from datetime import datetime

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.freezing_precip import FreezingPrecipEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    DerivedLevel,
    PrecipPhase,
    PrecipitationAssessment,
    RoutePointAnalysis,
    SoundingAnalysis,
)


def _rpa(i: int, sounding: SoundingAnalysis) -> RoutePointAnalysis:
    return RoutePointAnalysis(
        point_index=i, lat=48.0, lon=2.0 + i * 0.5,
        distance_from_origin_nm=i * 20.0,
        interpolated_time=datetime(2026, 1, 10, 10, 0),
        forecast_hour=datetime(2026, 1, 10, 10, 0),
        track_deg=90.0,
        sounding={"gfs": sounding},
    )


def _ctx(soundings: list[SoundingAnalysis]) -> RouteContext:
    return RouteContext(
        analyses=[_rpa(i, s) for i, s in enumerate(soundings)],
        cross_sections=[], elevation=None, models=["gfs"],
        cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        total_distance_nm=20.0 * max(len(soundings) - 1, 1),
    )


def _dry() -> SoundingAnalysis:
    """Benign point: dry assessment, no warm nose in the profile."""
    return SoundingAnalysis(
        precipitation=PrecipitationAssessment(),
        derived_levels=[
            DerivedLevel(pressure_hpa=1000, altitude_ft=400, wet_bulb_c=4.0),
            DerivedLevel(pressure_hpa=900, altitude_ft=3200, wet_bulb_c=0.5),
            DerivedLevel(pressure_hpa=800, altitude_ft=6400, wet_bulb_c=-4.0),
        ],
    )


def _active_fzra() -> SoundingAnalysis:
    return SoundingAnalysis(
        precipitation=PrecipitationAssessment(
            surface_phase=PrecipPhase.FREEZING_RAIN,
            freezing_rain_risk=True,
            total_mm=1.2,
        ),
    )


def _primed() -> SoundingAnalysis:
    """No active precip, but warm nose over sub-zero surface wet-bulb."""
    return SoundingAnalysis(
        precipitation=PrecipitationAssessment(),  # dry hour
        derived_levels=[
            DerivedLevel(pressure_hpa=1000, altitude_ft=500, wet_bulb_c=-2.0),
            DerivedLevel(pressure_hpa=925, altitude_ft=3000, wet_bulb_c=1.0),
            DerivedLevel(pressure_hpa=900, altitude_ft=4000, wet_bulb_c=1.5),
            DerivedLevel(pressure_hpa=800, altitude_ft=6500, wet_bulb_c=-5.0),
        ],
    )


def test_active_freezing_rain_is_red():
    ctx = _ctx([_dry()] * 9 + [_active_fzra()])
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.RED
    assert "Freezing precipitation" in result.per_model[0].detail


def test_ice_pellets_is_red():
    sounding = SoundingAnalysis(
        precipitation=PrecipitationAssessment(
            surface_phase=PrecipPhase.ICE_PELLETS, total_mm=0.8,
        ),
    )
    ctx = _ctx([_dry()] * 9 + [sounding])
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.RED


def test_primed_profile_is_amber():
    ctx = _ctx([_dry()] * 8 + [_primed()] * 2)  # 20% primed >= 5% default
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.AMBER
    assert "profile" in result.per_model[0].detail.lower()


def test_primed_below_threshold_is_green():
    ctx = _ctx([_dry()] * 8 + [_primed()] * 2)
    result = FreezingPrecipEvaluator.evaluate(ctx, {"extent_pct_amber": 30})
    assert result.aggregate_status == AdvisoryStatus.GREEN


def test_all_dry_is_green():
    ctx = _ctx([_dry()] * 10)
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.GREEN


def test_no_precip_data_is_unavailable():
    bare = SoundingAnalysis()  # no precipitation, no derived levels
    ctx = _ctx([bare] * 10)
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.per_model[0].status == AdvisoryStatus.UNAVAILABLE


def test_clear_verdict_low_coverage_is_unavailable():
    """A "no signature" verdict from assessable points at too few of the route.

    Regression for #391 review: 2 clear (dry, warm-profile) points among 8
    unassessable ones used to grade GREEN "No freezing precipitation signature".
    Below the coverage tolerance it is UNAVAILABLE.
    """
    bare = SoundingAnalysis()  # unassessable — no precip, no derived levels
    ctx = _ctx([_dry(), _dry()] + [bare] * 8)
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE


def test_active_fzra_low_coverage_still_red():
    """Active FZRA at a single assessed point among blanks still REDs (no blanking)."""
    bare = SoundingAnalysis()
    ctx = _ctx([_active_fzra()] + [bare] * 9)
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.RED


def test_unassessable_points_do_not_dilute_primed_percentage():
    """Points with no precip AND no thermodynamic profile leave the denominator.

    Regression for #391: 1 primed point among 24 unassessable (no precip, no
    derived_levels) points used to read 1/25 = 4% < 5% → GREEN, even though the
    only assessable point was primed. The honest reading is 1/1 = 100% → AMBER.
    """
    bare = SoundingAnalysis()  # unassessable — no precip, no derived levels
    ctx = _ctx([_primed()] + [bare] * 24)
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.AMBER
    assert result.per_model[0].total_points == 1


# --- Multi-model rule (#411, meteorology-decisions §81) -----------------------
#
# Under the default MAJORITY aggregation one model's freezing rain used to
# vanish: 1 RED among 2 GREEN read GREEN. The rule is a floor on top of
# MAJORITY: one valid model RED → at least AMBER, two or more → RED, never
# lower than MAJORITY gives.

import dataclasses

import pytest

from weatherbrief.analysis.advisories import invariants
from weatherbrief.models import (
    AdvisoryAggregation,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)


def _multi_ctx(per_model: dict[str, list[SoundingAnalysis]]) -> RouteContext:
    models = list(per_model)
    n = len(next(iter(per_model.values())))
    analyses = []
    for i in range(n):
        rpa = _rpa(i, per_model[models[0]][i])
        rpa.sounding = {m: per_model[m][i] for m in models}
        analyses.append(rpa)
    return RouteContext(
        analyses=analyses, cross_sections=[], elevation=None, models=models,
        cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        total_distance_nm=20.0 * max(n - 1, 1),
    )


_RED = [_dry()] * 9 + [_active_fzra()]
_GREEN = [_dry()] * 10
_AMBER = [_dry()] * 5 + [_primed()] * 5


def test_one_red_among_greens_floors_at_amber():
    ctx = _multi_ctx({"ecmwf": _GREEN, "gfs": _RED, "icon": _GREEN})
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.AMBER
    assert result.escalated_from == AdvisoryStatus.GREEN
    # The model that raised it speaks for it, and the card says it is alone.
    assert result.representative_model == "gfs"
    assert result.aggregate_detail.startswith("Freezing precipitation")
    assert "(gfs only, 1 of 3 models)" in result.aggregate_detail
    assert invariants.check_advisory(result) == []


def test_two_reds_are_red_even_against_a_larger_majority():
    ctx = _multi_ctx({
        "ecmwf": _RED, "gfs": _RED, "icon": _GREEN,
        "arome": _GREEN, "ukmo": _GREEN,
    })
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.RED
    assert result.escalated_from == AdvisoryStatus.GREEN
    assert result.representative_model in {"ecmwf", "gfs"}
    assert "(ecmwf, gfs only, 2 of 5 models)" in result.aggregate_detail
    assert invariants.check_advisory(result) == []


def test_two_valid_models_one_red_stays_red_untouched():
    """MAJORITY's tie-to-worst already gives RED; the rule never lowers it."""
    ctx = _multi_ctx({"ecmwf": _GREEN, "gfs": _RED})
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.RED
    assert result.escalated_from is None
    assert "only" not in result.aggregate_detail


def test_one_red_with_amber_majority_is_unchanged():
    ctx = _multi_ctx({"ecmwf": _AMBER, "gfs": _RED, "icon": _AMBER})
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.AMBER
    assert result.escalated_from is None
    assert result.representative_model in {"ecmwf", "icon"}


def test_one_red_with_green_majority_and_an_amber_speaks_for_the_red():
    """2 GREEN, 1 AMBER (primed), 1 RED: MAJORITY says GREEN, the floor AMBER.

    The AMBER model holds the grade, but the reason the card is AMBER is the RED
    model's freezing rain, so that is the sentence the card prints.
    """
    ctx = _multi_ctx({
        "ecmwf": _GREEN, "gfs": _RED, "icon": _GREEN, "arome": _AMBER,
    })
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.AMBER
    assert result.escalated_from == AdvisoryStatus.GREEN
    assert result.representative_model == "gfs"
    assert invariants.check_advisory(result) == []


def test_unavailable_models_do_not_count_in_the_total():
    no_data = [SoundingAnalysis()] * 10
    ctx = _multi_ctx({
        "ecmwf": _GREEN, "gfs": _RED, "icon": _GREEN, "ukmo": no_data,
    })
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.AMBER
    assert "1 of 3 models" in result.aggregate_detail


def test_all_green_is_untouched():
    ctx = _multi_ctx({"ecmwf": _GREEN, "gfs": _GREEN, "icon": _GREEN})
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.GREEN
    assert result.escalated_from is None


def test_worst_mode_is_unaffected():
    """WORST is already RED on one model; the registry re-aggregation keeps it."""
    from weatherbrief.analysis.advisories.registry import evaluate_all

    ctx = _multi_ctx({"ecmwf": _GREEN, "gfs": _RED, "icon": _GREEN})
    [result] = evaluate_all(
        ctx, enabled_ids={"freezing_precip"},
        aggregation=AdvisoryAggregation.WORST,
    )
    assert result.aggregate_status == AdvisoryStatus.RED
    assert result.escalated_from is None


def test_majority_through_the_registry_keeps_the_floor():
    from weatherbrief.analysis.advisories.registry import evaluate_all

    ctx = _multi_ctx({"ecmwf": _GREEN, "gfs": _RED, "icon": _GREEN})
    [result] = evaluate_all(ctx, enabled_ids={"freezing_precip"})
    assert result.aggregate_status == AdvisoryStatus.AMBER


def test_escalation_is_opt_in():
    """Every other advisory keeps plain MAJORITY (a GREEN over one RED, #578)."""
    def _m(model, status):
        return ModelAdvisoryResult.build(
            model=model, status=status, detail=status.value,
            affected=1 if status != AdvisoryStatus.GREEN else 0, total=10,
            total_distance_nm=200.0,
        )
    per_model = [
        _m("ecmwf", AdvisoryStatus.GREEN), _m("gfs", AdvisoryStatus.RED),
        _m("icon", AdvisoryStatus.GREEN),
    ]
    plain = RouteAdvisoryResult.from_per_model("x", per_model, {})
    assert plain.aggregate_status == AdvisoryStatus.GREEN
    assert plain.escalated_from is None


@pytest.mark.parametrize("locale, text", [
    ("fr", "(gfs uniquement, 1 sur 3 modèles)"),
    ("de", "(nur gfs, 1 von 3 Modellen)"),
])
def test_escalated_note_is_localised(locale, text):
    ctx = _multi_ctx({"ecmwf": _GREEN, "gfs": _RED, "icon": _GREEN})
    ctx = dataclasses.replace(ctx, locale=locale)
    result = FreezingPrecipEvaluator.evaluate(ctx, {})
    assert text in result.aggregate_detail


def test_an_escalation_without_a_red_model_is_a_violation():
    """Guards the relaxed invariant: the AMBER floor needs a RED behind it."""
    calm = ModelAdvisoryResult.build(
        model="gfs", status=AdvisoryStatus.GREEN, detail="clear",
        affected=0, total=10, total_distance_nm=300.0,
    )
    invented = RouteAdvisoryResult(
        advisory_id="freezing_precip", aggregate_status=AdvisoryStatus.AMBER,
        aggregate_detail="invented", per_model=[calm], representative_model="gfs",
        escalated_from=AdvisoryStatus.GREEN,
    )
    rules = {v.rule for v in invariants.check_advisory(invented)}
    assert "escalation_needs_a_red_model" in rules
    assert "aggregate_is_a_model_status" in rules
