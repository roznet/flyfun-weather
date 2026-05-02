"""SFIP (Simplified Forecast Icing Potential) icing index.

Fuzzy-logic membership functions combined with weights to produce a 0–100
icing potential index. Two variants: SFIP_O (full, with CLW data from GFS)
and SFIP_4 (proxy, using dewpoint depression + NWP cloud cover).

References:
  - Belo-Pereira (2015). Comparison of in-flight aircraft icing algorithms.
  - Morcrette et al. (2019). Development and evaluation of in-flight icing
    index forecast for aviation.
"""

from __future__ import annotations

import math

from weatherbrief.analysis.sounding.icing_common import (
    group_icing_levels,
    is_in_cloud_layer,
    nwp_cloud_cover_at_altitude,
)
from weatherbrief.models import (
    DerivedLevel,
    EnhancedCloudLayer,
    IcingRisk,
    IcingType,
    NWPCloudDiagnostics,
    SfipZone,
)

# --- SFIP_O weights (full variant, with CLW) ---
_W_T_FULL = 0.35
_W_RH_FULL = 0.15
_W_CLW_FULL = 0.35
_W_VV_FULL = 0.15

# --- SFIP_4 weights (proxy variant, without CLW) ---
_W_T_PROXY = 0.40
_W_RH_PROXY = 0.25
_W_CLW_PROXY = 0.25
_W_VV_PROXY = 0.10

# --- GA-tuned severity thresholds ---
_SFIP_NONE = 15.0
_SFIP_LIGHT = 30.0
_SFIP_MODERATE = 55.0


# ── Membership functions ─────────────────────────────────────────────


def membership_temperature(t_celsius: float) -> float:
    """Temperature membership function for SFIP.

    Piecewise linear ramp to peak 1.0 in [-5, -14]°C, then exponential
    decay below -14°C.  SLW concentration drops roughly exponentially
    with decreasing temperature as ice nucleation dominates.

    Decay constant ``_TEMP_DECAY_K`` controls steepness:
      k=0.4 → -15°C: 0.67, -17°C: 0.30, -20°C: 0.09
    """
    if t_celsius >= 0.0:
        return 0.0
    elif t_celsius >= -2.0:
        return 0.8 * (-t_celsius) / 2.0
    elif t_celsius >= -5.0:
        return 0.8 + 0.2 * (-t_celsius - 2.0) / 3.0
    elif t_celsius >= -14.0:
        return 1.0
    elif t_celsius >= -25.0:
        return math.exp(_TEMP_DECAY_K * (t_celsius + 14.0))
    else:
        return 0.0


# Exponential decay rate for m_T below -14°C.  Higher = steeper drop.
_TEMP_DECAY_K = 0.4


def membership_rh(rh_percent: float) -> float:
    """Relative humidity membership function for SFIP.

    Ramp from 0 at 50% to 1.0 at 100%, with steeper segments near saturation.
    """
    if rh_percent <= 50.0:
        return 0.0
    elif rh_percent <= 60.0:
        return 0.1 * (rh_percent - 50.0) / 10.0
    elif rh_percent <= 90.0:
        return 0.1 + 0.6 * (rh_percent - 60.0) / 30.0
    elif rh_percent <= 95.0:
        return 0.7 + 0.2 * (rh_percent - 90.0) / 5.0
    elif rh_percent <= 100.0:
        return 0.9 + 0.1 * (rh_percent - 95.0) / 5.0
    else:
        return 1.0


def membership_clw(clw_g_kg: float) -> float:
    """Cloud liquid water membership function for SFIP.

    Input in g/kg (GFS CLWMR × 1000). Ramp from 0 to 1.0 over [0, 0.2] g/kg.
    """
    if clw_g_kg <= 0.0:
        return 0.0
    elif clw_g_kg <= 0.01:
        return 0.3 * clw_g_kg / 0.01
    elif clw_g_kg <= 0.05:
        return 0.3 + 0.4 * (clw_g_kg - 0.01) / 0.04
    elif clw_g_kg <= 0.1:
        return 0.7 + 0.2 * (clw_g_kg - 0.05) / 0.05
    elif clw_g_kg <= 0.2:
        return 0.9 + 0.1 * (clw_g_kg - 0.1) / 0.1
    else:
        return 1.0


def membership_clw_proxy(
    dewpoint_depression: float,
    rh_percent: float,
    cloud_cover_at_band: float,
) -> float:
    """Proxy for CLW membership when actual CLW is not available.

    Uses dewpoint depression + NWP cloud cover as proxy for cloud presence.
    """
    # Sounding-based cloud detection
    if dewpoint_depression > 5.0:
        sounding_score = 0.0
    elif dewpoint_depression > 3.0:
        sounding_score = 0.2 * (5.0 - dewpoint_depression) / 2.0
    elif dewpoint_depression > 1.0:
        sounding_score = 0.2 + 0.5 * (3.0 - dewpoint_depression) / 2.0
    else:
        # DD < 1°C: very likely in cloud
        sounding_score = 0.7 + 0.3 * min(1.0, rh_percent / 100.0)

    # NWP cloud cover factor
    if cloud_cover_at_band > 80:
        nwp_score = 1.0
    elif cloud_cover_at_band > 50:
        nwp_score = 0.5 + 0.5 * (cloud_cover_at_band - 50.0) / 30.0
    elif cloud_cover_at_band > 20:
        nwp_score = 0.2 * (cloud_cover_at_band - 20.0) / 30.0
    else:
        nwp_score = 0.0

    # Combine: trust whichever is higher (conservative for safety)
    return max(sounding_score, nwp_score * 0.8)


def membership_vv(omega_pa_s: float | None) -> float:
    """Vertical velocity membership function for SFIP.

    Negative omega = ascent (favorable). Returns -0.3 to +0.5.
    """
    if omega_pa_s is None:
        return 0.0

    if omega_pa_s < -5.0:
        return 0.5
    elif omega_pa_s < -2.0:
        return 0.3 + 0.2 * (-omega_pa_s - 2.0) / 3.0
    elif omega_pa_s < -0.5:
        return 0.1 + 0.2 * (-omega_pa_s - 0.5) / 1.5
    elif omega_pa_s < 0.5:
        return 0.0
    elif omega_pa_s < 2.0:
        return -0.15 * (omega_pa_s - 0.5) / 1.5
    else:
        return -0.3


def glaciation_factor(clw_g_kg: float, icmr_g_kg: float, temperature_c: float | None = None) -> float:
    """Liquid fraction multiplier — delegates to ``icing_common.glaciation_factor``."""
    from weatherbrief.analysis.sounding.icing_common import glaciation_factor as _glac
    return _glac(clw_g_kg, icmr_g_kg, temperature_c)


# ── Per-level computation ────────────────────────────────────────────


def _cloud_cover_for_level(
    altitude_ft: float,
    nwp_cloud_low_pct: float | None,
    nwp_cloud_mid_pct: float | None,
    nwp_cloud_high_pct: float | None,
    nwp_cloud_diagnostics: NWPCloudDiagnostics | None = None,
) -> float:
    """Return altitude-aware NWP cloud cover % for a level.

    Delegates to ``icing_common.nwp_cloud_cover_at_altitude`` which checks
    **all** diagnostic layers (not just the ICAO band matching the altitude),
    correctly handling cross-band cloud layers.
    """
    result = nwp_cloud_cover_at_altitude(
        altitude_ft, nwp_cloud_low_pct, nwp_cloud_mid_pct, nwp_cloud_high_pct,
        nwp_cloud_diagnostics=nwp_cloud_diagnostics,
    )
    return result or 0.0


def compute_sfip_level(
    temperature_c: float,
    rh_pct: float,
    dewpoint_depression_c: float,
    clw_g_kg: float | None,
    icmr_g_kg: float | None,
    omega_pa_s: float | None,
    cloud_cover_at_band: float,
    clw_interpolated: bool = False,
) -> tuple[float, float, str, str]:
    """Compute SFIP for a single pressure level.

    Returns (sfip_raw, sfip_100, severity_str, variant_str).
    Temperature gating: returns zeros outside [0, -25]°C.

    Variant values:
      - ``"full"``: CLW from GRIB2, omega available
      - ``"full_no_vv"``: CLW from GRIB2 but omega unavailable (e.g. ICON)
      - ``"interp"``: CLW spatially interpolated from neighbors
      - ``"interp_no_vv"``: interpolated CLW, no omega
      - ``"proxy"``: no CLW data, uses DD+cloud proxy
      - ``"proxy_no_vv"``: proxy without omega
    """
    has_vv = omega_pa_s is not None
    if clw_g_kg is not None and clw_interpolated:
        variant = "interp" if has_vv else "interp_no_vv"
    elif clw_g_kg is not None:
        variant = "full" if has_vv else "full_no_vv"
    else:
        variant = "proxy" if has_vv else "proxy_no_vv"

    # Temperature gating
    if temperature_c >= 0.0 or temperature_c < -25.0:
        return 0.0, 0.0, "NONE", variant

    # Cloud gating for full variant: no liquid water → no icing
    if clw_g_kg is not None and clw_g_kg <= 0:
        return 0.0, 0.0, "NONE", variant

    m_t = membership_temperature(temperature_c)
    m_rh = membership_rh(rh_pct)
    m_vv = membership_vv(omega_pa_s)

    if clw_g_kg is not None:
        # Full variant (SFIP_O)
        m_clw = membership_clw(clw_g_kg)
        # Apply glaciation factor when ice data available
        if icmr_g_kg is not None and icmr_g_kg > 0:
            m_clw *= glaciation_factor(clw_g_kg, icmr_g_kg, temperature_c)
        sfip_value = (
            _W_T_FULL * m_t
            + _W_RH_FULL * m_rh
            + _W_CLW_FULL * m_clw
            + _W_VV_FULL * m_vv
        )
    else:
        # Proxy variant (SFIP_4)
        m_clw_p = membership_clw_proxy(dewpoint_depression_c, rh_pct, cloud_cover_at_band)
        sfip_value = (
            _W_T_PROXY * m_t
            + _W_RH_PROXY * m_rh
            + _W_CLW_PROXY * m_clw_p
            + _W_VV_PROXY * m_vv
        )

    sfip_raw = max(0.0, min(1.0, sfip_value))
    sfip_100 = round(sfip_raw * 100.0, 1)
    severity = sfip_to_risk(sfip_100).value.upper()

    return sfip_raw, sfip_100, severity, variant


# ── Severity mapping ─────────────────────────────────────────────────


def sfip_to_risk(sfip_100: float) -> IcingRisk:
    """Map SFIP 0–100 to IcingRisk using GA-tuned thresholds."""
    if sfip_100 >= _SFIP_MODERATE:
        return IcingRisk.SEVERE
    if sfip_100 >= _SFIP_LIGHT:
        return IcingRisk.MODERATE
    if sfip_100 >= _SFIP_NONE:
        return IcingRisk.LIGHT
    return IcingRisk.NONE


def _classify_icing_type(temperature_c: float) -> IcingType:
    """Classify icing type from dry-bulb temperature per SFIP literature.

    Uses dry-bulb thresholds from Belo-Pereira (2015) and Morcrette et al.
    (2019), which differ from the Ogimet wet-bulb thresholds in
    ``icing_common.classify_icing_type``.

    Thresholds (dry-bulb):
        CLEAR:  −3°C to 0°C
        MIXED: −10°C to −3°C
        RIME:  < −10°C
    """
    if -3.0 <= temperature_c <= 0.0:
        return IcingType.CLEAR
    if -10.0 <= temperature_c < -3.0:
        return IcingType.MIXED
    if temperature_c < -10.0:
        return IcingType.RIME
    return IcingType.NONE


# ── Zone builder ─────────────────────────────────────────────────────


def assess_sfip_zones(
    levels: list[DerivedLevel],
    dd_cloud_layers: list[EnhancedCloudLayer] | None = None,
    nwp_cloud_layers: list[EnhancedCloudLayer] | None = None,
    nwp_cloud_low_pct: float | None = None,
    nwp_cloud_mid_pct: float | None = None,
    nwp_cloud_high_pct: float | None = None,
    nwp_cloud_diagnostics: NWPCloudDiagnostics | None = None,
) -> list[SfipZone]:
    """Compute SFIP at each level and group into icing zones.

    Cloud gating per variant:
      - Full (CLW available): level must be within an NWP cloud layer
        (CLW is model output → gate by model cloud layers).  CLW <= 0
        is additionally gated out in compute_sfip_level.
      - Proxy (no CLW): level must be within a DD cloud layer
        (sounding-derived + NWP-filtered → same gray bands on cross-section).

    Stores per-level SFIP results on DerivedLevel objects.
    Returns list of SfipZone ordered by ascending altitude.
    """
    if not levels:
        return []

    dd_clouds = dd_cloud_layers or []
    nwp_clouds = nwp_cloud_layers or []

    # Compute SFIP for each level
    sfip_levels: list[tuple[DerivedLevel, IcingType, IcingRisk, float]] = []

    for lv in levels:
        if lv.temperature_c is None or lv.altitude_ft is None:
            continue
        if lv.relative_humidity_pct is None or lv.dewpoint_depression_c is None:
            continue

        # Cloud layer gating: full → NWP layers, proxy → DD layers
        is_proxy = lv.cloud_liquid_water_g_kg is None
        if is_proxy and not is_in_cloud_layer(lv, dd_clouds):
            continue
        if not is_proxy and not is_in_cloud_layer(lv, nwp_clouds):
            continue

        cloud_cover = _cloud_cover_for_level(
            lv.altitude_ft, nwp_cloud_low_pct, nwp_cloud_mid_pct, nwp_cloud_high_pct,
            nwp_cloud_diagnostics=nwp_cloud_diagnostics,
        )

        sfip_raw, sfip_100, severity, variant = compute_sfip_level(
            temperature_c=lv.temperature_c,
            rh_pct=lv.relative_humidity_pct,
            dewpoint_depression_c=lv.dewpoint_depression_c,
            clw_g_kg=lv.cloud_liquid_water_g_kg,
            icmr_g_kg=lv.ice_mixing_ratio_g_kg,
            omega_pa_s=lv.omega_pa_s,
            cloud_cover_at_band=cloud_cover,
            clw_interpolated=lv.clw_interpolated,
        )

        # Store on level
        lv.sfip_raw = round(sfip_raw, 4)
        lv.sfip_100 = sfip_100
        lv.sfip_severity = severity
        lv.sfip_variant = variant

        if sfip_100 <= 0:
            continue

        risk = sfip_to_risk(sfip_100)
        if risk == IcingRisk.NONE:
            continue

        icing_type = _classify_icing_type(lv.temperature_c)
        sfip_levels.append((lv, icing_type, risk, sfip_100))

    if not sfip_levels:
        return []

    return group_icing_levels(sfip_levels, _build_sfip_zone, all_levels=levels)


def _build_sfip_zone(
    items: list[tuple[DerivedLevel, IcingType, IcingRisk, float]],
) -> SfipZone:
    """Build an SfipZone from a group of adjacent icing levels."""
    levels_in_zone = [lv for lv, _, _, _ in items]
    types = [t for _, t, _, _ in items]
    risks = [r for _, _, r, _ in items]
    sfip_values = [v for _, _, _, v in items]

    base = levels_in_zone[0]
    top = levels_in_zone[-1]

    # Worst risk in zone
    risk_order = [IcingRisk.NONE, IcingRisk.LIGHT, IcingRisk.MODERATE, IcingRisk.SEVERE]
    worst_risk = max(risks, key=lambda r: risk_order.index(r))

    # Dominant icing type
    type_counts: dict[IcingType, int] = {}
    for t in types:
        type_counts[t] = type_counts.get(t, 0) + 1
    dominant_type = max(type_counts, key=type_counts.get)  # type: ignore[arg-type]

    # Mean values
    t_vals = [lv.temperature_c for lv in levels_in_zone if lv.temperature_c is not None]
    rh_vals = [lv.relative_humidity_pct for lv in levels_in_zone if lv.relative_humidity_pct is not None]

    # Variant: use first level's variant (all levels in a zone should have same variant)
    variant = levels_in_zone[0].sfip_variant or "full"

    return SfipZone(
        base_ft=round(base.altitude_ft),  # type: ignore[arg-type]
        top_ft=round(top.altitude_ft),  # type: ignore[arg-type]
        base_pressure_hpa=base.pressure_hpa,
        top_pressure_hpa=top.pressure_hpa,
        risk=worst_risk,
        icing_type=dominant_type,
        mean_sfip_100=round(sum(sfip_values) / len(sfip_values), 1) if sfip_values else None,
        mean_temperature_c=round(sum(t_vals) / len(t_vals), 1) if t_vals else None,
        mean_rh_pct=round(sum(rh_vals) / len(rh_vals), 0) if rh_vals else None,
        variant=variant,
    )
