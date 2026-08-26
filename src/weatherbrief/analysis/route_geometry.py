"""Shared route geometry helpers.

Small, dependency-light functions used by more than one task module (route
weather observations, weather alternates, the advisory extent primitive) so
callers don't reach into each other's private helpers.

:class:`RouteExtent` lives here rather than in ``advisories/_helpers`` for the
same reason :func:`cell_edges` does: the convective-character classifier in
``analysis/sounding/convective.py`` measures its contiguous-embedded run with it,
and the sounding layer must not import from the advisory layer. One definition,
so the extent a gate fires on and the "(Xnm/Ynm)" a card prints cannot drift.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from weatherbrief.models.analysis import RouteConfig


def compute_route_distances(route: RouteConfig) -> list[float]:
    """Cumulative great-circle distance (NM) at each waypoint, starting at 0."""
    from euro_aip.models.navpoint import NavPoint

    distances = [0.0]
    for i in range(1, len(route.waypoints)):
        prev = route.waypoints[i - 1]
        curr = route.waypoints[i]
        nav_a = NavPoint(latitude=prev.lat, longitude=prev.lon)
        nav_b = NavPoint(latitude=curr.lat, longitude=curr.lon)
        _, leg_nm = nav_a.haversine_distance(nav_b)
        distances.append(distances[-1] + leg_nm)
    return distances


def cell_edges(
    distances: Sequence[float], total_nm: float
) -> tuple[list[float], list[float]]:
    """Per-point cell boundaries: each point owns ``[left, right]`` midway to its neighbours.

    First point's cell starts at 0, last point's ends at ``total_nm`` — so the
    per-point cells tile ``[0, total_nm]`` exactly. Boundaries fall midway
    between adjacent points, matching the ribbon/region convention.

    Lives here rather than in ``advisories/_helpers`` because the convective
    *character* classifier (``analysis/sounding/convective.py``) measures its
    contiguous-embedded extent with the same convention, and the sounding layer
    must not import from the advisory layer. One definition so the extent a
    classifier gates on and the "(Xnm/Ynm)" a card prints cannot drift.
    """
    n = len(distances)
    lefts = [
        0.0 if i == 0 else (distances[i - 1] + distances[i]) / 2.0
        for i in range(n)
    ]
    rights = [
        total_nm if i == n - 1 else (distances[i] + distances[i + 1]) / 2.0
        for i in range(n)
    ]
    return lefts, rights


class RouteExtent(NamedTuple):
    """How much of a domain one advisory×model×severity-tier actually covers (#571).

    The single answer to *"how much of the flight is affected?"*. Before this
    existed the codebase carried four incompatible conventions for it — a
    proportional ``total_nm × affected/total`` in every message, a
    geometry-accurate midpoint-cell sum in the JSON, a contiguous-run measure in
    the convective-character gate and a gap-including ``max−min`` span in the VFR
    mitigation tip — and the message and the structured field routinely
    disagreed by up to 4×. One value object, produced once from the same
    ``cell_edges`` geometry the ribbon uses, makes that unrepeatable.

    Fields:
        ``points`` — affected point count.
        ``domain_points`` — the denominator in points (in-domain samples).
        ``nm`` — Σ midpoint-owned cells of the affected points.
        ``domain_nm`` — the DENOMINATOR's own nm: the miles the advisory could
            actually grade — route miles for most evaluators, *mountain* miles
            for ``mountain_wind``. Travelling with
            the extent is what makes "a restricted domain multiplied by the whole
            route length" (the ~4× ``mountain_wind`` overstatement) impossible to
            write: there is no route length lying around to multiply by.
        ``longest_run_nm`` — longest *contiguous* affected run, for barrier-type
            hazards ("you cannot get around it") as opposed to union coverage.
        ``minutes`` — ``nm`` at the flight's groundspeed, when a speed is known.
        ``distance_known`` — False on the degenerate zero-length route; the
            percentage still means what it says, the miles do not.
    """

    points: int
    domain_points: int
    nm: float
    domain_nm: float
    longest_run_nm: float = 0.0
    minutes: float | None = None
    # False when the route carried no usable length and the geometry fell back
    # to even spacing over a unit route (see :func:`route_extent`). The ratios
    # are then real but the absolute figures are unitless, so a message must
    # print the percentage alone rather than invent miles.
    distance_known: bool = True

    @property
    def pct(self) -> float:
        """Coverage as a percentage of the domain, measured in distance.

        Distance-based, not point-based: ``interpolate_route`` spaces points
        evenly at 10 nm but inserts extras at waypoints, so a point ratio and a
        distance ratio never agree. Keying the percentage off the same ``nm``
        the message prints makes them consistent by construction.
        """
        return 100.0 * self.nm / self.domain_nm if self.domain_nm else 0.0


# Cell width for the zero-length-route fallback (see :func:`route_extent`).
# Any value works — only ratios are read — but a whole number well above the
# 0.1 rounding granularity keeps the percentage exact.
_SYNTHETIC_CELL_NM = 100.0

EMPTY_EXTENT = RouteExtent(points=0, domain_points=0, nm=0.0, domain_nm=0.0)


def route_extent(
    distances: Sequence[float],
    total_nm: float,
    affected: Sequence[bool],
    in_domain: Sequence[bool] | None = None,
    speed_kt: float | None = None,
) -> RouteExtent:
    """Reduce per-point flags to a :class:`RouteExtent` over the route geometry.

    ``distances`` must be in along-route order and index-aligned with
    ``affected`` (and ``in_domain``, which defaults to all-True). Each point owns
    the interval to the midpoints of its neighbours (``cell_edges``) — the same
    geometry the ribbon and the convective-character contiguity gate use, so an
    extent, a highlight and a gate can never describe different pictures.

    ``domain_nm`` sums the cells of the in-domain points — the miles the
    percentage is a percentage *of*. Callers pass the points the advisory can
    actually speak for: for most evaluators that is the whole route, for
    ``mountain_wind`` the route's mountain points, and in both cases only the
    points the model resolved. Excluding unassessable points is the #391
    property — two snowing points among eight blanks must read as snow, not as
    20% of a route the model never saw — and it is why a fully-assessed
    evaluator's ``domain_nm`` equals its route length while a partially-assessed
    one honestly prints the span it graded.

    ``speed_kt`` is a groundspeed for the display-only ``minutes`` axis; omit it
    and ``minutes`` stays ``None``.
    """
    n = len(distances)
    if n == 0:
        return EMPTY_EXTENT
    dom = list(in_domain) if in_domain is not None else [True] * n
    aff = list(affected)

    # A zero-length route is not a reason to stop measuring. ``total_nm`` can be
    # 0 for a pattern or sightseeing flight whose origin and destination are the
    # same point — waypoint *count* is validated, distinctness is not — and
    # returning an empty extent there made ``grade_extent`` answer GREEN however
    # completely the weather covered the route. That is the #391 false-GREEN
    # failure mode: before this primitive existed these evaluators graded on a
    # point ratio, which has no dependency on route length. Fall back to even
    # spacing over a UNIT route, where a distance ratio is exactly the point
    # ratio, and flag the absolute figures as unitless (#571 review).
    known = total_nm > 0
    if not known:
        # Synthetic geometry at a scale where each point owns exactly one whole
        # cell, so the ratio survives the 0.1 rounding below intact. A unit
        # route would not: three of four points is 0.75, which rounds to 0.8 and
        # reports 80% instead of 75%.
        distances = [(i + 0.5) * _SYNTHETIC_CELL_NM for i in range(n)]
        total_nm = n * _SYNTHETIC_CELL_NM

    # ``cell_edges`` assumes along-route order, and its siblings ``build_ribbon``
    # / ``build_regions`` sort before reducing. Sort here too rather than trusting
    # every caller: an unsorted list produces negative cell widths, which would
    # net out to a small or zero ``nm`` and grade GREEN — the same false-GREEN
    # class, arriving through the one primitive documented as the single answer.
    order = sorted(range(n), key=lambda i: distances[i])
    if order != list(range(n)):
        distances = [distances[i] for i in order]
        aff = [aff[i] for i in order]
        dom = [dom[i] for i in order]

    lefts, rights = cell_edges(list(distances), total_nm)

    nm = 0.0
    domain_nm = 0.0
    longest = 0.0
    run_start: int | None = None
    for i in range(n):
        width = rights[i] - lefts[i]
        if dom[i]:
            domain_nm += width
        if aff[i]:
            nm += width
            if run_start is None:
                run_start = i
            longest = max(longest, rights[i] - lefts[run_start])
        else:
            run_start = None

    nm = round(nm, 1)
    return RouteExtent(
        points=sum(1 for a in aff if a),
        domain_points=sum(1 for d in dom if d),
        nm=nm,
        domain_nm=round(domain_nm, 1),
        longest_run_nm=round(longest, 1),
        minutes=(
            round(60.0 * nm / speed_kt, 1)
            if known and speed_kt and speed_kt > 0 else None
        ),
        distance_known=known,
    )
