"""Tests for cc-based NWP cloud layer detection (sounding/clouds.py).

Covers build_nwp_cloud_layers_from_fraction and the preferred-path wiring
in build_nwp_cloud_layers — ECMWF/ICON with per-level cloud_area_fraction
should take precedence over GRIB-bulk and synthesize paths.
"""

from weatherbrief.analysis.sounding.clouds import (
    build_nwp_cloud_layers,
    build_nwp_cloud_layers_from_fraction,
)
from weatherbrief.models import (
    CloudCoverage,
    NWPCloudDiagnostics,
    NWPCloudLayerDiag,
    PressureLevelData,
)


def _lv(p, gph_m, caf=None, t=None):
    return PressureLevelData(
        pressure_hpa=p,
        geopotential_height_m=gph_m,
        cloud_area_fraction_pct=caf,
        temperature_c=t,
    )


def test_no_caf_returns_none():
    """Empty/no-CAF profile triggers fallback (returns None)."""
    levels = [_lv(1000, 100), _lv(850, 1500), _lv(500, 5500)]
    assert build_nwp_cloud_layers_from_fraction(levels) is None


def test_empty_levels_returns_none():
    assert build_nwp_cloud_layers_from_fraction([]) is None


def test_single_deck():
    """One contiguous CAF-above-threshold run → one layer."""
    levels = [
        _lv(1000, 100, caf=0),
        _lv(925, 760, caf=82, t=15.0),
        _lv(900, 1000, caf=91, t=13.0),
        _lv(850, 1500, caf=5),
        _lv(700, 3100, caf=0),
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    assert layers is not None
    assert len(layers) == 1
    layer = layers[0]
    assert layer.source == "nwp_3d"
    # 760m ≈ 2493 ft, 1000m ≈ 3281 ft
    assert 2400 < layer.base_ft < 2600
    assert 3200 < layer.top_ft < 3400
    # 91% peak → OVC
    assert layer.coverage == CloudCoverage.OVC


def test_multiple_decks():
    """Separated CAF runs produce multiple layers."""
    levels = [
        _lv(1000, 100, caf=0),
        _lv(900, 1000, caf=70),
        _lv(850, 1500, caf=0),
        _lv(500, 5500, caf=60),
        _lv(300, 9500, caf=90),
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    assert layers is not None
    assert len(layers) == 2


def test_threshold_filters_trace_caf():
    """CAF below default 12.5% threshold is ignored."""
    levels = [
        _lv(1000, 100, caf=5),
        _lv(850, 1500, caf=8),
        _lv(700, 3000, caf=10),
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    # All below threshold → empty list (data present, no cloud)
    assert layers == []


def test_missing_gph_excludes_level():
    """Level with CAF but no geopotential height is skipped (breaks deck)."""
    levels = [
        _lv(1000, 100, caf=0),
        _lv(925, 700, caf=80, t=14.0),
        PressureLevelData(pressure_hpa=900, cloud_area_fraction_pct=85),  # no gph
        _lv(850, 1500, caf=80, t=12.0),
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    assert layers is not None
    # Two decks because missing-gph level breaks the run
    assert len(layers) == 2


def test_coverage_mapping():
    """Peak CAF drives coverage category."""
    cases = [
        (15.0, CloudCoverage.FEW),
        (35.0, CloudCoverage.SCT),
        (60.0, CloudCoverage.BKN),
        (95.0, CloudCoverage.OVC),
    ]
    for caf, expected in cases:
        levels = [_lv(1000, 100, caf=caf, t=15)]
        layers = build_nwp_cloud_layers_from_fraction(levels)
        assert layers and layers[0].coverage == expected, f"caf={caf}"


def test_build_nwp_cloud_layers_prefers_3d():
    """When per-level CAF is present, the 3D path wins over bulk diagnostics."""
    pl = [
        _lv(1000, 100, caf=0),
        _lv(925, 760, caf=70, t=14.0),
        _lv(850, 1500, caf=0),
    ]
    # Also provide GRIB bulk diagnostics with different boundaries
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=90, base_ft=500, top_ft=9999),
    )
    layers = build_nwp_cloud_layers(
        nwp_cloud_diagnostics=diag,
        pressure_levels=pl,
    )
    # 3D path picks the caf-driven deck, not the bulk one.
    assert layers is not None
    assert len(layers) == 1
    assert layers[0].source == "nwp_3d"


def test_build_nwp_cloud_layers_falls_back_when_no_caf():
    """No per-level CAF → falls through to existing GRIB-bulk path."""
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=60, base_ft=1200, top_ft=3500),
    )
    layers = build_nwp_cloud_layers(
        nwp_cloud_diagnostics=diag,
        nwp_cloud_low_pct=60,
        pressure_levels=[_lv(1000, 100), _lv(850, 1500)],  # no caf
    )
    assert layers is not None
    assert len(layers) == 1
    assert layers[0].source == "grib"
