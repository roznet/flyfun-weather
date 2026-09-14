"""Migration 096 must never overwrite a share code the app already minted.

The deploy sequence in ``designs/multi-user-deployment.md`` is
``docker compose up -d --build && docker exec weatherbrief alembic upgrade head``:
the new app is **already serving** while this migration runs, lazy share-code
mint included. So a request can claim a code for one of the very rows the
migration selected as NULL, in the window between that SELECT and the row's own
UPDATE.

An unconditional UPDATE silently replaces it — the same failure
``storage/trips.py::ensure_share_code`` was made conditional to close, one layer
down: whoever was handed the overwritten code has a link that 404s for its
recipient forever, and nothing errors to say so.

Driven through the real ``upgrade()`` with ``alembic.op`` bound to a live
connection (the pattern in :mod:`tests.test_migration_093_rows`), because the
guard being tested lives in the loop's WHERE clause, not in a transform.
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
    / "alembic" / "versions" / "096_trip_share_code_backfill.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_mig096", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, fn):
    """Invoke a migration function with ``alembic.op`` bound to ``conn``."""
    operations = Operations(MigrationContext.configure(conn))
    with Operations.context(operations):
        fn()


@pytest.fixture
def conn(tmp_path):
    """Three code-less trips, in a table shaped like 095's ``flight_trips``.

    Only the two columns the migration touches — it addresses the table by name
    through ``sa.table``, so the rest of 095's schema is irrelevant here.
    """
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm096.db'}")
    with engine.begin() as connection:
        connection.execute(sa.text(
            "CREATE TABLE flight_trips ("
            "  id VARCHAR(16) PRIMARY KEY,"
            "  share_code VARCHAR(16) UNIQUE"
            ")"
        ))
        for trip_id in ("trip-a", "trip-b", "trip-c"):
            connection.execute(
                sa.text("INSERT INTO flight_trips (id, share_code) VALUES (:i, NULL)"),
                {"i": trip_id},
            )
    with engine.begin() as connection:
        yield connection
    engine.dispose()


def _codes(conn) -> dict[str, str | None]:
    return {
        row[0]: row[1]
        for row in conn.execute(
            sa.text("SELECT id, share_code FROM flight_trips")
        ).fetchall()
    }


def test_backfills_every_code_less_trip(conn):
    module = _load_migration()
    _run(conn, module.upgrade)

    codes = _codes(conn)
    assert all(code for code in codes.values()), codes
    assert len(set(codes.values())) == 3, "codes must be unique"


def test_a_code_minted_mid_migration_is_not_overwritten(conn, monkeypatch):
    """The race the deploy order makes reachable, reproduced exactly.

    ``_gen_code`` is patched to claim ``trip-c`` on its first call — standing in
    for a request that hits ``ensure_share_code`` after the migration's SELECT
    has already listed ``trip-c`` as NULL. The migration must then leave that
    row alone rather than write its own code over it.
    """
    module = _load_migration()
    claimed = "APPMINT1"
    calls = {"n": 0}
    real_gen = module._gen_code

    def _gen_and_race() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            # The app mints for trip-c, mid-loop, on its own connection's
            # behalf. Committed and visible to the migration's UPDATE.
            conn.execute(
                sa.text(
                    "UPDATE flight_trips SET share_code = :c WHERE id = 'trip-c'"
                ),
                {"c": claimed},
            )
        return real_gen()

    monkeypatch.setattr(module, "_gen_code", _gen_and_race)
    _run(conn, module.upgrade)

    codes = _codes(conn)
    assert codes["trip-c"] == claimed, (
        "the migration overwrote a code the app had already handed out"
    )
    # …and the rows the app did not claim are still backfilled.
    assert codes["trip-a"] and codes["trip-b"]
    assert len(set(codes.values())) == 3


def test_upgrade_is_idempotent(conn):
    """A re-run must not re-roll codes that already exist.

    Same guard, reached the ordinary way: every row is non-NULL on the second
    pass, so the SELECT returns nothing and no UPDATE is issued at all.
    """
    module = _load_migration()
    _run(conn, module.upgrade)
    first = _codes(conn)

    _run(conn, module.upgrade)

    assert _codes(conn) == first
