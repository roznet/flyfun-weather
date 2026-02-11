"""Enhanced icing assessment using wet-bulb temperature and cloud awareness.

Uses DerivedLevel wet-bulb data and EnhancedCloudLayer information for
physically-based icing risk that replaces the simple T+RH heuristic.
"""

from __future__ import annotations

from weatherbrief.models import (
    DerivedLevel,
    EnhancedCloudLayer,
    IcingRisk,
    IcingType,
    IcingZone,
)

# Wet-bulb temperature bands for icing type and base severity
_WB_BANDS: list[tuple[float, float, IcingType, IcingRisk]] = [
    # (wb_min, wb_max, type, base_risk)
    (-3.0, 0.0, IcingType.CLEAR, IcingRisk.SEVERE),
    (-10.0, -3.0, IcingType.MIXED, IcingRisk.MODERATE),
    (-15.0, -10.0, IcingType.RIME, IcingRisk.MODERATE),
    (-20.0, -15.0, IcingType.RIME, IcingRisk.LIGHT),
]

# Dewpoint depression threshold — level must be near/in cloud
IN_CLOUD_DD_THRESHOLD = 3.0

# SLD thick-cloud threshold
SLD_THICK_CLOUD_FT = 3000
SLD_WARM_TOP_C = -12.0


def _is_near_cloud(level: DerivedLevel, clouds: list[EnhancedCloudLayer]) -> bool:
    """Check if a level is within or very near a cloud layer."""
    if level.altitude_ft is None:
        return False
    # Within cloud by dewpoint depression
    if level.dewpoint_depression_c is not None and level.dewpoint_depression_c < IN_CLOUD_DD_THRESHOLD:
        return True
    # Within 500ft of a cloud layer boundary
    margin = 500.0
    for cl in clouds:
        if (cl.base_ft - margin) <= level.altitude_ft <= (cl.top_ft + margin):
            return True
    return False


def _classify_icing(wet_bulb_c: float) -> tuple[IcingType, IcingRisk]:
    """Classify icing type and base severity from wet-bulb temperature."""
    for wb_min, wb_max, icing_type, risk in _WB_BANDS:
        if wb_min <= wet_bulb_c < wb_max:
            return icing_type, risk
    return IcingType.NONE, IcingRisk.NONE


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


def _detect_sld(
    clouds: list[EnhancedCloudLayer],
    levels: list[DerivedLevel],
) -> bool:
    """Detect supercooled large droplet (SLD) risk.

    Conditions: thick cloud (>3000ft) with relatively warm tops (>-12C),
    or a warm nose above an icing layer.
    """
    for cl in clouds:
        if cl.thickness_ft is not None and cl.thickness_ft > SLD_THICK_CLOUD_FT:
            if cl.mean_temperature_c is not None and cl.mean_temperature_c > SLD_WARM_TOP_C:
                return True

    # Check for warm nose: temperature inversion above freezing zone
    for i in range(1, len(levels) - 1):
        lv = levels[i]
        if lv.temperature_c is None or lv.wet_bulb_c is None:
            continue
        prev = levels[i - 1]
        if prev.temperature_c is None:
            continue
        # Warm nose: temperature increases with altitude in icing zone
        if lv.temperature_c > prev.temperature_c and -20 < lv.temperature_c < 0:
            return True

    return False


def assess_icing_zones(
    levels: list[DerivedLevel],
    clouds: list[EnhancedCloudLayer],
    precipitable_water_mm: float | None = None,
) -> list[IcingZone]:
    """Assess icing zones from derived levels and cloud information.

    Args:
        levels: Derived levels sorted by descending pressure (surface first).
        clouds: Detected cloud layers.
        precipitable_water_mm: Total column precipitable water (severity modifier).

    Returns:
        List of IcingZone, ordered from lowest to highest altitude.
    """
    if not levels:
        return []

    sld_risk = _detect_sld(clouds, levels)

    # Classify each level
    icing_levels: list[tuple[DerivedLevel, IcingType, IcingRisk]] = []
    for lv in levels:
        if lv.wet_bulb_c is None or lv.altitude_ft is None:
            continue
        if not _is_near_cloud(lv, clouds):
            continue
        icing_type, base_risk = _classify_icing(lv.wet_bulb_c)
        if icing_type == IcingType.NONE:
            continue
        risk = _enhance_severity(base_risk, lv, precipitable_water_mm)
        icing_levels.append((lv, icing_type, risk))

    if not icing_levels:
        return []

    # Group adjacent icing levels into zones
    zones: list[IcingZone] = []
    current: list[tuple[DerivedLevel, IcingType, IcingRisk]] = [icing_levels[0]]

    for item in icing_levels[1:]:
        prev_lv = current[-1][0]
        this_lv = item[0]
        # Adjacent if pressure levels are consecutive (gap < 100hPa)
        if abs(prev_lv.pressure_hpa - this_lv.pressure_hpa) <= 100:
            current.append(item)
        else:
            zones.append(_build_zone(current, sld_risk))
            current = [item]

    zones.append(_build_zone(current, sld_risk))
    return zones


def _build_zone(
    items: list[tuple[DerivedLevel, IcingType, IcingRisk]],
    sld_risk: bool,
) -> IcingZone:
    """Build an IcingZone from a group of adjacent icing levels."""
    levels_in_zone = [lv for lv, _, _ in items]
    types = [t for _, t, _ in items]
    risks = [r for _, _, r in items]

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
    )
