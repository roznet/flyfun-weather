"""Tests for cc-based NWP cloud layer detection (sounding/clouds.py).

Covers build_nwp_cloud_layers_from_fraction and the preferred-path wiring
in build_nwp_cloud_layers — ECMWF/ICON with per-level cloud_area_fraction
should take precedence over GRIB-bulk and synthesize paths.

The detector groups consecutive levels by their METAR coverage category
(FEW/SCT/BKN/OVC) and anchors layer base/top by linearly interpolating
``cloud_area_fraction_pct`` against the boundary CAF separating the layer
from its (different-category) neighbor — a model-derived edge instead of
pinning to the level altitude.
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
    """Profile with no CAF triggers fallback (returns None)."""
    levels = [_lv(1000, 100), _lv(850, 1500), _lv(500, 5500)]
    assert build_nwp_cloud_layers_from_fraction(levels) is None


def test_empty_levels_returns_none():
    assert build_nwp_cloud_layers_from_fraction([]) is None


def test_threshold_filters_trace_caf():
    """All levels sub-FEW → empty list (model-derived clear column)."""
    levels = [
        _lv(1000, 100, caf=5),
        _lv(850, 1500, caf=8),
        _lv(700, 3000, caf=10),
    ]
    assert build_nwp_cloud_layers_from_fraction(levels) == []


def test_single_homogeneous_deck():
    """Consecutive levels in the same coverage category form one layer."""
    levels = [
        _lv(1000, 100, caf=0),
        _lv(925, 760, caf=90, t=15.0),   # OVC
        _lv(900, 1000, caf=92, t=13.0),  # OVC
        _lv(850, 1500, caf=5),
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    assert layers is not None
    assert len(layers) == 1
    assert layers[0].coverage == CloudCoverage.OVC
    assert layers[0].source == "nwp_3d"


def test_clear_gap_produces_two_separate_decks():
    """Two cloud regions separated by a sub-FEW level emit two independent
    layers with real clear air between them — they don't merge across the
    gap and don't share an edge altitude."""
    levels = [
        _lv(1000, 100, caf=0),
        _lv(925, 760, caf=70, t=14),    # BKN — deck 1
        _lv(850, 1500, caf=5),          # sub-FEW gap
        _lv(700, 3100, caf=60, t=5),    # BKN — deck 2
        _lv(500, 5500, caf=0),
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    assert layers is not None
    assert len(layers) == 2
    assert all(layer.coverage == CloudCoverage.BKN for layer in layers)
    # Layers do NOT meet — there is real clear air between them.
    assert layers[1].base_ft > layers[0].top_ft


def test_category_change_splits():
    """Transition between coverage categories splits decks."""
    levels = [
        _lv(1000, 100, caf=0),
        _lv(950, 500, caf=60, t=10),   # BKN
        _lv(925, 760, caf=90, t=8),    # OVC
        _lv(900, 1000, caf=95, t=6),   # OVC
        _lv(850, 1500, caf=35, t=4),   # SCT
        _lv(800, 2000, caf=5),
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    assert layers is not None
    cats = [layer.coverage for layer in layers]
    assert cats == [CloudCoverage.BKN, CloudCoverage.OVC, CloudCoverage.SCT]


def test_threshold_crossing_base_below_clear():
    """Base interpolates against the layer's lower-bound CAF when below is clear."""
    # BKN (caf=70%) at 1000 m above clear (caf=0%) at 0 m.
    # BKN lower bound = 50 %, frac = (50−70)/(0−70) = 0.2857.
    # Going 1000 m → 0 m: alt = 3281 + 0.2857 × (0 − 3281) ≈ 2343 ft.
    levels = [
        _lv(1000, 0, caf=0),
        _lv(900, 1000, caf=70, t=10),
        _lv(850, 1500, caf=5),
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    assert layers is not None
    assert len(layers) == 1
    layer = layers[0]
    assert layer.coverage == CloudCoverage.BKN
    assert 2300 < layer.base_ft < 2400, layer.base_ft


def test_threshold_crossing_top_into_higher_category():
    """Top interpolates against the next category's lower bound when above is denser."""
    # BKN (60 %) at 1000 m below OVC (95 %) at 1500 m.
    # OVC lower bound = 87.5 %, frac = (87.5−60)/(95−60) = 0.7857.
    # Going 1000 m → 1500 m: alt ≈ 3281 + 0.7857 × (4921 − 3281) ≈ 4570 ft.
    levels = [
        _lv(1000, 0, caf=0),
        _lv(925, 1000, caf=60, t=12),  # BKN
        _lv(900, 1500, caf=95, t=8),   # OVC
        _lv(850, 1800, caf=5),
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    assert layers is not None
    assert len(layers) == 2
    bkn = next(layer for layer in layers if layer.coverage == CloudCoverage.BKN)
    assert 4500 < bkn.top_ft < 4650, bkn.top_ft


def test_single_level_deck_with_neighbors():
    """A single level above threshold, sandwiched by below-threshold neighbors,
    still gets non-zero thickness via threshold-crossing on both sides."""
    levels = [
        _lv(1000, 0, caf=0),
        _lv(900, 1000, caf=40, t=10),  # SCT — single-level deck
        _lv(850, 1500, caf=0),
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    assert layers is not None
    assert len(layers) == 1
    layer = layers[0]
    assert layer.coverage == CloudCoverage.SCT
    assert layer.thickness_ft > 0
    assert layer.top_ft > layer.base_ft


def test_floor_uses_own_altitude_fallback():
    """Lowest level above threshold with no level below — base = its own altitude."""
    levels = [
        _lv(1000, 100, caf=70, t=15),  # BKN at floor
        _lv(925, 760, caf=80, t=14),   # BKN
        _lv(900, 1000, caf=10),
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    assert layers is not None
    assert len(layers) == 1
    # Floor falls back to the surface level's own altitude: 100 m × 3.281 ≈ 328 ft.
    assert 320 < layers[0].base_ft < 340, layers[0].base_ft


def test_top_uses_own_altitude_fallback():
    """Highest level above threshold with no level above — top = its own altitude."""
    levels = [
        _lv(900, 1000, caf=10),
        _lv(700, 3000, caf=70, t=-5),  # BKN at TOA
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    assert layers is not None
    assert len(layers) == 1
    # 3000 m × 3.281 ≈ 9842 ft.
    assert 9800 < layers[0].top_ft < 9900, layers[0].top_ft


def test_movex_pattern():
    """Realistic MOVEX-style profile splits into 6 contiguous categorical layers
    instead of one merged BKN slab from 1.9k to 23k ft.

    Per-level CAF taken from prod ECMWF EGTF→LFRQ briefing on 2026-05-16 07Z.
    """
    levels = [
        _lv(1000, 146.6, caf=0.0),
        _lv(950, 571.1, caf=59.6, t=7.3),
        _lv(925, 790.4, caf=88.2, t=6.0),
        _lv(900, 1014.6, caf=94.4, t=4.8),
        _lv(850, 1479.2, caf=14.0, t=2.5),
        _lv(800, 1967.5, caf=17.1, t=0.1),
        _lv(700, 3027.4, caf=33.2, t=-5.4),
        _lv(600, 4222.3, caf=81.5, t=-12.4),
        _lv(500, 5601.2, caf=39.1, t=-17.3),
        _lv(400, 7240.8, caf=48.2, t=-27.8),
        _lv(300, 9245.8, caf=0.3, t=-42.1),
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    assert layers is not None

    cats = [layer.coverage for layer in layers]
    assert cats == [
        CloudCoverage.BKN,  # 950
        CloudCoverage.OVC,  # 925-900
        CloudCoverage.FEW,  # 850-800
        CloudCoverage.SCT,  # 700
        CloudCoverage.BKN,  # 600
        CloudCoverage.SCT,  # 500-400
    ], cats

    # Threshold-crossing gives every layer real thickness (no zero-thickness slabs).
    for layer in layers:
        assert layer.thickness_ft > 0, layer

    # Adjacent layers meet at the same crossing altitude (within rounding).
    for prev, nxt in zip(layers, layers[1:]):
        assert abs(prev.top_ft - nxt.base_ft) <= 1, (
            f"layers don't meet: prev top={prev.top_ft}, "
            f"next base={nxt.base_ft}"
        )


def test_mean_cover_pct_not_peak():
    """``mean_cloud_cover_pct`` reports the deck mean (not the peak)."""
    # OVC run: 90 % and 95 % — mean 92.5, peak 95.
    levels = [
        _lv(1000, 100, caf=0),
        _lv(925, 760, caf=90, t=14),
        _lv(900, 1000, caf=95, t=12),
        _lv(850, 1500, caf=0),
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    assert layers is not None
    assert len(layers) == 1
    assert layers[0].mean_cloud_cover_pct is not None
    assert abs(layers[0].mean_cloud_cover_pct - 92.5) < 0.6


def test_missing_gph_breaks_run():
    """Level missing geopotential height splits the run (can't anchor altitude)."""
    levels = [
        _lv(1000, 100, caf=0),
        _lv(925, 700, caf=80, t=14.0),  # BKN
        PressureLevelData(pressure_hpa=900, cloud_area_fraction_pct=85),  # no gph
        _lv(850, 1500, caf=80, t=12.0),  # BKN
        _lv(800, 1900, caf=0),
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    assert layers is not None
    # Two decks because the missing-gph level breaks the run.
    assert len(layers) == 2


def test_coverage_mapping():
    """CAF → METAR category mapping at the four threshold midpoints."""
    cases = [
        (15.0, CloudCoverage.FEW),
        (35.0, CloudCoverage.SCT),
        (60.0, CloudCoverage.BKN),
        (95.0, CloudCoverage.OVC),
    ]
    for caf, expected in cases:
        # Pad with clear levels so threshold-crossing has neighbors on both sides.
        levels = [
            _lv(1000, 0, caf=0),
            _lv(900, 1000, caf=caf, t=15),
            _lv(800, 2000, caf=0),
        ]
        layers = build_nwp_cloud_layers_from_fraction(levels)
        assert layers and layers[0].coverage == expected, f"caf={caf}"


def test_build_nwp_cloud_layers_prefers_3d():
    """When per-level CAF is present, the 3D path wins over bulk diagnostics."""
    pl = [
        _lv(1000, 100, caf=0),
        _lv(925, 760, caf=70, t=14.0),
        _lv(850, 1500, caf=0),
    ]
    diag = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=90, base_ft=500, top_ft=9999),
    )
    layers = build_nwp_cloud_layers(
        nwp_cloud_diagnostics=diag,
        pressure_levels=pl,
    )
    assert layers is not None
    assert len(layers) == 1
    assert layers[0].source == "nwp_3d"


def test_build_nwp_cloud_layers_falls_back_when_no_caf():
    """No per-level CAF → falls through to the GRIB-bulk path."""
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
