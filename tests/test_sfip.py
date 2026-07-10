"""Tests for SFIP icing index (sounding/sfip.py)."""

import pytest

from weatherbrief.analysis.sounding.sfip import (
    assess_sfip_zones,
    compute_sfip_level,
    glaciation_factor,
    membership_clw,
    membership_clw_proxy,
    membership_rh,
    membership_temperature,
    membership_vv,
    sfip_to_risk,
)
from weatherbrief.models import DerivedLevel, EnhancedCloudLayer, IcingRisk, IcingType


# ── membership_temperature ───────────────────────────────────────────


def test_mt_zero_at_0c():
    assert membership_temperature(0.0) == 0.0


def test_mt_zero_above_0c():
    assert membership_temperature(5.0) == 0.0


def test_mt_near_zero_at_minus25():
    # Exponential decay: exp(-0.4 * 11) ≈ 0.012 — negligible
    assert membership_temperature(-25.0) < 0.02


def test_mt_zero_below_minus25():
    assert membership_temperature(-30.0) == 0.0


def test_mt_peak_at_minus5_to_minus14():
    assert membership_temperature(-5.0) == pytest.approx(1.0)
    assert membership_temperature(-10.0) == pytest.approx(1.0)
    assert membership_temperature(-14.0) == pytest.approx(1.0)


def test_mt_ramp_at_minus2():
    # At -2°C: 0.8 * 2/2 = 0.8
    assert membership_temperature(-2.0) == pytest.approx(0.8)


def test_mt_exponential_decay_minus20():
    # exp(-0.4 * 6) ≈ 0.091 — steep drop from 1.0 at -14
    assert membership_temperature(-20.0) == pytest.approx(0.091, abs=0.01)


def test_mt_intermediate_minus3():
    # Between -2 and -5: 0.8 + 0.2 * (3 - 2) / 3 = 0.8 + 0.067 ≈ 0.867
    assert membership_temperature(-3.0) == pytest.approx(0.8 + 0.2 / 3.0, abs=1e-6)


def test_mt_exponential_decay_monotonic():
    # Verify monotonic decrease from -14 to -25
    temps = [-14, -15, -16, -17, -18, -19, -20, -22, -25]
    values = [membership_temperature(t) for t in temps]
    for i in range(len(values) - 1):
        assert values[i] > values[i + 1], f"Not monotonic at {temps[i]}→{temps[i+1]}"


def test_mt_continuous_at_minus14():
    # No discontinuity at the -14°C boundary
    assert membership_temperature(-14.0) == pytest.approx(1.0)
    assert membership_temperature(-14.01) > 0.99


# ── membership_rh ────────────────────────────────────────────────────


def test_mrh_zero_at_50():
    assert membership_rh(50.0) == 0.0


def test_mrh_zero_below_50():
    assert membership_rh(30.0) == 0.0


def test_mrh_one_at_100():
    assert membership_rh(100.0) == pytest.approx(1.0)


def test_mrh_above_100():
    assert membership_rh(105.0) == 1.0


def test_mrh_intermediate_80():
    # 80% is in 60–90 range: 0.1 + 0.6 * (80-60)/30 = 0.1 + 0.4 = 0.5
    assert membership_rh(80.0) == pytest.approx(0.5, abs=1e-6)


def test_mrh_at_90():
    # 0.1 + 0.6 * 30/30 = 0.7
    assert membership_rh(90.0) == pytest.approx(0.7, abs=1e-6)


def test_mrh_at_95():
    # 0.7 + 0.2 * 5/5 = 0.9
    assert membership_rh(95.0) == pytest.approx(0.9, abs=1e-6)


# ── membership_clw ───────────────────────────────────────────────────


def test_mclw_zero_at_zero():
    assert membership_clw(0.0) == 0.0


def test_mclw_zero_negative():
    assert membership_clw(-0.01) == 0.0


def test_mclw_one_above_0_2():
    assert membership_clw(0.3) == 1.0


def test_mclw_at_0_01():
    assert membership_clw(0.01) == pytest.approx(0.3, abs=1e-6)


def test_mclw_at_0_05():
    # 0.3 + 0.4 * (0.05 - 0.01) / 0.04 = 0.3 + 0.4 = 0.7
    assert membership_clw(0.05) == pytest.approx(0.7, abs=1e-6)


def test_mclw_at_0_1():
    # 0.7 + 0.2 * (0.1 - 0.05) / 0.05 = 0.7 + 0.2 = 0.9
    assert membership_clw(0.1) == pytest.approx(0.9, abs=1e-6)


def test_mclw_intermediate():
    # 0.03 in [0.01, 0.05]: 0.3 + 0.4 * (0.03 - 0.01)/0.04 = 0.3 + 0.2 = 0.5
    assert membership_clw(0.03) == pytest.approx(0.5, abs=1e-6)


# ── membership_clw_proxy ─────────────────────────────────────────────


def test_mclw_proxy_dry():
    """DD > 5 → sounding_score = 0, low cloud → nwp_score = 0."""
    assert membership_clw_proxy(6.0, 60.0, 10.0) == pytest.approx(0.0)


def test_mclw_proxy_in_cloud():
    """DD < 1 and high RH → high sounding score."""
    val = membership_clw_proxy(0.5, 98.0, 10.0)
    assert val > 0.7


def test_mclw_proxy_nwp_dominant():
    """High NWP cloud cover can dominate when DD is moderate."""
    val = membership_clw_proxy(4.0, 70.0, 90.0)
    # NWP score = 1.0 * 0.8 = 0.8, sounding < 0.2
    assert val >= 0.8


def test_mclw_proxy_moderate_dd():
    """DD between 1 and 3 → moderate sounding score."""
    val = membership_clw_proxy(2.0, 80.0, 30.0)
    # sounding_score = 0.2 + 0.5 * (3-2)/2 = 0.45
    assert val == pytest.approx(0.45, abs=0.05)


# ── membership_vv ────────────────────────────────────────────────────


def test_mvv_none():
    assert membership_vv(None) == 0.0


def test_mvv_neutral():
    assert membership_vv(0.0) == 0.0


def test_mvv_strong_ascent():
    assert membership_vv(-6.0) == 0.5


def test_mvv_moderate_ascent():
    val = membership_vv(-1.0)
    assert 0.1 < val < 0.3


def test_mvv_strong_subsidence():
    assert membership_vv(3.0) == -0.3


def test_mvv_moderate_subsidence():
    val = membership_vv(1.0)
    assert -0.15 < val < 0.0


# ── glaciation_factor ────────────────────────────────────────────────


def test_glaciation_no_water():
    assert glaciation_factor(0.0, 0.0) == 0.0


def test_glaciation_all_liquid():
    assert glaciation_factor(0.1, 0.0) == pytest.approx(1.0)


def test_glaciation_all_ice():
    assert glaciation_factor(0.0, 0.1) == pytest.approx(0.0)


def test_glaciation_equal():
    assert glaciation_factor(0.1, 0.1) == pytest.approx(0.5)


def test_glaciation_mostly_liquid():
    # 0.15 / (0.15 + 0.05) = 0.75
    assert glaciation_factor(0.15, 0.05) == pytest.approx(0.75)


# ── sfip_to_risk ─────────────────────────────────────────────────────


def test_risk_none():
    assert sfip_to_risk(0.0) == IcingRisk.NONE
    assert sfip_to_risk(14.9) == IcingRisk.NONE


def test_risk_light():
    assert sfip_to_risk(15.0) == IcingRisk.LIGHT
    assert sfip_to_risk(29.9) == IcingRisk.LIGHT


def test_risk_moderate():
    assert sfip_to_risk(30.0) == IcingRisk.MODERATE
    assert sfip_to_risk(54.9) == IcingRisk.MODERATE


def test_risk_severe():
    assert sfip_to_risk(55.0) == IcingRisk.SEVERE
    assert sfip_to_risk(100.0) == IcingRisk.SEVERE


# ── compute_sfip_level ───────────────────────────────────────────────


def test_level_warm_gated():
    """Above 0°C → zero SFIP."""
    raw, s100, sev, var = compute_sfip_level(
        temperature_c=5.0, rh_pct=100.0, dewpoint_depression_c=0.0,
        clw_g_kg=0.5, icmr_g_kg=None, omega_pa_s=None, cloud_cover_at_band=100.0,
    )
    assert s100 == 0.0
    assert sev == "NONE"


def test_level_too_cold_gated():
    """Below -25°C → zero SFIP."""
    raw, s100, sev, var = compute_sfip_level(
        temperature_c=-30.0, rh_pct=100.0, dewpoint_depression_c=0.0,
        clw_g_kg=0.5, icmr_g_kg=None, omega_pa_s=None, cloud_cover_at_band=100.0,
    )
    assert s100 == 0.0


def test_level_full_variant():
    """With CLW data → full variant."""
    raw, s100, sev, var = compute_sfip_level(
        temperature_c=-8.0, rh_pct=96.0, dewpoint_depression_c=1.0,
        clw_g_kg=0.15, icmr_g_kg=0.02, omega_pa_s=-1.5, cloud_cover_at_band=80.0,
    )
    assert var == "full"
    assert s100 > 50  # should be significant
    assert sev in ("MODERATE", "SEVERE")


def test_level_proxy_variant():
    """Without CLW data → proxy variant."""
    raw, s100, sev, var = compute_sfip_level(
        temperature_c=-8.0, rh_pct=96.0, dewpoint_depression_c=1.0,
        clw_g_kg=None, icmr_g_kg=None, omega_pa_s=-1.5, cloud_cover_at_band=80.0,
    )
    assert var == "proxy"
    assert s100 > 0


def test_level_glaciation_reduces_sfip():
    """High ice fraction should reduce SFIP compared to pure liquid."""
    _, s100_liquid, _, _ = compute_sfip_level(
        temperature_c=-10.0, rh_pct=98.0, dewpoint_depression_c=0.5,
        clw_g_kg=0.2, icmr_g_kg=0.0, omega_pa_s=None, cloud_cover_at_band=0.0,
    )
    _, s100_glaciated, _, _ = compute_sfip_level(
        temperature_c=-10.0, rh_pct=98.0, dewpoint_depression_c=0.5,
        clw_g_kg=0.05, icmr_g_kg=0.15, omega_pa_s=None, cloud_cover_at_band=0.0,
    )
    assert s100_liquid > s100_glaciated


def test_full_no_vv_renormalizes_remaining_memberships():
    _, score_missing, _, variant_missing = compute_sfip_level(
        temperature_c=-7.0,
        rh_pct=100.0,
        dewpoint_depression_c=0.0,
        clw_g_kg=0.2,
        icmr_g_kg=None,
        omega_pa_s=None,
        cloud_cover_at_band=100.0,
    )
    _, score_quiescent, _, variant_quiescent = compute_sfip_level(
        temperature_c=-7.0,
        rh_pct=100.0,
        dewpoint_depression_c=0.0,
        clw_g_kg=0.2,
        icmr_g_kg=None,
        omega_pa_s=0.0,
        cloud_cover_at_band=100.0,
    )
    assert variant_missing == "full_no_vv"
    assert variant_quiescent == "full"
    assert score_missing == 100.0
    assert score_quiescent == 85.0


def test_proxy_no_vv_renormalizes_remaining_memberships():
    _, score_missing, _, variant_missing = compute_sfip_level(
        temperature_c=-7.0,
        rh_pct=100.0,
        dewpoint_depression_c=0.0,
        clw_g_kg=None,
        icmr_g_kg=None,
        omega_pa_s=None,
        cloud_cover_at_band=100.0,
    )
    _, score_quiescent, _, variant_quiescent = compute_sfip_level(
        temperature_c=-7.0,
        rh_pct=100.0,
        dewpoint_depression_c=0.0,
        clw_g_kg=None,
        icmr_g_kg=None,
        omega_pa_s=0.0,
        cloud_cover_at_band=100.0,
    )
    assert variant_missing == "proxy_no_vv"
    assert variant_quiescent == "proxy"
    assert score_missing == 100.0
    assert score_quiescent == 90.0


# ── assess_sfip_zones integration tests ──────────────────────────────


def _level(pressure, alt, temp, dp, rh, dd, clw=None, icmr=None, omega=None):
    """Helper to create a DerivedLevel for testing."""
    return DerivedLevel(
        pressure_hpa=pressure,
        altitude_ft=alt,
        temperature_c=temp,
        dewpoint_c=dp,
        relative_humidity_pct=rh,
        dewpoint_depression_c=dd,
        cloud_liquid_water_g_kg=clw,
        ice_mixing_ratio_g_kg=icmr,
        omega_pa_s=omega,
    )


def _nwp_cloud(base_ft, top_ft):
    """Helper for NWP cloud layer used in full-variant tests."""
    return EnhancedCloudLayer(base_ft=base_ft, top_ft=top_ft, source="grib")


def test_zones_with_clw():
    """Full variant with CLW data produces zones when in NWP cloud."""
    levels = [
        _level(850, 5000, -3.0, -4.0, 92.0, 1.0, clw=0.08),
        _level(800, 6500, -6.0, -7.0, 95.0, 1.0, clw=0.15),
        _level(750, 8000, -9.0, -10.0, 96.0, 1.0, clw=0.20),
        _level(700, 10000, -12.0, -13.0, 93.0, 1.0, clw=0.10),
    ]
    zones = assess_sfip_zones(levels, nwp_cloud_layers=[_nwp_cloud(4000, 11000)])
    assert len(zones) >= 1
    assert zones[0].variant.startswith("full")
    assert zones[0].risk != IcingRisk.NONE


def test_zones_without_clw():
    """Proxy variant without CLW data produces zones when in cloud layer."""
    levels = [
        _level(800, 6500, -6.0, -7.0, 96.0, 1.0),
        _level(750, 8000, -9.0, -10.0, 97.0, 1.0),
        _level(700, 10000, -12.0, -13.0, 95.0, 1.0),
    ]
    clouds = [EnhancedCloudLayer(base_ft=6000, top_ft=10500)]
    zones = assess_sfip_zones(
        levels,
        dd_cloud_layers=clouds,
        nwp_cloud_mid_pct=80.0,
    )
    assert len(zones) >= 1
    assert zones[0].variant.startswith("proxy")


def test_zones_temperature_gating_warm():
    """No zones above 0°C."""
    levels = [
        _level(900, 3000, 5.0, 4.0, 95.0, 1.0, clw=0.2),
        _level(850, 5000, 2.0, 1.0, 94.0, 1.0, clw=0.15),
    ]
    zones = assess_sfip_zones(levels)
    assert len(zones) == 0


def test_zones_temperature_gating_cold():
    """No zones below -25°C."""
    levels = [
        _level(300, 30000, -40.0, -43.0, 80.0, 3.0, clw=0.1),
        _level(250, 34000, -50.0, -55.0, 70.0, 5.0, clw=0.05),
    ]
    zones = assess_sfip_zones(levels)
    assert len(zones) == 0


def test_zones_grouping():
    """Adjacent levels (gap <= 100 hPa) are grouped into one zone."""
    levels = [
        _level(800, 6500, -6.0, -7.0, 96.0, 1.0, clw=0.15),
        _level(750, 8000, -9.0, -10.0, 97.0, 1.0, clw=0.20),
    ]
    zones = assess_sfip_zones(levels, nwp_cloud_layers=[_nwp_cloud(6000, 9000)])
    assert len(zones) == 1
    assert zones[0].base_ft == 6500
    assert zones[0].top_ft == 8000


def test_zones_split_by_gap():
    """Non-adjacent levels (gap > 100 hPa) form separate zones."""
    levels = [
        _level(850, 5000, -3.0, -4.0, 96.0, 1.0, clw=0.15),
        _level(600, 14000, -15.0, -16.0, 95.0, 1.0, clw=0.15),
    ]
    zones = assess_sfip_zones(levels, nwp_cloud_layers=[_nwp_cloud(4000, 15000)])
    assert len(zones) == 2


def test_per_level_fields_stored():
    """SFIP fields are stored on DerivedLevel after assessment."""
    levels = [
        _level(700, 10000, -8.0, -9.0, 96.0, 1.0, clw=0.15),
    ]
    assess_sfip_zones(levels, nwp_cloud_layers=[_nwp_cloud(9000, 11000)])
    assert levels[0].sfip_raw is not None
    assert levels[0].sfip_100 is not None
    assert levels[0].sfip_100 > 0
    assert levels[0].sfip_variant.startswith("full")
    assert levels[0].sfip_severity is not None


def test_empty_levels():
    assert assess_sfip_zones([]) == []


def test_zone_mean_sfip():
    """Zone mean_sfip_100 is computed."""
    levels = [
        _level(800, 6500, -6.0, -7.0, 96.0, 1.0, clw=0.15),
        _level(750, 8000, -9.0, -10.0, 97.0, 1.0, clw=0.20),
    ]
    zones = assess_sfip_zones(levels, nwp_cloud_layers=[_nwp_cloud(6000, 9000)])
    assert len(zones) == 1
    assert zones[0].mean_sfip_100 is not None
    assert zones[0].mean_sfip_100 > 0


def test_zone_icing_type():
    """Zone icing type reflects dominant temperature band."""
    levels = [
        _level(700, 10000, -12.0, -13.0, 96.0, 1.0, clw=0.15),
        _level(650, 12000, -15.0, -16.0, 95.0, 1.0, clw=0.10),
    ]
    zones = assess_sfip_zones(levels, nwp_cloud_layers=[_nwp_cloud(9000, 13000)])
    assert len(zones) == 1
    assert zones[0].icing_type == IcingType.RIME


def test_comparison_both_indices():
    """Same profile produces both Ogimet-DD and SFIP zones (may differ)."""
    from weatherbrief.analysis.sounding.icing import assess_icing_zones_ogimet_dd

    levels = [
        DerivedLevel(
            pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
            dewpoint_c=-8.0, dewpoint_depression_c=1.0, relative_humidity_pct=96.0,
            cloud_liquid_water_g_kg=0.15,
        ),
        DerivedLevel(
            pressure_hpa=650, altitude_ft=12000, temperature_c=-10.0,
            dewpoint_c=-11.0, dewpoint_depression_c=1.0, relative_humidity_pct=95.0,
            cloud_liquid_water_g_kg=0.10,
        ),
    ]
    clouds = [EnhancedCloudLayer(base_ft=9000, top_ft=13000)]

    ogimet_zones = assess_icing_zones_ogimet_dd(levels, clouds)
    sfip_zones = assess_sfip_zones(levels, dd_cloud_layers=clouds, nwp_cloud_layers=clouds)

    assert len(ogimet_zones) >= 1
    assert len(sfip_zones) >= 1


# ── Cloud gating tests ──────────────────────────────────────────────


def test_full_variant_clw_zero_gated():
    """Full variant: CLW=0 produces no SFIP (cloud gated out)."""
    levels = [
        _level(700, 10000, -8.0, -9.0, 96.0, 1.0, clw=0.0),
        _level(650, 12000, -12.0, -13.0, 95.0, 1.0, clw=0.0),
    ]
    zones = assess_sfip_zones(levels)
    assert len(zones) == 0


def test_full_variant_clw_positive_in_nwp_cloud():
    """Full variant: CLW > 0 within NWP cloud layer produces zones."""
    levels = [
        _level(700, 10000, -8.0, -9.0, 96.0, 1.0, clw=0.15),
        _level(650, 12000, -12.0, -13.0, 95.0, 1.0, clw=0.10),
    ]
    zones = assess_sfip_zones(levels, nwp_cloud_layers=[_nwp_cloud(9000, 13000)])
    assert len(zones) >= 1
    assert all(z.variant.startswith("full") for z in zones)


def test_full_variant_clw_positive_outside_nwp_cloud():
    """Full variant: CLW > 0 outside NWP cloud layer → no zones."""
    levels = [
        _level(700, 10000, -8.0, -9.0, 96.0, 1.0, clw=0.15),
        _level(650, 12000, -12.0, -13.0, 95.0, 1.0, clw=0.10),
    ]
    zones = assess_sfip_zones(levels, nwp_cloud_layers=[_nwp_cloud(2000, 4000)])
    assert len(zones) == 0


def test_level_full_variant_clw_zero_returns_none():
    """compute_sfip_level: CLW=0 with full variant returns NONE severity."""
    _, s100, sev, var = compute_sfip_level(
        temperature_c=-10.0, rh_pct=98.0, dewpoint_depression_c=0.5,
        clw_g_kg=0.0, icmr_g_kg=None, omega_pa_s=-3.0, cloud_cover_at_band=100.0,
    )
    assert s100 == 0.0
    assert sev == "NONE"
    assert var == "full"


def test_proxy_no_cloud_layers_no_dd_gated():
    """Proxy variant: levels far from cloud (DD > 3, no cloud layers) → gated out."""
    levels = [
        _level(700, 10000, -8.0, -14.0, 70.0, 6.0),  # DD=6, dry
        _level(650, 12000, -12.0, -18.0, 65.0, 6.0),
    ]
    zones = assess_sfip_zones(levels, nwp_cloud_mid_pct=80.0)
    assert len(zones) == 0


def test_proxy_in_cloud_layer():
    """Proxy variant: levels inside a cloud layer produce zones."""
    clouds = [EnhancedCloudLayer(base_ft=9500, top_ft=12500)]
    levels = [
        _level(700, 10000, -8.0, -9.0, 96.0, 1.0),
        _level(650, 12000, -12.0, -13.0, 95.0, 1.0),
    ]
    zones = assess_sfip_zones(levels, dd_cloud_layers=clouds, nwp_cloud_mid_pct=80.0)
    assert len(zones) >= 1
    assert all(z.variant.startswith("proxy") for z in zones)


def test_proxy_outside_cloud_layer():
    """Proxy variant: levels outside cloud layers are gated out."""
    clouds = [EnhancedCloudLayer(base_ft=3000, top_ft=5000)]
    levels = [
        # DD < 3°C (moist air) but above the cloud layer — should NOT produce SFIP
        _level(700, 10000, -8.0, -9.0, 96.0, 1.0),
        _level(650, 12000, -12.0, -13.0, 95.0, 1.0),
    ]
    zones = assess_sfip_zones(levels, dd_cloud_layers=clouds, nwp_cloud_mid_pct=80.0)
    assert len(zones) == 0


def test_proxy_near_cloud_layer_margin():
    """Proxy variant: within 500ft margin of cloud layer passes gating."""
    clouds = [EnhancedCloudLayer(base_ft=9500, top_ft=12500)]
    levels = [
        # DD=4 (dry) but altitude within cloud layer boundaries
        _level(700, 10000, -8.0, -12.0, 80.0, 4.0),
        _level(650, 12000, -12.0, -16.0, 78.0, 4.0),
    ]
    zones = assess_sfip_zones(levels, dd_cloud_layers=clouds, nwp_cloud_mid_pct=80.0)
    assert len(zones) >= 1
    assert all(z.variant.startswith("proxy") for z in zones)


def test_proxy_far_from_cloud_layer_gated():
    """Proxy variant: altitude far from cloud layer AND DD > 3 → gated out."""
    clouds = [EnhancedCloudLayer(base_ft=15000, top_ft=20000)]
    levels = [
        _level(850, 5000, -3.0, -8.0, 70.0, 5.0),  # Far below cloud, dry
        _level(800, 6500, -6.0, -12.0, 65.0, 6.0),
    ]
    zones = assess_sfip_zones(levels, dd_cloud_layers=clouds, nwp_cloud_mid_pct=80.0)
    assert len(zones) == 0


def test_proxy_cloud_margin_500ft():
    """Proxy variant: level just within 500ft margin of cloud passes gating."""
    # Cloud base at 10000ft, level at 9600ft (within 500ft margin)
    clouds = [EnhancedCloudLayer(base_ft=10000, top_ft=15000)]
    levels = [
        _level(700, 9600, -8.0, -12.0, 78.0, 4.0),  # DD > 3, but within margin
    ]
    zones = assess_sfip_zones(levels, dd_cloud_layers=clouds, nwp_cloud_mid_pct=80.0)
    # Should produce a zone (within 500ft of cloud base)
    assert len(zones) >= 1


def test_proxy_outside_margin_gated():
    """Proxy variant: level just outside 500ft margin → gated out."""
    # Cloud base at 10000ft, level at 9000ft (1000ft below, outside 500ft margin)
    clouds = [EnhancedCloudLayer(base_ft=10000, top_ft=15000)]
    levels = [
        _level(750, 9000, -8.0, -12.0, 78.0, 4.0),  # DD > 3, outside margin
    ]
    zones = assess_sfip_zones(levels, dd_cloud_layers=clouds, nwp_cloud_mid_pct=80.0)
    assert len(zones) == 0


def test_mixed_full_proxy_clw_zero_clear_air():
    """Full variant with CLW=0 in clear air: no false alarms even with high T/RH."""
    levels = [
        _level(700, 10000, -10.0, -10.5, 97.0, 0.5, clw=0.0),
        _level(650, 12000, -14.0, -14.5, 98.0, 0.5, clw=0.0),
    ]
    zones = assess_sfip_zones(levels)
    # CLW=0 means full variant but gated → no zones despite perfect T/RH
    assert len(zones) == 0
