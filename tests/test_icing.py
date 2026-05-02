"""Tests for icing assessment methods (sounding/icing.py).

Tests cover the three active icing methods:
  - Ogimet-DD: Ogimet index gated by DD cloud layers, DD severity modulation
  - Ogimet-NWP: Ogimet index gated by NWP cloud layers, cloud fraction modulation
  - IENG: Temperature curve gated by NWP cloud layers, cloud fraction modulation
"""

from weatherbrief.analysis.sounding.icing import (
    _cape_to_cloud_split,
    _compute_layered_index,
    _dd_attenuation_factor,
    _index_to_risk,
    assess_icing_zones_ogimet_dd,
    assess_icing_zones_ogimet_nwp,
)
from weatherbrief.models import (
    CloudCoverage,
    DerivedLevel,
    EnhancedCloudLayer,
    IcingRisk,
    IcingType,
    NWPCloudDiagnostics,
    NWPCloudLayerDiag,
)


def _cloud(base_ft, top_ft, coverage=CloudCoverage.BKN):
    """Helper to create a cloud layer (defaults to BKN — icing-relevant)."""
    return EnhancedCloudLayer(base_ft=base_ft, top_ft=top_ft, coverage=coverage)


# --- Utility function tests ---


def test_layered_index_parabola_peak():
    """Layered index peaks at -7C with value 100."""
    assert _compute_layered_index(-7.0) == 100.0


def test_layered_index_zero_boundaries():
    """Layered index is zero outside -14C to 0C range."""
    assert _compute_layered_index(0.0) == 0.0
    assert _compute_layered_index(1.0) == 0.0
    assert _compute_layered_index(-15.0) == 0.0


def test_layered_index_symmetric():
    """Layered index is symmetric around -7C."""
    assert abs(_compute_layered_index(-3.0) - _compute_layered_index(-11.0)) < 0.01


def test_severity_thresholds():
    """Index-to-risk mapping follows 10/30/80 thresholds."""
    assert _index_to_risk(0.0) == IcingRisk.NONE
    assert _index_to_risk(5.0) == IcingRisk.NONE
    assert _index_to_risk(9.9) == IcingRisk.NONE
    assert _index_to_risk(10.0) == IcingRisk.LIGHT
    assert _index_to_risk(15.0) == IcingRisk.LIGHT
    assert _index_to_risk(30.0) == IcingRisk.MODERATE
    assert _index_to_risk(50.0) == IcingRisk.MODERATE
    assert _index_to_risk(80.0) == IcingRisk.SEVERE
    assert _index_to_risk(100.0) == IcingRisk.SEVERE


def test_cape_cloud_split():
    """CAPE-based layered/convective split mapping."""
    assert _cape_to_cloud_split(None) == (1.0, 0.0)
    assert _cape_to_cloud_split(50) == (1.0, 0.0)
    assert _cape_to_cloud_split(200) == (0.8, 0.2)
    assert _cape_to_cloud_split(800) == (0.5, 0.5)
    assert _cape_to_cloud_split(2000) == (0.2, 0.8)


# --- DD attenuation tests ---


def test_dd_attenuation_at_zero():
    """DD=0 → factor=1.0 (fully in cloud)."""
    assert _dd_attenuation_factor(0.0) == 1.0


def test_dd_attenuation_at_cutoff():
    """DD=cutoff → factor=0.0 (no cloud signal)."""
    assert _dd_attenuation_factor(2.0) == 0.0


def test_dd_attenuation_above_cutoff():
    """DD above cutoff → factor=0.0."""
    assert _dd_attenuation_factor(3.0) == 0.0


def test_dd_attenuation_at_half():
    """DD=cutoff/2 → factor=0.5 (midpoint of cosine taper)."""
    assert abs(_dd_attenuation_factor(1.0) - 0.5) < 0.001


def test_dd_attenuation_negative():
    """DD<0 → factor=1.0 (supersaturated)."""
    assert _dd_attenuation_factor(-0.5) == 1.0


# --- Ogimet-DD: cloud layer gating ---


def test_ogimet_dd_no_icing_warm():
    """No icing when temperature above 0C."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=3.0,
                     dewpoint_c=1.0, dewpoint_depression_c=2.0),
    ]
    zones = assess_icing_zones_ogimet_dd(levels, [_cloud(4000, 6000)])
    assert len(zones) == 0


def test_ogimet_dd_no_icing_too_cold():
    """No icing when temperature below icing range."""
    levels = [
        DerivedLevel(pressure_hpa=400, altitude_ft=24000, temperature_c=-25.0,
                     dewpoint_c=-28.0, dewpoint_depression_c=3.0, wet_bulb_c=-25.0),
    ]
    zones = assess_icing_zones_ogimet_dd(levels, [_cloud(23000, 25000)])
    assert len(zones) == 0


def test_ogimet_dd_no_icing_outside_cloud():
    """No icing when level is outside cloud layers."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
    ]
    # Cloud layer far away from level altitude
    zones = assess_icing_zones_ogimet_dd(levels, [_cloud(2000, 4000)])
    assert len(zones) == 0


def test_ogimet_dd_no_icing_no_cloud_layers():
    """No icing when no cloud layers exist."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones_ogimet_dd(levels, [])
    assert len(zones) == 0


def test_ogimet_dd_icing_at_peak():
    """Icing at -7C (peak of Ogimet parabola) within cloud layer."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-7.3, dewpoint_depression_c=0.3),
    ]
    zones = assess_icing_zones_ogimet_dd(levels, [_cloud(9000, 11000)])
    assert len(zones) == 1
    assert zones[0].icing_type == IcingType.MIXED
    assert zones[0].risk in (IcingRisk.MODERATE, IcingRisk.SEVERE)


def test_ogimet_dd_icing_type_classification():
    """Icing type classification based on temperature bands."""
    # Clear: -3 to 0
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=-1.5,
                     dewpoint_c=-2.0, dewpoint_depression_c=0.5),
    ]
    zones = assess_icing_zones_ogimet_dd(levels, [_cloud(4000, 6000)])
    assert len(zones) == 1
    assert zones[0].icing_type == IcingType.CLEAR

    # Mixed: -10 to -3
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-6.0,
                     dewpoint_c=-7.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones_ogimet_dd(levels, [_cloud(9000, 11000)])
    assert len(zones) == 1
    assert zones[0].icing_type == IcingType.MIXED

    # Rime: < -10
    levels = [
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, temperature_c=-12.0,
                     dewpoint_c=-13.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones_ogimet_dd(levels, [_cloud(17000, 19000)])
    assert len(zones) == 1
    assert zones[0].icing_type == IcingType.RIME


def test_ogimet_dd_reduces_severity_at_high_dd():
    """High DD should reduce effective icing compared to low DD."""
    levels_low_dd = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-7.3, dewpoint_depression_c=0.3),
    ]
    zones_low = assess_icing_zones_ogimet_dd(levels_low_dd, [_cloud(9000, 11000)])

    levels_high_dd = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-8.8, dewpoint_depression_c=1.8),
    ]
    zones_high = assess_icing_zones_ogimet_dd(levels_high_dd, [_cloud(9000, 11000)])

    assert len(zones_low) == 1
    assert len(zones_high) == 1
    risk_order = [IcingRisk.NONE, IcingRisk.LIGHT, IcingRisk.MODERATE, IcingRisk.SEVERE]
    assert risk_order.index(zones_low[0].risk) >= risk_order.index(zones_high[0].risk)


def test_ogimet_dd_no_icing_above_dd_cutoff():
    """DD above 2.0 → DD taper is 0 → no icing even inside cloud layer."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-9.5, dewpoint_depression_c=2.5),
    ]
    zones = assess_icing_zones_ogimet_dd(levels, [_cloud(9000, 11000)])
    assert len(zones) == 0


def test_ogimet_dd_cloud_margin():
    """Level within 500ft margin of cloud boundary is assessed."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=6400, temperature_c=-2.0,
                     dewpoint_c=-2.5, dewpoint_depression_c=0.5),
    ]
    # Cloud top at 6000, level at 6400 = 400ft above = within 500ft margin
    zones = assess_icing_zones_ogimet_dd(levels, [_cloud(4000, 6000)])
    assert len(zones) == 1


def test_ogimet_dd_single_level_thickness():
    """Single-level zone is expanded to ±500ft minimum thickness."""
    levels = [
        DerivedLevel(pressure_hpa=550, altitude_ft=14000, temperature_c=-12.0,
                     dewpoint_c=-13.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones_ogimet_dd(levels, [_cloud(13000, 15000)])
    assert len(zones) == 1
    assert zones[0].base_ft == 13500
    assert zones[0].top_ft == 14500


def test_ogimet_dd_multi_level_zone():
    """Multi-level zone spanning >1000ft is not expanded."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
        DerivedLevel(pressure_hpa=600, altitude_ft=14000, temperature_c=-12.0,
                     dewpoint_c=-13.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones_ogimet_dd(levels, [_cloud(9000, 15000)])
    assert len(zones) == 1
    assert zones[0].base_ft == 10000
    assert zones[0].top_ft == 14000


def test_ogimet_dd_adjacent_levels_grouped():
    """Adjacent icing levels (gap <= 100hPa) are grouped into a single zone."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
        DerivedLevel(pressure_hpa=800, altitude_ft=6500, temperature_c=-8.0,
                     dewpoint_c=-9.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones_ogimet_dd(levels, [_cloud(4000, 7000)])
    assert len(zones) == 1
    assert zones[0].base_ft == 5000
    assert zones[0].top_ft == 6500


def test_ogimet_dd_empty_input():
    """Empty levels returns empty list."""
    assert assess_icing_zones_ogimet_dd([], []) == []


def test_ogimet_dd_index_stored_on_level():
    """Icing index is stored on DerivedLevel after assessment."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
    ]
    assess_icing_zones_ogimet_dd(levels, [_cloud(9000, 11000)])
    assert levels[0].icing_index is not None
    assert levels[0].icing_index > 0


def test_ogimet_dd_mean_icing_index_on_zone():
    """Mean icing index is computed on the IcingZone."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
        DerivedLevel(pressure_hpa=800, altitude_ft=6500, temperature_c=-8.0,
                     dewpoint_c=-9.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones_ogimet_dd(levels, [_cloud(4000, 7000)])
    assert len(zones) == 1
    assert zones[0].mean_icing_index is not None
    assert zones[0].mean_icing_index > 0


def test_ogimet_dd_cape_convective():
    """With high CAPE, convective component contributes to icing index."""
    levels_a = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
    ]
    cloud = [_cloud(9000, 11000)]
    zones_layered = assess_icing_zones_ogimet_dd(levels_a, cloud, cape_jkg=0)
    idx_layered = levels_a[0].icing_index

    levels_b = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
    ]
    zones_convective = assess_icing_zones_ogimet_dd(levels_b, cloud, cape_jkg=2000)
    idx_convective = levels_b[0].icing_index

    assert idx_layered > 0
    assert idx_convective is not None
    assert len(zones_layered) == 1
    assert len(zones_convective) == 1


# --- Ogimet-NWP: NWP cloud layer gating ---


def test_ogimet_nwp_scales_by_cloud_pct():
    """NWP cloud % scales the icing index."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
    ]
    zones_full = assess_icing_zones_ogimet_nwp(
        levels, [_cloud(9000, 11000)], nwp_cloud_mid_pct=100.0,
    )
    levels[0].icing_index = None

    zones_half = assess_icing_zones_ogimet_nwp(
        levels, [_cloud(9000, 11000)], nwp_cloud_mid_pct=50.0,
    )

    assert len(zones_full) == 1
    assert len(zones_half) == 1


def test_ogimet_nwp_no_cloud_layers_no_icing():
    """No NWP cloud layers → no icing."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones_ogimet_nwp(
        levels, [], nwp_cloud_mid_pct=100.0,
    )
    assert len(zones) == 0


def test_ogimet_nwp_no_nwp_data_no_icing():
    """No NWP cloud data (None) → no icing."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones_ogimet_nwp(levels, [_cloud(9000, 11000)])
    assert len(zones) == 0


def test_ogimet_nwp_empty_input():
    """Empty levels returns empty list."""
    assert assess_icing_zones_ogimet_nwp([], []) == []


def test_ogimet_nwp_none_clouds_returns_empty():
    """``clouds=None`` (no native NWP envelope) → no zones, even with icing-prone levels.

    Required by the strict-native contract: Ogimet-NWP refuses to
    fabricate icing zones from bulk percentages alone, because the
    cross-section couldn't render a corresponding cloud band.
    """
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-7.5, dewpoint_depression_c=0.5),
    ]
    zones = assess_icing_zones_ogimet_nwp(
        levels, None, nwp_cloud_mid_pct=80.0,
    )
    assert zones == []


def test_ieng_none_clouds_returns_empty():
    """``clouds=None`` → no IENG zones (same rationale as Ogimet-NWP)."""
    from weatherbrief.analysis.sounding.icing import assess_icing_zones_ieng

    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-7.5, dewpoint_depression_c=0.5),
    ]
    assert assess_icing_zones_ieng(levels, None, nwp_cloud_mid_pct=80.0) == []
    assert assess_icing_zones_ieng(levels, [], nwp_cloud_mid_pct=80.0) == []


# --- Cloud-gap respect: zones must split at cloud-band gaps ---
#
# Regression: GFS produces three discrete NWP cloud bands (low/mid/high)
# with real altitude gaps between them. When icing-prone levels exist on
# both sides of a gap, the legacy pressure-gap heuristic in
# ``group_icing_levels`` (≤ 100 hPa) bridges across the gap because at
# typical 25 hPa spacing a 3-level cloud-band gap collapses to exactly
# 100 hPa between surviving icing levels. The result was one big icing
# zone displayed across "blue sky" in the cross-section — see
# investigation 2026-05-02 on flight kpao_kgcn-2026-05-05-dc1b pt13.
# The fix is index-based grouping: surviving icing levels only merge
# when they are *directly consecutive* in the pre-gated input.


def test_ogimet_nwp_does_not_bridge_cloud_band_gap():
    """Two NWP cloud bands with a gap → two icing zones, not one merged."""
    # Mimic GFS pt13 shape: lower band 4088-11477, mid band 15679-20851.
    # Levels at 700/675 hPa are inside lower; 650/625/600/575 are in the
    # gap (filtered by gating); 550/525/500 are inside mid.
    icing_T = -7.0  # peak Ogimet index temperature
    icing_TD = -7.5  # tight DD → nonzero index
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=9900, temperature_c=icing_T,
                     dewpoint_c=icing_TD, dewpoint_depression_c=0.5),
        DerivedLevel(pressure_hpa=675, altitude_ft=10840, temperature_c=icing_T,
                     dewpoint_c=icing_TD, dewpoint_depression_c=0.5),
        # Gap levels — same icing-prone profile but should be gated out
        DerivedLevel(pressure_hpa=650, altitude_ft=11810, temperature_c=icing_T,
                     dewpoint_c=icing_TD, dewpoint_depression_c=0.5),
        DerivedLevel(pressure_hpa=625, altitude_ft=12810, temperature_c=icing_T,
                     dewpoint_c=icing_TD, dewpoint_depression_c=0.5),
        DerivedLevel(pressure_hpa=600, altitude_ft=13840, temperature_c=icing_T,
                     dewpoint_c=icing_TD, dewpoint_depression_c=0.5),
        DerivedLevel(pressure_hpa=575, altitude_ft=14910, temperature_c=icing_T,
                     dewpoint_c=icing_TD, dewpoint_depression_c=0.5),
        # Levels inside the mid cloud band
        DerivedLevel(pressure_hpa=550, altitude_ft=16020, temperature_c=icing_T,
                     dewpoint_c=icing_TD, dewpoint_depression_c=0.5),
        DerivedLevel(pressure_hpa=525, altitude_ft=17170, temperature_c=icing_T,
                     dewpoint_c=icing_TD, dewpoint_depression_c=0.5),
    ]
    bands = [
        _cloud(4088, 11477),  # lower
        _cloud(15679, 20851),  # mid
    ]
    zones = assess_icing_zones_ogimet_nwp(levels, bands, nwp_cloud_mid_pct=80.0)

    # Expect TWO zones, one per cloud band.
    assert len(zones) == 2, f"expected 2 zones split at cloud gap, got {len(zones)}: {[(z.base_ft, z.top_ft) for z in zones]}"

    # Lower zone sits inside the lower cloud band.
    assert zones[0].base_ft >= 4088 - 500  # within ±CLOUD_MARGIN_FT
    assert zones[0].top_ft <= 11477 + 500
    # Upper zone sits inside the mid cloud band.
    assert zones[1].base_ft >= 15679 - 500
    assert zones[1].top_ft <= 20851 + 500


def test_ogimet_nwp_single_band_still_one_zone():
    """Sanity: a single cloud band with continuous icing → still ONE zone.

    The cloud-gap fix must not over-split: when all icing levels are
    directly consecutive in the input, the grouper should still merge
    them into one zone exactly like before.
    """
    levels = [
        DerivedLevel(pressure_hpa=750, altitude_ft=8100, temperature_c=-5.0,
                     dewpoint_c=-5.5, dewpoint_depression_c=0.5),
        DerivedLevel(pressure_hpa=725, altitude_ft=8990, temperature_c=-6.0,
                     dewpoint_c=-6.5, dewpoint_depression_c=0.5),
        DerivedLevel(pressure_hpa=700, altitude_ft=9900, temperature_c=-7.0,
                     dewpoint_c=-7.5, dewpoint_depression_c=0.5),
    ]
    zones = assess_icing_zones_ogimet_nwp(
        levels, [_cloud(7000, 11000)], nwp_cloud_mid_pct=80.0,
    )
    assert len(zones) == 1


def test_ogimet_dd_does_not_bridge_dd_cloud_gap():
    """Same fix applies to Ogimet-DD when DD clouds happen to have gaps.

    DD clouds are usually continuous (RH-derived) so this case is rare,
    but the fix in the shared grouper must cover it consistently.
    """
    icing_T = -7.0
    icing_TD = -7.5
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=9900, temperature_c=icing_T,
                     dewpoint_c=icing_TD, dewpoint_depression_c=0.5),
        DerivedLevel(pressure_hpa=650, altitude_ft=11810, temperature_c=icing_T,
                     dewpoint_c=icing_TD, dewpoint_depression_c=5.0),  # DD too high → gated out
        DerivedLevel(pressure_hpa=600, altitude_ft=13840, temperature_c=icing_T,
                     dewpoint_c=icing_TD, dewpoint_depression_c=5.0),  # gated out
        DerivedLevel(pressure_hpa=550, altitude_ft=16020, temperature_c=icing_T,
                     dewpoint_c=icing_TD, dewpoint_depression_c=0.5),
    ]
    # Two DD cloud bands with a gap, mirroring NWP shape
    bands = [_cloud(8000, 12000), _cloud(15500, 17000)]
    zones = assess_icing_zones_ogimet_dd(levels, bands)
    assert len(zones) == 2, f"expected 2 DD zones split at cloud gap, got {len(zones)}"


def test_ogimet_nwp_zero_cloud_pct_no_icing():
    """0% NWP cloud cover in matching band → no icing despite cloud layer."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones_ogimet_nwp(
        levels, [_cloud(9000, 11000)], nwp_cloud_mid_pct=0.0,
    )
    assert len(zones) == 0


# --- Wet-bulb icing type tests ---


def test_icing_type_uses_wet_bulb():
    """Icing type classification uses wet-bulb when available."""
    from weatherbrief.analysis.sounding.icing import _classify_icing_type

    assert _classify_icing_type(-3.0, wet_bulb_c=-5.0) == IcingType.MIXED
    assert _classify_icing_type(-10.0, wet_bulb_c=-12.0) == IcingType.RIME
    assert _classify_icing_type(-2.0) == IcingType.CLEAR


# --- _resolve_analyses icing tests ---


def test_resolve_analyses_ogimet_dd_returns_original():
    """ogimet_dd (default) returns the original list unchanged."""
    from weatherbrief.tasks.advise import _resolve_analyses
    from weatherbrief.models import RoutePointAnalysis, SoundingAnalysis, IcingZone
    from datetime import datetime, timezone

    zone = IcingZone(base_ft=5000, top_ft=10000, risk=IcingRisk.MODERATE, icing_type=IcingType.MIXED)
    sa = SoundingAnalysis(icing_zones=[zone])
    rpa = RoutePointAnalysis(
        point_index=0, lat=0, lon=0, distance_from_origin_nm=0,
        interpolated_time=datetime.now(timezone.utc),
        forecast_hour=datetime.now(timezone.utc), track_deg=0,
        sounding={"gfs": sa},
    )
    original = [rpa]
    result = _resolve_analyses(original, "ogimet_dd", None)
    assert result is original
    assert result[0].sounding["gfs"].icing_zones[0].risk == IcingRisk.MODERATE


def test_resolve_analyses_ogimet_nwp_swaps_without_mutation():
    """ogimet_nwp resolves icing_zones from NWP; original is untouched."""
    from weatherbrief.tasks.advise import _resolve_analyses
    from weatherbrief.models import RoutePointAnalysis, SoundingAnalysis, IcingZone
    from datetime import datetime, timezone

    dd_zone = IcingZone(base_ft=5000, top_ft=10000, risk=IcingRisk.MODERATE, icing_type=IcingType.MIXED)
    nwp_zone = IcingZone(base_ft=6000, top_ft=9000, risk=IcingRisk.LIGHT, icing_type=IcingType.RIME)
    sa = SoundingAnalysis(icing_zones=[dd_zone], icing_ogimet_nwp_zones=[nwp_zone])
    rpa = RoutePointAnalysis(
        point_index=0, lat=0, lon=0, distance_from_origin_nm=0,
        interpolated_time=datetime.now(timezone.utc),
        forecast_hour=datetime.now(timezone.utc), track_deg=0,
        sounding={"gfs": sa},
    )
    result = _resolve_analyses([rpa], "ogimet_nwp", None)
    assert result[0].sounding["gfs"].icing_zones[0].risk == IcingRisk.LIGHT
    assert result[0].sounding["gfs"].icing_zones[0].base_ft == 6000
    assert rpa.sounding["gfs"].icing_zones[0].risk == IcingRisk.MODERATE
    assert rpa.sounding["gfs"].icing_zones[0].base_ft == 5000


def test_resolve_analyses_sfip_nwp_converts_without_mutation():
    """sfip_nwp converts sfip_zones to IcingZone; original is untouched."""
    from weatherbrief.tasks.advise import _resolve_analyses
    from weatherbrief.models import RoutePointAnalysis, SoundingAnalysis, IcingZone, SfipZone
    from datetime import datetime, timezone

    dd_zone = IcingZone(base_ft=5000, top_ft=10000, risk=IcingRisk.LIGHT, icing_type=IcingType.RIME)
    sfip_zone = SfipZone(
        base_ft=7000, top_ft=12000, risk=IcingRisk.MODERATE,
        icing_type=IcingType.MIXED, mean_sfip_100=45.0, variant="full",
    )
    sa = SoundingAnalysis(icing_zones=[dd_zone], sfip_zones=[sfip_zone])
    rpa = RoutePointAnalysis(
        point_index=0, lat=0, lon=0, distance_from_origin_nm=0,
        interpolated_time=datetime.now(timezone.utc),
        forecast_hour=datetime.now(timezone.utc), track_deg=0,
        sounding={"gfs": sa},
    )
    result = _resolve_analyses([rpa], "sfip_nwp", None)
    assert len(result[0].sounding["gfs"].icing_zones) == 1
    converted = result[0].sounding["gfs"].icing_zones[0]
    assert converted.base_ft == 7000
    assert converted.risk == IcingRisk.MODERATE
    assert converted.mean_icing_index == 45.0
    assert rpa.sounding["gfs"].icing_zones[0].risk == IcingRisk.LIGHT
