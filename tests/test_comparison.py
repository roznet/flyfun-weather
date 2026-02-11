"""Tests for model comparison and divergence scoring."""

from weatherbrief.analysis.comparison import compare_models
from weatherbrief.models import AgreementLevel


def test_good_temperature_agreement():
    """Models within 2C spread = good agreement."""
    result = compare_models("temperature_c", {"gfs": 10.0, "ecmwf": 11.0})
    assert result.agreement == AgreementLevel.GOOD
    assert result.spread == 1.0
    assert result.mean == 10.5


def test_moderate_temperature_agreement():
    """Models with 2-5C spread = moderate agreement."""
    result = compare_models("temperature_c", {"gfs": 10.0, "ecmwf": 13.5})
    assert result.agreement == AgreementLevel.MODERATE
    assert result.spread == 3.5


def test_poor_temperature_agreement():
    """Models with >5C spread = poor agreement."""
    result = compare_models("temperature_c", {"gfs": 5.0, "ecmwf": 12.0})
    assert result.agreement == AgreementLevel.POOR
    assert result.spread == 7.0


def test_good_wind_speed_agreement():
    """Wind speed within 5kt = good."""
    result = compare_models("wind_speed_kt", {"gfs": 20, "ecmwf": 22, "icon": 21})
    assert result.agreement == AgreementLevel.GOOD
    assert result.spread == 2


def test_poor_wind_direction():
    """Wind direction spread >60 degrees = poor."""
    result = compare_models("wind_direction_deg", {"gfs": 270, "ecmwf": 340})
    assert result.spread == 70  # circular: 340-270 = 70
    assert result.agreement == AgreementLevel.POOR


def test_wind_direction_wraparound():
    """Wind direction handles 360/0 wraparound correctly."""
    # 350 and 010 are only 20 degrees apart, not 340
    result = compare_models("wind_direction_deg", {"gfs": 350, "ecmwf": 10})
    assert result.spread == 20
    assert result.agreement == AgreementLevel.GOOD


def test_wind_direction_circular_mean():
    """Circular mean of 350 and 010 should be ~0, not 180."""
    result = compare_models("wind_direction_deg", {"gfs": 350, "ecmwf": 10})
    assert result.mean == 0 or result.mean == 360 or abs(result.mean) < 1


def test_unknown_variable_uses_defaults():
    """Variables not in threshold table use defaults."""
    result = compare_models("cape_jkg", {"gfs": 100, "ecmwf": 102})
    assert result.agreement == AgreementLevel.GOOD


def test_three_models():
    """Works with three models."""
    result = compare_models("cloud_cover_pct", {"gfs": 30, "ecmwf": 40, "icon": 35})
    assert result.mean == 35.0
    assert result.spread == 10.0
    assert result.agreement == AgreementLevel.GOOD  # <15% spread
