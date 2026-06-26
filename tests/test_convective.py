"""Tests for convective risk assessment (sounding/convective.py)."""

from weatherbrief.analysis.sounding.convective import (
    ConvectiveCrossCheck,
    _effective_cape,
    assess_convective_nwp,
    assess_convective_thermo,
    classify_regime,
    convection_realized,
    convective_cross_check,
    effective_cape,
)
from weatherbrief.models import (
    ConvectiveAssessment,
    ConvectiveRegime,
    ConvectiveRisk,
    NWPCloudDiagnostics,
    ThermodynamicIndices,
)


# ---------------------------------------------------------------------------
# _effective_cape
# ---------------------------------------------------------------------------


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


def test_effective_cape_ml_only():
    """Uses ML-CAPE when other variants are None (ICON model)."""
    indices = ThermodynamicIndices(cape_mixed_layer_jkg=600.0)
    assert _effective_cape(indices) == 600.0


def test_effective_cape_nwp_raw_fallback_only():
    """NWP raw CAPE is only used when no MetPy variant is available."""
    # When SB-CAPE exists, NWP should NOT override it even if higher
    indices = ThermodynamicIndices(
        cape_surface_jkg=200.0,
        nwp_cape_jkg=900.0,
    )
    assert effective_cape(indices) == 200.0

    # When no MetPy variants exist, NWP is used as fallback
    indices_nwp_only = ThermodynamicIndices(nwp_cape_jkg=900.0)
    assert effective_cape(indices_nwp_only) == 900.0


def test_effective_cape_all_metpy_variants():
    """Max across MetPy CAPE variants (NWP excluded from max pool)."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=100.0,
        cape_most_unstable_jkg=300.0,
        cape_mixed_layer_jkg=500.0,
        nwp_cape_jkg=400.0,  # ignored because MetPy variants exist
    )
    assert effective_cape(indices) == 500.0


def test_effective_cape_none():
    """Returns None when no CAPE is available."""
    assert _effective_cape(ThermodynamicIndices()) is None


# ---------------------------------------------------------------------------
# assess_convective_thermo
# ---------------------------------------------------------------------------


def test_eu_thresholds_moderate_at_300():
    """300 J/kg triggers MODERATE with European thresholds."""
    indices = ThermodynamicIndices(cape_surface_jkg=350.0)
    result = assess_convective_thermo(indices)
    assert result.risk_level == ConvectiveRisk.MODERATE


def test_eu_thresholds_high_at_1000():
    """1000 J/kg triggers HIGH with European thresholds."""
    indices = ThermodynamicIndices(cape_surface_jkg=1200.0)
    result = assess_convective_thermo(indices)
    assert result.risk_level == ConvectiveRisk.HIGH


def test_eu_thresholds_extreme_at_2000():
    """2000 J/kg triggers EXTREME with European thresholds."""
    indices = ThermodynamicIndices(cape_surface_jkg=2500.0)
    result = assess_convective_thermo(indices)
    assert result.risk_level == ConvectiveRisk.EXTREME


def test_elevated_convection_mu_cape():
    """MU-CAPE > SB-CAPE drives risk level (elevated convection scenario)."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=30.0,       # nearly zero SB-CAPE (marine BL)
        cape_most_unstable_jkg=800.0,  # warm advection aloft
    )
    result = assess_convective_thermo(indices)
    assert result.risk_level == ConvectiveRisk.MODERATE


def test_cin_suppression():
    """Strong CIN cap reduces risk by one level."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=1500.0,
        cin_surface_jkg=-250.0,
    )
    result = assess_convective_thermo(indices)
    # 1500 J/kg = HIGH with EU thresholds, CIN suppression → MODERATE
    assert result.risk_level == ConvectiveRisk.MODERATE


def test_no_cape_no_risk():
    """No CAPE → NONE risk."""
    indices = ThermodynamicIndices()
    result = assess_convective_thermo(indices)
    assert result.risk_level == ConvectiveRisk.NONE


def test_thermo_base_ft_from_lfc():
    """base_ft populated from lfc_altitude_ft when available."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=500.0,
        lcl_altitude_ft=3000.0,
        lfc_altitude_ft=4000.0,
        el_altitude_ft=30000.0,
    )
    result = assess_convective_thermo(indices)
    assert result.base_ft == 4000.0
    assert result.top_ft == 30000.0


def test_thermo_base_ft_falls_back_to_lcl():
    """base_ft falls back to lcl_altitude_ft when lfc not available."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=500.0,
        lcl_altitude_ft=3000.0,
        el_altitude_ft=20000.0,
    )
    result = assess_convective_thermo(indices)
    assert result.base_ft == 3000.0
    assert result.top_ft == 20000.0


def test_thermo_method_field():
    """method is 'thermo'."""
    result = assess_convective_thermo(ThermodynamicIndices(cape_surface_jkg=100.0))
    assert result.method == "thermo"
    assert result.cover_pct is None


# ---------------------------------------------------------------------------
# classify_regime
# ---------------------------------------------------------------------------


def test_classify_regime_thermal():
    """Low CAPE → THERMAL regardless of cap."""
    assert classify_regime(150.0, -5.0) is ConvectiveRegime.THERMAL


def test_classify_regime_weak_instability():
    """Moderate CAPE → WEAK_INSTABILITY."""
    assert classify_regime(600.0, -15.0) is ConvectiveRegime.WEAK_INSTABILITY


def test_classify_regime_loaded_gun():
    """High CAPE + significant cap → LOADED_GUN."""
    assert classify_regime(1200.0, -80.0) is ConvectiveRegime.LOADED_GUN


def test_classify_regime_active_weak_cap():
    """High CAPE + weak cap → ACTIVE."""
    assert classify_regime(1200.0, -10.0) is ConvectiveRegime.ACTIVE


def test_classify_regime_active_unknown_cap():
    """High CAPE with no CIN data → ACTIVE (not loaded gun)."""
    assert classify_regime(1200.0, None) is ConvectiveRegime.ACTIVE


def test_classify_regime_none_without_cape():
    """No CAPE → no regime."""
    assert classify_regime(None, -50.0) is None


# Boundary cases at the exact regime cutoffs (CAPE 300/800, CIN -50).
# Cutoffs are inclusive on the high side (cape >= 800, cin <= -50) and
# exclusive on the THERMAL side (cape < 300).


def test_classify_regime_cape_high_boundary_loaded_gun():
    """CAPE exactly at HIGH cutoff with cap at CIN cutoff → LOADED_GUN."""
    assert classify_regime(800.0, -50.0) is ConvectiveRegime.LOADED_GUN


def test_classify_regime_cin_just_weaker_than_cap_is_active():
    """CAPE high but cap just weaker than the CIN cutoff → ACTIVE, not LOADED_GUN."""
    assert classify_regime(800.0, -49.0) is ConvectiveRegime.ACTIVE


def test_classify_regime_cape_just_below_high_is_weak_instability():
    """CAPE one below the HIGH cutoff stays WEAK_INSTABILITY even under a strong cap."""
    assert classify_regime(799.0, -80.0) is ConvectiveRegime.WEAK_INSTABILITY


def test_classify_regime_cape_low_boundary_is_weak_instability():
    """CAPE exactly at the LOW cutoff is WEAK_INSTABILITY, not THERMAL."""
    assert classify_regime(300.0, -10.0) is ConvectiveRegime.WEAK_INSTABILITY


def test_classify_regime_cape_just_below_low_is_thermal():
    """CAPE one below the LOW cutoff is THERMAL."""
    assert classify_regime(299.0, -10.0) is ConvectiveRegime.THERMAL


def test_regime_label_is_title_case():
    """label renders snake_case enum values as human-readable title case."""
    assert ConvectiveRegime.LOADED_GUN.label == "Loaded Gun"
    assert ConvectiveRegime.WEAK_INSTABILITY.label == "Weak Instability"
    assert ConvectiveRegime.ACTIVE.label == "Active"


# ---------------------------------------------------------------------------
# regime-aware scoring + drivers/suppressors
# ---------------------------------------------------------------------------


def test_loaded_gun_no_omega_keeps_risk():
    """No ascent data → don't downgrade a moderate-cap loaded gun (honest note).

    An unassessable loaded gun is not safely "low risk", so with omega absent
    risk stays at the CAPE level and the note says so plainly.
    """
    indices = ThermodynamicIndices(cape_surface_jkg=1500.0, cin_surface_jkg=-80.0)
    result = assess_convective_thermo(indices, omega_700_pa_s=None)
    assert result.regime is ConvectiveRegime.LOADED_GUN
    assert result.risk_level == ConvectiveRisk.HIGH  # not downgraded on missing data
    assert any("no ascent data" in s for s in result.suppressors)


def test_loaded_gun_no_ascent_with_omega_suppressed():
    """Omega present but not ascending → cap likely holds → one level down."""
    indices = ThermodynamicIndices(cape_surface_jkg=1500.0, cin_surface_jkg=-80.0)
    result = assess_convective_thermo(indices, omega_700_pa_s=0.1)
    assert result.regime is ConvectiveRegime.LOADED_GUN
    assert result.risk_level == ConvectiveRisk.MODERATE  # HIGH suppressed one level
    assert any("initiation inhibited" in s for s in result.suppressors)


def test_loaded_gun_neutral_omega_suppressed():
    """Neutral omega (0.0) is 'present, not ascending' → suppressed like subsidence."""
    indices = ThermodynamicIndices(cape_surface_jkg=1500.0, cin_surface_jkg=-80.0)
    result = assess_convective_thermo(indices, omega_700_pa_s=0.0)
    assert result.risk_level == ConvectiveRisk.MODERATE


def test_loaded_gun_very_strong_cap_suppressed_without_omega():
    """A CIN < -200 cap holds regardless of ascent data → still suppressed."""
    indices = ThermodynamicIndices(cape_surface_jkg=1500.0, cin_surface_jkg=-250.0)
    result = assess_convective_thermo(indices, omega_700_pa_s=None)
    assert result.regime is ConvectiveRegime.LOADED_GUN
    assert result.risk_level == ConvectiveRisk.MODERATE
    assert any("strong cap" in s.lower() for s in result.suppressors)


def test_loaded_gun_with_ascent_keeps_risk():
    """Large-scale ascent can erode the cap — risk stays at the CAPE level."""
    indices = ThermodynamicIndices(cape_surface_jkg=1500.0, cin_surface_jkg=-80.0)
    result = assess_convective_thermo(indices, omega_700_pa_s=-0.3)
    assert result.regime is ConvectiveRegime.LOADED_GUN
    assert result.risk_level == ConvectiveRisk.HIGH
    # driver names both the ascent and the cap strength (CIN) it may erode
    assert any("ascent" in d and "CIN" in d for d in result.drivers)
    # the loaded-gun cap must not also be surfaced as a suppressor
    assert not any("cap" in s.lower() for s in result.suppressors)


def test_active_regime_keeps_risk():
    """High CAPE with a weak cap initiates readily — no suppression."""
    indices = ThermodynamicIndices(cape_surface_jkg=1500.0, cin_surface_jkg=-10.0)
    result = assess_convective_thermo(indices)
    assert result.regime is ConvectiveRegime.ACTIVE
    assert result.risk_level == ConvectiveRisk.HIGH
    assert result.drivers


def test_active_regime_subsidence_not_flagged():
    """ACTIVE intentionally doesn't note subsidence and never suppresses."""
    indices = ThermodynamicIndices(cape_surface_jkg=1500.0, cin_surface_jkg=-10.0)
    result = assess_convective_thermo(indices, omega_700_pa_s=0.2)
    assert result.regime is ConvectiveRegime.ACTIVE
    assert result.risk_level == ConvectiveRisk.HIGH
    assert not result.suppressors


def test_thermal_strong_cap_suppressed():
    """THERMAL regime: a strong cap (CIN < -200) drops risk one level."""
    indices = ThermodynamicIndices(cape_surface_jkg=120.0, cin_surface_jkg=-250.0)
    result = assess_convective_thermo(indices)
    assert result.regime is ConvectiveRegime.THERMAL
    assert result.risk_level == ConvectiveRisk.MARGINAL  # LOW suppressed once
    assert any("cap" in s.lower() for s in result.suppressors)


def test_weak_instability_strong_cap_suppressed():
    """WEAK_INSTABILITY regime: a strong cap (CIN < -200) drops risk one level."""
    indices = ThermodynamicIndices(cape_surface_jkg=600.0, cin_surface_jkg=-250.0)
    result = assess_convective_thermo(indices)
    assert result.regime is ConvectiveRegime.WEAK_INSTABILITY
    assert result.risk_level == ConvectiveRisk.LOW  # MODERATE suppressed once
    assert any("cap" in s.lower() for s in result.suppressors)


def test_weak_instability_subsidence_adds_suppressor():
    """Subsidence is noted as a suppressor but doesn't change the tier."""
    indices = ThermodynamicIndices(cape_surface_jkg=600.0)
    result = assess_convective_thermo(indices, omega_700_pa_s=0.1)
    assert result.regime is ConvectiveRegime.WEAK_INSTABILITY
    assert result.risk_level == ConvectiveRisk.MODERATE
    assert any("subsidence" in s for s in result.suppressors)


def test_thermal_regime_annotated():
    """Thermal regime is labelled and annotated even at low CAPE."""
    indices = ThermodynamicIndices(cape_surface_jkg=120.0)
    result = assess_convective_thermo(indices)
    assert result.regime is ConvectiveRegime.THERMAL
    assert result.risk_level == ConvectiveRisk.LOW  # 120 >= 50
    assert any("thermal" in d.lower() for d in result.drivers)


def test_regime_none_when_no_cape():
    """Empty sounding → no regime, no drivers/suppressors."""
    result = assess_convective_thermo(ThermodynamicIndices())
    assert result.regime is None
    assert result.drivers == []
    assert result.suppressors == []


# ---------------------------------------------------------------------------
# realizable (mixed-layer) tier scoring for non-loaded-gun regimes
# ---------------------------------------------------------------------------


def test_active_tempered_on_mixed_layer_cape():
    """ACTIVE: high potential but collapsed ML + no ascent → tempered one level.

    Mirrors the dry-CAPE Po Valley case (GFS-like): SB/MU 1125, ML 563, weak
    cap, neutral omega. Potential alone reads HIGH; realizable ML reads MODERATE.
    """
    indices = ThermodynamicIndices(
        cape_surface_jkg=1125.0,
        cape_most_unstable_jkg=1125.0,
        cape_mixed_layer_jkg=563.0,
        cin_surface_jkg=-42.0,
    )
    result = assess_convective_thermo(indices, omega_700_pa_s=0.0)
    assert result.regime is ConvectiveRegime.ACTIVE
    assert result.risk_level == ConvectiveRisk.MODERATE  # was HIGH on potential
    assert any("mixed-layer" in s.lower() for s in result.suppressors)


def test_active_floor_one_level_below_potential():
    """Safety floor: ML near zero cannot drop more than one level below potential."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=2500.0,
        cape_most_unstable_jkg=2500.0,
        cape_mixed_layer_jkg=10.0,  # ML alone would be NONE
        cin_surface_jkg=-10.0,
    )
    result = assess_convective_thermo(indices, omega_700_pa_s=0.0)
    assert result.regime is ConvectiveRegime.ACTIVE
    # Potential EXTREME → floored at HIGH (one level), not dragged to ML's NONE
    assert result.risk_level == ConvectiveRisk.HIGH


def test_active_ascent_keeps_potential_tier():
    """Large-scale ascent can realize the latent CAPE → keep the potential tier."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=1125.0,
        cape_most_unstable_jkg=1125.0,
        cape_mixed_layer_jkg=563.0,
        cin_surface_jkg=-42.0,
    )
    result = assess_convective_thermo(indices, omega_700_pa_s=-0.3)
    assert result.regime is ConvectiveRegime.ACTIVE
    assert result.risk_level == ConvectiveRisk.HIGH
    assert not any("mixed-layer" in s.lower() for s in result.suppressors)


def test_active_ml_absent_stays_conservative():
    """No ML-CAPE → cannot temper, stay on the potential tier (conservative)."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=1500.0,
        cape_most_unstable_jkg=1500.0,
        cin_surface_jkg=-10.0,
    )
    result = assess_convective_thermo(indices, omega_700_pa_s=0.0)
    assert result.regime is ConvectiveRegime.ACTIVE
    assert result.risk_level == ConvectiveRisk.HIGH


def test_weak_instability_not_tempered_on_ml():
    """WEAK_INSTABILITY is NOT ML-tempered (scoped to ACTIVE only) — stays on potential.

    ML-tempering applies only to the ACTIVE regime, to target the surface-CAPE
    false-HIGH case without softening the whole route by a level.
    """
    indices = ThermodynamicIndices(
        cape_surface_jkg=600.0,
        cape_most_unstable_jkg=600.0,
        cape_mixed_layer_jkg=60.0,  # low ML, but WEAK keeps the potential tier
    )
    result = assess_convective_thermo(indices, omega_700_pa_s=0.0)
    assert result.regime is ConvectiveRegime.WEAK_INSTABILITY
    assert result.risk_level == ConvectiveRisk.MODERATE  # potential 600, not ML-softened
    assert not any("mixed-layer" in s.lower() for s in result.suppressors)


def test_loaded_gun_not_tempered_by_low_ml():
    """LOADED_GUN scores on potential — a collapsed ML must not hide a capped gun."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=1500.0,
        cape_most_unstable_jkg=1500.0,
        cape_mixed_layer_jkg=50.0,
        cin_surface_jkg=-80.0,
    )
    result = assess_convective_thermo(indices, omega_700_pa_s=None)
    assert result.regime is ConvectiveRegime.LOADED_GUN
    assert result.risk_level == ConvectiveRisk.HIGH  # potential-scored, not ML
    assert not any("mixed-layer" in s.lower() for s in result.suppressors)


# ---------------------------------------------------------------------------
# elevated convection flag (MU ≫ SB)
# ---------------------------------------------------------------------------


def test_elevated_convection_flagged():
    """MU-CAPE well above SB-CAPE → elevated flag + driver."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=30.0,
        cape_most_unstable_jkg=800.0,
    )
    result = assess_convective_thermo(indices)
    assert result.elevated_convection is True
    assert any("elevated" in d.lower() for d in result.drivers)


def test_elevated_not_flagged_when_surface_rooted():
    """MU ≈ SB (surface-rooted) → no elevated flag."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=1100.0,
        cape_most_unstable_jkg=1125.0,
        cape_mixed_layer_jkg=560.0,
        cin_surface_jkg=-42.0,
    )
    result = assess_convective_thermo(indices, omega_700_pa_s=0.0)
    assert result.elevated_convection is False


# ---------------------------------------------------------------------------
# _omega_near_700 (large-scale ascent trigger extraction)
# ---------------------------------------------------------------------------


def _lvl(pressure_hpa, omega_pa_s):
    from weatherbrief.models import DerivedLevel
    return DerivedLevel(pressure_hpa=pressure_hpa, omega_pa_s=omega_pa_s)


def test_omega_near_700_picks_closest():
    """Picks the omega from the level nearest 700 hPa."""
    from weatherbrief.analysis.sounding import _omega_near_700
    levels = [_lvl(850, 0.05), _lvl(700, -0.20), _lvl(500, -0.40)]
    assert _omega_near_700(levels) == -0.20


def test_omega_near_700_none_when_no_omega():
    """None when no level carries omega."""
    from weatherbrief.analysis.sounding import _omega_near_700
    levels = [_lvl(850, None), _lvl(700, None)]
    assert _omega_near_700(levels) is None


def test_omega_near_700_accepts_within_100hpa():
    """A level within the 100 hPa window (e.g. 600) is accepted."""
    from weatherbrief.analysis.sounding import _omega_near_700
    levels = [_lvl(600, -0.15)]
    assert _omega_near_700(levels) == -0.15


def test_omega_near_700_rejects_when_too_far():
    """No level within 100 hPa of 700 → None (don't trust distant omega)."""
    from weatherbrief.analysis.sounding import _omega_near_700
    levels = [_lvl(500, -0.30), _lvl(300, -0.50)]
    assert _omega_near_700(levels) is None


def test_omega_reaches_trigger_through_core_pass():
    """Regression for the dead-trigger bug: omega must be populated by the
    *core* (lite) pass, since that is where the convective assessment runs.

    Builds a real profile via prepare_profile → compute_derived_levels_core
    (no extended pass) and confirms _omega_near_700 sees the 700 hPa ascent.
    """
    from weatherbrief.analysis.sounding import _omega_near_700
    from weatherbrief.analysis.sounding.prepare import prepare_profile
    from weatherbrief.analysis.sounding.thermodynamics import (
        compute_derived_levels_core,
    )
    from weatherbrief.models import PressureLevelData

    levels_in = [
        PressureLevelData(pressure_hpa=850, temperature_c=20, dewpoint_c=15,
                          geopotential_height_m=1450, vertical_velocity_pa_s=0.10),
        PressureLevelData(pressure_hpa=700, temperature_c=10, dewpoint_c=2,
                          geopotential_height_m=3010, vertical_velocity_pa_s=-0.40),
        PressureLevelData(pressure_hpa=500, temperature_c=-10, dewpoint_c=-25,
                          geopotential_height_m=5550, vertical_velocity_pa_s=-0.20),
    ]
    profile = prepare_profile(levels_in)
    assert profile is not None

    core_levels = compute_derived_levels_core(profile)
    assert any(lv.omega_pa_s is not None for lv in core_levels), (
        "omega must be set in the core pass, not only the extended pass"
    )
    assert _omega_near_700(core_levels) == -0.40  # 700 hPa ascent reaches the trigger


# ---------------------------------------------------------------------------
# assess_convective_nwp
# ---------------------------------------------------------------------------


def test_nwp_returns_none_when_no_diagnostics():
    """Returns None when nwp_diagnostics is None."""
    indices = ThermodynamicIndices(cape_surface_jkg=500.0)
    assert assess_convective_nwp(indices, None) is None


def test_nwp_cape_fallback_when_no_native_content():
    """Empty diagnostics (no native scheme output) → CAPE fallback, not None.

    A diag with no convective fields AND no native cloud content at all (a
    defensive/synthetic case — production build_* returns None for that) scores
    on CAPE like the DD track, marked ``nwp_cape_fallback`` so dd_nwp_agreement
    skips the circular comparison. (#283)
    """
    indices = ThermodynamicIndices(cape_surface_jkg=500.0)
    diag = NWPCloudDiagnostics()  # all None → no native cloud content
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.method == "nwp_cape_fallback"
    assert result.risk_level == ConvectiveRisk.MODERATE  # CAPE 500 → MODERATE
    assert result.cover_pct is None
    assert result.top_ft is None


def test_nwp_cape_fallback_cin_suppression():
    """CAPE fallback still applies strong-CIN suppression."""
    indices = ThermodynamicIndices(cape_surface_jkg=1500.0, cin_surface_jkg=-250.0)
    diag = NWPCloudDiagnostics()
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.method == "nwp_cape_fallback"
    assert result.risk_level == ConvectiveRisk.MODERATE  # HIGH → MODERATE


def test_nwp_quiet_native_returns_none_risk_not_none():
    """Native model, quiet convective scheme → NONE-risk assessment (not None).

    Cloud content present (total_cover) marks it native; no convective top/cover
    means the model is not producing convection here → NONE. Returned as a real
    assessment so dd_nwp_agreement can compare it against a HIGH DD track. (#283)
    """
    indices = ThermodynamicIndices(cape_surface_jkg=1500.0)  # DD would be HIGH
    diag = NWPCloudDiagnostics(total_cover_pct=40.0)  # native content, no convection
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.method != "nwp_cape_fallback"
    assert result.risk_level == ConvectiveRisk.NONE
    assert result.top_ft is None


def test_nwp_risk_from_native_top_not_cape():
    """Native path: risk from convective tower top, NOT CAPE. (#283)"""
    # Low CAPE but a deep model tower (FL300) + firing cover → HIGH from the top.
    indices = ThermodynamicIndices(cape_surface_jkg=40.0)
    diag = NWPCloudDiagnostics(convective_cover_pct=20.0, convective_top_ft=30000.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.HIGH  # FL300 >= 280
    assert result.cover_pct == 20.0  # preserved as context


def test_nwp_top_tiering():
    """Tower-top FL thresholds map to native risk tiers (firing cover, no gate)."""
    for top_ft, expected in [
        (40000.0, ConvectiveRisk.EXTREME),  # FL400 >= 380
        (30000.0, ConvectiveRisk.HIGH),     # FL300 >= 280
        (22000.0, ConvectiveRisk.MODERATE), # FL220 >= 200
        (15000.0, ConvectiveRisk.LOW),      # FL150 >= 120
        (8000.0, ConvectiveRisk.MARGINAL),  # FL80 present but shallow
    ]:
        indices = ThermodynamicIndices(cape_surface_jkg=40.0)
        # cover 20% (>15, <35): realized so the firing gate is a pass-through,
        # and below the cover-bump threshold so the top alone sets the tier.
        diag = NWPCloudDiagnostics(convective_cover_pct=20.0, convective_top_ft=top_ft)
        result = assess_convective_nwp(indices, diag)
        assert result is not None
        assert result.risk_level == expected, f"top={top_ft}: want {expected}, got {result.risk_level}"


def test_nwp_cover_modifier_bumps_one_level():
    """Numerous cover (>=35%) bumps the tower-top tier up one level, capped HIGH."""
    indices = ThermodynamicIndices(cape_surface_jkg=40.0)
    # FL220 (MODERATE) + 50% cover → HIGH.
    diag = NWPCloudDiagnostics(convective_cover_pct=50.0, convective_top_ft=22000.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.HIGH


def test_nwp_cover_modifier_capped_at_high():
    """Cover bump never creates EXTREME — that needs a >=FL380 tower."""
    indices = ThermodynamicIndices(cape_surface_jkg=40.0)
    # FL300 (HIGH) + 60% cover → stays HIGH (no bump past HIGH).
    diag = NWPCloudDiagnostics(convective_cover_pct=60.0, convective_top_ft=30000.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.HIGH


def test_nwp_cover_only_scale_when_no_top():
    """Cover present but no tower top → depth-unknown scale, capped MODERATE."""
    indices = ThermodynamicIndices(cape_surface_jkg=40.0)
    for cover, expected in [
        (80.0, ConvectiveRisk.MODERATE),  # widespread
        (50.0, ConvectiveRisk.LOW),       # numerous
        (20.0, ConvectiveRisk.MARGINAL),  # scattered
        (5.0, ConvectiveRisk.NONE),       # isolated
        (0.0, ConvectiveRisk.NONE),       # quiet
    ]:
        diag = NWPCloudDiagnostics(convective_cover_pct=cover)
        result = assess_convective_nwp(indices, diag)
        assert result is not None
        assert result.risk_level == expected, f"cover={cover}: want {expected}, got {result.risk_level}"


def test_nwp_precip_fired_tiering_when_no_geometry():
    """§14 Phase 3: convective precip with no tower top / cover → rate-based tier.

    Depth unknown from rate alone, so the ladder is capped at MODERATE.
    """
    for precip, expected in [
        (4.26, ConvectiveRisk.MODERATE),   # active shower core (>=2.0)
        (2.0, ConvectiveRisk.MODERATE),
        (1.24, ConvectiveRisk.LOW),        # light showers (>=0.5)
        (0.5, ConvectiveRisk.LOW),
        (0.45, ConvectiveRisk.MARGINAL),   # isolated/weak (>=0.1)
        (0.1, ConvectiveRisk.NONE),        # at/below the firing gate (> 0.1 required)
        (0.0, ConvectiveRisk.NONE),
    ]:
        indices = ThermodynamicIndices(cape_surface_jkg=40.0)
        # total_cover marks it native; no convective top, no convective cover.
        diag = NWPCloudDiagnostics(
            total_cover_pct=70.0, convective_precip_mm_h=precip
        )
        result = assess_convective_nwp(indices, diag)
        assert result is not None
        assert result.risk_level == expected, (
            f"precip={precip}: want {expected}, got {result.risk_level}"
        )
        if precip > 0.1:
            assert result.method == "nwp_precip"


def test_nwp_ecmwf_channel_precip_fires_when_hcct_absent():
    """Regression: ECMWF over the Channel — cp 4.26 mm/h, hcct sentinel (no top),
    no convective-cover field → MODERATE via the precip rate, not a false NONE.

    EGTF→LFAT 2026-06-27 @ ~52 nm: elevated marine convection. Surface parcel has
    no CAPE (instability is elevated), so DD is quiet, but the model's own scheme
    is precipitating convectively — Windy shows it; we used to read NONE.
    """
    indices = ThermodynamicIndices(cape_surface_jkg=0.0, cin_surface_jkg=0.0)
    diag = NWPCloudDiagnostics(
        total_cover_pct=81.0,          # native cloud content (not the CAPE fallback)
        convective_precip_mm_h=4.26,   # ECMWF `cp`, de-accumulated
        # convective_top_ft / convective_cover_pct absent (hcct sentinel, no cc field)
    )
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.method == "nwp_precip"
    assert result.risk_level == ConvectiveRisk.MODERATE
    assert result.convective_precip_mm_h == 4.26


def test_nwp_precip_not_suppressed_by_strong_cin():
    """§14 Phase 3: CIN suppression must NOT downgrade a firing nwp_precip cell.

    `cp` proves the scheme fired; penalising it on surface/ML CIN is circular (the
    parameterisation already overcame the inhibition at the elevated triggering
    level). Both a strong negative ML-CIN and a strong negative surface CIN must
    leave the precip-derived tier intact.
    """
    # Strong negative ML-CIN — preferred over surface CIN — must not suppress.
    indices = ThermodynamicIndices(cape_surface_jkg=0.0, cin_surface_jkg=0.0)
    diag = NWPCloudDiagnostics(
        total_cover_pct=70.0, convective_precip_mm_h=4.26, ml_cin_jkg=-300.0
    )
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.method == "nwp_precip"
    assert result.risk_level == ConvectiveRisk.MODERATE  # not knocked to LOW

    # Strong negative surface CIN (no ML-CIN) — also must not suppress.
    indices2 = ThermodynamicIndices(cape_surface_jkg=0.0, cin_surface_jkg=-300.0)
    diag2 = NWPCloudDiagnostics(total_cover_pct=70.0, convective_precip_mm_h=4.26)
    result2 = assess_convective_nwp(indices2, diag2)
    assert result2 is not None
    assert result2.risk_level == ConvectiveRisk.MODERATE


def test_nwp_precip_not_used_when_tower_top_present():
    """Tower top stays primary: a precip rate never overrides a diagnosed top."""
    indices = ThermodynamicIndices(cape_surface_jkg=40.0, lcl_altitude_ft=3000.0)
    # ECMWF hcct present (FL150 → LOW) with light precip; top wins, method nwp_lcl_top.
    diag = NWPCloudDiagnostics(convective_top_ft=15000.0, convective_precip_mm_h=4.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.method == "nwp_lcl_top"
    assert result.risk_level == ConvectiveRisk.LOW


def test_nwp_gfs_sun_reims_high():
    """Regression (#283): GFS Sun LFQA — cover 46.8%, top FL332 → HIGH.

    GFS's own scheme is firing in the morning (matches Windy). Uses GFS's own
    indices (its CIN is weaker than ECMWF's loaded-gun −360, which belongs to a
    different model), so no cap suppression applies and the deep firing tower
    reads HIGH.
    """
    indices = ThermodynamicIndices(cape_surface_jkg=1225.0, cin_surface_jkg=-50.0)
    diag = NWPCloudDiagnostics(convective_cover_pct=46.8, convective_top_ft=33200.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.HIGH


def test_nwp_gfs_sat_reims_none():
    """Regression (#283): GFS Sat LFQA — cover 0%, no top → NONE."""
    indices = ThermodynamicIndices(cape_surface_jkg=2006.0, cin_surface_jkg=-104.0)
    diag = NWPCloudDiagnostics(convective_cover_pct=0.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.NONE


def test_nwp_ecmwf_sun_morning_capped_none_while_dd_high():
    """Regression (#283): ECMWF Sun morning — hcct sentinel (no top), native
    cloud content present → NONE, while DD reads HIGH → dd_nwp_agreement fires."""
    indices = ThermodynamicIndices(cape_surface_jkg=1225.0, cin_surface_jkg=-360.0)
    # hcct sentinel decodes convective_top_ft to None; ECMWF still emits cover/
    # ceiling content (here total_cover) so it is recognised as native.
    diag = NWPCloudDiagnostics(total_cover_pct=30.0)
    nwp = assess_convective_nwp(indices, diag)
    dd = assess_convective_thermo(indices)
    assert nwp is not None and dd is not None
    assert nwp.risk_level == ConvectiveRisk.NONE
    assert dd.risk_level in (ConvectiveRisk.HIGH, ConvectiveRisk.MODERATE)
    # The two independent tracks diverge by >= 2 tiers → comparison is meaningful.
    order = list(ConvectiveRisk)
    assert abs(order.index(dd.risk_level) - order.index(nwp.risk_level)) >= 2
    assert nwp.method != "nwp_cape_fallback"


def test_nwp_icon_shallow_cu_marginal():
    """Regression (#283): ICON shallow Cu FL111 → MARGINAL (not a storm)."""
    indices = ThermodynamicIndices(cape_surface_jkg=300.0)
    diag = NWPCloudDiagnostics(convective_base_ft=4000.0, convective_top_ft=11100.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.method == "nwp_hybrid"
    assert result.risk_level == ConvectiveRisk.MARGINAL


def test_nwp_preserves_thermo_indices():
    """NWP assessment preserves thermodynamic indices for context."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=500.0,
        k_index=38.0,
        total_totals=57.0,
        bulk_shear_0_6km_kt=30.0,
    )
    diag = NWPCloudDiagnostics(convective_cover_pct=60.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.cape_jkg == 500.0
    assert result.k_index == 38.0
    assert result.total_totals == 57.0
    assert result.bulk_shear_0_6km_kt == 30.0


def test_nwp_method_field():
    """method is 'nwp' and cover_pct is populated."""
    indices = ThermodynamicIndices(cape_surface_jkg=500.0)
    diag = NWPCloudDiagnostics(
        convective_cover_pct=40.0,
        convective_base_ft=2000.0,
        convective_top_ft=35000.0,
    )
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.method == "nwp"
    assert result.cover_pct == 40.0
    assert result.base_ft == 2000.0
    assert result.top_ft == 35000.0


def test_nwp_severity_modifiers():
    """NWP assessment computes severity modifiers from thermo indices."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=1500.0,
        bulk_shear_0_6km_kt=45.0,  # > 40 → strong shear
        k_index=38.0,  # > 35 → thunderstorm potential
    )
    diag = NWPCloudDiagnostics(convective_cover_pct=60.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert any("strong shear" in m for m in result.severe_modifiers)
    assert any("K-index" in m for m in result.severe_modifiers)


# ---------------------------------------------------------------------------
# assess_convective_nwp — hybrid path (no cover_pct, has base/top)
# ---------------------------------------------------------------------------


def test_nwp_hybrid_uses_native_top_risk():
    """Hybrid path (#283): risk from the native tower top, not CAPE."""
    indices = ThermodynamicIndices(cape_surface_jkg=500.0)
    diag = NWPCloudDiagnostics(
        convective_base_ft=5000.0,
        convective_top_ft=35000.0,
    )
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.method == "nwp_hybrid"
    assert result.risk_level == ConvectiveRisk.HIGH  # FL350 >= 280
    assert result.base_ft == 5000.0
    assert result.top_ft == 35000.0
    assert result.cover_pct is None


def test_nwp_hybrid_shallow_top_marginal():
    """Hybrid path: a shallow tower (FL150) → LOW regardless of CAPE."""
    indices = ThermodynamicIndices(cape_surface_jkg=20.0)
    diag = NWPCloudDiagnostics(
        convective_base_ft=3000.0,
        convective_top_ft=15000.0,
    )
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.LOW  # FL150 >= 120


def test_nwp_hybrid_top_drives_risk_without_cape():
    """Hybrid path: native top drives risk even with no CAPE data (#283)."""
    indices = ThermodynamicIndices()
    diag = NWPCloudDiagnostics(
        convective_base_ft=3000.0,
        convective_top_ft=25000.0,
    )
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.MODERATE  # FL250 >= 200
    assert result.method == "nwp_hybrid"


def test_nwp_base_only_quiet_none_risk():
    """Base present but no top and no cover → quiet native NONE assessment."""
    indices = ThermodynamicIndices(cape_surface_jkg=500.0)
    diag = NWPCloudDiagnostics(convective_base_ft=5000.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.NONE
    assert result.top_ft is None
    assert result.method != "nwp_cape_fallback"


def test_nwp_hybrid_preserves_modifiers():
    """Hybrid path computes severity modifiers from thermo indices."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=1500.0,
        bulk_shear_0_6km_kt=45.0,
    )
    diag = NWPCloudDiagnostics(
        convective_base_ft=4000.0,
        convective_top_ft=40000.0,
    )
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert any("strong shear" in m for m in result.severe_modifiers)


def test_nwp_full_path_preferred_over_hybrid():
    """When cover_pct is present, full NWP path is used even if base/top exist."""
    indices = ThermodynamicIndices(cape_surface_jkg=2000.0)
    diag = NWPCloudDiagnostics(
        convective_cover_pct=30.0,
        convective_base_ft=5000.0,
        convective_top_ft=35000.0,
    )
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.method == "nwp"  # not "nwp_hybrid"
    assert result.cover_pct == 30.0


def test_nwp_lcl_top_uses_lcl_as_base():
    """LCL-anchored path (ECMWF hcct): base=LCL, risk from native top (#283)."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=800.0,
        lcl_altitude_ft=3500.0,
    )
    diag = NWPCloudDiagnostics(convective_top_ft=28000.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.method == "nwp_lcl_top"
    assert result.base_ft == 3500.0
    assert result.top_ft == 28000.0
    assert result.risk_level == ConvectiveRisk.HIGH  # FL280 >= 280


def test_nwp_lcl_top_keeps_tower_when_lcl_none():
    """ECMWF hcct top with LCL unavailable still keeps the tower (#283 review Imp3).

    The top > LCL artefact guard must not silently discard a valid tower when
    LCL failed to compute (e.g. a very dry profile) — that would be a safety-
    relevant under-warning. Base is then unknown (None); the tower drives risk.
    """
    indices = ThermodynamicIndices(cape_surface_jkg=40.0)  # no lcl_altitude_ft
    diag = NWPCloudDiagnostics(convective_top_ft=28000.0)  # FL280
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.method == "nwp_lcl_top"
    assert result.top_ft == 28000.0
    assert result.base_ft is None
    assert result.risk_level == ConvectiveRisk.HIGH  # FL280 >= 280


def test_nwp_lcl_top_quiet_when_hcct_below_lcl():
    """LCL-anchored guard: a sub-LCL hcct artefact → quiet NONE (top dropped)."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=500.0,
        lcl_altitude_ft=8000.0,
    )
    diag = NWPCloudDiagnostics(convective_top_ft=6000.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.NONE
    assert result.top_ft is None
    assert result.method != "nwp_cape_fallback"


def test_nwp_cin_suppression():
    """Strong CIN cap reduces the native NWP risk by one level."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=1500.0,
        cin_surface_jkg=-250.0,   # strong cap
    )
    # FL300 tower + firing cover (20%) → HIGH; strong cap suppresses → MODERATE.
    diag = NWPCloudDiagnostics(convective_cover_pct=20.0, convective_top_ft=30000.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.MODERATE  # HIGH → MODERATE


# ---------------------------------------------------------------------------
# Phase 2 firing gate + native corroboration (#283)
# ---------------------------------------------------------------------------


def test_nwp_firing_gate_holds_dry_tower_down():
    """Deep tower but the scheme is dry (low cover) → held down one level."""
    indices = ThermodynamicIndices(cape_surface_jkg=40.0)
    # FL300 (HIGH) + cover 5% (≤15, not firing) → held down to MODERATE.
    diag = NWPCloudDiagnostics(convective_cover_pct=5.0, convective_top_ft=30000.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.MODERATE
    assert any("dry" in s for s in result.suppressors)


def test_nwp_firing_gate_holds_zero_precip_tower_down():
    """Deep tower with explicit ~0 convective precip → held down one level."""
    indices = ThermodynamicIndices(cape_surface_jkg=40.0)
    diag = NWPCloudDiagnostics(
        convective_base_ft=4000.0, convective_top_ft=30000.0,
        convective_precip_mm_h=0.0,
    )
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.MODERATE  # HIGH → held down


def test_nwp_firing_gate_missing_data_keeps_tier():
    """No cover and no precip (e.g. ICON deep tower, no rain_con) → keep tier.

    Missing data must never hold down — safety asymmetry. (#283)
    """
    indices = ThermodynamicIndices(cape_surface_jkg=40.0)
    diag = NWPCloudDiagnostics(convective_base_ft=4000.0, convective_top_ft=30000.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.HIGH  # FL300, not held down
    assert not any("dry" in s for s in result.suppressors)


def test_nwp_firing_gate_realized_by_precip_keeps_tier():
    """Convective precip above the firing gate → realized → keep the tier."""
    indices = ThermodynamicIndices(cape_surface_jkg=40.0)
    diag = NWPCloudDiagnostics(
        convective_base_ft=4000.0, convective_top_ft=30000.0,
        convective_precip_mm_h=1.5,
    )
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.HIGH


def test_nwp_corroboration_bumps_realized_cell():
    """A realized MODERATE cell with strong native K-index bumps to HIGH."""
    indices = ThermodynamicIndices(cape_surface_jkg=40.0)
    # FL220 (MODERATE) + firing precip + K-index 40 → corroborated up to HIGH.
    diag = NWPCloudDiagnostics(
        convective_base_ft=4000.0, convective_top_ft=22000.0,
        convective_precip_mm_h=1.0, k_index=40.0,
    )
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.HIGH
    assert any("corroborated" in d for d in result.drivers)


def test_nwp_corroboration_capped_at_high():
    """Corroboration never creates EXTREME — that needs a >=FL380 tower."""
    indices = ThermodynamicIndices(cape_surface_jkg=40.0)
    diag = NWPCloudDiagnostics(
        convective_cover_pct=50.0, convective_top_ft=30000.0,  # FL300 HIGH, firing
        total_totals=60.0,
    )
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.HIGH  # not bumped past HIGH


def test_nwp_firing_gate_and_cin_double_suppression():
    """Dry-firing deep tower under a strong cap drops two levels (gate + CIN).

    The firing gate and CIN suppression are independent physical suppressors
    (not firing AND capped), so both applying is intentional and bounded at
    NONE. (#283 review M3)
    """
    indices = ThermodynamicIndices(cape_surface_jkg=40.0, cin_surface_jkg=-250.0)
    # FL300 (HIGH) + cover 5% (dry → gate → MODERATE) + CIN -250 (→ CIN → LOW).
    diag = NWPCloudDiagnostics(convective_cover_pct=5.0, convective_top_ft=30000.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.LOW


def test_nwp_corroboration_only_on_realized_cell():
    """Strong native index on a DRY tower does not bump (gate holds down instead)."""
    indices = ThermodynamicIndices(cape_surface_jkg=40.0)
    # FL220 (MODERATE) dry (cover 5%) + K-index 40: not realized → gate holds
    # down to LOW; corroboration does not apply to an unrealized cell.
    diag = NWPCloudDiagnostics(
        convective_cover_pct=5.0, convective_top_ft=22000.0, k_index=40.0,
    )
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.LOW


# ---------------------------------------------------------------------------
# _resolve_analyses with convective_method
# ---------------------------------------------------------------------------


def test_resolve_analyses_convective_swaps_to_nwp():
    """convective_method='nwp' swaps active convective slot to NWP."""
    from weatherbrief.models import RoutePointAnalysis, SoundingAnalysis
    from weatherbrief.tasks.advise import _resolve_analyses

    thermo = ConvectiveAssessment(
        risk_level=ConvectiveRisk.NONE, method="thermo",
    )
    nwp = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, method="nwp", cover_pct=55.0,
    )
    sounding = SoundingAnalysis(
        convective=thermo,
        convective_thermo=thermo,
        convective_nwp=nwp,
    )
    rpa = RoutePointAnalysis(
        point_index=0, lat=51.0, lon=-1.0, distance_from_origin_nm=0.0,
        interpolated_time="2024-01-01T12:00:00Z",
        forecast_hour="2024-01-01T12:00:00Z",
        track_deg=90.0,
        sounding={"gfs": sounding},
    )

    resolved = _resolve_analyses([rpa], None, None, convective_method="nwp")
    assert resolved[0].sounding["gfs"].convective.method == "nwp"
    assert resolved[0].sounding["gfs"].convective.risk_level == ConvectiveRisk.MODERATE


def test_resolve_analyses_convective_fallback_to_thermo():
    """convective_method='nwp' falls back to thermo when NWP unavailable."""
    from weatherbrief.models import RoutePointAnalysis, SoundingAnalysis
    from weatherbrief.tasks.advise import _resolve_analyses

    thermo = ConvectiveAssessment(
        risk_level=ConvectiveRisk.LOW, method="thermo",
    )
    sounding = SoundingAnalysis(
        convective=thermo,
        convective_thermo=thermo,
        convective_nwp=None,
    )
    rpa = RoutePointAnalysis(
        point_index=0, lat=51.0, lon=-1.0, distance_from_origin_nm=0.0,
        interpolated_time="2024-01-01T12:00:00Z",
        forecast_hour="2024-01-01T12:00:00Z",
        track_deg=90.0,
        sounding={"gfs": sounding},
    )

    resolved = _resolve_analyses([rpa], None, None, convective_method="nwp")
    assert resolved[0].sounding["gfs"].convective.method == "thermo"
    assert resolved[0].sounding["gfs"].convective.risk_level == ConvectiveRisk.LOW


def test_resolve_analyses_no_swap_when_thermo():
    """convective_method='thermo' doesn't trigger a swap."""
    from weatherbrief.models import RoutePointAnalysis, SoundingAnalysis
    from weatherbrief.tasks.advise import _resolve_analyses

    thermo = ConvectiveAssessment(
        risk_level=ConvectiveRisk.LOW, method="thermo",
    )
    sounding = SoundingAnalysis(
        convective=thermo,
        convective_thermo=thermo,
    )
    rpa = RoutePointAnalysis(
        point_index=0, lat=51.0, lon=-1.0, distance_from_origin_nm=0.0,
        interpolated_time="2024-01-01T12:00:00Z",
        forecast_hour="2024-01-01T12:00:00Z",
        track_deg=90.0,
        sounding={"gfs": sounding},
    )

    resolved = _resolve_analyses([rpa], None, None, convective_method="thermo")
    # Should return the same objects (no copy needed)
    assert resolved is [rpa] or resolved[0].sounding["gfs"].convective is thermo


# ---------------------------------------------------------------------------
# SoundingAnalysis backward compat validator
# ---------------------------------------------------------------------------


def test_sounding_analysis_backward_compat_convective_thermo():
    """Loading old JSON without convective_thermo auto-populates it from convective."""
    from weatherbrief.models import SoundingAnalysis

    conv = ConvectiveAssessment(risk_level=ConvectiveRisk.LOW, method="thermo")
    sa = SoundingAnalysis(convective=conv)
    assert sa.convective_thermo is conv


# ---------------------------------------------------------------------------
# convective_cross_check — DD-vs-model-scheme divergence (details-only)
# ---------------------------------------------------------------------------


def test_cross_check_dd_not_corroborated():
    """Thermo MODERATE + model cover 0% (no geom) → dd_not_corroborated."""
    thermo = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, cape_jkg=1100.0, method="thermo"
    )
    nwp = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, cover_pct=0.0, method="nwp"
    )
    xc = convective_cross_check(thermo, nwp)
    assert isinstance(xc, ConvectiveCrossCheck)
    assert xc.direction == "dd_not_corroborated"
    assert "not corroborated" in xc.note
    assert "MODERATE" in xc.note
    assert "1100" in xc.note
    assert "0%" in xc.note


def test_cross_check_model_active_dd_quiet():
    """Thermo NONE + model fired (cover 40%) → model_active_dd_quiet."""
    thermo = ConvectiveAssessment(risk_level=ConvectiveRisk.NONE, method="thermo")
    nwp = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, cover_pct=40.0, method="nwp"
    )
    xc = convective_cross_check(thermo, nwp)
    assert xc is not None
    assert xc.direction == "model_active_dd_quiet"
    assert "fired" in xc.note
    assert "40% cover" in xc.note


def test_cross_check_dd_not_corroborated_trace_precip_note():
    """Note distinguishes trace-but-present precip from absent precip (#283 M1)."""
    thermo = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, cape_jkg=1100.0, method="thermo"
    )
    # Quiet: no cover, but precip present and <= the firing gate (trace).
    nwp = ConvectiveAssessment(
        risk_level=ConvectiveRisk.NONE, cover_pct=None,
        convective_precip_mm_h=0.05, method="nwp_lcl_top",
    )
    xc = convective_cross_check(thermo, nwp)
    assert xc is not None
    assert xc.direction == "dd_not_corroborated"
    assert "conv precip" in xc.note
    assert "no convective precip/cover" not in xc.note


def test_cross_check_precip_fired():
    """Thermo NONE + model convective precip → model_active_dd_quiet (#283)."""
    thermo = ConvectiveAssessment(risk_level=ConvectiveRisk.NONE, method="thermo")
    nwp = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE,
        cover_pct=None,
        convective_precip_mm_h=1.2,
        method="nwp_lcl_top",
    )
    xc = convective_cross_check(thermo, nwp)
    assert xc is not None
    assert xc.direction == "model_active_dd_quiet"
    assert "mm/h" in xc.note


def test_cross_check_deep_tower_active():
    """Deep tower (>=FL200) counts as fired; a shallow Cu top does NOT (#283)."""
    thermo = ConvectiveAssessment(risk_level=ConvectiveRisk.MARGINAL, method="thermo")
    # Deep tower FL250 → active.
    deep = ConvectiveAssessment(
        risk_level=ConvectiveRisk.HIGH, cover_pct=None,
        base_ft=4000.0, top_ft=25000.0, method="nwp_hybrid",
    )
    xc = convective_cross_check(thermo, deep)
    assert xc is not None
    assert xc.direction == "model_active_dd_quiet"
    assert "FL250" in xc.note

    # Shallow Cu (FL90) → bare geometry, NOT active → no spurious cross-check.
    shallow = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MARGINAL, cover_pct=None,
        base_ft=3000.0, top_ft=9000.0, method="nwp_hybrid",
    )
    assert convective_cross_check(thermo, shallow) is None


def test_cross_check_none_when_nwp_missing():
    """No NWP convective scheme → silent (None)."""
    thermo = ConvectiveAssessment(
        risk_level=ConvectiveRisk.HIGH, cape_jkg=1500.0, method="thermo"
    )
    assert convective_cross_check(thermo, None) is None


def test_cross_check_none_when_agree_both_active():
    """Thermo MODERATE + model active (60% cover) → both agree, None."""
    thermo = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, cape_jkg=900.0, method="thermo"
    )
    nwp = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, cover_pct=60.0, method="nwp"
    )
    assert convective_cross_check(thermo, nwp) is None


def test_cross_check_none_when_agree_both_quiet():
    """Thermo NONE + model quiet (0% cover) → both agree, None."""
    thermo = ConvectiveAssessment(risk_level=ConvectiveRisk.NONE, method="thermo")
    nwp = ConvectiveAssessment(
        risk_level=ConvectiveRisk.NONE, cover_pct=0.0, method="nwp"
    )
    assert convective_cross_check(thermo, nwp) is None


def test_cross_check_cover_with_geom_not_quiet():
    """Cover 0% but convective geometry present → not 'quiet', so no false signal."""
    thermo = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, cape_jkg=1000.0, method="thermo"
    )
    # cover 0 but base/top present → model_has_geom True → model_quiet False
    nwp = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE,
        cover_pct=0.0,
        base_ft=4000.0,
        top_ft=18000.0,
        method="nwp",
    )
    assert convective_cross_check(thermo, nwp) is None


def test_cross_check_skips_cape_fallback():
    """No cross-check when NWP is the CAPE fallback — comparison is circular (#283 review I1).

    A diagnostic carrying only stability indices (no cloud content) reaches the
    fallback path with all firing channels absent, which would otherwise read
    model_quiet and spuriously fire dd_not_corroborated against a MODERATE+ DD.
    """
    thermo = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, cape_jkg=1100.0, method="thermo"
    )
    nwp = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, cover_pct=None, top_ft=None,
        method="nwp_cape_fallback",
    )
    assert convective_cross_check(thermo, nwp) is None


def test_cross_check_none_when_thermo_missing():
    """No thermo assessment → nothing to compare against (None)."""
    nwp = ConvectiveAssessment(
        risk_level=ConvectiveRisk.NONE, cover_pct=40.0, method="nwp"
    )
    assert convective_cross_check(None, nwp) is None


# ---------------------------------------------------------------------------
# convection_realized — the realized-vs-potential gate shared with front
# co-location (#216). Tier-independent: stricter CIN bar than risk_level.
# ---------------------------------------------------------------------------


def test_realized_nwp_method_always_true():
    """NWP convective cloud (method != thermo) is realized regardless of CIN."""
    assert convection_realized(
        method="nwp", cin_jkg=-300.0, ml_cape_jkg=10.0, lifted_index=5.0
    ) is True


def test_realized_weak_cap_is_true():
    """Weak inhibition (CIN > -50) → realized even with low instability."""
    assert convection_realized(
        method="thermo", cin_jkg=-20.0, ml_cape_jkg=50.0, lifted_index=1.0
    ) is True


def test_potential_strong_cap_low_instability_is_false():
    """The LSGS case: strong cap (CIN -59.5), ML-CAPE 147 < 300, LI -1 > -2 →
    potential, not realized."""
    assert convection_realized(
        method="thermo", cin_jkg=-59.5, ml_cape_jkg=147.0, lifted_index=-1.0
    ) is False


def test_realized_strong_cap_high_ml_cape():
    """Loaded gun: strong cap but ML-CAPE >= 300 → realized."""
    assert convection_realized(
        method="thermo", cin_jkg=-80.0, ml_cape_jkg=1200.0, lifted_index=None
    ) is True


def test_realized_strong_cap_negative_li():
    """Strong cap but LI <= -2 → realized (symmetric to the ML-CAPE gate)."""
    assert convection_realized(
        method="thermo", cin_jkg=-80.0, ml_cape_jkg=120.0, lifted_index=-4.0
    ) is True


def test_strong_cap_no_instability_data_defaults_realized():
    """Strong cap but ML-CAPE and LI both unknown (e.g. ICON, no lifted index) →
    default to realized rather than silently hide a front."""
    assert convection_realized(
        method="thermo", cin_jkg=-90.0, ml_cape_jkg=None, lifted_index=None
    ) is True


def test_unknown_cin_is_realized():
    """No CIN signal at all → not 'strongly capped' → realized."""
    assert convection_realized(
        method="thermo", cin_jkg=None, ml_cape_jkg=None, lifted_index=None
    ) is True


def test_threshold_boundaries_exclusive_inclusive():
    """CIN == -50 is 'capped' (<=); ML-CAPE == 300 and LI == -2 are realized (>=/<=)."""
    # CIN exactly at the cap, low instability → potential.
    assert convection_realized(
        method="thermo", cin_jkg=-50.0, ml_cape_jkg=299.0, lifted_index=-1.9
    ) is False
    # ML-CAPE exactly at the realized anchor → realized.
    assert convection_realized(
        method="thermo", cin_jkg=-50.0, ml_cape_jkg=300.0, lifted_index=None
    ) is True
    # LI exactly at the realized boundary → realized.
    assert convection_realized(
        method="thermo", cin_jkg=-50.0, ml_cape_jkg=None, lifted_index=-2.0
    ) is True


# ---------------------------------------------------------------------------
# classify_convective_character (VFR avoidability — issue #294)
# ---------------------------------------------------------------------------

from weatherbrief.analysis.sounding.convective import (  # noqa: E402
    ConvCharPoint,
    classify_convective_character,
)
from weatherbrief.models import ConvectiveCharacter  # noqa: E402


def _pts(n_total, *, conv=0, realized=0, embedded=0, k=None, tt=None):
    """Build n_total points; the first `conv` are convective, of which
    `realized` are realized and `embedded` are embedded."""
    points = []
    for i in range(n_total):
        is_conv = i < conv
        points.append(
            ConvCharPoint(
                is_convective=is_conv,
                realized=is_conv and i < realized,
                embedded=is_conv and i < embedded,
                k_index=k if is_conv else None,
                total_totals=tt if is_conv else None,
            )
        )
    return points


def test_character_none_when_no_points():
    assert classify_convective_character([]) is ConvectiveCharacter.NONE


def test_character_none_when_no_convection():
    pts = _pts(10, conv=0)
    assert classify_convective_character(pts) is ConvectiveCharacter.NONE


def test_character_isolated_few_realized_cells():
    # 20 points, 4 convective, only 2 realized (10% of route) → isolated.
    pts = _pts(20, conv=4, realized=2)
    assert classify_convective_character(pts) is ConvectiveCharacter.ISOLATED


def test_character_scattered():
    # 10 points, 5 convective, 3 realized (30%) → scattered.
    pts = _pts(10, conv=5, realized=3)
    assert classify_convective_character(pts) is ConvectiveCharacter.SCATTERED


def test_character_widespread():
    # 10 points, 8 convective, 6 realized (60%) → widespread.
    pts = _pts(10, conv=8, realized=6)
    assert classify_convective_character(pts) is ConvectiveCharacter.WIDESPREAD


def test_character_embedded_takes_priority():
    # Even with few realized cells, a majority embedded → embedded.
    pts = _pts(20, conv=4, realized=2, embedded=3)
    assert classify_convective_character(pts) is ConvectiveCharacter.EMBEDDED


def test_character_organized_widespread_with_front():
    pts = _pts(10, conv=8, realized=6)
    assert (
        classify_convective_character(pts, front_present=True)
        is ConvectiveCharacter.ORGANIZED
    )


def test_character_organized_widespread_with_shear():
    pts = _pts(10, conv=8, realized=6)
    assert (
        classify_convective_character(pts, shear_kt=40)
        is ConvectiveCharacter.ORGANIZED
    )


def test_character_forcing_does_not_organize_isolated():
    # EDQT-like: forcing (front + shear) present but only a few realized cells
    # → stays ISOLATED (avoidable), not ORGANIZED. This is the ground-truth case.
    pts = _pts(20, conv=8, realized=2)
    assert (
        classify_convective_character(pts, front_present=True, shear_kt=38)
        is ConvectiveCharacter.ISOLATED
    )


def test_character_numerosity_nudges_band_up():
    # Isolated by coverage (10%), but very high K → bumped to scattered.
    pts = _pts(20, conv=4, realized=2, k=45)
    assert classify_convective_character(pts) is ConvectiveCharacter.SCATTERED


def test_character_numerosity_nudge_clamps_at_widespread():
    pts = _pts(10, conv=8, realized=6, tt=60)
    # already widespread; nudge clamps (no organize signal) → widespread.
    assert classify_convective_character(pts) is ConvectiveCharacter.WIDESPREAD


def test_character_k_nudge_plus_forcing_organizes_scattered():
    # Scattered realized coverage (30%), very high K (numerous-storm
    # environment), front present. The K nudge lifts SCATTERED → WIDESPREAD,
    # then forcing relabels WIDESPREAD → ORGANIZED. This is INTENTIONAL
    # (MCS-initiation: a moist numerous environment along a front), and is the
    # documented limit of coverage-first protection — only ISOLATED (≤15%) is
    # guaranteed safe, because the one-step nudge can lift it no higher than
    # SCATTERED, which never reaches the widespread-only forcing gate.
    pts = _pts(10, conv=5, realized=3, k=45)
    assert (
        classify_convective_character(pts, front_present=True)
        is ConvectiveCharacter.ORGANIZED
    )
    # Contrast: the same K nudge + forcing on ISOLATED (10%) cannot organize —
    # nudge lifts it only to SCATTERED, below the widespread forcing gate.
    iso = _pts(20, conv=4, realized=2, k=45)
    assert (
        classify_convective_character(iso, front_present=True)
        is ConvectiveCharacter.SCATTERED
    )
