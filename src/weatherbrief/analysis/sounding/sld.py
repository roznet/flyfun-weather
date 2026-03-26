"""Supercooled Large Droplet (SLD) detection from cloud microphysics.

Detects SLD risk from CLMR (cloud liquid water mixing ratio) and ICMR
(ice crystal mixing ratio) fields when available (GFS only currently).
The algorithm is model-agnostic — any model providing CLMR/ICMR will
produce SLD zones automatically.
"""

from __future__ import annotations

from weatherbrief.analysis.sounding.icing_common import (
    MIN_ZONE_HALF_THICKNESS_FT,
    ZONE_MAX_PRESSURE_GAP_HPA,
    classify_icing_type,
)
from weatherbrief.models import DerivedLevel, IcingRisk, IcingType, SldZone

# CLMR threshold — minimum cloud liquid water for SLD consideration (g/kg)
_CLMR_THRESHOLD_G_KG = 1e-3  # = 1e-6 kg/kg

# Liquid ratio threshold — SLD requires liquid-dominant cloud
_LIQUID_RATIO_THRESHOLD = 0.5

# Intensity normalisation — CLMR value at which intensity saturates (kg/kg)
_CLMR_SATURATION_KG_KG = 5e-4

# Risk thresholds on computed intensity (0–1)
_INTENSITY_MODERATE = 0.3
_INTENSITY_SEVERE = 0.7


def _intensity_to_risk(intensity: float) -> IcingRisk:
    if intensity >= _INTENSITY_SEVERE:
        return IcingRisk.SEVERE
    if intensity >= _INTENSITY_MODERATE:
        return IcingRisk.MODERATE
    if intensity > 0:
        return IcingRisk.LIGHT
    return IcingRisk.NONE


def assess_sld_zones(levels: list[DerivedLevel]) -> list[SldZone]:
    """Detect SLD zones from CLMR/ICMR microphysics data.

    For each level in the icing temperature range [-20°C, 0°C]:
    1. Requires CLMR > threshold (measurable supercooled liquid)
    2. Requires liquid ratio CLMR/(CLMR+ICMR) > 0.5 (liquid-dominant)
    3. Computes intensity from liquid ratio and CLMR magnitude

    Returns empty list when microphysics data is unavailable.
    """
    sld_levels: list[tuple[DerivedLevel, float, float]] = []

    for lv in levels:
        if lv.temperature_c is None or lv.altitude_ft is None:
            continue
        if not (-20.0 <= lv.temperature_c <= 0.0):
            continue

        clw = lv.cloud_liquid_water_g_kg
        if clw is None or clw <= _CLMR_THRESHOLD_G_KG:
            continue

        icmr = lv.ice_mixing_ratio_g_kg or 0.0
        total = clw + icmr
        if total <= 0:
            continue

        liquid_ratio = clw / total
        if liquid_ratio <= _LIQUID_RATIO_THRESHOLD:
            continue

        clw_kg_kg = clw / 1000.0
        intensity = liquid_ratio * min(clw_kg_kg / _CLMR_SATURATION_KG_KG, 1.0)

        risk = _intensity_to_risk(intensity)
        if risk == IcingRisk.NONE:
            continue

        sld_levels.append((lv, intensity, liquid_ratio))

    return _group_sld_levels(sld_levels)


def _group_sld_levels(
    sld_levels: list[tuple[DerivedLevel, float, float]],
) -> list[SldZone]:
    """Group adjacent SLD levels into zones."""
    if not sld_levels:
        return []

    zones: list[SldZone] = []
    current: list[tuple[DerivedLevel, float, float]] = [sld_levels[0]]

    for item in sld_levels[1:]:
        prev_lv = current[-1][0]
        this_lv = item[0]
        if abs(prev_lv.pressure_hpa - this_lv.pressure_hpa) <= ZONE_MAX_PRESSURE_GAP_HPA:
            current.append(item)
        else:
            zones.append(_build_sld_zone(current))
            current = [item]

    zones.append(_build_sld_zone(current))
    return zones


def _build_sld_zone(
    items: list[tuple[DerivedLevel, float, float]],
) -> SldZone:
    """Build an SldZone from grouped levels."""
    levels = [it[0] for it in items]
    intensities = [it[1] for it in items]
    liquid_ratios = [it[2] for it in items]

    altitudes = [lv.altitude_ft for lv in levels if lv.altitude_ft is not None]
    pressures = [lv.pressure_hpa for lv in levels]
    temperatures = [lv.temperature_c for lv in levels if lv.temperature_c is not None]

    base_ft = min(altitudes)
    top_ft = max(altitudes)

    # Expand thin zones for cross-section visibility
    if top_ft - base_ft < 2 * MIN_ZONE_HALF_THICKNESS_FT:
        mid = (top_ft + base_ft) / 2
        base_ft = mid - MIN_ZONE_HALF_THICKNESS_FT
        top_ft = mid + MIN_ZONE_HALF_THICKNESS_FT

    mean_intensity = sum(intensities) / len(intensities)
    mean_liquid_ratio = sum(liquid_ratios) / len(liquid_ratios)
    mean_temp = sum(temperatures) / len(temperatures) if temperatures else None

    risk = _intensity_to_risk(mean_intensity)

    icing_type = IcingType.NONE
    if temperatures:
        icing_type = classify_icing_type(sum(temperatures) / len(temperatures))

    return SldZone(
        base_ft=base_ft,
        top_ft=top_ft,
        base_pressure_hpa=max(pressures),
        top_pressure_hpa=min(pressures),
        risk=risk,
        icing_type=icing_type,
        mean_intensity=round(mean_intensity, 3),
        mean_liquid_ratio=round(mean_liquid_ratio, 3),
        mean_temperature_c=round(mean_temp, 1) if mean_temp is not None else None,
    )
