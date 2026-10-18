"""Convective GREEN-by-absence (#807, pipeline review 2026-08 §2.6).

A sounding with no convective assessment (no CAPE from any source and no native
NWP track) is *unassessed*, not clear. ``grade_convective_model`` leaves it out of
the grading denominator and, like the eleven other route evaluators (#391), a
would-be-GREEN model whose assessed points cover too little of the route grades
UNAVAILABLE. Flagged verdicts always stand. §26 fallback points (thermo-graded
where the NWP track is absent) ARE assessed and count as coverage.
"""

from __future__ import annotations

from datetime import datetime

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.convective import ConvectiveEvaluator
from weatherbrief.analysis.advisories.convective_grading import (
    grade_convective_model,
    resolve_convective_params,
)
from weatherbrief.models import (
    AdvisoryStatus,
    ConvectiveAssessment,
    ConvectiveRisk,
    HighlightSeverity,
    RoutePointAnalysis,
    SoundingAnalysis,
)
from weatherbrief.tasks.advise import _resolve_analyses

_MODEL = "gfs"


def _ctx(soundings: list[SoundingAnalysis | None], cruise_ft: int = 18000) -> RouteContext:
    analyses = [
        RoutePointAnalysis(
            point_index=i, lat=46.0, lon=3.0, distance_from_origin_nm=i * 10.0,
            interpolated_time=datetime(2026, 8, 27, 9, 0),
            forecast_hour=datetime(2026, 8, 27, 9, 0), track_deg=350.0,
            sounding={_MODEL: s} if s is not None else {},
        )
        for i, s in enumerate(soundings)
    ]
    resolved = _resolve_analyses(
        analyses, icing_method=None, cloud_source=None, convective_method="nwp",
    )
    return RouteContext(
        analyses=resolved, cross_sections=[], elevation=None, models=[_MODEL],
        cruise_altitude_ft=cruise_ft, flight_ceiling_ft=cruise_ft,
        total_distance_nm=len(soundings) * 10.0,
    )


def _grade(ctx: RouteContext):
    return grade_convective_model(ctx, _MODEL, resolve_convective_params(ctx))


def _quiet() -> SoundingAnalysis:
    """Assessed and quiet on both tracks."""
    thermo = ConvectiveAssessment(risk_level=ConvectiveRisk.NONE, cape_jkg=0.0)
    nwp = ConvectiveAssessment(risk_level=ConvectiveRisk.NONE, method="nwp", cover_pct=0.0)
    return SoundingAnalysis(convective=thermo, convective_thermo=thermo, convective_nwp=nwp)


def _unassessed() -> SoundingAnalysis:
    """A sounding with no convective assessment on either track (no CAPE, no scheme)."""
    return SoundingAnalysis(convective=None, convective_thermo=None, convective_nwp=None)


def _native_moderate() -> SoundingAnalysis:
    thermo = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, cape_jkg=600, base_ft=4000, top_ft=30000,
    )
    nwp = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, method="nwp", cover_pct=40.0,
        base_ft=4000, top_ft=30000,
    )
    return SoundingAnalysis(convective=thermo, convective_thermo=thermo, convective_nwp=nwp)


def _fallback_quiet() -> SoundingAnalysis:
    """§26 shape: thermo assessed (NONE), NWP track absent."""
    thermo = ConvectiveAssessment(risk_level=ConvectiveRisk.NONE, cape_jkg=10.0)
    return SoundingAnalysis(convective=thermo, convective_thermo=thermo, convective_nwp=None)


def test_mostly_unassessed_model_is_unavailable_not_green():
    ctx = _ctx([_quiet()] * 3 + [_unassessed()] * 7)
    grade = _grade(ctx)
    assert grade.total == 10
    assert grade.assessed == 3
    assert grade.status == AdvisoryStatus.UNAVAILABLE
    # Unassessed points never ribbon GREEN.
    sev = [s for _, s in grade.ribbon_points]
    assert sev.count(HighlightSeverity.UNAVAILABLE) == 7
    assert sev.count(HighlightSeverity.GREEN) == 3


def test_all_unassessed_is_unavailable():
    grade = _grade(_ctx([_unassessed()] * 5))
    assert grade.assessed == 0
    assert grade.status == AdvisoryStatus.UNAVAILABLE


def test_sufficiently_assessed_quiet_model_stays_green():
    grade = _grade(_ctx([_quiet()] * 6 + [_unassessed()] * 4))
    assert grade.assessed == 6
    assert grade.status == AdvisoryStatus.GREEN


def test_missing_soundings_count_against_coverage_too():
    """The domain is the whole route: thin sounding coverage abstains as well."""
    grade = _grade(_ctx([_quiet()] * 2 + [None] * 8))
    assert grade.total == 2
    assert grade.status == AdvisoryStatus.UNAVAILABLE


def test_flagged_verdict_on_thin_coverage_stands():
    """Only a would-be GREEN is demoted; real hazard evidence is never diluted."""
    grade = _grade(_ctx([_native_moderate()] * 2 + [_unassessed()] * 8))
    assert grade.status in (AdvisoryStatus.AMBER, AdvisoryStatus.RED)
    assert grade.affected_mod == 2


def test_unassessed_points_leave_the_extent_domain():
    """An unassessed point must not dilute the flagged extent as if it were clear."""
    graded_all = _grade(_ctx([_native_moderate()] * 2 + [_quiet()] * 8))
    graded_partial = _grade(_ctx([_native_moderate()] * 2 + [_quiet()] * 3 + [_unassessed()] * 5))
    assert graded_partial.extent.domain_nm < graded_all.extent.domain_nm
    assert graded_partial.extent.pct > graded_all.extent.pct


def test_section26_fallback_points_count_as_assessed():
    """An absent NWP track graded on an assessed thermo NONE is coverage (§26)."""
    grade = _grade(_ctx([_fallback_quiet()] * 10))
    assert grade.assessed == 10
    assert grade.status == AdvisoryStatus.GREEN


def test_evaluator_wording_and_status_for_unassessed_model():
    ctx = _ctx([_quiet()] * 3 + [_unassessed()] * 7)
    result = ConvectiveEvaluator.evaluate(ctx, resolve_convective_params(ctx))
    (m,) = result.per_model
    assert m.status == AdvisoryStatus.UNAVAILABLE
    assert m.total_points == 3
