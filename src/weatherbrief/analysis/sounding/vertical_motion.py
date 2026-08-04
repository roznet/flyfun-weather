"""Vertical motion analysis and turbulence indicators.

Computes Richardson Number, Brunt-Vaisala frequency, classifies vertical
motion profiles, and assesses clear-air turbulence (CAT) risk from
NWP omega/w and derived stability indicators.
"""

from __future__ import annotations

import logging

import metpy.calc as mpcalc
import numpy as np

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

# Richardson number thresholds for CAT risk — classical Miles-Howard tiers.
_RI_SEVERE = 0.25
_RI_MODERATE = 0.5
_RI_LIGHT = 1.0

# Altitude-dependent loosening of those tiers (#533).
#
# NWP-derived Ri carries a systematic positive bias: model vertical resolution
# is too coarse to resolve the thin shear sheets (100–300 m) where KH
# instability develops, so computed Ri reads too high. That bias is a function
# of *layer thickness*, which is a function of altitude — 25 hPa spans ~230 m
# at 950 hPa but ~800 m at 300 hPa. The previous flat 0.5/1.0/2.0 calibration
# applied the upper-troposphere correction unchanged in the boundary layer,
# where the levels are already at the shear-layer scale, inflating every tier
# by one and painting "Severe CAT" across summer-afternoon low-level profiles.
#
# So: classical tiers unchanged at or below _RI_LOOSEN_BASE_FT, ramping to
# _RI_LOOSEN_MAX× (i.e. the old 0.5/1.0/2.0) at _RI_LOOSEN_TOP_FT and above.
# The ramp rather than a step avoids a cliff where two adjacent levels either
# side of a fixed altitude classify differently on the same Ri.
_RI_LOOSEN_BASE_FT = 10000.0
_RI_LOOSEN_TOP_FT = 20000.0
_RI_LOOSEN_MAX = 2.0

# Surface well-mixed (convective boundary) layer detection (#533).
#
# A surface-rooted layer whose temperature lapse rate is at or near the dry
# adiabat (9.76 °C/km) is convectively mixed. Shear inside it is boundary-layer
# roughness — thermals and mechanical mixing — not the sheet-like KH
# instability the Ri diagnostic is calibrated for, and flagging it as CAT
# paints summer-afternoon noise at the bottom of every cross-section. This
# generalises the §8(c) guard, which only caught *negative* Ri at the two
# lowest levels: a 3,000–4,000 ft deep mixed layer with small-positive Ri
# sailed straight past it.
_MIXED_LAYER_LAPSE_C_PER_KM = 8.8  # ≈ 2.7 °C/1000 ft
_MIXED_LAYER_MAX_DEPTH_FT = 10000.0

# Fallback boundary-layer depth (AGL) used to tag CAT layers as boundary-layer
# origin when no well-mixed layer is detected (stable/nocturnal BL). Layers
# lying wholly below this are still real — low-level wind shear on a frontal
# day is a genuine GA hazard — but they do not bypass the route-percentage
# gate the way free-atmosphere severe CAT does.
_BL_FALLBACK_DEPTH_FT = 5000.0

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

    if profile.height is not None:
        heights_m = profile.height.to("meter").magnitude
    else:
        heights_m = np.array([
            _pressure_to_altitude_ft(p) / M_TO_FT for p in pressures
        ])

    # Compute potential temperature at each level (single vectorized call —
    # MetPy propagates NaN per element, matching the old per-level guards)
    try:
        theta = mpcalc.potential_temperature(
            profile.pressure, profile.temperature,
        ).to("kelvin").magnitude
    except Exception:
        logger.debug("Potential temperature failed", exc_info=True)
        theta = np.full(len(pressures), np.nan)

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

            # Negative Ri (N² < 0, statically unstable layer) is stored too:
            # a convectively unstable shear layer is the most turbulent case
            # and must not read as "no data" downstream.
            if shear_sq > _MIN_SHEAR_SQ:
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


def _ri_threshold_scale(altitude_ft: float) -> float:
    """Multiplier applied to the classical Ri tiers at *altitude_ft*.

    1.0 (classical) at or below 10,000 ft, ramping linearly to 2.0 (the old
    flat calibration) at 20,000 ft and above. See ``_RI_LOOSEN_BASE_FT``.
    """
    if altitude_ft <= _RI_LOOSEN_BASE_FT:
        return 1.0
    if altitude_ft >= _RI_LOOSEN_TOP_FT:
        return _RI_LOOSEN_MAX
    frac = (altitude_ft - _RI_LOOSEN_BASE_FT) / (_RI_LOOSEN_TOP_FT - _RI_LOOSEN_BASE_FT)
    return 1.0 + frac * (_RI_LOOSEN_MAX - 1.0)


def _classify_cat_risk(ri: float, altitude_ft: float) -> CATRiskLevel:
    """Classify CAT risk from Richardson number at a given altitude.

    Thresholds are the classical 0.25/0.5/1.0 scaled by
    ``_ri_threshold_scale(altitude_ft)`` — unchanged in the lower troposphere,
    loosened aloft where model layer thickness genuinely hides the shear sheet.

    Negative Ri means N² < 0 — static (convective) instability rather than
    shear-driven KH instability. Capped at MODERATE: turbulence intensity in
    that regime is buoyancy-driven and owned by the convective tier, and
    superadiabatic layers are common in daytime profiles.
    """
    if ri < 0:
        return CATRiskLevel.MODERATE
    scale = _ri_threshold_scale(altitude_ft)
    if ri < _RI_SEVERE * scale:
        return CATRiskLevel.SEVERE
    if ri < _RI_MODERATE * scale:
        return CATRiskLevel.MODERATE
    if ri < _RI_LIGHT * scale:
        return CATRiskLevel.LIGHT
    return CATRiskLevel.NONE


def mixed_layer_top_index(derived_levels: list[DerivedLevel]) -> int:
    """Index of the highest level still inside the surface well-mixed layer.

    Walks up from the surface while each layer's temperature lapse rate is at
    or above ``_MIXED_LAYER_LAPSE_C_PER_KM`` (i.e. at/near dry-adiabatic),
    stopping at the first stable layer or once the mixed layer would exceed
    ``_MIXED_LAYER_MAX_DEPTH_FT`` above the lowest level.

    Returns 0 when the surface layer is not well mixed — nothing is suppressed
    in that case, since Ri is only ever stored from index 1 upwards.

    Note the index convention: ``lapse_rate_c_per_km`` on level *i* describes
    the layer *above* it (i → i+1), whereas ``richardson_number`` on level
    *i+1* describes the layer *below* it — the same layer. So a returned index
    of *n* means levels 1…*n* carry the Ri of a well-mixed layer.
    """
    if not derived_levels:
        return 0

    surface_ft = derived_levels[0].altitude_ft
    if surface_ft is None:
        return 0

    top = 0
    for i in range(len(derived_levels) - 1):
        lapse = derived_levels[i].lapse_rate_c_per_km
        if lapse is None or lapse < _MIXED_LAYER_LAPSE_C_PER_KM:
            break
        next_ft = derived_levels[i + 1].altitude_ft
        if next_ft is None or (next_ft - surface_ft) > _MIXED_LAYER_MAX_DEPTH_FT:
            break
        top = i + 1
    return top


def _build_cat_layers(
    derived_levels: list[DerivedLevel],
    max_index_gap: int = 2,
) -> list[CATRiskLayer]:
    """Group adjacent low-Ri levels into CAT risk layers, split by severity.

    Qualifying levels (Ri below the LIGHT tier for their altitude) are merged
    when both the pressure gap
    (≤ 100 hPa) **and** the original-index gap (≤ *max_index_gap*) are
    small enough.  The index-gap check prevents chaining scattered low-Ri
    levels across large stable gaps that the pressure-only check misses
    (e.g. GFS 25 hPa spacing where stable levels are simply skipped).

    After adjacency grouping, each group is split into sub-layers by
    severity tier so that e.g. boundary-layer SEVERE shear doesn't paint
    an entire deep layer SEVERE when higher levels are only MODERATE.

    Levels inside the surface well-mixed layer are excluded entirely (#533),
    and the layers that survive are tagged ``boundary_layer`` when they lie
    wholly within the boundary layer.
    """
    ml_top_idx = mixed_layer_top_index(derived_levels)
    surface_ft = derived_levels[0].altitude_ft if derived_levels else None
    ml_top_ft = (
        derived_levels[ml_top_idx].altitude_ft if ml_top_idx > 0 else None
    )
    # Boundary-layer ceiling used only for tagging surviving layers: the
    # detected mixed-layer top when there is one, else a fixed AGL fallback.
    bl_top_ft: float | None = None
    if surface_ft is not None:
        bl_top_ft = max(ml_top_ft or 0.0, surface_ft + _BL_FALLBACK_DEPTH_FT)

    # Collect qualifying levels with their original index in derived_levels
    cat_levels: list[tuple[int, DerivedLevel, CATRiskLevel, float]] = []
    for idx, lv in enumerate(derived_levels):
        if lv.richardson_number is None or lv.altitude_ft is None:
            continue
        # Inside the surface well-mixed layer: convective boundary-layer
        # roughness, not KH instability. Suppressed regardless of Ri sign.
        if ml_top_idx > 0 and idx <= ml_top_idx:
            continue
        # Negative Ri at the surface-adjacent layer is the daytime
        # superadiabatic surface layer (thermals) — not CAT. Kept as a
        # fallback for profiles with no lapse-rate data to detect a mixed
        # layer from. Elevated statically-unstable layers (idx > 1) qualify.
        if lv.richardson_number < 0 and idx <= 1:
            continue
        risk = _classify_cat_risk(lv.richardson_number, lv.altitude_ft)
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
        layers.extend(_split_by_severity(items, bl_top_ft))

    return layers


def _split_by_severity(
    items: list[tuple[DerivedLevel, CATRiskLevel, float]],
    bl_top_ft: float | None = None,
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
            result.append(_build_single_cat_layer(run, bl_top_ft))
            run = [item]

    result.append(_build_single_cat_layer(run, bl_top_ft))
    return result


def _build_single_cat_layer(
    items: list[tuple[DerivedLevel, CATRiskLevel, float]],
    bl_top_ft: float | None = None,
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
        # Wholly inside the boundary layer — a layer that pokes above it is a
        # free-atmosphere feature rooted low, not BL roughness.
        boundary_layer=bl_top_ft is not None and top.altitude_ft <= bl_top_ft,
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

    # Surface well-mixed layer top (#533) — the depth over which CAT layers
    # were suppressed. Reported so the suppression is inspectable rather than
    # silently swallowing layers.
    ml_top_idx = mixed_layer_top_index(derived_levels)
    mixed_layer_top_ft = (
        derived_levels[ml_top_idx].altitude_ft if ml_top_idx > 0 else None
    )

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
        mixed_layer_top_ft=(
            round(mixed_layer_top_ft) if mixed_layer_top_ft is not None else None
        ),
    )
