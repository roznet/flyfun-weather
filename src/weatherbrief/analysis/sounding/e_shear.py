"""E-Shear turbulence index (CloudPath method).

Computes the E parameter from combined vertical and horizontal wind shear:

    E = (5 * HWS + VWS² + 42) / 4

Where:
    VWS = vertical wind shear (kt/1000ft), differences between adjacent levels
    HWS = horizontal wind shear (kt/100nm), differences between route points

Thresholds:
    E ≥  80 → Moderate turbulence
    E ≥ 160 → Severe turbulence

Works for all models (GFS, ECMWF, ICON) — only requires wind at pressure levels.
"""

from __future__ import annotations

import math

from weatherbrief.models import CATRiskLayer, CATRiskLevel, DerivedLevel

# E-parameter thresholds
_E_MODERATE = 80.0
_E_SEVERE = 160.0
_E_LIGHT = 40.0  # lower threshold for light turbulence detection

# Unit conversions. The CloudPath E formula is calibrated with VWS in
# kt/1000ft and HWS in kt/100nm; shear is computed here in SI (m/s per m),
# so the scale factors are exact unit conversions, not round powers of ten.
# (Previously 1e3/1e5, i.e. m/s-per-km and m/s-per-100km — that overstated
# VWS ×1.69 (×2.85 after squaring) and understated HWS ×3.6 relative to the
# formula's calibration units.)
# Constants are the *exact* international definitions (1 nm = 1852 m,
# 1 ft = 0.3048 m, 1 hr = 3600 s) so kt↔m/s round-trips exactly. Earlier the
# code mixed truncated values (1.94384 one way, 0.51444 the other) that were
# not exact reciprocals, leaving a ~1e-5 inconsistency and making _HWS_SCALE
# 359999.168 instead of exactly 360000.
_KT_TO_MS = 1852.0 / 3600.0        # 1 kt = 1852 m / 3600 s  (≈ 0.514444)
_MS_TO_KT = 3600.0 / 1852.0        # exact reciprocal        (≈ 1.943844)
_VWS_SCALE = _MS_TO_KT * 304.8     # (m/s per m) → kt per 1000 ft  (≈ 592.484)
_HWS_SCALE = _MS_TO_KT * 185200.0  # (m/s per m) → kt per 100 nm   (= 360000)

# Min zone half-thickness for visibility
_MIN_ZONE_HALF_FT = 500
# Max pressure gap for grouping
_ZONE_MAX_PRESSURE_GAP_HPA = 100


def _e_parameter(hws: float, vws: float) -> float:
    """CloudPath E-Shear parameter ``E = (5·HWS + VWS² + 42) / 4``.

    HWS in kt/100nm, VWS in kt/1000ft. Single definition of the formula so it
    can be value-tested directly (see tests/test_e_shear.py).
    """
    return (5.0 * hws + vws * vws + 42.0) / 4.0


def _classify_e_risk(e_val: float) -> CATRiskLevel:
    if e_val >= _E_SEVERE:
        return CATRiskLevel.SEVERE
    if e_val >= _E_MODERATE:
        return CATRiskLevel.MODERATE
    if e_val >= _E_LIGHT:
        return CATRiskLevel.LIGHT
    return CATRiskLevel.NONE


def _wind_to_uv(speed_kt: float, direction_deg: float) -> tuple[float, float]:
    """Convert wind speed/direction to U/V components in m/s."""
    speed_ms = speed_kt * _KT_TO_MS
    rad = math.radians(direction_deg)
    u = -speed_ms * math.sin(rad)
    v = -speed_ms * math.cos(rad)
    return u, v


def compute_e_shear_per_sounding(
    levels: list[DerivedLevel],
    hws_at_level: dict[int, float] | None = None,
) -> list[CATRiskLayer]:
    """Compute E-Shear turbulence layers for a single sounding.

    Args:
        levels: Derived levels with wind and altitude data.
        hws_at_level: Pre-computed horizontal wind shear per pressure level
            (from adjacent route points). If None, HWS is assumed 0.

    Returns:
        List of CATRiskLayer with E-Shear risk classification.
    """
    if len(levels) < 2:
        return []

    # Compute VWS between adjacent levels and E parameter per level
    e_levels: list[tuple[DerivedLevel, CATRiskLevel, float]] = []

    for i in range(1, len(levels)):
        lv = levels[i]
        prev = levels[i - 1]

        if (lv.altitude_ft is None or prev.altitude_ft is None
                or lv.wind_speed_kt is None or prev.wind_speed_kt is None
                or lv.wind_direction_deg is None or prev.wind_direction_deg is None):
            continue

        dz = (lv.altitude_ft - prev.altitude_ft) * 0.3048  # ft → m
        if dz <= 0:
            continue

        u1, v1 = _wind_to_uv(prev.wind_speed_kt, prev.wind_direction_deg)
        u2, v2 = _wind_to_uv(lv.wind_speed_kt, lv.wind_direction_deg)

        du_dz = (u2 - u1) / dz
        dv_dz = (v2 - v1) / dz

        # VWS: magnitude of vertical wind shear vector, scaled
        vws = math.sqrt(du_dz**2 + dv_dz**2) * _VWS_SCALE

        # HWS from pre-computed horizontal shear (or 0)
        hws = 0.0
        if hws_at_level is not None:
            hws = hws_at_level.get(lv.pressure_hpa, 0.0)

        # E parameter
        e_val = _e_parameter(hws, vws)

        risk = _classify_e_risk(e_val)
        if risk == CATRiskLevel.NONE:
            continue

        e_levels.append((lv, risk, e_val))

    return _group_e_shear_levels(e_levels)


def compute_hws_between_points(
    levels_a: list[DerivedLevel],
    levels_b: list[DerivedLevel],
    distance_nm: float,
) -> dict[int, float]:
    """Compute horizontal wind shear between two adjacent route points.

    Returns a dict mapping pressure_hpa → HWS value for matching levels.
    """
    if distance_nm <= 0:
        return {}

    distance_m = distance_nm * 1852.0

    # Index levels_b by pressure for fast lookup
    b_by_pressure: dict[int, DerivedLevel] = {
        lv.pressure_hpa: lv for lv in levels_b
        if lv.wind_speed_kt is not None and lv.wind_direction_deg is not None
    }

    hws: dict[int, float] = {}
    for lv_a in levels_a:
        if (lv_a.wind_speed_kt is None or lv_a.wind_direction_deg is None):
            continue
        lv_b = b_by_pressure.get(lv_a.pressure_hpa)
        if lv_b is None:
            continue

        ua, va = _wind_to_uv(lv_a.wind_speed_kt, lv_a.wind_direction_deg)
        ub, vb = _wind_to_uv(lv_b.wind_speed_kt, lv_b.wind_direction_deg)

        du_dx = (ub - ua) / distance_m
        dv_dx = (vb - va) / distance_m

        hws[lv_a.pressure_hpa] = math.sqrt(du_dx**2 + dv_dx**2) * _HWS_SCALE

    return hws


def _group_e_shear_levels(
    e_levels: list[tuple[DerivedLevel, CATRiskLevel, float]],
) -> list[CATRiskLayer]:
    """Group adjacent E-Shear levels into risk layers, split by severity.

    After adjacency grouping (pressure gap ≤ 100 hPa), each group is split
    into sub-layers by severity tier so that each layer's risk accurately
    reflects its altitude range.
    """
    if not e_levels:
        return []

    groups: list[list[tuple[DerivedLevel, CATRiskLevel, float]]] = []
    current: list[tuple[DerivedLevel, CATRiskLevel, float]] = [e_levels[0]]

    for item in e_levels[1:]:
        prev_lv = current[-1][0]
        this_lv = item[0]
        if abs(prev_lv.pressure_hpa - this_lv.pressure_hpa) <= _ZONE_MAX_PRESSURE_GAP_HPA:
            current.append(item)
        else:
            groups.append(current)
            current = [item]
    groups.append(current)

    # Split each adjacency group into sub-layers by severity tier
    layers: list[CATRiskLayer] = []
    for group in groups:
        layers.extend(_split_e_shear_by_severity(group))

    return layers


def _split_e_shear_by_severity(
    items: list[tuple[DerivedLevel, CATRiskLevel, float]],
) -> list[CATRiskLayer]:
    """Split a group of adjacent levels into sub-layers by severity tier."""
    result: list[CATRiskLayer] = []
    run: list[tuple[DerivedLevel, CATRiskLevel, float]] = [items[0]]

    for item in items[1:]:
        if item[1] == run[-1][1]:
            run.append(item)
        else:
            result.append(_build_e_shear_layer(run))
            run = [item]

    result.append(_build_e_shear_layer(run))
    return result


def _build_e_shear_layer(
    items: list[tuple[DerivedLevel, CATRiskLevel, float]],
) -> CATRiskLayer:
    """Build a CATRiskLayer from grouped E-Shear levels of same severity."""
    risk = items[0][1]

    altitudes = [lv.altitude_ft for lv, _, _ in items if lv.altitude_ft is not None]
    base_ft = min(altitudes)
    top_ft = max(altitudes)

    # Expand thin zones
    if top_ft - base_ft < 2 * _MIN_ZONE_HALF_FT:
        mid = (top_ft + base_ft) / 2
        base_ft = mid - _MIN_ZONE_HALF_FT
        top_ft = mid + _MIN_ZONE_HALF_FT

    return CATRiskLayer(
        base_ft=round(base_ft),
        top_ft=round(top_ft),
        base_pressure_hpa=items[0][0].pressure_hpa,
        top_pressure_hpa=items[-1][0].pressure_hpa,
        risk=risk,
    )
