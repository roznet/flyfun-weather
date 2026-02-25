"""Tests for convective risk assessment (sounding/convective.py)."""

from weatherbrief.analysis.sounding.convective import _effective_cape, assess_convective
from weatherbrief.models import ConvectiveRisk, ThermodynamicIndices


def test_effective_cape_uses_max():
    """Effective CAPE is max(SB, MU)."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=100.0,
        cape_most_unstable_jkg=500.0,
    )
    assert _effective_cape(indices) == 500.0


def test_effective_cape_sb_only():
    """Falls back to SB-CAPE when MU-CAPE is None."""
    indices = ThermodynamicIndices(cape_surface_jkg=200.0)
    assert _effective_cape(indices) == 200.0


def test_effective_cape_mu_only():
    """Uses MU-CAPE when SB-CAPE is None (elevated convection)."""
    indices = ThermodynamicIndices(cape_most_unstable_jkg=800.0)
    assert _effective_cape(indices) == 800.0


def test_effective_cape_none():
    """Returns None when no CAPE is available."""
    assert _effective_cape(ThermodynamicIndices()) is None


def test_eu_thresholds_moderate_at_300():
    """300 J/kg triggers MODERATE with European thresholds."""
    indices = ThermodynamicIndices(cape_surface_jkg=350.0)
    result = assess_convective(indices)
    assert result.risk_level == ConvectiveRisk.MODERATE


def test_eu_thresholds_high_at_1000():
    """1000 J/kg triggers HIGH with European thresholds."""
    indices = ThermodynamicIndices(cape_surface_jkg=1200.0)
    result = assess_convective(indices)
    assert result.risk_level == ConvectiveRisk.HIGH


def test_eu_thresholds_extreme_at_2000():
    """2000 J/kg triggers EXTREME with European thresholds."""
    indices = ThermodynamicIndices(cape_surface_jkg=2500.0)
    result = assess_convective(indices)
    assert result.risk_level == ConvectiveRisk.EXTREME


def test_elevated_convection_mu_cape():
    """MU-CAPE > SB-CAPE drives risk level (elevated convection scenario)."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=30.0,       # nearly zero SB-CAPE (marine BL)
        cape_most_unstable_jkg=800.0,  # warm advection aloft
    )
    result = assess_convective(indices)
    assert result.risk_level == ConvectiveRisk.MODERATE


def test_cin_suppression():
    """Strong CIN cap reduces risk by one level."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=1500.0,
        cin_surface_jkg=-250.0,
    )
    result = assess_convective(indices)
    # 1500 J/kg = HIGH with EU thresholds, CIN suppression → MODERATE
    assert result.risk_level == ConvectiveRisk.MODERATE


def test_no_cape_no_risk():
    """No CAPE → NONE risk."""
    indices = ThermodynamicIndices()
    result = assess_convective(indices)
    assert result.risk_level == ConvectiveRisk.NONE
