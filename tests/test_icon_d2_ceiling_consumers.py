"""ICON-D2 ceiling-limited fetch (#469 phase 2 / #474) — consumer safety.

The re-land (approach A) keeps the asymmetric cut: t/qv/p stay full column
(CAPE), while u/v/w/qc/qi/clc are dropped above a ceiling-derived level. That
leaves upper pressure levels carrying temperature but no wind and no cloud
fraction. These tests pin the guiding invariant of the re-land:

    a column truncated by a fetch optimisation must never be reportable as
    calm, clear, or smooth — only as unavailable.

so the fetch flag can stay ON by default. The pure fetch/level-split machinery
is covered by ``test_icon_d2_ceiling.py``; here we exercise the sounding
*consumers* against a synthetic truncated column.
"""

from __future__ import annotations

import numpy as np

from weatherbrief.analysis.advisories.dd_nwp_agreement import _cloud_overlap_fraction
from weatherbrief.analysis.sounding import analyze_sounding
from weatherbrief.analysis.sounding.clouds import build_nwp_cloud_layers_from_fraction
from weatherbrief.analysis.sounding.prepare import prepare_profile
from weatherbrief.models import CloudCoverage, EnhancedCloudLayer, PressureLevelData


def _lv(p, gph_m, *, t=None, rh=None, ws=None, wd=None, w=None, caf=None):
    return PressureLevelData(
        pressure_hpa=p,
        geopotential_height_m=gph_m,
        temperature_c=t,
        relative_humidity_pct=rh,
        wind_speed_kt=ws,
        wind_direction_deg=wd,
        vertical_velocity_pa_s=w,
        cloud_area_fraction_pct=caf,
    )


def _truncated_column() -> list[PressureLevelData]:
    """ICON-D2-style column truncated above ~500 hPa.

    Below the cut every level carries wind/omega/cloud; above it (500, 300 hPa)
    only the thermodynamic column (t/qv → RH) survives, mirroring the real cut.
    A strong directional wind shear near 700 hPa seeds a CAT layer in the
    flyable column so the turbulence path has something to grade.
    """
    return [
        _lv(1000, 110, t=15, rh=80, ws=10, wd=200, w=0.0, caf=0),
        _lv(900, 990, t=9, rh=75, ws=15, wd=210, w=0.1, caf=0),
        _lv(800, 1950, t=3, rh=70, ws=20, wd=225, w=0.1, caf=0),
        _lv(700, 3010, t=-3, rh=65, ws=55, wd=300, w=0.2, caf=0),  # shear kink
        _lv(600, 4200, t=-10, rh=60, ws=60, wd=305, w=0.1, caf=0),
        # --- cut here (~18 kft): wind/omega/clc unavailable above ---
        _lv(500, 5570, t=-20, rh=55),
        _lv(300, 9160, t=-45, rh=40),
    ]


# ---------------------------------------------------------------------------
# prepare.py — per-level wind gate (NaN fill, not all-or-nothing)
# ---------------------------------------------------------------------------


def test_prepare_keeps_wind_for_flyable_column():
    profile = prepare_profile(_truncated_column())
    assert profile is not None
    assert profile.wind_speed is not None  # NOT dropped for the whole profile
    ws = profile.wind_speed.to("knot").magnitude
    # Surface-first order: the flyable levels keep real wind, the truncated top
    # two are NaN.
    assert not np.isnan(ws[0])
    assert np.isnan(ws[-1]) and np.isnan(ws[-2])
    assert np.count_nonzero(~np.isnan(ws)) == 5


def test_prepare_all_windless_stays_none():
    # A genuinely wind-less profile (no level has wind) still yields None — the
    # NaN-fill only kicks in when SOME level has wind.
    levels = [_lv(1000, 110, t=15, rh=80), _lv(900, 990, t=9, rh=75),
              _lv(800, 1950, t=3, rh=70)]
    profile = prepare_profile(levels)
    assert profile is not None
    assert profile.wind_speed is None


# ---------------------------------------------------------------------------
# analyze_sounding — end-to-end truncation honesty
# ---------------------------------------------------------------------------


def test_analyze_sounding_records_fetched_column_top():
    result = analyze_sounding(_truncated_column())
    assert result is not None
    # The top of the fetched wind column is the highest wind-carrying level
    # (600 hPa ≈ 4200 m ≈ 13.8 kft), NOT the model top.
    assert result.fetched_column_top_ft is not None
    assert 13_000 < result.fetched_column_top_ft < 14_500


def test_full_column_has_no_fetched_top():
    # Same column but with wind all the way up → not truncated → field stays None.
    levels = _truncated_column()
    levels[-1] = _lv(300, 9160, t=-45, rh=40, ws=70, wd=310, w=0.1)
    levels[-2] = _lv(500, 5570, t=-20, rh=55, ws=65, wd=308, w=0.1)
    result = analyze_sounding(levels)
    assert result is not None
    assert result.fetched_column_top_ft is None


def test_turbulence_axis_alive_in_flyable_column():
    # The headline #391/#393 failure: an all-or-nothing wind gate would leave
    # EVERY level with richardson_number None → cat_risk_layers == [] → a false
    # smooth GREEN. With the per-level gate the flyable column keeps Richardson.
    result = analyze_sounding(_truncated_column())
    assert result is not None
    ri_below = [dl.richardson_number for dl in result.derived_levels
                if dl.wind_speed_kt is not None]
    assert any(r is not None for r in ri_below)
    # Above the cut wind is honestly absent.
    assert all(dl.wind_speed_kt is None
               for dl in result.derived_levels if dl.pressure_hpa < 550)


def test_bulk_shear_truncated_top_is_none_not_nan():
    result = analyze_sounding(_truncated_column())
    assert result is not None and result.indices is not None
    # 0–6 km top sits above the cut → shear unavailable → None (never a NaN that
    # would poison JSON). 0–1 km is entirely below the cut → still computed.
    assert result.indices.bulk_shear_0_6km_kt is None
    assert result.indices.bulk_shear_0_1km_kt is not None


# ---------------------------------------------------------------------------
# clouds.py — top_truncated sentinel
# ---------------------------------------------------------------------------


def test_deck_top_bounded_by_cut_is_truncated():
    levels = [
        _lv(1000, 110, t=15, ws=10, wd=200, caf=0),
        _lv(850, 1500, t=6, ws=20, wd=220, caf=90),   # OVC deck
        _lv(700, 3010, t=-2, ws=25, wd=240, caf=92),  # OVC deck top
        _lv(500, 5570, t=-20, rh=55),                 # truncated: no caf, no wind
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    assert layers and len(layers) == 1
    assert layers[0].coverage == CloudCoverage.OVC
    assert layers[0].top_truncated is True


def test_deck_top_bounded_by_clear_air_is_not_truncated():
    # Same deck, but the level above carries wind + a sub-FEW cloud fraction —
    # a genuine model-clear boundary, not a fetch cut.
    levels = [
        _lv(1000, 110, t=15, ws=10, wd=200, caf=0),
        _lv(850, 1500, t=6, ws=20, wd=220, caf=90),   # OVC deck top
        _lv(700, 3010, t=-2, ws=25, wd=240, caf=5),   # sub-FEW, HAS wind
        _lv(500, 5570, t=-20, ws=30, wd=250, caf=3),  # sub-FEW, HAS wind
    ]
    layers = build_nwp_cloud_layers_from_fraction(levels)
    assert layers and len(layers) == 1
    assert layers[0].top_truncated is False


# ---------------------------------------------------------------------------
# dd_nwp_agreement — the truncated strip is kept out of the comparison
# ---------------------------------------------------------------------------


def test_overlap_clips_to_fetched_top():
    # DD (full column) sees a high deck the truncated NWP column never fetched.
    dd = [EnhancedCloudLayer(base_ft=3000, top_ft=6000, source="dd"),
          EnhancedCloudLayer(base_ft=20000, top_ft=24000, source="dd")]
    nwp = [EnhancedCloudLayer(base_ft=3000, top_ft=6000, source="nwp_3d")]
    # Unclipped: the high DD deck drags the Jaccard well below agreement.
    assert _cloud_overlap_fraction(dd, nwp) < 0.6
    # Clipped at the fetched top (~18 kft): only the shared low deck remains →
    # full agreement, no spurious "tracks diverge".
    assert _cloud_overlap_fraction(dd, nwp, max_alt_ft=18000.0) == 1.0
