"""Forecast convective risk vs the observed radar/lightning archive (#793)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("duckdb")
pa = pytest.importorskip("pyarrow")
import pyarrow.parquet as pq  # noqa: E402

from weatherbrief.tasks.convective_verification import (  # noqa: E402
    ConvectiveScoreParams,
    score_convective,
    with_skill,
)

TS = pa.timestamp("us", tz="UTC")
H12 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
INIT = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)


def _write(path, rows, schema):
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = {name: [r.get(name) for r in rows] for name in schema.names}
    pq.write_table(pa.Table.from_pydict(cols, schema=schema), path)


SNAP_SCHEMA = pa.schema([
    ("icao", pa.string()), ("model", pa.string()),
    ("model_init_time", TS), ("forecast_hour", TS),
    ("sounding_convective_risk", pa.string()), ("cape_jkg", pa.float64()),
    ("nwp_conv_cover_pct", pa.float64()), ("nwp_conv_top_ft", pa.float64()),
])
GRID_SCHEMA = pa.schema([
    ("icao", pa.string()), ("frame_valid_time", TS), ("radius_nm", pa.float64()),
    ("insufficient_coverage", pa.bool_()), ("max_value", pa.float64()),
    ("highest_fl", pa.float64()),
])
LI_SCHEMA = pa.schema([
    ("icao", pa.string()), ("frame_valid_time", TS), ("radius_nm", pa.float64()),
    ("flash_count", pa.int32()),
])


def _snap(icao, risk, init=INIT, model="ecmwf"):
    return {"icao": icao, "model": model, "model_init_time": init,
            "forecast_hour": H12, "sounding_convective_risk": risk}


def _radar(icao, minutes, dbz, *, radius=10.0, bad=False):
    return {"icao": icao, "frame_valid_time": H12 + timedelta(minutes=minutes),
            "radius_nm": radius, "insufficient_coverage": bad, "max_value": dbz}


def _li(icao, minutes, flashes, radius=20.0):
    return {"icao": icao, "frame_valid_time": H12 + timedelta(minutes=minutes),
            "radius_nm": radius, "flash_count": flashes}


@pytest.fixture
def archives(tmp_path):
    verif, observed = tmp_path / "verification", tmp_path / "observed"
    _write(verif / "snapshots" / "2026-07-01.parquet", [
        _snap("ZZAA", "moderate"),                      # hit: 45 dBZ
        _snap("ZZAB", "low"),                           # miss: lightning only
        _snap("ZZAC", "high"),                          # false alarm
        _snap("ZZAD", "none"),                          # correct negative
        _snap("ZZAE", "high"),                          # unknown: radar coverage bad
        _snap("ZZAF", None),                            # no sounding: excluded
        _snap("ZZAG", "none"),                          # 12:30 echo is the 13:00 hour
        _snap("ZZAA", "moderate", init=INIT - timedelta(days=1)),  # D1 hit
    ], SNAP_SCHEMA)
    _write(observed / "opera_dbzh" / "2026-07-01.parquet", [
        _radar("ZZAA", 20, 45.0),
        _radar("ZZAB", 0, None),
        _radar("ZZAC", -10, 25.0),
        _radar("ZZAD", 0, None),
        _radar("ZZAD", 0, 55.0, radius=20.0),            # outside the 10 NM disc
        _radar("ZZAE", 0, 50.0, bad=True),
        _radar("ZZAG", 10, 20.0),
        _radar("ZZAG", 30, 50.0),
    ], GRID_SCHEMA)
    _write(observed / "eumetsat_li" / "2026-07-01.parquet", [
        _li("ZZAA", 0, 0), _li("ZZAB", -20, 3), _li("ZZAC", 0, 0),
        _li("ZZAD", 0, 0), _li("ZZAD", 0, 9, radius=40.0), _li("ZZAE", 0, 0),
        _li("ZZAG", 0, 0),
    ], LI_SCHEMA)
    return verif, observed


def test_contingency_per_model_and_lead_day(archives, tmp_path):
    verif, observed = archives
    pairs_out = tmp_path / "pairs.parquet"
    rows = score_convective(verif, observed, pairs_out=pairs_out)

    by_day = {r["days_out"]: r for r in rows}
    d0 = by_day[0]
    assert (d0["hits"], d0["misses"], d0["false_alarms"], d0["correct_negatives"]) == (1, 1, 1, 2)
    assert d0["n"] == 5  # ZZAE (unknown) and ZZAF (no risk) dropped
    assert d0["pod"] == pytest.approx(0.5)
    assert d0["far"] == pytest.approx(0.5)
    assert by_day[1]["hits"] == 1

    pairs = {(r["icao"], r["days_out"]): r for r in pq.read_table(pairs_out).to_pylist()}
    assert pairs[("ZZAA", 0)]["max_dbz"] == 45.0
    assert pairs[("ZZAB", 0)]["flashes"] == 3
    assert ("ZZAE", 0) not in pairs


def test_threshold_is_a_parameter(archives):
    verif, observed = archives
    rows = score_convective(
        verif, observed, ConvectiveScoreParams(risk_threshold="low", dbz_threshold=20.0),
    )
    d0 = next(r for r in rows if r["days_out"] == 0)
    # ZZAB (low) is now a hit; ZZAC's 25 dBZ and ZZAG's 20 dBZ now count as observed.
    assert (d0["hits"], d0["misses"], d0["false_alarms"], d0["correct_negatives"]) == (3, 1, 0, 1)


def test_tops_are_optional_and_joined_when_present(archives, tmp_path):
    verif, observed = archives
    _write(observed / "eumetsat_ctth" / "2026-07-01.parquet", [
        {"icao": "ZZAA", "frame_valid_time": H12, "radius_nm": 10.0,
         "insufficient_coverage": False, "highest_fl": 380.0},
    ], GRID_SCHEMA)
    out = tmp_path / "pairs.parquet"
    score_convective(verif, observed, pairs_out=out)
    tops = {r["icao"]: r["max_top_fl"] for r in pq.read_table(out).to_pylist() if r["days_out"] == 0}
    assert tops["ZZAA"] == 380.0
    assert tops["ZZAC"] is None


def test_skill_scores_handle_empty_denominators():
    r = with_skill({"hits": 0, "misses": 0, "false_alarms": 0})
    assert r["pod"] is None and r["far"] is None and r["csi"] is None and r["bias"] is None


def test_radius_not_archived_is_rejected():
    """The radius columns are matched exactly, so an unarchived radius would
    score nothing; it fails loudly instead."""
    with pytest.raises(ValueError, match="radar_radius_nm"):
        ConvectiveScoreParams(radar_radius_nm=15.0)
