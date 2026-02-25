"""Convective risk assessment from thermodynamic indices.

Pure threshold logic — no MetPy dependency. Takes ThermodynamicIndices
and returns ConvectiveAssessment.
"""

from __future__ import annotations

from weatherbrief.models import ConvectiveAssessment, ConvectiveRisk, ThermodynamicIndices

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


def assess_convective(indices: ThermodynamicIndices) -> ConvectiveAssessment:
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

    # Severity modifiers
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

    return ConvectiveAssessment(
        risk_level=risk,
        cape_jkg=cape,
        cin_jkg=cin,
        lcl_altitude_ft=indices.lcl_altitude_ft,
        lfc_altitude_ft=indices.lfc_altitude_ft,
        el_altitude_ft=indices.el_altitude_ft,
        bulk_shear_0_6km_kt=shear_06,
        lifted_index=indices.lifted_index,
        k_index=indices.k_index,
        total_totals=indices.total_totals,
        severe_modifiers=modifiers,
    )
