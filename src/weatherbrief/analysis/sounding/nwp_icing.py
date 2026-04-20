"""NWP-native icing zone detection from model condensate fields.

Unlike Ogimet (dewpoint-heuristic) or SFIP (fuzzy logic blending CLW + T +
derived quantities), this detector reads the model's own supercooled liquid
water (CLW) and ice (CIW) mixing ratios directly and flags levels where
liquid water coexists with sub-freezing temperatures — the most direct
indicator the physics layer of the forecast itself produces for icing.

Available when the GRIB feed delivers CLW/CIW at pressure levels (GFS,
ICON-EU, ECMWF commercial a2).  Runs independently of cloud layer gating.
"""

from __future__ import annotations

from weatherbrief.analysis.sounding.icing_common import (
    classify_icing_type,
    group_icing_levels,
)
from weatherbrief.models import (
    DerivedLevel,
    IcingRisk,
    IcingType,
    IcingZone,
)

# CLW thresholds (g/kg) — aviation severity mapping of supercooled liquid.
# Tighter than LWC (g/m³) thresholds because mixing ratio scales with air
# density; at ~500 hPa, 0.1 g/kg ≈ 0.07 g/m³ which is already "moderate".
_CLW_LIGHT_G_KG = 0.01
_CLW_MODERATE_G_KG = 0.05
_CLW_SEVERE_G_KG = 0.2

# CIW threshold — distinguishes trace ice from glaciated cloud.  Pure ice
# with negligible CLW is not an icing concern; we only use CIW to reclassify
# a mixed layer's type (MIXED vs CLEAR/RIME).
_CIW_MIXED_G_KG = 0.005

# Temperature band for liquid icing — CLW outside this band is not an
# airframe icing concern (warmer: no freezing; colder: Bergeron glaciates).
_ICING_T_MIN_C = -20.0
_ICING_T_MAX_C = 0.0


def _severity_from_clw(clw_g_kg: float) -> IcingRisk:
    """Map supercooled liquid mixing ratio to severity class."""
    if clw_g_kg >= _CLW_SEVERE_G_KG:
        return IcingRisk.SEVERE
    if clw_g_kg >= _CLW_MODERATE_G_KG:
        return IcingRisk.MODERATE
    if clw_g_kg >= _CLW_LIGHT_G_KG:
        return IcingRisk.LIGHT
    return IcingRisk.NONE


def _icing_type_with_ciw(
    t_c: float,
    clw_g_kg: float,
    ciw_g_kg: float | None,
) -> IcingType:
    """Override classify_icing_type to MIXED when significant ice coexists.

    Default temperature-based type assumes cloud is liquid; when the model
    reports substantial ice at the same level, the layer is genuinely mixed-
    phase regardless of T, so we upgrade the type.
    """
    base = classify_icing_type(t_c)
    if base == IcingType.NONE:
        return base
    if ciw_g_kg is not None and ciw_g_kg >= _CIW_MIXED_G_KG and clw_g_kg > 0:
        return IcingType.MIXED
    return base


def _build_zone(
    items: list[tuple[DerivedLevel, IcingType, IcingRisk, float]],
) -> IcingZone:
    """Aggregate adjacent per-level icing into a zone."""
    levels = [lv for lv, _, _, _ in items]
    types = [t for _, t, _, _ in items]
    risks = [r for _, _, r, _ in items]
    clw_vals = [v for _, _, _, v in items]

    base = levels[0]
    top = levels[-1]
    base_ft = round(base.altitude_ft)
    top_ft = round(top.altitude_ft)

    # Expand thin (single-level) zones for cross-section visibility.
    min_half = 500
    if top_ft - base_ft < min_half * 2:
        mid = (base_ft + top_ft) / 2
        base_ft = round(mid - min_half)
        top_ft = round(mid + min_half)

    risk_order = [IcingRisk.NONE, IcingRisk.LIGHT, IcingRisk.MODERATE, IcingRisk.SEVERE]
    worst = max(risks, key=lambda r: risk_order.index(r))

    type_counts: dict[IcingType, int] = {}
    for t in types:
        type_counts[t] = type_counts.get(t, 0) + 1
    dominant = max(type_counts, key=type_counts.get)

    t_vals = [lv.temperature_c for lv in levels if lv.temperature_c is not None]
    rh_vals = [lv.relative_humidity_pct for lv in levels
               if lv.relative_humidity_pct is not None]
    # Reuse mean_icing_index slot to carry peak CLW (g/kg ×100) for UI tooltip.
    peak_clw = max(clw_vals) if clw_vals else 0.0

    return IcingZone(
        base_ft=base_ft,
        top_ft=top_ft,
        base_pressure_hpa=base.pressure_hpa,
        top_pressure_hpa=top.pressure_hpa,
        risk=worst,
        icing_type=dominant,
        mean_temperature_c=round(sum(t_vals) / len(t_vals), 1) if t_vals else None,
        mean_icing_index=round(peak_clw * 100.0, 2),
        mean_rh_pct=round(sum(rh_vals) / len(rh_vals), 0) if rh_vals else None,
    )


def assess_nwp_icing_zones(
    levels: list[DerivedLevel],
) -> list[IcingZone]:
    """Detect icing zones directly from model CLW/CIW fields.

    Returns an empty list when no level carries non-trace CLW in the
    supercooled band — note this differs from "None available": the model
    predicted no icing, which is signal.
    """
    if not levels:
        return []

    icing_levels: list[tuple[DerivedLevel, IcingType, IcingRisk, float]] = []

    for lv in levels:
        if lv.temperature_c is None or lv.altitude_ft is None:
            continue
        clw = lv.cloud_liquid_water_g_kg
        if clw is None or clw < _CLW_LIGHT_G_KG:
            continue
        if not (_ICING_T_MIN_C <= lv.temperature_c <= _ICING_T_MAX_C):
            continue

        risk = _severity_from_clw(clw)
        if risk == IcingRisk.NONE:
            continue
        icing_type = _icing_type_with_ciw(
            lv.temperature_c, clw, lv.ice_mixing_ratio_g_kg,
        )
        if icing_type == IcingType.NONE:
            continue

        icing_levels.append((lv, icing_type, risk, clw))

    return group_icing_levels(icing_levels, _build_zone)
