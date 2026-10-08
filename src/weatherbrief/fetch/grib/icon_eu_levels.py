"""Model-level to pressure-level interpolation for ICON-EU data.

ICON-EU provides data on hybrid model levels. Each grid point has its own
pressure profile (from the P variable). This module interpolates QC/QI
values from model levels to standard pressure levels using log-pressure
interpolation.
"""

from __future__ import annotations

import logging
from itertools import compress

import numpy as np

from weatherbrief.fetch.variables import EXTENDED_PRESSURE_LEVELS

logger = logging.getLogger(__name__)

# Target pressure levels for interpolation (hPa).
# Use extended 28-level set for higher vertical resolution in the GA altitude
# band (25 hPa spacing below FL180 vs 50-100 hPa gaps with the 19-level set).
# ICON-EU's 40 model levels provide enough resolution to support this.
TARGET_PRESSURE_LEVELS_HPA = EXTENDED_PRESSURE_LEVELS

# Physical bounds per interpolated field, applied after interpolation.
# A field absent from this map is SIGNED and must not be clamped.
#
# Temperature and the wind components U/V/W are signed: clamping a negative
# U or V corrupts wind speed AND direction, and clamping negative (downward)
# W destroys subsidence — which then zeroes the omega derived from it. Only
# genuinely non-negative quantities (condensate mixing ratios, specific
# humidity) and the bounded cloud-fraction percentage are clamped here.
# See issue #441 finding #1.
_FIELD_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "cloud_liquid_water_kg_kg": (0.0, None),
    "ice_mixing_ratio_kg_kg": (0.0, None),
    # Precipitating hydrometeors (#530) — mixing ratios, non-negative like the
    # cloud species. A log-pressure interpolation across a sharp precipitation
    # edge can undershoot below zero otherwise.
    "rain_water_kg_kg": (0.0, None),
    "snow_water_kg_kg": (0.0, None),
    "graupel_water_kg_kg": (0.0, None),
    "raw_specific_humidity_kg_kg": (0.0, None),
    "cloud_area_fraction_pct": (0.0, 100.0),
}


def bounds_for_field(field_key: str) -> tuple[float | None, float | None] | None:
    """Return (min, max) physical bounds for an interpolated ICON field.

    Returns None for signed fields (temperature, wind components) that must
    not be clamped.
    """
    return _FIELD_BOUNDS.get(field_key)


def interpolate_model_to_pressure_levels(
    model_pressures_pa: list[float],
    model_values: list[float],
    target_pressures_hpa: list[int] | None = None,
    bounds: tuple[float | None, float | None] | None = None,
) -> dict[int, float]:
    """Interpolate a variable from model levels to standard pressure levels.

    Uses log-pressure interpolation (linear in ln(p)), which is the standard
    approach for atmospheric vertical interpolation.

    Args:
        model_pressures_pa: Pressure at each model level in Pa, one per level.
            Must correspond 1:1 with model_values.
        model_values: Variable values at each model level.
        target_pressures_hpa: Target pressure levels in hPa.
            Defaults to ICON_PRESSURE_LEVELS.
        bounds: Optional ``(min, max)`` clamp applied to each interpolated
            value; either side may be None to leave it unbounded. Defaults to
            None (no clamping) so signed fields (T, U, V, W) pass through
            unchanged. Use :func:`bounds_for_field` to pick per-field bounds.

    Returns:
        Dict mapping pressure_hpa to interpolated value. Levels outside
        the model pressure range are excluded.
    """
    if target_pressures_hpa is None:
        target_pressures_hpa = TARGET_PRESSURE_LEVELS_HPA

    if len(model_pressures_pa) != len(model_values):
        logger.warning(
            "Mismatched lengths: %d pressures vs %d values",
            len(model_pressures_pa), len(model_values),
        )
        return {}

    if len(model_pressures_pa) < 2:
        return {}

    # Convert to numpy arrays
    p_pa = np.array(model_pressures_pa, dtype=np.float64)
    vals = np.array(model_values, dtype=np.float64)

    # Filter out NaN or non-positive pressures
    valid = np.isfinite(p_pa) & (p_pa > 0) & np.isfinite(vals)
    if valid.sum() < 2:
        return {}
    p_pa = p_pa[valid]
    vals = vals[valid]

    # Sort by pressure (ascending — lower pressure = higher altitude)
    sort_idx = np.argsort(p_pa)
    p_pa = p_pa[sort_idx]
    vals = vals[sort_idx]

    # Log-pressure coordinates
    ln_p = np.log(p_pa)

    # Pressure range of model data
    p_min_pa = p_pa[0]
    p_max_pa = p_pa[-1]

    lo, hi = (None, None) if bounds is None else bounds

    result: dict[int, float] = {}
    for target_hpa in target_pressures_hpa:
        target_pa = target_hpa * 100.0
        # Skip if outside model range (no extrapolation)
        if target_pa < p_min_pa or target_pa > p_max_pa:
            continue
        ln_target = np.log(target_pa)
        interp_val = float(np.interp(ln_target, ln_p, vals))
        if lo is not None and interp_val < lo:
            interp_val = lo
        if hi is not None and interp_val > hi:
            interp_val = hi
        result[target_hpa] = interp_val

    return result


def interpolate_columns_to_pressure_levels(
    model_pressures_pa: np.ndarray,
    model_values: np.ndarray,
    target_pressures_hpa: list[int] | None = None,
    bounds: tuple[float | None, float | None] | None = None,
) -> list[dict[int, float]]:
    """Batched :func:`interpolate_model_to_pressure_levels`, one column per row.

    ``model_pressures_pa`` / ``model_values`` are ``(n_points, n_levels)``
    float64 arrays in model-level order, NaN where a level is missing. Row
    ``i`` of the result is exactly what the scalar function returns for that
    column with the NaNs dropped — same keys, same key order, same Python
    floats, bit for bit (#704).

    The filter, sort and log run once over the whole matrix; only the final
    ``np.interp`` stays per column, called with all of the column's in-range
    targets at once. It is not re-derived in numpy arithmetic on purpose:
    numpy's compiled interp fuses ``slope*dx + y0`` into an FMA on arm64 and
    not on x86, so no single numpy expression matches it on both. A column
    whose valid pressures repeat goes through the scalar function, because
    the order a sort leaves equal pressures in is not guaranteed to match.
    """
    if target_pressures_hpa is None:
        target_pressures_hpa = TARGET_PRESSURE_LEVELS_HPA

    p_pa = np.asarray(model_pressures_pa, dtype=np.float64)
    vals = np.asarray(model_values, dtype=np.float64)
    n_points = p_pa.shape[0]
    results: list[dict[int, float]] = [{} for _ in range(n_points)]
    if n_points == 0 or p_pa.shape[1] < 2 or not target_pressures_hpa:
        return results

    valid = np.isfinite(p_pa) & (p_pa > 0) & np.isfinite(vals)
    n_valid = valid.sum(axis=1)
    # Invalid cells sort last (+inf) and are never read: each row is sliced
    # to its first n_valid entries below.
    key = np.where(valid, p_pa, np.inf)
    order = np.argsort(key, axis=1, kind="stable")
    p_sorted = np.take_along_axis(key, order, axis=1)
    v_sorted = np.take_along_axis(np.where(valid, vals, 0.0), order, axis=1)
    ln_p = np.log(p_sorted)
    repeats = p_sorted[:, 1:] == p_sorted[:, :-1]

    # Per-target scalars, computed exactly as the scalar function does, then
    # put in pressure order so each column's in-range targets are one slice.
    targets_pa = np.array([t * 100.0 for t in target_pressures_hpa], dtype=np.float64)
    ln_targets = np.array(
        [np.log(t * 100.0) for t in target_pressures_hpa], dtype=np.float64,
    )
    t_order = np.argsort(targets_pa, kind="stable")
    t_sorted = targets_pa[t_order]
    ln_t_sorted = ln_targets[t_order]

    rows = np.flatnonzero(n_valid >= 2)
    n_row = n_valid[rows]
    # Targets outside the column's [p_min, p_max] are skipped (no extrapolation).
    first = np.searchsorted(t_sorted, p_sorted[rows, 0], side="left")
    stop = np.searchsorted(t_sorted, p_sorted[rows, n_row - 1], side="right")
    t_idx = np.arange(t_sorted.size)
    in_range = (t_idx[None, :] >= first[:, None]) & (t_idx[None, :] < stop[:, None])
    interp = np.zeros((rows.size, t_sorted.size), dtype=np.float64)

    scalar_rows: list[int] = []
    for i, (r, k, a, b) in enumerate(
        zip(rows.tolist(), n_row.tolist(), first.tolist(), stop.tolist()),
    ):
        if repeats[r, : k - 1].any():
            scalar_rows.append(i)
            continue
        if a >= b:
            continue
        xp = ln_p[r, :k]
        fp = v_sorted[r, :k]
        if b - a < k:
            interp[i, a:b] = np.interp(ln_t_sorted[a:b], xp, fp)
        else:
            # With at least as many targets as levels np.interp may take its
            # precomputed-slope path, which need not round like the scalar
            # call; keep to the scalar call for this (sparse) column.
            interp[i, a:b] = [np.interp(x, xp, fp) for x in ln_t_sorted[a:b]]

    lo, hi = (None, None) if bounds is None else bounds
    if lo is not None:
        interp = np.where(interp < lo, lo, interp)
    if hi is not None:
        interp = np.where(interp > hi, hi, interp)

    # Back to the caller's target order for the dict keys.
    t_back = np.empty_like(t_order)
    t_back[t_order] = np.arange(t_order.size)
    in_range_rows = in_range[:, t_back].tolist()
    interp_rows = interp[:, t_back].tolist()
    for i, r in enumerate(rows.tolist()):
        results[r] = dict(
            compress(zip(target_pressures_hpa, interp_rows[i]), in_range_rows[i]),
        )
    for i in scalar_rows:
        r = int(rows[i])
        results[r] = interpolate_model_to_pressure_levels(
            p_pa[r, valid[r]].tolist(), vals[r, valid[r]].tolist(),
            target_pressures_hpa, bounds,
        )

    return results
