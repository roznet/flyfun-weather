"""Route interpolation — generate evenly-spaced points along a multi-leg route."""

from __future__ import annotations

from weatherbrief.fetch.route_walk import walk_route
from weatherbrief.models import RouteConfig, RoutePoint


def interpolate_route(
    route: RouteConfig, spacing_nm: float = 10.0
) -> list[RoutePoint]:
    """Generate evenly-spaced points along a route, including all named waypoints.

    Walks each leg using great-circle math, dropping an interpolated point
    every ``spacing_nm`` nautical miles. Named waypoints are always included
    and marked with their ICAO code.

    Returns:
        List of RoutePoint ordered by distance from origin.
    """
    return [
        RoutePoint(
            lat=lat,
            lon=lon,
            distance_from_origin_nm=round(dist, 1),
            waypoint_icao=icao,
            waypoint_name=name,
        )
        for lat, lon, dist, icao, name in walk_route(route, spacing_nm)
    ]
