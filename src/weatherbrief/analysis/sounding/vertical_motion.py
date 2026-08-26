"""Vertical motion analysis and turbulence indicators.

Computes Richardson Number, Brunt-Vaisala frequency, classifies vertical
motion profiles, and assesses clear-air turbulence (CAT) risk from
NWP omega/w and derived stability indicators.
"""

from __future__ import annotations

import logging
import math

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

# Thickness-dependent loosening of those tiers (#533 as an altitude ramp, #539
# as the thickness law that subsumes it).
#
# NWP-derived Ri carries a systematic positive bias: model vertical resolution
# is too coarse to resolve the thin shear sheets (100–300 m) where KH
# instability develops, so a Ri averaged over a thick layer reads too high.
# The bias is a function of the *layer thickness the Ri was computed over* —
# nothing else. #533 scaled by altitude as a proxy for it, which is right only
# for one level set: it gave a GFS 25 hPa layer and an ECMWF 75 hPa layer at
# the same altitude the same multiplier, and it was calibrated on ICON/GFS/
# ECMWF over a single route with UKMO and Météo-France never spot-checked.
#
# Since #534 every Ri carries its own physical extent (the level pair it was
# differenced over), so the correction is applied to the thing it is actually
# a function of: scale = clamp(Δz / _RI_DZ_REF_FT, 1.0, _RI_DZ_SCALE_MAX).
#
# Calibration. _RI_DZ_REF_FT is the thickness at which the classical tiers
# stand unscaled — the top of the 100–300 m shear-sheet band the correction
# exists for, i.e. ~1,000 ft. That single constant reproduces both of the
# altitude ramp's own knees on the level set it was calibrated against
# (GFS/ICON-EU-GRIB, 25 hPa below 500 hPa then 50 hPa):
#
#   - the ramp's 10,000 ft knee is exactly where that spacing passes 1,000 ft
#     (700→675 hPa spans ~935 ft, mid 10,350 ft — old scale 1.03, new 1.00);
#   - the ramp's 20,000 ft ceiling is exactly where the spacing steps to
#     50 hPa (500→450 hPa spans ~2,520 ft, mid 19,550 ft — old 1.96, new 2.00,
#     the cap).
#
# So the ramp was the thickness law all along, read through one model's
# geometry. Expressing it directly self-corrects per model with no per-model
# configuration: ECMWF's coarse low-level levels are loosened where the ramp
# left them classical, GFS's 25 hPa mid-troposphere layers stay near-classical
# where the ramp loosened them on altitude alone, and UKMO/Météo-France/GEM
# pick up the correction their 50 hPa spacing warrants without ever having
# been spot-checked.
#
# The floor of 1.0 keeps the tiers from ever going *below* Miles-Howard: the
# classical value is the physical criterion, and a finer-than-reference layer
# is no reason to claim turbulence at a Ri the theory calls stable. The cap
# bounds the other end — a Ri differenced across a 150 hPa gap is a bulk
# Richardson number of dubious meaning, and it should not be able to loosen
# the tiers without limit.
_RI_DZ_REF_FT = 1000.0
_RI_DZ_SCALE_MAX = 2.0

# Surface well-mixed (convective boundary) layer detection (#533).
#
# Inside a convectively mixed layer virtual potential temperature is
# near-constant with height, so the standard parcel criterion applies: the
# mixed layer extends while θv stays within a small excess of its surface
# value (Holtslag-style +0.5 K over land). Shear inside it is boundary-layer
# roughness — thermals and mechanical mixing — not the sheet-like KH
# instability the Ri diagnostic is calibrated for, and flagging it as CAT
# paints summer-afternoon noise at the bottom of every cross-section. This
# generalises the §8(c) guard, which only caught *negative* Ri at the two
# lowest levels: a 3,000–4,000 ft deep mixed layer with small-positive Ri
# sailed straight past it.
#
# A per-layer lapse-rate walk (each layer ≥ _MIXED_LAYER_LAPSE_C_PER_KM) is
# kept as the fallback when temperatures are unavailable. It is NOT the
# primary detector because it is brittle: one interpolation kink below the
# dry adiabat truncates the walk, and on the #533 pack that read a mixed
# layer reaching 3,000–4,000 ft as ~1,000 ft deep at a third of the route
# points. The θv criterion integrates over the column instead of gating on
# every 25 hPa slice.
_MIXED_LAYER_THETA_V_EXCESS_K = 0.5
_MIXED_LAYER_LAPSE_C_PER_KM = 8.8  # ≈ 2.7 °C/1000 ft (fallback walk only)
_MIXED_LAYER_MAX_DEPTH_FT = 10000.0

# Fallback boundary-layer depth (AGL) used to tag CAT layers as boundary-layer
# origin when no well-mixed layer is detected (stable/nocturnal BL). Layers
# lying wholly below this are still real — low-level wind shear on a frontal
# day is a genuine GA hazard — but they do not bypass the route-percentage
# gate the way free-atmosphere severe CAT does.
#
# "AGL" is measured from the MODEL's own ground, resolved from its surface
# pressure against the column's own heights (#541). Before that it was
# measured from ``derived_levels[0]`` — the lowest *delivered* pressure level,
# which is not the ground: nothing in the fetch or the analysis path clips
# sub-surface levels, so over an Alpine crossing the column still starts at
# 1000 hPa several thousand feet inside the mountain and the "AGL" fallback
# was effectively MSL-anchored. See meteorology-decisions §29.
_BL_FALLBACK_DEPTH_FT = 5000.0

# Bounds for a usable surface pressure. Outside these the value is a decode
# artefact rather than a surface, and the ground datum falls back to the
# lowest delivered level (i.e. the pre-#541 behaviour).
_SURFACE_PRESSURE_MIN_HPA = 300.0
_SURFACE_PRESSURE_MAX_HPA = 1100.0

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


def _ri_threshold_scale(layer_thickness_ft: float) -> float:
    """Multiplier applied to the classical Ri tiers for a layer of *Δz*.

    ``clamp(Δz / 1,000 ft, 1.0, 2.0)``: classical Miles-Howard at and below
    the shear-sheet reference thickness, loosening in proportion to how much
    the layer smooths the shear away, capped at the ×2 the #533 altitude ramp
    reached aloft. See ``_RI_DZ_REF_FT`` for the calibration.
    """
    scale = layer_thickness_ft / _RI_DZ_REF_FT
    if scale <= 1.0:
        return 1.0
    return min(scale, _RI_DZ_SCALE_MAX)


def _layer_thickness_ft(below: DerivedLevel, level: DerivedLevel) -> float:
    """Depth (ft) of the layer a level's Richardson number was computed over.

    ``compute_stability_indicators`` differences *below* → *level*, so that
    pair is the Ri's own physical extent. Falls back to a standard-atmosphere
    estimate from the pressures when the lower level carries no altitude —
    a coarse model must not silently read as a fine one, and the thickness is
    only ever used as a threshold multiplier.
    """
    if below.altitude_ft is not None and level.altitude_ft is not None:
        return level.altitude_ft - below.altitude_ft
    return _pressure_to_altitude_ft(level.pressure_hpa) - _pressure_to_altitude_ft(
        below.pressure_hpa
    )


def _classify_cat_risk(ri: float, layer_thickness_ft: float) -> CATRiskLevel:
    """Classify CAT risk from a Richardson number computed over *Δz*.

    Thresholds are the classical 0.25/0.5/1.0 scaled by
    ``_ri_threshold_scale(layer_thickness_ft)`` — unchanged where the model
    resolves the shear sheet, loosened in proportion to how thick the layer
    the Ri was averaged over actually is.

    Negative Ri means N² < 0 — static (convective) instability rather than
    shear-driven KH instability. Capped at MODERATE: turbulence intensity in
    that regime is buoyancy-driven and owned by the convective tier, and
    superadiabatic layers are common in daytime profiles.
    """
    if ri < 0:
        return CATRiskLevel.MODERATE
    scale = _ri_threshold_scale(layer_thickness_ft)
    if ri < _RI_SEVERE * scale:
        return CATRiskLevel.SEVERE
    if ri < _RI_MODERATE * scale:
        return CATRiskLevel.MODERATE
    if ri < _RI_LIGHT * scale:
        return CATRiskLevel.LIGHT
    return CATRiskLevel.NONE


def _theta_v_k(lv: DerivedLevel) -> float | None:
    """Virtual potential temperature (K) of a level, plain math (no MetPy).

    Standalone runs this per level over tens of thousands of soundings on a
    single core, so scalar MetPy calls are deliberately avoided. Bolton (1980)
    vapour pressure from dewpoint; dry θ when dewpoint is missing (inside a
    mixed layer mixing ratio is near-constant, so the dry fallback shifts the
    profile, not the gradient the detector keys on).
    """
    if lv.temperature_c is None:
        return None
    theta = (lv.temperature_c + 273.15) * (1000.0 / lv.pressure_hpa) ** 0.2854
    if lv.dewpoint_c is None:
        return theta
    e_hpa = 6.112 * math.exp(17.67 * lv.dewpoint_c / (lv.dewpoint_c + 243.5))
    e_hpa = min(e_hpa, 0.9 * lv.pressure_hpa)
    r = 0.622 * e_hpa / (lv.pressure_hpa - e_hpa)
    return theta * (1.0 + 0.61 * r)


def ground_altitude_ft(
    derived_levels: list[DerivedLevel],
    surface_pressure_hpa: float | None,
) -> float | None:
    """Altitude (ft) of the model's own ground, in the column's height datum.

    The sounding path never clips sub-surface levels: Open-Meteo serves every
    requested pressure level whatever the terrain, the GRIB decoders replace
    levels one for one, and ``prepare_profile`` filters only on missing
    temperature. So ``derived_levels[0]`` is the lowest *delivered* level, not
    the surface — over the Alps it can sit thousands of feet inside the
    mountain (#541).

    The model's surface pressure is the anchor, read against the column's own
    geopotential heights by log-pressure interpolation. That keeps the answer
    in the *same datum as the levels it is compared against*, which is the
    whole point: SRTM terrain would be truer ground but a different surface
    from the one the model integrated its shear over, and mixing the two would
    put "ground" above levels the model considers free atmosphere. See
    meteorology-decisions §29.

    Returns None when there is no usable surface pressure or fewer than two
    levels carry an altitude — callers then keep the pre-#541 behaviour of
    treating the lowest delivered level as the surface.
    """
    if surface_pressure_hpa is None:
        return None
    if not (
        _SURFACE_PRESSURE_MIN_HPA
        <= surface_pressure_hpa
        <= _SURFACE_PRESSURE_MAX_HPA
    ):
        return None

    # Sorted rather than assumed: prepare_profile emits descending pressure,
    # but this helper is public and a caller could hand it any column.
    pts = sorted(
        (
            (float(lv.pressure_hpa), float(lv.altitude_ft))
            for lv in derived_levels
            if lv.altitude_ft is not None and lv.pressure_hpa > 0
        ),
        key=lambda pt: -pt[0],
    )
    if len(pts) < 2:
        return None

    # Interpolate on log-pressure — the hypsometric relation is linear there,
    # so the bracketing pair carries its own mean layer temperature and no
    # standard atmosphere is assumed. Outside the column the same pair is
    # extrapolated from the nearest end: below the lowest level that is the
    # ordinary flat-terrain case (a sea-level field sits under 1000 hPa),
    # while above the highest is unreachable for any real surface pressure
    # and just avoids reading the wrong end of the profile.
    if surface_pressure_hpa >= pts[0][0]:
        lo, hi = pts[0], pts[1]
    elif surface_pressure_hpa <= pts[-1][0]:
        lo, hi = pts[-2], pts[-1]
    else:
        lo, hi = pts[0], pts[1]
        for below, above in zip(pts, pts[1:]):
            if below[0] >= surface_pressure_hpa >= above[0]:
                lo, hi = below, above
                break
    if lo[0] == hi[0]:
        return lo[1]
    frac = math.log(lo[0] / surface_pressure_hpa) / math.log(lo[0] / hi[0])
    return lo[1] + frac * (hi[1] - lo[1])


def ground_level_index(
    derived_levels: list[DerivedLevel],
    ground_ft: float | None,
) -> int:
    """Index of the lowest level at or above *ground_ft*.

    Levels below it are the model's own below-ground extrapolation: their
    temperature and wind are a downward continuation of the free atmosphere,
    so a Richardson number differenced across them describes nothing that
    exists. Returns 0 when the ground is unknown or already at/below the
    lowest delivered level, which is the flat-route case and leaves the
    pre-#541 behaviour untouched.
    """
    if ground_ft is None:
        return 0
    for idx, lv in enumerate(derived_levels):
        if lv.altitude_ft is not None and lv.altitude_ft >= ground_ft:
            return idx
    # Every delivered level is below ground — pathological, but returning the
    # last index would silently suppress the whole column. Keep index 0 and
    # let the ordinary suppression rules apply.
    return 0


def mixed_layer_top_index(
    derived_levels: list[DerivedLevel],
    start_idx: int = 0,
) -> int:
    """Index of the highest level still inside the surface well-mixed layer.

    Parcel criterion: walks up from the surface while each level's virtual
    potential temperature stays within ``_MIXED_LAYER_THETA_V_EXCESS_K`` of
    the surface value (θv is near-constant in a mixed layer and climbs
    through the capping stable air), stopping at the first exceedance or once
    the layer would exceed ``_MIXED_LAYER_MAX_DEPTH_FT`` above the surface
    level. Falls back to the per-layer lapse-rate walk when temperatures are
    unavailable.

    *start_idx* is the lowest level at or above the model's ground
    (``ground_level_index``); it is the surface parcel and the depth datum.
    It defaults to 0 — the lowest delivered level — which is right only where
    that level is above ground. Over high terrain the surface parcel would
    otherwise be a below-ground extrapolation, warm and moist by construction,
    and the θv walk would read the whole sub-surface column as "mixed" (#541).

    Returns *start_idx* when the surface layer is not well mixed — nothing is
    suppressed in that case beyond the sub-surface levels themselves.

    Index convention: ``richardson_number`` on level *i* describes the layer
    below it (*i-1* → *i*), so a returned index of *n* means levels
    *start_idx*+1…*n* carry the Ri of well-mixed layers.
    """
    if start_idx >= len(derived_levels):
        return start_idx

    surface_ft = derived_levels[start_idx].altitude_ft
    if surface_ft is None:
        return start_idx

    theta_v0 = _theta_v_k(derived_levels[start_idx])
    if theta_v0 is None:
        return _mixed_layer_top_index_lapse(derived_levels, start_idx)

    top = start_idx
    for i in range(start_idx + 1, len(derived_levels)):
        lv = derived_levels[i]
        if lv.altitude_ft is None or (lv.altitude_ft - surface_ft) > _MIXED_LAYER_MAX_DEPTH_FT:
            break
        theta_v = _theta_v_k(lv)
        if theta_v is None or theta_v > theta_v0 + _MIXED_LAYER_THETA_V_EXCESS_K:
            break
        top = i
    return top


def _mixed_layer_top_index_lapse(
    derived_levels: list[DerivedLevel],
    start_idx: int = 0,
) -> int:
    """Fallback detector: per-layer lapse-rate walk (used when θv can't be
    computed). Brittle — a single sub-adiabatic kink truncates it — which is
    why it is no longer the primary method (#534 follow-up review)."""
    if start_idx >= len(derived_levels):
        return start_idx
    surface_ft = derived_levels[start_idx].altitude_ft
    top = start_idx
    for i in range(start_idx, len(derived_levels) - 1):
        lapse = derived_levels[i].lapse_rate_c_per_km
        if lapse is None or lapse < _MIXED_LAYER_LAPSE_C_PER_KM:
            break
        next_ft = derived_levels[i + 1].altitude_ft
        if next_ft is None or surface_ft is None or (
            next_ft - surface_ft
        ) > _MIXED_LAYER_MAX_DEPTH_FT:
            break
        top = i + 1
    return top


def _build_cat_layers(
    derived_levels: list[DerivedLevel],
    ml_top_idx: int,
    ground_idx: int = 0,
    ground_ft: float | None = None,
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

    Levels inside the surface well-mixed layer (up to *ml_top_idx*, from
    ``mixed_layer_top_index``) are excluded entirely (#533), and the layers
    that survive are tagged ``boundary_layer`` when they lie wholly within
    the boundary layer.

    *ground_idx* / *ground_ft* locate the model's own surface (#541). Levels
    at or below *ground_idx* are excluded: their Ri is differenced against a
    below-ground extrapolation, and a "shear layer" painted inside a mountain
    is neither a hazard nor a thing. *ground_ft* is the AGL datum for the
    boundary-layer ceiling. Both default to the lowest delivered level, which
    is the pre-#541 behaviour and stays right wherever that level is above
    ground — i.e. every flat route.
    """
    surface_ft = ground_ft
    if surface_ft is None and derived_levels:
        surface_ft = derived_levels[ground_idx].altitude_ft
    ml_top_ft = (
        derived_levels[ml_top_idx].altitude_ft if ml_top_idx > ground_idx else None
    )
    # Boundary-layer ceiling, used only for tagging the layers that survive
    # suppression. Deliberately the HIGHER of the detected mixed-layer top and
    # the fixed AGL fallback, not an either/or (#534 review):
    #
    #   - The two only differ when a mixed layer was actually detected, i.e. a
    #     convective daytime BL. A frontal low-level-wind-shear day has no
    #     well-mixed surface layer, so ml_top_ft is None and the fallback is
    #     what applies — the case the fallback exists for.
    #   - Where they do differ, a shear sheet between the mixed-layer top and
    #     5,000 ft AGL is most often that layer's own entrainment/capping
    #     shear — a BL phenomenon, so tagging it BL is right.
    #   - Taking only ml_top_ft would re-open the #533 failure mode: with a
    #     shallow detected mixed layer (say 1,200 ft), a severe sheet at
    #     2,000 ft would read as free atmosphere and RED the whole route off
    #     one point of seventeen.
    #
    # The tag is not a mute: a BL-tagged severe layer is still floored at
    # AMBER, still REDs past the route-percentage threshold, and still paints
    # its band in the cross-section.
    bl_top_ft: float | None = None
    if surface_ft is not None:
        bl_top_ft = max(ml_top_ft or 0.0, surface_ft + _BL_FALLBACK_DEPTH_FT)

    # Collect qualifying levels with their original index in derived_levels.
    # ``richardson_number`` on level *idx* describes the layer BELOW it
    # (idx-1 → idx), so each qualifying level carries its base level too and
    # the resulting CAT layer spans the physical layer, not just the upper
    # bound. Building layers from the flagged levels' own altitudes emitted
    # zero-thickness layers (base == top) whenever a single level qualified —
    # on the #533 pack that put a severe "layer" at 2,523–2,523 ft over a
    # 2,500 ft cruise, silently missing it by 23 ft.
    cat_levels: list[tuple[int, DerivedLevel, DerivedLevel, CATRiskLevel, float]] = []
    for idx, lv in enumerate(derived_levels):
        if lv.richardson_number is None or lv.altitude_ft is None:
            continue
        # At or below the model's ground, or inside the surface well-mixed
        # layer: the first is a below-ground extrapolation and the second is
        # convective boundary-layer roughness rather than KH instability.
        # Suppressed regardless of Ri sign. ``ml_top_idx >= ground_idx`` by
        # construction, so the one cut covers both; the level at *ground_idx*
        # goes too, because its Ri is differenced against the last
        # below-ground level. The ``cut > 0`` guard keeps index 0 eligible
        # when neither applies — real columns never store a Ri there, but a
        # hand-built profile may.
        cut = max(ml_top_idx, ground_idx)
        if cut > 0 and idx <= cut:
            continue
        # Negative Ri at the surface-adjacent layer is the daytime
        # superadiabatic surface layer (thermals) — not CAT. Kept as a
        # fallback for profiles with no lapse-rate data to detect a mixed
        # layer from. Elevated statically-unstable layers qualify.
        if lv.richardson_number < 0 and idx <= ground_idx + 1:
            continue
        base_lv = lv
        # Thickness of the layer the Ri was differenced over, used to scale
        # the tiers (#539). Deliberately read from the Ri's OWN level pair,
        # not from the emitted layer's geometry below: how much the model
        # smoothed the shear away is a property of the difference, while the
        # base extension is a question about where to paint the band. Where
        # the two disagree — the sparse-column case — the Ri really was taken
        # across the wide gap, and the cap in ``_ri_threshold_scale`` is what
        # bounds a bulk Ri of dubious meaning.
        thickness_ft = _RI_DZ_REF_FT
        if idx > 0:
            below = derived_levels[idx - 1]
            thickness_ft = _layer_thickness_ft(below, lv)
            # Same 100 hPa adjacency criterion as the grouping below: across a
            # sparse column (missing levels), extending the base would paint a
            # multi-thousand-ft band from one Ri of dubious meaning — keep the
            # old upper-bound-only behaviour there.
            if (
                below.altitude_ft is not None
                and (below.pressure_hpa - lv.pressure_hpa) <= 100
            ):
                base_lv = below
        risk = _classify_cat_risk(lv.richardson_number, thickness_ft)
        if risk == CATRiskLevel.NONE:
            continue
        cat_levels.append((idx, base_lv, lv, risk, lv.richardson_number))

    if not cat_levels:
        return []

    # Group adjacent levels (pressure gap <= 100 hPa AND index gap <= max_index_gap)
    groups: list[list[tuple[int, DerivedLevel, DerivedLevel, CATRiskLevel, float]]] = []
    current: list[tuple[int, DerivedLevel, DerivedLevel, CATRiskLevel, float]] = [cat_levels[0]]

    for item in cat_levels[1:]:
        prev_idx, prev_lv = current[-1][0], current[-1][2]
        this_idx, this_lv = item[0], item[2]
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
        items = [(base_lv, lv, risk, ri) for _, base_lv, lv, risk, ri in group]
        layers.extend(_split_by_severity(items, bl_top_ft))

    return layers


def _split_by_severity(
    items: list[tuple[DerivedLevel, DerivedLevel, CATRiskLevel, float]],
    bl_top_ft: float | None = None,
) -> list[CATRiskLayer]:
    """Split a group of adjacent levels into sub-layers by severity tier.

    Adjacent levels with the same severity are merged into one sub-layer.
    This ensures each layer's risk accurately reflects its altitude range.
    """
    result: list[CATRiskLayer] = []
    run: list[tuple[DerivedLevel, DerivedLevel, CATRiskLevel, float]] = [items[0]]

    for item in items[1:]:
        if item[2] == run[-1][2]:
            run.append(item)
        else:
            result.append(_build_single_cat_layer(run, bl_top_ft))
            run = [item]

    result.append(_build_single_cat_layer(run, bl_top_ft))
    return result


def _build_single_cat_layer(
    items: list[tuple[DerivedLevel, DerivedLevel, CATRiskLevel, float]],
    bl_top_ft: float | None = None,
) -> CATRiskLayer:
    """Build a CATRiskLayer from a group of same-severity levels.

    Each item is ``(base_lv, lv, risk, ri)`` where ``base_lv`` is the level
    below ``lv`` — the bottom of the physical layer that ``lv``'s Ri
    describes. The layer therefore spans base of the first item → the last
    item's level, so a single qualifying level still yields a layer with real
    thickness rather than a zero-thickness line at its upper bound.
    """
    risk = items[0][2]
    min_ri = min(ri for _, _, _, ri in items)

    base = items[0][0]
    top = items[-1][1]

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
    surface_pressure_hpa: float | None = None,
) -> VerticalMotionAssessment:
    """Build complete vertical motion assessment from enriched derived levels.

    *surface_pressure_hpa* is the model's own surface pressure at this point
    and hour. It locates the ground inside the column (#541) and is what makes
    the boundary-layer datum genuinely AGL over terrain; without it the lowest
    delivered pressure level stands in, which is only right where that level
    is above ground.
    """
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

    # The model's own ground (#541). Everything below is a sub-surface
    # extrapolation the fetch never clips, and every "surface" datum in this
    # function is measured from here rather than from the lowest delivered
    # pressure level. Falls back to that level when no surface pressure is
    # available, which is the pre-#541 behaviour.
    ground_ft = ground_altitude_ft(derived_levels, surface_pressure_hpa)
    ground_idx = ground_level_index(derived_levels, ground_ft)

    # Surface well-mixed layer (#533) — CAT layers inside it are suppressed,
    # and its top is reported so the suppression is inspectable rather than
    # silently swallowing layers. Resolved once and shared with the layer
    # builder rather than walked twice (#534 review).
    ml_top_idx = mixed_layer_top_index(derived_levels, start_idx=ground_idx)
    mixed_layer_top_ft = (
        derived_levels[ml_top_idx].altitude_ft if ml_top_idx > ground_idx else None
    )

    # Build CAT risk layers from Ri
    cat_layers = _build_cat_layers(
        derived_levels, ml_top_idx, ground_idx=ground_idx, ground_ft=ground_ft,
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
        model_surface_altitude_ft=(
            round(ground_ft) if ground_ft is not None else None
        ),
    )
