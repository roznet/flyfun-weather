"""Migration 105 removes only a stored ``synoptic_forecast_map_enabled: false``.

Every other preference key must survive byte-for-byte in meaning, and a stored
``true`` is the user's and stays. Driven through the real ``upgrade()``
(pattern in :mod:`tests.test_migration_096_share_codes`).
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
    / "alembic" / "versions" / "105_synoptic_map_default_on.py"
)

_OTHERS = {"locale": "de", "units_region": "us", "defer_email_for_model_update": False}

#: user_id -> (stored blob, expected parsed blob afterwards; None = unchanged text).
_CASES = {
    "off": (json.dumps({**_OTHERS, "synoptic_forecast_map_enabled": False}), _OTHERS),
    "on": (json.dumps({**_OTHERS, "synoptic_forecast_map_enabled": True}), None),
    "absent": (json.dumps(_OTHERS), None),
    "empty": ("", None),
    "garbage": ('{"synoptic_forecast_map_enabled": fal', None),
}


def _load_migration():
    spec = importlib.util.spec_from_file_location("_mig105", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def conn(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm105.db'}")
    with engine.begin() as connection:
        connection.execute(sa.text(
            "CREATE TABLE user_preferences ("
            "  user_id VARCHAR(64) PRIMARY KEY,"
            "  app_prefs_json TEXT"
            ")"
        ))
        for user_id, (raw, _) in _CASES.items():
            connection.execute(
                sa.text("INSERT INTO user_preferences VALUES (:u, :j)"),
                {"u": user_id, "j": raw},
            )
    with engine.begin() as connection:
        yield connection
    engine.dispose()


def test_drops_only_stored_false(conn):
    with Operations.context(Operations(MigrationContext.configure(conn))):
        _load_migration().upgrade()
    stored = dict(conn.execute(sa.text(
        "SELECT user_id, app_prefs_json FROM user_preferences"
    )).fetchall())
    for user_id, (raw, expected) in _CASES.items():
        if expected is None:
            assert stored[user_id] == raw, user_id
        else:
            assert json.loads(stored[user_id]) == expected, user_id
