"""Regression tests: an Ogimet zone at risk NONE is not icing.

The Ogimet methods emit a zone wherever cloud sits in the icing temperature
band and stamp it with the computed index, so ``risk == NONE`` means "assessed
here, index below the LIGHT threshold" — the method saying *no icing*. The
grading paths tested a zone's *existence* instead of its risk, so those
assessed-but-clean points counted as icing hits.

Reported from a real briefing (EDDC→EDDE→EDGS→EDKV, 2026-07-27): ECMWF graded
AMBER icing escape and contributed to a RED IFR feasibility on three points
whose only zones carried ``risk=none`` and an Ogimet index of 0.4–1.9, while the
cross-section — which filters ``none`` out of every icing layer — correctly drew
nothing. SFIP never showed the bug because it only ever emits zones at LIGHT or
worse; the fault surfaced once Ogimet became the graded method.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import hazardous_icing_zones
from weatherbrief.analysis.advisories.fiki_icing import FIKIIcingEvaluator
from weatherbrief.analysis.advisories.icing_escape import IcingEscapeEvaluator
from weatherbrief.analysis.advisories.ifr_feasibility import IFRFeasibilityEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    IcingRisk,
    IcingType,
    IcingZone,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
)


def _defaults(evaluator) -> dict:
    return {p.key: p.default for p in evaluator.catalog_entry().parameters}


def _ctx(soundings: list[SoundingAnalysis]) -> RouteContext:
    """RouteContext over one model, zones already resolved into ``icing_zones``."""
    analyses = [
        RoutePointAnalysis(
            point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
            interpolated_time=datetime(2026, 3, 1, 10, 0),
            forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
            sounding={"gfs": s},
        )
        for i, s in enumerate(soundings)
    ]
    return RouteContext(
        analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
        cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=200,
    )


def _zone(risk: IcingRisk, *, sld: bool = False, index: float = 0.8) -> IcingZone:
    """A zone straddling cruise+buffer, so only its risk decides the grade.

    Geometry mirrors the reported briefing: a single 700 hPa level padded to
    1000 ft, sitting just above an 8,000 ft cruise and inside the 2,000 ft
    relevance buffer.
    """
    return IcingZone(
        base_ft=9623, top_ft=10623, base_pressure_hpa=700, top_pressure_hpa=700,
        risk=risk, icing_type=IcingType.CLEAR, sld_risk=sld,
        mean_temperature_c=-0.8, mean_icing_index=index, mean_rh_pct=77.0,
    )


def _sounding(zones: list[IcingZone]) -> SoundingAnalysis:
    return SoundingAnalysis(
        indices=ThermodynamicIndices(freezing_level_ft=9000),
        icing_zones=zones,
    )


# ── the predicate itself ─────────────────────────────────────────────


def test_hazardous_filter_drops_none_risk():
    assert hazardous_icing_zones([_zone(IcingRisk.NONE)]) == []


def test_hazardous_filter_keeps_real_icing():
    for risk in (IcingRisk.LIGHT, IcingRisk.MODERATE, IcingRisk.SEVERE):
        assert len(hazardous_icing_zones([_zone(risk)])) == 1


def test_hazardous_filter_keeps_sld_despite_none_risk():
    """SLD is a hazard regardless of the index — the same carve-out the escape
    cost model has always made (``risk == NONE and not sld_risk`` → skip)."""
    assert len(hazardous_icing_zones([_zone(IcingRisk.NONE, sld=True)])) == 1


# ── the grading paths ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "evaluator", [IcingEscapeEvaluator, FIKIIcingEvaluator, IFRFeasibilityEvaluator]
)
def test_none_risk_zones_do_not_flag_icing(evaluator):
    """A route where every point carries only risk=NONE zones grades GREEN."""
    ctx = _ctx([_sounding([_zone(IcingRisk.NONE)]) for _ in range(10)])
    result = evaluator.evaluate(ctx, _defaults(evaluator))
    assert result.aggregate_status == AdvisoryStatus.GREEN
    assert result.per_model[0].affected_points == 0


@pytest.mark.parametrize(
    "evaluator", [IcingEscapeEvaluator, FIKIIcingEvaluator, IFRFeasibilityEvaluator]
)
def test_real_icing_still_flags(evaluator):
    """No over-correction: MODERATE zones on the same geometry still flag."""
    ctx = _ctx([_sounding([_zone(IcingRisk.MODERATE)]) for _ in range(10)])
    result = evaluator.evaluate(ctx, _defaults(evaluator))
    assert result.aggregate_status != AdvisoryStatus.GREEN
    assert result.per_model[0].affected_points > 0


def test_none_risk_points_do_not_inflate_the_affected_extent():
    """The reported symptom: extent counted assessed-but-clean points.

    Half the route carries real LIGHT icing, half carries risk=NONE zones. Only
    the former may reach the extent — before the fix both did, and the headline
    read double the true coverage.
    """
    soundings = [_sounding([_zone(IcingRisk.LIGHT)]) for _ in range(5)]
    soundings += [_sounding([_zone(IcingRisk.NONE)]) for _ in range(5)]
    result = IcingEscapeEvaluator.evaluate(_ctx(soundings), _defaults(IcingEscapeEvaluator))
    assert result.per_model[0].affected_points == 5


def test_sld_at_none_risk_still_flags():
    """A NONE-risk zone carrying SLD is a hazard and must survive the filter."""
    ctx = _ctx([_sounding([_zone(IcingRisk.NONE, sld=True)]) for _ in range(10)])
    result = IcingEscapeEvaluator.evaluate(ctx, _defaults(IcingEscapeEvaluator))
    assert result.aggregate_status != AdvisoryStatus.GREEN
