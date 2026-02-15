"""Common route walking generator — shared by route interpolation and elevation profiling."""

from __future__ import annotations

from collections.abc import Iterator

from euro_aip.models.navpoint import NavPoint

from weatherbrief.models import RouteConfig


def walk_route(
    route: RouteConfig,
    spacing_nm: float,
) -> Iterator[tuple[float, float, float, str | None, str | None]]:
    """Yield (lat, lon, distance_nm, waypoint_icao, waypoint_name) along a route.

    Walks each leg using great-circle math, yielding points at the
    specified spacing. Named waypoints are always included.
    """
    cumulative_nm = 0.0

    for leg_idx in range(len(route.waypoints) - 1):
        wp_a = route.waypoints[leg_idx]
        wp_b = route.waypoints[leg_idx + 1]

        nav_a = NavPoint(latitude=wp_a.lat, longitude=wp_a.lon)
        nav_b = NavPoint(latitude=wp_b.lat, longitude=wp_b.lon)

        # Emit start waypoint (only for first leg)
        if leg_idx == 0:
            yield (wp_a.lat, wp_a.lon, cumulative_nm, wp_a.icao, wp_a.name)

        bearing, leg_distance = nav_a.haversine_distance(nav_b)

        # Interpolated points along the leg
        dist_along_leg = spacing_nm
        while dist_along_leg < leg_distance - 1.0:  # 1nm tolerance
            interp = nav_a.point_from_bearing_distance(bearing, dist_along_leg)
            yield (
                round(interp.latitude, 5),
                round(interp.longitude, 5),
                round(cumulative_nm + dist_along_leg, 2),
                None,
                None,
            )
            dist_along_leg += spacing_nm

        # End waypoint of this leg
        cumulative_nm += leg_distance
        yield (wp_b.lat, wp_b.lon, round(cumulative_nm, 2), wp_b.icao, wp_b.name)
