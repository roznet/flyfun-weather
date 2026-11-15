"""Unit tests for the SLD vs PIREP hindcast study script (#1000).

The script lives in ``scripts/`` (not a package), so it is loaded by path.
All data is fictional; nothing here touches the network.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sld_pirep_hindcast.py"
_spec = importlib.util.spec_from_file_location("sld_pirep_hindcast", _PATH)
hc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = hc  # dataclasses resolve their module through sys.modules
_spec.loader.exec_module(hc)


# ── Truth classes ────────────────────────────────────────────────────────


@pytest.mark.parametrize("icing, report, expected", [
    ("LGT RIME", "ZZZ UA /OV ZZZ/FL080/IC LGT RIME", "rime_lgt"),
    ("LT RM", "", "rime_lgt"),
    ("TRACE RME", "", "rime_lgt"),
    ("MOD RIME", "", "rime_mod"),
    ("MOD-SEV RIME", "", "rime_mod"),
    ("LGT CLR", "", "clr_mxd_lgt"),
    ("LGT MIXED", "", "clr_mxd_lgt"),
    ("MOD MIX", "", "clr_mxd_mod"),
    ("LGT MOD CLR", "", "clr_mxd_mod"),
    ("MOD MX", "", "clr_mxd_mod"),
    ("TRACE", "", "icing_untyped"),
    ("MOD-SEV", "", "icing_untyped"),
    ("NEG", "", "neg"),
    ("NEG", "ZZZ UA /WX FZ RA/IC NEG", "neg"),           # weather seen, no ice
    ("LGT CLR", "ZZZ UA /IC LGT CLR/RM FZRA", "fz_remark"),
    ("MOD RIME", "ZZZ UA /IC MOD RIME/RM SLD OBS", "fz_remark"),
    ("ZTLFD-41", "", None),                               # facility code, not icing
    (None, "ZZZ UA /RM FZDZ", None),                      # no icing group at all
    ("None", "", None),
])
def test_classify_truth(icing, report, expected):
    assert hc.classify_truth(icing, report) == expected


_CSV_HEADER = "VALID,URGENT,AIRCRAFT,REPORT,ICING,TURBULENCE,ATRCC,PRODUCT_ID,FL,LAT,LON\n"


def test_parse_pirep_csv_filters_and_dedupes():
    rows = [
        # kept
        "202501061200,F,ZZ01,ZZZ UA /IC MOD MIX,MOD MIX,None,ZKC,P1,6000,39.0,-95.0",
        # same report through a second product → duplicate
        "202501061200,F,ZZ01,ZZZ UA /IC MOD MIX,MOD MIX,None,ZKC,P2,6000,39.0,-95.0",
        # no icing information → dropped
        "202501061200,F,ZZ02,ZZZ UA /TB LGT,None,LGT,ZKC,P1,8000,39.0,-95.0",
        # outside CONUS (Alaska) → dropped
        "202501061200,F,ZZ03,ZZZ UA /IC LGT RIME,LGT RIME,None,ZAN,P1,3000,58.3,-134.5",
        # above the altitude cap → dropped
        "202501061200,F,ZZ04,ZZZ UA /IC LGT RIME,LGT RIME,None,ZKC,P1,31000,39.0,-95.0",
        # malformed position → dropped
        "202501061200,F,ZZ05,ZZZ UA,LGT RIME,None,ZKC,P1,5000,,",
    ]
    n_rows, kept = hc.parse_pirep_csv(_CSV_HEADER + "\n".join(rows) + "\n")
    assert n_rows == 6
    assert len(kept) == 1
    p = kept[0]
    assert (p.truth, p.alt_ft, p.aircraft) == ("clr_mxd_mod", 6000, "ZZ01")
    assert p.valid == datetime(2025, 1, 6, 12, 0, tzinfo=UTC)


# ── Run / lead and byte ranges ───────────────────────────────────────────


@pytest.mark.parametrize("hour, run_hour, lead", [
    (0, 18, 6), (3, 0, 3), (6, 0, 6), (9, 6, 3), (12, 6, 6), (21, 18, 3),
])
def test_run_for_valid(hour, run_hour, lead):
    run, got_lead = hc.run_for_valid(datetime(2025, 1, 6, hour, tzinfo=UTC))
    assert (run.hour, got_lead) == (run_hour, lead)


def test_merge_ranges():
    assert hc.merge_ranges([(0, 9), (10, 19), (30, 39)]) == [(0, 19), (30, 39)]
    assert hc.merge_ranges([(30, 39), (0, 9)], max_gap=20) == [(0, 39)]
    assert hc.merge_ranges([(0, 9), (10, None)]) == [(0, None)]


_GFS_IDX = """\
1:0:d=2025010600:TMP:250 mb:6 hour fcst:
2:100:d=2025010600:TMP:850 mb:6 hour fcst:
3:200:d=2025010600:RWMR:850 mb:6 hour fcst:
4:300:d=2025010600:UGRD:850 mb:6 hour fcst:
5:400:d=2025010600:CFRZR:surface:6 hour fcst:
6:500:d=2025010600:CFRZR:surface:0-6 hour ave fcst:
7:600:d=2025010600:TMP:2 m above ground:6 hour fcst:
8:700:d=2025010600:PRATE:surface:0-6 hour ave fcst:
9:800:d=2025010600:PRATE:surface:6 hour fcst:
"""


def test_plan_gfs_picks_levels_and_instantaneous_surface():
    ranges = hc.plan_gfs(_GFS_IDX)
    # TMP 850, RWMR 850 (not TMP 250 above MIN_LEVEL_HPA, not UGRD),
    # instantaneous CFRZR (not the average), 2 m TMP, instantaneous PRATE (EOF).
    assert ranges == [(100, 199), (200, 299), (400, 499), (600, 699), (800, None)]


def test_plan_ecmwf():
    lines = [
        {"param": "t", "levtype": "pl", "levelist": "850", "_offset": 0, "_length": 10},
        {"param": "t", "levtype": "pl", "levelist": "250", "_offset": 10, "_length": 10},
        {"param": "u", "levtype": "pl", "levelist": "850", "_offset": 20, "_length": 10},
        {"param": "ptype", "levtype": "sfc", "_offset": 30, "_length": 10},
        {"param": "msl", "levtype": "sfc", "_offset": 40, "_length": 10},
    ]
    text = "\n".join(json.dumps(d) for d in lines)
    assert hc.plan_ecmwf(text) == [(0, 9), (30, 39)]


# ── Grid ─────────────────────────────────────────────────────────────────

GFS_GRID = hc.Grid(lat0=90.0, lon0=0.0, dlat=0.25, dlon=0.25, ni=1440, nj=721, j_positive=False)
EC_GRID = hc.Grid(lat0=90.0, lon0=-180.0, dlat=0.25, dlon=0.25, ni=1440, nj=721, j_positive=False)


@pytest.mark.parametrize("grid", [GFS_GRID, EC_GRID])
def test_grid_index_round_trip(grid):
    iy, ix = grid.index(39.1, -95.1)
    lat, lon = grid.latlon(iy, ix)
    assert abs(lat - 39.1) <= 0.125 and abs(lon - -95.1) <= 0.125
    cells = grid.neighbourhood(39.1, -95.1)
    assert len(cells) == 9 and (iy, ix) in cells


def test_grid_index_conventions_differ():
    assert GFS_GRID.index(0.0, -95.0) == (360, 1060)   # 265°E on a 0..360 axis
    assert EC_GRID.index(0.0, -95.0) == (360, 340)     # -95 on a -180..180 axis


# ── Signals ──────────────────────────────────────────────────────────────


def _column(levels: dict, sfc: dict | None = None) -> dict:
    return {"levels": {str(p): v for p, v in levels.items()}, "sfc": sfc or {}}


def _lv(t_c: float, gh_m: float, qr=None, qs=None) -> dict:
    d = {"t": t_c + hc.T0_K, "gh": gh_m, "r": 95.0}
    if qr is not None:
        d["qr"] = qr
    if qs is not None:
        d["qs"] = qs
    return d


def test_supercooled_rain_at_altitude():
    col = _column({
        1000: _lv(2.0, 100),
        925: _lv(-3.0, 760, qr=5e-6, qs=1e-7),     # ~2,500 ft, supercooled rain
        850: _lv(-6.0, 1500, qr=0.0, qs=2e-5),
        700: _lv(-12.0, 3000, qr=0.0),
    }, {"sp": 101000.0})
    at = hc.column_signals(col, alt_ft=2500)
    assert at["scr_alt"] is True and at["scr_alt_liquid"] is True and at["scr_column"] is True
    high = hc.column_signals(col, alt_ft=9800)
    assert high["scr_alt"] is False and high["scr_column"] is True


def test_supercooled_rain_wet_snow_fails_liquid_fraction():
    col = _column({925: _lv(-1.0, 760, qr=2e-6, qs=8e-6), 850: _lv(-4.0, 1500, qr=0.0)},
                  {"sp": 101000.0})
    sig = hc.column_signals(col, alt_ft=2500)
    assert sig["scr_alt"] is True and sig["scr_alt_liquid"] is False


def test_below_ground_levels_ignored():
    # Ground at 850 hPa: the 925 hPa supercooled rain is the model's extrapolation.
    col = _column({925: _lv(-1.0, 760, qr=5e-6), 850: _lv(-4.0, 1500, qr=0.0),
                   700: _lv(-10.0, 3000, qr=0.0)}, {"sp": 85000.0})
    assert hc.column_signals(col, alt_ft=5000)["scr_column"] is False


def test_signals_absent_when_model_lacks_inputs():
    col = _column({850: _lv(-4.0, 1500), 700: _lv(-10.0, 3000)}, {"sp": 101000.0})
    sig = hc.column_signals(col, alt_ft=5000)
    assert sig["scr_alt"] is None and sig["cat_fzra_pl"] is None and sig["ptype_fz"] is None


@pytest.mark.parametrize("ptype, rate_kg_m2_s, fz, fzdz", [
    (3.0, 1e-5, True, False),     # freezing rain, 0.036 mm/h
    (12.0, 1e-5, True, True),     # freezing drizzle
    (12.0, 1e-6, False, False),   # 0.0036 mm/h: below the 0.01 mm/h gate
    (1.0, 1e-4, False, False),    # rain
])
def test_ptype_gate(ptype, rate_kg_m2_s, fz, fzdz):
    col = _column({850: _lv(-4.0, 1500)}, {"ptype": ptype, "prate": rate_kg_m2_s})
    sig = hc.column_signals(col, alt_ft=5000)
    assert (sig["ptype_fz"], sig["ptype_fzdz"]) == (fz, fzdz)


def test_categorical_flags():
    col = _column({850: _lv(-4.0, 1500)}, {"cfrzr": 1.0, "cicep": 0.0})
    assert hc.column_signals(col, alt_ft=5000)["cat_fzra_pl"] is True


def test_levels_near_falls_back_to_bracketing_pair():
    levels = hc.column_levels(_column({850: _lv(-4.0, 1500), 700: _lv(-10.0, 3000)}))
    near = hc.levels_near(levels, 7500)   # between ~4,900 ft and ~9,800 ft
    assert [lv.p for lv in near] == [850, 700]
    # 1,500 m = 4,921 ft, 3,000 m = 9,843 ft → 7,500 ft is 52.4 % of the way up.
    assert hc.temperature_at(levels, 7500) == pytest.approx(-7.14, abs=0.01)
