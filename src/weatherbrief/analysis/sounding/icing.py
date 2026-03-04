"""Enhanced icing assessment using Ogimet continuous icing index.

Uses a physically-based icing index that peaks at −7°C (matching observed
supercooled liquid water distribution) with separate stratiform and convective
components blended by CAPE. Replaces the previous wet-bulb band classification.
"""

from __future__ import annotations

import math

from weatherbrief.models import (
    CloudCoverage,
    DerivedLevel,
    EnhancedCloudLayer,
    IcingRisk,
    IcingType,
    IcingZone,
    NWPCloudDiagnostics,
)

# Dewpoint depression threshold — level must be near/in cloud
IN_CLOUD_DD_THRESHOLD = 3.0

# --- DD attenuation ---

_DD_ATTENUATION_CUTOFF = 2.0  # DD above this → factor = 0


def _dd_attenuation_factor(dewpoint_depression_c: float) -> float:
    """Cosine taper: 1.0 at DD=0, 0.0 at DD>=cutoff."""
    if dewpoint_depression_c <= 0:
        return 1.0
    if dewpoint_depression_c >= _DD_ATTENUATION_CUTOFF:
        return 0.0
    return 0.5 * (1.0 + math.cos(math.pi * dewpoint_depression_c / _DD_ATTENUATION_CUTOFF))

# SLD thick-cloud threshold (kept for reference, currently disabled)
SLD_THICK_CLOUD_FT = 3000
SLD_WARM_TOP_C = -12.0

# Minimum half-thickness for single-level icing zones (matches GFS vertical
# resolution ~25 hPa ≈ 750–1000 ft and the _is_near_cloud() margin).
_MIN_ZONE_HALF_THICKNESS_FT = 500

# Ogimet icing index severity thresholds
_INDEX_LIGHT = 10.0
_INDEX_MODERATE = 30.0
_INDEX_SEVERE = 80.0

# LWC-based icing severity thresholds (g/m³) — aviation meteorology literature
_LWC_LIGHT = 0.0       # Any measurable LWC with icing-range temp
_LWC_MODERATE = 0.1     # g/m³
_LWC_SEVERE = 0.6       # g/m³

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


def _classify_icing_type(temperature_c: float, wet_bulb_c: float | None = None) -> IcingType:
    """Classify icing type from wet-bulb temperature (or dry-bulb fallback).

    The accretion surface temperature is closer to wet-bulb than dry-bulb
    in cloud/precipitating conditions.  Wet-bulb is always <= dry-bulb,
    so thresholds are shifted down by ~1°C to compensate.
    """
    t = wet_bulb_c if wet_bulb_c is not None else temperature_c
    if -4.0 <= t <= 0.0:
        return IcingType.CLEAR
    if -11.0 <= t < -4.0:
        return IcingType.MIXED
    if t < -11.0:
        return IcingType.RIME
    return IcingType.NONE


def _index_to_risk(index: float) -> IcingRisk:
    """Map continuous icing index to risk level."""
    if index >= _INDEX_SEVERE:
        return IcingRisk.SEVERE
    if index >= _INDEX_MODERATE:
        return IcingRisk.MODERATE
    if index >= _INDEX_LIGHT:
        return IcingRisk.LIGHT
    return IcingRisk.NONE


def _lwc_to_icing_severity(lwc_g_m3: float) -> IcingRisk:
    """Map cloud liquid water content (g/m³) to icing severity.

    Based on aviation meteorology literature thresholds.
    """
    if lwc_g_m3 >= _LWC_SEVERE:
        return IcingRisk.SEVERE
    if lwc_g_m3 >= _LWC_MODERATE:
        return IcingRisk.MODERATE
    if lwc_g_m3 > _LWC_LIGHT:
        return IcingRisk.LIGHT
    return IcingRisk.NONE


# --- Cloud proximity check ---


# Dewpoint depression below which a level is unconditionally in-cloud
# (BKN-equivalent moisture, DD < 2°C).  DD 2–3°C corresponds to SCT
# coverage — avoidable in VMC — so those levels only count when a
# BKN/OVC cloud layer is nearby.
_IN_CLOUD_DD_BKN = 2.0


def _is_near_cloud(level: DerivedLevel, clouds: list[EnhancedCloudLayer]) -> bool:
    """Check if a level is in or near unavoidable (BKN/OVC) cloud.

    Returns True when:
    * The level's own DD < 2°C  (broken/overcast moisture — definitely in cloud).
    * The level is within 500 ft of a BKN or OVC cloud layer.

    Scattered clouds (DD 2-3°C, coverage=SCT) are flyable in VMC — pilots
    can see and avoid them — so they do not gate icing assessment.
    """
    if level.altitude_ft is None:
        return False
    if level.dewpoint_depression_c is not None and level.dewpoint_depression_c < _IN_CLOUD_DD_BKN:
        return True
    margin = 500.0
    for cl in clouds:
        if cl.coverage == CloudCoverage.SCT:
            continue
        if (cl.base_ft - margin) <= level.altitude_ft <= (cl.top_ft + margin):
            return True
    return False


# --- Severity modifiers (secondary adjustment on top of Ogimet index) ---


_ENHANCE_MIN_HIGH_RH_LEVELS = 3  # require deep saturation (multiple levels)
_ENHANCE_RH_THRESHOLD = 95.0
_ENHANCE_MIN_NWP_CLOUD_PCT = 50.0  # NWP must strongly corroborate
_ENHANCE_SEVERE_MAX_TEMP_C = -5.0  # warm icing stays MODERATE at most


def _enhance_severity(
    base_risk: IcingRisk,
    levels_in_zone: list[DerivedLevel],
    precipitable_water_mm: float | None,
    nwp_cloud_pct: float | None = None,
) -> IcingRisk:
    """Potentially upgrade severity based on zone-level moisture indicators.

    RH-based upgrade requires corroboration:
      - At least 3 levels in the zone with RH > 95% (deep saturation)
      - NWP cloud cover >= 50% in the corresponding altitude band
    The MODERATE → SEVERE upgrade additionally requires the zone to be cold
    enough (mean temp ≤ -5°C) — warm icing near the freezing level is
    unlikely to be truly severe regardless of moisture signals.
    """
    if base_risk == IcingRisk.NONE:
        return IcingRisk.NONE

    high_rh_count = sum(
        1 for lv in levels_in_zone
        if lv.relative_humidity_pct is not None
        and lv.relative_humidity_pct > _ENHANCE_RH_THRESHOLD
    )
    nwp_confirms = (
        nwp_cloud_pct is not None
        and nwp_cloud_pct >= _ENHANCE_MIN_NWP_CLOUD_PCT
    )

    if high_rh_count >= _ENHANCE_MIN_HIGH_RH_LEVELS and nwp_confirms:
        if base_risk == IcingRisk.MODERATE:
            # Only upgrade to SEVERE in cold icing — warm layers near
            # the freezing level are manageable even when saturated
            t_vals = [
                lv.temperature_c
                for lv in levels_in_zone
                if lv.temperature_c is not None
            ]
            mean_t = sum(t_vals) / len(t_vals) if t_vals else 0.0
            if mean_t <= _ENHANCE_SEVERE_MAX_TEMP_C:
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


_NWP_CLOUD_ALTITUDE_MARGIN_FT = 500.0


def _nwp_cloud_for_altitude(
    altitude_ft: float,
    nwp_cloud_low_pct: float | None,
    nwp_cloud_mid_pct: float | None,
    nwp_cloud_high_pct: float | None = None,
    nwp_cloud_diagnostics: NWPCloudDiagnostics | None = None,
) -> float | None:
    """Return the NWP cloud cover percentage for a given altitude.

    When *nwp_cloud_diagnostics* is provided, checks **all** diagnostic
    layers (low/mid/high) and returns the highest cloud cover for any layer
    whose base/top range (with margin) contains the altitude.  This handles
    NWP cloud layers that extend beyond ICAO band boundaries — e.g. a low
    cloud layer topping at 11,000 ft is correctly detected at 8,000 ft even
    though 8,000 ft falls in the ICAO "mid" band.

    When diagnostics are ``None`` or no layer has base/top, fall back to
    the existing bulk percentage behavior (backward compatible).
    """
    margin = _NWP_CLOUD_ALTITUDE_MARGIN_FT

    # When diagnostics are available, check all layers regardless of ICAO band
    if nwp_cloud_diagnostics is not None:
        candidates: list[tuple[float | None, object | None]] = [
            (nwp_cloud_low_pct, nwp_cloud_diagnostics.low),
            (nwp_cloud_mid_pct, nwp_cloud_diagnostics.mid),
            (nwp_cloud_high_pct, nwp_cloud_diagnostics.high),
        ]
        best: float | None = None
        any_diag = False
        for bulk_pct, diag_layer in candidates:
            if diag_layer is None or diag_layer.base_ft is None or diag_layer.top_ft is None:
                continue
            any_diag = True
            if (diag_layer.base_ft - margin) <= altitude_ft <= (diag_layer.top_ft + margin):
                pct = bulk_pct or 0.0
                if best is None or pct > best:
                    best = pct
        if any_diag:
            return best if best is not None else 0.0

    # No diagnostics available — fall back to bulk ICAO band percentages
    if altitude_ft < 6500:
        return nwp_cloud_low_pct
    elif altitude_ft < 20000:
        return nwp_cloud_mid_pct
    else:
        return nwp_cloud_high_pct


# NWP cloud cover threshold for the fallback icing pass — when the NWP model
# reports cloud cover above this in an icing-temperature band, assess icing
# even if the sounding didn't detect cloud proximity at that level.
_NWP_CLOUD_ICING_THRESHOLD = 50.0


def assess_icing_zones(
    levels: list[DerivedLevel],
    clouds: list[EnhancedCloudLayer],
    precipitable_water_mm: float | None = None,
    cape_jkg: float | None = None,
    nwp_cloud_low_pct: float | None = None,
    nwp_cloud_mid_pct: float | None = None,
    nwp_cloud_high_pct: float | None = None,
    severity_enhance: bool = True,
    nwp_cloud_diagnostics: NWPCloudDiagnostics | None = None,
) -> list[IcingZone]:
    """Assess icing zones using Ogimet continuous icing index.

    Two-pass approach:
      1. Standard pass: LWC-direct or cloud-proximity-gated Ogimet index.
      2. NWP fallback pass: for levels in the 0 to −20°C band that were
         skipped in pass 1, if NWP cloud cover > 50% at the corresponding
         altitude band, compute the Ogimet index anyway.  This prevents
         false-negative icing when NWP sub-grid microphysics detects cloud
         that the coarse pressure-level sounding missed.

    Args:
        levels: Derived levels sorted by descending pressure (surface first).
        clouds: Detected cloud layers.
        precipitable_water_mm: Total column precipitable water (severity modifier).
        cape_jkg: Surface-based CAPE for layered/convective split.
        nwp_cloud_low_pct: NWP low cloud cover (SFC–6500ft).
        nwp_cloud_mid_pct: NWP mid cloud cover (6500–20000ft).
        nwp_cloud_high_pct: NWP high cloud cover (>20000ft).
        severity_enhance: If False, skip RH/PW severity upgrades (user preference).
        nwp_cloud_diagnostics: GFS cloud layer diagnostics with per-layer base/top.
            When provided, NWP cloud checks are altitude-aware; when None, falls
            back to bulk percentage behavior.

    Returns:
        List of IcingZone, ordered from lowest to highest altitude.

    Note:
        Mutates input levels in-place: sets ``icing_index`` on each
        :class:`DerivedLevel` that has a computed icing index.
    """
    if not levels:
        return []

    sld_risk = _detect_sld(clouds, levels)
    layered_frac, convective_frac = _cape_to_cloud_split(cape_jkg)
    vd_base = _cloud_base_vapor_density(clouds, levels)

    # Pass 1: standard assessment (LWC-direct + cloud-proximity gated Ogimet)
    icing_levels: list[tuple[DerivedLevel, IcingType, IcingRisk, float]] = []
    assessed: set[int] = set()  # pressure levels already assessed

    for lv in levels:
        if lv.temperature_c is None or lv.altitude_ft is None:
            continue
        if lv.dewpoint_c is None:
            continue

        # Direct LWC-based icing (when GRIB2 CLWMR data is available)
        if (
            lv.cloud_liquid_water_g_m3 is not None
            and lv.cloud_liquid_water_g_m3 > 0
            and -20.0 <= lv.temperature_c <= 0.0
        ):
            lwc_risk = _lwc_to_icing_severity(lv.cloud_liquid_water_g_m3)
            if lwc_risk != IcingRisk.NONE:
                icing_type = _classify_icing_type(lv.temperature_c, lv.wet_bulb_c)
                if icing_type != IcingType.NONE:
                    # Store a synthetic icing index proportional to LWC
                    index = min(lv.cloud_liquid_water_g_m3 / _LWC_SEVERE * 100, 100)
                    lv.icing_index = round(index, 1)
                    icing_levels.append((lv, icing_type, lwc_risk, index))
                    assessed.add(lv.pressure_hpa)
                    continue

        if not _is_near_cloud(lv, clouds):
            continue

        # Compute and store Ogimet index on the level
        index = _compute_icing_index(
            lv.temperature_c, lv.dewpoint_c,
            layered_frac, convective_frac, vd_base,
        )
        lv.icing_index = round(index, 1)
        assessed.add(lv.pressure_hpa)

        if index <= 0:
            continue

        icing_type = _classify_icing_type(lv.temperature_c, lv.wet_bulb_c)
        if icing_type == IcingType.NONE:
            continue

        base_risk = _index_to_risk(index)
        icing_levels.append((lv, icing_type, base_risk, index))

    # Pass 2: NWP cloud cover fallback — catch levels the sounding missed.
    # The NWP cloud scheme has sub-grid microphysics not recoverable from
    # coarse pressure levels; trust it for icing gating when DD is too dry.
    for lv in levels:
        if lv.pressure_hpa in assessed:
            continue
        if lv.temperature_c is None or lv.altitude_ft is None or lv.dewpoint_c is None:
            continue
        if not (-20.0 <= lv.temperature_c <= 0.0):
            continue

        nwp_cloud = _nwp_cloud_for_altitude(
            lv.altitude_ft, nwp_cloud_low_pct, nwp_cloud_mid_pct, nwp_cloud_high_pct,
            nwp_cloud_diagnostics=nwp_cloud_diagnostics,
        )
        if nwp_cloud is None or nwp_cloud < _NWP_CLOUD_ICING_THRESHOLD:
            continue

        # NWP says cloudy in icing-temperature band — compute index
        index = _compute_icing_index(
            lv.temperature_c, lv.dewpoint_c,
            layered_frac, convective_frac, vd_base,
        )
        lv.icing_index = round(index, 1)

        if index <= 0:
            continue

        icing_type = _classify_icing_type(lv.temperature_c, lv.wet_bulb_c)
        if icing_type == IcingType.NONE:
            continue

        base_risk = _index_to_risk(index)
        icing_levels.append((lv, icing_type, base_risk, index))

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
            zones.append(_build_zone(
                current, sld_risk, precipitable_water_mm,
                nwp_cloud_low_pct, nwp_cloud_mid_pct,
                severity_enhance=severity_enhance,
                nwp_cloud_diagnostics=nwp_cloud_diagnostics,
            ))
            current = [item]

    zones.append(_build_zone(
        current, sld_risk, precipitable_water_mm,
        nwp_cloud_low_pct, nwp_cloud_mid_pct,
        severity_enhance=severity_enhance,
        nwp_cloud_diagnostics=nwp_cloud_diagnostics,
    ))
    return zones


def _nwp_cloud_for_zone(
    base_ft: float,
    nwp_cloud_low_pct: float | None,
    nwp_cloud_mid_pct: float | None,
    nwp_cloud_diagnostics: NWPCloudDiagnostics | None = None,
) -> float | None:
    """Pick the NWP cloud cover band matching the zone's base altitude."""
    return _nwp_cloud_for_altitude(
        base_ft, nwp_cloud_low_pct, nwp_cloud_mid_pct,
        nwp_cloud_diagnostics=nwp_cloud_diagnostics,
    )


def _build_zone(
    items: list[tuple[DerivedLevel, IcingType, IcingRisk, float]],
    sld_risk: bool,
    precipitable_water_mm: float | None = None,
    nwp_cloud_low_pct: float | None = None,
    nwp_cloud_mid_pct: float | None = None,
    severity_enhance: bool = True,
    nwp_cloud_diagnostics: NWPCloudDiagnostics | None = None,
) -> IcingZone:
    """Build an IcingZone from a group of adjacent icing levels."""
    levels_in_zone = [lv for lv, _, _, _ in items]
    types = [t for _, t, _, _ in items]
    risks = [r for _, _, r, _ in items]
    indices = [idx for _, _, _, idx in items]

    base = levels_in_zone[0]
    top = levels_in_zone[-1]

    base_ft = round(base.altitude_ft)
    top_ft = round(top.altitude_ft)

    # Expand thin zones (single pressure level) to minimum thickness so they
    # are visible on the cross-section canvas and detectable by evaluators.
    if top_ft - base_ft < _MIN_ZONE_HALF_THICKNESS_FT * 2:
        mid_ft = (base_ft + top_ft) / 2
        base_ft = round(mid_ft - _MIN_ZONE_HALF_THICKNESS_FT)
        top_ft = round(mid_ft + _MIN_ZONE_HALF_THICKNESS_FT)

    # Worst base risk in zone, then apply zone-level enhancement
    risk_order = [IcingRisk.NONE, IcingRisk.LIGHT, IcingRisk.MODERATE, IcingRisk.SEVERE]
    worst_risk = max(risks, key=lambda r: risk_order.index(r))

    if severity_enhance:
        nwp_pct = _nwp_cloud_for_zone(
            base.altitude_ft, nwp_cloud_low_pct, nwp_cloud_mid_pct,
            nwp_cloud_diagnostics=nwp_cloud_diagnostics,
        )
        worst_risk = _enhance_severity(
            worst_risk, levels_in_zone, precipitable_water_mm, nwp_pct,
        )

    # Dominant icing type
    type_counts: dict[IcingType, int] = {}
    for t in types:
        type_counts[t] = type_counts.get(t, 0) + 1
    dominant_type = max(type_counts, key=type_counts.get)

    # Mean values
    t_vals = [lv.temperature_c for lv in levels_in_zone if lv.temperature_c is not None]
    wb_vals = [lv.wet_bulb_c for lv in levels_in_zone if lv.wet_bulb_c is not None]

    rh_vals = [lv.relative_humidity_pct for lv in levels_in_zone if lv.relative_humidity_pct is not None]

    return IcingZone(
        base_ft=base_ft,
        top_ft=top_ft,
        base_pressure_hpa=base.pressure_hpa,
        top_pressure_hpa=top.pressure_hpa,
        risk=worst_risk,
        icing_type=dominant_type,
        sld_risk=sld_risk,
        mean_temperature_c=round(sum(t_vals) / len(t_vals), 1) if t_vals else None,
        mean_wet_bulb_c=round(sum(wb_vals) / len(wb_vals), 1) if wb_vals else None,
        mean_icing_index=round(sum(indices) / len(indices), 1) if indices else None,
        mean_rh_pct=round(sum(rh_vals) / len(rh_vals), 0) if rh_vals else None,
    )


# --- Clean single-pass Ogimet-DD assessment ---


def _build_zone_simple(
    items: list[tuple[DerivedLevel, IcingType, IcingRisk, float]],
) -> IcingZone:
    """Build an IcingZone without severity enhancement (for clean methods)."""
    return _build_zone(items, sld_risk=False, severity_enhance=False)


def _group_into_zones(
    icing_levels: list[tuple[DerivedLevel, IcingType, IcingRisk, float]],
) -> list[IcingZone]:
    """Group adjacent icing levels into zones (no severity enhancement)."""
    if not icing_levels:
        return []

    zones: list[IcingZone] = []
    current: list[tuple[DerivedLevel, IcingType, IcingRisk, float]] = [icing_levels[0]]

    for item in icing_levels[1:]:
        prev_lv = current[-1][0]
        this_lv = item[0]
        if abs(prev_lv.pressure_hpa - this_lv.pressure_hpa) <= 100:
            current.append(item)
        else:
            zones.append(_build_zone_simple(current))
            current = [item]

    zones.append(_build_zone_simple(current))
    return zones


def assess_icing_zones_ogimet_dd(
    levels: list[DerivedLevel],
    clouds: list[EnhancedCloudLayer],
    cape_jkg: float | None = None,
) -> list[IcingZone]:
    """Ogimet index with continuous DD attenuation (no binary cloud gate).

    For every level in the icing temperature range, computes:
        effective_index = ogimet_index(T) × dd_attenuation_factor(DD)

    The DD factor IS the cloud signal — no NWP fallback, no binary gate.
    """
    if not levels:
        return []

    layered_frac, convective_frac = _cape_to_cloud_split(cape_jkg)
    vd_base = _cloud_base_vapor_density(clouds, levels)

    icing_levels: list[tuple[DerivedLevel, IcingType, IcingRisk, float]] = []

    for lv in levels:
        if lv.temperature_c is None or lv.altitude_ft is None or lv.dewpoint_c is None:
            continue
        if lv.dewpoint_depression_c is None:
            continue

        raw_index = _compute_icing_index(
            lv.temperature_c, lv.dewpoint_c,
            layered_frac, convective_frac, vd_base,
        )
        if raw_index <= 0:
            continue

        dd_factor = _dd_attenuation_factor(lv.dewpoint_depression_c)
        effective = raw_index * dd_factor
        if effective <= 0:
            continue

        icing_type = _classify_icing_type(lv.temperature_c, lv.wet_bulb_c)
        if icing_type == IcingType.NONE:
            continue

        risk = _index_to_risk(effective)
        lv.icing_index = round(effective, 1)
        icing_levels.append((lv, icing_type, risk, effective))

    return _group_into_zones(icing_levels)


def assess_icing_zones_ogimet_nwp(
    levels: list[DerivedLevel],
    clouds: list[EnhancedCloudLayer],
    cape_jkg: float | None = None,
    nwp_cloud_low_pct: float | None = None,
    nwp_cloud_mid_pct: float | None = None,
    nwp_cloud_high_pct: float | None = None,
    nwp_cloud_diagnostics: NWPCloudDiagnostics | None = None,
) -> list[IcingZone]:
    """Ogimet index scaled by all available NWP data.

    Uses the full set of NWP outputs to modulate the Ogimet temperature-based
    icing index:

      effective = ogimet(T) × cloud_fraction(alt) × glaciation(CLW, ICMR)

    - **Cloud fraction**: NWP cloud cover (%) at the level's altitude, using
      diagnostic base/top boundaries when available (GFS, ICON-EU) or bulk
      ICAO band percentages gated by DD cloud proximity otherwise.
    - **Glaciation factor**: When GFS CLW and ICMR fields are available,
      reduces the index for glaciated cloud (CLW → 0, ICMR dominant).
      Falls back to 1.0 (no reduction) when microphysics data is absent.
    """
    if not levels:
        return []

    # When diagnostics are missing, bulk NWP percentages cover entire ICAO
    # bands (SFC-6500, 6500-20000, 20000+).  Use DD cloud proximity as a
    # vertical constraint to avoid false positives at cloud-free altitudes.
    need_dd_gate = nwp_cloud_diagnostics is None

    layered_frac, convective_frac = _cape_to_cloud_split(cape_jkg)
    vd_base = _cloud_base_vapor_density(clouds, levels)

    icing_levels: list[tuple[DerivedLevel, IcingType, IcingRisk, float]] = []

    for lv in levels:
        if lv.temperature_c is None or lv.altitude_ft is None or lv.dewpoint_c is None:
            continue

        raw_index = _compute_icing_index(
            lv.temperature_c, lv.dewpoint_c,
            layered_frac, convective_frac, vd_base,
        )
        if raw_index <= 0:
            continue

        nwp_cloud = _nwp_cloud_for_altitude(
            lv.altitude_ft, nwp_cloud_low_pct, nwp_cloud_mid_pct, nwp_cloud_high_pct,
            nwp_cloud_diagnostics=nwp_cloud_diagnostics,
        )
        if nwp_cloud is None or nwp_cloud <= 0:
            continue

        # Without precise NWP layer boundaries, require DD cloud proximity
        # so we don't apply e.g. 15% low-cloud to the entire SFC-6500ft band.
        if need_dd_gate and not _is_near_cloud(lv, clouds):
            continue

        cloud_fraction = nwp_cloud / 100.0
        effective = raw_index * cloud_fraction

        # Apply glaciation factor when CLW/ICMR microphysics are available.
        # In glaciated cloud (CLW ≈ 0, all ice crystals) there is no
        # supercooled liquid water and therefore no structural icing.
        clw = lv.cloud_liquid_water_g_kg
        icmr = lv.ice_mixing_ratio_g_kg
        if clw is not None and icmr is not None:
            total = clw + icmr
            glac = (clw / total) if total > 0 else 0.0
            effective *= glac

        if effective <= 0:
            continue

        icing_type = _classify_icing_type(lv.temperature_c, lv.wet_bulb_c)
        if icing_type == IcingType.NONE:
            continue

        risk = _index_to_risk(effective)
        lv.icing_index = round(effective, 1)
        icing_levels.append((lv, icing_type, risk, effective))

    return _group_into_zones(icing_levels)
