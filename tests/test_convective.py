"""Tests for convective risk assessment (sounding/convective.py)."""

from weatherbrief.analysis.sounding.convective import (
    _effective_cape,
    assess_convective_nwp,
    assess_convective_thermo,
)
from weatherbrief.models import (
    ConvectiveAssessment,
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


def test_effective_cape_nwp_raw():
    """Uses NWP raw CAPE when it exceeds other variants."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=200.0,
        nwp_cape_jkg=900.0,
    )
    assert _effective_cape(indices) == 900.0


def test_effective_cape_all_variants():
    """Max across all four CAPE variants."""
    indices = ThermodynamicIndices(
        cape_surface_jkg=100.0,
        cape_most_unstable_jkg=300.0,
        cape_mixed_layer_jkg=500.0,
        nwp_cape_jkg=400.0,
    )
    assert _effective_cape(indices) == 500.0


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


def test_nwp_risk_marginal_at_10():
    """10% convective cover → MARGINAL."""
    indices = ThermodynamicIndices(cape_surface_jkg=0.0)
    diag = NWPCloudDiagnostics(convective_cover_pct=15.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.MARGINAL


def test_nwp_risk_low_at_25():
    """25% convective cover → LOW."""
    indices = ThermodynamicIndices()
    diag = NWPCloudDiagnostics(convective_cover_pct=30.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.LOW


def test_nwp_risk_moderate_at_50():
    """50% convective cover → MODERATE."""
    indices = ThermodynamicIndices()
    diag = NWPCloudDiagnostics(convective_cover_pct=55.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.MODERATE


def test_nwp_risk_high_at_75():
    """75% convective cover → HIGH."""
    indices = ThermodynamicIndices()
    diag = NWPCloudDiagnostics(convective_cover_pct=80.0)
    result = assess_convective_nwp(indices, diag)
    assert result is not None
    assert result.risk_level == ConvectiveRisk.HIGH


def test_nwp_risk_none_below_10():
    """< 10% convective cover → NONE."""
    indices = ThermodynamicIndices()
    diag = NWPCloudDiagnostics(convective_cover_pct=5.0)
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
    indices = ThermodynamicIndices()
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
