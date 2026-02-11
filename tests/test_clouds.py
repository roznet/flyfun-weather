"""Tests for cloud layer estimation."""

from weatherbrief.analysis.clouds import estimate_cloud_layers
from weatherbrief.models import PressureLevelData


def test_single_cloud_layer():
    """Detects a single cloud layer where RH >= 80%."""
    levels = [
        PressureLevelData(pressure_hpa=1000, relative_humidity_pct=50,
                          geopotential_height_m=100),
        PressureLevelData(pressure_hpa=925, relative_humidity_pct=85,
                          geopotential_height_m=770),
        PressureLevelData(pressure_hpa=850, relative_humidity_pct=90,
                          geopotential_height_m=1450),
        PressureLevelData(pressure_hpa=700, relative_humidity_pct=50,
                          geopotential_height_m=3010),
    ]
    layers = estimate_cloud_layers(levels)
    assert len(layers) == 1
    # Base at 925 level (~770m -> ~2526ft)
    assert abs(layers[0].base_ft - 770 * 3.28084) < 1
    # Top at 700 level (~3010m -> ~9875ft)
    assert abs(layers[0].top_ft - 3010 * 3.28084) < 1


def test_no_cloud():
    """No cloud when all RH below threshold."""
    levels = [
        PressureLevelData(pressure_hpa=1000, relative_humidity_pct=50,
                          geopotential_height_m=100),
        PressureLevelData(pressure_hpa=850, relative_humidity_pct=60,
                          geopotential_height_m=1450),
        PressureLevelData(pressure_hpa=700, relative_humidity_pct=40,
                          geopotential_height_m=3010),
    ]
    layers = estimate_cloud_layers(levels)
    assert len(layers) == 0


def test_two_layers():
    """Detects two separate cloud layers."""
    levels = [
        PressureLevelData(pressure_hpa=1000, relative_humidity_pct=85,
                          geopotential_height_m=100),
        PressureLevelData(pressure_hpa=925, relative_humidity_pct=50,
                          geopotential_height_m=770),
        PressureLevelData(pressure_hpa=850, relative_humidity_pct=90,
                          geopotential_height_m=1450),
        PressureLevelData(pressure_hpa=700, relative_humidity_pct=50,
                          geopotential_height_m=3010),
    ]
    layers = estimate_cloud_layers(levels)
    assert len(layers) == 2


def test_cloud_top_unknown():
    """Cloud extending to highest level has unknown top."""
    levels = [
        PressureLevelData(pressure_hpa=1000, relative_humidity_pct=50,
                          geopotential_height_m=100),
        PressureLevelData(pressure_hpa=500, relative_humidity_pct=85,
                          geopotential_height_m=5500),
        PressureLevelData(pressure_hpa=300, relative_humidity_pct=90,
                          geopotential_height_m=9100),
    ]
    layers = estimate_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].top_ft is None
    assert layers[0].note == "estimated, top unknown"


def test_missing_rh_skipped():
    """Levels with missing RH or geopotential are skipped."""
    levels = [
        PressureLevelData(pressure_hpa=1000, relative_humidity_pct=None,
                          geopotential_height_m=100),
        PressureLevelData(pressure_hpa=850, relative_humidity_pct=90,
                          geopotential_height_m=None),
        PressureLevelData(pressure_hpa=700, relative_humidity_pct=50,
                          geopotential_height_m=3010),
    ]
    layers = estimate_cloud_layers(levels)
    assert len(layers) == 0
