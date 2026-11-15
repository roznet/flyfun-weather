"""Unit tests for the ECMWF-at-US-PIREPs icing collector (#1003).

The script lives in ``scripts/`` (not a package), so it is loaded by path.
All data is fictional (ZZ aircraft, made-up positions); nothing touches the
network. GRIB files are written with eccodes' own samples.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import zlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location("icing_pirep_collector", _SCRIPTS / "icing_pirep_collector.py")
ic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ic
_spec.loader.exec_module(ic)
hc = ic.hc

T0 = datetime(2026, 1, 10, 0, tzinfo=UTC)


def _awc(**kw) -> dict:
    """An AWC JSON report with every field empty, as the API sends them."""
    row = {"receiptTime": "2026-01-10T14:20:00.000Z", "obsTime": int((T0 + timedelta(hours=14, minutes=10)).timestamp()),
           "qcField": 0, "icaoId": "", "acType": "ZZ01", "lat": 40.0, "lon": -100.0,
           "fltLvl": 120, "fltLvlType": "OTHER", "temp": -8, "pirepType": "PIREP",
           "rawOb": "ZZZ UA /OV ZZZ/FL120/TP ZZ01", "brkAction": "", "vertGust": None}
    for p in ("icg", "tb"):
        for i in (1, 2):
            row.update({f"{p}Int{i}": "", f"{p}Type{i}": "", f"{p}Bas{i}": None, f"{p}Top{i}": None})
            if p == "tb":
                row[f"tbFreq{i}"] = ""
    row.update(kw)
    return row


# ── AWC parsing ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("kw, truth", [
    ({"icgInt1": "LGT", "icgType1": "RIME"}, "rime_lgt"),
    ({"icgInt1": "MOD", "icgType1": "MIXED"}, "clr_mxd_mod"),
    ({"icgInt1": "LGT-MOD", "icgType1": "CLEAR"}, "clr_mxd_mod"),
    ({"icgInt1": "TRC-LGT", "icgType1": ""}, "icing_untyped"),
    ({"icgInt1": "NEG"}, "neg"),
    ({"icgInt1": "NEGclr"}, "neg"),
    ({"icgInt1": "MOD", "icgType1": "CLEAR", "rawOb": "ZZZ UA /IC MOD CLR/RM FZDZ"}, "fz_remark"),
    ({"tbInt1": "LGT", "tbFreq1": "CONT"}, ic.NO_ICING),
])
def test_parse_awc_truth(kw, truth):
    r = ic.parse_awc_report(_awc(**kw))
    assert r is not None and r.truth == truth


def test_parse_awc_layers_and_units():
    r = ic.parse_awc_report(_awc(fltLvl=41, icgInt1="MOD", icgType1="MIXED", icgBas1=41, icgTop1=60,
                                 tbInt1="LGT", tbFreq1="OCNL"))
    assert r.alt_ft == 4100
    assert r.icing == "MOD MIXED"
    assert r.icing_layers == [{"int": "MOD", "type": "MIXED", "base_ft": 4100, "top_ft": 6000}]
    assert r.turb_layers[0]["freq"] == "OCNL"
    assert r.valid == T0 + timedelta(hours=14, minutes=10)


@pytest.mark.parametrize("kw", [
    {},                                                    # no icing, no turbulence
    {"icgInt1": "LGT", "fltLvl": 0, "fltLvlType": "DURD"},  # no altitude
    {"icgInt1": "LGT", "fltLvl": 300},                     # above FL250
    {"icgInt1": "LGT", "lat": 61.2, "lon": -150.0},        # Alaska
    {"icgInt1": "LGT", "obsTime": None},                   # malformed
])
def test_parse_awc_drops(kw):
    assert ic.parse_awc_report(_awc(**kw)) is None


class _Resp:
    def __init__(self, rows):
        self.status_code = 200
        self._rows = rows
        self.content = json.dumps(rows).encode()

    def json(self):
        return self._rows

    def raise_for_status(self):
        pass


def test_fetch_awc_splits_full_answers():
    calls = []

    def get(url, params, timeout, headers):
        calls.append((params["date"], params["age"]))
        n = ic.AWC_MAX_ROWS if params["age"] == "1" else 5
        return _Resp([_awc(icgInt1="LGT")] * n)

    rows, n_calls = ic.fetch_awc_window(None, T0 + timedelta(hours=14), 1.0, get)
    assert n_calls == 3 and len(rows) == 10
    assert calls == [("20260110_1400", "1"), ("20260110_1400", "0.5"), ("20260110_1330", "0.5")]


def test_store_and_backfill_idempotent(tmp_path):
    conn = ic.open_db(tmp_path)
    rows = [_awc(icgInt1="LGT", icgType1="RIME"), _awc(icgInt1="LGT", icgType1="RIME"),  # duplicate
            _awc(tbInt1="MOD", acType="ZZ02")]

    def get(url, params, timeout, headers):
        return _Resp(rows)

    end = T0 + timedelta(hours=15)
    assert ic.cmd_pireps(conn, None, end - timedelta(hours=1), end, force=False, get=get) == 2
    assert ic.cmd_pireps(conn, None, end - timedelta(hours=1), end, force=False, get=get) == 0
    assert ic.cmd_pireps(conn, None, end - timedelta(hours=1), end, force=True, get=get) == 0
    assert conn.execute("SELECT COUNT(*) FROM pireps").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM pirep_details").fetchone()[0] == 2
    assert conn.execute("SELECT n_calls FROM pirep_hours").fetchone()[0] == 1


# ── Run choice ───────────────────────────────────────────────────────────


def test_nearest_hour():
    assert ic.nearest_hour(T0 + timedelta(minutes=29)) == T0
    assert ic.nearest_hour(T0 + timedelta(minutes=30)) == T0 + timedelta(hours=1)


def test_candidate_runs_leads():
    v = T0 + timedelta(hours=12)  # 12Z: lead 0 is not allowed, so 00Z at +12
    assert ic.candidate_runs(v, (0, 12), 12) == [T0]
    v = T0 + timedelta(hours=10)
    assert ic.candidate_runs(v, (0, 6, 12, 18), 12) == [T0 + timedelta(hours=6), T0]


def _disk(runs: dict) -> ic.RunFiles:
    return ic.RunFiles({run: {"a2": {v: Path("x") for v in valids}} for run, valids in runs.items()})


def test_choose_run_states():
    v = T0 + timedelta(hours=10)
    cyc = (0, 12)
    on = _disk({T0: [v]})
    assert ic.choose_run(v, cyc, 12, on, set(), T0 + timedelta(hours=9)) == ("ok", T0)
    # Not landed yet, still time: wait.
    assert ic.choose_run(v, cyc, 12, _disk({}), set(), T0 + timedelta(hours=9)) == ("wait", None)
    # Past the wait: missing, or pruned when it was seen before.
    late = T0 + timedelta(hours=ic.RUN_WAIT_H + 1)
    assert ic.choose_run(v, cyc, 12, _disk({}), set(), late) == ("run_missing", None)
    assert ic.choose_run(v, cyc, 12, _disk({}), {T0}, late) == ("pruned", None)
    # Run on disk without that step.
    assert ic.choose_run(v, cyc, 12, _disk({T0: []}), set(), late) == ("step_missing", None)


def test_choose_run_freshest_wins_then_falls_back():
    v = T0 + timedelta(hours=10)
    cyc = (0, 6, 12, 18)
    six = T0 + timedelta(hours=6)
    both = _disk({T0: [v], six: [v]})
    assert ic.choose_run(v, cyc, 12, both, set(), six + timedelta(hours=9)) == ("ok", six)
    # 06Z not there yet but can still land: wait rather than take 00Z at +10.
    only00 = _disk({T0: [v]})
    assert ic.choose_run(v, cyc, 12, only00, set(), six + timedelta(hours=9)) == ("wait", None)
    # 06Z never came: fall back to 00Z.
    assert ic.choose_run(v, cyc, 12, only00, set(), six + timedelta(hours=ic.RUN_WAIT_H + 1)) == ("ok", T0)


# ── Grids ────────────────────────────────────────────────────────────────


def test_msg_grid_regional_and_wrap():
    # Europe-style GRIB2 grid crossing Greenwich (lon0 = 342.5 = -17.5).
    eu = ic.MsgGrid(lat0=59.5, lon0=342.5, dlat=0.25, dlon=0.25, ni=183, nj=99, j_positive=False)
    assert eu.flat_index(59.5, -17.5) == 0
    assert eu.flat_index(59.5, 0.0) == 70
    assert eu.flat_index(40.0, -100.0) is None
    # US area as GRIB1 writes it (negative longitudes).
    us = ic.MsgGrid(lat0=55.0, lon0=-128.0, dlat=0.25, dlon=0.25, ni=229, nj=141, j_positive=False)
    assert us.flat_index(55.0, -128.0) == 0
    assert us.flat_index(40.0, -100.0) == 60 * 229 + 112
    assert us.flat_index(40.0, 232.0 + 28.0) == 60 * 229 + 112  # same point, 0..360 convention
    glob = ic.MsgGrid(lat0=90.0, lon0=0.0, dlat=0.25, dlon=0.25, ni=1440, nj=721, j_positive=False)
    assert glob.flat_index(0.0, -0.1) == 360 * 1440 + 0  # wraps to 360 ≡ 0


def test_conus_coverage():
    us = ic.MsgGrid(lat0=55.0, lon0=-128.0, dlat=0.25, dlon=0.25, ni=249, nj=141, j_positive=False)
    eu = ic.MsgGrid(lat0=59.5, lon0=342.5, dlat=0.25, dlon=0.25, ni=183, nj=99, j_positive=False)
    assert ic.conus_coverage(us) == 1.0
    assert ic.conus_coverage(eu) == 0.0


# ── End to end on synthetic GRIB ─────────────────────────────────────────

eccodes = pytest.importorskip("eccodes")

US_AREA = dict(lat_first=50.0, lat_last=24.0, lon_first=-126.0, lon_last=-66.0)  # 0.5° to keep it small
EU_AREA = dict(lat_first=59.5, lat_last=35.0, lon_first=-17.5, lon_last=28.0)


def _write(fh, sample: str, area: dict, short: str, value: float, level: int | None = None):
    import numpy as np

    gid = eccodes.codes_grib_new_from_samples(sample)
    d = 0.5
    ni = round((area["lon_last"] - area["lon_first"]) / d) + 1
    nj = round((area["lat_first"] - area["lat_last"]) / d) + 1
    for k, v in (("Ni", ni), ("Nj", nj),
                 ("latitudeOfFirstGridPointInDegrees", area["lat_first"]),
                 ("latitudeOfLastGridPointInDegrees", area["lat_last"]),
                 ("longitudeOfFirstGridPointInDegrees", area["lon_first"] % 360),
                 ("longitudeOfLastGridPointInDegrees", area["lon_last"] % 360),
                 ("iDirectionIncrementInDegrees", d), ("jDirectionIncrementInDegrees", d)):
        eccodes.codes_set(gid, k, v)
    eccodes.codes_set(gid, "shortName", short)
    if level is not None:
        eccodes.codes_set(gid, "level", level)
    eccodes.codes_set_values(gid, np.full(ni * nj, value))
    eccodes.codes_write(gid, fh)
    eccodes.codes_release(gid)


def _name(part: str, run: datetime, valid: datetime) -> str:
    step = int((valid - run).total_seconds() // 3600)
    return f"brg_{part}_ifs-ens-cf_od_oper_fc_{run:%Y%m%dT%H%M%SZ}_{valid:%Y%m%dT%H%M%SZ}_{step}"


def _make_run(d: Path, run: datetime, valid: datetime, a2_us: bool = True, a1_offset: int = 0):
    with open(d / _name("a2", run, valid), "wb") as fh:
        for area in ([EU_AREA, US_AREA] if a2_us else [EU_AREA]):
            # A warm-to-cold column, 1000-500 hPa, gh roughly standard.
            for p, t, gh in ((1000, 278.0, 100.0), (850, 268.0, 1500.0), (700, 262.0, 3000.0),
                             (500, 250.0, 5600.0), (100, 210.0, 16000.0)):
                _write(fh, "regular_ll_pl_grib2", area, "t", t, p)
                _write(fh, "regular_ll_pl_grib2", area, "gh", gh, p)
                _write(fh, "regular_ll_pl_grib2", area, "clwc", 2e-4, p)
    sfc_valid = valid + timedelta(hours=a1_offset)
    with open(d / _name("a1", run, sfc_valid), "wb") as fh:
        _write(fh, "regular_ll_sfc_grib2", US_AREA, "2t", 276.0)
        _write(fh, "regular_ll_sfc_grib2", US_AREA, "ptype", 12.0)
        _write(fh, "regular_ll_sfc_grib2", US_AREA, "tprate", 1e-4)  # 0.36 mm/h
        _write(fh, "regular_ll_sfc_grib2", US_AREA, "sp", 101000.0)
    old = time.time() - 3600
    for f in d.iterdir():
        os.utime(f, (old, old))


def _add_report(conn, valid: datetime, lat: float, lon: float, alt: int = 8000, truth="rime_lgt"):
    with conn:
        cur = conn.execute("INSERT INTO pireps (valid, lat, lon, alt_ft, aircraft, icing, report, truth)"
                           " VALUES (?,?,?,?,?,?,?,?)", (ic._iso(valid), lat, lon, alt, "ZZ01", "LGT RIME", "", truth))
    return cur.lastrowid


def test_sample_end_to_end(tmp_path):
    grib = tmp_path / "grib"
    grib.mkdir()
    valid = T0 + timedelta(hours=9)
    _make_run(grib, T0, valid, a1_offset=0)
    conn = ic.open_db(tmp_path / "out")
    us = _add_report(conn, valid + timedelta(minutes=10), 40.1, -100.2)
    eu_only = _add_report(conn, valid - timedelta(minutes=20), 48.0, 2.0)   # Paris: a2 EU, no a1 there
    nowhere = _add_report(conn, valid, 10.0, -40.0)                          # mid-Atlantic
    later = _add_report(conn, valid + timedelta(hours=5), 40.0, -100.0)      # +14 h: needs the 12Z run

    totals = ic.cmd_sample(conn, grib, (0, 12), 12, now=T0 + timedelta(hours=10))
    assert totals == {"ok": 2, "outside_grid": 1, "waiting": 1}
    rows = {pid: (status, lead) for pid, status, lead in
            conn.execute("SELECT pirep_id, status, lead_h FROM samples")}
    assert rows[us] == ("ok", 9) and rows[eu_only] == ("ok", 9) and rows[nowhere][0] == "outside_grid"
    assert later not in rows

    # The stored column is on the global 0.25° lattice and carries a2 + a1 fields.
    iy, ix = ic.CANONICAL_GRID.index(40.1, -100.2)
    blob = conn.execute("SELECT payload FROM columns WHERE valid=? AND iy=? AND ix=?",
                        (ic._iso(valid), iy, ix)).fetchone()[0]
    payload = json.loads(zlib.decompress(blob))
    assert set(payload["levels"]) == {"1000", "850", "700", "500"}  # 100 hPa dropped
    assert payload["levels"]["850"] == {"t": 268.0, "gh": 1500.0, "clwc": 0.0002}
    assert payload["sfc"] == {"t2m": 276.0, "ptype": 12.0, "prate": 0.0001, "sp": 101000.0}
    assert conn.execute("SELECT run, lead_h, n_columns FROM fetches WHERE valid=?",
                        (ic._iso(valid),)).fetchone() == (ic._iso(T0), 9, 18)

    # The #1000 scoring runs on it unchanged (hourly valid times).
    res = hc.score(conn, ["ecmwf"], None, None, subzero=True, with_product=True, step_h=1)
    assert res["scored"]["ecmwf"] == 2
    assert res["results"]["ecmwf"]["ptype_fz"]["rime_lgt"] == [1, 1]  # US cell has FZDZ, EU none

    # Second pass: nothing new; the 12Z run never comes, so the late report is run_missing.
    totals = ic.cmd_sample(conn, grib, (0, 12), 12, now=T0 + timedelta(hours=12 + ic.RUN_WAIT_H + 1))
    assert totals == {"run_missing": 1}


def test_sample_sfc_only_and_a1_offset(tmp_path):
    grib = tmp_path / "grib"
    grib.mkdir()
    valid = T0 + timedelta(hours=4)
    _make_run(grib, T0, valid, a2_us=False, a1_offset=-1)  # a1 only at 03Z
    conn = ic.open_db(tmp_path / "out")
    pid = _add_report(conn, valid, 40.0, -100.0)
    assert ic.cmd_sample(conn, grib, (0, 12), 12, now=T0 + timedelta(hours=10)) == {"sfc_only": 1}
    payload = json.loads(zlib.decompress(conn.execute("SELECT payload FROM columns LIMIT 1").fetchone()[0]))
    assert payload["sfc_offset_h"] == -1 and payload["levels"] == {}
    assert conn.execute("SELECT status FROM samples WHERE pirep_id=?", (pid,)).fetchone()[0] == "sfc_only"


def test_turbulence_only_reports_are_not_scored(tmp_path):
    grib = tmp_path / "grib"
    grib.mkdir()
    valid = T0 + timedelta(hours=9)
    _make_run(grib, T0, valid)
    conn = ic.open_db(tmp_path / "out")
    _add_report(conn, valid, 40.0, -100.0, truth=ic.NO_ICING)
    ic.cmd_sample(conn, grib, (0, 12), 12, now=T0 + timedelta(hours=10))
    res = hc.score(conn, ["ecmwf"], date(2026, 1, 10), date(2026, 1, 10), subzero=False,
                   with_product=False, step_h=1)
    assert res["scored"]["ecmwf"] == 0


def test_status_and_coverage_print(tmp_path, capsys):
    grib = tmp_path / "grib"
    grib.mkdir()
    valid = T0 + timedelta(hours=9)
    _make_run(grib, T0, valid)
    conn = ic.open_db(tmp_path / "out")
    _add_report(conn, valid, 40.0, -100.0)
    ic.cmd_sample(conn, grib, (0, 12), 12, now=T0 + timedelta(hours=10))
    ic.cmd_status(conn)
    ic.cmd_coverage(grib, None)
    out = capsys.readouterr().out
    assert "ok by lead: +9h 1" in out
    assert "CONUS covered 100%" in out and "CONUS covered 0%" in out


def test_later_reports_stay_on_the_run_already_used(tmp_path):
    grib = tmp_path / "grib"
    grib.mkdir()
    valid = T0 + timedelta(hours=9)
    _make_run(grib, T0, valid)
    conn = ic.open_db(tmp_path / "out")
    _add_report(conn, valid, 40.0, -100.0)
    ic.cmd_sample(conn, grib, (0, 12), 12, now=T0 + timedelta(hours=10))
    late = _add_report(conn, valid + timedelta(minutes=5), 41.0, -99.0)
    for f in grib.iterdir():
        f.unlink()  # the run is pruned before the late report is sampled
    assert ic.cmd_sample(conn, grib, (0, 12), 12, now=T0 + timedelta(hours=30)) == {"pruned": 1}
    assert conn.execute("SELECT status, run FROM samples WHERE pirep_id=?", (late,)).fetchone() == ("pruned", ic._iso(T0))


def test_unreadable_icing_group_is_not_turbulence_only():
    r = ic.parse_awc_report(_awc(icgInt1="XYZ", tbInt1="LGT"))
    assert r.truth == ic.ICING_UNCLASSIFIED
    assert ic.parse_awc_report(_awc(icgInt1="XYZ")) is None


def test_decode_errors_are_retried_next_pass(tmp_path):
    grib = tmp_path / "grib"
    grib.mkdir()
    valid = T0 + timedelta(hours=9)
    _make_run(grib, T0, valid)
    a2 = grib / _name("a2", T0, valid)
    good = a2.read_bytes()
    a2.write_bytes(b"GRIB" + b"\0" * 64)  # caught mid-sync
    old = time.time() - 3600
    os.utime(a2, (old, old))
    conn = ic.open_db(tmp_path / "out")
    pid = _add_report(conn, valid, 40.0, -100.0)
    assert ic.cmd_sample(conn, grib, (0, 12), 12, now=T0 + timedelta(hours=10)) == {"error": 1}
    a2.write_bytes(good)
    os.utime(a2, (old, old))
    assert ic.cmd_sample(conn, grib, (0, 12), 12, now=T0 + timedelta(hours=11)) == {"ok": 1}
    assert conn.execute("SELECT status FROM samples WHERE pirep_id=?", (pid,)).fetchone()[0] == "ok"


def test_lock_skips_second_run_and_status_reports_it(tmp_path):
    with ic._lock(tmp_path) as first:
        assert first
        with ic._lock(tmp_path) as second:
            assert not second
        assert ic.lock_state(tmp_path).startswith("lock: HELD")
    assert ic.lock_state(tmp_path) == "lock: free"
