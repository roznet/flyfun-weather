"""Shared utilities for advisory evaluators."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from collections.abc import Callable

from weatherbrief.models import (
    AdvisoryStatus,
    ElevationProfile,
    IcingZone,
    MitigationProfile,
    MitigationSegment,
    MitigationTransition,
)

if TYPE_CHECKING:
    from weatherbrief.analysis.advisories import RouteContext
    from weatherbrief.analysis.advisories.vertical_profile import CostModel, Profile
    from weatherbrief.models import RouteCrossSection, SoundingAnalysis


def build_cost_model(
    ctx: RouteContext,
    model: str,
    cell_cost: Callable[[SoundingAnalysis, float], float],
    floor_margin_ft: float,
) -> CostModel | None:
    """Assemble a ``(point × altitude)`` :class:`CostModel` for the shared solver (#335).

    The one place bin construction, per-point terrain-floor / ceiling walling, and
    floor-band start/end anchoring live — each advisory supplies only ``cell_cost``
    (its hazard→cost mapping) and the ``floor_margin_ft`` above terrain. Terrain is looked
    up once here via ``terrain_at_distance`` (linear interpolation) so every consumer of
    the shared solver computes the *same* floor for the same physical point. Returns None
    when no route point carries this model's sounding.
    """
    from weatherbrief.analysis.advisories.vertical_profile import (
        INF,
        MITIGATION_BIN_STEP_FT,
        CostModel,
        floor_bin,
        floor_reachable_bins,
    )

    cruise = ctx.cruise_altitude_ft
    ceiling = ctx.flight_ceiling_ft
    step = MITIGATION_BIN_STEP_FT
    top = max(int(ceiling), int(cruise))
    bins = list(range(step, top + step, step))

    points = [
        (rpa.distance_from_origin_nm or 0.0, rpa.sounding[model])
        for rpa in ctx.analyses
        if model in rpa.sounding
    ]
    points.sort(key=lambda t: t[0])
    if not points:
        return None

    distances = [d for d, _ in points]
    cost_field: list[list[float]] = []
    floors: list[float] = []
    for d, sounding in points:
        floor = (terrain_at_distance(ctx.elevation, d) or 0.0) + floor_margin_ft
        floors.append(floor)
        cost_field.append([
            INF if (alt < floor or alt > ceiling) else cell_cost(sounding, alt)
            for alt in bins
        ])

    start = floor_reachable_bins(cost_field[0], floor_bin(bins, floors[0]))
    end = floor_reachable_bins(cost_field[-1], floor_bin(bins, floors[-1]))
    return CostModel(
        cost_field=cost_field,
        distances_nm=distances,
        bin_altitudes_ft=bins,
        allowed_start_bins=start,
        allowed_end_bins=end,
    )


def to_mitigation_profile(profile: Profile) -> MitigationProfile:
    """Convert a solver :class:`Profile` into the storable :class:`MitigationProfile`.

    Shared by every advisory that derives mitigations from the vertical-profile solver
    (VFR feasibility, icing escape — issue #335), so the solver→model bridge lives once.
    """
    return MitigationProfile(
        segments=[
            MitigationSegment(dist_from_nm=s.dist_from_nm, dist_to_nm=s.dist_to_nm, altitude_ft=s.alt_ft)
            for s in profile.segments
        ],
        transitions=[
            MitigationTransition(
                from_nm=t.from_nm, to_nm=t.to_nm,
                from_altitude_ft=t.from_alt_ft, to_altitude_ft=t.to_alt_ft,
            )
            for t in profile.transitions
        ],
    )


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
    affected_nm = round(total_distance_nm * affected / total)
    total_nm = round(total_distance_nm)
    pct = 100 * affected / total
    return f"{affected_nm}nm/{total_nm}nm ({pct:.0f}%)"


def icing_zones_in_altitude_range(
    zones: list[IcingZone],
    floor_ft: float,
    ceiling_ft: float,
) -> list[IcingZone]:
    """Return icing zones that overlap the altitude range [floor_ft, ceiling_ft]."""
    return [z for z in zones if z.top_ft > floor_ft and z.base_ft < ceiling_ft]


def has_relevant_icing(
    zones: list[IcingZone],
    cruise_altitude_ft: float,
    buffer_ft: float = 2000,
) -> bool:
    """Check whether any icing zone overlaps [0, cruise_altitude + buffer].

    Consistent with FIKI's cruise_icing_buffer_ft logic: icing far above the
    cruise altitude is irrelevant for non-FIKI advisories.
    """
    ceiling = cruise_altitude_ft + buffer_ft
    return bool(icing_zones_in_altitude_range(zones, 0, ceiling))


def min_icing_clearance(
    zones: list[IcingZone],
    cruise_alt_ft: float,
) -> float:
    """Minimum vertical distance from cruise altitude to any icing zone.

    Returns ``float('inf')`` when no icing zones exist.
    """
    min_dist = float("inf")
    for zone in zones:
        if zone.base_ft <= cruise_alt_ft <= zone.top_ft:
            return 0.0
        elif cruise_alt_ft < zone.base_ft:
            min_dist = min(min_dist, zone.base_ft - cruise_alt_ft)
        else:
            min_dist = min(min_dist, cruise_alt_ft - zone.top_ft)
    return min_dist


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


def showers_at_point(
    cross_sections: list[RouteCrossSection],
    model: str,
    point_index: int,
    target_time: datetime,
) -> float | None:
    """Convective precipitation (showers, mm) at a route point for one model.

    ``showers`` is Open-Meteo's convective-only precip and is available for
    every model — the uniform realized-convection signal used by the convective
    character advisory (issue #294). Picks the hourly nearest ``target_time``.
    Returns None when unavailable.
    """
    for cs in cross_sections:
        if cs.model.value != model:
            continue
        if point_index >= len(cs.point_forecasts):
            return None
        hourly = cs.point_forecasts[point_index].at_time(target_time)
        if hourly is None:
            return None
        return hourly.showers_mm
    return None


def wind_at_altitude(
    cross_sections: list[RouteCrossSection],
    model: str,
    point_index: int,
    target_alt_ft: float,
    target_time: datetime,
) -> tuple[float, float] | None:
    """Find wind speed/direction at nearest pressure level to target altitude.

    Picks the hourly forecast nearest to *target_time* (rather than the
    first hour available, which can lag the route point's actual valid
    time on multi-hour flights). Returns (speed_kt, direction_deg) or
    None if unavailable.
    """
    from weatherbrief.analysis.wind import pick_wind_at_pressure
    from weatherbrief.models import altitude_to_pressure_hpa

    target_pressure = altitude_to_pressure_hpa(int(target_alt_ft))

    for cs in cross_sections:
        if cs.model.value != model:
            continue
        if point_index >= len(cs.point_forecasts):
            return None

        wf = cs.point_forecasts[point_index]
        hourly = wf.at_time(target_time)
        if hourly is None:
            return None

        best_level = pick_wind_at_pressure(hourly, target_pressure)
        if best_level is not None and best_level.wind_speed_kt is not None and best_level.wind_direction_deg is not None:
            return (best_level.wind_speed_kt, best_level.wind_direction_deg)

    return None
