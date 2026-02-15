"""Shared utilities for advisory evaluators."""

from __future__ import annotations

from typing import TYPE_CHECKING

from weatherbrief.models import AdvisoryStatus, ElevationProfile

if TYPE_CHECKING:
    from weatherbrief.models import RouteCrossSection


def format_extent(
    affected: int,
    total: int,
    total_distance_nm: float,
) -> str:
    """Format affected/total as a distance string, e.g. '30nm/55nm (55%)'.

    Converts point counts to nautical miles using the actual route distance
    and number of analysis points. When there are too few points to compute
    spacing, falls back to the percentage only.
    """
    if total <= 0:
        return "0nm"
    spacing = total_distance_nm / max(total - 1, 1)
    affected_nm = round(affected * spacing)
    total_nm = round(total_distance_nm)
    pct = 100 * affected / total
    return f"{affected_nm}nm/{total_nm}nm ({pct:.0f}%)"


def worst_status(statuses: list[AdvisoryStatus]) -> AdvisoryStatus:
    """Return the worst (most severe) status from a list."""
    order = [AdvisoryStatus.GREEN, AdvisoryStatus.AMBER, AdvisoryStatus.RED]
    worst = AdvisoryStatus.GREEN
    for s in statuses:
        if s == AdvisoryStatus.UNAVAILABLE:
            continue
        if s in order and order.index(s) > order.index(worst):
            worst = s
    return worst


def pct_above_threshold(
    affected: int,
    total: int,
    amber_pct: float,
    red_pct: float | None = None,
) -> AdvisoryStatus:
    """Common pattern: GREEN below amber threshold, AMBER between, RED above red threshold."""
    if total == 0:
        return AdvisoryStatus.GREEN
    pct = 100.0 * affected / total
    if red_pct is not None and pct >= red_pct:
        return AdvisoryStatus.RED
    if pct >= amber_pct:
        return AdvisoryStatus.AMBER
    return AdvisoryStatus.GREEN


def terrain_at_distance(
    elevation: ElevationProfile | None,
    distance_nm: float,
) -> float | None:
    """Interpolate terrain elevation at a given distance along the route.

    Returns elevation in feet, or None if no profile available.
    """
    if elevation is None or not elevation.points:
        return None

    points = elevation.points

    # Clamp to range
    if distance_nm <= points[0].distance_nm:
        return points[0].elevation_ft
    if distance_nm >= points[-1].distance_nm:
        return points[-1].elevation_ft

    # Binary search for bracketing points
    lo, hi = 0, len(points) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if points[mid].distance_nm <= distance_nm:
            lo = mid
        else:
            hi = mid

    # Linear interpolation
    p0, p1 = points[lo], points[hi]
    if p1.distance_nm == p0.distance_nm:
        return p0.elevation_ft
    frac = (distance_nm - p0.distance_nm) / (p1.distance_nm - p0.distance_nm)
    return p0.elevation_ft + frac * (p1.elevation_ft - p0.elevation_ft)


def max_terrain_near_point(
    elevation: ElevationProfile | None,
    distance_nm: float,
    radius_nm: float = 5.0,
) -> float | None:
    """Find maximum terrain elevation within radius of a distance along the route."""
    if elevation is None or not elevation.points:
        return None

    max_elev = None
    for pt in elevation.points:
        if abs(pt.distance_nm - distance_nm) <= radius_nm:
            if max_elev is None or pt.elevation_ft > max_elev:
                max_elev = pt.elevation_ft
    return max_elev


def wind_at_altitude(
    cross_sections: list[RouteCrossSection],
    model: str,
    point_index: int,
    target_alt_ft: float,
) -> tuple[float, float] | None:
    """Find wind speed/direction at nearest pressure level to target altitude.

    Returns (speed_kt, direction_deg) or None if unavailable.
    """
    from weatherbrief.models import altitude_to_pressure_hpa

    target_pressure = altitude_to_pressure_hpa(int(target_alt_ft))

    for cs in cross_sections:
        if cs.model.value != model:
            continue
        if point_index >= len(cs.point_forecasts):
            return None

        wf = cs.point_forecasts[point_index]
        if not wf.hourly:
            return None

        # Use the first hourly forecast (closest to target time)
        hourly = wf.hourly[0]
        best_level = None
        best_diff = float("inf")

        for level in hourly.pressure_levels:
            if level.wind_speed_kt is None or level.wind_direction_deg is None:
                continue
            diff = abs(level.pressure_hpa - target_pressure)
            if diff < best_diff:
                best_diff = diff
                best_level = level

        if best_level is not None:
            return (best_level.wind_speed_kt, best_level.wind_direction_deg)

    return None
