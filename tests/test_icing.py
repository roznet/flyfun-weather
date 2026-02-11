"""Tests for icing band analysis."""

from weatherbrief.analysis.icing import assess_icing_at_level, assess_icing_profile
from weatherbrief.models import IcingRisk, PressureLevelData


def test_no_icing_warm():
    """No icing when temperature above freezing."""
    level = PressureLevelData(
        pressure_hpa=850, temperature_c=5, relative_humidity_pct=95,
        geopotential_height_m=1500,
    )
    result = assess_icing_at_level(level)
    assert result.risk == IcingRisk.NONE


def test_no_icing_too_cold():
    """No icing when temperature below -20C."""
    level = PressureLevelData(
        pressure_hpa=400, temperature_c=-25, relative_humidity_pct=90,
        geopotential_height_m=7000,
    )
    result = assess_icing_at_level(level)
    assert result.risk == IcingRisk.NONE


def test_no_icing_dry():
    """No icing when humidity is low even in temp range."""
    level = PressureLevelData(
        pressure_hpa=700, temperature_c=-5, relative_humidity_pct=40,
        geopotential_height_m=3000,
    )
    result = assess_icing_at_level(level)
    assert result.risk == IcingRisk.NONE


def test_severe_icing():
    """Severe icing: 0 to -10C, very high humidity."""
    level = PressureLevelData(
        pressure_hpa=850, temperature_c=-3, relative_humidity_pct=95,
        geopotential_height_m=1500,
    )
    result = assess_icing_at_level(level)
    assert result.risk == IcingRisk.SEVERE


def test_moderate_icing_warm_band():
    """Moderate icing: 0 to -10C, high humidity but not extreme."""
    level = PressureLevelData(
        pressure_hpa=850, temperature_c=-5, relative_humidity_pct=85,
        geopotential_height_m=1500,
    )
    result = assess_icing_at_level(level)
    assert result.risk == IcingRisk.MODERATE


def test_light_icing_cold_band():
    """Light icing: -10 to -20C, moderate humidity."""
    level = PressureLevelData(
        pressure_hpa=600, temperature_c=-15, relative_humidity_pct=75,
        geopotential_height_m=4200,
    )
    result = assess_icing_at_level(level)
    assert result.risk == IcingRisk.LIGHT


def test_profile_returns_all_levels(sample_pressure_levels):
    """assess_icing_profile returns one band per pressure level."""
    result = assess_icing_profile(sample_pressure_levels)
    assert len(result) == len(sample_pressure_levels)


def test_altitude_in_feet():
    """Altitude is converted to feet from geopotential height."""
    level = PressureLevelData(
        pressure_hpa=850, temperature_c=-2, relative_humidity_pct=90,
        geopotential_height_m=1500,
    )
    result = assess_icing_at_level(level)
    assert result.altitude_ft is not None
    assert abs(result.altitude_ft - 4921) < 2  # 1500m * 3.28084
