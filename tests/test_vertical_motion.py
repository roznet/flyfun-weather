"""Tests for vertical motion analysis and turbulence indicators."""

from __future__ import annotations

from weatherbrief.analysis.sounding import analyze_sounding
from weatherbrief.analysis.sounding.prepare import prepare_profile
from weatherbrief.analysis.sounding.thermodynamics import compute_derived_levels
from weatherbrief.analysis.sounding.vertical_motion import (
    assess_vertical_motion,
    classify_vertical_motion,
    compute_stability_indicators,
)
from weatherbrief.models import (
    CATRiskLevel,
    DerivedLevel,
    PressureLevelData,
    VerticalMotionClass,
)


# --- Classification tests ---


def test_classify_unavailable_no_omega():
    """UNAVAILABLE when no omega data present."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000),
    ]
    assert classify_vertical_motion(levels) == VerticalMotionClass.UNAVAILABLE


def test_classify_quiescent():
    """QUIESCENT when all |omega| < 0.1 Pa/s."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, omega_pa_s=0.02),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, omega_pa_s=-0.03),
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, omega_pa_s=0.01),
    ]
    assert classify_vertical_motion(levels) == VerticalMotionClass.QUIESCENT


def test_classify_synoptic_ascent():
    """SYNOPTIC_ASCENT when coherent negative omega."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, omega_pa_s=-0.2),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, omega_pa_s=-0.3),
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, omega_pa_s=-0.15),
    ]
    assert classify_vertical_motion(levels) == VerticalMotionClass.SYNOPTIC_ASCENT


def test_classify_synoptic_subsidence():
    """SYNOPTIC_SUBSIDENCE when coherent positive omega."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, omega_pa_s=0.2),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, omega_pa_s=0.3),
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, omega_pa_s=0.15),
    ]
    assert classify_vertical_motion(levels) == VerticalMotionClass.SYNOPTIC_SUBSIDENCE


def test_classify_convective():
    """CONVECTIVE when |omega| > 1 Pa/s."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, omega_pa_s=-0.2),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, omega_pa_s=-1.5),
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, omega_pa_s=-0.3),
    ]
    assert classify_vertical_motion(levels) == VerticalMotionClass.CONVECTIVE


def test_classify_oscillating():
    """OSCILLATING when >=2 significant sign changes."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, omega_pa_s=-0.2),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, omega_pa_s=0.3),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, omega_pa_s=-0.25),
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, omega_pa_s=0.15),
    ]
    assert classify_vertical_motion(levels) == VerticalMotionClass.OSCILLATING


# --- Stability indicators tests ---


def test_stability_indicators_computed(sample_pressure_levels_with_omega):
    """N² and Ri are computed for levels with wind and omega data."""
    profile = prepare_profile(sample_pressure_levels_with_omega)
    assert profile is not None

    derived = compute_derived_levels(profile)
    compute_stability_indicators(profile, derived)

    # Upper levels should have N² and Ri values (layer below)
    levels_with_n2 = [lv for lv in derived if lv.bv_freq_squared_per_s2 is not None]
    levels_with_ri = [lv for lv in derived if lv.richardson_number is not None]

    # Should have at least some computed values (not all levels will have them)
    assert len(levels_with_n2) > 0
    assert len(levels_with_ri) > 0


def test_stability_indicators_positive_n2(sample_pressure_levels_with_omega):
    """N² should generally be positive in a stably-stratified atmosphere."""
    profile = prepare_profile(sample_pressure_levels_with_omega)
    derived = compute_derived_levels(profile)
    compute_stability_indicators(profile, derived)

    n2_vals = [lv.bv_freq_squared_per_s2 for lv in derived if lv.bv_freq_squared_per_s2 is not None]
    # Standard atmosphere is stably stratified, N² should be positive
    assert all(n2 > 0 for n2 in n2_vals)


# --- CAT risk layer tests ---


def test_cat_risk_from_low_ri():
    """Low Ri values produce CAT risk layers split by severity."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, omega_pa_s=-1.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, omega_pa_s=-1.5,
                     richardson_number=0.3),
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, omega_pa_s=-1.0,
                     richardson_number=0.8),
    ]
    assessment = assess_vertical_motion(levels)

    # Ri=0.3 → SEVERE, Ri=0.8 → MODERATE — different severities → 2 layers
    assert len(assessment.cat_risk_layers) == 2
    assert assessment.cat_risk_layers[0].risk == CATRiskLevel.SEVERE
    assert assessment.cat_risk_layers[0].base_ft == 10000
    assert assessment.cat_risk_layers[1].risk == CATRiskLevel.MODERATE
    assert assessment.cat_risk_layers[1].base_ft == 18000


def test_no_cat_risk_high_ri():
    """No CAT layers when Ri > 1.0 everywhere."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, omega_pa_s=-1.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, omega_pa_s=-1.5,
                     richardson_number=5.0),
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, omega_pa_s=-1.0,
                     richardson_number=10.0),
    ]
    assessment = assess_vertical_motion(levels)
    assert len(assessment.cat_risk_layers) == 0


def test_cat_layer_grouping_same_severity():
    """Adjacent low-Ri levels with the same severity form one layer."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, omega_pa_s=-1.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, omega_pa_s=-1.5,
                     richardson_number=0.6),
        DerivedLevel(pressure_hpa=600, altitude_ft=14000, omega_pa_s=-1.2,
                     richardson_number=0.7),
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, omega_pa_s=-1.0,
                     richardson_number=5.0),
    ]
    assessment = assess_vertical_motion(levels)
    # Both levels are MODERATE (0.5 <= Ri < 1.0) — one layer
    assert len(assessment.cat_risk_layers) == 1
    layer = assessment.cat_risk_layers[0]
    assert layer.base_ft == 10000
    assert layer.top_ft == 14000
    assert layer.risk == CATRiskLevel.MODERATE


def test_severity_split_boundary_layer():
    """A deep layer with mixed severities is split by severity tier.

    Reproduces the EGTF→EGBJ scenario: SEVERE shear at 975hPa (BL)
    chains through MODERATE mid-levels. Without severity splitting, the
    whole band would be painted SEVERE. With splitting, each altitude
    range gets its own accurate risk.
    """
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=545, omega_pa_s=-0.1),
        DerivedLevel(pressure_hpa=975, altitude_ft=1224, omega_pa_s=-0.0,
                     richardson_number=0.31),   # SEVERE
        DerivedLevel(pressure_hpa=950, altitude_ft=1916, omega_pa_s=-0.2,
                     richardson_number=0.89),   # MODERATE
        DerivedLevel(pressure_hpa=925, altitude_ft=2621, omega_pa_s=-0.3,
                     richardson_number=1.60),   # LIGHT
        DerivedLevel(pressure_hpa=900, altitude_ft=3343, omega_pa_s=-0.3,
                     richardson_number=0.66),   # MODERATE
        DerivedLevel(pressure_hpa=850, altitude_ft=4839, omega_pa_s=-0.2,
                     richardson_number=0.78),   # MODERATE
    ]
    assessment = assess_vertical_motion(levels)

    # Should produce 3 sub-layers: SEVERE, MODERATE, LIGHT, MODERATE
    # But adjacent MODERATE levels merge, so: SEVERE(1224), MODERATE(1916),
    # LIGHT(2621), MODERATE(3343-4839)
    assert len(assessment.cat_risk_layers) == 4

    risks = [l.risk for l in assessment.cat_risk_layers]
    assert risks == [
        CATRiskLevel.SEVERE,
        CATRiskLevel.MODERATE,
        CATRiskLevel.LIGHT,
        CATRiskLevel.MODERATE,
    ]

    # Cruise at 4000ft would fall in the last MODERATE layer, not SEVERE
    cruise = 4000
    layers_at_cruise = [
        l for l in assessment.cat_risk_layers
        if l.base_ft <= cruise <= l.top_ft
    ]
    assert len(layers_at_cruise) == 1
    assert layers_at_cruise[0].risk == CATRiskLevel.MODERATE


def test_boundary_layer_shear_not_merged_to_cruise():
    """Boundary-layer shear (low Ri near surface) must not merge into a deep
    layer that reaches cruise altitude.  With the 100 hPa grouping threshold,
    levels at 950/900 hPa (BL) should stay separate from 700/600 hPa."""
    levels = [
        # Surface / boundary layer — low Ri (wind shear in BL)
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, omega_pa_s=-0.5),
        DerivedLevel(pressure_hpa=950, altitude_ft=1800, omega_pa_s=-0.5,
                     richardson_number=0.2),
        DerivedLevel(pressure_hpa=900, altitude_ft=3200, omega_pa_s=-0.5,
                     richardson_number=0.3),
        # Gap — no shear at mid-levels
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, omega_pa_s=-0.5,
                     richardson_number=5.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, omega_pa_s=-0.5,
                     richardson_number=5.0),
        DerivedLevel(pressure_hpa=600, altitude_ft=14000, omega_pa_s=-0.5,
                     richardson_number=5.0),
    ]
    assessment = assess_vertical_motion(levels)

    # Should get exactly one CAT layer from the BL shear
    assert len(assessment.cat_risk_layers) == 1
    layer = assessment.cat_risk_layers[0]

    # Layer must be confined to boundary layer, not reaching cruise (~6000 ft)
    assert layer.top_ft <= 4000, (
        f"BL shear layer top {layer.top_ft} ft should not reach cruise altitude"
    )


def test_scattered_cat_not_merged_across_stable_gap():
    """Qualifying levels separated by 3+ stable levels must not merge.

    GFS-realistic 25 hPa spacing: low Ri at 975/950/925 and at 700, with
    several stable levels in between.  The index gap is too large to merge.
    BL cluster has mixed severities (MODERATE + LIGHT), which also get split.
    """
    levels = [
        # Surface
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, omega_pa_s=-0.5),
        # BL low-Ri cluster (indices 1, 2, 3)
        DerivedLevel(pressure_hpa=975, altitude_ft=1100, omega_pa_s=-0.5,
                     richardson_number=0.59),   # MODERATE
        DerivedLevel(pressure_hpa=950, altitude_ft=1800, omega_pa_s=-0.5,
                     richardson_number=0.96),   # MODERATE
        DerivedLevel(pressure_hpa=925, altitude_ft=2500, omega_pa_s=-0.5,
                     richardson_number=1.04),   # LIGHT
        # Stable gap (indices 4, 5, 6, 7)
        DerivedLevel(pressure_hpa=900, altitude_ft=3200, omega_pa_s=-0.5,
                     richardson_number=2.84),
        DerivedLevel(pressure_hpa=875, altitude_ft=3900, omega_pa_s=-0.5,
                     richardson_number=56.15),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, omega_pa_s=-0.5,
                     richardson_number=135.0),
        DerivedLevel(pressure_hpa=800, altitude_ft=6200, omega_pa_s=-0.5,
                     richardson_number=20.0),
        # Upper low-Ri (index 8)
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, omega_pa_s=-0.5,
                     richardson_number=0.71),   # MODERATE
    ]
    assessment = assess_vertical_motion(levels)
    # BL: MODERATE(1100-1800) + LIGHT(2500) = 2 sub-layers
    # Upper: MODERATE(10000) = 1 layer
    assert len(assessment.cat_risk_layers) == 3, (
        f"Expected 3 layers (2 BL sub-layers + 1 upper), got {len(assessment.cat_risk_layers)}"
    )
    # BL layers stay low
    assert assessment.cat_risk_layers[0].top_ft <= 2000
    assert assessment.cat_risk_layers[1].top_ft <= 3000
    # Upper layer is separate
    assert assessment.cat_risk_layers[2].base_ft >= 9000


def test_cat_merges_across_single_stable_level():
    """Two qualifying levels with exactly 1 stable level between them still merge.

    Index gap = 2 (the default max_index_gap), so they should form one layer.
    """
    levels = [
        DerivedLevel(pressure_hpa=800, altitude_ft=6200, omega_pa_s=-0.5,
                     richardson_number=0.8),   # index 0, qualifying
        DerivedLevel(pressure_hpa=775, altitude_ft=7100, omega_pa_s=-0.5,
                     richardson_number=5.0),   # index 1, stable
        DerivedLevel(pressure_hpa=750, altitude_ft=8000, omega_pa_s=-0.5,
                     richardson_number=0.9),   # index 2, qualifying
    ]
    assessment = assess_vertical_motion(levels)
    assert len(assessment.cat_risk_layers) == 1, (
        f"Expected 1 merged layer, got {len(assessment.cat_risk_layers)}"
    )
    assert assessment.cat_risk_layers[0].base_ft == 6200
    assert assessment.cat_risk_layers[0].top_ft == 8000


def test_deep_layer_prevented_by_index_gap():
    """Full GFS pressure range with qualifying levels at BL and tropopause.

    No single layer should span more than 10,000 ft when the qualifying
    levels are separated by many stable levels in between.
    """
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, omega_pa_s=-0.5),
        # BL qualifying
        DerivedLevel(pressure_hpa=975, altitude_ft=1100, omega_pa_s=-0.5,
                     richardson_number=0.3),
        DerivedLevel(pressure_hpa=950, altitude_ft=1800, omega_pa_s=-0.5,
                     richardson_number=0.4),
        # Mid-level stable
        DerivedLevel(pressure_hpa=900, altitude_ft=3200, omega_pa_s=-0.5,
                     richardson_number=10.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, omega_pa_s=-0.5,
                     richardson_number=15.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, omega_pa_s=-0.5,
                     richardson_number=20.0),
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, omega_pa_s=-0.5,
                     richardson_number=25.0),
        # Tropopause qualifying
        DerivedLevel(pressure_hpa=300, altitude_ft=30000, omega_pa_s=-0.5,
                     richardson_number=0.2),
        DerivedLevel(pressure_hpa=275, altitude_ft=31500, omega_pa_s=-0.5,
                     richardson_number=0.35),
    ]
    assessment = assess_vertical_motion(levels)
    for layer in assessment.cat_risk_layers:
        span = layer.top_ft - layer.base_ft
        assert span <= 10000, (
            f"CAT layer {layer.base_ft}–{layer.top_ft} ft spans {span} ft, "
            f"exceeding 10,000 ft limit"
        )
    # Should have exactly 2 layers (BL + tropopause)
    assert len(assessment.cat_risk_layers) == 2


# --- Convective contamination tests ---


def test_convective_contamination_detected():
    """Mid-level |omega| > 0.5 Pa/s triggers convective contamination."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, omega_pa_s=-0.1),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, omega_pa_s=-0.15),
        DerivedLevel(pressure_hpa=600, altitude_ft=14000, omega_pa_s=-0.8),
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, omega_pa_s=-0.6),
        DerivedLevel(pressure_hpa=400, altitude_ft=24000, omega_pa_s=-0.2),
    ]
    assessment = assess_vertical_motion(levels)
    assert assessment.convective_contamination is True


def test_no_convective_contamination():
    """No contamination when mid-level omega is moderate."""
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, omega_pa_s=-0.1),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, omega_pa_s=-0.15),
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, omega_pa_s=-0.3),
        DerivedLevel(pressure_hpa=400, altitude_ft=24000, omega_pa_s=-0.2),
    ]
    assessment = assess_vertical_motion(levels)
    assert assessment.convective_contamination is False


# --- Omega → w conversion test ---


def test_omega_to_w_conversion(sample_pressure_levels_with_omega):
    """Omega values are converted to w (ft/min) in derived levels."""
    profile = prepare_profile(sample_pressure_levels_with_omega)
    assert profile is not None
    assert profile.omega is not None

    derived = compute_derived_levels(profile)

    # All levels with omega should have w_fpm computed
    levels_with_omega = [lv for lv in derived if lv.omega_pa_s is not None]
    levels_with_w = [lv for lv in derived if lv.w_fpm is not None]
    assert len(levels_with_omega) > 0
    assert len(levels_with_w) == len(levels_with_omega)

    # Negative omega (ascent) → positive w (upward)
    # At 500 hPa, -1.0 Pa/s, T=-28C → w should be roughly positive ~30 fpm
    lv_500 = next(lv for lv in derived if lv.pressure_hpa == 500)
    assert lv_500.omega_pa_s < 0  # ascending
    assert lv_500.w_fpm > 0  # upward in ft/min


# --- Integration tests ---


def test_analyze_sounding_with_omega(sample_pressure_levels_with_omega):
    """Full sounding analysis produces vertical motion assessment with omega data."""
    result = analyze_sounding(sample_pressure_levels_with_omega)
    assert result is not None
    assert result.vertical_motion is not None
    assert result.vertical_motion.classification != VerticalMotionClass.UNAVAILABLE
    assert result.vertical_motion.max_omega_pa_s is not None
    assert result.vertical_motion.max_w_fpm is not None


def test_analyze_sounding_without_omega(sample_pressure_levels):
    """Full sounding analysis without omega produces UNAVAILABLE classification."""
    result = analyze_sounding(sample_pressure_levels)
    assert result is not None
    assert result.vertical_motion is not None
    assert result.vertical_motion.classification == VerticalMotionClass.UNAVAILABLE
    assert result.vertical_motion.max_omega_pa_s is None


# --- Backward compatibility ---


def test_backward_compat_no_vertical_motion():
    """SoundingAnalysis deserializes correctly without vertical_motion field."""
    from weatherbrief.models import SoundingAnalysis

    # Simulate old JSON without vertical_motion
    old_json = '{"indices": null, "derived_levels": [], "cloud_layers": [], "icing_zones": [], "convective": null}'
    sa = SoundingAnalysis.model_validate_json(old_json)
    assert sa.vertical_motion is None


def test_backward_compat_no_omega_in_pressure_level():
    """PressureLevelData deserializes correctly without vertical_velocity_pa_s."""
    old_json = '{"pressure_hpa": 500, "temperature_c": -28}'
    pl = PressureLevelData.model_validate_json(old_json)
    assert pl.vertical_velocity_pa_s is None


def test_backward_compat_no_cat_risk_in_regime():
    """VerticalRegime deserializes correctly without cat_risk field."""
    from weatherbrief.models import VerticalRegime

    old_json = '{"floor_ft": 0, "ceiling_ft": 18000, "in_cloud": false, "label": "Clear"}'
    vr = VerticalRegime.model_validate_json(old_json)
    assert vr.cat_risk is None
    assert vr.strong_vertical_motion is False


# --- Statically unstable layers (negative Ri, N² < 0) ---


def test_negative_ri_elevated_layer_is_moderate_cat():
    """An elevated statically-unstable shear layer (Ri < 0) must surface as
    CAT, capped at MODERATE (buoyancy-driven turbulence, owned by the
    convective tier for severity) — not vanish as missing data."""
    levels = [
        DerivedLevel(pressure_hpa=900, altitude_ft=3000),
        DerivedLevel(pressure_hpa=850, altitude_ft=5000, richardson_number=5.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, richardson_number=-0.4),
        DerivedLevel(pressure_hpa=650, altitude_ft=12000, richardson_number=-1.2),
    ]
    assessment = assess_vertical_motion(levels)
    assert len(assessment.cat_risk_layers) == 1
    layer = assessment.cat_risk_layers[0]
    assert layer.risk == CATRiskLevel.MODERATE
    assert layer.base_ft == 10000
    assert layer.top_ft == 12000


def test_negative_ri_surface_layer_skipped():
    """Negative Ri on the surface-adjacent layer is the daytime superadiabatic
    layer (thermals) — excluded from CAT to avoid summer-afternoon noise."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300),
        DerivedLevel(pressure_hpa=975, altitude_ft=1000, richardson_number=-0.5),
        DerivedLevel(pressure_hpa=950, altitude_ft=1800, richardson_number=8.0),
    ]
    assessment = assess_vertical_motion(levels)
    assert assessment.cat_risk_layers == []


def test_stability_indicators_store_negative_ri():
    """compute_stability_indicators stores Ri for N² < 0 layers (negative Ri),
    so unstable layers are distinguishable from missing data."""
    import numpy as np
    from metpy.units import units as u

    from weatherbrief.analysis.sounding.prepare import PreparedProfile

    # Temperature increasing lapse over a strongly superadiabatic upper layer:
    # 20°C at 950 hPa → 10°C at 900 hPa over ~480 m is far steeper than the
    # dry adiabat → theta decreases with height → N² < 0.
    profile = PreparedProfile(
        pressure=np.array([1000.0, 950.0, 900.0]) * u.hPa,
        temperature=np.array([22.0, 20.0, 10.0]) * u.degC,
        dewpoint=np.array([10.0, 8.0, 0.0]) * u.degC,
        height=np.array([110.0, 550.0, 1030.0]) * u.m,
        wind_speed=np.array([5.0, 10.0, 15.0]) * u.knots,
        wind_direction=np.array([270.0, 270.0, 270.0]) * u.degrees,
        omega=None,
        surface_pressure=None,
        surface_temperature=None,
        surface_dewpoint=None,
    )
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=360),
        DerivedLevel(pressure_hpa=950, altitude_ft=1800),
        DerivedLevel(pressure_hpa=900, altitude_ft=3380),
    ]
    compute_stability_indicators(profile, levels)
    assert levels[2].richardson_number is not None
    assert levels[2].richardson_number < 0
