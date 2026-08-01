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

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
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


# ---------------------------------------------------------------------------
# Second half of revision 085: missing indexes, redundant-index drops,
# users (provider, provider_sub) UNIQUE, DECIMAL money columns.
# ---------------------------------------------------------------------------

# (table, index, columns, unique) — spec Tier-1 item 4 + Tier-2 item 6.
NEW_INDEXES = [
    ("briefing_usage", "ix_briefing_usage_timestamp", ["timestamp"], False),
    ("cost_ledger", "ix_cost_ledger_service_created", ["service", "created_at"], False),
    ("verification_scores", "ix_verif_scores_obs_time", ["observation_time"], False),
    ("taf_verification_scores", "ix_taf_verif_obs_time", ["observation_time"], False),
    ("users", "uq_users_provider_sub", ["provider", "provider_sub"], True),
]

# Spec Tier-2 item 5, verbatim — the 13 leftmost-prefix duplicates plus
# ix_afs_hour_model. All of these exist on a SQLite schema migrated to 084
# (asserted below before the upgrade), so "gone at head" is meaningful. The
# 15th drop (the plain duplicate on oauth_refresh_tokens.token_hash) never
# exists on SQLite — covered separately in test_redundant_indexes_dropped.
DROPPED_INDEXES = [
    ("verification_observations", "ix_verif_obs_icao"),
    ("verification_scores", "ix_verif_scores_icao"),
    ("taf_verification_scores", "ix_taf_verif_icao"),
    ("flight_verification_map", "ix_fvm_flight"),
    ("flight_subscriptions", "ix_flight_subs_flight"),
    ("flight_briefing_seen", "ix_flight_seen_user"),
    ("verification_monthly_stats", "ix_vms_month"),
    ("verification_daily_stats", "ix_vds_date_model"),
    ("airport_monthly_summary", "ix_ams_month"),
    ("airport_daily_summary", "ix_ads_date"),
    ("analytics_event_daily", "ix_aed_day"),
    ("analytics_briefing_feature_daily", "ix_abfd_day"),
    ("analytics_xsection_config_daily", "ix_axcd_day"),
    ("airport_forecast_snapshots", "ix_afs_hour_model"),
]

# Never dropped (spec Tier-2 item 5): the FORCE-INDEXed one and the two
# awaiting sys.schema_unused_indexes evidence.
KEPT_SCORES_INDEXES = [
    "ix_verif_scores_lead",
    "ix_verif_scores_model",
    "ix_verif_scores_source_time",
]


def _index_map(conn, table: str) -> dict:
    return {i["name"]: i for i in sa.inspect(conn).get_indexes(table)}


def _insert_user(conn, user_id: str, provider_sub: str, provider: str = "google") -> None:
    conn.execute(
        sa.text(
            "INSERT INTO users (id, provider, provider_sub, created_at)"
            " VALUES (:i, :p, :s, :ts)"
        ),
        {"i": user_id, "p": provider, "s": provider_sub, "ts": TS_A},
    )


def test_new_indexes_created(db_at_084):
    command.upgrade(_config(), "head")

    engine = sa.create_engine(db_at_084)
    with engine.connect() as conn:
        for table, name, columns, unique in NEW_INDEXES:
            idx = _index_map(conn, table).get(name)
            assert idx is not None, f"{name} missing on {table}"
            assert idx["column_names"] == columns
            assert bool(idx["unique"]) == unique  # SQLite reports 0/1
    engine.dispose()


def test_redundant_indexes_dropped(db_at_084):
    engine = sa.create_engine(db_at_084)
    with engine.connect() as conn:
        # Sanity: the whole drop list is really there at 084 — otherwise the
        # "gone at head" assertions below would be vacuous on SQLite.
        for table, name in DROPPED_INDEXES:
            assert name in _index_map(conn, table), f"{name} never existed"
    engine.dispose()

    command.upgrade(_config(), "head")

    engine = sa.create_engine(db_at_084)
    with engine.connect() as conn:
        for table, name in DROPPED_INDEXES:
            assert name not in _index_map(conn, table), f"{name} survived"
        kept = _index_map(conn, "verification_scores")
        for name in KEPT_SCORES_INDEXES:
            assert name in kept, f"FORCE-INDEXed/evidence-gated {name} dropped"
        # The 15th drop targets the NON-UNIQUE duplicate of
        # oauth_refresh_tokens.token_hash that only prod MySQL carries; on
        # SQLite the only index by that name IS the unique one (migration 041
        # declared unique=True + index=True, one merged index) — the guard
        # must make the drop a no-op, not remove the uniqueness guard.
        token_idx = _index_map(conn, "oauth_refresh_tokens")[
            "ix_oauth_refresh_tokens_token_hash"
        ]
        assert token_idx["unique"]
    engine.dispose()


def test_users_provider_sub_unique_enforced(db_at_084):
    command.upgrade(_config(), "head")

    engine = sa.create_engine(db_at_084)
    with engine.begin() as conn:
        _insert_user(conn, "user-1", "sub-1")
        # Control: same sub under a different provider is a different identity.
        _insert_user(conn, "user-2", "sub-1", provider="local")
    with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            _insert_user(conn, "user-3", "sub-1")
    engine.dispose()


def test_users_duplicate_identity_blocks_upgrade(db_at_084):
    """The unique-index pre-check must refuse, not dedupe silently."""
    engine = sa.create_engine(db_at_084)
    with engine.begin() as conn:
        _insert_user(conn, "user-1", "dup-sub")
        _insert_user(conn, "user-2", "dup-sub")
    engine.dispose()

    with pytest.raises(RuntimeError, match=r"duplicate \(provider, provider_sub\)"):
        command.upgrade(_config(), "head")


def test_money_columns_decimal_and_readback(db_at_084):
    """FLOAT→DECIMAL keeps values through the conversion (1234.56 read-back)."""
    engine = sa.create_engine(db_at_084)
    with engine.begin() as conn:
        # Seeded at 084 (FLOAT) so the read-back also proves the batch table
        # rebuild on SQLite preserves existing rows.
        conn.execute(
            sa.text(
                "INSERT INTO cost_ledger (user_id, service, action, cost, created_at)"
                " VALUES ('user-1', 'flyfun-weather', 'briefing', 1234.56, :ts)"
            ),
            {"ts": TS_A},
        )
        conn.execute(
            sa.text(
                "INSERT INTO donation_ledger"
                " (service, amount, currency, amount_usd, fx_rate,"
                "  provider_ref, created_at)"
                " VALUES ('flyfun-weather', 1234.56, 'EUR', 1234.56, 1.083333,"
                "         'pi_test', :ts)"
            ),
            {"ts": TS_A},
        )
    engine.dispose()

    command.upgrade(_config(), "head")

    engine = sa.create_engine(db_at_084)
    with engine.connect() as conn:
        insp = sa.inspect(conn)

        def col_type(table, column):
            cols = {c["name"]: str(c["type"]).upper() for c in insp.get_columns(table)}
            return cols[column]

        assert col_type("cost_ledger", "cost") == "DECIMAL(12, 4)"
        assert col_type("donation_ledger", "amount") == "DECIMAL(12, 4)"
        assert col_type("donation_ledger", "amount_usd") == "DECIMAL(12, 4)"
        assert col_type("donation_ledger", "net_usd") == "DECIMAL(12, 4)"
        assert col_type("donation_ledger", "fx_rate") == "DECIMAL(12, 6)"

        cost = conn.execute(sa.text("SELECT cost FROM cost_ledger")).scalar_one()
        assert cost == 1234.56
        amount = conn.execute(
            sa.text("SELECT amount FROM donation_ledger")
        ).scalar_one()
        assert amount == 1234.56
        fx = conn.execute(
            sa.text("SELECT fx_rate FROM donation_ledger")
        ).scalar_one()
        assert fx == 1.083333
    engine.dispose()


def test_upgrade_is_idempotent(db_at_084):
    command.upgrade(_config(), "head")
    # Alembic-level re-run: already at head, a no-op by version tracking.
    command.upgrade(_config(), "head")

    # Stronger form: execute the revision body a second time against the
    # already-migrated schema — every create/drop guard must make it a no-op
    # instead of crashing on duplicate/absent index names.
    spec = importlib.util.spec_from_file_location(
        "migration_085", REPO_ROOT / "alembic" / "versions" / "085_mysql_review_fixes.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    engine = sa.create_engine(db_at_084)
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mod.upgrade()
    engine.dispose()

    # State is still the post-upgrade one.
    engine = sa.create_engine(db_at_084)
    with engine.connect() as conn:
        assert "uq_users_provider_sub" in _index_map(conn, "users")
        for table, name in DROPPED_INDEXES:
            assert name not in _index_map(conn, table)
    engine.dispose()
