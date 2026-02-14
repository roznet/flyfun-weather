"""Tests for enhanced icing assessment using Ogimet index (sounding/icing.py)."""

from weatherbrief.analysis.sounding.icing import (
    _cape_to_cloud_split,
    _compute_layered_index,
    _index_to_risk,
    assess_icing_zones,
)
from weatherbrief.models import DerivedLevel, EnhancedCloudLayer, IcingRisk, IcingType


def _cloud(base_ft, top_ft):
    """Helper to create a cloud layer."""
    return EnhancedCloudLayer(base_ft=base_ft, top_ft=top_ft)


def test_no_icing_warm():
    """No icing when temperature above 0C."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=3.0,
                     dewpoint_c=1.0, dewpoint_depression_c=2.0),
    ]
    zones = assess_icing_zones(levels, [_cloud(4000, 6000)])
    assert len(zones) == 0


def test_no_icing_too_cold():
    """No icing when temperature below -14C (layered) or -20C (convective)."""
    levels = [
        DerivedLevel(pressure_hpa=400, altitude_ft=24000, temperature_c=-25.0,
                     dewpoint_c=-28.0, dewpoint_depression_c=3.0, wet_bulb_c=-25.0),
    ]
    # Even near cloud, temperature is outside icing index range
    zones = assess_icing_zones(levels, [_cloud(23000, 25000)])
    assert len(zones) == 0


def test_no_icing_dry():
    """No icing when not near cloud (high dewpoint depression)."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-5.0,
                     dewpoint_c=-15.0, dewpoint_depression_c=10.0),
    ]
    # No cloud layers nearby
    zones = assess_icing_zones(levels, [])
    assert len(zones) == 0


def test_layered_index_parabola_peak():
    """Layered index peaks at -7C with value 100."""
    # At -7C: 100 * 7 * 7 / 49 = 100
    assert _compute_layered_index(-7.0) == 100.0


def test_layered_index_zero_boundaries():
    """Layered index is zero outside -14C to 0C range."""
    assert _compute_layered_index(0.0) == 0.0
    assert _compute_layered_index(1.0) == 0.0
    assert _compute_layered_index(-15.0) == 0.0


def test_layered_index_symmetric():
    """Layered index is symmetric around -7C."""
    assert abs(_compute_layered_index(-3.0) - _compute_layered_index(-11.0)) < 0.01


def test_icing_at_peak_temperature():
    """Icing at -7C (peak of Ogimet parabola) should detect moderate+ icing."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones(levels, [_cloud(9000, 11000)])
    assert len(zones) == 1
    assert zones[0].icing_type == IcingType.MIXED
    # At -7C, layered index = 100, combined = 50 → MODERATE
    assert zones[0].risk in (IcingRisk.MODERATE, IcingRisk.SEVERE)


def test_severity_thresholds():
    """Index-to-risk mapping follows 30/80 thresholds."""
    assert _index_to_risk(0.0) == IcingRisk.NONE
    assert _index_to_risk(15.0) == IcingRisk.LIGHT
    assert _index_to_risk(30.0) == IcingRisk.MODERATE
    assert _index_to_risk(50.0) == IcingRisk.MODERATE
    assert _index_to_risk(80.0) == IcingRisk.SEVERE
    assert _index_to_risk(100.0) == IcingRisk.SEVERE


def test_icing_type_from_temperature():
    """Icing type classification based on temperature bands."""
    # Clear: -3 to 0
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=-1.5,
                     dewpoint_c=-2.0, dewpoint_depression_c=0.5),
    ]
    zones = assess_icing_zones(levels, [_cloud(4000, 6000)])
    assert len(zones) == 1
    assert zones[0].icing_type == IcingType.CLEAR

    # Mixed: -10 to -3
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-6.0,
                     dewpoint_c=-7.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones(levels, [_cloud(9000, 11000)])
    assert len(zones) == 1
    assert zones[0].icing_type == IcingType.MIXED

    # Rime: < -10
    levels = [
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, temperature_c=-12.0,
                     dewpoint_c=-13.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones(levels, [_cloud(17000, 19000)])
    assert len(zones) == 1
    assert zones[0].icing_type == IcingType.RIME


def test_severity_enhanced_by_high_rh():
    """RH > 95% upgrades severity by one level."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-7.5, dewpoint_depression_c=0.5,
                     relative_humidity_pct=97.0),
    ]
    zones = assess_icing_zones(levels, [_cloud(9000, 11000)])
    assert len(zones) == 1
    # Base risk is MODERATE from Ogimet, RH > 95% upgrades to SEVERE
    assert zones[0].risk == IcingRisk.SEVERE


def test_near_cloud_margin():
    """Level within 500ft of cloud boundary is assessed."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=6400, temperature_c=-2.0,
                     dewpoint_c=-3.0, dewpoint_depression_c=5.0),
    ]
    # Cloud top at 6000, level at 6400 = 400ft above = within 500ft margin
    zones = assess_icing_zones(levels, [_cloud(4000, 6000)])
    assert len(zones) == 1


def test_adjacent_levels_grouped():
    """Adjacent icing levels (gap <= 100hPa) are grouped into a single zone."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
        DerivedLevel(pressure_hpa=800, altitude_ft=6500, temperature_c=-8.0,
                     dewpoint_c=-9.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones(levels, [_cloud(4000, 7000)])
    assert len(zones) == 1
    assert zones[0].base_ft == 5000
    assert zones[0].top_ft == 6500


def test_empty_levels():
    """Empty input returns empty list."""
    assert assess_icing_zones([], []) == []


def test_cape_cloud_split():
    """CAPE-based layered/convective split mapping."""
    assert _cape_to_cloud_split(None) == (1.0, 0.0)
    assert _cape_to_cloud_split(50) == (1.0, 0.0)
    assert _cape_to_cloud_split(200) == (0.8, 0.2)
    assert _cape_to_cloud_split(800) == (0.5, 0.5)
    assert _cape_to_cloud_split(2000) == (0.2, 0.8)


def test_icing_index_stored_on_level():
    """Icing index is stored on DerivedLevel after assessment."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
    ]
    assess_icing_zones(levels, [_cloud(9000, 11000)])
    assert levels[0].icing_index is not None
    assert levels[0].icing_index > 0


def test_mean_icing_index_on_zone():
    """Mean icing index is computed on the IcingZone."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
        DerivedLevel(pressure_hpa=800, altitude_ft=6500, temperature_c=-8.0,
                     dewpoint_c=-9.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones(levels, [_cloud(4000, 7000)])
    assert len(zones) == 1
    assert zones[0].mean_icing_index is not None
    assert zones[0].mean_icing_index > 0


def test_high_cape_convective_icing():
    """With high CAPE, convective component contributes to icing index."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
    ]
    # With no CAPE (pure layered)
    zones_layered = assess_icing_zones(levels, [_cloud(9000, 11000)], cape_jkg=0)
    idx_layered = levels[0].icing_index

    # Reset icing_index
    levels[0].icing_index = None

    # With high CAPE (mostly convective)
    zones_convective = assess_icing_zones(levels, [_cloud(9000, 11000)], cape_jkg=2000)
    idx_convective = levels[0].icing_index

    # Both should produce non-zero icing
    assert idx_layered > 0
    assert idx_convective is not None
    # The indices may differ due to different layered/convective weighting
    assert len(zones_layered) == 1
    assert len(zones_convective) == 1
