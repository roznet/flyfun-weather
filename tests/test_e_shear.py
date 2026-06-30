"""Tests for the E-Shear turbulence index — unit calibration and grouping.

The CloudPath formula E = (5·HWS + VWS² + 42)/4 is calibrated with VWS in
kt/1000ft and HWS in kt/100nm. These tests pin the unit conversions so a
scale-factor regression cannot silently shift the severity thresholds.
"""

from __future__ import annotations

import pytest

from weatherbrief.analysis.sounding.e_shear import (
    _E_LIGHT,
    _E_MODERATE,
    _E_SEVERE,
    _HWS_SCALE,
    _MS_TO_KT,
    _VWS_SCALE,
    _classify_e_risk,
    _e_parameter,
    compute_e_shear_per_sounding,
    compute_hws_between_points,
)
from weatherbrief.models import CATRiskLevel, DerivedLevel

# Independent unit-conversion oracle (does NOT use the module's own constants).
# 1 knot = 1 nm/hr = 1852 m / 3600 s, so 1 m/s = 3600/1852 kt.
_KT_PER_MS = 3600.0 / 1852.0          # ≈ 1.943844
_M_PER_1000FT = 1000.0 * 0.3048       # 304.8 m (foot is exactly 0.3048 m)
_M_PER_100NM = 100.0 * 1852.0         # 185200 m


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


# --- Scale factors are exact unit conversions (independent oracle) ---


def test_ms_to_kt_constant_is_exact_knot_definition():
    """_MS_TO_KT must equal 3600/1852 (knot = 1852 m per 3600 s)."""
    assert _MS_TO_KT == pytest.approx(_KT_PER_MS, rel=1e-5)


def test_vws_scale_is_exact_kt_per_1000ft_conversion():
    """VWS scale converts (m/s)/m → kt/1000ft: (3600/1852)·304.8 ≈ 592.48."""
    assert _VWS_SCALE == pytest.approx(_KT_PER_MS * _M_PER_1000FT, rel=1e-6)


def test_hws_scale_is_exact_kt_per_100nm_conversion():
    """HWS scale converts (m/s)/m → kt/100nm; (3600/1852)·185200 = 360000 exactly."""
    assert _HWS_SCALE == pytest.approx(_KT_PER_MS * _M_PER_100NM, rel=1e-6)
    # The nm/s cancellation makes this an exact round number — a strong check.
    assert _HWS_SCALE == pytest.approx(360_000.0, rel=1e-6)


# --- E parameter is the exact CloudPath formula (exact float, not just tier) ---


def test_e_parameter_exact_values():
    """E = (5·HWS + VWS² + 42)/4, evaluated exactly."""
    assert _e_parameter(0.0, 20.0) == pytest.approx(110.5)   # (400+42)/4
    assert _e_parameter(0.0, 10.0) == pytest.approx(35.5)    # (100+42)/4
    assert _e_parameter(0.0, 25.0) == pytest.approx(166.75)  # (625+42)/4
    assert _e_parameter(30.0, 0.0) == pytest.approx(48.0)    # (150+42)/4


def test_e_risk_thresholds_pinned():
    """Severity boundaries sit exactly on the documented E cutoffs (40/80/160)."""
    assert _classify_e_risk(39.99) == CATRiskLevel.NONE
    assert _classify_e_risk(40.0) == CATRiskLevel.LIGHT
    assert _classify_e_risk(_E_MODERATE) == CATRiskLevel.MODERATE
    assert _classify_e_risk(_E_SEVERE) == CATRiskLevel.SEVERE
    assert _E_LIGHT == 40.0
    assert _E_MODERATE == 80.0
    assert _E_SEVERE == 160.0
