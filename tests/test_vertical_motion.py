"""Tests for vertical motion analysis and turbulence indicators."""

from __future__ import annotations

import pytest

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
                     richardson_number=0.15),
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, omega_pa_s=-1.0,
                     richardson_number=0.8),
    ]
    assessment = assess_vertical_motion(levels)

    # Ri=0.15 at 10,000 ft (classical tiers) → SEVERE; Ri=0.8 at 18,000 ft
    # (tiers scaled ×1.8) → MODERATE — different severities → 2 layers
    assert len(assessment.cat_risk_layers) == 2
    assert assessment.cat_risk_layers[0].risk == CATRiskLevel.SEVERE
    assert assessment.cat_risk_layers[0].base_ft == 10000
    assert assessment.cat_risk_layers[1].risk == CATRiskLevel.MODERATE
    assert assessment.cat_risk_layers[1].base_ft == 18000


def test_no_cat_risk_high_ri():
    """No CAT layers when Ri is well above the LIGHT tier everywhere."""
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
                     richardson_number=0.3),
        DerivedLevel(pressure_hpa=600, altitude_ft=14000, omega_pa_s=-1.2,
                     richardson_number=0.5),
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, omega_pa_s=-1.0,
                     richardson_number=5.0),
    ]
    assessment = assess_vertical_motion(levels)
    # MODERATE at both: 0.3 against the classical 0.25/0.5 tiers at 10,000 ft,
    # 0.5 against the ×1.4-scaled 0.35/0.7 tiers at 14,000 ft — one layer
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
                     richardson_number=0.155),  # SEVERE
        DerivedLevel(pressure_hpa=950, altitude_ft=1916, omega_pa_s=-0.2,
                     richardson_number=0.445),  # MODERATE
        DerivedLevel(pressure_hpa=925, altitude_ft=2621, omega_pa_s=-0.3,
                     richardson_number=0.80),   # LIGHT
        DerivedLevel(pressure_hpa=900, altitude_ft=3343, omega_pa_s=-0.3,
                     richardson_number=0.33),   # MODERATE
        DerivedLevel(pressure_hpa=850, altitude_ft=4839, omega_pa_s=-0.2,
                     richardson_number=0.39),   # MODERATE
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
                     richardson_number=0.15),
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
                     richardson_number=0.295),  # MODERATE
        DerivedLevel(pressure_hpa=950, altitude_ft=1800, omega_pa_s=-0.5,
                     richardson_number=0.48),   # MODERATE
        DerivedLevel(pressure_hpa=925, altitude_ft=2500, omega_pa_s=-0.5,
                     richardson_number=0.52),   # LIGHT
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
                     richardson_number=0.355),  # MODERATE
    ]
    assessment = assess_vertical_motion(levels)
    # BL: MODERATE(300-1800) + LIGHT(1800-2500) = 2 sub-layers
    # Upper: MODERATE(6200-10000; the Ri at 700 hPa describes the layer down
    # to the 800 hPa level below it) = 1 layer
    assert len(assessment.cat_risk_layers) == 3, (
        f"Expected 3 layers (2 BL sub-layers + 1 upper), got {len(assessment.cat_risk_layers)}"
    )
    # BL layers stay low
    assert assessment.cat_risk_layers[0].top_ft <= 2000
    assert assessment.cat_risk_layers[1].top_ft <= 3000
    # Upper layer is separate — it does not chain down into the BL cluster
    assert assessment.cat_risk_layers[2].base_ft == 6200
    assert assessment.cat_risk_layers[2].top_ft == 10000
    assert assessment.cat_risk_layers[2].base_ft > assessment.cat_risk_layers[1].top_ft


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


# --- Altitude-dependent Ri calibration (#533) ---


def test_ri_thresholds_classical_in_lower_troposphere():
    """Below 10,000 ft the classical 0.25/0.5/1.0 tiers apply unchanged.

    Ri = 0.35 at 2,500 ft used to read SEVERE under the flat 0.5/1.0/2.0
    calibration — the defect behind the EGNY→EGKB "severe CAT at 2,500 ft"
    reports. It is MODERATE now.
    """
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300),
        DerivedLevel(pressure_hpa=950, altitude_ft=1800, richardson_number=0.35),
        DerivedLevel(pressure_hpa=900, altitude_ft=3200, richardson_number=1.20),
    ]
    assessment = assess_vertical_motion(levels)
    risks = [la.risk for la in assessment.cat_risk_layers]
    # 0.35 → MODERATE (was SEVERE); 1.20 → NONE (was LIGHT)
    assert risks == [CATRiskLevel.MODERATE]


def test_ri_thresholds_loosened_in_upper_troposphere():
    """At/above 20,000 ft the tiers are the old loosened 0.5/1.0/2.0.

    The resolution argument genuinely holds up there: 25 hPa spans ~800 m at
    300 hPa, far coarser than the shear sheet it needs to resolve.
    """
    levels = [
        DerivedLevel(pressure_hpa=400, altitude_ft=24000),
        DerivedLevel(pressure_hpa=350, altitude_ft=26000, richardson_number=0.35),
        DerivedLevel(pressure_hpa=300, altitude_ft=30000, richardson_number=0.80),
    ]
    assessment = assess_vertical_motion(levels)
    risks = [la.risk for la in assessment.cat_risk_layers]
    # Both would be one tier lower under the classical tiers: 0.35 MODERATE,
    # 0.80 LIGHT.
    assert risks == [CATRiskLevel.SEVERE, CATRiskLevel.MODERATE]


def test_ri_threshold_scale_ramps_between_10k_and_20k():
    """The multiplier ramps rather than stepping — no cliff at a fixed level."""
    from weatherbrief.analysis.sounding.vertical_motion import _ri_threshold_scale

    assert _ri_threshold_scale(2500) == 1.0
    assert _ri_threshold_scale(10000) == 1.0
    assert _ri_threshold_scale(15000) == pytest.approx(1.5)
    assert _ri_threshold_scale(20000) == 2.0
    assert _ri_threshold_scale(35000) == 2.0


# --- Well-mixed boundary layer suppression (#533) ---


def _mixed_layer_profile(ri_at_2500: float) -> list[DerivedLevel]:
    """Daytime profile with a mixed layer topping out at 2,500 ft.

    Lapse rate on level *i* describes the layer *above* it, so the two
    surface-rooted layers here are at the dry adiabat (9.8 °C/km) and
    everything above 2,500 ft is stable.
    """
    return [
        DerivedLevel(pressure_hpa=1000, altitude_ft=500, lapse_rate_c_per_km=9.8),
        DerivedLevel(pressure_hpa=975, altitude_ft=1200, lapse_rate_c_per_km=9.8,
                     richardson_number=0.6),
        DerivedLevel(pressure_hpa=950, altitude_ft=2500, lapse_rate_c_per_km=4.0,
                     richardson_number=ri_at_2500),
        DerivedLevel(pressure_hpa=925, altitude_ft=3900, lapse_rate_c_per_km=6.0,
                     richardson_number=0.8),
        DerivedLevel(pressure_hpa=900, altitude_ft=5200, lapse_rate_c_per_km=6.0,
                     richardson_number=9.0),
    ]


def test_well_mixed_layer_suppresses_positive_ri_cat():
    """Small-*positive* Ri inside the daytime mixed layer is not CAT.

    The §8(c) guard only caught negative Ri at the two lowest levels; a
    3,000–4,000 ft deep mixed layer with Ri ≈ 0.2 sailed past it and produced
    "Severe CAT" at 2,500 ft on a CAVOK August afternoon (#533).
    """
    assessment = assess_vertical_motion(_mixed_layer_profile(ri_at_2500=0.2))

    assert assessment.mixed_layer_top_ft == 2500
    # Only the layer above the mixed-layer top survives — it spans from the
    # mixed-layer top to the level whose Ri describes it.
    assert [(la.base_ft, la.top_ft) for la in assessment.cat_risk_layers] == [(2500, 3900)]


def test_shear_above_mixed_layer_top_still_flagged():
    """Suppression stops at the mixed-layer top — no blanket low-altitude floor.

    A hard 5,000 ft floor would blind us to genuine low-level wind shear; the
    capping/entrainment layer just above the mixed layer is exactly where a
    frontal day puts it.
    """
    levels = _mixed_layer_profile(ri_at_2500=0.2)
    levels[3].richardson_number = 0.15  # sharp shear sheet in the 2,500–3,900 ft layer
    assessment = assess_vertical_motion(levels)

    assert len(assessment.cat_risk_layers) == 1
    assert assessment.cat_risk_layers[0].risk == CATRiskLevel.SEVERE
    # The layer spans from the mixed-layer top (suppression stops there) up to
    # the level carrying the low Ri.
    assert assessment.cat_risk_layers[0].base_ft == 2500
    assert assessment.cat_risk_layers[0].top_ft == 3900


def test_bl_ceiling_is_the_higher_of_mixed_layer_top_and_agl_fallback():
    """The BL ceiling is max(mixed-layer top, surface + 5,000 ft) — not either/or.

    Pins the #534 review decision. A shear sheet at 3,900 ft above a 2,500 ft
    mixed layer (surface 500 ft, so the fallback ceiling is 5,500 ft) is still
    tagged boundary-layer: it is most likely the mixed layer's own
    entrainment/capping shear, and reading it as free atmosphere would let a
    single route point RED the advisory again — the #533 failure mode.

    The tag is not a mute. Such a layer is still floored at AMBER and still
    REDs past the route-percentage threshold; see ``TestTurbulence``.
    """
    levels = _mixed_layer_profile(ri_at_2500=0.2)
    levels[3].richardson_number = 0.15  # 2,500–3,900 ft — above the mixed-layer top
    assessment = assess_vertical_motion(levels)

    assert assessment.mixed_layer_top_ft == 2500
    layer = assessment.cat_risk_layers[0]
    assert (layer.base_ft, layer.top_ft) == (2500, 3900)
    assert layer.boundary_layer is True


def test_layer_above_the_agl_fallback_is_free_atmosphere():
    """Above surface + 5,000 ft the tag is off, so severe keeps its RED bypass."""
    levels = _mixed_layer_profile(ri_at_2500=0.2)
    levels.append(DerivedLevel(pressure_hpa=850, altitude_ft=6200,
                               richardson_number=0.15))
    assessment = assess_vertical_motion(levels)

    # The 5,200–6,200 ft layer pokes above the 5,500 ft BL ceiling.
    by_top = {la.top_ft: la for la in assessment.cat_risk_layers}
    assert by_top[6200].boundary_layer is False


def test_no_mixed_layer_when_surface_layer_is_stable():
    """A stable surface layer means no mixed layer — nothing is suppressed."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=500, lapse_rate_c_per_km=4.0),
        DerivedLevel(pressure_hpa=975, altitude_ft=1200, lapse_rate_c_per_km=5.0,
                     richardson_number=0.2),
        DerivedLevel(pressure_hpa=950, altitude_ft=2500, lapse_rate_c_per_km=6.0,
                     richardson_number=9.0),
    ]
    assessment = assess_vertical_motion(levels)

    assert assessment.mixed_layer_top_ft is None
    assert len(assessment.cat_risk_layers) == 1
    assert assessment.cat_risk_layers[0].risk == CATRiskLevel.SEVERE


def test_mixed_layer_depth_is_capped():
    """A dry-adiabatic column is not treated as a 30,000 ft deep mixed layer."""
    levels = [
        DerivedLevel(pressure_hpa=1000 - 25 * i, altitude_ft=500 + 2000 * i,
                     lapse_rate_c_per_km=9.8,
                     richardson_number=(0.2 if i else None))
        for i in range(12)
    ]
    assessment = assess_vertical_motion(levels)

    assert assessment.mixed_layer_top_ft is not None
    assert assessment.mixed_layer_top_ft - 500 <= 10000


# --- Boundary-layer tagging (#533) ---


def test_boundary_layer_flag_set_for_low_layers():
    """Layers wholly inside the boundary layer are tagged for the advisory gate."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=500),
        DerivedLevel(pressure_hpa=950, altitude_ft=2500, richardson_number=0.2),
        DerivedLevel(pressure_hpa=700, altitude_ft=10000, richardson_number=0.2),
    ]
    assessment = assess_vertical_motion(levels)

    by_top = {la.top_ft: la for la in assessment.cat_risk_layers}
    assert by_top[2500].boundary_layer is True
    assert by_top[10000].boundary_layer is False


# --- CAT layer geometry: Ri describes the layer BELOW its level (#534) ---


def test_single_level_cat_layer_spans_the_layer_below():
    """A lone qualifying level yields a layer spanning its physical layer.

    Regression for the EGNY→EGKB miss: ICON point 8 stored Ri=0.24 on the
    925 hPa level at 2,523 ft, describing the 950→925 hPa layer
    (1,765–2,523 ft) that contains the 2,500 ft cruise. Building the layer
    from the flagged level's own altitude produced a zero-thickness band at
    2,523–2,523 ft, and the evaluator missed cruise by 23 ft — reading a
    still-sheared column as "smooth ride expected".
    """
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=292),
        DerivedLevel(pressure_hpa=950, altitude_ft=1765, richardson_number=2.0),
        DerivedLevel(pressure_hpa=925, altitude_ft=2523, richardson_number=0.24),
        DerivedLevel(pressure_hpa=900, altitude_ft=3296, richardson_number=5.0),
    ]
    assessment = assess_vertical_motion(levels)

    assert len(assessment.cat_risk_layers) == 1
    layer = assessment.cat_risk_layers[0]
    assert (layer.base_ft, layer.top_ft) == (1765, 2523)
    assert layer.risk == CATRiskLevel.SEVERE
    assert layer.base_ft < 2500 < layer.top_ft  # cruise inside the physical layer


def test_sparse_column_does_not_extend_base_across_wide_gap():
    """Across a >100 hPa gap the base is NOT extended down.

    Ri computed over such a slab is already dubious; extending the layer
    would paint a multi-thousand-ft band from a single number. The old
    upper-bound-only behaviour is kept there (mirrors the 100 hPa adjacency
    criterion used for grouping).
    """
    levels = [
        DerivedLevel(pressure_hpa=500, altitude_ft=18000, richardson_number=5.0),
        DerivedLevel(pressure_hpa=300, altitude_ft=30000, richardson_number=0.2),
    ]
    assessment = assess_vertical_motion(levels)

    assert len(assessment.cat_risk_layers) == 1
    assert assessment.cat_risk_layers[0].base_ft == 30000


# --- θv-parcel mixed-layer detection (#534 follow-up) ---


def _thetav_profile() -> list[DerivedLevel]:
    """Convective profile whose second layer has a mild sub-adiabatic kink
    (8.0 °C/km) inside an otherwise well-mixed 2,500 ft column, capped by an
    inversion. θv stays within 0.5 K of the surface value up to 2,500 ft."""
    return [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, temperature_c=25.0,
                     lapse_rate_c_per_km=9.9),
        DerivedLevel(pressure_hpa=975, altitude_ft=1000, temperature_c=22.9,
                     lapse_rate_c_per_km=8.0, richardson_number=0.3),
        DerivedLevel(pressure_hpa=950, altitude_ft=1750, temperature_c=21.07,
                     lapse_rate_c_per_km=9.8, richardson_number=0.2),
        DerivedLevel(pressure_hpa=925, altitude_ft=2500, temperature_c=18.83,
                     lapse_rate_c_per_km=-2.7, richardson_number=0.2),
        DerivedLevel(pressure_hpa=900, altitude_ft=3300, temperature_c=19.5,
                     richardson_number=0.15),
    ]


def test_theta_v_parcel_walks_through_a_lapse_kink():
    """The θv criterion integrates over the column; the per-layer lapse walk
    truncates at the first sub-8.8 layer.

    This is the #533-pack failure mode: one interpolation kink at the second
    level read a deep mixed layer as ~1,000 ft, leaving small-positive Ri
    inside it flagged as CAT. θv keeps walking (cumulative excess ≈0.41 K)
    and stops at the real inversion.
    """
    from weatherbrief.analysis.sounding.vertical_motion import (
        _mixed_layer_top_index_lapse,
        mixed_layer_top_index,
    )

    levels = _thetav_profile()
    assert mixed_layer_top_index(levels) == 3  # 2,500 ft — through the kink
    assert _mixed_layer_top_index_lapse(levels) == 1  # truncated at the kink


def test_theta_v_suppression_covers_the_kinked_mixed_layer():
    """CAT inside the θv-detected mixed layer is suppressed; the inversion
    shear above it survives (spanning up from the mixed-layer top)."""
    assessment = assess_vertical_motion(_thetav_profile())

    assert assessment.mixed_layer_top_ft == 2500
    assert [(la.base_ft, la.top_ft, la.risk) for la in assessment.cat_risk_layers] == [
        (2500, 3300, CATRiskLevel.SEVERE),
    ]


def test_theta_v_stable_surface_detects_no_mixed_layer():
    """θv climbing off the surface means no mixed layer — nothing suppressed."""
    from weatherbrief.analysis.sounding.vertical_motion import mixed_layer_top_index

    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=300, temperature_c=12.0),
        DerivedLevel(pressure_hpa=975, altitude_ft=1000, temperature_c=11.5),
    ]
    assert mixed_layer_top_index(levels) == 0


def test_lapse_fallback_used_when_temperatures_missing():
    """Without temperatures the θv criterion can't run; the lapse walk takes
    over so lapse-only profiles (older packs) keep their detection."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=500, lapse_rate_c_per_km=9.8),
        DerivedLevel(pressure_hpa=975, altitude_ft=1200, lapse_rate_c_per_km=9.8),
        DerivedLevel(pressure_hpa=950, altitude_ft=2500, lapse_rate_c_per_km=4.0),
        DerivedLevel(pressure_hpa=925, altitude_ft=3900),
    ]
    from weatherbrief.analysis.sounding.vertical_motion import mixed_layer_top_index

    assert mixed_layer_top_index(levels) == 2  # 2,500 ft via the lapse walk


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
