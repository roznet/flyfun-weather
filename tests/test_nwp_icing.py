"""Tests for NWP-native icing zone detection (sounding/nwp_icing.py).

Covers direct CLW/CIW condensate-based icing detection — independent of
Ogimet-DD dewpoint heuristics and of cloud layer gating.
"""

from weatherbrief.analysis.sounding.nwp_icing import assess_nwp_icing_zones
from weatherbrief.models import DerivedLevel, IcingRisk, IcingType


def _lv(p, alt_ft, t_c, clw=None, ciw=None, rh=None):
    return DerivedLevel(
        pressure_hpa=p,
        altitude_ft=alt_ft,
        temperature_c=t_c,
        cloud_liquid_water_g_kg=clw,
        ice_mixing_ratio_g_kg=ciw,
        relative_humidity_pct=rh,
    )


def test_empty_levels():
    assert assess_nwp_icing_zones([]) == []


def test_no_clw_no_zone():
    """Column with no CLW anywhere returns empty."""
    levels = [
        _lv(1000, 200, 10.0),
        _lv(700, 10000, -5.0),
        _lv(500, 18000, -20.0),
    ]
    assert assess_nwp_icing_zones(levels) == []


def test_clw_outside_temp_band_ignored():
    """CLW at T>0 (warm) isn't an icing concern."""
    levels = [
        _lv(1000, 200, 5.0, clw=0.1),
        _lv(950, 1500, 3.0, clw=0.2),
    ]
    assert assess_nwp_icing_zones(levels) == []


def test_clw_in_supercooled_band_makes_zone():
    """CLW with T in [-20, 0] yields an icing zone."""
    levels = [
        _lv(1000, 200, 5.0),
        _lv(700, 10000, -3.0, clw=0.08, rh=95),
        _lv(600, 14000, -10.0, clw=0.1, rh=90),
    ]
    zones = assess_nwp_icing_zones(levels)
    assert len(zones) == 1
    z = zones[0]
    # Spans both CLW levels (10000-14000 ft)
    assert z.base_ft == 10000
    assert z.top_ft == 14000
    # CLW 0.1 is at MODERATE threshold (>=0.05)
    assert z.risk == IcingRisk.MODERATE


def test_severity_thresholds():
    """Risk classification maps CLW amount to severity."""
    # Light: 0.02 g/kg
    zones = assess_nwp_icing_zones([_lv(700, 10000, -5.0, clw=0.02)])
    assert zones[0].risk == IcingRisk.LIGHT

    # Moderate: 0.08 g/kg
    zones = assess_nwp_icing_zones([_lv(700, 10000, -5.0, clw=0.08)])
    assert zones[0].risk == IcingRisk.MODERATE

    # Severe: 0.3 g/kg
    zones = assess_nwp_icing_zones([_lv(700, 10000, -5.0, clw=0.3)])
    assert zones[0].risk == IcingRisk.SEVERE


def test_icing_type_clear_in_warm_supercooled():
    """T in [-4, 0] with CLW → CLEAR type (large drop / glaze)."""
    zones = assess_nwp_icing_zones([_lv(700, 10000, -2.0, clw=0.05)])
    assert zones[0].icing_type == IcingType.CLEAR


def test_icing_type_mixed_when_ciw_present():
    """CLW + substantial CIW at same level → MIXED type regardless of T."""
    zones = assess_nwp_icing_zones([_lv(700, 10000, -2.0, clw=0.05, ciw=0.01)])
    assert zones[0].icing_type == IcingType.MIXED


def test_pure_ice_not_icing_zone():
    """CIW only (no CLW) doesn't produce a zone — glaciated cloud."""
    levels = [_lv(500, 20000, -40.0, ciw=0.02)]
    assert assess_nwp_icing_zones(levels) == []


def test_multiple_zones_separated_by_pressure_gap():
    """Non-adjacent CLW layers produce separate zones."""
    levels = [
        _lv(900, 3000, -2.0, clw=0.05),
        _lv(850, 5000, -4.0, clw=0.04),
        # Large gap (850 → 500 = 350 hPa > 100 hPa threshold)
        _lv(500, 18000, -15.0, clw=0.06),
    ]
    zones = assess_nwp_icing_zones(levels)
    assert len(zones) == 2


def test_thin_zone_expanded_for_visibility():
    """Single-level icing zone expanded to minimum 1000 ft thickness."""
    zones = assess_nwp_icing_zones([_lv(700, 10000, -5.0, clw=0.08)])
    assert len(zones) == 1
    z = zones[0]
    assert z.top_ft - z.base_ft >= 1000
