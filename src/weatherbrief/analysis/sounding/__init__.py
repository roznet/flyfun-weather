"""Sounding analysis subpackage — MetPy-based atmospheric analysis.

Public API: analyze_sounding() takes pressure level data and returns a
SoundingAnalysis with thermodynamic indices, enhanced cloud layers,
icing zones, inversion layers, and convective assessment.
"""

from __future__ import annotations

import logging

from weatherbrief.models import DerivedLevel, HourlyForecast, PressureLevelData, SoundingAnalysis

logger = logging.getLogger(__name__)

# Dry air gas constant (J/(kg·K))
_RD = 287.05


def _enrich_lwc(
    derived_levels: list[DerivedLevel],
    raw_levels: list[PressureLevelData],
) -> None:
    """Convert CLWMR (kg/kg) to cloud liquid water content (g/m³) on derived levels.

    Uses ideal gas law for air density: ρ = P / (Rd × T_K).
    LWC (g/m³) = CLWMR (kg/kg) × ρ_air (kg/m³) × 1000.
    """
    # Build lookup from raw pressure levels
    raw_by_pressure: dict[int, PressureLevelData] = {
        lv.pressure_hpa: lv for lv in raw_levels
    }

    for dl in derived_levels:
        raw = raw_by_pressure.get(dl.pressure_hpa)
        if raw is None or raw.cloud_liquid_water_kg_kg is None:
            continue
        if dl.temperature_c is None:
            continue

        clwmr = raw.cloud_liquid_water_kg_kg
        if clwmr <= 0:
            continue

        # Air density from ideal gas law
        t_k = dl.temperature_c + 273.15
        p_pa = dl.pressure_hpa * 100.0
        rho_air = p_pa / (_RD * t_k)

        dl.cloud_liquid_water_g_m3 = round(clwmr * rho_air * 1000.0, 4)


def analyze_sounding(
    levels: list[PressureLevelData],
    hourly: HourlyForecast | None = None,
) -> SoundingAnalysis | None:
    """Run full sounding analysis on pressure level data.

    Pipeline: prepare → thermodynamics → clouds → inversions → icing → convective.
    Returns None if profile preparation fails (insufficient data).
    """
    from weatherbrief.analysis.sounding.clouds import (
        detect_cloud_layers,
        enrich_cloud_top_uncertainty,
    )
    from weatherbrief.analysis.sounding.convective import assess_convective
    from weatherbrief.analysis.sounding.icing import assess_icing_zones
    from weatherbrief.analysis.sounding.inversions import detect_inversions
    from weatherbrief.analysis.sounding.prepare import prepare_profile
    from weatherbrief.analysis.sounding.thermodynamics import (
        compute_derived_levels,
        compute_indices,
    )
    from weatherbrief.analysis.sounding.vertical_motion import (
        assess_vertical_motion,
        compute_stability_indicators,
    )

    profile = prepare_profile(levels, hourly)
    if profile is None:
        return None

    # Thermodynamic indices and per-level derived values
    indices = compute_indices(profile)
    derived_levels = compute_derived_levels(profile)

    # Enrich derived levels with CLWMR/ICMR from raw pressure level data
    _enrich_lwc(derived_levels, levels)

    # Enhanced cloud detection
    cloud_layers = detect_cloud_layers(
        derived_levels,
        lcl_altitude_ft=indices.lcl_altitude_ft,
    )

    # Cloud top uncertainty enrichment
    enrich_cloud_top_uncertainty(cloud_layers, indices, indices.cape_surface_jkg)

    # Temperature inversion detection
    inversion_layers = detect_inversions(derived_levels)

    # Enhanced icing assessment (Ogimet index with CAPE-based cloud split)
    icing_zones = assess_icing_zones(
        derived_levels,
        cloud_layers,
        precipitable_water_mm=indices.precipitable_water_mm,
        cape_jkg=indices.cape_surface_jkg,
        nwp_cloud_low_pct=hourly.cloud_cover_low_pct if hourly else None,
        nwp_cloud_mid_pct=hourly.cloud_cover_mid_pct if hourly else None,
    )

    # Convective assessment
    convective = assess_convective(indices)

    # Vertical motion and turbulence assessment
    compute_stability_indicators(profile, derived_levels)
    vertical_motion = assess_vertical_motion(derived_levels)

    return SoundingAnalysis(
        indices=indices,
        derived_levels=derived_levels,
        cloud_layers=cloud_layers,
        icing_zones=icing_zones,
        inversion_layers=inversion_layers,
        convective=convective,
        vertical_motion=vertical_motion,
        cloud_cover_low_pct=hourly.cloud_cover_low_pct if hourly else None,
        cloud_cover_mid_pct=hourly.cloud_cover_mid_pct if hourly else None,
        cloud_cover_high_pct=hourly.cloud_cover_high_pct if hourly else None,
    )
