"""The terminal-deck corridor gate measures a contiguous run, not a raw span.

``_terminal_deck_span`` used ``max(d) - min(d)`` over the qualifying points —
the fourth of the four extent conventions #571 removes, and the only one that
counts the *gaps* as part of the extent. Two field clouds 20 nm apart with clear
air between them scored a 20 nm "run" and earned a "climb to cruise after X nm"
tip for a deck that is not there.
"""

from __future__ import annotations

from datetime import datetime

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.vfr_feasibility import (
    _is_real_terminal_deck,
    _terminal_deck_span,
)
from weatherbrief.models import (
    CloudCoverage,
    EnhancedCloudLayer,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
)

_TOTAL_NM = 100.0
_SPACING = 10.0


def _ctx(deck_idx: set[int]) -> RouteContext:
    """11 points at 10 nm spacing; a sub-cruise BKN deck at the given indices."""
    deck = [EnhancedCloudLayer(base_ft=2000, top_ft=5000, coverage=CloudCoverage.BKN)]
    analyses = [
        RoutePointAnalysis(
            point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * _SPACING,
            interpolated_time=datetime(2026, 3, 1, 10, 0),
            forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
            sounding={"gfs": SoundingAnalysis(
                indices=ThermodynamicIndices(freezing_level_ft=9000),
                cloud_layers=deck if i in deck_idx else [],
            )},
        )
        for i in range(int(_TOTAL_NM / _SPACING) + 1)
    ]
    return RouteContext(
        analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
        cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        total_distance_nm=_TOTAL_NM,
    )


class TestTerminalDeckRun:
    def test_two_isolated_clouds_are_not_a_run(self):
        # Field cloud at the origin and another 30 nm out, clear between.
        # max(d) - min(d) called that a 30 nm deck; the contiguous run is 10 nm
        # (each point's own cell).
        _, run_nm = _terminal_deck_span(_ctx({0, 3}), "gfs", 8000.0, 0.0, 40.0)
        assert run_nm < 15.0

    def test_a_real_contiguous_deck_measures_its_run(self):
        _, run_nm = _terminal_deck_span(_ctx({0, 1, 2}), "gfs", 8000.0, 0.0, 40.0)
        assert run_nm >= 20.0

    def test_a_lone_field_cloud_still_fails_the_gate(self):
        count, run_nm = _terminal_deck_span(_ctx({0}), "gfs", 8000.0, 0.0, 40.0)
        assert count == 1
        assert not _is_real_terminal_deck(count, run_nm)

    def test_the_point_count_arm_of_the_gate_is_unchanged(self):
        # Two adjacent points still qualify on count alone (#342 Bug A's rule).
        count, run_nm = _terminal_deck_span(_ctx({0, 1}), "gfs", 8000.0, 0.0, 40.0)
        assert count == 2
        assert _is_real_terminal_deck(count, run_nm)
