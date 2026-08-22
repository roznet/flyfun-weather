"""A model with NO native convective track is capped at AMBER (#568).

§18 (meteorology-decisions) established that DD/thermo alone can never produce a
convective RED: when the model's own scheme is *quiet* under a loaded sounding,
the point is raised to AMBER and excluded from the red-coverage count. That rule
keyed on the cross-check's ``dd_not_corroborated`` — which requires an NWP
assessment to compare against — so it had a hole exactly where there was no NWP
assessment at all.

Production shape of the hole (LFMD→EGTF 2026-08-27, pack ``2026-08-22T14-41-23``):
ICON-EU's GRIB run was 20 minutes short of covering a ~113 h flight, so every
route point had ``nwp_cloud_diagnostics is None`` → ``convective_nwp is None`` →
``_resolve_analyses`` silently fell back to thermo. ICON was then graded on MetPy
parcel CAPE while GFS and ECMWF were graded on their own convective schemes, and
its identical thermodynamic signal went uncapped to RED (59/64 points, dd_trigger
0) beside GFS AMBER (15/15 all dd_trigger) and ECMWF AMBER (42/38).

The discriminator is ``SoundingAnalysis.convective_nwp_fallback``, set in
``_resolve_analyses`` — the only place that knows the *requested* method. The two
obvious substitutes are both wrong and each has a test below:
``convective_nwp is None`` is False under an explicit thermo request, and
``convective_method_effective == "thermo"`` cannot tell the two apart at all.
"""

from __future__ import annotations

from datetime import datetime

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.convective_grading import (
    grade_convective_model,
    resolve_convective_params,
)
from weatherbrief.models import (
    AdvisoryStatus,
    ConvectiveAssessment,
    ConvectiveRisk,
    NWPCloudDiagnostics,
    RoutePointAnalysis,
    SoundingAnalysis,
)
from weatherbrief.tasks.advise import _resolve_analyses

_MODEL = "icon"


def _ctx(
    soundings: list[SoundingAnalysis],
    *,
    convective_method: str = "nwp",
    cruise_ft: int = 18000,
) -> RouteContext:
    analyses = [
        RoutePointAnalysis(
            point_index=i, lat=46.0, lon=3.0, distance_from_origin_nm=i * 10.0,
            interpolated_time=datetime(2026, 8, 27, 9, 0),
            forecast_hour=datetime(2026, 8, 27, 9, 0), track_deg=350.0,
            sounding={_MODEL: s},
        )
        for i, s in enumerate(soundings)
    ]
    resolved = _resolve_analyses(
        analyses,
        icing_method=None,
        cloud_source=None,
        convective_method=convective_method,
    )
    return RouteContext(
        analyses=resolved, cross_sections=[], elevation=None, models=[_MODEL],
        cruise_altitude_ft=cruise_ft, flight_ceiling_ft=cruise_ft,
        total_distance_nm=len(soundings) * 10.0,
    )


def _grade(ctx: RouteContext):
    return grade_convective_model(ctx, _MODEL, resolve_convective_params(ctx))


def _no_native_track(thermo_risk: ConvectiveRisk) -> SoundingAnalysis:
    """The #568 shape: thermo loaded, and the model has no NWP track at all.

    ``convective_nwp=None`` is what ``assess_convective_nwp`` returns when the
    hour carries no ``nwp_cloud_diagnostics`` — an out-of-range GRIB run, or a
    model with no convective scheme ingested at all.
    """
    thermo = ConvectiveAssessment(
        risk_level=thermo_risk, cape_jkg=1400, base_ft=4000, top_ft=32000,
    )
    # ``analyze_sounding`` stores the thermo assessment in BOTH slots; the
    # resolution step is what (does or does not) swap the NWP one in.
    return SoundingAnalysis(
        convective=thermo, convective_thermo=thermo, convective_nwp=None
    )


def _quiet_native_track(thermo_risk: ConvectiveRisk) -> SoundingAnalysis:
    """§18's original shape: the scheme IS present and reads quiet."""
    thermo = ConvectiveAssessment(
        risk_level=thermo_risk, cape_jkg=1400, base_ft=4000, top_ft=32000,
    )
    return SoundingAnalysis(
        convective=thermo,
        convective_thermo=thermo,
        convective_nwp=ConvectiveAssessment(
            risk_level=ConvectiveRisk.NONE, cape_jkg=None, base_ft=None, top_ft=None,
        ),
        nwp_cloud_diagnostics=NWPCloudDiagnostics(convective_precip_mm_h=0.0),
    )


def _firing_native_track(nwp_risk: ConvectiveRisk) -> SoundingAnalysis:
    """The model's own scheme fires — the RED that must survive all three fixes."""
    thermo = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, cape_jkg=1400, base_ft=4000, top_ft=32000,
    )
    return SoundingAnalysis(
        convective=thermo,
        convective_thermo=thermo,
        convective_nwp=ConvectiveAssessment(
            risk_level=nwp_risk, cape_jkg=1400, base_ft=4000, top_ft=38900,
            convective_precip_mm_h=2.1,
        ),
        nwp_cloud_diagnostics=NWPCloudDiagnostics(convective_precip_mm_h=2.1),
    )


# ---------------------------------------------------------------------------
# The marker itself
# ---------------------------------------------------------------------------


def test_resolution_marks_the_silent_fallback():
    """``_resolve_analyses`` is the only layer that knows the requested method."""
    s = _ctx([_no_native_track(ConvectiveRisk.MODERATE)]).analyses[0].sounding[_MODEL]
    assert s.convective_nwp_fallback is True
    assert s.convective_method_effective == "thermo"


def test_explicit_thermo_request_is_not_a_fallback():
    """The ambiguity ``convective_method_effective`` cannot resolve.

    Both this and the test above badge ``"thermo"``. Only the marker separates
    "we fell back" from "you asked for it".
    """
    ctx = _ctx(
        [_quiet_native_track(ConvectiveRisk.HIGH)], convective_method="thermo"
    )
    s = ctx.analyses[0].sounding[_MODEL]
    assert s.convective_nwp_fallback is False
    assert s.convective_method_effective == "thermo"


def test_present_native_track_is_not_a_fallback():
    s = _ctx([_quiet_native_track(ConvectiveRisk.MODERATE)]).analyses[0].sounding[_MODEL]
    assert s.convective_nwp_fallback is False
    assert s.convective_method_effective == "nwp"


# ---------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------


def test_absent_track_caps_at_amber_and_counts_as_a_dd_trigger():
    """The production bug, in miniature: 6/6 thermo HIGH, no native track.

    Without the fix this is RED (HIGH anywhere → RED) with ``dd_trigger_count``
    0. With it, every point is capped at MODERATE, counted, and therefore
    excluded from the red-coverage test.
    """
    grade = _grade(_ctx([_no_native_track(ConvectiveRisk.HIGH)] * 6))
    assert grade.status == AdvisoryStatus.AMBER
    assert grade.affected == 6
    assert grade.dd_trigger_count == 6
    assert grade.worst_risk == ConvectiveRisk.MODERATE


def test_absent_track_does_not_red_on_coverage_either():
    """The other route to RED: MODERATE+ extent crossing the 50 % threshold.

    All six points are affected (100 % ≥ ``affected_pct_red``), so coverage
    alone would red it. The red-coverage exclusion — ``affected -
    dd_trigger_count`` — is what holds it at AMBER.
    """
    grade = _grade(_ctx([_no_native_track(ConvectiveRisk.MODERATE)] * 6))
    assert grade.status == AdvisoryStatus.AMBER
    assert grade.affected == grade.dd_trigger_count == 6


def test_absent_track_regions_are_reasoned_dd_fallback():
    """"The scheme was quiet" and "there was no scheme" are different facts."""
    grade = _grade(_ctx([_no_native_track(ConvectiveRisk.HIGH)] * 6))
    reasons = {c.reason_code for _, c in grade.region_cells if c is not None}
    assert reasons == {"dd_fallback"}
    assert {c.method_id for _, c in grade.region_cells if c is not None} == {"thermo"}


def test_absent_track_says_so_on_the_card():
    """The card was silent, so the outlier read as meteorological disagreement.

    ``convective_cross_check`` returns None for an absent NWP track — correctly,
    there is nothing to compare — so the note has to come from the grading layer.
    """
    grade = _grade(_ctx([_no_native_track(ConvectiveRisk.HIGH)] * 6))
    assert grade.cross_check is not None
    assert "No NWP Convective forecast" in grade.cross_check


def test_absent_track_below_the_min_risk_floor_stays_green():
    """The cap must not manufacture an amber out of quiet thermodynamics."""
    grade = _grade(_ctx([_no_native_track(ConvectiveRisk.NONE)] * 6))
    assert grade.status == AdvisoryStatus.GREEN
    assert grade.affected == 0
    assert grade.dd_trigger_count == 0
    assert grade.cross_check is None


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------


def test_explicit_thermo_request_stays_uncapped():
    """A user who chose thermo keeps DD grading — the cap must not override it.

    Same soundings as the fallback case, but requested rather than fallen back
    to: the model HAS a native track (quiet), the user simply asked for the
    other one. Capping here would silently overrule the choice.
    """
    grade = _grade(
        _ctx([_quiet_native_track(ConvectiveRisk.HIGH)] * 6, convective_method="thermo")
    )
    assert grade.status == AdvisoryStatus.RED
    assert grade.dd_trigger_count == 0
    assert grade.worst_risk == ConvectiveRisk.HIGH


def test_quiet_but_present_track_keeps_the_442_behaviour():
    """§18 regression guard: the original DD-trigger path is untouched."""
    grade = _grade(_ctx([_quiet_native_track(ConvectiveRisk.HIGH)] * 6))
    assert grade.status == AdvisoryStatus.AMBER
    assert grade.dd_trigger_count == 6
    reasons = {c.reason_code for _, c in grade.region_cells if c is not None}
    assert reasons == {"dd_trigger"}


def test_a_firing_native_scheme_still_grades_red():
    """Fix 1 must never cap a model whose own scheme is firing.

    The refreshed pack's ICON — a real deep cluster over the Bourbonnais, tops
    to FL389, 2.1 mm/h convective rain — is the case the cap must leave alone.
    """
    grade = _grade(_ctx([_firing_native_track(ConvectiveRisk.HIGH)] * 6))
    assert grade.status == AdvisoryStatus.RED
    assert grade.dd_trigger_count == 0
    assert grade.worst_risk == ConvectiveRisk.HIGH
