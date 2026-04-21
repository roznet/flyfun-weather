"""Convective risk assessment from thermodynamic indices and NWP diagnostics.

Pure threshold logic — no MetPy dependency. Takes ThermodynamicIndices
and returns ConvectiveAssessment.
"""

from __future__ import annotations

from weatherbrief.models import (
    ConvectiveAssessment,
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


def assess_convective_thermo(indices: ThermodynamicIndices) -> ConvectiveAssessment:
    """Assess convective risk from thermodynamic indices.

    Risk is primarily driven by CAPE (max of surface-based and most-unstable),
    modulated by CIN (convective inhibition). Severe modifiers flag additional
    hazards when shear, freezing level, or instability indices exceed critical
    values.
    """
    cape = _effective_cape(indices)
    cin = indices.cin_surface_jkg

    # Base risk from CAPE
    risk = ConvectiveRisk.NONE
    if cape is not None:
        for threshold, level in _CAPE_THRESHOLDS:
            if cape >= threshold:
                risk = level
                break

        # Marginal: any CAPE > 0 with a defined LFC/EL → shallow convection
        if risk == ConvectiveRisk.NONE and cape > 0:
            if indices.lfc_altitude_ft is not None and indices.el_altitude_ft is not None:
                risk = ConvectiveRisk.MARGINAL

    # Suppress by one level if strong CIN cap
    if cin is not None and cin < CIN_CAP_THRESHOLD and risk != ConvectiveRisk.NONE:
        risk_levels = list(ConvectiveRisk)
        idx = risk_levels.index(risk)
        if idx > 0:
            risk = risk_levels[idx - 1]

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
