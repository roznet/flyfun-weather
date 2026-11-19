"""Unit tests for the PIREP scoring of the #1003 collector.

The scripts live in ``scripts/`` (not a package), so they are loaded by path.
All data is fictional (ZZ aircraft, made-up positions and columns).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
import zlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location("icing_pirep_collector", _SCRIPTS / "icing_pirep_collector.py")
ic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ic
_spec.loader.exec_module(ic)
import icing_pirep_score as sc  # noqa: E402

hc = ic.hc


@pytest.fixture(autouse=True)
def _restore_logging():
    """``ic.main`` configures logging for the CLI; keep that out of other tests."""
    loggers = [logging.getLogger(n) for n in ("", "urllib3", "metpy", "weatherbrief")]
    saved = [(lg, lg.level, list(lg.handlers)) for lg in loggers]
    yield
    for lg, level, handlers in saved:
        lg.setLevel(level)
        lg.handlers[:] = handlers
VALID = datetime(2026, 1, 10, 14, tzinfo=UTC)
#            hPa: (T °C, gh m)
_PROFILE = {1000: (6.0, 100.0), 950: (3.0, 540.0), 925: (1.5, 770.0), 900: (0.0, 1000.0), 850: (-3.0, 1460.0),
            800: (-6.0, 1950.0), 700: (-11.0, 3010.0), 600: (-18.0, 4200.0), 500: (-27.0, 5570.0),
            400: (-39.0, 7190.0), 300: (-52.0, 9160.0), 250: (-58.0, 10360.0), 200: (-60.0, 11780.0)}


def _column(cloudy: bool) -> dict:
    levels = {}
    for p, (t, gh) in _PROFILE.items():
        wet = cloudy and 500 <= p <= 900
        levels[str(p)] = {"t": t + hc.T0_K, "gh": gh, "r": 98.0 if wet else 30.0, "u": 5.0, "v": 5.0, "w": -0.1,
                          "cc": 0.95 if wet else 0.0, "clwc": 2e-4 if wet else 0.0, "ciwc": 2e-5 if wet else 0.0}
    cover = 0.95 if cloudy else 0.0
    return {"levels": levels, "sfc": {"t2m": 281.0, "td2m": 276.0, "sp": 101300.0, "lcc": cover, "mcc": cover,
                                      "hcc": 0.0, "tcc": cover, "mucape": 0.0}}


def _store_column(conn, lat: float, lon: float, payload: dict) -> None:
    iy, ix = ic.CANONICAL_GRID.index(lat, lon)
    valid = ic._iso(VALID)
    with conn:
        conn.execute("INSERT OR REPLACE INTO columns VALUES (?,?,?,?,?,?,?)",
                     ("ecmwf", valid, iy, ix, lat, lon, zlib.compress(json.dumps(payload).encode())))
        conn.execute("INSERT OR REPLACE INTO fetches VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     ("ecmwf", valid, ic._iso(VALID.replace(hour=12)), 2, "ok", 0, 0.0, 1,
                      json.dumps(ic.CANONICAL_GRID.__dict__), None, valid))


_seq = iter(range(1, 10_000))


def _store_report(conn, lat: float, lon: float, alt: int, truth: str, *, fl: str = "OTHER", report: str = "ZZZ UA",
                  temp: float | None = None, icing: list | None = None, clouds: list | None = None) -> None:
    valid = ic._iso(VALID)
    with conn:
        cur = conn.execute("INSERT INTO pireps (valid, lat, lon, alt_ft, aircraft, icing, report, truth)"
                           " VALUES (?,?,?,?,?,?,?,?)", (valid, lat, lon, alt, f"ZZ{next(_seq)}", None, report, truth))
        conn.execute("INSERT INTO pirep_details VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (cur.lastrowid, "awc", valid, None, "PIREP", fl, temp, json.dumps(icing) if icing else None, None,
                      json.dumps(clouds) if clouds else None))
        conn.execute("INSERT INTO samples VALUES (?,?,?,?,?,?,?,?)",
                     (cur.lastrowid, "ecmwf", valid, ic._iso(VALID.replace(hour=12)), 2, "ok", None, valid))


# ── Where the report is ──────────────────────────────────────────────────


def _levels() -> dict:
    return _column(True)["levels"]


def test_level_report_is_at_its_altitude():
    t = sc.report_target(8000, "OTHER", "ZZZ UA /FL080", None, _levels())
    assert (t.lo_ft, t.hi_ft, t.known_altitude) == (8000, 8000, True)


@pytest.mark.parametrize("fl, report", [("DURD", "ZZZ UA"), ("OTHER", "ZZZ UA /RM DURC"), ("OTHER", "ZZZ UA /RM DURGC")])
def test_climb_or_descent_without_a_layer_has_no_known_altitude(fl, report):
    assert sc.report_target(8000, fl, report, None, _levels()).known_altitude is False


def test_remark_that_only_contains_the_letters_is_not_a_descent():
    assert sc.report_target(8000, "OTHER", "ZZZ UA /RM ENDURANCE", None, _levels()).known_altitude is True


def test_stated_layer_replaces_the_flight_level_even_in_descent():
    icing = json.dumps([{"int": "LGT", "type": "RIME", "base_ft": 12000, "top_ft": 9000}])
    t = sc.report_target(16000, "DURD", "ZZZ UA /RM DURD", icing, _levels())
    assert (t.lo_ft, t.hi_ft, t.known_altitude) == (9000, 12000, True)


def test_thick_layer_does_not_say_where_the_icing_was():
    icing = json.dumps([{"int": "LGT", "type": "RIME", "base_ft": 2000, "top_ft": 19000}])
    assert sc.report_target(16000, "OTHER", "ZZZ UA", icing, _levels()).known_altitude is False


def test_flight_levels_move_onto_the_model_height_only_from_18000_ft():
    levels = _levels()
    assert sc.model_altitude_ft(levels, 17000) == 17000
    # Colder than ISA: FL200 (~466 hPa) sits below 20,000 ft geopotential here.
    assert 18000 < sc.model_altitude_ft(levels, 20000) < 20000


def test_level_value_is_none_outside_the_stored_levels():
    assert sc.level_value(_levels(), 60000, "t") is None
    assert sc.level_value({"500": {"gh": 5500.0, "t": 250.0}}, 18000, "t") is None


# ── What the model says there ────────────────────────────────────────────


@pytest.mark.parametrize("lo, hi, expected", [
    (6000, 6000, 2),      # inside the moderate zone
    (8900, 8900, 2),      # within the tolerance above it
    (9500, 9500, sc.NO_ZONE),
    (9500, 11500, sc.NO_ZONE),
    (9500, 12000, 1),     # within the tolerance below the upper zone
    (2000, 4500, 2),      # a range reaching into it from below
    (10000, 14000, 1),
])
def test_zone_risk(lo, hi, expected):
    zones = [(5000.0, 8000.0, 2), (13000.0, 15000.0, 1)]
    assert sc.zone_risk(zones, sc.Target(lo, hi, True)) == expected


def test_baselines_read_the_range_with_the_same_tolerance():
    levels = _levels()
    assert all(sc.baseline_flags(levels, sc.Target(8000, 8000, True)).values())
    assert not any(sc.baseline_flags(_column(False)["levels"], sc.Target(8000, 8000, True)).values())


# ── Statistics ───────────────────────────────────────────────────────────


def test_skill_is_pod_minus_false_alarm_with_a_range_around_it():
    hours = [f"h{i % 12}" for i in range(120)]
    icing = [i % 2 == 0 for i in range(120)]
    flags = [(i % 2 == 0 and i % 8 != 0) or i % 10 == 1 for i in range(120)]
    s = sc.skill(flags, icing, hours)
    assert s.value == pytest.approx(s.pod - s.false_alarm)
    assert s.low <= s.value <= s.high
    assert s == sc.skill(flags, icing, hours)  # deterministic


@pytest.mark.parametrize("icing, pod, false_alarm", [([True, True], 0.5, None), ([False, False], None, 0.5)])
def test_skill_without_one_class_has_no_value(icing, pod, false_alarm):
    s = sc.skill([True, False], icing, ["h1", "h2"])
    assert (s.pod, s.false_alarm, s.value, s.low) == (pod, false_alarm, None, None)


# ── End to end ───────────────────────────────────────────────────────────


@pytest.fixture
def conn(tmp_path):
    db = ic.open_db(tmp_path / "out")
    _store_column(db, 40.0, -100.0, _column(True))
    _store_column(db, 44.0, -90.0, _column(False))
    return db


def test_score_flags_cloud_not_clear_air_and_accounts_for_every_report(conn):
    for _ in range(3):
        _store_report(conn, 40.0, -100.0, 8000, "rime_lgt")
    _store_report(conn, 40.0, -100.0, 8000, "rime_mod", report="ZZZ UA /RM DURD")  # scored, altitude unknown
    _store_report(conn, 44.0, -90.0, 8000, "neg")
    _store_report(conn, 40.0, -100.0, 1000, "rime_lgt")                            # +4 C in the model
    _store_report(conn, 40.0, -100.0, 8000, "neg", fl="GRND")
    _store_report(conn, 30.0, -80.0, 8000, "rime_lgt")                             # no column there
    _store_report(conn, 40.0, -100.0, 8000, ic.NO_ICING)                           # not a candidate

    data = sc.collect(conn)

    assert sum(r.known_altitude for r in data.reports) == 4
    assert len(data.reports) == 5
    assert dict(data.skipped) == {"model not at -20..0 C": 1, "on the ground": 1, "no model column": 1}
    assert len(data.reports) + sum(data.skipped.values()) == 8
    cloudy = next(r for r in data.reports if r.truth == "rime_lgt")
    clear = next(r for r in data.reports if r.truth == "neg")
    assert max(cloudy.nearest.values()) >= sc.RISK_RANK["light"]
    assert all(v < sc.RISK_RANK["light"] for v in clear.nearest.values())
    assert all(cloudy.base_nearest.values()) and not any(clear.base_nearest.values())


def test_analysis_failure_is_counted_not_dropped(conn, monkeypatch):
    monkeypatch.setattr(sc, "column_zones", lambda payload, valid: None)
    _store_report(conn, 40.0, -100.0, 8000, "rime_lgt")
    data = sc.collect(conn)
    assert data.reports == [] and dict(data.skipped) == {"sounding analysis failed": 1}


def test_date_range_limits_the_score(conn):
    _store_report(conn, 40.0, -100.0, 8000, "rime_lgt")
    assert len(sc.collect(conn, start=VALID.date()).reports) == 1
    assert sc.collect(conn, end=datetime(2026, 1, 9).date()).reports == []


def test_temperature_leaves_climbs_out_and_sky_needs_a_clear_verdict(conn):
    _store_report(conn, 40.0, -100.0, 8000, ic.NO_ICING, temp=-9.0, clouds=[{"cover": "OVC"}])
    _store_report(conn, 40.0, -100.0, 8000, ic.NO_ICING, temp=-9.0, fl="DURD", clouds=[{"cover": "SCT"}])
    _store_report(conn, 44.0, -90.0, 8000, ic.SKY_ONLY, clouds=[{"cover": "CLR"}])
    data = sc.collect(conn)
    assert len(data.temps) == 1 and data.climb_descent_temps == 1
    assert sorted(data.sky) == [(False, 0.0), (True, 0.95)]


def test_score_command_prints_the_tables(tmp_path, capsys):
    db = ic.open_db(tmp_path)
    _store_column(db, 40.0, -100.0, _column(True))
    _store_column(db, 44.0, -90.0, _column(False))
    _store_report(db, 40.0, -100.0, 8000, "rime_mod", temp=-9.0, clouds=[{"cover": "OVC"}])
    _store_report(db, 44.0, -90.0, 8000, "neg", clouds=[{"cover": "CLR"}])
    db.close()

    assert ic.main(["--out", str(tmp_path), "score"]) == 0

    out = capsys.readouterr().out
    assert "2 icing or NEG reports scored" in out
    for name in (*sc.METHODS, *sc.BASELINES, "Temperature", "Sky"):
        assert name in out


def test_score_on_an_empty_database_says_so(tmp_path, capsys):
    ic.open_db(tmp_path).close()
    assert ic.main(["--out", str(tmp_path), "score"]) == 0
    assert "0 icing or NEG reports scored" in capsys.readouterr().out
