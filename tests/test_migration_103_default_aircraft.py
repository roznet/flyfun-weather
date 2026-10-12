"""Migration 103 (#777): a default aircraft per user, speed off the profile.

Driven through the real ``upgrade()`` with ``alembic.op`` bound to a live
SQLite connection (the pattern in :mod:`tests.test_migration_096_share_codes`),
on tables cut down to the columns the migration reads and writes.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from weatherbrief.storage.aircraft_backfill import plan_backfill

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "103_default_aircraft.py"
)

_STAMP = "2026-01-01 00:00:00"


def _load_migration():
    spec = importlib.util.spec_from_file_location("_mig103", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, fn):
    # Operations.context takes the MigrationContext itself, as alembic's own
    # runner does — handing it an Operations breaks batch_alter_table.
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _seed(conn) -> None:
    for ddl in (
        "CREATE TABLE users (id VARCHAR(64) PRIMARY KEY)",
        "CREATE TABLE user_aircraft ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id VARCHAR(64) NOT NULL,"
        "  icao_type VARCHAR(4) NOT NULL, tail_number VARCHAR(10), nickname VARCHAR(50),"
        "  is_ifr BOOLEAN, is_fiki BOOLEAN, cruise_speed_kt INTEGER, ceiling_ft INTEGER,"
        "  is_default BOOLEAN, created_at DATETIME)",
        "CREATE TABLE flight_profiles ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id VARCHAR(64), is_default BOOLEAN,"
        "  settings_json TEXT, updated_at DATETIME)",
        "CREATE TABLE flights ("
        "  id VARCHAR(256) PRIMARY KEY, user_id VARCHAR(64), profile_id INTEGER,"
        "  aircraft_id INTEGER)",
        "CREATE TABLE pireps (id INTEGER PRIMARY KEY, aircraft_id INTEGER)",
    ):
        conn.execute(sa.text(ddl))

    def user(uid):
        conn.execute(sa.text("INSERT INTO users (id) VALUES (:u)"), {"u": uid})

    def profile(pid, uid, settings, default):
        conn.execute(sa.text(
            "INSERT INTO flight_profiles (id, user_id, is_default, settings_json, updated_at)"
            " VALUES (:i, :u, :d, :s, :t)"
        ), {"i": pid, "u": uid, "d": default, "s": json.dumps(settings), "t": _STAMP})

    def aircraft(aid, uid, speed, default, created):
        conn.execute(sa.text(
            "INSERT INTO user_aircraft (id, user_id, icao_type, is_ifr, is_fiki,"
            " cruise_speed_kt, is_default, created_at)"
            " VALUES (:i, :u, 'C172', 1, 0, :s, :d, :c)"
        ), {"i": aid, "u": uid, "s": speed, "d": default, "c": created})

    def flight(fid, uid, pid, aid):
        conn.execute(sa.text(
            "INSERT INTO flights (id, user_id, profile_id, aircraft_id) VALUES (:f, :u, :p, :a)"
        ), {"f": fid, "u": uid, "p": pid, "a": aid})

    # no-aircraft: profile speed seeds the placeholder
    user("no-aircraft")
    profile(1, "no-aircraft", {"speed_kt": 120, "flight_rules": "vfr_only", "cruise_altitude_ft": 6000}, True)
    flight("na-1", "no-aircraft", None, None)
    flight("na-2", "no-aircraft", 1, None)

    # speedless: one aircraft without speed, flown under two profile speeds
    user("speedless")
    profile(2, "speedless", {"speed_kt": 110}, True)
    profile(3, "speedless", {"speed_kt": 130}, False)
    aircraft(10, "speedless", None, True, "2025-01-01 00:00:00")
    flight("sl-1", "speedless", 3, 10)
    flight("sl-2", "speedless", 3, 10)
    flight("sl-3", "speedless", 2, 10)  # the one that must change: 110 -> 130

    # no-default: two aircraft, neither default → oldest promoted
    user("no-default")
    profile(4, "no-default", {"speed_kt": 150}, True)
    aircraft(20, "no-default", 90, False, "2025-03-01 00:00:00")
    aircraft(21, "no-default", 150, False, "2025-02-01 00:00:00")
    flight("nd-1", "no-default", None, None)
    flight("nd-2", "no-default", None, 20)

    # two-defaults: the younger default is cleared
    user("two-defaults")
    aircraft(30, "two-defaults", 100, True, "2025-01-01 00:00:00")
    aircraft(31, "two-defaults", 140, True, "2025-06-01 00:00:00")

    # bare: no profile, no aircraft, no flights
    user("bare")


@pytest.fixture
def conn(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm103.db'}")
    with engine.begin() as connection:
        _seed(connection)
    with engine.begin() as connection:
        yield connection
    engine.dispose()


def _aircraft(conn, uid):
    return conn.execute(sa.text(
        "SELECT id, icao_type, nickname, cruise_speed_kt, ceiling_ft, is_ifr, is_fiki,"
        " is_default, is_placeholder FROM user_aircraft WHERE user_id = :u ORDER BY id"
    ), {"u": uid}).mappings().all()


def test_every_user_ends_with_exactly_one_default(conn):
    _run(conn, _load_migration().upgrade)
    for uid in ("no-aircraft", "speedless", "no-default", "two-defaults", "bare"):
        rows = _aircraft(conn, uid)
        assert sum(bool(r["is_default"]) for r in rows) == 1, (uid, rows)


def test_placeholder_fields(conn):
    _run(conn, _load_migration().upgrade)
    (row,) = _aircraft(conn, "no-aircraft")
    assert row["icao_type"] == "ZZZZ"
    assert row["nickname"] == "My aircraft"
    assert row["cruise_speed_kt"] == 120
    assert row["ceiling_ft"] is None
    assert not row["is_ifr"]  # vfr_only profile
    assert not row["is_fiki"]
    assert row["is_default"] and row["is_placeholder"]

    (bare,) = _aircraft(conn, "bare")
    assert bare["is_placeholder"] and bare["cruise_speed_kt"] is None and bare["is_ifr"]


def test_existing_aircraft_are_not_placeholders(conn):
    _run(conn, _load_migration().upgrade)
    for uid in ("speedless", "no-default", "two-defaults"):
        assert not any(r["is_placeholder"] for r in _aircraft(conn, uid))


def test_promotion_and_extra_defaults(conn):
    _run(conn, _load_migration().upgrade)
    assert [(r["id"], bool(r["is_default"])) for r in _aircraft(conn, "no-default")] == [
        (20, False), (21, True),  # 21 is older
    ]
    assert [(r["id"], bool(r["is_default"])) for r in _aircraft(conn, "two-defaults")] == [
        (30, True), (31, False),
    ]


def test_gap_fill_takes_the_most_used_speed(conn):
    _run(conn, _load_migration().upgrade)
    (row,) = _aircraft(conn, "speedless")
    assert row["cruise_speed_kt"] == 130


def test_speed_kt_stripped_and_updated_at_kept(conn):
    _run(conn, _load_migration().upgrade)
    rows = conn.execute(sa.text("SELECT settings_json, updated_at FROM flight_profiles")).fetchall()
    for raw, updated_at in rows:
        assert "speed_kt" not in json.loads(raw)
        assert str(updated_at).startswith("2026-01-01")
    first = json.loads(conn.execute(sa.text(
        "SELECT settings_json FROM flight_profiles WHERE id = 1"
    )).scalar())
    assert first == {"flight_rules": "vfr_only", "cruise_altitude_ft": 6000}


def test_cruise_ias_invariant(conn):
    """Every flight resolves the same cruise IAS, except the one flight that
    shared a speedless aircraft with flights under a different profile speed."""
    plan = plan_backfill(conn)
    assert plan.mismatches == [("sl-3", 110.0, 130.0)]
    assert plan.before_ias["na-1"] == plan.after_ias["na-1"] == 120.0
    assert plan.before_ias["nd-1"] == plan.after_ias["nd-1"] == 150.0
    assert plan.before_ias["nd-2"] == plan.after_ias["nd-2"] == 90.0


def test_dry_run_writes_nothing(conn, monkeypatch):
    monkeypatch.setenv("AIRCRAFT_BACKFILL_DRY_RUN", "1")
    with pytest.raises(RuntimeError, match="DRY_RUN"):
        _run(conn, _load_migration().upgrade)
    cols = [c["name"] for c in sa.inspect(conn).get_columns("user_aircraft")]
    assert "is_placeholder" not in cols
    assert _aircraft_count(conn) == 5


def _aircraft_count(conn) -> int:
    return conn.execute(sa.text("SELECT COUNT(*) FROM user_aircraft")).scalar()


def test_downgrade_removes_placeholders_and_column(conn):
    module = _load_migration()
    _run(conn, module.upgrade)
    assert _aircraft_count(conn) == 7  # 5 + 2 placeholders
    _run(conn, module.downgrade)
    assert _aircraft_count(conn) == 5
    cols = [c["name"] for c in sa.inspect(conn).get_columns("user_aircraft")]
    assert "is_placeholder" not in cols
