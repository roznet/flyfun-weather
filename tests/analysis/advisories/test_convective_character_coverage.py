"""A model with no soundings is UNAVAILABLE, never "no convective character".

This is the #391 false-GREEN class the PR exists to eliminate, reintroduced by
one of the PR's own fixes. Round 3 stopped `build_character_points` dropping a
route point the model has no sounding for — the drop left `cell_edges` tiling
the whole route over only the covered points, so one realized cell at 40 nm of a
200 nm route measured 165 nm. Keeping the point as an unassessed placeholder
fixed the geometry and broke the emptiness test: `points` is now non-empty
whenever the *route* has points, so `if not points` stopped meaning "no data".

Execution then fell through to `classify_convective_character`, whose
`total = sum(1 for p in points if p.assessed)` is 0, returning
`ConvectiveCharacter.NONE` → GREEN, "No significant convective character". The
same `None`-vs-band confusion silently dropped `ifr_feasibility`'s EMBEDDED
escalation (§22) for that model (#571 review round 9).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.convective_character import (
    ConvectiveCharacterEvaluator,
    build_character_points,
    classify_route_character,
    resolve_character_params,
)
from weatherbrief.models import AdvisoryStatus, RoutePointAnalysis, SoundingAnalysis

_DEFAULTS = {
    p.key: p.default for p in ConvectiveCharacterEvaluator.catalog_entry().parameters
}


def _ctx(with_sounding: bool, n: int = 10) -> RouteContext:
    analyses = [
        RoutePointAnalysis(
            point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
            interpolated_time=datetime(2026, 3, 1, 10, 0),
            forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=90.0,
            sounding={"gfs": SoundingAnalysis()} if with_sounding else {},
        )
        for i in range(n)
    ]
    return RouteContext(
        analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
        cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        total_distance_nm=(n - 1) * 20.0,
    )


class TestNoSoundingsAnywhere:
    @pytest.fixture
    def blank(self):
        return _ctx(with_sounding=False)

    def test_the_placeholder_points_are_still_built(self, blank):
        """Guards the guard. If a later change reverts to dropping unsounded
        points, `points` goes empty, the old truthiness test starts working
        again by accident, and the assertions below stop proving anything."""
        inputs = build_character_points(blank, "gfs", _DEFAULTS)
        assert len(inputs.points) == 10
        assert not inputs.any_assessed

    def test_the_evaluator_is_unavailable_not_green(self, blank):
        m = ConvectiveCharacterEvaluator.evaluate(blank, _DEFAULTS).per_model[0]
        assert m.status == AdvisoryStatus.UNAVAILABLE
        assert m.status != AdvisoryStatus.GREEN

    def test_the_composite_entry_point_returns_no_band(self, blank):
        """`ifr_feasibility` escalates on EMBEDDED; a band of NONE from a model
        with no data would silently suppress that escalation (§22)."""
        assert classify_route_character(
            blank, "gfs", resolve_character_params(blank),
        ) is None


class TestSoundingsPresent:
    def test_a_real_model_still_grades(self):
        """The fix must not turn a genuinely clear model into UNAVAILABLE."""
        ctx = _ctx(with_sounding=True)
        inputs = build_character_points(ctx, "gfs", _DEFAULTS)
        assert inputs.any_assessed
        m = ConvectiveCharacterEvaluator.evaluate(ctx, _DEFAULTS).per_model[0]
        assert m.status == AdvisoryStatus.GREEN

    def test_partial_coverage_still_grades(self):
        """One assessed point among nine blanks is thin, not absent — the
        coverage tolerance owns that call, not the emptiness guard."""
        ctx = _ctx(with_sounding=False)
        ctx.analyses[3].sounding["gfs"] = SoundingAnalysis()
        inputs = build_character_points(ctx, "gfs", _DEFAULTS)
        assert inputs.any_assessed
        assert classify_route_character(
            ctx, "gfs", resolve_character_params(ctx),
        ) is not None
