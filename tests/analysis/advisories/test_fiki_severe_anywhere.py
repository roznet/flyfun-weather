"""FIKI: SEVERE icing / SLD at cruise is RED anywhere on the route (#807).

Before #807 the SLD / SEVERE / thickness RED rules aggregated only over the
50 nm terminal corridors. Mid-route icing reached the grade solely through the
clear-cruise extent, so a short stretch of SEVERE icing *at cruise* — under the
20 % amber extent — graded GREEN. Severe icing exceeds FIKI certification
anywhere, so it now grades RED wherever it sits within the cruise buffer.

Also pinned: ``severe_is_red`` is no longer a tunable — a stored ``0`` does not
turn severe icing green.
"""

from __future__ import annotations

from datetime import datetime

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.fiki_icing import FIKIIcingEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    HighlightSeverity,
    IcingRisk,
    IcingType,
    IcingZone,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
)

_TOTAL_NM = 600.0
_STEP_NM = 30.0  # 21 points
_CRUISE_FT = 8000


def _defaults() -> dict:
    return {p.key: p.default for p in FIKIIcingEvaluator.catalog_entry().parameters}


def _zone(base: int, top: int, risk: IcingRisk, *, sld: bool = False) -> IcingZone:
    return IcingZone(
        base_ft=base, top_ft=top, risk=risk, icing_type=IcingType.MIXED,
        sld_risk=sld,
    )


def _ctx(zones_at: dict[float, list[IcingZone]]) -> RouteContext:
    """600 nm route; ``zones_at`` maps distance → zones at that point."""
    n = int(_TOTAL_NM / _STEP_NM) + 1
    analyses = []
    for i in range(n):
        dist = i * _STEP_NM
        sounding = SoundingAnalysis(
            indices=ThermodynamicIndices(freezing_level_ft=4000),
            icing_zones=zones_at.get(dist, []),
        )
        analyses.append(RoutePointAnalysis(
            point_index=i, lat=48.0, lon=2.0 + i * 0.1,
            distance_from_origin_nm=dist,
            interpolated_time=datetime(2026, 3, 1, 10, 0),
            forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=90.0,
            sounding={"gfs": sounding},
        ))
    return RouteContext(
        analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
        cruise_altitude_ft=_CRUISE_FT, flight_ceiling_ft=18000,
        total_distance_nm=_TOTAL_NM,
    )


def _midroute(zone: IcingZone) -> dict[float, list[IcingZone]]:
    # Two adjacent points at 270/300 nm — far outside both 50 nm corridors and
    # well under the 20 % amber extent of a 600 nm leg.
    return {270.0: [zone], 300.0: [zone]}


def _red_reasons(result) -> set[str]:
    out: set[str] = set()
    for pm in result.per_model:
        if pm.highlights:
            for r in pm.highlights.regions:
                if r.severity == HighlightSeverity.RED and r.reason_code:
                    out.add(r.reason_code)
    return out


class TestSevereAtCruiseAnywhere:
    def test_midroute_severe_at_cruise_is_red(self):
        ctx = _ctx(_midroute(_zone(7000, 9000, IcingRisk.SEVERE)))
        result = FIKIIcingEvaluator.evaluate(ctx, _defaults())
        assert result.aggregate_status == AdvisoryStatus.RED
        (pm,) = result.per_model
        assert "severe icing (cruise)" in pm.detail
        assert _red_reasons(result) == {"severe_icing"}

    def test_midroute_moderate_at_cruise_short_stretch_unchanged(self):
        """The fix is about severity, not extent: MODERATE over the same short
        stretch still grades on the clear-cruise extent alone (not RED)."""
        ctx = _ctx(_midroute(_zone(7000, 9000, IcingRisk.MODERATE)))
        result = FIKIIcingEvaluator.evaluate(ctx, _defaults())
        assert result.aggregate_status != AdvisoryStatus.RED

    def test_midroute_severe_within_buffer_above_cruise_is_red(self):
        """'At cruise' is the clear-air predicate: within the cruise buffer."""
        ctx = _ctx(_midroute(_zone(9000, 11000, IcingRisk.SEVERE)))  # 1000 ft above
        result = FIKIIcingEvaluator.evaluate(ctx, _defaults())
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_midroute_severe_far_below_cruise_not_red(self):
        """Mid-route, the column below cruise is not transited (documented
        choice): SEVERE 2000–4000 ft under an 8000 ft cruise is not RED."""
        ctx = _ctx(_midroute(_zone(2000, 4000, IcingRisk.SEVERE)))
        result = FIKIIcingEvaluator.evaluate(ctx, _defaults())
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_midroute_sld_at_cruise_is_red(self):
        ctx = _ctx(_midroute(_zone(7000, 9000, IcingRisk.LIGHT, sld=True)))
        result = FIKIIcingEvaluator.evaluate(ctx, _defaults())
        assert result.aggregate_status == AdvisoryStatus.RED
        (pm,) = result.per_model
        assert "SLD risk (cruise)" in pm.detail
        assert _red_reasons(result) == {"sld"}

    def test_midroute_thickness_stays_corridor_scoped(self):
        """A thick MODERATE column mid-route, clear of cruise, is not a
        transit-thickness RED — thickness measures the climb/descent column."""
        ctx = _ctx(_midroute(_zone(0, 5500, IcingRisk.MODERATE)))
        result = FIKIIcingEvaluator.evaluate(ctx, _defaults())
        assert result.aggregate_status == AdvisoryStatus.GREEN


class TestSevereIsRedRetired:
    def test_not_in_catalog(self):
        keys = {p.key for p in FIKIIcingEvaluator.catalog_entry().parameters}
        assert "severe_is_red" not in keys

    def test_stored_false_still_red_midroute(self):
        ctx = _ctx(_midroute(_zone(7000, 9000, IcingRisk.SEVERE)))
        params = {**_defaults(), "severe_is_red": 0}
        result = FIKIIcingEvaluator.evaluate(ctx, params)
        assert result.aggregate_status == AdvisoryStatus.RED
        assert "severe_is_red" not in result.parameters_used

    def test_stored_false_still_red_in_corridor(self):
        """Departure-corridor SEVERE below cruise: RED regardless of the key."""
        zone = _zone(2000, 4000, IcingRisk.SEVERE)
        ctx = _ctx({0.0: [zone], 30.0: [zone]})
        params = {**_defaults(), "severe_is_red": 0}
        result = FIKIIcingEvaluator.evaluate(ctx, params)
        assert result.aggregate_status == AdvisoryStatus.RED
        (pm,) = result.per_model
        assert "severe icing (dep)" in pm.detail
