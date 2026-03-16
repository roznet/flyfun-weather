"""Shared utilities for icing assessment methods (Ogimet and SFIP).

Centralises cloud-proximity checks, NWP cloud altitude lookups,
icing-type classification, and zone-grouping logic so that all three
icing methods behave consistently.
"""

from __future__ import annotations

from collections.abc import Callable

from weatherbrief.models import (
    CloudCoverage,
    DerivedLevel,
    EnhancedCloudLayer,
    IcingRisk,
    IcingType,
    NWPCloudDiagnostics,
)


# ── Cloud-proximity check ───────────────────────────────────────────


# Dewpoint depression thresholds for cloud proximity gating.
# DD < BKN threshold → unconditionally in-cloud (BKN/OVC equivalent).
# DD < SCT threshold → possibly in cloud (SCT equivalent, used by SFIP proxy).
DD_BKN_THRESHOLD = 2.0
DD_SCT_THRESHOLD = 3.0

# Vertical margin around cloud layer boundaries
CLOUD_MARGIN_FT = 500.0


def is_near_cloud(
    level: DerivedLevel,
    clouds: list[EnhancedCloudLayer],
    *,
    dd_threshold: float = DD_BKN_THRESHOLD,
    skip_sct: bool = True,
) -> bool:
    """Check if a level is in or near cloud.

    Args:
        level: Derived level to check.
        clouds: Detected cloud layers.
        dd_threshold: DD below which the level is unconditionally in-cloud.
            Use ``DD_BKN_THRESHOLD`` (2.0) for Ogimet methods (ignores SCT),
            ``DD_SCT_THRESHOLD`` (3.0) for SFIP proxy (wider net).
        skip_sct: When True, scattered cloud layers are not counted for
            the altitude-proximity check (pilots can see and avoid).

    Returns:
        True when the level's DD is below *dd_threshold* or the level's
        altitude is within 500 ft of a qualifying cloud layer.
    """
    if level.altitude_ft is None:
        return False
    if level.dewpoint_depression_c is not None and level.dewpoint_depression_c < dd_threshold:
        return True
    for cl in clouds:
        if skip_sct and cl.coverage == CloudCoverage.SCT:
            continue
        if (cl.base_ft - CLOUD_MARGIN_FT) <= level.altitude_ft <= (cl.top_ft + CLOUD_MARGIN_FT):
            return True
    return False


# ── NWP cloud cover at altitude ─────────────────────────────────────


_NWP_CLOUD_ALTITUDE_MARGIN_FT = 500.0


def nwp_cloud_cover_at_altitude(
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


# ── Glaciation floor ───────────────────────────────────────────────


# GFS often reports CLW=0 at grid scale even when sub-grid SLW exists,
# especially at warmer temperatures where SLW is physically expected.
# A temperature-dependent floor prevents CLW=0 from completely zeroing
# out icing when NWP cloud cover confirms cloud exists.
_GLAC_FLOOR_WARM = 0.8   # above -10°C — SLW very likely despite CLW=0
_GLAC_FLOOR_COLD = 0.15  # below -20°C — glaciation is physically plausible


def glaciation_factor(
    clw_g_kg: float,
    icmr_g_kg: float,
    temperature_c: float | None = None,
) -> float:
    """Liquid-fraction multiplier — reduces icing when cloud is glaciated.

    Returns CLW / (CLW + ICE) floored by a temperature-dependent minimum.
    At warm temperatures (> −10 °C) the floor is high (0.8) because SLW
    is physically expected even when the model's grid-scale CLW is zero.
    At cold temperatures (< −20 °C) the floor drops to 0.15.

    When *temperature_c* is None, no floor is applied (backward compat).
    """
    total = clw_g_kg + icmr_g_kg
    if total <= 0:
        return 0.0
    raw = clw_g_kg / total
    if temperature_c is None:
        return raw
    # Temperature-dependent floor
    if temperature_c >= -10.0:
        floor = _GLAC_FLOOR_WARM
    elif temperature_c <= -20.0:
        floor = _GLAC_FLOOR_COLD
    else:
        frac = (temperature_c - (-10.0)) / (-20.0 - (-10.0))
        floor = _GLAC_FLOOR_WARM + frac * (_GLAC_FLOOR_COLD - _GLAC_FLOOR_WARM)
    return max(raw, floor)


# ── Icing type classification ───────────────────────────────────────


def classify_icing_type(
    temperature_c: float,
    wet_bulb_c: float | None = None,
) -> IcingType:
    """Classify icing type from wet-bulb temperature (dry-bulb fallback).

    The accretion surface temperature is closer to wet-bulb than dry-bulb
    in cloud/precipitating conditions.  Wet-bulb is always <= dry-bulb,
    so thresholds are shifted down by ~1°C to compensate.

    Thresholds (wet-bulb):
        CLEAR:  −4°C to 0°C
        MIXED: −11°C to −4°C
        RIME:  < −11°C
    """
    t = wet_bulb_c if wet_bulb_c is not None else temperature_c
    if -4.0 <= t <= 0.0:
        return IcingType.CLEAR
    if -11.0 <= t < -4.0:
        return IcingType.MIXED
    if t < -11.0:
        return IcingType.RIME
    return IcingType.NONE


# ── Zone grouping ───────────────────────────────────────────────────


# Maximum pressure gap between adjacent levels to be grouped in same zone.
ZONE_MAX_PRESSURE_GAP_HPA = 100

# Minimum half-thickness for single-level zones (cross-section visibility).
MIN_ZONE_HALF_THICKNESS_FT = 500


def group_icing_levels(
    icing_levels: list[tuple[DerivedLevel, IcingType, IcingRisk, float]],
    build_zone_fn: Callable,
) -> list:
    """Group adjacent icing levels into zones using shared adjacency logic.

    Adjacent levels (pressure gap <= 100 hPa) are grouped together.
    Each group is passed to *build_zone_fn* to construct the zone object.

    Args:
        icing_levels: Tuples of (level, icing_type, risk, index_value).
        build_zone_fn: Callable that takes a list of tuples and returns a zone.

    Returns:
        List of zone objects, ordered by ascending altitude.
    """
    if not icing_levels:
        return []

    zones: list = []
    current: list[tuple[DerivedLevel, IcingType, IcingRisk, float]] = [icing_levels[0]]

    for item in icing_levels[1:]:
        prev_lv = current[-1][0]
        this_lv = item[0]
        if abs(prev_lv.pressure_hpa - this_lv.pressure_hpa) <= ZONE_MAX_PRESSURE_GAP_HPA:
            current.append(item)
        else:
            zones.append(build_zone_fn(current))
            current = [item]

    zones.append(build_zone_fn(current))
    return zones
