"""Common route walking generator — shared by route interpolation and elevation profiling."""

from __future__ import annotations

from collections.abc import Iterator
import math

from euro_aip.models.navpoint import NavPoint

from weatherbrief.models import RouteConfig


def walk_route(
    route: RouteConfig,
    spacing_nm: float,
    *,
    max_segment_nm: float | None = None,
    max_segments: int | None = None,
) -> Iterator[tuple[float, float, float, str | None, str | None]]:
    """Yield (lat, lon, distance_nm, waypoint_icao, waypoint_name) along a route.

    Walks each leg using great-circle math, yielding points at the
    specified spacing. Named waypoints are always included.

    Opt-in ``max_segment_nm`` uses unrounded equal subdivisions, with no
    final-gap tolerance. ``max_segments`` refuses the entire route before
    yielding any point. Legacy callers retain their original sampling.
    """
    if max_segment_nm is not None:
        yield from _walk_strict(route, spacing_nm, max_segment_nm, max_segments)
        return
    if max_segments is not None:
        raise ValueError("max_segments requires max_segment_nm")
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


def _walk_strict(route, spacing_nm, max_segment_nm, max_segments):
    if not all(math.isfinite(v) and v > 0 for v in (spacing_nm, max_segment_nm)):
        raise ValueError("invalid_route")
    if max_segments is not None and (type(max_segments) is not int or max_segments < 1):
        raise ValueError("route_segment_limit")
    if len(route.waypoints) < 2 or any(
        not math.isfinite(wp.lat) or not math.isfinite(wp.lon)
        or abs(wp.lat) > 90 or abs(wp.lon) > 180 for wp in route.waypoints
    ):
        raise ValueError("invalid_route")
    legs = []
    count = 0
    for a, b in zip(route.waypoints, route.waypoints[1:]):
        start = NavPoint(latitude=a.lat, longitude=a.lon)
        end = NavPoint(latitude=b.lat, longitude=b.lon)
        bearing, distance = start.haversine_distance(end)
        if not math.isfinite(distance) or distance < 0:
            raise ValueError("invalid_route")
        steps = max(1, math.ceil(distance / min(spacing_nm, max_segment_nm)))
        count += steps
        if max_segments is not None and count > max_segments:
            raise ValueError("route_segment_limit")
        legs.append((start, b, bearing, distance, steps))
    first = route.waypoints[0]
    yield first.lat, first.lon, 0., first.icao, first.name
    cumulative = 0.
    for start, end, bearing, distance, steps in legs:
        for i in range(1, steps):
            along = distance * i / steps
            point = start.point_from_bearing_distance(bearing, along)
            yield point.latitude, point.longitude, cumulative + along, None, None
        cumulative += distance
        yield end.lat, end.lon, cumulative, end.icao, end.name
