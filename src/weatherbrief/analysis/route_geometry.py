"""Shared route geometry helpers.

Small, dependency-light functions used by more than one task module (route
weather observations, weather alternates) so callers don't reach into each
other's private helpers.
"""

from __future__ import annotations

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
