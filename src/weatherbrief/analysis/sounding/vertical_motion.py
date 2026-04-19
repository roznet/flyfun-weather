"""Vertical motion analysis and turbulence indicators.

Computes Richardson Number, Brunt-Vaisala frequency, classifies vertical
motion profiles, and assesses clear-air turbulence (CAT) risk from
NWP omega/w and derived stability indicators.
"""

from __future__ import annotations

import logging

import metpy.calc as mpcalc
import numpy as np
from metpy.units import units

from weatherbrief.analysis.sounding.prepare import PreparedProfile
from weatherbrief.analysis.sounding.thermodynamics import M_TO_FT, _pressure_to_altitude_ft
from weatherbrief.models import (
    CATRiskLayer,
    CATRiskLevel,
    DerivedLevel,
    VerticalMotionAssessment,
    VerticalMotionClass,
)

logger = logging.getLogger(__name__)

# Richardson number thresholds for CAT risk.
# Loosened from classical 0.25/0.5/1.0 to compensate for the systematic
# positive bias in NWP-derived Ri: model vertical resolution (25–50 hPa
# between levels) is too coarse to resolve the thin shear layers (100–300m)
# where KH instability develops, so computed Ri is always too high.
_RI_SEVERE = 0.5
_RI_MODERATE = 1.0
_RI_LIGHT = 2.0

# Omega thresholds (Pa/s) for classification
# Typical synoptic omega: ±0.1–1.0 Pa/s; convective: >1 Pa/s
_OMEGA_QUIESCENT = 0.1  # |omega| < 0.1 Pa/s → quiescent
_OMEGA_CONVECTIVE = 1.0  # |omega| > 1 Pa/s → convective
_OMEGA_SIGNIFICANT = 0.05  # minimum for sign-change counting

# Convective contamination: mid-level (700-400 hPa) omega threshold
_CONTAMINATION_PRESSURE_MIN = 400  # hPa (top)
_CONTAMINATION_PRESSURE_MAX = 700  # hPa (bottom)
_CONTAMINATION_OMEGA = 0.5  # |omega| > 0.5 Pa/s

# Strong vertical motion threshold
_STRONG_W_FPM = 200.0

# Gravity constant
_G = 9.80665  # m/s²

# Minimum shear squared to avoid division by zero
_MIN_SHEAR_SQ = 1e-10


def compute_stability_indicators(
    profile: PreparedProfile,
    derived_levels: list[DerivedLevel],
) -> None:
    """Compute N² and Richardson number for adjacent layer pairs.

    Enriches derived_levels in-place with richardson_number and
    bv_freq_squared_per_s2 for each level (representing the layer below).
    """
    pressures = profile.pressure.to("hPa").magnitude
    temps = profile.temperature.to("degC").magnitude

    if profile.height is not None:
        heights_m = profile.height.to("meter").magnitude
    else:
        heights_m = np.array([
            _pressure_to_altitude_ft(p) / M_TO_FT for p in pressures
        ])

    # Compute potential temperature at each level
    theta = np.empty(len(pressures))
    for i in range(len(pressures)):
        try:
            pt = mpcalc.potential_temperature(
                pressures[i] * units.hPa, temps[i] * units.degC,
            )
            theta[i] = float(pt.to("kelvin").magnitude)
        except Exception:
            theta[i] = np.nan

    # Compute wind components for shear calculation
    u_vals = np.full(len(pressures), np.nan)
    v_vals = np.full(len(pressures), np.nan)
    if profile.wind_speed is not None and profile.wind_direction is not None:
        try:
            u, v = mpcalc.wind_components(profile.wind_speed, profile.wind_direction)
            u_vals = u.to("m/s").magnitude
            v_vals = v.to("m/s").magnitude
        except Exception:
            logger.debug("Failed to compute wind components", exc_info=True)

    # Per adjacent layer pair (i is lower, i+1 is upper)
    for i in range(len(pressures) - 1):
        if i + 1 >= len(derived_levels):
            break

        dz = heights_m[i + 1] - heights_m[i]
        if dz <= 0 or np.isnan(theta[i]) or np.isnan(theta[i + 1]):
            continue

        theta_mean = (theta[i] + theta[i + 1]) / 2.0
        d_theta = theta[i + 1] - theta[i]

        # Brunt-Vaisala frequency squared: N² = (g/θ) × (dθ/dz)
        n_sq = (_G / theta_mean) * (d_theta / dz)
        derived_levels[i + 1].bv_freq_squared_per_s2 = round(float(n_sq), 8)

        # Wind shear squared: S² = (du/dz)² + (dv/dz)²
        if not np.isnan(u_vals[i]) and not np.isnan(u_vals[i + 1]):
            du_dz = (u_vals[i + 1] - u_vals[i]) / dz
            dv_dz = (v_vals[i + 1] - v_vals[i]) / dz
            shear_sq = du_dz**2 + dv_dz**2

            if shear_sq > _MIN_SHEAR_SQ and n_sq >= 0:
                ri = n_sq / shear_sq
                derived_levels[i + 1].richardson_number = round(float(ri), 2)


def classify_vertical_motion(
    derived_levels: list[DerivedLevel],
) -> VerticalMotionClass:
    """Classify the vertical motion profile from omega data."""
    omega_values = [
        lv.omega_pa_s for lv in derived_levels if lv.omega_pa_s is not None
    ]
    if not omega_values:
        return VerticalMotionClass.UNAVAILABLE

    abs_omegas = [abs(o) for o in omega_values]
    max_abs = max(abs_omegas)

    # Check for convective
    if max_abs > _OMEGA_CONVECTIVE:
        return VerticalMotionClass.CONVECTIVE

    # Check for quiescent
    if max_abs < _OMEGA_QUIESCENT:
        return VerticalMotionClass.QUIESCENT

    # Count significant sign changes
    significant = [o for o in omega_values if abs(o) > _OMEGA_SIGNIFICANT]
    sign_changes = 0
    for i in range(len(significant) - 1):
        if significant[i] * significant[i + 1] < 0:
            sign_changes += 1

    if sign_changes >= 2:
        return VerticalMotionClass.OSCILLATING

    # Coherent direction: negative omega = ascent, positive = subsidence
    mean_omega = sum(omega_values) / len(omega_values)
    if mean_omega < 0:
        return VerticalMotionClass.SYNOPTIC_ASCENT
    return VerticalMotionClass.SYNOPTIC_SUBSIDENCE


def _classify_cat_risk(ri: float) -> CATRiskLevel:
    """Classify CAT risk from Richardson number."""
    if ri < _RI_SEVERE:
        return CATRiskLevel.SEVERE
    if ri < _RI_MODERATE:
        return CATRiskLevel.MODERATE
    if ri < _RI_LIGHT:
        return CATRiskLevel.LIGHT
    return CATRiskLevel.NONE


def _build_cat_layers(
    derived_levels: list[DerivedLevel],
    max_index_gap: int = 2,
) -> list[CATRiskLayer]:
    """Group adjacent low-Ri levels into CAT risk layers, split by severity.

    Qualifying levels (Ri < 2.0) are merged when both the pressure gap
    (≤ 100 hPa) **and** the original-index gap (≤ *max_index_gap*) are
    small enough.  The index-gap check prevents chaining scattered low-Ri
    levels across large stable gaps that the pressure-only check misses
    (e.g. GFS 25 hPa spacing where stable levels are simply skipped).

    After adjacency grouping, each group is split into sub-layers by
    severity tier so that e.g. boundary-layer SEVERE shear doesn't paint
    an entire deep layer SEVERE when higher levels are only MODERATE.
    """
    # Collect qualifying levels with their original index in derived_levels
    cat_levels: list[tuple[int, DerivedLevel, CATRiskLevel, float]] = []
    for idx, lv in enumerate(derived_levels):
        if lv.richardson_number is None or lv.altitude_ft is None:
            continue
        risk = _classify_cat_risk(lv.richardson_number)
        if risk == CATRiskLevel.NONE:
            continue
        cat_levels.append((idx, lv, risk, lv.richardson_number))

    if not cat_levels:
        return []

    # Group adjacent levels (pressure gap <= 100 hPa AND index gap <= max_index_gap)
    groups: list[list[tuple[int, DerivedLevel, CATRiskLevel, float]]] = []
    current: list[tuple[int, DerivedLevel, CATRiskLevel, float]] = [cat_levels[0]]

    for item in cat_levels[1:]:
        prev_idx, prev_lv = current[-1][0], current[-1][1]
        this_idx, this_lv = item[0], item[1]
        if (
            (this_idx - prev_idx) <= max_index_gap
            and abs(prev_lv.pressure_hpa - this_lv.pressure_hpa) <= 100
        ):
            current.append(item)
        else:
            groups.append(current)
            current = [item]
    groups.append(current)

    # Split each adjacency group into sub-layers by severity tier
    layers: list[CATRiskLayer] = []
    for group in groups:
        items = [(lv, risk, ri) for _, lv, risk, ri in group]
        layers.extend(_split_by_severity(items))

    return layers


def _split_by_severity(
    items: list[tuple[DerivedLevel, CATRiskLevel, float]],
) -> list[CATRiskLayer]:
    """Split a group of adjacent levels into sub-layers by severity tier.

    Adjacent levels with the same severity are merged into one sub-layer.
    This ensures each layer's risk accurately reflects its altitude range.
    """
    result: list[CATRiskLayer] = []
    run: list[tuple[DerivedLevel, CATRiskLevel, float]] = [items[0]]

    for item in items[1:]:
        if item[1] == run[-1][1]:
            run.append(item)
        else:
            result.append(_build_single_cat_layer(run))
            run = [item]

    result.append(_build_single_cat_layer(run))
    return result


def _build_single_cat_layer(
    items: list[tuple[DerivedLevel, CATRiskLevel, float]],
) -> CATRiskLayer:
    """Build a CATRiskLayer from a group of same-severity levels."""
    risk = items[0][1]
    min_ri = min(ri for _, _, ri in items)

    base = items[0][0]
    top = items[-1][0]

    return CATRiskLayer(
        base_ft=round(base.altitude_ft),
        top_ft=round(top.altitude_ft),
        base_pressure_hpa=base.pressure_hpa,
        top_pressure_hpa=top.pressure_hpa,
        richardson_number=round(min_ri, 2),
        risk=risk,
    )


def assess_vertical_motion(
    derived_levels: list[DerivedLevel],
) -> VerticalMotionAssessment:
    """Build complete vertical motion assessment from enriched derived levels."""
    classification = classify_vertical_motion(derived_levels)

    # Find max omega/w
    max_omega: float | None = None
    max_w: float | None = None
    max_w_level: float | None = None

    for lv in derived_levels:
        if lv.omega_pa_s is not None:
            if max_omega is None or abs(lv.omega_pa_s) > abs(max_omega):
                max_omega = lv.omega_pa_s
        if lv.w_fpm is not None:
            if max_w is None or abs(lv.w_fpm) > abs(max_w):
                max_w = lv.w_fpm
                max_w_level = lv.altitude_ft

    # Build CAT risk layers from Ri
    cat_layers = _build_cat_layers(derived_levels)

    # Detect convective contamination: mid-level |omega| > threshold
    convective_contamination = False
    for lv in derived_levels:
        if lv.omega_pa_s is not None and lv.pressure_hpa is not None:
            if (_CONTAMINATION_PRESSURE_MIN <= lv.pressure_hpa <= _CONTAMINATION_PRESSURE_MAX
                    and abs(lv.omega_pa_s) > _CONTAMINATION_OMEGA):
                convective_contamination = True
                break

    return VerticalMotionAssessment(
        classification=classification,
        max_omega_pa_s=round(max_omega, 4) if max_omega is not None else None,
        max_w_fpm=round(max_w, 1) if max_w is not None else None,
        max_w_level_ft=round(max_w_level) if max_w_level is not None else None,
        cat_risk_layers=cat_layers,
        convective_contamination=convective_contamination,
    )
