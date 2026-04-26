"""Enhanced icing assessment using Ogimet continuous icing index.

Uses a physically-based icing index that peaks at −7°C (matching observed
supercooled liquid water distribution) with separate stratiform and convective
components blended by CAPE. Replaces the previous wet-bulb band classification.
"""

from __future__ import annotations

import math

from weatherbrief.analysis.sounding.icing_common import (
    MIN_ZONE_HALF_THICKNESS_FT as _MIN_ZONE_HALF_THICKNESS_FT,
    classify_icing_type,
    glaciation_factor,
    group_icing_levels,
    is_in_cloud_layer,
    nwp_cloud_cover_at_altitude,
)
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

# _MIN_ZONE_HALF_THICKNESS_FT imported from icing_common

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
    """Classify icing type — delegates to ``icing_common.classify_icing_type``."""
    return classify_icing_type(temperature_c, wet_bulb_c)


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


def _nwp_cloud_for_altitude(
    altitude_ft: float,
    nwp_cloud_low_pct: float | None,
    nwp_cloud_mid_pct: float | None,
    nwp_cloud_high_pct: float | None = None,
    nwp_cloud_diagnostics: NWPCloudDiagnostics | None = None,
) -> float | None:
    """Delegates to ``icing_common.nwp_cloud_cover_at_altitude``."""
    return nwp_cloud_cover_at_altitude(
        altitude_ft, nwp_cloud_low_pct, nwp_cloud_mid_pct, nwp_cloud_high_pct,
        nwp_cloud_diagnostics=nwp_cloud_diagnostics,
    )


# NWP cloud cover threshold for the fallback icing pass — when the NWP model
# reports cloud cover above this in an icing-temperature band, assess icing
# even if the sounding didn't detect cloud proximity at that level.
_NWP_CLOUD_ICING_THRESHOLD = 50.0


# --- Zone building ---


def _build_zone_simple(
    items: list[tuple[DerivedLevel, IcingType, IcingRisk, float]],
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

    # Expand thin zones (single pressure level) to minimum thickness
    if top_ft - base_ft < _MIN_ZONE_HALF_THICKNESS_FT * 2:
        mid_ft = (base_ft + top_ft) / 2
        base_ft = round(mid_ft - _MIN_ZONE_HALF_THICKNESS_FT)
        top_ft = round(mid_ft + _MIN_ZONE_HALF_THICKNESS_FT)

    risk_order = [IcingRisk.NONE, IcingRisk.LIGHT, IcingRisk.MODERATE, IcingRisk.SEVERE]
    worst_risk = max(risks, key=lambda r: risk_order.index(r))

    type_counts: dict[IcingType, int] = {}
    for t in types:
        type_counts[t] = type_counts.get(t, 0) + 1
    dominant_type = max(type_counts, key=type_counts.get)

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
        mean_temperature_c=round(sum(t_vals) / len(t_vals), 1) if t_vals else None,
        mean_wet_bulb_c=round(sum(wb_vals) / len(wb_vals), 1) if wb_vals else None,
        mean_icing_index=round(sum(indices) / len(indices), 1) if indices else None,
        mean_rh_pct=round(sum(rh_vals) / len(rh_vals), 0) if rh_vals else None,
    )


def _group_into_zones(
    icing_levels: list[tuple[DerivedLevel, IcingType, IcingRisk, float]],
) -> list[IcingZone]:
    """Group adjacent icing levels into zones (no severity enhancement)."""
    return group_icing_levels(icing_levels, _build_zone_simple)


def assess_icing_zones_ogimet_dd(
    levels: list[DerivedLevel],
    clouds: list[EnhancedCloudLayer],
    cape_jkg: float | None = None,
) -> list[IcingZone]:
    """Ogimet index gated by DD cloud layers with DD severity modulation.

    Cloud gating uses ``is_in_cloud_layer`` — the caller passes
    DD-detected + NWP-filtered cloud layers (same layers drawn on the
    cross-section).  Within cloud, the DD attenuation factor modulates
    severity (denser cloud → more icing).
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

        # Cloud layer gating: skip levels outside DD cloud layers
        if not is_in_cloud_layer(lv, clouds):
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
    clouds: list[EnhancedCloudLayer] | None,
    cape_jkg: float | None = None,
    nwp_cloud_low_pct: float | None = None,
    nwp_cloud_mid_pct: float | None = None,
    nwp_cloud_high_pct: float | None = None,
    nwp_cloud_diagnostics: NWPCloudDiagnostics | None = None,
) -> list[IcingZone]:
    """Ogimet index gated by NWP cloud layers, scaled by cloud fraction.

      effective = ogimet(T) × cloud_fraction(alt) × glaciation(CLW, ICMR)

    Cloud gating uses ``is_in_cloud_layer`` — the caller passes NWP cloud
    layers (pure model output).  Within cloud, NWP cloud cover percentage
    modulates severity and glaciation factor reduces index in glaciated cloud.

    Returns ``[]`` immediately when ``clouds`` is None or empty: the
    Ogimet-NWP variant requires a model-native cloud envelope, and we
    refuse to fabricate icing zones from bulk percentages alone — that
    would produce icing calls in altitudes the cross-section can't
    visually anchor to a cloud band.

    Stores per-level index in ``lv.icing_index_nwp`` (separate from
    ``icing_index`` used by Ogimet-DD) to avoid overwriting.
    """
    if not levels or not clouds:
        return []

    layered_frac, convective_frac = _cape_to_cloud_split(cape_jkg)
    vd_base = _cloud_base_vapor_density(clouds, levels)

    icing_levels: list[tuple[DerivedLevel, IcingType, IcingRisk, float]] = []

    for lv in levels:
        if lv.temperature_c is None or lv.altitude_ft is None or lv.dewpoint_c is None:
            continue

        # NWP cloud layer gating: skip levels outside NWP cloud layers
        if not is_in_cloud_layer(lv, clouds):
            continue

        raw_index = _compute_icing_index(
            lv.temperature_c, lv.dewpoint_c,
            layered_frac, convective_frac, vd_base,
        )
        if raw_index <= 0:
            continue

        # NWP cloud cover for severity modulation
        nwp_cloud = _nwp_cloud_for_altitude(
            lv.altitude_ft, nwp_cloud_low_pct, nwp_cloud_mid_pct, nwp_cloud_high_pct,
            nwp_cloud_diagnostics=nwp_cloud_diagnostics,
        )
        if nwp_cloud is None or nwp_cloud <= 0:
            continue

        cloud_fraction = nwp_cloud / 100.0
        effective = raw_index * cloud_fraction

        # Apply glaciation factor when CLW/ICMR microphysics are available.
        # Temperature-dependent floor prevents grid-scale CLW=0 from
        # completely zeroing out icing at warm temps where SLW is expected.
        clw = lv.cloud_liquid_water_g_kg
        icmr = lv.ice_mixing_ratio_g_kg
        if clw is not None and icmr is not None:
            effective *= glaciation_factor(clw, icmr, lv.temperature_c)

        if effective <= 0:
            continue

        icing_type = _classify_icing_type(lv.temperature_c, lv.wet_bulb_c)
        if icing_type == IcingType.NONE:
            continue

        risk = _index_to_risk(effective)
        lv.icing_index_nwp = round(effective, 1)
        icing_levels.append((lv, icing_type, risk, effective))

    return _group_into_zones(icing_levels)


def assess_icing_zones_ieng(
    levels: list[DerivedLevel],
    clouds: list[EnhancedCloudLayer] | None,
    cape_jkg: float | None = None,
    nwp_cloud_low_pct: float | None = None,
    nwp_cloud_mid_pct: float | None = None,
    nwp_cloud_high_pct: float | None = None,
    nwp_cloud_diagnostics: NWPCloudDiagnostics | None = None,
) -> list[IcingZone]:
    """IENG icing index: temperature curve scaled by NWP cloud fraction.

    Cloud gating uses ``is_in_cloud_layer`` — the caller passes NWP cloud
    layers (pure model output).  Within cloud, NWP cloud cover percentage
    modulates severity.  No glaciation correction.

    Returns ``[]`` immediately when ``clouds`` is None or empty: same
    rationale as Ogimet-NWP — refuse to compute icing without a
    model-native cloud envelope.
    """
    if not levels or not clouds:
        return []

    icing_levels: list[tuple[DerivedLevel, IcingType, IcingRisk, float]] = []

    for lv in levels:
        if lv.temperature_c is None or lv.altitude_ft is None:
            continue

        # NWP cloud layer gating: skip levels outside NWP cloud layers
        if not is_in_cloud_layer(lv, clouds):
            continue

        raw_index = _compute_layered_index(lv.temperature_c)
        if raw_index <= 0:
            continue

        # NWP cloud cover for severity modulation
        nwp_cloud = _nwp_cloud_for_altitude(
            lv.altitude_ft, nwp_cloud_low_pct, nwp_cloud_mid_pct, nwp_cloud_high_pct,
            nwp_cloud_diagnostics=nwp_cloud_diagnostics,
        )
        if nwp_cloud is None or nwp_cloud <= 0:
            continue

        cloud_fraction = nwp_cloud / 100.0
        effective = raw_index * cloud_fraction

        # Add convective component when CAPE is significant
        if cape_jkg is not None and cape_jkg > 100 and lv.dewpoint_c is not None:
            vd_base = _cloud_base_vapor_density(clouds, levels)
            conv_index = _compute_convective_index(lv.temperature_c, 0.0, vd_base)
            if conv_index > 0:
                conv_cover = 0.0
                if nwp_cloud_diagnostics and nwp_cloud_diagnostics.convective_cover_pct:
                    conv_cover = nwp_cloud_diagnostics.convective_cover_pct / 100.0
                effective += conv_index * conv_cover

        if effective <= 0:
            continue

        icing_type = _classify_icing_type(lv.temperature_c, lv.wet_bulb_c)
        if icing_type == IcingType.NONE:
            continue

        risk = _index_to_risk(effective)
        icing_levels.append((lv, icing_type, risk, effective))

    return _group_into_zones(icing_levels)
