"""Spatial interpolation of GRIB-enriched fields across route points.

Fills gaps where a route point's GRIB grid cell returned None by linearly
interpolating from neighboring route points that have data.  Both neighbors
must exist (no edge extrapolation) and the gap must be within ``max_gap_nm``.

Interpolation rules (see also fetch/grib/fill.py for the time axis):

    Time axis  — forward-fill from preceding native GRIB hour (fill.py)
    Spatial axis — linear interpolation between neighboring route points (here)
    Vertical axis — linear in pressure, handled in sounding analysis

When adding new GRIB-enriched fields, add a spatial interpolation function
here and call it from ``interpolate_all_spatially``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from weatherbrief.models import (
        NWPCloudDiagnostics,
        NWPCloudLayerDiag,
        PressureLevelData,
        RouteCrossSection,
        RoutePoint,
    )

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def interpolate_all_spatially(
    cross_sections: list[RouteCrossSection],
    route_points: list[RoutePoint],
    max_gap_nm: float = 100.0,
) -> None:
    """Run spatial interpolation for all GRIB-enriched fields.

    Called once before sounding analysis.  Fills gaps in both CLW/ICMR
    (per-pressure-level) and cloud diagnostics (per-point scalar).

    Registered SKIP (#462): ``explicit_convective_diagnostics`` (ICON-D2
    explicit-convection) is deliberately NOT interpolated here. Its values are
    already corridor MAXIMA over a route buffer computed at decode — a spatial
    reduction, not a point sample — so interpolating them between route points
    would double-smooth extrema (and linear interpolation of dBZ is invalid
    anyway, dBZ being logarithmic). A point whose corridor decode failed is an
    honest per-hour "unavailable", carried by the payload's completeness flags.
    """
    interpolate_cloud_water_spatially(cross_sections, route_points, max_gap_nm)
    interpolate_diagnostics_spatially(cross_sections, route_points, max_gap_nm)


# ---------------------------------------------------------------------------
# Cloud liquid water / ice mixing ratio (per-pressure-level)
# ---------------------------------------------------------------------------

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

        for hour_idx in range(_max_hourly_len(cs)):
            all_pressures = _collect_pressures(cs, hour_idx, n_points)

            for pressure in all_pressures:
                filled = _interpolate_level(
                    cs, hour_idx, n_points, pressure, distances, max_gap_nm,
                )
                total_filled += filled

    if total_filled > 0:
        logger.info("Spatial interpolation filled %d (point, level) CLW/ICMR gaps", total_filled)

    return total_filled


# ---------------------------------------------------------------------------
# Cloud diagnostics (NWPCloudDiagnostics on HourlyForecast)
# ---------------------------------------------------------------------------

def interpolate_diagnostics_spatially(
    cross_sections: list[RouteCrossSection],
    route_points: list[RoutePoint],
    max_gap_nm: float = 100.0,
) -> int:
    """Fill cloud diagnostics gaps by linear interpolation along the route.

    For each model cross-section and hourly time slot: if a route point has
    ``nwp_cloud_diagnostics=None``, find the nearest left and right neighbors
    with diagnostics and linearly interpolate all numeric sub-fields
    (cover_pct, base_ft, top_ft, ceiling_ft, etc.) in distance-space.

    Returns:
        Total count of points that were filled.
    """
    if not cross_sections or not route_points:
        return 0

    distances = [rp.distance_from_origin_nm for rp in route_points]
    n_points = len(route_points)
    total_filled = 0

    for cs in cross_sections:
        if len(cs.point_forecasts) != n_points:
            continue

        for hour_idx in range(_max_hourly_len(cs)):
            # Classify points as having diagnostics or not
            has_data: list[int] = []
            needs_fill: list[int] = []

            for pt_idx in range(n_points):
                hourly_list = cs.point_forecasts[pt_idx].hourly
                if hour_idx >= len(hourly_list):
                    continue
                if hourly_list[hour_idx].nwp_cloud_diagnostics is not None:
                    has_data.append(pt_idx)
                else:
                    needs_fill.append(pt_idx)

            if not needs_fill or not has_data:
                continue

            for pt_idx in needs_fill:
                left_idx = _find_neighbor(has_data, pt_idx, direction=-1)
                right_idx = _find_neighbor(has_data, pt_idx, direction=+1)

                if left_idx is None or right_idx is None:
                    continue

                gap_nm = distances[right_idx] - distances[left_idx]
                if gap_nm > max_gap_nm:
                    continue

                left_h = cs.point_forecasts[left_idx].hourly[hour_idx]
                right_h = cs.point_forecasts[right_idx].hourly[hour_idx]
                target_h = cs.point_forecasts[pt_idx].hourly[hour_idx]

                left_diag = left_h.nwp_cloud_diagnostics
                right_diag = right_h.nwp_cloud_diagnostics
                if left_diag is None or right_diag is None:
                    continue

                if gap_nm <= 0:
                    frac = 0.5
                else:
                    frac = (distances[pt_idx] - distances[left_idx]) / gap_nm

                # attach_nwp_diagnostics, not a bare assignment: the freezing
                # level is mirrored onto target_h.freezing_level_m, which is
                # the field sounding analysis actually reads for
                # indices.nwp_freezing_level_ft (#485 follow-up).
                target_h.attach_nwp_diagnostics(
                    _lerp_diagnostics(left_diag, right_diag, frac)
                )
                total_filled += 1

    if total_filled > 0:
        logger.info(
            "Spatial interpolation filled %d cloud diagnostics gaps",
            total_filled,
        )

    return total_filled


# ---------------------------------------------------------------------------
# Linear interpolation helpers for NWPCloudDiagnostics
# ---------------------------------------------------------------------------

def _lerp_optional(a: float | None, b: float | None, frac: float) -> float | None:
    """Linearly interpolate two optional floats.  Returns None if both are None."""
    if a is not None and b is not None:
        return a * (1 - frac) + b * frac
    # One-sided: use whichever is available (nearest-neighbor fallback)
    return a if a is not None else b


def _lerp_layer(
    a: NWPCloudLayerDiag, b: NWPCloudLayerDiag, frac: float,
) -> NWPCloudLayerDiag:
    from weatherbrief.models import NWPCloudLayerDiag as Cls
    return Cls(
        cover_pct=_lerp_optional(a.cover_pct, b.cover_pct, frac),
        base_ft=_lerp_optional(a.base_ft, b.base_ft, frac),
        top_ft=_lerp_optional(a.top_ft, b.top_ft, frac),
        top_temp_c=_lerp_optional(a.top_temp_c, b.top_temp_c, frac),
    )


def _lerp_diagnostics(
    a: NWPCloudDiagnostics, b: NWPCloudDiagnostics, frac: float,
) -> NWPCloudDiagnostics:
    """Interpolate every diagnostic between two route points.

    Field coverage comes from ``NWP_CLOUD_DIAG_SCALARS`` rather than a
    hand-written list — this used to drop ``freezing_level_ft`` because the
    list here and the one on the time axis drifted independently (#485). The
    spatial axis makes no averaged-vs-instantaneous distinction: neighbouring
    route points share a valid time, so every scalar interpolates the same way.
    """
    from weatherbrief.models import NWPCloudDiagnostics as Cls
    from weatherbrief.models.analysis import (
        NWP_CLOUD_DIAG_META_FIELDS,
        NWP_CLOUD_DIAG_SCALARS,
    )

    scalars = {
        name: _lerp_optional(getattr(a, name), getattr(b, name), frac)
        for name in NWP_CLOUD_DIAG_SCALARS
    }
    # Meta/capability markers are constant per (model, point) — carried
    # through, never lerped (a boolean through _lerp_optional would come
    # back as a float). Nearest-available wins when one side lacks it.
    meta = {
        name: (
            getattr(a, name) if getattr(a, name) is not None else getattr(b, name)
        )
        for name in NWP_CLOUD_DIAG_META_FIELDS
    }
    return Cls(
        low=_lerp_layer(a.low, b.low, frac),
        mid=_lerp_layer(a.mid, b.mid, frac),
        high=_lerp_layer(a.high, b.high, frac),
        **scalars,
        **meta,
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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
        left_idx = _find_neighbor(has_data, pt_idx, direction=-1)
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
        best = None
        for idx in sorted_indices:
            if idx < target:
                best = idx
            else:
                break
        return best
    else:
        for idx in sorted_indices:
            if idx > target:
                return idx
        return None
