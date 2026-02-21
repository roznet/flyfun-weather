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

from weatherbrief.models import (
    DerivedLevel,
    IcingRisk,
    IcingType,
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

    Piecewise linear: peaks 1.0 in [-5, -14]°C, tapers to 0 at 0°C and -25°C.
    """
    if t_celsius >= 0.0:
        return 0.0
    elif t_celsius >= -2.0:
        return 0.8 * (-t_celsius) / 2.0
    elif t_celsius >= -5.0:
        return 0.8 + 0.2 * (-t_celsius - 2.0) / 3.0
    elif t_celsius >= -14.0:
        return 1.0
    elif t_celsius >= -20.0:
        return 0.8 + 0.2 * (-t_celsius - 20.0) / 6.0
    elif t_celsius >= -25.0:
        # Ramp down from 0.8 at -20°C to 0.0 at -25°C
        return 0.8 * (t_celsius + 25.0) / 5.0
    else:
        return 0.0


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


def glaciation_factor(clw_g_kg: float, icmr_g_kg: float) -> float:
    """Liquid fraction multiplier — reduces icing when cloud is glaciated.

    Both inputs in g/kg. Returns 0.0 (all ice) to 1.0 (all liquid).
    """
    total = clw_g_kg + icmr_g_kg
    if total <= 0:
        return 0.0
    return clw_g_kg / total


# ── Per-level computation ────────────────────────────────────────────


def _cloud_cover_for_pressure(
    pressure_hpa: int,
    nwp_cloud_low_pct: float | None,
    nwp_cloud_mid_pct: float | None,
    nwp_cloud_high_pct: float | None,
) -> float:
    """Map pressure level to ICAO cloud band and return NWP cloud cover %.

    Low: SFC–6500 ft (~1013–800 hPa), Mid: 6500–20000 ft (~800–450 hPa),
    High: above 20000 ft (~<450 hPa).
    """
    if pressure_hpa >= 800:
        return nwp_cloud_low_pct or 0.0
    elif pressure_hpa >= 450:
        return nwp_cloud_mid_pct or 0.0
    else:
        return nwp_cloud_high_pct or 0.0


def compute_sfip_level(
    temperature_c: float,
    rh_pct: float,
    dewpoint_depression_c: float,
    clw_g_kg: float | None,
    icmr_g_kg: float | None,
    omega_pa_s: float | None,
    cloud_cover_at_band: float,
) -> tuple[float, float, str, str]:
    """Compute SFIP for a single pressure level.

    Returns (sfip_raw, sfip_100, severity_str, variant_str).
    Temperature gating: returns zeros outside [0, -25]°C.
    """
    # Temperature gating
    if temperature_c >= 0.0 or temperature_c < -25.0:
        return 0.0, 0.0, "NONE", "full" if clw_g_kg is not None else "proxy"

    m_t = membership_temperature(temperature_c)
    m_rh = membership_rh(rh_pct)
    m_vv = membership_vv(omega_pa_s)

    if clw_g_kg is not None:
        # Full variant (SFIP_O)
        m_clw = membership_clw(clw_g_kg)
        # Apply glaciation factor when ice data available
        if icmr_g_kg is not None and icmr_g_kg > 0:
            m_clw *= glaciation_factor(clw_g_kg, icmr_g_kg)
        sfip_value = (
            _W_T_FULL * m_t
            + _W_RH_FULL * m_rh
            + _W_CLW_FULL * m_clw
            + _W_VV_FULL * m_vv
        )
        variant = "full"
    else:
        # Proxy variant (SFIP_4)
        m_clw_p = membership_clw_proxy(dewpoint_depression_c, rh_pct, cloud_cover_at_band)
        sfip_value = (
            _W_T_PROXY * m_t
            + _W_RH_PROXY * m_rh
            + _W_CLW_PROXY * m_clw_p
            + _W_VV_PROXY * m_vv
        )
        variant = "proxy"

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
    """Classify icing type from temperature (same as Ogimet)."""
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
    nwp_cloud_low_pct: float | None = None,
    nwp_cloud_mid_pct: float | None = None,
    nwp_cloud_high_pct: float | None = None,
) -> list[SfipZone]:
    """Compute SFIP at each level and group into icing zones.

    Stores per-level SFIP results on DerivedLevel objects.
    Returns list of SfipZone ordered by ascending altitude.
    """
    if not levels:
        return []

    # Compute SFIP for each level
    sfip_levels: list[tuple[DerivedLevel, IcingType, IcingRisk, float]] = []

    for lv in levels:
        if lv.temperature_c is None or lv.altitude_ft is None:
            continue
        if lv.relative_humidity_pct is None or lv.dewpoint_depression_c is None:
            continue

        cloud_cover = _cloud_cover_for_pressure(
            lv.pressure_hpa, nwp_cloud_low_pct, nwp_cloud_mid_pct, nwp_cloud_high_pct,
        )

        sfip_raw, sfip_100, severity, variant = compute_sfip_level(
            temperature_c=lv.temperature_c,
            rh_pct=lv.relative_humidity_pct,
            dewpoint_depression_c=lv.dewpoint_depression_c,
            clw_g_kg=lv.cloud_liquid_water_g_kg,
            icmr_g_kg=lv.ice_mixing_ratio_g_kg,
            omega_pa_s=lv.omega_pa_s,
            cloud_cover_at_band=cloud_cover,
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

    # Group adjacent levels into zones (same 100 hPa adjacency rule as Ogimet)
    zones: list[SfipZone] = []
    current: list[tuple[DerivedLevel, IcingType, IcingRisk, float]] = [sfip_levels[0]]

    for item in sfip_levels[1:]:
        prev_lv = current[-1][0]
        this_lv = item[0]
        if abs(prev_lv.pressure_hpa - this_lv.pressure_hpa) <= 100:
            current.append(item)
        else:
            zones.append(_build_sfip_zone(current))
            current = [item]

    zones.append(_build_sfip_zone(current))
    return zones


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
