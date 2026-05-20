"""Convective risk assessment from thermodynamic indices and NWP diagnostics.

Pure threshold logic — no MetPy dependency. Takes ThermodynamicIndices
and returns ConvectiveAssessment.
"""

from __future__ import annotations

from weatherbrief.models import (
    ConvectiveAssessment,
    ConvectiveRegime,
    ConvectiveRisk,
    NWPCloudDiagnostics,
    ThermodynamicIndices,
)

# CAPE thresholds (J/kg) for risk classification — European-calibrated.
# European convection produces severe weather at lower CAPE than the US;
# CAPE > 2000 J/kg is already exceptional over western Europe.
_CAPE_THRESHOLDS = [
    (2000, ConvectiveRisk.EXTREME),
    (1000, ConvectiveRisk.HIGH),
    (300, ConvectiveRisk.MODERATE),
    (50, ConvectiveRisk.LOW),
]

# CIN threshold above which convection is capped
CIN_CAP_THRESHOLD = -200  # J/kg (strong cap)

# Regime classification boundaries.
# CAPE (J/kg): below LOW → thermal-dominated; at/above HIGH → high-instability.
# CIN (J/kg): at/below CAP → a capping inversion significant enough that
# initiation depends on whether large-scale ascent erodes the cap.
REGIME_CAPE_LOW = 300
REGIME_CAPE_HIGH = 800
REGIME_CIN_CAP = -50

# 700 hPa omega trigger thresholds (Pa/s; negative = ascent).
OMEGA_ASCENT = -0.1      # clear large-scale ascent
OMEGA_SUBSIDENCE = 0.05  # large-scale subsidence


def effective_cape(indices: ThermodynamicIndices) -> float | None:
    """Return the most relevant CAPE for convective risk.

    Uses max of MetPy-computed variants (SB-CAPE, MU-CAPE, ML-CAPE).
    Falls back to NWP raw CAPE only when no MetPy variant is available,
    because model-native CAPE can diverge significantly from sounding-
    derived values (different parcel selection, virtual temperature
    corrections, etc.) and including it in the max pool could inflate
    convective risk when the sounding doesn't support it.
    """
    metpy_values = [
        v for v in (
            indices.cape_surface_jkg,
            indices.cape_most_unstable_jkg,
            indices.cape_mixed_layer_jkg,
        )
        if v is not None
    ]
    if metpy_values:
        return max(metpy_values)
    # Fallback: NWP raw CAPE when no MetPy variants available
    return indices.nwp_cape_jkg


# Backward-compatible alias (private name used by callers during transition)
_effective_cape = effective_cape


def _severity_modifiers(indices: ThermodynamicIndices, cape: float | None) -> list[str]:
    """Compute severity modifier strings from indices."""
    modifiers: list[str] = []
    shear_06 = indices.bulk_shear_0_6km_kt

    if shear_06 is not None:
        if shear_06 > 40:
            modifiers.append("strong shear (>40kt 0-6km): organized/supercell potential")
        elif shear_06 > 25:
            modifiers.append("moderate shear (>25kt 0-6km): multicell potential")

    if (
        indices.freezing_level_ft is not None
        and indices.freezing_level_ft > 11500  # ~3500m
        and cape is not None
        and cape > 1000
    ):
        modifiers.append("high freezing level + CAPE: hail risk")

    if indices.k_index is not None and indices.k_index > 35:
        modifiers.append(f"high K-index ({indices.k_index}): thunderstorm potential")

    if indices.total_totals is not None and indices.total_totals > 55:
        modifiers.append(f"high Total Totals ({indices.total_totals}): severe thunderstorm potential")

    if indices.lifted_index is not None and indices.lifted_index < -6:
        modifiers.append(f"strongly negative LI ({indices.lifted_index}): extreme instability")

    return modifiers


def _risk_from_cape(cape: float | None, indices: ThermodynamicIndices) -> ConvectiveRisk:
    """Base risk purely from effective CAPE (with MARGINAL for shallow convection)."""
    if cape is None:
        return ConvectiveRisk.NONE
    for threshold, level in _CAPE_THRESHOLDS:
        if cape >= threshold:
            return level
    # Marginal: any CAPE > 0 with a defined LFC/EL → shallow convection
    if cape > 0 and indices.lfc_altitude_ft is not None and indices.el_altitude_ft is not None:
        return ConvectiveRisk.MARGINAL
    return ConvectiveRisk.NONE


def classify_regime(cape: float | None, cin: float | None) -> ConvectiveRegime | None:
    """Classify the dominant convective regime from CAPE and CIN.

    Returns None when CAPE is unavailable (insufficient data to characterise
    the regime). The boundaries are deliberately simple, hard cutoffs — the
    per-regime scoring below, not the classification, carries the nuance.
    """
    if cape is None:
        return None
    if cape >= REGIME_CAPE_HIGH:
        if cin is not None and cin <= REGIME_CIN_CAP:
            return ConvectiveRegime.LOADED_GUN
        return ConvectiveRegime.ACTIVE
    if cape < REGIME_CAPE_LOW:
        return ConvectiveRegime.THERMAL
    return ConvectiveRegime.WEAK_INSTABILITY


def _down_one(risk: ConvectiveRisk) -> ConvectiveRisk:
    """Drop risk by one ordinal level (clamped at NONE)."""
    levels = list(ConvectiveRisk)
    idx = levels.index(risk)
    return levels[idx - 1] if idx > 0 else risk


def _shared_annotations(indices: ThermodynamicIndices) -> tuple[list[str], list[str]]:
    """Moisture / geometry drivers and suppressors common to all regimes."""
    drivers: list[str] = []
    suppressors: list[str] = []

    if indices.k_index is not None and indices.k_index >= 30:
        drivers.append(f"Moist mid-levels (K-index {indices.k_index:.0f})")

    if indices.lcl_altitude_ft is not None and indices.lfc_altitude_ft is not None:
        gap = indices.lfc_altitude_ft - indices.lcl_altitude_ft
        if gap <= 500:
            drivers.append(f"Small LCL–LFC gap (~{gap:.0f} ft): low barrier to initiation")

    if indices.lcl_altitude_ft is not None and indices.lcl_altitude_ft > 8000:
        suppressors.append(
            f"High-based (LCL {indices.lcl_altitude_ft:.0f} ft): reduced surface impact"
        )

    return drivers, suppressors


def _regime_explanation(
    regime: ConvectiveRegime | None,
    risk: ConvectiveRisk,
    cape: float | None,
    cin: float | None,
    omega_700_pa_s: float | None,
) -> tuple[ConvectiveRisk, list[str], list[str]]:
    """Apply regime-aware risk adjustment and build drivers/suppressors.

    Only the LOADED_GUN regime adjusts the risk level here (trigger-gated cap
    erosion); the other regimes keep the CAPE-derived risk and the caller
    applies the generic strong-CIN suppression. Returns the (possibly adjusted)
    risk plus the lists of factors raising and holding down risk.
    """
    if regime is None:
        return risk, [], []

    drivers: list[str] = []
    suppressors: list[str] = []
    ascent = omega_700_pa_s is not None and omega_700_pa_s <= OMEGA_ASCENT
    subsidence = omega_700_pa_s is not None and omega_700_pa_s >= OMEGA_SUBSIDENCE

    if regime is ConvectiveRegime.LOADED_GUN:
        drivers.append(f"High instability (CAPE {cape:.0f} J/kg)")
        if ascent:
            drivers.append(
                f"Large-scale ascent (ω₇₀₀ {omega_700_pa_s:.2f} Pa/s) may erode the cap"
            )
        else:
            risk = _down_one(risk)
            suppressors.append(
                f"Capping inversion (CIN {cin:.0f} J/kg) with no large-scale ascent — "
                "initiation unlikely (loaded gun)"
            )
    elif regime is ConvectiveRegime.ACTIVE:
        cap_note = f", weak/absent cap (CIN {cin:.0f} J/kg)" if cin is not None else ""
        drivers.append(
            f"High instability (CAPE {cape:.0f} J/kg){cap_note} — convection readily initiates"
        )
        if ascent:
            drivers.append(
                f"Large-scale ascent (ω₇₀₀ {omega_700_pa_s:.2f} Pa/s) reinforces lift"
            )
    elif regime is ConvectiveRegime.WEAK_INSTABILITY:
        drivers.append(f"Moderate instability (CAPE {cape:.0f} J/kg)")
        if ascent:
            drivers.append(
                f"Large-scale ascent (ω₇₀₀ {omega_700_pa_s:.2f} Pa/s) aids initiation"
            )
        elif subsidence:
            suppressors.append(
                f"Large-scale subsidence (ω₇₀₀ {omega_700_pa_s:.2f} Pa/s) suppresses development"
            )
    elif regime is ConvectiveRegime.THERMAL:
        if cape is not None and cape > 0:
            drivers.append(
                "Thermal-dominated regime — daytime heating / orographic lift may "
                "trigger isolated convection"
            )

    return risk, drivers, suppressors


def assess_convective_thermo(
    indices: ThermodynamicIndices,
    omega_700_pa_s: float | None = None,
) -> ConvectiveAssessment:
    """Assess convective risk from thermodynamic indices.

    Risk starts from effective CAPE (max of MetPy SB/MU/ML variants), then the
    convective *regime* (THERMAL / WEAK_INSTABILITY / LOADED_GUN / ACTIVE)
    selects regime-appropriate reasoning:

    - LOADED_GUN (high CAPE under a strong cap): risk is held down one level
      unless 700 hPa omega shows large-scale ascent that could erode the cap —
      this is what stops high CAPE alone from producing a false HIGH.
    - ACTIVE (high CAPE, weak cap): risk stays at the CAPE-derived level;
      convection initiates readily.
    - WEAK_INSTABILITY / THERMAL: CAPE-derived risk with the generic strong-CIN
      suppression preserved.

    The ``drivers`` and ``suppressors`` lists make the reasoning explicit for
    the briefing narrative. ``severe_modifiers`` continue to flag the character
    of convection if it fires (shear, hail, etc.).
    """
    cape = _effective_cape(indices)
    cin = indices.cin_surface_jkg

    risk = _risk_from_cape(cape, indices)
    regime = classify_regime(cape, cin)

    risk, drivers, suppressors = _regime_explanation(
        regime, risk, cape, cin, omega_700_pa_s
    )

    # Generic strong-CIN suppression for regimes that don't model the cap
    # themselves (LOADED_GUN already gated initiation on the cap above; ACTIVE
    # is weak-cap by definition).
    if regime not in (ConvectiveRegime.LOADED_GUN, ConvectiveRegime.ACTIVE):
        if cin is not None and cin < CIN_CAP_THRESHOLD and risk != ConvectiveRisk.NONE:
            risk = _down_one(risk)
            suppressors.insert(0, f"Strong cap (CIN {cin:.0f} J/kg) suppresses convection")

    shared_drivers, shared_suppressors = _shared_annotations(indices)
    drivers.extend(shared_drivers)
    suppressors.extend(shared_suppressors)

    modifiers = _severity_modifiers(indices, cape)

    # Unified interface: base from LFC (fallback LCL), top from EL
    base_ft = indices.lfc_altitude_ft if indices.lfc_altitude_ft is not None else indices.lcl_altitude_ft
    top_ft = indices.el_altitude_ft

    return ConvectiveAssessment(
        risk_level=risk,
        cape_jkg=cape,
        cin_jkg=cin,
        lcl_altitude_ft=indices.lcl_altitude_ft,
        lfc_altitude_ft=indices.lfc_altitude_ft,
        el_altitude_ft=indices.el_altitude_ft,
        bulk_shear_0_6km_kt=indices.bulk_shear_0_6km_kt,
        lifted_index=indices.lifted_index,
        k_index=indices.k_index,
        total_totals=indices.total_totals,
        severe_modifiers=modifiers,
        regime=regime,
        drivers=drivers,
        suppressors=suppressors,
        base_ft=base_ft,
        top_ft=top_ft,
        cover_pct=None,
        method="thermo",
    )


# Keep backward-compatible alias
assess_convective = assess_convective_thermo


def assess_convective_nwp(
    indices: ThermodynamicIndices,
    nwp_diagnostics: NWPCloudDiagnostics | None,
) -> ConvectiveAssessment | None:
    """Assess convective risk from NWP model convective cloud parameterization.

    Returns None when nwp_diagnostics is None (GRIB2 data unavailable).
    Risk is driven by convective_cover_pct thresholds. Thermodynamic indices
    are preserved for context. Severity modifiers are computed from the same
    indices as the thermo method.
    """
    if nwp_diagnostics is None:
        return None

    cover = nwp_diagnostics.convective_cover_pct
    cape = _effective_cape(indices)
    modifiers = _severity_modifiers(indices, cape)

    if cover is not None:
        # Full NWP path: risk from CAPE (same as thermo), cover is informational.
        # NWP provides better convective geometry (base/top) than thermo (LFC/EL).
        risk = ConvectiveRisk.NONE
        if cape is not None:
            for threshold, level in _CAPE_THRESHOLDS:
                if cape >= threshold:
                    risk = level
                    break
            if risk == ConvectiveRisk.NONE and cape >= 10:
                risk = ConvectiveRisk.MARGINAL
        method = "nwp"
    elif (
        nwp_diagnostics.convective_base_ft is not None
        and nwp_diagnostics.convective_top_ft is not None
    ):
        # Hybrid path (e.g. ICON-EU): no cover_pct but GRIB base/top exist.
        # Derive risk from CAPE thresholds (same as thermo) and pair with
        # NWP geometric bounds for a more accurate convective envelope.
        risk = ConvectiveRisk.NONE
        if cape is not None:
            for threshold, level in _CAPE_THRESHOLDS:
                if cape >= threshold:
                    risk = level
                    break
            # Marginal: meaningful CAPE with defined base/top.
            # ICON can report convective geometry with negligible CAPE (<10 J/kg);
            # filter that noise — 10 J/kg is well below the LOW threshold (50).
            if risk == ConvectiveRisk.NONE and cape >= 10:
                risk = ConvectiveRisk.MARGINAL
        method = "nwp_hybrid"
    elif (
        nwp_diagnostics.convective_top_ft is not None
        and indices.lcl_altitude_ft is not None
        and nwp_diagnostics.convective_top_ft > indices.lcl_altitude_ft
    ):
        # LCL-anchored (e.g. ECMWF hcct): model gives convective top height
        # but no base — use LCL as the convective base proxy. Risk from CAPE
        # thresholds; base = LCL, top = hcct. Guard against hcct ≤ LCL
        # (rare elevated-convection artefact) so downstream never sees
        # base ≥ top.
        risk = ConvectiveRisk.NONE
        if cape is not None:
            for threshold, level in _CAPE_THRESHOLDS:
                if cape >= threshold:
                    risk = level
                    break
            if risk == ConvectiveRisk.NONE and cape >= 10:
                risk = ConvectiveRisk.MARGINAL
        method = "nwp_lcl_top"
    else:
        return None

    # Suppress by one level if strong CIN cap (same as thermo path)
    cin = indices.cin_surface_jkg
    if cin is not None and cin < CIN_CAP_THRESHOLD and risk != ConvectiveRisk.NONE:
        risk_levels = list(ConvectiveRisk)
        idx = risk_levels.index(risk)
        if idx > 0:
            risk = risk_levels[idx - 1]

    # LCL-anchored path uses LCL as the convective base proxy
    base_ft = nwp_diagnostics.convective_base_ft
    if method == "nwp_lcl_top":
        base_ft = indices.lcl_altitude_ft

    return ConvectiveAssessment(
        risk_level=risk,
        cape_jkg=cape,
        cin_jkg=cin,
        lcl_altitude_ft=indices.lcl_altitude_ft,
        lfc_altitude_ft=indices.lfc_altitude_ft,
        el_altitude_ft=indices.el_altitude_ft,
        bulk_shear_0_6km_kt=indices.bulk_shear_0_6km_kt,
        lifted_index=indices.lifted_index,
        k_index=indices.k_index,
        total_totals=indices.total_totals,
        severe_modifiers=modifiers,
        base_ft=base_ft,
        top_ft=nwp_diagnostics.convective_top_ft,
        cover_pct=cover,
        method=method,
    )
