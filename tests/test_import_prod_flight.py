"""Row remapping of scripts/import_prod_flight.py (prod flight → local dev)."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "import_prod_flight.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("import_prod_flight", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


FLIGHT = "zzzz_yyyy-2026-01-02-abcd"


def test_remap_flight_owner_and_prod_only_fks(mod):
    row = {
        "id": FLIGHT, "user_id": "prod-user", "profile_id": 12, "aircraft_id": 34,
        "trip_id": "t1", "share_code": "AbCdEfGh", "auto_refresh": 1,
        "last_auto_refresh_at": "2026-01-01T06:00:00", "route_name": "R",
    }
    out = mod.remap_flight(row, "dev-user-001")
    assert out["user_id"] == "dev-user-001"
    assert out["profile_id"] is None and out["aircraft_id"] is None and out["trip_id"] is None
    assert out["share_code"] is None
    assert out["auto_refresh"] is False and out["last_auto_refresh_at"] is None
    assert out["route_name"] == "R"
    assert row["user_id"] == "prod-user"  # input untouched


def test_remap_pack_rehomes_artifact_path(mod):
    ts_dir = "2026-01-01T19-34-45.646436p00-00"
    row = {
        "id": 9763, "flight_id": FLIGHT, "integrity_hmac": "deadbeef",
        "artifact_path": f"/app/data/packs/prod-user/{FLIGHT}/{ts_dir}",
    }
    out = mod.remap_pack(row, Path("/local/data/packs"), "dev-user-001")
    assert "id" not in out
    assert out["integrity_hmac"] is None
    assert out["artifact_path"] == f"/local/data/packs/dev-user-001/{FLIGHT}/{ts_dir}"
    assert mod.remap_pack({**row, "artifact_path": ""}, Path("/x"), "u")["artifact_path"] == ""


def test_coerce_row_types_and_drops_unknown_columns(mod):
    cols = {"fetch_timestamp": datetime, "has_gramet": bool, "days_out": int, "note": str}
    raw = {
        "fetch_timestamp": "2026-01-01T19:34:45.646436",  # MySQL naive = UTC
        "has_gramet": 1, "days_out": 2, "note": None, "prod_only": "x",
    }
    out, dropped = mod.coerce_row(raw, cols)
    assert out["fetch_timestamp"] == datetime(2026, 1, 1, 19, 34, 45, 646436, tzinfo=timezone.utc)
    assert out["has_gramet"] is True and out["days_out"] == 2 and out["note"] is None
    assert dropped == ["prod_only"]


def test_parse_dt_converts_offsets_to_utc(mod):
    assert mod.parse_dt("2026-01-01T10:00:00+02:00") == datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
    assert mod.parse_dt(None) is None
