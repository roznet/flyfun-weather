"""Synthetic-grid tests for the shared vertical-profile solver (issue #335).

Hand-built cost fields (no fixtures) exercise the solver's contract directly:
continuous-band feasibility, multi-deck gaps, terrain blockage, above-cruise-only
bands, no-path blockage, the conservative transition-column convention, and the
finite-hazard objective tier. See designs/future/vertical-profile-solver.md.
"""

from __future__ import annotations

from weatherbrief.analysis.advisories.vertical_profile import (
    INF,
    Blockage,
    CostModel,
    Profile,
    floor_reachable_bins,
    solve,
)

# Altitude bins every 1000 ft from 1000..10000 (index b -> (b+1)*1000 ft).
BINS = [(b + 1) * 1000 for b in range(10)]  # 1000..10000


def _uniform(n_points: int, cost_by_bin: list[float]) -> list[list[float]]:
    """Cost field where every route point has the same per-bin costs."""
    return [list(cost_by_bin) for _ in range(n_points)]


def _model(cost_field, *, start=None, end=None, dist_step=20.0) -> CostModel:
    n = len(cost_field)
    return CostModel(
        cost_field=cost_field,
        distances_nm=[i * dist_step for i in range(n)],
        bin_altitudes_ft=BINS,
        allowed_start_bins=start,
        allowed_end_bins=end,
    )


def _floor_band(cost_field, point) -> set[int]:
    return floor_reachable_bins(cost_field[point])


# ---------------------------------------------------------------------------
# floor_reachable_bins — the anchoring primitive
# ---------------------------------------------------------------------------

def test_floor_band_stops_at_hard_wall():
    """Contiguous finite run from the floor stops at the first inf (VFR deck)."""
    # Deck (inf) at bins 5,6; clear above at 7+. Floor band is bins 0..4 only.
    col = [0, 0, 0, 0, 0, INF, INF, 0, 0, 0]
    assert floor_reachable_bins(col) == {0, 1, 2, 3, 4}


def test_floor_band_passes_through_soft_wall():
    """A finite (soft-wall) layer does NOT stop the run — icing can be climbed through."""
    col = [0, 0, 0, 0, 0, 5.0, 5.0, 0, 0, 0]  # finite, not inf
    assert floor_reachable_bins(col) == set(range(10))


def test_floor_band_from_explicit_floor_bin():
    """The floor bin is passed explicitly; the scan starts there and stops at the deck."""
    col = [INF, INF, 0, 0, 0, INF, 0, 0, 0, 0]  # terrain floor at bin 2, deck at 5
    assert floor_reachable_bins(col, floor_bin=2) == {2, 3, 4}


def test_floor_band_empty_when_deck_at_floor():
    """A deck sitting AT the floor bin → empty band (can't get airborne into clear air)."""
    col = [0, 0, INF, INF, 0, 0, 0, 0, 0, 0]  # deck at bins 2,3
    assert floor_reachable_bins(col, floor_bin=2) == set()


# ---------------------------------------------------------------------------
# Feasible profiles
# ---------------------------------------------------------------------------

def test_clear_route_holds_preferred():
    """All-clear grid → single segment at the preferred altitude, no transitions."""
    cf = _uniform(5, [0.0] * 10)
    prof = solve(_model(cf), preferred_alt_ft=8000)
    assert isinstance(prof, Profile)
    assert len(prof.segments) == 1
    assert prof.segments[0].alt_ft == 8000
    assert prof.transitions == []
    assert prof.total_cost == 0.0


def test_single_deck_route_stays_under():
    """A deck spanning the preferred altitude everywhere → fly under it the whole way.

    Mirrors the EDDN→EGSG worked example: the connected clear band never reaches
    cruise, so the profile is a single low segment with NO climb-to-cruise claim.
    """
    # Deck inf at bins 6,7,8 (7k,8k,9k). Clear band below is bins 0..5 (<=6000).
    col = [0, 0, 0, 0, 0, 0, INF, INF, INF, 0]
    cf = _uniform(6, col)
    start = _floor_band(cf, 0)
    end = _floor_band(cf, 5)
    prof = solve(_model(cf, start=start, end=end), preferred_alt_ft=8000)
    assert isinstance(prof, Profile)
    # Best feasible band closest to preferred 8000 under the deck = 6000 (bin 5).
    assert len(prof.segments) == 1
    assert prof.segments[0].alt_ft == 6000
    assert prof.transitions == []


def test_multi_deck_with_gap_threads_the_gap():
    """A greedy 'single max clear altitude' misjudges this; the DP threads the gap.

    Point A blocks high band, point C blocks low band, but a middle band is clear at
    both → the path must sit in the shared clear band, not oscillate.
    """
    # bins clear everywhere at index 4 (5000). Point 1 walls low (0..2), point 3 walls
    # high (6..9); bin 4 is clear at all points → continuous band at 5000.
    p0 = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    p1 = [INF, INF, INF, 0, 0, 0, 0, 0, 0, 0]
    p2 = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    p3 = [0, 0, 0, 0, 0, 0, INF, INF, INF, INF]
    p4 = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    cf = [p0, p1, p2, p3, p4]
    prof = solve(_model(cf), preferred_alt_ft=5000)
    assert isinstance(prof, Profile)
    # 5000 (bin 4) is clear at every point → a single flat segment, zero transitions.
    assert prof.transitions == []
    assert all(s.alt_ft == 5000 for s in prof.segments)


def test_above_cruise_only_band():
    """The only continuous clear band sits ABOVE preferred cruise → report it (decision 2)."""
    # Deck inf at bins 4,5,6 (5k,6k,7k) everywhere; preferred 6000 is walled.
    # Below-deck band 0..3 and above-deck band 7..9 both continuous. With start/end
    # anchored above the deck, the solver must use the above-cruise band.
    col = [INF, INF, INF, INF, INF, INF, INF, 0, 0, 0]  # only 8k,9k,10k clear
    cf = _uniform(5, col)
    prof = solve(_model(cf, start={7, 8, 9}, end={7, 8, 9}), preferred_alt_ft=6000)
    assert isinstance(prof, Profile)
    assert prof.segments[0].alt_ft == 8000  # closest clear bin to preferred, above it
    assert all(s.alt_ft >= 8000 for s in prof.segments)


# ---------------------------------------------------------------------------
# Blockage
# ---------------------------------------------------------------------------

def test_no_path_deck_to_terrain_blockage():
    """A full-column wall at one point → Blockage naming that distance band."""
    # Point 2 is entirely walled (deck to terrain) → no continuous path.
    cf = _uniform(5, [0.0] * 10)
    cf[2] = [INF] * 10
    blk = solve(_model(cf), preferred_alt_ft=8000)
    assert isinstance(blk, Blockage)
    assert blk.from_nm <= 40.0 <= blk.to_nm  # point 2 is at 40 nm
    assert "40" in blk.reason


def test_anchoring_forces_blockage_vs_cruise_cheat():
    """Anchoring to the floor band is what turns a 'start at cruise' cheat into a
    blockage (decision 9).

    A deck (bins 2..4) persists the whole route; the clear cruise band (bin 8) sits
    above it. Unanchored, the solver 'starts at cruise' and flies happily — a physical
    impossibility (you'd have to climb through the deck from the field). Anchored to the
    floor band (below the deck), cruise is unreachable → the honest blockage.
    """
    col = [0, 0, INF, INF, INF, 0, 0, 0, 0, 0]  # deck at bins 2..4 at every point
    cf = _uniform(5, col)

    # Unanchored (start=None) — the cheat: it just sits at cruise.
    cheat = solve(_model(cf, start=None, end={8}), preferred_alt_ft=9000)
    assert isinstance(cheat, Profile)
    assert cheat.segments[0].alt_ft == 9000

    # Anchored to the floor band {0,1}: no path from below the deck up to cruise.
    start = _floor_band(cf, 0)  # {0, 1}
    blk = solve(_model(cf, start=start, end={8}), preferred_alt_ft=9000)
    assert isinstance(blk, Blockage)


# ---------------------------------------------------------------------------
# Transition-column convention (decision 6)
# ---------------------------------------------------------------------------

def test_deck_ends_between_points_blocks_climb():
    """Deck present at point i, gone at i+1 → the i→i+1 climb is still blocked.

    Conservative convention: the crossing is walled if EITHER endpoint column walls the
    interval, so the path cannot sneak up through the point where the deck still exists.
    """
    # Two points. Start low (bin 0) at point 0, want bin 9 at point 1. Point 0 walls the
    # in-between bins 1..8; point 1 is clear. Climbing 0->9 crosses point-0 walls → inf.
    p0 = [0, INF, INF, INF, INF, INF, INF, INF, INF, 0]
    p1 = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    cf = [p0, p1]
    blk = solve(_model(cf, start={0}, end={9}), preferred_alt_ft=10000)
    # No way to reach bin 9 at point 1 from bin 0 at point 0 (must cross point-0 walls),
    # and bin 9 is the only allowed end → blockage.
    assert isinstance(blk, Blockage)


def test_transition_allowed_when_both_columns_clear():
    """Symmetric control: when neither column walls the interval, the climb is allowed."""
    p0 = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    p1 = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    cf = [p0, p1]
    prof = solve(_model(cf, start={0}, end={9}), preferred_alt_ft=10000)
    assert isinstance(prof, Profile)
    assert len(prof.transitions) == 1
    assert prof.transitions[0].from_alt_ft == 1000
    assert prof.transitions[0].to_alt_ft == 10000


# ---------------------------------------------------------------------------
# Objective tiers (decision 5)
# ---------------------------------------------------------------------------

def test_soft_wall_prefers_low_hazard_over_staying_at_cruise():
    """Finite-hazard tier: a two-transition escape must beat staying in light icing.

    Every path is feasible (nothing inf), so without the hazard tier the solver would
    stay flat at cruise. The hazard tier forces it to route around the finite-cost band.
    """
    # Preferred 8000 (bin 7) carries finite icing cost 5 at the middle points; a clear
    # band at 3000 (bin 2) costs 0. Staying at cruise costs 5*k; detouring low costs
    # only the two transitions' crossing (all finite/0) → lower hazard.
    n = 5
    cf = []
    for i in range(n):
        col = [0.0] * 10
        if 1 <= i <= 3:
            col[7] = 5.0  # icing at cruise band on the middle points
        cf.append(col)
    prof = solve(_model(cf), preferred_alt_ft=8000)
    assert isinstance(prof, Profile)
    # It must NOT stay flat at 8000 through the icing — some segment leaves cruise.
    assert any(s.alt_ft != 8000 for s in prof.segments)
    assert prof.total_cost < 15.0  # strictly cheaper than 3×5 spent sitting in icing


def test_fewest_transitions_tiebreak():
    """Equal hazard → fewer transitions wins (tier 3)."""
    # All-clear grid: staying flat (0 transitions) must beat any wandering path.
    cf = _uniform(6, [0.0] * 10)
    prof = solve(_model(cf), preferred_alt_ft=5000)
    assert isinstance(prof, Profile)
    assert prof.transitions == []
