"""Tests for temperature inversion detection (sounding/inversions.py)."""

from weatherbrief.analysis.sounding.inversions import detect_inversions
from weatherbrief.models import DerivedLevel


def test_no_inversions_standard_lapse():
    """No inversions in a standard lapse rate profile (positive lapse = cooling)."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, temperature_c=15.0, lapse_rate_c_per_km=6.5),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=5.0, lapse_rate_c_per_km=6.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-5.0, lapse_rate_c_per_km=5.5),
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, temperature_c=-20.0, lapse_rate_c_per_km=6.0),
    ]
    inversions = detect_inversions(levels)
    assert len(inversions) == 0


def test_single_elevated_inversion():
    """Single elevated inversion detected at mid-level.

    Negative lapse rate at 850hPa means T increases from 5000ft to 10000ft.
    """
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, temperature_c=15.0, lapse_rate_c_per_km=6.5),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=5.0, lapse_rate_c_per_km=-3.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=8.0, lapse_rate_c_per_km=5.5),
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, temperature_c=-15.0, lapse_rate_c_per_km=6.0),
    ]
    inversions = detect_inversions(levels)
    assert len(inversions) == 1
    inv = inversions[0]
    assert inv.base_ft == 5000
    assert inv.top_ft == 10000  # extends to the next level above
    assert inv.strength_c == 3.0  # 8.0 - 5.0
    assert inv.surface_based is False


def test_surface_based_inversion():
    """Surface-based inversion detected starting at lowest level.

    Negative lapse at 1000hPa means T increases from 300ft to 2500ft.
    """
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, temperature_c=2.0, lapse_rate_c_per_km=-5.0),
        DerivedLevel(pressure_hpa=925, altitude_ft=2500, temperature_c=5.0, lapse_rate_c_per_km=6.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=0.0, lapse_rate_c_per_km=6.5),
    ]
    inversions = detect_inversions(levels)
    assert len(inversions) == 1
    assert inversions[0].surface_based is True
    assert inversions[0].base_ft == 300
    assert inversions[0].top_ft == 2500
    assert inversions[0].strength_c == 3.0  # 5.0 - 2.0


def test_multiple_inversions():
    """Two separate inversions are detected."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, temperature_c=15.0, lapse_rate_c_per_km=-2.0),
        DerivedLevel(pressure_hpa=925, altitude_ft=2500, temperature_c=16.0, lapse_rate_c_per_km=6.5),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=5.0, lapse_rate_c_per_km=6.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-3.0, lapse_rate_c_per_km=-1.5),
        DerivedLevel(pressure_hpa=600, altitude_ft=14000, temperature_c=-2.0, lapse_rate_c_per_km=5.5),
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, temperature_c=-20.0, lapse_rate_c_per_km=6.0),
    ]
    inversions = detect_inversions(levels)
    assert len(inversions) == 2
    assert inversions[0].surface_based is True
    assert inversions[0].base_ft == 300
    assert inversions[0].top_ft == 2500
    assert inversions[1].surface_based is False
    assert inversions[1].base_ft == 10000
    assert inversions[1].top_ft == 14000


def test_strength_calculation():
    """Inversion strength is top_temp - base_temp.

    Negative lapse at 850hPa means inversion from 5000ft to 10000ft.
    """
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, temperature_c=10.0, lapse_rate_c_per_km=6.5),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=-2.0, lapse_rate_c_per_km=-4.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=4.0, lapse_rate_c_per_km=5.0),
    ]
    inversions = detect_inversions(levels)
    assert len(inversions) == 1
    assert inversions[0].base_ft == 5000
    assert inversions[0].top_ft == 10000
    assert inversions[0].strength_c == 6.0  # 4.0 - (-2.0)
    assert inversions[0].base_temperature_c == -2.0
    assert inversions[0].top_temperature_c == 4.0


def test_empty_input():
    """Empty input returns empty list."""
    assert detect_inversions([]) == []


def test_no_valid_lapse_rates():
    """Levels without lapse rate data are skipped."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, temperature_c=15.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=5.0),
    ]
    inversions = detect_inversions(levels)
    assert len(inversions) == 0


def test_consecutive_negative_lapse_grouped():
    """Multiple consecutive negative lapse rate levels form one inversion.

    The inversion top extends to the level above the last negative-lapse level.
    """
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, temperature_c=10.0, lapse_rate_c_per_km=6.0),
        DerivedLevel(pressure_hpa=925, altitude_ft=2500, temperature_c=5.0, lapse_rate_c_per_km=-3.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=8.0, lapse_rate_c_per_km=-2.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=10.0, lapse_rate_c_per_km=6.0),
    ]
    inversions = detect_inversions(levels)
    assert len(inversions) == 1
    assert inversions[0].base_ft == 2500
    assert inversions[0].top_ft == 10000  # next level above last negative-lapse level
    assert inversions[0].strength_c == 5.0  # 10.0 - 5.0
