"""Enhanced icing assessment using Ogimet continuous icing index.

Uses a physically-based icing index that peaks at −7°C (matching observed
supercooled liquid water distribution) with separate stratiform and convective
components blended by CAPE. Replaces the previous wet-bulb band classification.
"""

from __future__ import annotations

import math

from weatherbrief.models import (
    DerivedLevel,
    EnhancedCloudLayer,
    IcingRisk,
    IcingType,
    IcingZone,
)

# Dewpoint depression threshold — level must be near/in cloud
IN_CLOUD_DD_THRESHOLD = 3.0

# SLD thick-cloud threshold (kept for reference, currently disabled)
SLD_THICK_CLOUD_FT = 3000
SLD_WARM_TOP_C = -12.0

# Ogimet icing index severity thresholds
_INDEX_MODERATE = 30.0
_INDEX_SEVERE = 80.0

# Water vapor: Rv = 461.5 J/(kg·K), reference ρv at 20°C saturation ≈ 17.3 g/m³
_RV = 461.5
_RHO_V_20SAT = 17.3e-3  # kg/m³


# --- Ogimet icing index functions ---


def _compute_layered_index(temperature_c: float) -> float:
    """Ogimet layered (stratiform) icing index. Parabola peaking at −7°C."""
    t = temperature_c
    if not (-14.0 <= t <= 0.0):
        return 0.0
    return 100.0 * (-t) * (t + 14.0) / 49.0


def _compute_convective_index(
    temperature_c: float,
    vapor_density: float,
    vapor_density_base: float,
) -> float:
    """Ogimet convective icing index.

    Args:
        temperature_c: Temperature at the level.
        vapor_density: Water vapor density at this level (kg/m³).
        vapor_density_base: Water vapor density at cloud base (kg/m³).
    """
    t_k = temperature_c + 273.15
    if not (-20.0 <= temperature_c <= 0.0) or t_k <= 253.15:
        return 0.0
    moisture_term = (vapor_density_base - vapor_density) / _RHO_V_20SAT
    if moisture_term <= 0:
        return 0.0
    temp_term = (t_k - 253.15) / 20.0
    return 200.0 * moisture_term * math.sqrt(temp_term)


def _cape_to_cloud_split(cape_jkg: float | None) -> tuple[float, float]:
    """Map CAPE to layered/convective fraction."""
    if cape_jkg is None or cape_jkg < 100:
        return 1.0, 0.0
    if cape_jkg < 500:
        return 0.8, 0.2
    if cape_jkg < 1500:
        return 0.5, 0.5
    return 0.2, 0.8


def _vapor_density(dewpoint_c: float) -> float:
    """Compute water vapor density from dewpoint using Magnus + ideal gas.

    e_sat(Td) via Magnus formula, then ρv = e_sat / (Rv × T_K).
    """
    td = dewpoint_c
    t_k = td + 273.15
    # Magnus formula: e_sat in hPa
    e_sat_hpa = 6.112 * math.exp(17.67 * td / (td + 243.5))
    e_sat_pa = e_sat_hpa * 100.0
    return e_sat_pa / (_RV * t_k)


def _compute_icing_index(
    temperature_c: float,
    dewpoint_c: float,
    layered_frac: float,
    convective_frac: float,
    vapor_density_base: float,
) -> float:
    """Combined Ogimet icing index (0–100 scale)."""
    rho_v = _vapor_density(dewpoint_c)

    layered = _compute_layered_index(temperature_c)
    convective = _compute_convective_index(
        temperature_c, rho_v, vapor_density_base,
    )
    # Blend and normalize to 0–100
    raw = layered_frac * layered + convective_frac * convective
    return min(max(raw / 2.0, 0.0), 100.0)


def _classify_icing_type(temperature_c: float) -> IcingType:
    """Classify icing type from temperature (physical, independent of severity)."""
    if -3.0 <= temperature_c <= 0.0:
        return IcingType.CLEAR
    if -10.0 <= temperature_c < -3.0:
        return IcingType.MIXED
    if temperature_c < -10.0:
        return IcingType.RIME
    return IcingType.NONE


def _index_to_risk(index: float) -> IcingRisk:
    """Map continuous icing index to risk level."""
    if index >= _INDEX_SEVERE:
        return IcingRisk.SEVERE
    if index >= _INDEX_MODERATE:
        return IcingRisk.MODERATE
    if index > 0:
        return IcingRisk.LIGHT
    return IcingRisk.NONE


# --- Cloud proximity check ---


def _is_near_cloud(level: DerivedLevel, clouds: list[EnhancedCloudLayer]) -> bool:
    """Check if a level is within or very near a cloud layer."""
    if level.altitude_ft is None:
        return False
    if level.dewpoint_depression_c is not None and level.dewpoint_depression_c < IN_CLOUD_DD_THRESHOLD:
        return True
    margin = 500.0
    for cl in clouds:
        if (cl.base_ft - margin) <= level.altitude_ft <= (cl.top_ft + margin):
            return True
    return False


# --- Severity modifiers (secondary adjustment on top of Ogimet index) ---


def _enhance_severity(
    base_risk: IcingRisk,
    level: DerivedLevel,
    precipitable_water_mm: float | None,
) -> IcingRisk:
    """Potentially upgrade severity based on moisture indicators."""
    if base_risk == IcingRisk.NONE:
        return IcingRisk.NONE
    rh = level.relative_humidity_pct
    if rh is not None and rh > 95:
        if base_risk == IcingRisk.MODERATE:
            return IcingRisk.SEVERE
        if base_risk == IcingRisk.LIGHT:
            return IcingRisk.MODERATE
    if precipitable_water_mm is not None and precipitable_water_mm > 25:
        if base_risk == IcingRisk.LIGHT:
            return IcingRisk.MODERATE
    return base_risk


# --- SLD detection (disabled) ---


def _detect_sld(
    clouds: list[EnhancedCloudLayer],
    levels: list[DerivedLevel],
) -> bool:
    """Detect supercooled large droplet (SLD) risk.

    Currently disabled — returns False unconditionally.
    """
    return False


# --- Cloud-base vapor density ---


def _cloud_base_vapor_density(
    clouds: list[EnhancedCloudLayer],
    levels: list[DerivedLevel],
) -> float:
    """Get water vapor density at the lowest cloud base.

    Falls back to surface level if no cloud-base level match.
    """
    if clouds:
        # Find the level closest to the lowest cloud base
        target_ft = min(cl.base_ft for cl in clouds)
        best: DerivedLevel | None = None
        best_dist = float("inf")
        for lv in levels:
            if lv.altitude_ft is not None and lv.dewpoint_c is not None:
                dist = abs(lv.altitude_ft - target_ft)
                if dist < best_dist:
                    best_dist = dist
                    best = lv
        if best is not None and best.dewpoint_c is not None:
            return _vapor_density(best.dewpoint_c)

    # Fallback: use lowest level with valid dewpoint
    for lv in levels:
        if lv.dewpoint_c is not None:
            return _vapor_density(lv.dewpoint_c)

    return _RHO_V_20SAT  # safe fallback


# --- Main assessment ---


def assess_icing_zones(
    levels: list[DerivedLevel],
    clouds: list[EnhancedCloudLayer],
    precipitable_water_mm: float | None = None,
    cape_jkg: float | None = None,
) -> list[IcingZone]:
    """Assess icing zones using Ogimet continuous icing index.

    Args:
        levels: Derived levels sorted by descending pressure (surface first).
        clouds: Detected cloud layers.
        precipitable_water_mm: Total column precipitable water (severity modifier).
        cape_jkg: Surface-based CAPE for layered/convective split.

    Returns:
        List of IcingZone, ordered from lowest to highest altitude.
    """
    if not levels:
        return []

    sld_risk = _detect_sld(clouds, levels)
    layered_frac, convective_frac = _cape_to_cloud_split(cape_jkg)
    vd_base = _cloud_base_vapor_density(clouds, levels)

    # Compute icing index for each level and classify
    icing_levels: list[tuple[DerivedLevel, IcingType, IcingRisk, float]] = []
    for lv in levels:
        if lv.temperature_c is None or lv.altitude_ft is None:
            continue
        if lv.dewpoint_c is None:
            continue
        if not _is_near_cloud(lv, clouds):
            continue

        # Compute and store Ogimet index on the level
        index = _compute_icing_index(
            lv.temperature_c, lv.dewpoint_c,
            layered_frac, convective_frac, vd_base,
        )
        lv.icing_index = round(index, 1)

        if index <= 0:
            continue

        icing_type = _classify_icing_type(lv.temperature_c)
        if icing_type == IcingType.NONE:
            continue

        base_risk = _index_to_risk(index)
        risk = _enhance_severity(base_risk, lv, precipitable_water_mm)
        icing_levels.append((lv, icing_type, risk, index))

    if not icing_levels:
        return []

    # Group adjacent icing levels into zones
    zones: list[IcingZone] = []
    current: list[tuple[DerivedLevel, IcingType, IcingRisk, float]] = [icing_levels[0]]

    for item in icing_levels[1:]:
        prev_lv = current[-1][0]
        this_lv = item[0]
        if abs(prev_lv.pressure_hpa - this_lv.pressure_hpa) <= 100:
            current.append(item)
        else:
            zones.append(_build_zone(current, sld_risk))
            current = [item]

    zones.append(_build_zone(current, sld_risk))
    return zones


def _build_zone(
    items: list[tuple[DerivedLevel, IcingType, IcingRisk, float]],
    sld_risk: bool,
) -> IcingZone:
    """Build an IcingZone from a group of adjacent icing levels."""
    levels_in_zone = [lv for lv, _, _, _ in items]
    types = [t for _, t, _, _ in items]
    risks = [r for _, _, r, _ in items]
    indices = [idx for _, _, _, idx in items]

    base = levels_in_zone[0]
    top = levels_in_zone[-1]

    # Worst risk in zone
    risk_order = [IcingRisk.NONE, IcingRisk.LIGHT, IcingRisk.MODERATE, IcingRisk.SEVERE]
    worst_risk = max(risks, key=lambda r: risk_order.index(r))

    # Dominant icing type
    type_counts: dict[IcingType, int] = {}
    for t in types:
        type_counts[t] = type_counts.get(t, 0) + 1
    dominant_type = max(type_counts, key=type_counts.get)

    # Mean values
    t_vals = [lv.temperature_c for lv in levels_in_zone if lv.temperature_c is not None]
    wb_vals = [lv.wet_bulb_c for lv in levels_in_zone if lv.wet_bulb_c is not None]

    return IcingZone(
        base_ft=round(base.altitude_ft),
        top_ft=round(top.altitude_ft),
        base_pressure_hpa=base.pressure_hpa,
        top_pressure_hpa=top.pressure_hpa,
        risk=worst_risk,
        icing_type=dominant_type,
        sld_risk=sld_risk,
        mean_temperature_c=round(sum(t_vals) / len(t_vals), 1) if t_vals else None,
        mean_wet_bulb_c=round(sum(wb_vals) / len(wb_vals), 1) if wb_vals else None,
        mean_icing_index=round(sum(indices) / len(indices), 1) if indices else None,
    )
