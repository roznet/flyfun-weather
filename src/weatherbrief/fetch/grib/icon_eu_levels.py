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


def interpolate_model_to_pressure_levels(
    model_pressures_pa: list[float],
    model_values: list[float],
    target_pressures_hpa: list[int] | None = None,
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

    result: dict[int, float] = {}
    for target_hpa in target_pressures_hpa:
        target_pa = target_hpa * 100.0
        # Skip if outside model range (no extrapolation)
        if target_pa < p_min_pa or target_pa > p_max_pa:
            continue
        ln_target = np.log(target_pa)
        interp_val = float(np.interp(ln_target, ln_p, vals))
        # Cloud water values should not be negative
        result[target_hpa] = max(0.0, interp_val)

    return result
