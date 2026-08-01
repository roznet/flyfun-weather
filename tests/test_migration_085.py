"""Migration 085 — dedupe + UNIQUE (flight_id, fetch_timestamp) on briefing_packs.

Why these tests run the real alembic chain: ``Base.metadata.create_all`` would
bake the new constraint in from the start (the ORM carries it now), making it
impossible to seed the duplicate rows the migration exists to clean up. Only a
schema built *by the migrations themselves* up to 084 reproduces the prod
situation: a ``briefing_packs`` table with no uniqueness guard and possibly
duplicate pairs — one of which would 500 every ``load_pack_meta`` read via
``scalar_one_or_none()``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

REPO_ROOT = Path(__file__).resolve().parents[1]

# SQLAlchemy renders SQLite DateTime as this exact text format; the dedupe
# GROUP BY compares the stored values, so duplicates must match byte-for-byte.
TS_A = "2026-02-19 18:00:00.000000"
TS_B = "2026-02-18 08:00:00.000000"


def _config() -> Config:
    # No ini file: env.py then skips fileConfig and resolves the database
    # purely from DATABASE_URL (set by the fixture below).
    cfg = Config()
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


@pytest.fixture
def db_at_084(tmp_path, monkeypatch):
    """A temp SQLite DB migrated to 084 (pre-constraint schema)."""
    db_path = tmp_path / "mig085.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    command.upgrade(_config(), "084")
    return f"sqlite:///{db_path}"


def _insert_pack(conn, flight_id: str, fetch_ts: str) -> int:
    """Raw-SQL insert of a minimal pack row; returns the new rowid.

    FK enforcement stays off (alembic's own engine never enables the pragma),
    so no parent flight row is needed.
    """
    result = conn.execute(
        sa.text(
            "INSERT INTO briefing_packs"
            " (flight_id, fetch_timestamp, days_out, artifact_path)"
            " VALUES (:f, :ts, 2, '')"
        ),
        {"f": flight_id, "ts": fetch_ts},
    )
    return result.lastrowid


def _pack_rows(url: str) -> list[int]:
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        ids = [
            r[0]
            for r in conn.execute(sa.text("SELECT id FROM briefing_packs ORDER BY id"))
        ]
    engine.dispose()
    return ids


def test_upgrade_dedupes_keeping_max_id_and_enforces_unique(db_at_084):
    engine = sa.create_engine(db_at_084)
    with engine.begin() as conn:
        # The duplicate pair a racing double-refresh would have left behind.
        dupe_old = _insert_pack(conn, "flight-1", TS_A)
        dupe_new = _insert_pack(conn, "flight-1", TS_A)
        # Control rows: distinct timestamp, distinct flight — must survive.
        other_ts = _insert_pack(conn, "flight-1", TS_B)
        other_flight = _insert_pack(conn, "flight-2", TS_A)
    engine.dispose()
    assert dupe_old < dupe_new

    command.upgrade(_config(), "head")

    # Only the newest row of the duplicate pair survives; controls untouched.
    assert _pack_rows(db_at_084) == sorted([dupe_new, other_ts, other_flight])

    # The constraint physically exists and rejects a new duplicate.
    engine = sa.create_engine(db_at_084)
    with engine.connect() as conn:
        indexes = sa.inspect(conn).get_indexes("briefing_packs")
        uq = {i["name"]: i for i in indexes}["uq_briefing_packs_flight_ts"]
        assert uq["unique"]  # SQLite reports 1, not True
        assert uq["column_names"] == ["flight_id", "fetch_timestamp"]
        with pytest.raises(IntegrityError):
            _insert_pack(conn, "flight-1", TS_A)
    engine.dispose()


def test_upgrade_on_empty_table_and_downgrade(db_at_084):
    """Dedupe on an empty table is a no-op; downgrade drops only the index."""
    command.upgrade(_config(), "head")

    engine = sa.create_engine(db_at_084)
    with engine.connect() as conn:
        names = {i["name"] for i in sa.inspect(conn).get_indexes("briefing_packs")}
    assert "uq_briefing_packs_flight_ts" in names

    command.downgrade(_config(), "084")

    with engine.connect() as conn:
        names = {i["name"] for i in sa.inspect(conn).get_indexes("briefing_packs")}
    assert "uq_briefing_packs_flight_ts" not in names
    engine.dispose()
