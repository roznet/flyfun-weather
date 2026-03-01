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
    """Enrich derived levels with cloud liquid water and ice mixing ratio fields.

    Sets volumetric LWC (g/m³) for Ogimet, and mixing ratios in g/kg for SFIP.
    Uses ideal gas law for air density: ρ = P / (Rd × T_K).
    LWC (g/m³) = CLWMR (kg/kg) × ρ_air (kg/m³) × 1000.

    GFS provides CLWMR/ICMR at 50-hPa standard levels, while Open-Meteo
    provides derived levels at 25-hPa spacing.  After the direct-match pass,
    interpolate to fill intermediate levels so SFIP sees continuous data
    instead of alternating full/proxy.
    """
    # Build lookup from raw pressure levels
    raw_by_pressure: dict[int, PressureLevelData] = {
        lv.pressure_hpa: lv for lv in raw_levels
    }

    # Pass 1: direct match — set values where raw GRIB data exists
    for dl in derived_levels:
        raw = raw_by_pressure.get(dl.pressure_hpa)
        if raw is None:
            continue

        # Mixing ratios in g/kg for SFIP (simple unit conversion, no density needed).
        # Preserve 0.0 values — SFIP needs to distinguish "measured zero" (full
        # variant, NONE severity) from "no data" (None → proxy variant).
        if raw.cloud_liquid_water_kg_kg is not None:
            dl.cloud_liquid_water_g_kg = round(raw.cloud_liquid_water_kg_kg * 1000.0, 6)
        if raw.ice_mixing_ratio_kg_kg is not None:
            dl.ice_mixing_ratio_g_kg = round(raw.ice_mixing_ratio_kg_kg * 1000.0, 6)

        # Propagate spatial interpolation flag
        if raw.clw_interpolated:
            dl.clw_interpolated = True

        # Volumetric LWC (g/m³) for Ogimet — requires temperature for density
        if raw.cloud_liquid_water_kg_kg is None or dl.temperature_c is None:
            continue

        clwmr = raw.cloud_liquid_water_kg_kg
        if clwmr <= 0:
            continue

        # Air density from ideal gas law
        t_k = dl.temperature_c + 273.15
        p_pa = dl.pressure_hpa * 100.0
        rho_air = p_pa / (_RD * t_k)

        dl.cloud_liquid_water_g_m3 = round(clwmr * rho_air * 1000.0, 4)

    # Pass 2: interpolate CLW/ICMR to intermediate levels (e.g. 25-hPa
    # spacing between 50-hPa GRIB levels).  Sorted high-to-low pressure.
    _interpolate_cloud_water(derived_levels)


def _interpolate_cloud_water(derived_levels: list[DerivedLevel]) -> None:
    """Fill intermediate levels by linear interpolation between enriched neighbors.

    Derived levels are sorted high-to-low pressure (descending).  For each
    level that still has cloud_liquid_water_g_kg == None, find the nearest
    enriched levels above and below in pressure and interpolate linearly.
    Only interpolates if BOTH neighbors have data (not None).
    """
    # Index enriched levels by pressure for fast neighbor lookup
    enriched: dict[int, DerivedLevel] = {}
    for dl in derived_levels:
        if dl.cloud_liquid_water_g_kg is not None or dl.ice_mixing_ratio_g_kg is not None:
            enriched[dl.pressure_hpa] = dl

    if len(enriched) < 2:
        return

    # Sorted pressures (descending — high pressure first = low altitude)
    enriched_pressures = sorted(enriched.keys(), reverse=True)

    for dl in derived_levels:
        if dl.cloud_liquid_water_g_kg is not None:
            continue  # already enriched

        p = dl.pressure_hpa

        # Find bracketing enriched pressures
        p_above: int | None = None  # higher pressure (lower altitude)
        p_below: int | None = None  # lower pressure (higher altitude)
        for ep in enriched_pressures:
            if ep > p:
                p_above = ep
            elif ep < p:
                p_below = ep
                break

        if p_above is None or p_below is None:
            continue

        above = enriched[p_above]
        below = enriched[p_below]

        # Linear interpolation weight (in pressure space)
        frac = (p_above - p) / (p_above - p_below)

        # CLW
        if above.cloud_liquid_water_g_kg is not None and below.cloud_liquid_water_g_kg is not None:
            dl.cloud_liquid_water_g_kg = round(
                above.cloud_liquid_water_g_kg * (1 - frac) + below.cloud_liquid_water_g_kg * frac,
                6,
            )

        # ICMR
        if above.ice_mixing_ratio_g_kg is not None and below.ice_mixing_ratio_g_kg is not None:
            dl.ice_mixing_ratio_g_kg = round(
                above.ice_mixing_ratio_g_kg * (1 - frac) + below.ice_mixing_ratio_g_kg * frac,
                6,
            )


def analyze_sounding(
    levels: list[PressureLevelData],
    hourly: HourlyForecast | None = None,
    icing_severity_enhance: bool = False,
    model_key: str | None = None,
) -> SoundingAnalysis | None:
    """Run full sounding analysis on pressure level data.

    Pipeline: prepare → thermodynamics → clouds → inversions → icing → convective.
    Returns None if profile preparation fails (insufficient data).
    """
    from weatherbrief.analysis.sounding.clouds import (
        build_nwp_cloud_layers,
        detect_cloud_layers,
        enrich_cloud_top_uncertainty,
    )
    from weatherbrief.analysis.sounding.convective import assess_convective
    from weatherbrief.analysis.sounding.icing import (
        assess_icing_zones_ogimet_dd,
        assess_icing_zones_ogimet_nwp,
    )
    from weatherbrief.analysis.sounding.inversions import detect_inversions
    from weatherbrief.analysis.sounding.prepare import prepare_profile
    from weatherbrief.analysis.sounding.thermodynamics import (
        compute_derived_levels,
        compute_indices,
    )
    from weatherbrief.analysis.sounding.precipitation import assess_precipitation
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

    # NWP cloud layers from model diagnostics
    nwp_cloud_layers = build_nwp_cloud_layers(
        nwp_cloud_diagnostics=hourly.nwp_cloud_diagnostics if hourly else None,
        nwp_cloud_low_pct=hourly.cloud_cover_low_pct if hourly else None,
        nwp_cloud_mid_pct=hourly.cloud_cover_mid_pct if hourly else None,
        nwp_cloud_high_pct=hourly.cloud_cover_high_pct if hourly else None,
    )

    # Temperature inversion detection
    inversion_layers = detect_inversions(derived_levels)

    # SFIP icing index (fuzzy-logic, parallel to Ogimet)
    from weatherbrief.analysis.sounding.sfip import assess_sfip_zones

    sfip_zones = assess_sfip_zones(
        derived_levels,
        cloud_layers=cloud_layers,
        nwp_cloud_low_pct=hourly.cloud_cover_low_pct if hourly else None,
        nwp_cloud_mid_pct=hourly.cloud_cover_mid_pct if hourly else None,
        nwp_cloud_high_pct=hourly.cloud_cover_high_pct if hourly else None,
        nwp_cloud_diagnostics=hourly.nwp_cloud_diagnostics if hourly else None,
    )

    # Ogimet-DD icing: continuous DD attenuation (primary method)
    icing_zones = assess_icing_zones_ogimet_dd(
        derived_levels,
        cloud_layers,
        cape_jkg=indices.cape_surface_jkg,
    )

    # Ogimet-NWP icing: NWP cloud cover scaling (Autorouter-style)
    icing_ogimet_nwp_zones = assess_icing_zones_ogimet_nwp(
        derived_levels,
        cloud_layers,
        cape_jkg=indices.cape_surface_jkg,
        nwp_cloud_low_pct=hourly.cloud_cover_low_pct if hourly else None,
        nwp_cloud_mid_pct=hourly.cloud_cover_mid_pct if hourly else None,
        nwp_cloud_high_pct=hourly.cloud_cover_high_pct if hourly else None,
        nwp_cloud_diagnostics=hourly.nwp_cloud_diagnostics if hourly else None,
    )

    # Precipitation phase classification
    precipitation = assess_precipitation(
        derived_levels,
        levels,
        hourly=hourly,
        freezing_level_ft=indices.freezing_level_ft,
    )

    # Convective assessment
    convective = assess_convective(indices)

    # Vertical motion and turbulence assessment
    compute_stability_indicators(profile, derived_levels)
    vertical_motion = assess_vertical_motion(derived_levels)

    # Compute ceiling fields for Key Altitudes display
    from weatherbrief.models.analysis import CloudCoverage

    sounding_ceiling_ft: float | None = None
    bkn_ovc_layers = [
        cl for cl in cloud_layers
        if cl.coverage in (CloudCoverage.BKN, CloudCoverage.OVC)
    ]
    if bkn_ovc_layers:
        lowest = min(bkn_ovc_layers, key=lambda cl: cl.base_ft)
        sounding_ceiling_ft = lowest.base_ft
        # LCL floor: when the ceiling layer starts at the bottom of the
        # sounding (first derived level), the base is just the level's
        # standard-atmosphere altitude, not a real cloud base.
        # Use LCL as a more realistic ceiling estimate.
        if (indices.lcl_altitude_ft is not None
                and derived_levels
                and lowest.base_pressure_hpa is not None
                and lowest.base_pressure_hpa >= derived_levels[0].pressure_hpa
                and indices.lcl_altitude_ft > sounding_ceiling_ft):
            sounding_ceiling_ft = round(indices.lcl_altitude_ft)

    nwp_ceiling_ft: float | None = None
    if hourly and hourly.nwp_cloud_diagnostics:
        nwp_ceiling_ft = hourly.nwp_cloud_diagnostics.ceiling_ft

    indices.sounding_ceiling_ft = sounding_ceiling_ft
    indices.nwp_ceiling_ft = nwp_ceiling_ft

    # --- Raw NWP value preservation ---
    # Attach raw model-computed values alongside MetPy-derived equivalents
    # for validation and divergence detection.
    if hourly is not None:
        from weatherbrief.fetch.variables import NWP_CAPE_TYPE

        indices.nwp_cape_jkg = hourly.cape_jkg
        indices.nwp_cape_type = NWP_CAPE_TYPE.get(model_key, "unknown") if model_key else None
        indices.nwp_cin_jkg = hourly.convective_inhibition_jkg
        indices.nwp_lifted_index = hourly.lifted_index_raw
        if hourly.freezing_level_m is not None:
            indices.nwp_freezing_level_ft = round(hourly.freezing_level_m * 3.28084)

        # Divergence flag: raw CAPE vs computed CAPE differ significantly
        raw = indices.nwp_cape_jkg
        calc = indices.cape_surface_jkg
        if raw is not None and calc is not None:
            abs_diff = abs(raw - calc)
            larger = max(abs(raw), abs(calc), 1.0)  # avoid division by zero
            indices.cape_raw_vs_calc_divergent = (
                abs_diff > 200.0 or abs_diff / larger > 1.0
            )

    return SoundingAnalysis(
        indices=indices,
        derived_levels=derived_levels,
        cloud_layers=cloud_layers,
        nwp_cloud_layers=nwp_cloud_layers,
        dd_cloud_layers=cloud_layers,
        icing_zones=icing_zones,
        icing_ogimet_dd_zones=icing_zones,
        icing_ogimet_nwp_zones=icing_ogimet_nwp_zones,
        sfip_zones=sfip_zones,
        inversion_layers=inversion_layers,
        convective=convective,
        precipitation=precipitation,
        vertical_motion=vertical_motion,
        cloud_cover_low_pct=hourly.cloud_cover_low_pct if hourly else None,
        cloud_cover_mid_pct=hourly.cloud_cover_mid_pct if hourly else None,
        cloud_cover_high_pct=hourly.cloud_cover_high_pct if hourly else None,
        nwp_cloud_diagnostics=hourly.nwp_cloud_diagnostics if hourly else None,
    )
