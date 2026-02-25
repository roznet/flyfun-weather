"""Spatial interpolation of cloud liquid water and ice mixing ratio across route points.

Some GFS grid cells return None for CLW/ICMR via Open-Meteo, which forces the
SFIP icing index to fall back to a "proxy" variant that overestimates icing
extent.  This module fills those gaps by linearly interpolating per-pressure-level
from neighboring route points that do have data.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from weatherbrief.models import PressureLevelData, RouteCrossSection, RoutePoint

logger = logging.getLogger(__name__)


def interpolate_cloud_water_spatially(
    cross_sections: list[RouteCrossSection],
    route_points: list[RoutePoint],
    max_gap_nm: float = 100.0,
) -> int:
    """Fill CLW/ICMR gaps by linear interpolation from neighboring route points.

    For each model cross-section, for each hourly forecast entry, and for each
    pressure level: if a point has CLW=None, find the nearest left and right
    neighbors (along the route) with CLW data and linearly interpolate in
    distance-space.  CLW=0.0 is treated as real data ("measured zero"), not a gap.

    Filled ``PressureLevelData`` objects get ``clw_interpolated = True`` so
    downstream SFIP computation can report variant="interp" instead of "proxy".

    Args:
        cross_sections: Model cross-sections with per-point forecasts.
        route_points: Route points with distance_from_origin_nm.
        max_gap_nm: Maximum distance (nautical miles) between bounding data
            points for interpolation to apply.

    Returns:
        Total count of (point, level) pairs that were filled.
    """
    if not cross_sections or not route_points:
        return 0

    distances = [rp.distance_from_origin_nm for rp in route_points]
    n_points = len(route_points)
    total_filled = 0

    for cs in cross_sections:
        if len(cs.point_forecasts) != n_points:
            continue

        # Iterate over each hourly time slot
        for hour_idx in range(_max_hourly_len(cs)):
            # Collect pressure levels present across all points at this hour
            all_pressures = _collect_pressures(cs, hour_idx, n_points)

            for pressure in all_pressures:
                filled = _interpolate_level(
                    cs, hour_idx, n_points, pressure, distances, max_gap_nm,
                )
                total_filled += filled

    if total_filled > 0:
        logger.info("Spatial interpolation filled %d (point, level) CLW/ICMR gaps", total_filled)

    return total_filled


def _max_hourly_len(cs: RouteCrossSection) -> int:
    """Return the maximum number of hourly entries across all points."""
    return max((len(wf.hourly) for wf in cs.point_forecasts), default=0)


def _collect_pressures(
    cs: RouteCrossSection, hour_idx: int, n_points: int,
) -> set[int]:
    """Collect all pressure level values present at a given hour across points."""
    pressures: set[int] = set()
    for pt_idx in range(n_points):
        hourly_list = cs.point_forecasts[pt_idx].hourly
        if hour_idx >= len(hourly_list):
            continue
        for pl in hourly_list[hour_idx].pressure_levels:
            pressures.add(pl.pressure_hpa)
    return pressures


def _get_level(
    cs: RouteCrossSection, pt_idx: int, hour_idx: int, pressure: int,
) -> PressureLevelData | None:
    """Get PressureLevelData for a specific point/hour/pressure, or None."""
    hourly_list = cs.point_forecasts[pt_idx].hourly
    if hour_idx >= len(hourly_list):
        return None
    for pl in hourly_list[hour_idx].pressure_levels:
        if pl.pressure_hpa == pressure:
            return pl
    return None


def _interpolate_level(
    cs: RouteCrossSection,
    hour_idx: int,
    n_points: int,
    pressure: int,
    distances: list[float],
    max_gap_nm: float,
) -> int:
    """Interpolate CLW/ICMR at one pressure level across route points.

    Returns the number of (point, level) pairs filled.
    """
    # Gather indices where CLW data exists vs is None
    has_data: list[int] = []
    needs_fill: list[int] = []

    for pt_idx in range(n_points):
        pl = _get_level(cs, pt_idx, hour_idx, pressure)
        if pl is None:
            continue
        if pl.cloud_liquid_water_kg_kg is not None:
            has_data.append(pt_idx)
        else:
            needs_fill.append(pt_idx)

    if not needs_fill or not has_data:
        return 0

    filled = 0
    for pt_idx in needs_fill:
        # Find nearest left neighbor with data
        left_idx = _find_neighbor(has_data, pt_idx, direction=-1)
        # Find nearest right neighbor with data
        right_idx = _find_neighbor(has_data, pt_idx, direction=+1)

        if left_idx is None or right_idx is None:
            continue  # edge gap — one-sided, skip

        gap_nm = distances[right_idx] - distances[left_idx]
        if gap_nm > max_gap_nm:
            continue  # gap too wide

        left_pl = _get_level(cs, left_idx, hour_idx, pressure)
        right_pl = _get_level(cs, right_idx, hour_idx, pressure)
        target_pl = _get_level(cs, pt_idx, hour_idx, pressure)

        if left_pl is None or right_pl is None or target_pl is None:
            continue

        # Distance fraction for linear interpolation
        if gap_nm <= 0:
            frac = 0.5
        else:
            frac = (distances[pt_idx] - distances[left_idx]) / gap_nm

        # Interpolate CLW
        if left_pl.cloud_liquid_water_kg_kg is not None and right_pl.cloud_liquid_water_kg_kg is not None:
            target_pl.cloud_liquid_water_kg_kg = round(
                left_pl.cloud_liquid_water_kg_kg * (1 - frac)
                + right_pl.cloud_liquid_water_kg_kg * frac,
                9,
            )

        # Interpolate ICMR (if both neighbors have it)
        if left_pl.ice_mixing_ratio_kg_kg is not None and right_pl.ice_mixing_ratio_kg_kg is not None:
            target_pl.ice_mixing_ratio_kg_kg = round(
                left_pl.ice_mixing_ratio_kg_kg * (1 - frac)
                + right_pl.ice_mixing_ratio_kg_kg * frac,
                9,
            )

        target_pl.clw_interpolated = True
        filled += 1

    return filled


def _find_neighbor(
    sorted_indices: list[int], target: int, direction: int,
) -> int | None:
    """Find nearest index in sorted_indices that is < target (direction=-1) or > target (direction=+1)."""
    if direction < 0:
        # Find largest index < target
        best = None
        for idx in sorted_indices:
            if idx < target:
                best = idx
            else:
                break
        return best
    else:
        # Find smallest index > target
        for idx in sorted_indices:
            if idx > target:
                return idx
        return None
