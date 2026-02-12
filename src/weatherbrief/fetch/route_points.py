"""Route interpolation — generate evenly-spaced points along a multi-leg route."""

from __future__ import annotations

from euro_aip.models.navpoint import NavPoint

from weatherbrief.models import RouteConfig, RoutePoint


def interpolate_route(
    route: RouteConfig, spacing_nm: float = 20.0
) -> list[RoutePoint]:
    """Generate evenly-spaced points along a route, including all named waypoints.

    Walks each leg using great-circle math, dropping an interpolated point
    every ``spacing_nm`` nautical miles. Named waypoints are always included
    and marked with their ICAO code.

    Returns:
        List of RoutePoint ordered by distance from origin.
    """
    points: list[RoutePoint] = []
    cumulative_nm = 0.0

    for leg_idx in range(len(route.waypoints) - 1):
        wp_a = route.waypoints[leg_idx]
        wp_b = route.waypoints[leg_idx + 1]

        nav_a = NavPoint(latitude=wp_a.lat, longitude=wp_a.lon)
        nav_b = NavPoint(latitude=wp_b.lat, longitude=wp_b.lon)

        # Add the start waypoint of this leg (only for leg 0 — later legs
        # get their start waypoint from the previous leg's endpoint)
        if leg_idx == 0:
            points.append(
                RoutePoint(
                    lat=wp_a.lat,
                    lon=wp_a.lon,
                    distance_from_origin_nm=cumulative_nm,
                    waypoint_icao=wp_a.icao,
                )
            )

        bearing, leg_distance = nav_a.haversine_distance(nav_b)

        # Interpolated points along the leg
        dist_along_leg = spacing_nm
        while dist_along_leg < leg_distance - 1.0:  # 1nm tolerance to avoid near-duplicates
            interp = nav_a.point_from_bearing_distance(bearing, dist_along_leg)
            points.append(
                RoutePoint(
                    lat=round(interp.latitude, 5),
                    lon=round(interp.longitude, 5),
                    distance_from_origin_nm=round(cumulative_nm + dist_along_leg, 1),
                )
            )
            dist_along_leg += spacing_nm

        # End waypoint of this leg
        cumulative_nm += leg_distance
        points.append(
            RoutePoint(
                lat=wp_b.lat,
                lon=wp_b.lon,
                distance_from_origin_nm=round(cumulative_nm, 1),
                waypoint_icao=wp_b.icao,
            )
        )

    return points
