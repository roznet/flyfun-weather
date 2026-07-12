"""Regression tests for the Ogimet-NWP "method unavailable" icing path (#391).

When a user selects the Ogimet-NWP icing method but a model has no native cloud
envelope, ``assess_icing_zones_ogimet_nwp`` returns ``[]`` — "method could not
run", not "ran, found no icing". _resolve_analyses flags this via
``active_icing_available=False`` so the icing evaluators grade UNAVAILABLE
instead of clear-by-absence.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.fiki_icing import FIKIIcingEvaluator
from weatherbrief.analysis.advisories.icing_escape import IcingEscapeEvaluator
from weatherbrief.analysis.advisories.ifr_feasibility import IFRFeasibilityEvaluator
from weatherbrief.tasks.advise import _resolve_analyses
from weatherbrief.models import (
    AdvisoryStatus,
    ConvectiveAssessment,
    ConvectiveRisk,
    EnhancedCloudLayer,
    CloudCoverage,
    IcingRisk,
    IcingType,
    IcingZone,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
)


def _defaults(evaluator) -> dict:
    return {p.key: p.default for p in evaluator.catalog_entry().parameters}


def _ctx(soundings: list[SoundingAnalysis], *, icing_method: str) -> RouteContext:
    analyses = [
        RoutePointAnalysis(
            point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
            interpolated_time=datetime(2026, 3, 1, 10, 0),
            forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
            sounding={"gfs": s},
        )
        for i, s in enumerate(soundings)
    ]
    resolved = _resolve_analyses(analyses, icing_method=icing_method, cloud_method=None)
    return RouteContext(
        analyses=resolved, cross_sections=[], elevation=None, models=["gfs"],
        cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
    )


def _no_envelope_sounding() -> SoundingAnalysis:
    """A model with no native cloud envelope: Ogimet-NWP returns no zones."""
    return SoundingAnalysis(
        indices=ThermodynamicIndices(freezing_level_ft=5000),
        nwp_cloud_layers=None,
        icing_ogimet_nwp_zones=[],
    )


@pytest.mark.parametrize("evaluator", [IcingEscapeEvaluator, FIKIIcingEvaluator])
def test_ogimet_nwp_no_envelope_is_unavailable_not_green(evaluator):
    """Ogimet-NWP on a model with no cloud envelope → UNAVAILABLE, not clear."""
    ctx = _ctx([_no_envelope_sounding() for _ in range(10)], icing_method="ogimet_nwp")
    result = evaluator.evaluate(ctx, _defaults(evaluator))
    assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE


def test_resolve_analyses_flags_unavailable_active_icing():
    """_resolve_analyses marks active_icing_available=False when Ogimet-NWP can't run."""
    ctx = _ctx([_no_envelope_sounding()], icing_method="ogimet_nwp")
    assert ctx.analyses[0].sounding["gfs"].active_icing_available is False


def test_ogimet_nwp_with_envelope_still_grades():
    """A model WITH a native cloud envelope grades normally (no over-correction).

    Genuinely-clear icing on an assessable model stays GREEN, and real icing
    zones still flag — the flag only fires when the method could not run.
    """
    envelope = [EnhancedCloudLayer(base_ft=4000, top_ft=8000, coverage=CloudCoverage.OVC)]
    clear = SoundingAnalysis(
        indices=ThermodynamicIndices(freezing_level_ft=5000),
        nwp_cloud_layers=envelope,
        icing_ogimet_nwp_zones=[],  # ran, found no icing
    )
    ctx = _ctx([clear for _ in range(10)], icing_method="ogimet_nwp")
    assert ctx.analyses[0].sounding["gfs"].active_icing_available is True
    result = IcingEscapeEvaluator.evaluate(ctx, _defaults(IcingEscapeEvaluator))
    assert result.aggregate_status == AdvisoryStatus.GREEN


def test_default_ogimet_dd_is_always_available():
    """The default DD method never sets the unavailable flag."""
    dd = SoundingAnalysis(
        indices=ThermodynamicIndices(freezing_level_ft=5000),
        icing_zones=[],
    )
    ctx = _ctx([dd for _ in range(10)], icing_method="ogimet_dd")
    assert ctx.analyses[0].sounding["gfs"].active_icing_available is True
    result = IcingEscapeEvaluator.evaluate(ctx, _defaults(IcingEscapeEvaluator))
    assert result.aggregate_status == AdvisoryStatus.GREEN


def test_ifr_icing_axis_unavailable_but_convective_still_drives_grade():
    """IFR: icing axis UNAVAILABLE must not blank a real convective verdict.

    A model on Ogimet-NWP with no cloud envelope but a HIGH convective risk must
    still grade RED — the icing axis is unassessable (not clear), and the
    convective axis drives the composite.
    """
    conv = SoundingAnalysis(
        indices=ThermodynamicIndices(freezing_level_ft=5000),
        nwp_cloud_layers=None,
        icing_ogimet_nwp_zones=[],
        convective=ConvectiveAssessment(risk_level=ConvectiveRisk.HIGH, cape_jkg=2500),
    )
    ctx = _ctx([conv for _ in range(10)], icing_method="ogimet_nwp")
    result = IFRFeasibilityEvaluator.evaluate(ctx, _defaults(IFRFeasibilityEvaluator))
    assert result.aggregate_status == AdvisoryStatus.RED


def test_mitigation_solver_does_not_price_unavailable_icing_as_clear():
    """The escape cost model treats an Ogimet-NWP-unavailable cell as impassable.

    Regression for #391 review: `_icing_cell_cost` used to return 0 (clear) for a
    point whose icing method could not run (icing_zones=[]), so the mitigation
    solver could route an altitude "escape" through an unassessed segment as if
    confirmed ice-free. Above warm air, an unassessable cell must cost INF.
    """
    from weatherbrief.analysis.advisories.icing_escape import _icing_cell_cost
    from weatherbrief.analysis.advisories.vertical_profile import INF

    # Unassessable icing, cell above the freezing level (not guaranteed warm) →
    # must be impassable, not free.
    s = SoundingAnalysis(
        indices=ThermodynamicIndices(freezing_level_ft=3000),
        active_icing_available=False,
        icing_zones=[],
    )
    assert _icing_cell_cost(s, 8000) == INF
    # Below the freezing level is genuinely warm → still free regardless.
    assert _icing_cell_cost(s, 1000) == 0.0
    # An assessable, ice-free cell above warm air is free (no over-correction).
    s_ok = SoundingAnalysis(
        indices=ThermodynamicIndices(freezing_level_ft=3000),
        active_icing_available=True,
        icing_zones=[],
    )
    assert _icing_cell_cost(s_ok, 8000) == 0.0


def test_ifr_real_icing_zone_on_ogimet_nwp_still_flags():
    """Real Ogimet-NWP icing zones (envelope present) still flag icing (no over-correction)."""
    envelope = [EnhancedCloudLayer(base_ft=4000, top_ft=10000, coverage=CloudCoverage.OVC)]
    zone = IcingZone(base_ft=4000, top_ft=10000, risk=IcingRisk.MODERATE, icing_type=IcingType.MIXED)
    iced = SoundingAnalysis(
        indices=ThermodynamicIndices(freezing_level_ft=5000),
        nwp_cloud_layers=envelope,
        icing_ogimet_nwp_zones=[zone],
    )
    ctx = _ctx([iced for _ in range(10)], icing_method="ogimet_nwp")
    result = IFRFeasibilityEvaluator.evaluate(ctx, _defaults(IFRFeasibilityEvaluator))
    assert result.aggregate_status in (AdvisoryStatus.AMBER, AdvisoryStatus.RED)
