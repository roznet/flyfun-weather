"""Tests for enhanced cloud layer detection (sounding/clouds.py)."""

from weatherbrief.analysis.sounding.clouds import detect_cloud_layers
from weatherbrief.models import DerivedLevel


def test_single_cloud_layer():
    """Detects a single cloud layer where dewpoint depression < 3C."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=8.0),
        DerivedLevel(pressure_hpa=925, altitude_ft=2530, dewpoint_depression_c=2.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=1.5),
        DerivedLevel(pressure_hpa=700, altitude_ft=9880, dewpoint_depression_c=10.0),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].base_ft == 2530
    assert layers[0].top_ft == 4760


def test_no_cloud():
    """No cloud when all dewpoint depression above threshold."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=5.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=8.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=9880, dewpoint_depression_c=12.0),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 0


def test_two_layers():
    """Detects two separate cloud layers."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=1.5),
        DerivedLevel(pressure_hpa=925, altitude_ft=2530, dewpoint_depression_c=8.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=2.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=9880, dewpoint_depression_c=6.0),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 2


def test_cloud_extending_to_top():
    """Cloud extending to top of profile is captured."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=8.0),
        DerivedLevel(pressure_hpa=500, altitude_ft=18040, dewpoint_depression_c=1.5),
        DerivedLevel(pressure_hpa=300, altitude_ft=29860, dewpoint_depression_c=2.0),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].base_ft == 18040
    assert layers[0].top_ft == 29860


def test_coverage_ovc():
    """Mean DD < 1C classifies as OVC."""
    levels = [
        DerivedLevel(pressure_hpa=925, altitude_ft=2530, dewpoint_depression_c=0.5),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=0.8),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].coverage.value == "ovc"


def test_coverage_bkn():
    """Mean DD 1-2C classifies as BKN."""
    levels = [
        DerivedLevel(pressure_hpa=925, altitude_ft=2530, dewpoint_depression_c=1.2),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=1.8),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].coverage.value == "bkn"


def test_coverage_sct():
    """Mean DD 2-3C classifies as SCT."""
    levels = [
        DerivedLevel(pressure_hpa=925, altitude_ft=2530, dewpoint_depression_c=2.5),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=2.8),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].coverage.value == "sct"


def test_missing_dd_skipped():
    """Levels with missing dewpoint depression are skipped."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=None),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=1.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=9880, dewpoint_depression_c=None),
    ]
    # Single level can't form a layer (no adjacent partner)
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].base_ft == 4760
    assert layers[0].top_ft == 4760


def test_empty_levels():
    """Empty input returns empty list."""
    assert detect_cloud_layers([]) == []
