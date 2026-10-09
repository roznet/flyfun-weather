"""Migration 098 clears only names that are wholly the derived label (#728).

A trip name is user data: the one thing this migration must never do is clear
a name the pilot typed. Pilots renaming a trip were handed the derived label to
edit, so real names often *contain* a chain ("Summer tour EGTF → LFAT → EGTF,
3–5 Aug") — those are the pilot's and stay.

Driven through the real ``upgrade()`` (pattern in
:mod:`tests.test_migration_096_share_codes`).
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "098_trip_unnamed_names.py"
)

#: name -> what the migration must leave behind.
_CASES = {
    "EGTF → LSGS → EGTF, 20 Feb": "",
    "EGTF → LSGS → LFAT → EGTF, 20–22 Feb": "",
    "EGTF → LSGS → EGTF, 28 Feb–2 Mar": "",
    "Trip, 3 Aug": "",
    "New trip": "",
    "Summer tour EGTF → LFAT → EGTF, 3–5 Aug": "Summer tour EGTF → LFAT → EGTF, 3–5 Aug",
    "Alpine tour": "Alpine tour",
    "EGTF → LSGS": "EGTF → LSGS",
    "": "",
}


def _load_migration():
    spec = importlib.util.spec_from_file_location("_mig098", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, fn):
    operations = Operations(MigrationContext.configure(conn))
    with Operations.context(operations):
        fn()


@pytest.fixture
def conn(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm098.db'}")
    with engine.begin() as connection:
        connection.execute(sa.text(
            "CREATE TABLE flight_trips ("
            "  id VARCHAR(16) PRIMARY KEY,"
            "  name VARCHAR(200) NOT NULL DEFAULT ''"
            ")"
        ))
        for index, name in enumerate(_CASES):
            connection.execute(
                sa.text("INSERT INTO flight_trips (id, name) VALUES (:i, :n)"),
                {"i": f"trip-{index}", "n": name},
            )
    with engine.begin() as connection:
        yield connection
    engine.dispose()


def _names(conn) -> dict[str, str]:
    return dict(conn.execute(sa.text("SELECT id, name FROM flight_trips")).fetchall())


def test_clears_derived_names_and_keeps_pilot_names(conn):
    _run(conn, _load_migration().upgrade)
    names = _names(conn)
    for index, (before, after) in enumerate(_CASES.items()):
        assert names[f"trip-{index}"] == after, before


def test_the_pattern_matches_what_the_api_used_to_derive():
    """Lock the regex to the producer it reverses, not to hand-written strings."""
    from datetime import datetime, timezone

    from weatherbrief.api.trips import _derived_name

    module = _load_migration()
    utc = timezone.utc
    for departures in (
        [datetime(2026, 2, 20, tzinfo=utc)],
        [datetime(2026, 2, 20, tzinfo=utc), datetime(2026, 2, 22, tzinfo=utc)],
        [datetime(2026, 2, 28, tzinfo=utc), datetime(2026, 3, 2, tzinfo=utc)],
    ):
        for chain in ("EGTF → LSGS → EGTF", "EGTF", ""):
            name = _derived_name(chain, departures)
            assert module.is_derived_name(name), name
    assert module.is_derived_name(_derived_name("EGTF → LSGS", []))


def test_a_rename_landing_mid_migration_survives(conn, monkeypatch):
    """The app is serving while this runs; a rename between the SELECT and the
    UPDATE is the pilot's."""
    module = _load_migration()
    real = module.is_derived_name
    calls = {"n": 0}

    def _match_and_race(name: str) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            conn.execute(sa.text(
                "UPDATE flight_trips SET name = 'Renamed' WHERE id = 'trip-0'"
            ))
        return real(name)

    monkeypatch.setattr(module, "is_derived_name", _match_and_race)
    _run(conn, module.upgrade)
    assert _names(conn)["trip-0"] == "Renamed"
