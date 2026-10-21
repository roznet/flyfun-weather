"""Migration 111 switches the new icing card off only where both old cards were off (#827).

Driven through the real ``upgrade()`` (pattern in
:mod:`tests.test_migration_105_synoptic_default`): every other profile, a
malformed row included, keeps its stored text unchanged.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "111_aircraft_icing_enabled.py"
)


def _settings(enabled):
    return json.dumps({"cruise_altitude_ft": 8000, "advisories": {"enabled": enabled}})


#: profile id -> (stored text, expected aircraft_icing afterwards; "same" = text unchanged)
_CASES = {
    1: (_settings({"icing_escape": False}), False),
    2: (_settings({"icing_escape": False, "fiki_icing": False}), False),
    3: (_settings({"icing_escape": False, "fiki_icing": True}), "same"),
    4: (_settings({"icing_escape": True}), "same"),
    5: (_settings({}), "same"),
    6: (json.dumps({"cruise_altitude_ft": 8000}), "same"),
    7: ("", "same"),
    8: ('{"advisories": {"enabled": {"icing_escape": fal', "same"),
}


def _load_migration():
    spec = importlib.util.spec_from_file_location("_mig111", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def conn(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm111.db'}")
    with engine.begin() as connection:
        connection.execute(sa.text(
            "CREATE TABLE flight_profiles (id INTEGER PRIMARY KEY, settings_json TEXT)"
        ))
        for pid, (raw, _) in _CASES.items():
            connection.execute(
                sa.text("INSERT INTO flight_profiles VALUES (:i, :s)"), {"i": pid, "s": raw},
            )
    with engine.begin() as connection:
        yield connection
    engine.dispose()


def test_only_both_off_profiles_lose_the_card(conn):
    with Operations.context(Operations(MigrationContext.configure(conn))):
        _load_migration().upgrade()
    stored = dict(conn.execute(sa.text("SELECT id, settings_json FROM flight_profiles")).fetchall())
    for pid, (raw, expected) in _CASES.items():
        if expected == "same":
            assert stored[pid] == raw, pid
            continue
        after = json.loads(stored[pid])
        before = json.loads(raw)
        assert after["advisories"]["enabled"]["aircraft_icing"] is expected
        # Nothing else moves; the old keys stay, inert.
        after["advisories"]["enabled"].pop("aircraft_icing")
        assert after == before, pid


@pytest.mark.parametrize(("enabled", "expected"), [
    ({"icing_escape": False}, False),                       # fiki default off
    ({"icing_escape": False, "fiki_icing": False}, False),
    ({"icing_escape": False, "fiki_icing": True}, None),    # FIKI card was on
    ({"icing_escape": True}, None),
    ({"cloud_top": False}, None),                           # icing_escape default on
    ({"icing_escape": False, "aircraft_icing": True}, True),  # already decided
])
def test_settings_migration(enabled, expected):
    settings = {"advisories": {"enabled": dict(enabled)}}
    new, changed = _load_migration().migrate_icing_enabled(settings)
    assert new["advisories"]["enabled"].get("aircraft_icing") == expected
    assert changed == (expected is False and "aircraft_icing" not in enabled)
    # Old keys are kept, inert.
    for k, v in enabled.items():
        assert new["advisories"]["enabled"][k] == v


def test_settings_migration_leaves_uncustomized_profiles():
    for settings in ({}, {"advisories": {}}, {"advisories": {"enabled": {}}}):
        assert _load_migration().migrate_icing_enabled(settings) == (settings, False)
