"""Tests for enhanced cloud layer detection (sounding/clouds.py)."""

import pytest

from weatherbrief.analysis.sounding.clouds import build_nwp_cloud_layers, detect_cloud_layers
from weatherbrief.models import DerivedLevel, NWPCloudDiagnostics, NWPCloudLayerDiag
from weatherbrief.models.analysis import CloudCoverage, EnhancedCloudLayer, SoundingAnalysis


def test_single_cloud_layer():
    """Consecutive levels in the same DD category form one layer.

    Both 925 (DD=1.7) and 850 (DD=1.5) classify as BKN (1.0 ≤ DD < 2.0)
    so they merge into a single BKN layer; threshold-crossing on the
    neighbors gives non-zero base/top.
    """
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=8.0),
        DerivedLevel(pressure_hpa=925, altitude_ft=2530, dewpoint_depression_c=1.7),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=1.5),
        DerivedLevel(pressure_hpa=700, altitude_ft=9880, dewpoint_depression_c=10.0),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].coverage == CloudCoverage.BKN
    # Base/top are interpolated, not pinned to level altitudes.
    assert layers[0].base_ft < 2530, layers[0].base_ft
    assert layers[0].top_ft > 4760, layers[0].top_ft


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
    # 1000 (BKN, DD=1.5) | 925 clear | 850 (SCT, DD=2.0) | 700 clear → 2 layers
    assert len(layers) == 2


def test_cloud_extending_to_top():
    """Cloud extending to top of profile is captured.

    Both upper levels share BKN category so they merge; the topmost edge
    falls back to the level's own altitude (no level above to interpolate
    against — TOA fallback).
    """
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=8.0),
        DerivedLevel(pressure_hpa=500, altitude_ft=18040, dewpoint_depression_c=1.5),
        DerivedLevel(pressure_hpa=300, altitude_ft=29860, dewpoint_depression_c=1.8),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].coverage == CloudCoverage.BKN
    # Topmost edge falls back to the level's own altitude.
    assert layers[0].top_ft == 29860


def test_coverage_ovc():
    """Two adjacent OVC levels (DD < 1.0) form one OVC layer."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=8.0),
        DerivedLevel(pressure_hpa=925, altitude_ft=2530, dewpoint_depression_c=0.5),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=0.8),
        DerivedLevel(pressure_hpa=700, altitude_ft=9880, dewpoint_depression_c=8.0),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].coverage == CloudCoverage.OVC


def test_coverage_bkn():
    """Two adjacent BKN levels (1.0 ≤ DD < 2.0) form one BKN layer."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=8.0),
        DerivedLevel(pressure_hpa=925, altitude_ft=2530, dewpoint_depression_c=1.2),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=1.8),
        DerivedLevel(pressure_hpa=700, altitude_ft=9880, dewpoint_depression_c=8.0),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].coverage == CloudCoverage.BKN


def test_coverage_sct():
    """Two adjacent SCT levels (2.0 ≤ DD < 3.0) form one SCT layer."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=8.0),
        DerivedLevel(pressure_hpa=925, altitude_ft=2530, dewpoint_depression_c=2.5),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=2.8),
        DerivedLevel(pressure_hpa=700, altitude_ft=9880, dewpoint_depression_c=8.0),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].coverage == CloudCoverage.SCT


def test_dd_category_change_splits():
    """A column where DD oscillates across category boundaries splits into
    multiple categorical layers instead of one mean-DD-classified slab."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=8.0),
        DerivedLevel(pressure_hpa=950, altitude_ft=1700, dewpoint_depression_c=2.5),  # SCT
        DerivedLevel(pressure_hpa=925, altitude_ft=2530, dewpoint_depression_c=0.8),  # OVC
        DerivedLevel(pressure_hpa=900, altitude_ft=3300, dewpoint_depression_c=0.4),  # OVC
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=1.5),  # BKN
        DerivedLevel(pressure_hpa=700, altitude_ft=9880, dewpoint_depression_c=8.0),  # clear
    ]
    layers = detect_cloud_layers(levels)
    cats = [layer.coverage for layer in layers]
    assert cats == [CloudCoverage.SCT, CloudCoverage.OVC, CloudCoverage.BKN]
    # Layers stack contiguously (within rounding).
    for prev, nxt in zip(layers, layers[1:]):
        assert abs(prev.top_ft - nxt.base_ft) <= 1, (prev.top_ft, nxt.base_ft)
    # No degenerate slabs.
    for layer in layers:
        assert layer.thickness_ft > 0


def test_dd_threshold_crossing_base_below_clear():
    """SCT base interpolates against DD = 3.0 K (SCT/clear boundary)
    when below is clear.

    DD goes from 8.0 (clear) at 330 ft to 2.5 (SCT) at 1700 ft.
    Boundary DD = 3.0. frac = (3.0 − 2.5) / (8.0 − 2.5) = 0.0909.
    Interpolated alt = 1700 + 0.0909 × (330 − 1700) ≈ 1576 ft.
    """
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=8.0),
        DerivedLevel(pressure_hpa=950, altitude_ft=1700, dewpoint_depression_c=2.5),
        DerivedLevel(pressure_hpa=900, altitude_ft=3300, dewpoint_depression_c=8.0),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].coverage == CloudCoverage.SCT
    assert 1500 < layers[0].base_ft < 1650, layers[0].base_ft


def test_dd_single_level_deck_with_neighbors():
    """A single-level deck sandwiched by clear neighbors gets non-zero
    thickness from threshold-crossing on both sides."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=8.0),
        DerivedLevel(pressure_hpa=925, altitude_ft=2530, dewpoint_depression_c=1.5),  # BKN
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=8.0),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].coverage == CloudCoverage.BKN
    assert layers[0].thickness_ft > 0
    assert layers[0].top_ft > layers[0].base_ft


def test_missing_dd_uses_midpoint_fallback():
    """When a deck-edge neighbor has DD=None but a valid altitude, the
    layer edge falls back to the altitude midpoint — the missing DD blocks
    threshold-crossing but altitude info is still usable."""
    levels = [
        DerivedLevel(pressure_hpa=1000, altitude_ft=330, dewpoint_depression_c=None),
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=1.0),
        DerivedLevel(pressure_hpa=700, altitude_ft=9880, dewpoint_depression_c=None),
    ]
    layers = detect_cloud_layers(levels)
    assert len(layers) == 1
    assert layers[0].coverage == CloudCoverage.BKN
    # Midpoint between (330, 4760) ≈ 2545; midpoint between (4760, 9880) ≈ 7320.
    assert 2500 < layers[0].base_ft < 2600
    assert 7300 < layers[0].top_ft < 7400


def test_empty_levels():
    """Empty input returns empty list."""
    assert detect_cloud_layers([]) == []


def test_single_level_deck_has_non_collapsed_pressures():
    """A deck consisting of one pressure level must still span a non-zero
    pressure range — interpolated between neighbors so the Skew-T view
    (which plots in pressure coords) renders the deck instead of drawing
    a zero-height band.

    Regression for the OBIMO/ICON case where a BKN run at a single level
    (600 hPa) reported base_pressure_hpa == top_pressure_hpa, making the
    deck invisible on the Skew-T while the cross-section (altitude axis)
    correctly drew it.
    """
    levels = [
        DerivedLevel(pressure_hpa=850, altitude_ft=4760, dewpoint_depression_c=0.3),  # OVC
        DerivedLevel(pressure_hpa=700, altitude_ft=9880, dewpoint_depression_c=0.3),  # OVC
        DerivedLevel(pressure_hpa=600, altitude_ft=13800, dewpoint_depression_c=1.8),  # BKN
        DerivedLevel(pressure_hpa=500, altitude_ft=18300, dewpoint_depression_c=2.3),  # SCT
    ]
    layers = detect_cloud_layers(levels)
    # Three runs: OVC[850,700], BKN[600], SCT[500] → 3 layers.
    assert len(layers) == 3, [(c.base_ft, c.top_ft, c.coverage.value) for c in layers]
    bkn = next(c for c in layers if c.coverage == CloudCoverage.BKN)
    assert bkn.base_pressure_hpa is not None and bkn.top_pressure_hpa is not None
    assert bkn.base_pressure_hpa > bkn.top_pressure_hpa, (
        f"Single-level BKN deck collapsed: base={bkn.base_pressure_hpa} "
        f"top={bkn.top_pressure_hpa} hPa"
    )
    # Interpolated edges — base sits between 700 (denser) and 600 (in deck),
    # top sits between 600 (in deck) and 500 (clear/lighter).
    assert 600 < bkn.base_pressure_hpa < 700
    assert 500 < bkn.top_pressure_hpa < 600


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


def test_build_nwp_cloud_layers_no_native_returns_none():
    """No GRIB diag and no per-level cc → returns None (no synth fallback).

    Previously the function would synthesize layers from Open-Meteo
    bulk cover percentages narrowed by DD evidence. The strict-native
    contract drops that path: bulk %s alone don't constitute a model-
    native cloud envelope, so the function reports "no NWP layer data"
    rather than fabricating one.
    """
    layers = build_nwp_cloud_layers(
        nwp_cloud_diagnostics=None,
        nwp_cloud_low_pct=80.0,  # bulk cover present but no native source
        nwp_cloud_mid_pct=50.0,
        nwp_cloud_high_pct=20.0,
    )
    assert layers is None


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


def test_resolve_analyses_dd_keeps_layers_and_badges_the_method():
    """All-DD/thermo methods keep the DD layers — and still badge the method.

    Since #403 an *absent* (None) method resolves to its NWP default, so the
    no-swap case requires the DD/thermo methods to be stated explicitly. Since the
    #409 follow-up the DD path is no longer object-identity: it swaps no data but
    stamps ``cloud_method_effective="dd"``, so "graded on DD" is distinguishable
    from "this advisory has no method axis".
    """
    from weatherbrief.tasks.advise import _resolve_analyses
    rpa, dd_layers, _ = _make_rpa_with_clouds()
    original = [rpa]
    result = _resolve_analyses(original, "ogimet_dd", "dd", "thermo")
    s = result[0].sounding["gfs"]
    assert s.cloud_layers[0].base_ft == 3000  # DD layers kept verbatim
    assert s.cloud_method_effective == "dd"
    assert rpa.sounding["gfs"].cloud_method_effective is None  # original untouched


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


def test_cloud_source_from_method():
    """Styled <style>_<source> forms and bare/None reduce to the right source."""
    from weatherbrief.tasks.advise import _cloud_source_from_method
    assert _cloud_source_from_method("nwp") == "nwp"
    assert _cloud_source_from_method("soft_nwp") == "nwp"
    assert _cloud_source_from_method("square_nwp") == "nwp"
    assert _cloud_source_from_method("natural_nwp") == "nwp"
    assert _cloud_source_from_method("dd") == "dd"
    assert _cloud_source_from_method("natural_dd") == "dd"
    assert _cloud_source_from_method("square_dd") == "dd"
    assert _cloud_source_from_method(None) is None
    assert _cloud_source_from_method("") is None


@pytest.mark.parametrize("method", ["soft_nwp", "square_nwp", "natural_nwp"])
def test_resolve_analyses_styled_nwp_swaps(method):
    """Styled *_nwp forms resolve to NWP layers (regression: used to fall to DD)."""
    from weatherbrief.tasks.advise import _resolve_analyses
    rpa, _, _ = _make_rpa_with_clouds()
    result = _resolve_analyses([rpa], None, method)
    assert result[0].sounding["gfs"].cloud_layers[0].base_ft == 5000
    assert result[0].sounding["gfs"].cloud_layers[0].coverage == CloudCoverage.OVC


@pytest.mark.parametrize("method", ["natural_dd", "square_dd", "soft_dd"])
def test_resolve_analyses_styled_dd_keeps_dd(method):
    """Styled *_dd forms keep the DD-derived base layers."""
    from weatherbrief.tasks.advise import _resolve_analyses
    rpa, _, _ = _make_rpa_with_clouds()
    result = _resolve_analyses([rpa], None, method)
    assert result[0].sounding["gfs"].cloud_layers[0].base_ft == 3000
