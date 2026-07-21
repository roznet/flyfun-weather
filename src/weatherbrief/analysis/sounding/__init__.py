"""Sounding analysis subpackage — MetPy-based atmospheric analysis.

Public API: analyze_sounding() takes pressure level data and returns a
SoundingAnalysis with thermodynamic indices, enhanced cloud layers,
icing zones, inversion layers, and convective assessment.
"""

from __future__ import annotations

import logging

from weatherbrief.models import (
    DerivedLevel,
    EnhancedCloudLayer,
    HourlyForecast,
    PressureLevelData,
    SoundingAnalysis,
)
from weatherbrief.models.analysis import CloudCoverage

from weatherbrief.analysis.sounding.prepare import PreparedProfile

logger = logging.getLogger(__name__)

# Dry air gas constant (J/(kg·K))
_RD = 287.05

# Hardcoded off — see designs/cloud-layers-analysis.md "ICAO band overlay (disabled)".
# When True, the active ``cloud_layers`` slot is built by
# ``apply_nwp_coverage(dd_cloud_layers, …)``, which splits decks at ICAO
# band boundaries (6500/20000 ft) and re-codes coverage from NWP bulk
# band %. Now that the category-split DD detector produces good per-deck
# OVC/BKN/SCT classes with moisture-defined edges, that overlay degrades
# rather than improves the result. Kept callable so we can A/B it later.
_APPLY_NWP_COVERAGE_OVERLAY = False


def compute_sounding_ceiling_ft(
    cloud_layers: list[EnhancedCloudLayer],
    derived_levels: list[DerivedLevel],
    lcl_altitude_ft: float | None,
) -> float | None:
    """Lowest BKN/OVC cloud base (ft) as the sounding ceiling, floored to the LCL.

    Returns the base altitude of the lowest broken/overcast layer, or None when
    there is no BKN/OVC layer. The LCL floor applies only to a *surface-rooted*
    deck — one whose base pressure is at or below the surface level of the
    sounding (``base_pressure_hpa >= derived_levels[0].pressure_hpa``) — because
    such a deck cannot realistically have a base below the lifting condensation
    level; in that case the ceiling is raised to the LCL.

    Extracted from ``analyze_sounding`` so it can be unit-tested directly (and so
    tests exercise the production logic rather than a copy of it).
    """
    bkn_ovc_layers = [
        cl for cl in cloud_layers
        if cl.coverage in (CloudCoverage.BKN, CloudCoverage.OVC)
    ]
    if not bkn_ovc_layers:
        return None
    lowest = min(bkn_ovc_layers, key=lambda cl: cl.base_ft)
    ceiling_ft: float | None = lowest.base_ft
    if (lcl_altitude_ft is not None
            and derived_levels
            and lowest.base_pressure_hpa is not None
            and lowest.base_pressure_hpa >= derived_levels[0].pressure_hpa
            and lcl_altitude_ft > ceiling_ft):
        ceiling_ft = round(lcl_altitude_ft)
    return ceiling_ft


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
            # Mark vertically interpolated levels so SFIP reports
            # variant="interp" (same treatment as spatially interpolated).
            dl.clw_interpolated = True

        # ICMR
        if above.ice_mixing_ratio_g_kg is not None and below.ice_mixing_ratio_g_kg is not None:
            dl.ice_mixing_ratio_g_kg = round(
                above.ice_mixing_ratio_g_kg * (1 - frac) + below.ice_mixing_ratio_g_kg * frac,
                6,
            )


def _omega_near_700(derived_levels: list[DerivedLevel]) -> float | None:
    """Return model omega (Pa/s) at the level nearest 700 hPa, if reasonably close.

    Used as the large-scale ascent trigger for the convective loaded-gun
    regime. Returns None when no level within 100 hPa of 700 carries omega.
    """
    best: float | None = None
    best_dist: float | None = None
    for lvl in derived_levels:
        if lvl.omega_pa_s is None:
            continue
        dist = abs(lvl.pressure_hpa - 700)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best = lvl.omega_pa_s
    if best_dist is not None and best_dist <= 100:
        return best
    return None


def analyze_sounding_lite(
    levels: list[PressureLevelData],
    hourly: HourlyForecast | None = None,
    model_key: str | None = None,
    _profile: PreparedProfile | None = None,
) -> SoundingAnalysis | None:
    """Lightweight sounding analysis: ceiling, clouds, and convective risk.

    Computes the subset needed for verification scoring and
    ``reconcile_ceiling()``: core thermodynamic indices (LCL, parcel,
    LFC, EL, surface CAPE/CIN, lifted index, freezing level), DD cloud
    layers with NWP coverage reclassification, inversions, and
    convective assessment.

    Skips: extended indices (MU/ML-CAPE, Showalter, K-index, bulk shear),
    icing (Ogimet, SFIP, IENG, SLD), NWP cloud layer synthesis,
    precipitation, and vertical motion / turbulence.

    Returns None if profile preparation fails (insufficient data).
    """
    from weatherbrief.analysis.sounding.clouds import (
        detect_cloud_layers,
    )
    from weatherbrief.analysis.sounding.convective import (
        assess_convective_explicit,
        assess_convective_nwp,
        assess_convective_thermo,
    )
    from weatherbrief.analysis.sounding.inversions import detect_inversions
    from weatherbrief.analysis.sounding.prepare import prepare_profile
    from weatherbrief.analysis.sounding.thermodynamics import (
        compute_derived_levels_core,
        compute_indices_core,
    )

    profile = _profile or prepare_profile(levels, hourly)
    if profile is None:
        return None

    core_result = compute_indices_core(profile)
    indices = core_result.indices
    parcel_path = core_result.parcel_path
    derived_levels = compute_derived_levels_core(profile)

    # Attach raw NWP values for convective assessment fallback
    if hourly is not None:
        from weatherbrief.fetch.variables import NWP_CAPE_TYPE

        indices.nwp_cape_jkg = hourly.cape_jkg
        indices.nwp_cape_type = NWP_CAPE_TYPE.get(model_key, "unknown") if model_key else None
        indices.nwp_cin_jkg = hourly.convective_inhibition_jkg
        indices.nwp_lifted_index = hourly.lifted_index_raw
        # Model-native K-index / Total Totals (currently ECMWF kx/totalx via
        # GRIB; None elsewhere). Preferred over the MetPy-derived values by the
        # convective character advisory (issue #294).
        indices.nwp_k_index = hourly.nwp_k_index
        indices.nwp_total_totals = hourly.nwp_total_totals
        if hourly.freezing_level_m is not None:
            indices.nwp_freezing_level_ft = round(hourly.freezing_level_m * 3.28084)

        raw = indices.nwp_cape_jkg
        calc = indices.cape_surface_jkg
        if raw is not None and calc is not None:
            abs_diff = abs(raw - calc)
            larger = max(abs(raw), abs(calc), 1.0)
            indices.cape_raw_vs_calc_divergent = (
                abs_diff > 200.0 or abs_diff / larger > 1.0
            )

    # DD cloud layers (sounding-derived)
    dd_cloud_layers = detect_cloud_layers(
        derived_levels,
        lcl_altitude_ft=indices.lcl_altitude_ft,
    )

    # Active cloud_layers slot. With _APPLY_NWP_COVERAGE_OVERLAY off (the
    # current default) we publish dd_cloud_layers verbatim — they already
    # carry moisture-defined edges and per-deck OVC/BKN/SCT classes from
    # the category-split detector. The ICAO band overlay (when enabled)
    # would split decks at 6500/20000 ft and re-classify each segment by
    # NWP bulk band %, which destroys the per-deck edges and produces
    # zero-pressure-span segments that disappear from the Skew-T view.
    if _APPLY_NWP_COVERAGE_OVERLAY:
        from weatherbrief.analysis.sounding.clouds import apply_nwp_coverage
        cloud_layers = apply_nwp_coverage(
            dd_cloud_layers,
            nwp_cloud_low_pct=hourly.cloud_cover_low_pct if hourly else None,
            nwp_cloud_mid_pct=hourly.cloud_cover_mid_pct if hourly else None,
            nwp_cloud_high_pct=hourly.cloud_cover_high_pct if hourly else None,
            nwp_cloud_diagnostics=hourly.nwp_cloud_diagnostics if hourly else None,
        )
    else:
        cloud_layers = list(dd_cloud_layers)

    # Inversions (cheap — single linear scan, ~2 µs)
    inversion_layers = detect_inversions(derived_levels)

    # Convective assessment — pass the 700 hPa omega so the loaded-gun regime
    # can tell whether large-scale ascent might erode a capping inversion.
    convective = assess_convective_thermo(
        indices, omega_700_pa_s=_omega_near_700(derived_levels)
    )
    if hourly and hourly.explicit_convective_diagnostics is not None:
        convective_nwp = assess_convective_explicit(
            indices,
            hourly.explicit_convective_diagnostics,
            hourly.nwp_cloud_diagnostics,
        )
    else:
        convective_nwp = assess_convective_nwp(
            indices, hourly.nwp_cloud_diagnostics if hourly else None,
        )

    # Compute ceiling from NWP-reclassified cloud layers
    sounding_ceiling_ft = compute_sounding_ceiling_ft(
        cloud_layers, derived_levels, indices.lcl_altitude_ft,
    )

    nwp_ceiling_ft: float | None = None
    if hourly and hourly.nwp_cloud_diagnostics:
        nwp_ceiling_ft = hourly.nwp_cloud_diagnostics.ceiling_ft

    indices.sounding_ceiling_ft = sounding_ceiling_ft
    indices.nwp_ceiling_ft = nwp_ceiling_ft

    return SoundingAnalysis(
        indices=indices,
        parcel_path=parcel_path,
        derived_levels=derived_levels,
        cloud_layers=cloud_layers,
        nwp_cloud_layers=[],
        dd_cloud_layers=dd_cloud_layers,
        icing_zones=[],
        icing_ogimet_dd_zones=[],
        icing_ogimet_nwp_zones=[],
        sfip_zones=[],
        ieng_icing_zones=[],
        sld_zones=[],
        inversion_layers=inversion_layers,
        convective=convective,
        convective_thermo=convective,
        convective_nwp=convective_nwp,
        precipitation=None,
        vertical_motion=None,
        cloud_cover_low_pct=hourly.cloud_cover_low_pct if hourly else None,
        cloud_cover_mid_pct=hourly.cloud_cover_mid_pct if hourly else None,
        cloud_cover_high_pct=hourly.cloud_cover_high_pct if hourly else None,
        visibility_m=hourly.visibility_m if hourly else None,
        temperature_2m_c=hourly.temperature_2m_c if hourly else None,
        dewpoint_2m_c=hourly.dewpoint_2m_c if hourly else None,
        nwp_cloud_diagnostics=hourly.nwp_cloud_diagnostics if hourly else None,
    )


def _analyze_sounding_heavy(
    result: SoundingAnalysis,
    levels: list[PressureLevelData],
    hourly: HourlyForecast | None,
    profile: PreparedProfile,
) -> None:
    """Extend a lite SoundingAnalysis with icing, NWP cloud synthesis, etc.

    Mutates *result* in-place.  Called by ``analyze_sounding()`` to
    complete the full analysis after the lite phase.  Recomputes
    cloud_layers and inversions after extended enrichment.
    """
    from weatherbrief.analysis.sounding.clouds import (
        build_nwp_cloud_layers,
        enrich_cloud_top_uncertainty,
    )
    from weatherbrief.analysis.sounding.convective import effective_cape
    from weatherbrief.analysis.sounding.icing import (
        assess_icing_zones_ieng,
        assess_icing_zones_ogimet_dd,
        assess_icing_zones_ogimet_nwp,
    )
    from weatherbrief.analysis.sounding.inversions import detect_inversions
    from weatherbrief.analysis.sounding.precipitation import assess_precipitation
    from weatherbrief.analysis.sounding.sfip import assess_sfip_zones
    from weatherbrief.analysis.sounding.sld import assess_sld_zones
    from weatherbrief.analysis.sounding.thermodynamics import (
        compute_derived_levels_extended,
        compute_indices_extended,
    )
    from weatherbrief.analysis.sounding.vertical_motion import (
        assess_vertical_motion,
        compute_stability_indicators,
    )

    indices = result.indices
    derived_levels = result.derived_levels
    dd_cloud_layers = result.dd_cloud_layers

    # Extended thermodynamic indices (MU/ML-CAPE, Showalter, K-index, etc.)
    compute_indices_extended(profile, indices)

    # Extended per-level fields (wet bulb, theta-e, RH, omega)
    compute_derived_levels_extended(profile, derived_levels)

    # Enrich derived levels with CLWMR/ICMR from raw pressure level data
    _enrich_lwc(derived_levels, levels)

    # Cloud top uncertainty enrichment — use effective CAPE (max of all variants)
    eff_cape = effective_cape(indices)
    enrich_cloud_top_uncertainty(dd_cloud_layers, indices, eff_cape)

    # Active cloud_layers slot. See _APPLY_NWP_COVERAGE_OVERLAY note above
    # — overlay disabled by default, so we publish dd_cloud_layers verbatim.
    if _APPLY_NWP_COVERAGE_OVERLAY:
        from weatherbrief.analysis.sounding.clouds import apply_nwp_coverage
        cloud_layers = apply_nwp_coverage(
            dd_cloud_layers,
            nwp_cloud_low_pct=hourly.cloud_cover_low_pct if hourly else None,
            nwp_cloud_mid_pct=hourly.cloud_cover_mid_pct if hourly else None,
            nwp_cloud_high_pct=hourly.cloud_cover_high_pct if hourly else None,
            nwp_cloud_diagnostics=hourly.nwp_cloud_diagnostics if hourly else None,
        )
    else:
        cloud_layers = list(dd_cloud_layers)

    # Temperature inversion detection
    inversion_layers = detect_inversions(derived_levels)

    # NWP cloud layers from model diagnostics (None if no native NWP source)
    nwp_cloud_layers = build_nwp_cloud_layers(
        nwp_cloud_diagnostics=hourly.nwp_cloud_diagnostics if hourly else None,
        nwp_cloud_low_pct=hourly.cloud_cover_low_pct if hourly else None,
        nwp_cloud_mid_pct=hourly.cloud_cover_mid_pct if hourly else None,
        nwp_cloud_high_pct=hourly.cloud_cover_high_pct if hourly else None,
        pressure_levels=levels,
    )

    # SFIP icing index (full → NWP-gated, proxy → DD-gated)
    sfip_zones = assess_sfip_zones(
        derived_levels,
        dd_cloud_layers=cloud_layers,
        nwp_cloud_layers=nwp_cloud_layers or [],
        nwp_cloud_low_pct=hourly.cloud_cover_low_pct if hourly else None,
        nwp_cloud_mid_pct=hourly.cloud_cover_mid_pct if hourly else None,
        nwp_cloud_high_pct=hourly.cloud_cover_high_pct if hourly else None,
        nwp_cloud_diagnostics=hourly.nwp_cloud_diagnostics if hourly else None,
    )

    # Ogimet-DD icing
    icing_zones = assess_icing_zones_ogimet_dd(
        derived_levels,
        cloud_layers,
        cape_jkg=eff_cape,
    )

    # Ogimet-NWP icing (gated by NWP cloud layers; returns [] if none)
    icing_ogimet_nwp_zones = assess_icing_zones_ogimet_nwp(
        derived_levels,
        nwp_cloud_layers,
        cape_jkg=eff_cape,
        nwp_cloud_low_pct=hourly.cloud_cover_low_pct if hourly else None,
        nwp_cloud_mid_pct=hourly.cloud_cover_mid_pct if hourly else None,
        nwp_cloud_high_pct=hourly.cloud_cover_high_pct if hourly else None,
        nwp_cloud_diagnostics=hourly.nwp_cloud_diagnostics if hourly else None,
    )

    # IENG icing (gated by NWP cloud layers; returns [] if none)
    ieng_icing_zones = assess_icing_zones_ieng(
        derived_levels,
        nwp_cloud_layers,
        cape_jkg=eff_cape,
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

    # SLD detection
    sld_zones = assess_sld_zones(
        derived_levels,
        cloud_layers=cloud_layers,
        freezing_level_ft=indices.freezing_level_ft,
        precipitation=precipitation,
    )

    # Vertical motion and turbulence
    compute_stability_indicators(profile, derived_levels)
    vertical_motion = assess_vertical_motion(derived_levels)

    # Update result in-place
    result.cloud_layers = cloud_layers
    result.nwp_cloud_layers = nwp_cloud_layers
    result.icing_zones = icing_zones
    result.icing_ogimet_dd_zones = icing_zones
    result.icing_ogimet_nwp_zones = icing_ogimet_nwp_zones
    result.sfip_zones = sfip_zones
    result.ieng_icing_zones = ieng_icing_zones
    result.sld_zones = sld_zones
    result.inversion_layers = inversion_layers
    result.precipitation = precipitation
    result.vertical_motion = vertical_motion


def analyze_sounding(
    levels: list[PressureLevelData],
    hourly: HourlyForecast | None = None,
    icing_severity_enhance: bool = False,
    model_key: str | None = None,
) -> SoundingAnalysis | None:
    """Run full sounding analysis on pressure level data.

    Pipeline: lite (ceiling + convective) → heavy (icing, inversions, etc.).
    Returns None if profile preparation fails (insufficient data).
    """
    from weatherbrief.analysis.sounding.prepare import prepare_profile

    profile = prepare_profile(levels, hourly)
    if profile is None:
        return None

    result = analyze_sounding_lite(levels, hourly, model_key=model_key, _profile=profile)
    if result is None:
        return None

    _analyze_sounding_heavy(result, levels, hourly, profile)

    return result
