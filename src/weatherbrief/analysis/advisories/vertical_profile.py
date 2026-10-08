"""Shared vertical-profile path-finder over a ``(route-point × altitude-bin)`` grid.

See ``designs/future/vertical-profile-solver.md``. A consumer advisory (VFR
feasibility, icing escape) builds a :class:`CostModel` via a hazard→cost mapping;
this module's :func:`solve` returns either the min-cost continuous vertical profile
(the climb/cruise/descent bands and the transitions between them) or the blocking
segment when no feasible path exists.

The model, in one paragraph:

- Each route point ``i`` and altitude bin ``b`` carries a **cost** ``cost_field[i][b]``:
  ``inf`` = hard wall (cannot occupy/cross), finite = soft wall (occupy at a penalty),
  ``0`` = feasible. Terrain floor and ceiling are baked in as ``inf`` outside the band.
- A path picks one bin per point. **Occupying** ``(i, b)`` costs ``cost_field[i][b]``.
- A **transition** from bin ``a`` at point ``i`` to bin ``b`` at ``i+1`` (``a != b``)
  crosses the altitude interval between them. Per the conservative column convention
  (design decision 6) the crossing is charged against *both* endpoint columns over the
  strictly-in-between bins: ``inf`` if *either* column walls the interval, else the
  ``max`` of the two columns' summed finite costs. Each transition also increments a
  transition counter.
- The objective is **lexicographic** (design decision 5): feasibility (no ``inf``
  crossed, implicit) → lowest total finite hazard cost → fewest transitions → smallest
  deviation from the preferred altitude. No summed weights.
- The path is **surface-anchored** (design decision 9): it may begin only in
  ``allowed_start_bins`` at point 0 and end only in ``allowed_end_bins`` at the last
  point. :func:`floor_reachable_bins` computes the natural default — the contiguous run
  of finite bins upward from the floor — which for a hard wall stops at the first deck
  (you cannot climb over it from the field) and for a soft wall continues through it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

INF = math.inf

# Canonical altitude-bin granularity (ft) for every consumer's cost grid. 500 ft matches
# the resolution of the VFR vertical-mitigation altitudes. Sourced here so VFR and icing
# share one value rather than redefining it per module.
MITIGATION_BIN_STEP_FT = 500


@dataclass(frozen=True)
class CostModel:
    """Everything the solver needs, produced by an advisory's hazard→cost mapping.

    ``cost_field[i][b]`` is the cost of occupying bin ``b`` at route point ``i``
    (``inf`` outside ``[terrain_floor, ceiling]`` — the bounds are baked in here rather
    than passed separately, keeping :func:`solve` a pure grid search). ``distances_nm``
    and ``bin_altitudes_ft`` label the two axes. ``allowed_start_bins`` /
    ``allowed_end_bins`` constrain the path's endpoints (``None`` = any bin).
    """

    cost_field: list[list[float]]
    distances_nm: list[float]
    bin_altitudes_ft: list[int]
    allowed_start_bins: set[int] | None = None
    allowed_end_bins: set[int] | None = None


@dataclass(frozen=True)
class Segment:
    """A contiguous along-route stretch flown at one altitude bin."""

    dist_from_nm: float
    dist_to_nm: float
    alt_ft: int


@dataclass(frozen=True)
class Transition:
    """A climb or descent on the edge between two adjacent route points.

    ``from_nm`` is the distance of the point where the aircraft leaves ``from_alt_ft``;
    ``to_nm`` is where it settles at ``to_alt_ft``. A climb has ``to_alt_ft >
    from_alt_ft``; a descent the reverse. Consumers choose which distance to phrase
    against (a climb-out reports ``to_nm``; a descent reports ``from_nm``).
    """

    from_nm: float
    to_nm: float
    from_alt_ft: int
    to_alt_ft: int


@dataclass(frozen=True)
class Profile:
    """A feasible continuous vertical profile: bands + the transitions between them."""

    segments: list[Segment]
    transitions: list[Transition]
    total_cost: float


@dataclass(frozen=True)
class Blockage:
    """No feasible path: the along-route band where continuity breaks, with a reason."""

    from_nm: float
    to_nm: float
    reason: str


@dataclass(frozen=True)
class _Cost:
    """Lexicographic path cost: (hazard, transitions, deviation), all additive ≥ 0."""

    hazard: float = 0.0
    transitions: int = 0
    deviation: float = 0.0

    def __add__(self, other: _Cost) -> _Cost:
        return _Cost(
            self.hazard + other.hazard,
            self.transitions + other.transitions,
            self.deviation + other.deviation,
        )

    def _key(self) -> tuple[float, float, int]:
        # Lexicographic order (design decision 5): lowest finite hazard → closest to
        # preferred altitude → fewest transitions. NOTE deviation precedes transitions:
        # with transitions ahead of deviation, a flat low profile (0 transitions) would
        # beat climbing back to cruise (1 transition) at equal hazard — i.e. the aircraft
        # forced low by a departure deck would never climb back up. Transitions are only a
        # tie-break between equally-close-to-cruise paths (prevents needless oscillation).
        # Codex's icing concern ("stay in icing vs two-transition escape") is handled by
        # the hazard tier above, so it is unaffected by this sub-order.
        return (self.hazard, self.deviation, self.transitions)

    def __lt__(self, other: _Cost) -> bool:
        return self._key() < other._key()


_INF_COST = _Cost(INF, 10**9, INF)


def floor_bin(bin_altitudes_ft: list[int], floor_alt_ft: float) -> int:
    """Index of the lowest bin at or above ``floor_alt_ft`` (``len`` if none qualify)."""
    for b, alt in enumerate(bin_altitudes_ft):
        if alt >= floor_alt_ft:
            return b
    return len(bin_altitudes_ft)


def floor_reachable_bins(column: list[float], floor_bin: int = 0) -> set[int]:
    """Bins reachable by climbing from the terrain floor at one route point.

    Scans upward from ``floor_bin`` (the lowest occupiable bin — terrain + margin) and
    stops at the first ``inf`` (a hard wall the aircraft cannot climb through from the
    field). For a soft-wall column (icing: finite, not ``inf``) the run continues through
    the layer — exactly the hard-wall / soft-wall distinction the anchoring needs.

    ``floor_bin`` must be passed explicitly rather than inferred from the leading run of
    ``inf`` cells: a deck sitting *at* the floor is also ``inf``, and inferring the floor
    would wrongly skip over it and anchor the start above the deck. Returns an empty set
    when the floor bin itself is walled (no way to get airborne into clear air).
    """
    bins: set[int] = set()
    for b in range(floor_bin, len(column)):
        if column[b] == INF:
            break
        bins.add(b)
    return bins


def _crossing_matrix(col: np.ndarray) -> np.ndarray:
    """Summed cost of the bins strictly between every pair of bins in one column.

    ``out[lo, hi]`` (``lo < hi``) is ``col[lo+1] + … + col[hi-1]`` — ``0`` for adjacent
    bins, ``inf`` if any in-between bin is ``inf`` (costs are ≥ 0, so an ``inf`` term
    makes the sum ``inf`` with no ``nan``). The matrix is symmetric; the diagonal is
    unused (staying in a bin is not a crossing).

    Built with ``cumsum`` along rows, which accumulates strictly left to right from
    ``0.0`` — the same order, and so the same float result bit for bit, as summing the
    interval in a loop. A prefix-sum difference (``P[hi] - P[lo+1]``) would be O(1)
    too but rounds differently, which could flip exact-tie decisions in the
    lexicographic objective. O(B²) per column instead of O(B³) per edge.
    """
    nbins = col.shape[0]
    k = np.arange(nbins)
    # row lo holds col[k] for k > lo, else 0 (adding 0.0 first is exact).
    terms = np.where(k[None, :] > k[:, None], col[None, :], 0.0)
    acc = np.cumsum(terms, axis=1)  # acc[lo, k] = col[lo+1] + … + col[k]
    upper = np.zeros((nbins, nbins))
    upper[:, 1:] = acc[:, :-1]      # upper[lo, hi] = col[lo+1] + … + col[hi-1]
    upper = np.triu(upper, 1)
    return upper + upper.T


def _crossing_cost(col_i: list[float], col_j: list[float], a: int, b: int) -> float:
    """Cost of a transition crossing bins strictly between ``a`` and ``b``.

    Conservative column convention (decision 6): ``inf`` if either endpoint column walls
    any strictly-in-between bin, else the ``max`` of the two columns' summed finite
    costs over that interval. Endpoints ``a``/``b`` are node-costed at their own points,
    so they are excluded here. Scalar reference for :func:`_crossing_matrix`, which
    :func:`solve` uses.
    """
    lo, hi = (a, b) if a < b else (b, a)
    sum_i = 0.0
    sum_j = 0.0
    for k in range(lo + 1, hi):
        if col_i[k] == INF or col_j[k] == INF:
            return INF
        sum_i += col_i[k]
        sum_j += col_j[k]
    return max(sum_i, sum_j)


def solve(model: CostModel, preferred_alt_ft: int, rate_limit: float | None = None) -> Profile | Blockage:
    """Return the lexicographically-best vertical profile, or a :class:`Blockage`.

    ``rate_limit`` is an accepted-but-unused hook (design decision 4). The search is a
    forward dynamic program over points, vectorised over the ``(from-bin × to-bin)``
    transition matrix at each step (issue #704: the scalar triple loop cost ~0.2–0.6 s
    per solve on a 64-point grid and runs per model, per time-scan candidate).

    Equivalence with the scalar formulation is exact, ties included: hazard and
    deviation are summed in the same order (``(dp + edge) + node``), crossing sums come
    from :func:`_crossing_matrix`, and among equal ``(hazard, deviation, transitions)``
    keys the lowest from-bin wins, as the scalar ``a``-ascending strict-``<`` scan did.
    ``tests/test_vertical_profile.py`` pins this against a copy of the scalar solver.
    """
    cf_list = model.cost_field
    n = len(cf_list)
    if n == 0:
        return Blockage(0.0, 0.0, "no route points")
    nbins = len(model.bin_altitudes_ft)
    alts = model.bin_altitudes_ft

    start_bins = model.allowed_start_bins if model.allowed_start_bins is not None else set(range(nbins))
    end_bins = model.allowed_end_bins if model.allowed_end_bins is not None else set(range(nbins))

    cf = np.asarray(cf_list, dtype=float).reshape(n, nbins)
    dev = np.abs(np.asarray(alts, dtype=float) - preferred_alt_ft)
    step = (np.arange(nbins)[:, None] != np.arange(nbins)[None, :]).astype(np.int64)

    # dp_* = best cost to reach each bin at the current point (inf hazard = unreachable);
    # parent[i, b] = previous bin on the best path into (i, b).
    dp_h = np.full(nbins, INF)
    dp_t = np.full(nbins, _INF_COST.transitions, dtype=np.int64)
    dp_d = np.full(nbins, INF)
    parent = np.full((n, nbins), -1, dtype=np.int64)

    for b in range(nbins):
        if b in start_bins and cf[0, b] != INF:
            dp_h[b] = cf[0, b]
            dp_t[b] = 0
            dp_d[b] = dev[b]

    # Track forward reachability for blockage localisation.
    reachable_upto = 0 if np.any(dp_h != INF) else -1

    cross_prev = _crossing_matrix(cf[0])
    for i in range(1, n):
        col_cur = cf[i]
        cross_cur = _crossing_matrix(col_cur)
        edge_h = np.maximum(cross_prev, cross_cur)
        np.fill_diagonal(edge_h, 0.0)
        # cand_*[a, b]: arrive at bin b of point i from bin a of point i-1.
        valid = (dp_h != INF)[:, None] & (edge_h != INF) & (col_cur != INF)[None, :]
        with np.errstate(invalid="ignore"):
            cand_h = np.where(valid, (dp_h[:, None] + edge_h) + col_cur[None, :], INF)
            cand_d = np.where(valid, (dp_d[:, None] + 0.0) + dev[None, :], INF)
        cand_t = np.where(valid, dp_t[:, None] + step, _INF_COST.transitions)

        # Lexicographic argmin down each column: hazard → deviation → transitions →
        # lowest from-bin (argmax returns the first True).
        tie = cand_h == cand_h.min(axis=0)
        d_masked = np.where(tie, cand_d, INF)
        tie &= d_masked == d_masked.min(axis=0)
        t_masked = np.where(tie, cand_t, np.iinfo(np.int64).max)
        tie &= t_masked == t_masked.min(axis=0)
        best_a = np.argmax(tie, axis=0)

        reached = valid.any(axis=0)
        cols = np.arange(nbins)
        dp_h = np.where(reached, cand_h[best_a, cols], INF)
        dp_d = np.where(reached, cand_d[best_a, cols], INF)
        dp_t = np.where(reached, cand_t[best_a, cols], _INF_COST.transitions)
        # A reachable-from-nowhere bin keeps parent -1 (only read on a feasible path).
        parent[i] = np.where(reached, best_a, -1)
        cross_prev = cross_cur
        if reached.any():
            reachable_upto = i

    dp = [
        _Cost(float(h), int(t), float(d)) if h != INF else _INF_COST
        for h, t, d in zip(dp_h, dp_t, dp_d)
    ]

    # Pick the best feasible end bin.
    best_end = -1
    best_cost = _INF_COST
    for b in end_bins:
        if dp[b].hazard != INF and dp[b] < best_cost:
            best_cost = dp[b]
            best_end = b

    if best_end < 0:
        return _blockage(model, reachable_upto, end_bins, dp)

    # Backtrack the bin sequence.
    seq = [0] * n
    seq[n - 1] = best_end
    for i in range(n - 1, 0, -1):
        seq[i - 1] = int(parent[i, seq[i]])

    return _to_profile(model, seq, best_cost.hazard)


def _to_profile(model: CostModel, seq: list[int], total_cost: float) -> Profile:
    """Collapse a per-point bin sequence into segments + transitions."""
    dist = model.distances_nm
    alts = model.bin_altitudes_ft
    n = len(seq)

    segments: list[Segment] = []
    transitions: list[Transition] = []
    seg_start_i = 0
    for i in range(1, n):
        if seq[i] != seq[i - 1]:
            segments.append(Segment(dist[seg_start_i], dist[i - 1], alts[seq[i - 1]]))
            transitions.append(
                Transition(dist[i - 1], dist[i], alts[seq[i - 1]], alts[seq[i]])
            )
            seg_start_i = i
    segments.append(Segment(dist[seg_start_i], dist[n - 1], alts[seq[n - 1]]))
    return Profile(segments=segments, transitions=transitions, total_cost=total_cost)


def _blockage(model: CostModel, reachable_upto: int, end_bins: set[int], dp: list[_Cost]) -> Blockage:
    """Locate where continuity breaks and describe the wall there."""
    dist = model.distances_nm
    alts = model.bin_altitudes_ft
    n = len(model.cost_field)

    # If the last column was reached but no *allowed* end bin is feasible, the block is
    # at the arrival column; otherwise it is the first column we could not reach.
    if reachable_upto >= n - 1:
        blk = n - 1
    else:
        blk = min(reachable_upto + 1, n - 1) if reachable_upto >= 0 else 0

    col = model.cost_field[blk]
    walled = [alts[b] for b, c in enumerate(col) if c == INF]
    if walled:
        reason = f"no clear band near {dist[blk]:.0f} nm (wall {min(walled)}–{max(walled)} ft)"
    else:
        reason = f"no continuous clear profile through {dist[blk]:.0f} nm"

    from_nm = dist[max(blk - 1, 0)]
    to_nm = dist[min(blk + 1, n - 1)]
    return Blockage(from_nm=from_nm, to_nm=to_nm, reason=reason)
