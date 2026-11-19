"""The one thin-layer convention (meteorology-decisions §86).

Every layer seen at a single level (icing zones, SLD zones, flat NWP-3D cloud
decks) goes through ``thin_layers.widen_thin_layer``. The last tests change the
constant and check every user follows it, so a copy of the logic anywhere else
fails here.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from weatherbrief.analysis.sounding import thin_layers
from weatherbrief.analysis.sounding.clouds import build_nwp_cloud_layers_from_fraction
from weatherbrief.analysis.sounding.icing import _build_zone_simple
from weatherbrief.analysis.sounding.sfip import _build_sfip_zone
from weatherbrief.models import DerivedLevel, IcingRisk, IcingType, PressureLevelData


@pytest.mark.parametrize("base, top, expected", [
    (5000, 5000, (4500, 5500)),     # one level
    (5000, 5300, (4650, 5650)),     # thin: centred on its middle
    (5000, 6000, (5000, 6000)),     # exactly the minimum: unchanged
    (5000, 9000, (5000, 9000)),
])
def test_widen_thin_layer(base, top, expected):
    assert thin_layers.widen_thin_layer(base, top) == expected


def _caf_peak(peak_pct: float) -> list[PressureLevelData]:
    """A single-level deck at 700 hPa (~9,840 ft) between clear levels."""
    rows = ((1000, 100), (850, 1500), (700, 3000), (600, 4200), (500, 5600))
    return [PressureLevelData(pressure_hpa=p, geopotential_height_m=gh, temperature_c=-5.0,
                              cloud_area_fraction_pct=peak_pct if p == 700 else 0.0)
            for p, gh in rows]


@pytest.mark.parametrize("peak", [12.5, 25.0, 50.0, 87.5])
def test_deck_flat_on_a_category_threshold_is_kept_at_the_thin_layer_thickness(peak, caplog):
    with caplog.at_level(logging.WARNING):
        layers = build_nwp_cloud_layers_from_fraction(_caf_peak(peak))
    assert len(layers) == 1
    deck = layers[0]
    level_ft = 3000 * 3.28084
    assert (deck.base_ft, deck.top_ft) == (round(level_ft - 500), round(level_ft + 500))
    assert deck.thickness_ft == 1000
    assert 700 < deck.base_pressure_hpa < 850 and 600 < deck.top_pressure_hpa < 700
    assert deck.mean_cloud_cover_pct == peak
    assert "degenerate" not in caplog.text


def test_deck_just_above_the_threshold_keeps_its_crossing_geometry():
    deck = build_nwp_cloud_layers_from_fraction(_caf_peak(13.0))[0]
    assert deck.thickness_ft < 1000  # unchanged: only flat decks are widened


def test_a_level_between_denser_decks_does_not_collapse():
    # The crossing toward a denser neighbour lies strictly inside the segment.
    levels = _caf_peak(50.0)
    for lv in levels:
        if lv.pressure_hpa in (850, 600):
            lv.cloud_area_fraction_pct = 90.0
    layers = build_nwp_cloud_layers_from_fraction(levels)
    bkn = [lyr for lyr in layers if lyr.mean_cloud_cover_pct == 50.0]
    assert len(bkn) == 1 and bkn[0].thickness_ft > 0


# ── Every user follows the one convention ────────────────────────────────


def _dl(alt_ft: float) -> DerivedLevel:
    return DerivedLevel(pressure_hpa=700, altitude_ft=alt_ft, temperature_c=-6.0, dewpoint_c=-7.0)


def test_every_thin_layer_follows_the_constant(monkeypatch):
    monkeypatch.setattr(thin_layers, "MIN_LAYER_HALF_THICKNESS_FT", 750.0)
    lv = _dl(9843.0)

    ogimet = _build_zone_simple([(lv, IcingType.MIXED, IcingRisk.LIGHT, 40.0)])
    assert ogimet.top_ft - ogimet.base_ft == 1500

    sfip = _build_sfip_zone([(lv, IcingType.MIXED, IcingRisk.LIGHT, 40.0)])
    assert sfip.top_ft - sfip.base_ft == 1500

    deck = build_nwp_cloud_layers_from_fraction(_caf_peak(25.0))[0]
    assert deck.thickness_ft == 1500


def test_no_other_module_keeps_its_own_half_thickness():
    src = Path(thin_layers.__file__).resolve().parents[2]
    own = re.compile(r"^\s*[A-Z_]*HALF_THICKNESS[A-Z_]*\s*=", re.M)
    copies = [str(f.relative_to(src)) for f in src.rglob("*.py")
              if f.name != "thin_layers.py" and own.search(f.read_text())]
    assert copies == []
