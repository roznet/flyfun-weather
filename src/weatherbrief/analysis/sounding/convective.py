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

# NWP convective cover thresholds for risk classification
_NWP_COVER_THRESHOLDS = [
    (75, ConvectiveRisk.HIGH),
    (50, ConvectiveRisk.MODERATE),
    (25, ConvectiveRisk.LOW),
    (10, ConvectiveRisk.MARGINAL),
]

# CIN threshold above which convection is capped
CIN_CAP_THRESHOLD = -200  # J/kg (strong cap)


def _effective_cape(indices: ThermodynamicIndices) -> float | None:
    """Return the most relevant CAPE for convective risk.

    Uses max(SB-CAPE, MU-CAPE) to catch elevated convection common in
    European maritime environments where SB-CAPE can be near zero while
    a warm layer aloft is unstable.
    """
    values = [
        v for v in (indices.cape_surface_jkg, indices.cape_most_unstable_jkg)
        if v is not None
    ]
    return max(values) if values else None


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
    if cover is None:
        return None

    # Risk from NWP convective cover
    risk = ConvectiveRisk.NONE
    for threshold, level in _NWP_COVER_THRESHOLDS:
        if cover >= threshold:
            risk = level
            break

    cape = _effective_cape(indices)
    modifiers = _severity_modifiers(indices, cape)

    return ConvectiveAssessment(
        risk_level=risk,
        cape_jkg=cape,
        cin_jkg=indices.cin_surface_jkg,
        lcl_altitude_ft=indices.lcl_altitude_ft,
        lfc_altitude_ft=indices.lfc_altitude_ft,
        el_altitude_ft=indices.el_altitude_ft,
        bulk_shear_0_6km_kt=indices.bulk_shear_0_6km_kt,
        lifted_index=indices.lifted_index,
        k_index=indices.k_index,
        total_totals=indices.total_totals,
        severe_modifiers=modifiers,
        base_ft=nwp_diagnostics.convective_base_ft,
        top_ft=nwp_diagnostics.convective_top_ft,
        cover_pct=cover,
        method="nwp",
    )
