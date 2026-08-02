"""Model-level to pressure-level interpolation for ICON-EU data.

ICON-EU provides data on hybrid model levels. Each grid point has its own
pressure profile (from the P variable). This module interpolates QC/QI
values from model levels to standard pressure levels using log-pressure
interpolation.
"""

from __future__ import annotations

import logging

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
