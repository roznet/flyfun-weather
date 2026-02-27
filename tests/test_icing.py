"""Tests for enhanced icing assessment using Ogimet index (sounding/icing.py)."""

from weatherbrief.analysis.sounding.icing import (
    _cape_to_cloud_split,
    _compute_layered_index,
    _index_to_risk,
    assess_icing_zones,
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
    """RH upgrade requires ≥3 levels with RH > 95%, NWP cloud ≥ 50%, and cold temps for SEVERE."""
    # Single level, high RH — should NOT upgrade (insufficient corroboration)
    levels_single = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-7.5, dewpoint_depression_c=0.5,
                     relative_humidity_pct=97.0),
    ]
    zones = assess_icing_zones(levels_single, [_cloud(9000, 11000)])
    assert len(zones) == 1
    assert zones[0].risk == IcingRisk.MODERATE  # no upgrade (only 1 level)

    # Two levels with RH > 95% — still not enough (need 3)
    levels_two = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-7.5, dewpoint_depression_c=0.5,
                     relative_humidity_pct=97.0),
        DerivedLevel(pressure_hpa=650, altitude_ft=12000, temperature_c=-10.0,
                     dewpoint_c=-10.5, dewpoint_depression_c=0.5,
                     relative_humidity_pct=98.0),
    ]
    zones = assess_icing_zones(
        levels_two, [_cloud(9000, 13000)], nwp_cloud_mid_pct=60.0,
    )
    assert len(zones) == 1
    assert zones[0].risk == IcingRisk.MODERATE  # not enough levels

    # Three cold levels with RH > 95% AND NWP cloud ≥ 50% — should upgrade
    levels_three_cold = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-7.5, dewpoint_depression_c=0.5,
                     relative_humidity_pct=97.0),
        DerivedLevel(pressure_hpa=650, altitude_ft=12000, temperature_c=-10.0,
                     dewpoint_c=-10.5, dewpoint_depression_c=0.5,
                     relative_humidity_pct=98.0),
        DerivedLevel(pressure_hpa=600, altitude_ft=14000, temperature_c=-13.0,
                     dewpoint_c=-13.5, dewpoint_depression_c=0.5,
                     relative_humidity_pct=96.0),
    ]
    zones = assess_icing_zones(
        levels_three_cold, [_cloud(9000, 15000)], nwp_cloud_mid_pct=60.0,
    )
    assert len(zones) == 1
    assert zones[0].risk == IcingRisk.SEVERE  # cold, deep, saturated → upgrade

    # Three warm levels (mean > -5°C) — should NOT upgrade to SEVERE
    levels_three_warm = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=-2.0,
                     dewpoint_c=-2.5, dewpoint_depression_c=0.5,
                     relative_humidity_pct=97.0),
        DerivedLevel(pressure_hpa=800, altitude_ft=6500, temperature_c=-3.0,
                     dewpoint_c=-3.5, dewpoint_depression_c=0.5,
                     relative_humidity_pct=98.0),
        DerivedLevel(pressure_hpa=750, altitude_ft=8000, temperature_c=-4.0,
                     dewpoint_c=-4.5, dewpoint_depression_c=0.5,
                     relative_humidity_pct=96.0),
    ]
    zones = assess_icing_zones(
        levels_three_warm, [_cloud(4000, 9000)], nwp_cloud_low_pct=80.0,
    )
    assert len(zones) == 1
    assert zones[0].risk == IcingRisk.MODERATE  # warm icing stays MODERATE

    # NWP cloud too low — should NOT upgrade
    zones = assess_icing_zones(
        levels_three_cold, [_cloud(9000, 15000)], nwp_cloud_mid_pct=10.0,
    )
    assert len(zones) == 1
    assert zones[0].risk == IcingRisk.MODERATE


def test_near_cloud_margin():
    """Level within 500ft of cloud boundary is assessed."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=6400, temperature_c=-2.0,
                     dewpoint_c=-3.0, dewpoint_depression_c=5.0),
    ]
    # Cloud top at 6000, level at 6400 = 400ft above = within 500ft margin
    zones = assess_icing_zones(levels, [_cloud(4000, 6000)])
    assert len(zones) == 1


def test_single_level_zone_minimum_thickness():
    """Single-level zone is expanded to ±500ft minimum thickness."""
    levels = [
        DerivedLevel(pressure_hpa=550, altitude_ft=14000, temperature_c=-12.0,
                     dewpoint_c=-13.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones(levels, [_cloud(13000, 15000)])
    assert len(zones) == 1
    # Single level at 14000ft → should expand to 13500–14500ft
    assert zones[0].base_ft == 13500
    assert zones[0].top_ft == 14500


def test_multi_level_zone_unchanged():
    """Multi-level zone spanning >1000ft is not expanded."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
        DerivedLevel(pressure_hpa=600, altitude_ft=14000, temperature_c=-12.0,
                     dewpoint_c=-13.0, dewpoint_depression_c=1.0),
    ]
    zones = assess_icing_zones(levels, [_cloud(9000, 15000)])
    assert len(zones) == 1
    assert zones[0].base_ft == 10000
    assert zones[0].top_ft == 14000


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


# --- NWP cloud cover fallback tests ---


def test_nwp_cloud_fallback_catches_dry_level():
    """NWP cloud cover > 50% triggers icing assessment for dry-sounding levels."""
    # Level at -7°C but high DD (dry sounding) — no cloud proximity
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-15.0, dewpoint_depression_c=8.0),
    ]
    # Without NWP cloud: no icing detected
    zones_no_nwp = assess_icing_zones(levels, [])
    assert len(zones_no_nwp) == 0

    # Reset icing_index
    levels[0].icing_index = None

    # With NWP mid cloud > 50% and diagnostics covering 10,000ft:
    # icing should be detected via fallback
    diag = NWPCloudDiagnostics(
        mid=NWPCloudLayerDiag(cover_pct=80.0, base_ft=8000, top_ft=12000),
    )
    zones_nwp = assess_icing_zones(
        levels, [], nwp_cloud_mid_pct=80.0, nwp_cloud_diagnostics=diag,
    )
    assert len(zones_nwp) == 1
    assert zones_nwp[0].risk != IcingRisk.NONE


def test_nwp_cloud_fallback_skips_warm_levels():
    """NWP fallback does not trigger for levels above 0°C."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, temperature_c=3.0,
                     dewpoint_c=-5.0, dewpoint_depression_c=8.0),
    ]
    zones = assess_icing_zones(levels, [], nwp_cloud_low_pct=90.0)
    assert len(zones) == 0


def test_nwp_cloud_fallback_skips_already_assessed():
    """NWP fallback doesn't double-count levels already assessed in pass 1."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-8.0, dewpoint_depression_c=1.0),
    ]
    # Level has low DD → will be assessed in pass 1 via cloud proximity
    # NWP cloud also high → pass 2 should skip it
    zones = assess_icing_zones(
        levels, [_cloud(9000, 11000)], nwp_cloud_mid_pct=90.0,
    )
    assert len(zones) == 1  # only one zone, not duplicated


def test_nwp_cloud_fallback_low_cloud_below_threshold():
    """NWP cloud < 50% does not trigger the fallback."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-15.0, dewpoint_depression_c=8.0),
    ]
    zones = assess_icing_zones(levels, [], nwp_cloud_mid_pct=30.0)
    assert len(zones) == 0


# --- Wet-bulb icing type tests ---


def test_icing_type_uses_wet_bulb():
    """Icing type classification uses wet-bulb when available."""
    from weatherbrief.analysis.sounding.icing import _classify_icing_type

    # Dry-bulb = -3°C (border clear/mixed), wet-bulb = -5°C (MIXED range)
    assert _classify_icing_type(-3.0, wet_bulb_c=-5.0) == IcingType.MIXED

    # Dry-bulb = -10°C (border mixed/rime), wet-bulb = -12°C (RIME range)
    assert _classify_icing_type(-10.0, wet_bulb_c=-12.0) == IcingType.RIME

    # No wet-bulb → falls back to dry-bulb
    assert _classify_icing_type(-2.0) == IcingType.CLEAR


# --- Altitude-aware NWP cloud diagnostics tests ---


def test_nwp_cloud_high_altitude_no_icing_at_low():
    """NWP mid cloud at 72% but located at 21,000ft should NOT trigger icing at 10,000ft."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-15.0, dewpoint_depression_c=8.0),
    ]
    # Cloud diagnostics: mid-cloud layer is at 21,000–23,000ft (glaciated, far above)
    diag = NWPCloudDiagnostics(
        mid=NWPCloudLayerDiag(cover_pct=72.0, base_ft=21000, top_ft=23000),
    )
    zones = assess_icing_zones(
        levels, [], nwp_cloud_mid_pct=72.0, nwp_cloud_diagnostics=diag,
    )
    assert len(zones) == 0


def test_nwp_cloud_covering_level_triggers_icing():
    """NWP mid cloud at 72% with base/top covering 10,000ft SHOULD trigger icing."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-15.0, dewpoint_depression_c=8.0),
    ]
    # Cloud diagnostics: mid-cloud layer covers the level altitude
    diag = NWPCloudDiagnostics(
        mid=NWPCloudLayerDiag(cover_pct=72.0, base_ft=8000, top_ft=14000),
    )
    zones = assess_icing_zones(
        levels, [], nwp_cloud_mid_pct=72.0, nwp_cloud_diagnostics=diag,
    )
    assert len(zones) == 1
    assert zones[0].risk != IcingRisk.NONE


def test_nwp_cloud_fallback_bulk_pct_when_no_diagnostics():
    """Without diagnostics, fall back to bulk % behavior (backward compat)."""
    levels = [
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, temperature_c=-7.0,
                     dewpoint_c=-15.0, dewpoint_depression_c=8.0),
    ]
    # No diagnostics — should use bulk nwp_cloud_mid_pct as before
    zones = assess_icing_zones(
        levels, [], nwp_cloud_mid_pct=80.0, nwp_cloud_diagnostics=None,
    )
    assert len(zones) == 1
    assert zones[0].risk != IcingRisk.NONE
