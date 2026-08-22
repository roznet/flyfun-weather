"""Shared route geometry helpers.

Small, dependency-light functions used by more than one task module (route
weather observations, weather alternates) so callers don't reach into each
other's private helpers.
"""

from __future__ import annotations

from collections.abc import Sequence

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
