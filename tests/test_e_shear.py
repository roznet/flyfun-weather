"""Tests for the E-Shear turbulence index — unit calibration and grouping.

The CloudPath formula E = (5·HWS + VWS² + 42)/4 is calibrated with VWS in
kt/1000ft and HWS in kt/100nm. These tests pin the unit conversions so a
scale-factor regression cannot silently shift the severity thresholds.
"""

from __future__ import annotations

import pytest

from weatherbrief.analysis.sounding.e_shear import (
    compute_e_shear_per_sounding,
    compute_hws_between_points,
)
from weatherbrief.models import CATRiskLevel, DerivedLevel


def _lv(pressure_hpa: int, altitude_ft: float, speed_kt: float,
        direction_deg: float = 360.0) -> DerivedLevel:
    return DerivedLevel(
        pressure_hpa=pressure_hpa, altitude_ft=altitude_ft,
        wind_speed_kt=speed_kt, wind_direction_deg=direction_deg,
    )


def test_vws_units_20kt_per_1000ft_is_moderate():
    """Δ20 kt over 1000 ft → VWS = 20 kt/1000ft → E = (400+42)/4 = 110.5 → MODERATE."""
    levels = [_lv(900, 3000, 0.0), _lv(875, 4000, 20.0)]
    layers = compute_e_shear_per_sounding(levels)
    assert len(layers) == 1
    assert layers[0].risk == CATRiskLevel.MODERATE


def test_vws_units_10kt_per_1000ft_is_none():
    """Δ10 kt over 1000 ft → E = (100+42)/4 = 35.5 < 40 → no layer."""
    levels = [_lv(900, 3000, 0.0), _lv(875, 4000, 10.0)]
    assert compute_e_shear_per_sounding(levels) == []


def test_vws_units_25kt_per_1000ft_is_severe():
    """Δ25 kt over 1000 ft → E = (625+42)/4 = 166.75 ≥ 160 → SEVERE."""
    levels = [_lv(900, 3000, 0.0), _lv(875, 4000, 25.0)]
    layers = compute_e_shear_per_sounding(levels)
    assert len(layers) == 1
    assert layers[0].risk == CATRiskLevel.SEVERE


def test_hws_units_kt_per_100nm():
    """Δ36 kt over 100 nm at the same level → HWS ≈ 36 kt/100nm."""
    a = [_lv(700, 10000, 0.0)]
    b = [_lv(700, 10000, 36.0)]
    hws = compute_hws_between_points(a, b, distance_nm=100.0)
    assert hws[700] == pytest.approx(36.0, rel=0.01)


def test_hws_contributes_to_e():
    """HWS-only: E = (5·HWS + 42)/4 → HWS 30 → E = 48 → LIGHT."""
    # Two identical-wind levels (zero VWS), HWS injected externally.
    levels = [_lv(900, 3000, 10.0), _lv(875, 4000, 10.0)]
    layers = compute_e_shear_per_sounding(levels, hws_at_level={875: 30.0})
    assert len(layers) == 1
    assert layers[0].risk == CATRiskLevel.LIGHT
