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

INF = math.inf


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


def _crossing_cost(col_i: list[float], col_j: list[float], a: int, b: int) -> float:
    """Cost of a transition crossing bins strictly between ``a`` and ``b``.

    Conservative column convention (decision 6): ``inf`` if either endpoint column walls
    any strictly-in-between bin, else the ``max`` of the two columns' summed finite
    costs over that interval. Endpoints ``a``/``b`` are node-costed at their own points,
    so they are excluded here.
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
    forward dynamic program over points; with a ~60×40 grid it is effectively free.
    """
    cf = model.cost_field
    n = len(cf)
    if n == 0:
        return Blockage(0.0, 0.0, "no route points")
    nbins = len(model.bin_altitudes_ft)
    dist = model.distances_nm
    alts = model.bin_altitudes_ft

    start_bins = model.allowed_start_bins if model.allowed_start_bins is not None else set(range(nbins))
    end_bins = model.allowed_end_bins if model.allowed_end_bins is not None else set(range(nbins))

    def dev(b: int) -> float:
        return abs(alts[b] - preferred_alt_ft)

    # dp[b] = best cost to reach bin b at the current point; parent[i][b] = prev bin.
    dp: list[_Cost] = [_INF_COST] * nbins
    parent: list[list[int]] = [[-1] * nbins for _ in range(n)]

    for b in range(nbins):
        if b in start_bins and cf[0][b] != INF:
            dp[b] = _Cost(cf[0][b], 0, dev(b))

    # Track forward reachability for blockage localisation.
    reachable_upto = 0 if any(c.hazard != INF for c in dp) else -1

    for i in range(1, n):
        ndp: list[_Cost] = [_INF_COST] * nbins
        col_prev, col_cur = cf[i - 1], cf[i]
        for b in range(nbins):
            if col_cur[b] == INF:
                continue
            best = _INF_COST
            best_a = -1
            node = _Cost(col_cur[b], 0, dev(b))
            for a in range(nbins):
                if dp[a].hazard == INF:
                    continue
                if a == b:
                    edge = _Cost(0.0, 0, 0.0)
                else:
                    cc = _crossing_cost(col_prev, col_cur, a, b)
                    if cc == INF:
                        continue
                    edge = _Cost(cc, 1, 0.0)
                cand = dp[a] + edge + node
                if cand < best:
                    best = cand
                    best_a = a
            ndp[b] = best
            parent[i][b] = best_a
        dp = ndp
        if any(c.hazard != INF for c in dp):
            reachable_upto = i

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
        seq[i - 1] = parent[i][seq[i]]

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
