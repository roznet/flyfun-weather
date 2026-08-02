"""Tests for precipitation phase classification (sounding/precipitation.py)."""

from datetime import datetime

import pytest

from weatherbrief.analysis.sounding.precipitation import (
    _classify_intensity,
    _classify_levels,
    _compute_ice_fraction,
    _compute_precip_ice_fraction,
    _level_ice_fraction,
    detect_warm_nose,
    _determine_surface_phase,
    _level_phase_from_ice_fraction,
    _level_phase_from_wet_bulb,
    assess_precipitation,
)
from weatherbrief.models import (
    DerivedLevel,
    HourlyForecast,
    PrecipIntensity,
    PrecipPhase,
    PressureLevelData,
)


# --- Intensity classification ---


def test_intensity_none():
    assert _classify_intensity(None) == PrecipIntensity.NONE
    assert _classify_intensity(0.0) == PrecipIntensity.NONE


def test_intensity_light():
    assert _classify_intensity(0.5) == PrecipIntensity.LIGHT
    assert _classify_intensity(0.99) == PrecipIntensity.LIGHT


def test_intensity_moderate():
    assert _classify_intensity(1.0) == PrecipIntensity.MODERATE
    assert _classify_intensity(4.0) == PrecipIntensity.MODERATE


def test_intensity_heavy():
    assert _classify_intensity(5.0) == PrecipIntensity.HEAVY
    assert _classify_intensity(10.0) == PrecipIntensity.HEAVY


# --- Wet-bulb phase classification ---


def test_wet_bulb_snow():
    """Sub-zero wet-bulb is snow — no partial melting below Tw 0 °C."""
    assert _level_phase_from_wet_bulb(-10.0) == PrecipPhase.SNOW
    assert _level_phase_from_wet_bulb(-5.0) == PrecipPhase.SNOW
    assert _level_phase_from_wet_bulb(-0.1) == PrecipPhase.SNOW


def test_wet_bulb_mixed():
    """Melting band: begins at Tw 0 °C, complete near +1.3 (Matsuo & Sasyo)."""
    assert _level_phase_from_wet_bulb(0.0) == PrecipPhase.MIXED  # exact boundary
    assert _level_phase_from_wet_bulb(0.5) == PrecipPhase.MIXED
    assert _level_phase_from_wet_bulb(1.3) == PrecipPhase.MIXED  # exact boundary


def test_wet_bulb_rain():
    assert _level_phase_from_wet_bulb(1.4) == PrecipPhase.RAIN
    assert _level_phase_from_wet_bulb(5.0) == PrecipPhase.RAIN


def test_wet_bulb_matches_surface_fallback_convention():
    """Per-level and surface-fallback classification share one convention:
    wet snow (Tw 0..+1.3) counts toward the snow/mixed hazard band rather
    than reading as plain rain."""
    levels = [DerivedLevel(pressure_hpa=1000, altitude_ft=300, wet_bulb_c=0.8)]
    hourly = HourlyForecast(time=datetime(2025, 1, 1), precipitation_mm=1.0)
    result = assess_precipitation(levels, [], hourly=hourly)
    assert result.surface_phase == PrecipPhase.MIXED


# --- GRIB2 ice fraction ---


def test_ice_fraction_snow():
    assert _level_phase_from_ice_fraction(0.9) == PrecipPhase.SNOW


def test_ice_fraction_mixed():
    assert _level_phase_from_ice_fraction(0.5) == PrecipPhase.MIXED


def test_ice_fraction_rain():
    assert _level_phase_from_ice_fraction(0.1) == PrecipPhase.RAIN


def test_compute_ice_fraction():
    raw = PressureLevelData(
        pressure_hpa=700,
        cloud_liquid_water_kg_kg=0.0002,
        ice_mixing_ratio_kg_kg=0.0008,
    )
    frac = _compute_ice_fraction(raw)
    assert frac is not None
    assert abs(frac - 0.8) < 0.01


def test_compute_ice_fraction_zero_total():
    raw = PressureLevelData(
        pressure_hpa=700,
        cloud_liquid_water_kg_kg=0.0,
        ice_mixing_ratio_kg_kg=0.0,
    )
    assert _compute_ice_fraction(raw) is None


def test_compute_ice_fraction_missing():
    raw = PressureLevelData(pressure_hpa=700)
    assert _compute_ice_fraction(raw) is None


# --- Dry conditions ---


def test_dry_no_precipitation():
    """When no precipitation, should return DRY assessment."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, wet_bulb_c=5.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, wet_bulb_c=-2.0),
    ]
    hourly = HourlyForecast(
        time=datetime(2025, 1, 1),
        precipitation_mm=0.0,
        snowfall_cm=0.0,
    )
    result = assess_precipitation(levels, [], hourly=hourly)
    assert result.surface_phase == PrecipPhase.DRY
    assert result.surface_intensity == PrecipIntensity.NONE
    assert result.precipitation_zones == []


def test_dry_none_precipitation():
    """When precipitation is None, should return DRY assessment."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, wet_bulb_c=5.0),
    ]
    hourly = HourlyForecast(time=datetime(2025, 1, 1))
    result = assess_precipitation(levels, [], hourly=hourly)
    assert result.surface_phase == PrecipPhase.DRY


# --- Pure snow profile ---


def test_pure_snow():
    """All levels below freezing → SNOW surface phase and all-snow zones."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, wet_bulb_c=-8.0),
        DerivedLevel(pressure_hpa=925, altitude_ft=2500, wet_bulb_c=-10.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, wet_bulb_c=-12.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, wet_bulb_c=-18.0),
    ]
    hourly = HourlyForecast(
        time=datetime(2025, 1, 1),
        precipitation_mm=2.0,
        snowfall_cm=2.5,
        rain_mm=0.0,
        temperature_2m_c=-5.0,
    )
    result = assess_precipitation(levels, [], hourly=hourly)
    assert result.surface_phase == PrecipPhase.SNOW
    assert result.surface_intensity == PrecipIntensity.MODERATE
    # All zones should be SNOW
    for zone in result.precipitation_zones:
        assert zone.phase == PrecipPhase.SNOW


# --- Pure rain profile ---


def test_pure_rain():
    """Warm column → RAIN surface phase."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, wet_bulb_c=10.0),
        DerivedLevel(pressure_hpa=925, altitude_ft=2500, wet_bulb_c=7.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, wet_bulb_c=4.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, wet_bulb_c=0.5),
    ]
    hourly = HourlyForecast(
        time=datetime(2025, 1, 1),
        precipitation_mm=3.0,
        rain_mm=3.0,
        snowfall_cm=0.0,
    )
    result = assess_precipitation(levels, [], hourly=hourly)
    assert result.surface_phase == PrecipPhase.RAIN
    assert result.surface_intensity == PrecipIntensity.MODERATE


# --- Mixed precipitation ---


def test_mixed_precipitation():
    """Both rain and snow reported → MIXED."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, wet_bulb_c=0.5),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, wet_bulb_c=-3.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, wet_bulb_c=-10.0),
    ]
    hourly = HourlyForecast(
        time=datetime(2025, 1, 1),
        precipitation_mm=4.0,
        rain_mm=2.0,
        snowfall_cm=1.5,
    )
    result = assess_precipitation(levels, [], hourly=hourly)
    assert result.surface_phase == PrecipPhase.MIXED


# --- Freezing rain profile ---


def test_freezing_rain_warm_nose():
    """Classic freezing rain: cold surface, warm nose above, cold aloft."""
    levels = [
        # Surface: cold
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, wet_bulb_c=-3.0),
        DerivedLevel(pressure_hpa=975, altitude_ft=1000, wet_bulb_c=-2.0),
        # Warm nose
        DerivedLevel(pressure_hpa=925, altitude_ft=2500, wet_bulb_c=2.0),
        DerivedLevel(pressure_hpa=900, altitude_ft=3500, wet_bulb_c=3.0),
        DerivedLevel(pressure_hpa=875, altitude_ft=4200, wet_bulb_c=1.5),
        # Cold aloft
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, wet_bulb_c=-4.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, wet_bulb_c=-12.0),
    ]
    hourly = HourlyForecast(
        time=datetime(2025, 1, 1),
        precipitation_mm=2.0,
        snowfall_cm=0.0,
    )
    result = assess_precipitation(levels, [], hourly=hourly)
    assert result.freezing_rain_risk is True
    assert result.surface_phase == PrecipPhase.FREEZING_RAIN
    assert result.warm_nose_base_ft is not None
    assert result.warm_nose_top_ft is not None


def test_ice_pellets_deep_cold():
    """Deep cold surface layer + warm nose → ice pellets."""
    levels = [
        # Deep cold surface layer
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, wet_bulb_c=-5.0),
        DerivedLevel(pressure_hpa=975, altitude_ft=1000, wet_bulb_c=-4.0),
        DerivedLevel(pressure_hpa=950, altitude_ft=1700, wet_bulb_c=-3.0),
        # Warm nose
        DerivedLevel(pressure_hpa=925, altitude_ft=2500, wet_bulb_c=2.0),
        DerivedLevel(pressure_hpa=900, altitude_ft=3500, wet_bulb_c=3.0),
        # Cold aloft
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, wet_bulb_c=-6.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, wet_bulb_c=-15.0),
    ]
    hourly = HourlyForecast(
        time=datetime(2025, 1, 1),
        precipitation_mm=2.0,
        snowfall_cm=0.0,
    )
    result = assess_precipitation(levels, [], hourly=hourly)
    assert result.surface_phase == PrecipPhase.ICE_PELLETS


# --- GRIB2-enhanced classification ---


def test_grib2_ice_fraction_classification():
    """GRIB2 CLWMR/ICMR should override wet-bulb classification."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, wet_bulb_c=2.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, wet_bulb_c=-3.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, wet_bulb_c=-8.0),
    ]
    raw_levels = [
        PressureLevelData(pressure_hpa=1000),
        PressureLevelData(
            pressure_hpa=850,
            cloud_liquid_water_kg_kg=0.0001,
            ice_mixing_ratio_kg_kg=0.0009,  # 90% ice → SNOW
        ),
        PressureLevelData(
            pressure_hpa=700,
            cloud_liquid_water_kg_kg=0.0,
            ice_mixing_ratio_kg_kg=0.001,  # 100% ice → SNOW
        ),
    ]
    hourly = HourlyForecast(
        time=datetime(2025, 1, 1),
        precipitation_mm=1.5,
        snowfall_cm=1.0,
        rain_mm=0.0,
    )
    result = assess_precipitation(levels, raw_levels, hourly=hourly)
    # Upper levels should be classified as SNOW based on ice fraction
    snow_zones = [z for z in result.precipitation_zones if z.phase == PrecipPhase.SNOW]
    assert len(snow_zones) >= 1
    # At least one zone should have ice_fraction set
    zones_with_ice = [z for z in result.precipitation_zones if z.ice_fraction is not None]
    assert len(zones_with_ice) >= 1


# --- Zone grouping ---


def test_zone_grouping():
    """Adjacent levels with the same phase should be merged into one zone."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, wet_bulb_c=5.0),
        DerivedLevel(pressure_hpa=925, altitude_ft=2500, wet_bulb_c=3.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, wet_bulb_c=-3.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, wet_bulb_c=-8.0),
    ]
    hourly = HourlyForecast(
        time=datetime(2025, 1, 1),
        precipitation_mm=2.0,
        rain_mm=1.5,
        snowfall_cm=0.5,
    )
    result = assess_precipitation(levels, [], hourly=hourly)
    # Should have zones for RAIN (surface levels) and SNOW (upper levels)
    phases = [z.phase for z in result.precipitation_zones]
    assert PrecipPhase.RAIN in phases
    assert PrecipPhase.SNOW in phases


# --- Warm nose detection ---


def test_no_warm_nose_warm_surface():
    """No warm nose when surface is warm."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, wet_bulb_c=5.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, wet_bulb_c=0.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, wet_bulb_c=-5.0),
    ]
    fz_risk, base, top, ice = detect_warm_nose(levels)
    assert fz_risk is False
    assert base is None


def test_no_warm_nose_monotone_cold():
    """No warm nose when temperature decreases monotonically."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, wet_bulb_c=-2.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, wet_bulb_c=-5.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, wet_bulb_c=-10.0),
    ]
    fz_risk, base, top, ice = detect_warm_nose(levels)
    assert fz_risk is False


# --- Surface phase fallback ---


def test_surface_phase_fallback_wet_bulb():
    """When rain/snow breakdown unavailable, use surface wet-bulb."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, wet_bulb_c=-2.0),
    ]
    hourly = HourlyForecast(
        time=datetime(2025, 1, 1),
        precipitation_mm=1.0,
        # rain_mm and snowfall_cm are None (unavailable model)
    )
    result = assess_precipitation(levels, [], hourly=hourly)
    # Surface wet-bulb < 0 → SNOW
    assert result.surface_phase == PrecipPhase.SNOW


def test_surface_phase_fallback_temperature():
    """When wet-bulb unavailable, use surface temperature."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300),  # no wet_bulb
    ]
    hourly = HourlyForecast(
        time=datetime(2025, 1, 1),
        precipitation_mm=1.0,
        temperature_2m_c=-2.0,
    )
    result = assess_precipitation(levels, [], hourly=hourly)
    assert result.surface_phase == PrecipPhase.SNOW


# --- Pass-through of surface amounts ---


def test_surface_amounts_passed_through():
    """Surface rain/snow/total should be passed through to assessment."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, wet_bulb_c=5.0),
    ]
    hourly = HourlyForecast(
        time=datetime(2025, 1, 1),
        precipitation_mm=3.5,
        rain_mm=2.5,
        snowfall_cm=0.0,
    )
    result = assess_precipitation(levels, [], hourly=hourly)
    assert result.total_mm == 3.5
    assert result.rain_mm == 2.5
    assert result.snow_cm == 0.0


# --- Precipitation partition from qr/qs/qg (#530) ---
#
# Three tiers, in order: the genuine precipitation partition (qr vs qs+qg)
# where the model publishes the falling species, the cloud partition
# (clwmr vs icmr) as the historical proxy, then wet-bulb. The tests below pin
# each tier AND the hand-off between them, because the failure mode of getting
# the order wrong is invisible: every tier returns a plausible phase.


def test_precip_partition_all_snow():
    raw = PressureLevelData(
        pressure_hpa=850,
        rain_water_kg_kg=0.0,
        snow_water_kg_kg=5e-4,
        graupel_water_kg_kg=1e-5,
    )
    assert _compute_precip_ice_fraction(raw) == 1.0
    assert _level_phase_from_ice_fraction(
        _compute_precip_ice_fraction(raw),
    ) == PrecipPhase.SNOW


def test_precip_partition_all_rain():
    raw = PressureLevelData(
        pressure_hpa=925,
        rain_water_kg_kg=4e-4,
        snow_water_kg_kg=0.0,
        graupel_water_kg_kg=0.0,
    )
    assert _compute_precip_ice_fraction(raw) == 0.0
    assert _level_phase_from_ice_fraction(
        _compute_precip_ice_fraction(raw),
    ) == PrecipPhase.RAIN


def test_precip_partition_mixed():
    raw = PressureLevelData(
        pressure_hpa=900,
        rain_water_kg_kg=2e-4,
        snow_water_kg_kg=2e-4,
        graupel_water_kg_kg=0.0,
    )
    assert _compute_precip_ice_fraction(raw) == 0.5
    assert _level_phase_from_ice_fraction(
        _compute_precip_ice_fraction(raw),
    ) == PrecipPhase.MIXED


def test_precip_partition_counts_graupel_as_frozen():
    """qg is frequently 0 — but when it is not, it is ice, not rain."""
    raw = PressureLevelData(
        pressure_hpa=700,
        rain_water_kg_kg=1e-4,
        snow_water_kg_kg=0.0,
        graupel_water_kg_kg=9e-4,
    )
    assert _compute_precip_ice_fraction(raw) == pytest.approx(0.9)


def test_precip_partition_tolerates_a_single_missing_species():
    """A model writing qr and qs but no qg is saying "no graupel", not
    "unknown graupel" — the same missing-vs-zero rule the cloud builder uses."""
    raw = PressureLevelData(
        pressure_hpa=900, rain_water_kg_kg=1e-4, snow_water_kg_kg=3e-4,
    )
    assert _compute_precip_ice_fraction(raw) == 0.75


def test_precip_partition_absent_when_no_species_reported():
    """ICON-EU / GFS / ECMWF: nothing published, so nothing to partition."""
    raw = PressureLevelData(pressure_hpa=850, cloud_liquid_water_kg_kg=1e-4)
    assert _compute_precip_ice_fraction(raw) is None


def test_precip_partition_absent_when_nothing_is_falling():
    """All three reported as zero: no precipitation at this level, so the
    partition is undefined — not "0% ice", which would read as RAIN."""
    raw = PressureLevelData(
        pressure_hpa=850,
        rain_water_kg_kg=0.0,
        snow_water_kg_kg=0.0,
        graupel_water_kg_kg=0.0,
    )
    assert _compute_precip_ice_fraction(raw) is None


# --- The three-tier fallback chain ---


def test_precip_partition_wins_over_the_cloud_proxy():
    """The decisive case: cloud is glaciated (would say SNOW) while what is
    actually falling is rain. Only the precipitation partition gets this right."""
    raw = PressureLevelData(
        pressure_hpa=900,
        cloud_liquid_water_kg_kg=1e-5,
        ice_mixing_ratio_kg_kg=9e-5,   # 90% cloud ice → the proxy says SNOW
        rain_water_kg_kg=5e-4,          # ...but rain is what is falling
        snow_water_kg_kg=0.0,
        graupel_water_kg_kg=0.0,
    )
    assert _compute_ice_fraction(raw) == 0.9
    assert _level_ice_fraction(raw) == 0.0

    levels = [DerivedLevel(pressure_hpa=900, altitude_ft=3000, wet_bulb_c=-4.0)]
    phases = _classify_levels(levels, {900: raw})
    assert phases[0][1] == PrecipPhase.RAIN


def test_falls_back_to_the_cloud_proxy_when_species_are_absent():
    raw = PressureLevelData(
        pressure_hpa=850,
        cloud_liquid_water_kg_kg=1e-5,
        ice_mixing_ratio_kg_kg=9e-5,
    )
    assert _level_ice_fraction(raw) == 0.9

    levels = [DerivedLevel(pressure_hpa=850, altitude_ft=5000, wet_bulb_c=5.0)]
    phases = _classify_levels(levels, {850: raw})
    assert phases[0][1] == PrecipPhase.SNOW  # cloud proxy, not the warm wet-bulb


def test_falls_back_to_wet_bulb_when_no_condensate_at_all():
    raw = PressureLevelData(pressure_hpa=850)
    assert _level_ice_fraction(raw) is None

    levels = [DerivedLevel(pressure_hpa=850, altitude_ft=5000, wet_bulb_c=-4.0)]
    phases = _classify_levels(levels, {850: raw})
    assert phases[0][1] == PrecipPhase.SNOW


def test_zone_ice_fraction_uses_the_same_tier_as_the_levels():
    """The zone mean and the per-level phase must never be graded on different
    tiers — a zone labelled RAIN with ice_fraction 0.9 is unreadable."""
    raw = PressureLevelData(
        pressure_hpa=900,
        cloud_liquid_water_kg_kg=1e-5,
        ice_mixing_ratio_kg_kg=9e-5,
        rain_water_kg_kg=5e-4,
        snow_water_kg_kg=0.0,
        graupel_water_kg_kg=0.0,
    )
    levels = [DerivedLevel(pressure_hpa=900, altitude_ft=3000, wet_bulb_c=-4.0)]
    hourly = HourlyForecast(time=datetime(2025, 1, 1), precipitation_mm=2.0)

    result = assess_precipitation(levels, [raw], hourly=hourly)
    zone = result.precipitation_zones[0]
    assert zone.phase == PrecipPhase.RAIN
    assert zone.ice_fraction == 0.0
