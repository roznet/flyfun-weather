"""Tests for convective risk assessment (sounding/convective.py)."""

from weatherbrief.analysis.sounding.convective import (
    _effective_cape,
    assess_convective_nwp,
    assess_convective_thermo,
    classify_regime,
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
    assert any("ascent" in d for d in result.drivers)
    assert not result.suppressors


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


def test_nwp_returns_none_when_no_cover_no_bounds():
    """Returns None when cover_pct, base, and top are all None."""
    indices = ThermodynamicIndices(cape_surface_jkg=500.0)
    diag = NWPCloudDiagnostics()  # all None
    assert assess_convective_nwp(indices, diag) is None


def test_nwp_risk_from_cape_not_cover():
    """NWP full path uses CAPE thresholds, not cover — cover is informational."""
    # High CAPE + low cover → HIGH (not LOW as cover thresholds would give)
    indices = ThermodynamicIndices(cape_surface_jkg=1500.0)
    diag = NWPCloudDiagnostics(convective_cover_pct=15.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.HIGH
    assert result.cover_pct == 15.0  # preserved as context


def test_nwp_low_cape_high_cover_not_dangerous():
    """Low CAPE + high cover → low risk despite widespread convection."""
    indices = ThermodynamicIndices(cape_surface_jkg=40.0)
    diag = NWPCloudDiagnostics(convective_cover_pct=80.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.MARGINAL  # CAPE 40 >= 10 → marginal
    assert result.cover_pct == 80.0


def test_nwp_cape_thresholds():
    """NWP full path follows same CAPE thresholds as thermo."""
    diag = NWPCloudDiagnostics(convective_cover_pct=50.0)

    for cape, expected in [
        (2500, ConvectiveRisk.EXTREME),
        (1200, ConvectiveRisk.HIGH),
        (400, ConvectiveRisk.MODERATE),
        (80, ConvectiveRisk.LOW),
        (15, ConvectiveRisk.MARGINAL),
        (0, ConvectiveRisk.NONE),
    ]:
        indices = ThermodynamicIndices(cape_surface_jkg=float(cape))
        result = assess_convective_nwp(indices, diag)
        assert result is not None
        assert result.risk_level == expected, f"CAPE={cape}: expected {expected}, got {result.risk_level}"


def test_nwp_no_cape_no_risk():
    """No CAPE data with cover → NONE risk."""
    indices = ThermodynamicIndices()
    diag = NWPCloudDiagnostics(convective_cover_pct=80.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.NONE


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


def test_nwp_hybrid_uses_cape_risk():
    """Hybrid path: CAPE-based risk when cover_pct absent but base/top exist."""
    indices = ThermodynamicIndices(cape_surface_jkg=500.0)
    diag = NWPCloudDiagnostics(
        convective_base_ft=5000.0,
        convective_top_ft=35000.0,
    )
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.method == "nwp_hybrid"
    assert result.risk_level == ConvectiveRisk.MODERATE  # 500 >= 300
    assert result.base_ft == 5000.0
    assert result.top_ft == 35000.0
    assert result.cover_pct is None


def test_nwp_hybrid_marginal_low_cape():
    """Hybrid path: small positive CAPE → MARGINAL."""
    indices = ThermodynamicIndices(cape_surface_jkg=20.0)
    diag = NWPCloudDiagnostics(
        convective_base_ft=3000.0,
        convective_top_ft=15000.0,
    )
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.MARGINAL


def test_nwp_hybrid_no_cape_no_risk():
    """Hybrid path: no CAPE data → NONE risk but still returns assessment."""
    indices = ThermodynamicIndices()
    diag = NWPCloudDiagnostics(
        convective_base_ft=3000.0,
        convective_top_ft=25000.0,
    )
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.NONE
    assert result.method == "nwp_hybrid"


def test_nwp_hybrid_returns_none_partial_bounds():
    """Returns None when only base exists (no top) and no cover."""
    indices = ThermodynamicIndices(cape_surface_jkg=500.0)
    diag = NWPCloudDiagnostics(convective_base_ft=5000.0)
    assert assess_convective_nwp(indices, diag) is None


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
    """LCL-anchored path (ECMWF hcct): base=LCL when no convective_base_ft."""
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
    assert result.risk_level == ConvectiveRisk.MODERATE  # 800 >= 300


def test_nwp_lcl_top_returns_none_when_hcct_below_lcl():
    """LCL-anchored guard: hcct ≤ LCL yields None, not base > top."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=500.0,
        lcl_altitude_ft=8000.0,
    )
    diag = NWPCloudDiagnostics(convective_top_ft=6000.0)
    assert assess_convective_nwp(indices, diag) is None


def test_nwp_cin_suppression():
    """Strong CIN cap reduces NWP risk by one level."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=1500.0,  # HIGH
        cin_surface_jkg=-250.0,   # strong cap
    )
    diag = NWPCloudDiagnostics(convective_cover_pct=50.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.MODERATE  # HIGH → MODERATE


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
