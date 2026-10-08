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


# ---------------------------------------------------------------------------
# Vectorised solver ≡ scalar DP (issue #704)
# ---------------------------------------------------------------------------
#
# `solve` vectorises the per-step (from-bin × to-bin) transition. The scalar
# triple-loop it replaced is kept below, verbatim in logic, as the reference: the
# two must agree exactly — same bin sequence, same total cost, same blockage —
# including exact-tie decisions, which is why random fields below draw from a
# small set of repeated costs (ties are common) as well as arbitrary floats.

import random  # noqa: E402

import numpy as np  # noqa: E402

from weatherbrief.analysis.advisories import vertical_profile as _vp  # noqa: E402


def _solve_reference(model: CostModel, preferred_alt_ft: int):
    """The pre-#704 scalar DP, used only as an equivalence oracle."""
    cf = model.cost_field
    n = len(cf)
    if n == 0:
        return Blockage(0.0, 0.0, "no route points")
    nbins = len(model.bin_altitudes_ft)
    alts = model.bin_altitudes_ft
    start_bins = model.allowed_start_bins if model.allowed_start_bins is not None else set(range(nbins))
    end_bins = model.allowed_end_bins if model.allowed_end_bins is not None else set(range(nbins))

    def dev(b):
        return abs(alts[b] - preferred_alt_ft)

    dp = [_vp._INF_COST] * nbins
    parent = [[-1] * nbins for _ in range(n)]
    for b in range(nbins):
        if b in start_bins and cf[0][b] != INF:
            dp[b] = _vp._Cost(cf[0][b], 0, dev(b))
    reachable_upto = 0 if any(c.hazard != INF for c in dp) else -1
    for i in range(1, n):
        ndp = [_vp._INF_COST] * nbins
        col_prev, col_cur = cf[i - 1], cf[i]
        for b in range(nbins):
            if col_cur[b] == INF:
                continue
            best = _vp._INF_COST
            best_a = -1
            node = _vp._Cost(col_cur[b], 0, dev(b))
            for a in range(nbins):
                if dp[a].hazard == INF:
                    continue
                if a == b:
                    edge = _vp._Cost(0.0, 0, 0.0)
                else:
                    cc = _vp._crossing_cost(col_prev, col_cur, a, b)
                    if cc == INF:
                        continue
                    edge = _vp._Cost(cc, 1, 0.0)
                cand = dp[a] + edge + node
                if cand < best:
                    best = cand
                    best_a = a
            ndp[b] = best
            parent[i][b] = best_a
        dp = ndp
        if any(c.hazard != INF for c in dp):
            reachable_upto = i
    best_end = -1
    best_cost = _vp._INF_COST
    for b in end_bins:
        if dp[b].hazard != INF and dp[b] < best_cost:
            best_cost = dp[b]
            best_end = b
    if best_end < 0:
        return _vp._blockage(model, reachable_upto, end_bins, dp)
    seq = [0] * n
    seq[n - 1] = best_end
    for i in range(n - 1, 0, -1):
        seq[i - 1] = parent[i][seq[i]]
    return _vp._to_profile(model, seq, best_cost.hazard)


def _random_field(rng: random.Random, n: int, nbins: int, style: str) -> list[list[float]]:
    field = []
    for _ in range(n):
        col = []
        for _ in range(nbins):
            r = rng.random()
            if style == "hard":      # VFR-like: clear or walled
                col.append(INF if r < 0.25 else 0.0)
            elif style == "ties":    # few distinct soft costs → many exact ties
                col.append(INF if r < 0.1 else rng.choice([0.0, 0.0, 0.5, 1.0, 2.0]))
            else:                    # arbitrary floats, non-dyadic sums
                col.append(INF if r < 0.1 else (0.0 if r < 0.5 else rng.uniform(0.0, 3.0)))
        field.append(col)
    return field


def test_crossing_matrix_matches_scalar_crossing_cost():
    rng = random.Random(704)
    for _ in range(50):
        nbins = rng.randint(1, 15)
        ci = [INF if rng.random() < 0.15 else rng.uniform(0, 3) for _ in range(nbins)]
        cj = [INF if rng.random() < 0.15 else rng.uniform(0, 3) for _ in range(nbins)]
        mi, mj = _vp._crossing_matrix(np.array(ci)), _vp._crossing_matrix(np.array(cj))
        for a in range(nbins):
            for b in range(nbins):
                if a == b:
                    continue
                assert max(mi[a, b], mj[a, b]) == _vp._crossing_cost(ci, cj, a, b)


def test_vectorised_solve_matches_scalar_reference():
    rng = random.Random(335704)
    for trial in range(400):
        style = ("hard", "ties", "float")[trial % 3]
        n = rng.randint(1, 12)
        nbins = rng.randint(1, 12)
        field = _random_field(rng, n, nbins, style)
        bins = [500 * (b + 1) for b in range(nbins)]
        start = end = None
        if rng.random() < 0.5:
            start = floor_reachable_bins(field[0])
            end = floor_reachable_bins(field[-1])
        elif rng.random() < 0.3:
            start = set(rng.sample(range(nbins), rng.randint(0, nbins)))
            end = set(rng.sample(range(nbins), rng.randint(0, nbins)))
        model = CostModel(
            cost_field=field,
            distances_nm=[10.0 * i for i in range(n)],
            bin_altitudes_ft=bins,
            allowed_start_bins=start,
            allowed_end_bins=end,
        )
        preferred = rng.choice(bins)
        assert solve(model, preferred) == _solve_reference(model, preferred), (trial, style)
