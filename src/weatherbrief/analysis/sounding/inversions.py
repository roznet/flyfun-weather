"""Temperature inversion detection from lapse rate analysis.

Identifies stable layers where temperature increases with altitude (negative
lapse rate). Inversions trap haze/fog and indicate smooth air above.
"""

from __future__ import annotations

from weatherbrief.models import DerivedLevel, InversionLayer


def detect_inversions(derived_levels: list[DerivedLevel]) -> list[InversionLayer]:
    """Detect temperature inversion layers from derived level lapse rates.

    A negative lapse rate at level i means temperature increases from level i
    to level i+1 (the next higher level). We group consecutive such levels and
    build inversions spanning from the first level to the level above the last.

    Args:
        derived_levels: Sorted by descending pressure (surface first).

    Returns:
        List of InversionLayer, ordered from lowest to highest altitude.
    """
    if not derived_levels:
        return []

    # Filter to levels with valid lapse rate and altitude
    valid = [
        lv for lv in derived_levels
        if lv.lapse_rate_c_per_km is not None and lv.altitude_ft is not None
    ]
    if not valid:
        return []

    # Build index mapping valid levels back to all_levels for finding the "next" level
    all_with_alt = [lv for lv in derived_levels if lv.altitude_ft is not None]

    inversions: list[InversionLayer] = []
    current: list[DerivedLevel] = []

    for lv in valid:
        if lv.lapse_rate_c_per_km < 0:
            current.append(lv)
        else:
            if current:
                layer = _build_inversion(current, all_with_alt)
                if layer is not None:
                    inversions.append(layer)
                current = []

    # Handle inversion extending to top of profile
    if current:
        layer = _build_inversion(current, all_with_alt)
        if layer is not None:
            inversions.append(layer)

    return inversions


def _find_next_level(
    last_inv_level: DerivedLevel,
    all_levels: list[DerivedLevel],
) -> DerivedLevel | None:
    """Find the level immediately above (lower pressure) the given level."""
    found = False
    for lv in all_levels:
        if found:
            return lv
        if lv is last_inv_level:
            found = True
    return None


def _build_inversion(
    levels: list[DerivedLevel],
    all_levels: list[DerivedLevel],
) -> InversionLayer | None:
    """Build an InversionLayer from consecutive negative-lapse-rate levels.

    The inversion spans from the first level (base) to the next level above
    the last negative-lapse-rate level (top), since the lapse rate at level i
    describes the layer from i to i+1.
    """
    if not levels:
        return None

    base = levels[0]
    if base.altitude_ft is None:
        return None

    # The top of the inversion is the level above the last negative-lapse-rate level
    next_above = _find_next_level(levels[-1], all_levels)
    if next_above is not None and next_above.altitude_ft is not None:
        top = next_above
    else:
        # Inversion extends to top of profile — use last level in group
        top = levels[-1]

    if top.altitude_ft is None:
        return None

    # Strength: temperature gain through the inversion
    base_temp = base.temperature_c
    top_temp = top.temperature_c
    if base_temp is not None and top_temp is not None:
        strength = top_temp - base_temp
    else:
        strength = 0.0

    # Surface-based if the inversion starts at the first (lowest) level
    surface_based = base is all_levels[0]

    return InversionLayer(
        base_ft=round(base.altitude_ft),
        top_ft=round(top.altitude_ft),
        base_pressure_hpa=base.pressure_hpa,
        top_pressure_hpa=top.pressure_hpa,
        strength_c=round(strength, 1),
        base_temperature_c=base_temp,
        top_temperature_c=top_temp,
        surface_based=surface_based,
    )
