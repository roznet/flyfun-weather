"""Tests for enhanced cloud layer detection (sounding/clouds.py)."""

from weatherbrief.analysis.sounding.clouds import build_nwp_cloud_layers, detect_cloud_layers
from weatherbrief.models import DerivedLevel, NWPCloudDiagnostics, NWPCloudLayerDiag
from weatherbrief.models.analysis import CloudCoverage, EnhancedCloudLayer, SoundingAnalysis


def test_single_cloud_layer():
    """Detects a single cloud layer where dewpoint depression < 3C."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=8.0),
        DerivedLevel(pressure_hpa=925, altitude_ft=2530, dewpoint_depression_c=2.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=1.5),
        DerivedLevel(pressure_hpa=700, altitude_ft=9880, dewpoint_depression_c=10.0),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].base_ft == 2530
    assert layers[0].top_ft == 4760


def test_no_cloud():
    """No cloud when all dewpoint depression above threshold."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=5.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=8.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=9880, dewpoint_depression_c=12.0),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 0


def test_two_layers():
    """Detects two separate cloud layers."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=1.5),
        DerivedLevel(pressure_hpa=925, altitude_ft=2530, dewpoint_depression_c=8.0),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=2.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=9880, dewpoint_depression_c=6.0),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 2


def test_cloud_extending_to_top():
    """Cloud extending to top of profile is captured."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=8.0),
        DerivedLevel(pressure_hpa=500, altitude_ft=18040, dewpoint_depression_c=1.5),
        DerivedLevel(pressure_hpa=300, altitude_ft=29860, dewpoint_depression_c=2.0),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].base_ft == 18040
    assert layers[0].top_ft == 29860


def test_coverage_ovc():
    """Mean DD < 1C classifies as OVC."""
    levels = [
        DerivedLevel(pressure_hpa=925, altitude_ft=2530, dewpoint_depression_c=0.5),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=0.8),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].coverage.value == "ovc"


def test_coverage_bkn():
    """Mean DD 1-2C classifies as BKN."""
    levels = [
        DerivedLevel(pressure_hpa=925, altitude_ft=2530, dewpoint_depression_c=1.2),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=1.8),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].coverage.value == "bkn"


def test_coverage_sct():
    """Mean DD 2-3C classifies as SCT."""
    levels = [
        DerivedLevel(pressure_hpa=925, altitude_ft=2530, dewpoint_depression_c=2.5),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=2.8),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].coverage.value == "sct"


def test_missing_dd_skipped():
    """Levels with missing dewpoint depression are skipped."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=None),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=1.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=9880, dewpoint_depression_c=None),
    ]
    # Single level can't form a layer (no adjacent partner)
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].base_ft == 4760
    assert layers[0].top_ft == 4760


def test_empty_levels():
    """Empty input returns empty list."""
    assert detect_cloud_layers([]) == []


# --- NWP cloud layer tests ---


def test_build_nwp_cloud_layers_full_diagnostics():
    """GFS-style diagnostics with full base/top produce layers."""
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=90.0, base_ft=2000.0, top_ft=5000.0),
        mid=NWPCloudLayerDiag(cover_pct=60.0, base_ft=10000.0, top_ft=18000.0),
        high=NWPCloudLayerDiag(cover_pct=30.0, base_ft=25000.0, top_ft=35000.0),
    )
    layers = build_nwp_cloud_layers(diag)
    assert layers is not None
    assert len(layers) == 3
    # Check coverage mapping
    assert layers[0].coverage == CloudCoverage.OVC  # 90% >= 87.5
    assert layers[1].coverage == CloudCoverage.BKN  # 60% >= 50
    assert layers[2].coverage == CloudCoverage.SCT  # 30% >= 25
    # Check altitudes
    assert layers[0].base_ft == 2000
    assert layers[0].top_ft == 5000
    assert layers[2].base_ft == 25000


def test_build_nwp_cloud_layers_none_diagnostics():
    """None diagnostics returns None."""
    assert build_nwp_cloud_layers(None) is None


def test_build_nwp_cloud_layers_no_base_top():
    """Diagnostics without base_ft/top_ft (ICON-EU pattern) returns None."""
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=80.0),
        mid=NWPCloudLayerDiag(cover_pct=50.0),
        high=NWPCloudLayerDiag(cover_pct=20.0),
    )
    assert build_nwp_cloud_layers(diag) is None


def test_build_nwp_cloud_layers_zero_cover_skipped():
    """Bands with zero coverage are not included."""
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=0.0, base_ft=2000.0, top_ft=5000.0),
        mid=NWPCloudLayerDiag(cover_pct=70.0, base_ft=10000.0, top_ft=18000.0),
        high=NWPCloudLayerDiag(cover_pct=0.0, base_ft=25000.0, top_ft=35000.0),
    )
    layers = build_nwp_cloud_layers(diag)
    assert layers is not None
    assert len(layers) == 1
    assert layers[0].base_ft == 10000


def test_build_nwp_cloud_layers_all_zero_cover():
    """All bands zero coverage returns empty list (not None — diagnostics exist)."""
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=0.0, base_ft=2000.0, top_ft=5000.0),
        mid=NWPCloudLayerDiag(cover_pct=0.0, base_ft=10000.0, top_ft=18000.0),
        high=NWPCloudLayerDiag(cover_pct=0.0, base_ft=25000.0, top_ft=35000.0),
    )
    layers = build_nwp_cloud_layers(diag)
    assert layers is not None
    assert len(layers) == 0


def test_build_nwp_cloud_layers_coverage_thresholds():
    """Verify coverage percentage mapping to METAR categories."""
    # 87.5% -> OVC
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=87.5, base_ft=1000.0, top_ft=3000.0),
    )
    layers = build_nwp_cloud_layers(diag)
    assert layers[0].coverage == CloudCoverage.OVC

    # 50% -> BKN
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=50.0, base_ft=1000.0, top_ft=3000.0),
    )
    layers = build_nwp_cloud_layers(diag)
    assert layers[0].coverage == CloudCoverage.BKN

    # 25% -> SCT
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=25.0, base_ft=1000.0, top_ft=3000.0),
    )
    layers = build_nwp_cloud_layers(diag)
    assert layers[0].coverage == CloudCoverage.SCT

    # 15% -> FEW (1-2 oktas)
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=15.0, base_ft=1000.0, top_ft=3000.0),
    )
    layers = build_nwp_cloud_layers(diag)
    assert layers[0].coverage == CloudCoverage.FEW

    # 10% -> sub-FEW, no layer created (essentially clear)
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=10.0, base_ft=1000.0, top_ft=3000.0),
    )
    layers = build_nwp_cloud_layers(diag)
    assert layers == []


def test_build_nwp_cloud_layers_convective():
    """Convective layer is included when present."""
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=80.0, base_ft=2000.0, top_ft=5000.0),
        convective_cover_pct=60.0,
        convective_base_ft=3000.0,
        convective_top_ft=35000.0,
    )
    layers = build_nwp_cloud_layers(diag)
    assert layers is not None
    assert len(layers) == 2
    # Sorted by base_ft
    assert layers[0].base_ft == 2000
    assert layers[1].base_ft == 3000
    assert layers[1].top_ft == 35000


def test_build_nwp_cloud_layers_fallback_cover_pct():
    """When diag.cover_pct is None, falls back to Open-Meteo nwp_cloud_*_pct."""
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=None, base_ft=2000.0, top_ft=5000.0),
    )
    # Without fallback, no coverage -> skipped
    layers = build_nwp_cloud_layers(diag, nwp_cloud_low_pct=None)
    assert layers is not None
    assert len(layers) == 0

    # With fallback coverage
    layers = build_nwp_cloud_layers(diag, nwp_cloud_low_pct=75.0)
    assert layers is not None
    assert len(layers) == 1
    assert layers[0].coverage == CloudCoverage.BKN


def test_build_nwp_cloud_layers_dd_none():
    """NWP layers have mean_dewpoint_depression_c = None."""
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=90.0, base_ft=2000.0, top_ft=5000.0),
    )
    layers = build_nwp_cloud_layers(diag)
    assert layers[0].mean_dewpoint_depression_c is None


# --- _resolve_analyses cloud tests ---


def _make_rpa_with_clouds():
    """Create a minimal RoutePointAnalysis with DD and NWP cloud layers."""
    from datetime import datetime, timezone
    from weatherbrief.models import RoutePointAnalysis

    dd_layers = [EnhancedCloudLayer(base_ft=3000, top_ft=8000, coverage=CloudCoverage.BKN)]
    nwp_layers = [EnhancedCloudLayer(base_ft=5000, top_ft=12000, coverage=CloudCoverage.OVC)]

    sounding = SoundingAnalysis(
        cloud_layers=list(dd_layers),
        nwp_cloud_layers=list(nwp_layers),
    )
    rpa = RoutePointAnalysis(
        point_index=0,
        lat=48.0,
        lon=11.0,
        distance_from_origin_nm=0.0,
        interpolated_time=datetime.now(timezone.utc),
        forecast_hour=datetime.now(timezone.utc),
        track_deg=90.0,
        sounding={"gfs": sounding},
    )
    return rpa, dd_layers, nwp_layers


def test_resolve_analyses_dd_returns_original():
    """DD method returns the original list unchanged (identity)."""
    from weatherbrief.tasks.advise import _resolve_analyses
    rpa, dd_layers, _ = _make_rpa_with_clouds()
    original = [rpa]
    result = _resolve_analyses(original, None, "dd")
    assert result is original
    assert result[0].sounding["gfs"].cloud_layers[0].base_ft == 3000


def test_resolve_analyses_nwp_swaps_without_mutation():
    """NWP method resolves cloud_layers from NWP; original is untouched."""
    from weatherbrief.tasks.advise import _resolve_analyses
    rpa, _, nwp_layers = _make_rpa_with_clouds()
    result = _resolve_analyses([rpa], None, "nwp")
    # Resolved has NWP layers
    assert result[0].sounding["gfs"].cloud_layers[0].base_ft == 5000
    assert result[0].sounding["gfs"].cloud_layers[0].coverage == CloudCoverage.OVC
    # Original is NOT mutated
    assert rpa.sounding["gfs"].cloud_layers[0].base_ft == 3000


def test_resolve_analyses_nwp_fallback():
    """NWP method with None nwp_cloud_layers falls back to DD source."""
    from weatherbrief.tasks.advise import _resolve_analyses
    rpa, dd_layers, _ = _make_rpa_with_clouds()
    rpa.sounding["gfs"].nwp_cloud_layers = None
    result = _resolve_analyses([rpa], None, "nwp")
    assert result[0].sounding["gfs"].cloud_layers[0].base_ft == 3000
