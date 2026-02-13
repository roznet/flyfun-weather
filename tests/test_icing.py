"""Tests for enhanced icing assessment (sounding/icing.py)."""

from weatherbrief.analysis.sounding.icing import assess_icing_zones
from weatherbrief.models import DerivedLevel, EnhancedCloudLayer, IcingRisk, IcingType


def _cloud(base_ft, top_ft):
    """Helper to create a cloud layer."""
    return EnhancedCloudLayer(base_ft=base_ft, top_ft=top_ft)


def test_no_icing_warm():
    """No icing when wet-bulb above 0C."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, wet_bulb_c=3.0,
                     dewpoint_depression_c=2.0),
    ]
    zones = assess_icing_zones(levels, [_cloud(4000, 6000)])
    assert len(zones) == 0


def test_no_icing_too_cold():
    """No icing when wet-bulb below -20C."""
    levels = [
        DerivedLevel(pressure_hpa=400, altitude_ft=24000, wet_bulb_c=-25.0,
                     dewpoint_depression_c=2.0),
    ]
    zones = assess_icing_zones(levels, [_cloud(23000, 25000)])
    assert len(zones) == 0


def test_no_icing_dry():
    """No icing when not near cloud (high dewpoint depression)."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, wet_bulb_c=-5.0,
                     dewpoint_depression_c=10.0),
    ]
    # No cloud layers nearby
    zones = assess_icing_zones(levels, [])
    assert len(zones) == 0


def test_severe_clear_icing():
    """Severe clear icing: wet-bulb -3C to 0C, in cloud."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, wet_bulb_c=-1.5,
                     dewpoint_depression_c=1.0, temperature_c=-2.0),
    ]
    zones = assess_icing_zones(levels, [_cloud(4000, 6000)])
    assert len(zones) == 1
    assert zones[0].risk == IcingRisk.SEVERE
    assert zones[0].icing_type == IcingType.CLEAR


def test_moderate_mixed_icing():
    """Moderate mixed icing: wet-bulb -10C to -3C, in cloud."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, wet_bulb_c=-6.0,
                     dewpoint_depression_c=1.5, temperature_c=-8.0),
    ]
    zones = assess_icing_zones(levels, [_cloud(9000, 11000)])
    assert len(zones) == 1
    assert zones[0].risk == IcingRisk.MODERATE
    assert zones[0].icing_type == IcingType.MIXED


def test_light_rime_icing():
    """Light rime icing: wet-bulb -20C to -15C."""
    levels = [
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, wet_bulb_c=-17.0,
                     dewpoint_depression_c=2.0, temperature_c=-18.0),
    ]
    zones = assess_icing_zones(levels, [_cloud(17000, 19000)])
    assert len(zones) == 1
    assert zones[0].risk == IcingRisk.LIGHT
    assert zones[0].icing_type == IcingType.RIME


def test_severity_enhanced_by_high_rh():
    """RH > 95% upgrades moderate to severe."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, wet_bulb_c=-5.0,
                     dewpoint_depression_c=0.5, temperature_c=-6.0,
                     relative_humidity_pct=97.0),
    ]
    zones = assess_icing_zones(levels, [_cloud(9000, 11000)])
    assert len(zones) == 1
    assert zones[0].risk == IcingRisk.SEVERE


def test_near_cloud_margin():
    """Level within 500ft of cloud boundary is assessed."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=6400, wet_bulb_c=-2.0,
                     dewpoint_depression_c=5.0, temperature_c=-3.0),
    ]
    # Cloud top at 6000, level at 6400 = 400ft above = within 500ft margin
    zones = assess_icing_zones(levels, [_cloud(4000, 6000)])
    assert len(zones) == 1


def test_adjacent_levels_grouped():
    """Adjacent icing levels (gap <= 100hPa) are grouped into a single zone."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, wet_bulb_c=-7.0,
                     dewpoint_depression_c=1.0, temperature_c=-8.0),
        DerivedLevel(pressure_hpa=800, altitude_ft=6500, wet_bulb_c=-8.0,
                     dewpoint_depression_c=1.5, temperature_c=-9.0),
    ]
    zones = assess_icing_zones(levels, [_cloud(4000, 7000)])
    assert len(zones) == 1
    assert zones[0].base_ft == 5000
    assert zones[0].top_ft == 6500
    assert zones[0].risk == IcingRisk.MODERATE


def test_empty_levels():
    """Empty input returns empty list."""
    assert assess_icing_zones([], []) == []
