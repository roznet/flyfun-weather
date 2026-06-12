"""Precipitation phase classification and intensity assessment.

Classifies precipitation type at each pressure level using wet-bulb temperature
(primary) and GRIB2 ice/liquid mixing ratios (when available). Detects warm-nose
profiles for freezing rain / ice pellet risk.
"""

from __future__ import annotations

from weatherbrief.models import (
    DerivedLevel,
    HourlyForecast,
    PrecipIntensity,
    PrecipPhase,
    PrecipitationAssessment,
    PrecipitationZone,
    PressureLevelData,
)


def assess_precipitation(
    levels: list[DerivedLevel],
    raw_levels: list[PressureLevelData],
    hourly: HourlyForecast | None = None,
    freezing_level_ft: float | None = None,
) -> PrecipitationAssessment:
    """Assess precipitation phase and intensity from sounding data.

    Args:
        levels: Derived levels sorted by descending pressure (surface first).
        raw_levels: Raw pressure level data (for CLWMR/ICMR).
        hourly: Surface forecast data for the same time step.
        freezing_level_ft: Freezing level from thermodynamic indices.

    Returns:
        PrecipitationAssessment with surface phase, intensity, and vertical zones.
    """
    precip_mm = hourly.precipitation_mm if hourly else None
    snowfall_cm = hourly.snowfall_cm if hourly else None
    rain_mm = hourly.rain_mm if hourly else None
    showers_mm = hourly.showers_mm if hourly else None

    # Check if any precipitation is occurring
    has_precip = (
        (precip_mm is not None and precip_mm > 0)
        or (snowfall_cm is not None and snowfall_cm > 0)
    )
    if not has_precip:
        return PrecipitationAssessment(
            rain_mm=rain_mm,
            snow_cm=snowfall_cm,
            total_mm=precip_mm,
        )

    # Classify surface intensity from total precipitation rate
    intensity = _classify_intensity(precip_mm)

    # Build raw_levels lookup for GRIB2 data
    raw_by_pressure: dict[int, PressureLevelData] = {
        lv.pressure_hpa: lv for lv in raw_levels
    }

    # Per-level phase classification (top-down)
    level_phases = _classify_levels(levels, raw_by_pressure)

    # Warm nose / freezing rain detection
    freezing_rain_risk, warm_nose_base, warm_nose_top, ice_pellets = (
        detect_warm_nose(levels)
    )

    # Determine surface phase
    surface_phase = _determine_surface_phase(
        hourly, levels, freezing_rain_risk, ice_pellets,
    )

    # Override surface phase for ice pellets
    if ice_pellets:
        surface_phase = PrecipPhase.ICE_PELLETS

    # Group adjacent levels into zones
    zones = _build_precipitation_zones(levels, raw_by_pressure)

    return PrecipitationAssessment(
        surface_phase=surface_phase,
        surface_intensity=intensity,
        precipitation_zones=zones,
        freezing_rain_risk=freezing_rain_risk,
        warm_nose_base_ft=warm_nose_base,
        warm_nose_top_ft=warm_nose_top,
        rain_mm=rain_mm if rain_mm is not None else (
            (precip_mm - (snowfall_cm or 0) * 10) if precip_mm else None
        ),
        snow_cm=snowfall_cm,
        total_mm=precip_mm,
    )


def _classify_intensity(precip_mm: float | None) -> PrecipIntensity:
    """Classify precipitation intensity from hourly total (mm/h)."""
    if precip_mm is None or precip_mm <= 0:
        return PrecipIntensity.NONE
    if precip_mm < 1.0:
        return PrecipIntensity.LIGHT
    if precip_mm <= 4.0:
        return PrecipIntensity.MODERATE
    return PrecipIntensity.HEAVY


def _level_phase_from_wet_bulb(wet_bulb_c: float) -> PrecipPhase:
    """Classify precipitation phase from wet-bulb temperature."""
    if wet_bulb_c < -5.0:
        return PrecipPhase.SNOW
    if wet_bulb_c < 0.0:
        return PrecipPhase.MIXED
    return PrecipPhase.RAIN


def _level_phase_from_ice_fraction(ice_fraction: float) -> PrecipPhase:
    """Classify precipitation phase from GRIB2 ice fraction."""
    if ice_fraction > 0.8:
        return PrecipPhase.SNOW
    if ice_fraction >= 0.2:
        return PrecipPhase.MIXED
    return PrecipPhase.RAIN


def _compute_ice_fraction(raw: PressureLevelData) -> float | None:
    """Compute ice fraction from CLWMR and ICMR, guarding div-by-zero."""
    clwmr = raw.cloud_liquid_water_kg_kg
    icmr = raw.ice_mixing_ratio_kg_kg
    if clwmr is None or icmr is None:
        return None
    total = clwmr + icmr
    if total <= 0:
        return None
    return icmr / total


def _classify_levels(
    levels: list[DerivedLevel],
    raw_by_pressure: dict[int, PressureLevelData],
) -> list[tuple[DerivedLevel, PrecipPhase]]:
    """Classify phase at each level and store on DerivedLevel.precip_phase."""
    result: list[tuple[DerivedLevel, PrecipPhase]] = []
    for lv in levels:
        if lv.altitude_ft is None:
            continue

        # Try GRIB2-based classification first
        raw = raw_by_pressure.get(lv.pressure_hpa)
        ice_frac = _compute_ice_fraction(raw) if raw else None
        if ice_frac is not None:
            phase = _level_phase_from_ice_fraction(ice_frac)
        elif lv.wet_bulb_c is not None:
            phase = _level_phase_from_wet_bulb(lv.wet_bulb_c)
        else:
            continue

        lv.precip_phase = phase.value
        result.append((lv, phase))

    return result


def detect_warm_nose(
    levels: list[DerivedLevel],
) -> tuple[bool, float | None, float | None, bool]:
    """Detect warm nose above a cold surface for freezing rain / ice pellets.

    Pure profile-shape check (wet-bulb based) — independent of whether
    precipitation is currently falling, so callers can also flag a *primed*
    profile (freezing-rain shape without active precip yet).

    Returns:
        (freezing_rain_risk, warm_nose_base_ft, warm_nose_top_ft, ice_pellets)
    """
    # Need at least a few levels to detect a warm nose
    valid = [lv for lv in levels if lv.wet_bulb_c is not None and lv.altitude_ft is not None]
    if len(valid) < 3:
        return False, None, None, False

    # Surface level is first (highest pressure = lowest altitude)
    surface_tw = valid[0].wet_bulb_c
    if surface_tw is None or surface_tw >= 0.0:
        # Surface is warm — no freezing rain concern
        return False, None, None, False

    # Walk upward (decreasing pressure) looking for warm nose (Tw > 0)
    warm_nose_base_ft: float | None = None
    warm_nose_top_ft: float | None = None
    cold_surface_depth = 0

    for lv in valid:
        if lv.wet_bulb_c < 0.0 and warm_nose_base_ft is None:
            cold_surface_depth += 1
        elif lv.wet_bulb_c >= 0.0:
            if warm_nose_base_ft is None:
                warm_nose_base_ft = lv.altitude_ft
            warm_nose_top_ft = lv.altitude_ft
        elif warm_nose_base_ft is not None:
            # Back to cold above the warm nose — stop
            break

    if warm_nose_base_ft is None:
        return False, None, None, False

    # Count warm levels in the nose
    warm_levels = sum(
        1 for lv in valid
        if lv.altitude_ft is not None
        and warm_nose_base_ft <= lv.altitude_ft <= warm_nose_top_ft
        and lv.wet_bulb_c >= 0.0
    )

    # Deep cold surface layer + significant warm nose → ice pellets
    # Shallow cold surface layer + warm nose → freezing rain
    ice_pellets = cold_surface_depth >= 3 and warm_levels >= 2
    freezing_rain = not ice_pellets and warm_levels >= 2

    return (
        freezing_rain,
        warm_nose_base_ft,
        warm_nose_top_ft,
        ice_pellets,
    )


def _determine_surface_phase(
    hourly: HourlyForecast | None,
    levels: list[DerivedLevel],
    freezing_rain_risk: bool,
    ice_pellets: bool,
) -> PrecipPhase:
    """Determine the surface precipitation phase."""
    if freezing_rain_risk:
        return PrecipPhase.FREEZING_RAIN
    if ice_pellets:
        return PrecipPhase.ICE_PELLETS

    rain_mm = hourly.rain_mm if hourly else None
    snowfall_cm = hourly.snowfall_cm if hourly else None

    # When rain/snow breakdown is available from the model
    has_rain = rain_mm is not None and rain_mm > 0
    has_snow = snowfall_cm is not None and snowfall_cm > 0

    if has_snow and not has_rain:
        return PrecipPhase.SNOW
    if has_rain and not has_snow:
        return PrecipPhase.RAIN
    if has_rain and has_snow:
        return PrecipPhase.MIXED

    # Fallback: use surface wet-bulb from lowest DerivedLevel
    if levels:
        surface = levels[0]
        if surface.wet_bulb_c is not None:
            if surface.wet_bulb_c < 0.0:
                return PrecipPhase.SNOW
            if surface.wet_bulb_c <= 1.3:
                return PrecipPhase.MIXED
            return PrecipPhase.RAIN

    # Final fallback: check surface temperature
    if hourly and hourly.temperature_2m_c is not None:
        if hourly.temperature_2m_c < 0.5:
            return PrecipPhase.SNOW
        return PrecipPhase.RAIN

    return PrecipPhase.RAIN


def _build_precipitation_zones(
    levels: list[DerivedLevel],
    raw_by_pressure: dict[int, PressureLevelData],
) -> list[PrecipitationZone]:
    """Group adjacent levels with the same precip phase into zones."""
    # Filter to levels with a classified phase
    classified = [
        lv for lv in levels
        if lv.precip_phase is not None and lv.altitude_ft is not None
    ]
    if not classified:
        return []

    zones: list[PrecipitationZone] = []
    current: list[DerivedLevel] = [classified[0]]

    for lv in classified[1:]:
        if lv.precip_phase == current[-1].precip_phase:
            current.append(lv)
        else:
            zones.append(_finalize_zone(current, raw_by_pressure))
            current = [lv]

    zones.append(_finalize_zone(current, raw_by_pressure))
    return zones


def _finalize_zone(
    zone_levels: list[DerivedLevel],
    raw_by_pressure: dict[int, PressureLevelData],
) -> PrecipitationZone:
    """Create a PrecipitationZone from a group of same-phase levels."""
    base = zone_levels[0]
    top = zone_levels[-1]

    wb_vals = [lv.wet_bulb_c for lv in zone_levels if lv.wet_bulb_c is not None]
    mean_wb = round(sum(wb_vals) / len(wb_vals), 1) if wb_vals else None

    # Compute mean ice fraction if GRIB2 data available
    ice_fracs: list[float] = []
    for lv in zone_levels:
        raw = raw_by_pressure.get(lv.pressure_hpa)
        if raw:
            frac = _compute_ice_fraction(raw)
            if frac is not None:
                ice_fracs.append(frac)
    mean_ice_frac = round(sum(ice_fracs) / len(ice_fracs), 2) if ice_fracs else None

    return PrecipitationZone(
        base_ft=round(base.altitude_ft),
        top_ft=round(top.altitude_ft),
        base_pressure_hpa=base.pressure_hpa,
        top_pressure_hpa=top.pressure_hpa,
        phase=PrecipPhase(base.precip_phase),
        mean_wet_bulb_c=mean_wb,
        ice_fraction=mean_ice_frac,
    )
