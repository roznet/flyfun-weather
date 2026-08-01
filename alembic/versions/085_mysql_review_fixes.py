"""Dedupe briefing_packs and enforce UNIQUE (flight_id, fetch_timestamp).

``save_pack_meta`` inserted unconditionally while the readers
(``load_pack_meta`` / ``update_pack_meta``) use ``scalar_one_or_none()`` — so
a single duplicate ``(flight_id, fetch_timestamp)`` pair, left behind e.g. by
two concurrent refreshes racing the provisional-pack insert, makes every
subsequent read of that pack raise (HTTP 500). This migration first deletes
all but the newest row (``MAX(id)``) of each duplicated pair, then adds the
unique index that turns the race into a constraint violation the save path
can catch and convert into an update (see the ``begin_nested`` guard in
``storage/flights.py``).

The dedupe is deliberately standard SQL — no MySQL-only JOIN-delete — so the
same revision runs on SQLite (dev/tests) and MySQL (prod). The keep-set is
double-nested in a derived table because MySQL refuses a DELETE whose
subquery reads the target table directly (error 1093); the derived table
forces the keep-set to materialize first. Singletons are their own
``MAX(id)``, so they are retained by construction.

The rest of the mysql-review schema hygiene rides in the same revision
(``docs/superpowers/specs/2026-07-31-mysql-optimization-design.md`` —
Tier-1 item 4, Tier-2 items 5-7):

* **Missing indexes**: ``ix_briefing_usage_timestamp``,
  ``ix_cost_ledger_service_created (service, created_at)``,
  ``ix_verif_scores_obs_time (observation_time)``,
  ``ix_taf_verif_obs_time (observation_time)`` — audited hot/range paths,
  retention DELETEs for the last two.
* **users (provider, provider_sub) UNIQUE** — the OAuth login lookup becomes
  indexed and duplicate-identity races impossible. A pre-check refuses with a
  clear error if duplicates exist (expected zero; they would need a manual
  account-merge decision, never a silent dedupe).
* **Drop 14 redundant indexes** — 13 leftmost-prefix duplicates of wider
  UNIQUE keys and the non-unique ``oauth_refresh_tokens.token_hash``
  duplicate. Never dropped: ``ix_verif_scores_lead`` /
  ``ix_verif_scores_model`` (need ``sys.schema_unused_indexes`` evidence),
  the FORCE-INDEXed ``ix_verif_scores_source_time``, and
  ``ix_afs_hour_model`` — dropping that one was considered (081's documented
  follow-up assumed region-aware serving) and rejected: the map path still
  filters bare ``forecast_hour`` with no region predicate
  (``api/maps.py:118-129`` availability scan,
  ``tasks/map_queries.py:262-266`` per-model init-time probe,
  ``tasks/cache_builder.py:275-279`` rebuild probe), so the region-leading
  ``ix_afs_region_hour_model`` cannot serve those queries and the drop would
  restore full/per-model scans on the hot map path.
* **Money → DECIMAL** — ``cost_ledger.cost`` and
  ``donation_ledger.amount/amount_usd/net_usd`` to ``DECIMAL(12,4)``,
  ``donation_ledger.fx_rate`` to ``DECIMAL(12,6)``.

Every create/drop is existence-guarded (``_index_exists``) so the revision
runs on MySQL (prod) and SQLite (dev/tests) and is safe to re-run. The
``users``/``cost_ledger``/``donation_ledger``/``oauth_refresh_tokens`` ORM
models live in flyfun_common and stay untouched — their dev ``create_all``
output keeps the pre-085 shape; this migration governs prod.

Revision ID: 085
Revises: 084
Create Date: 2026-08-01
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "085"
down_revision: Union[str, None] = "084"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger(__name__)


def _index_exists(table: str, name: str) -> bool:
    """Dialect-portable index-existence check.

    MySQL answers via ``information_schema.STATISTICS``, SQLite via
    ``PRAGMA index_list`` — the two dialects this project runs on. Table
    names are fixed constants from this file, so the PRAGMA f-string is safe.
    """
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        return bool(
            bind.execute(
                sa.text(
                    "SELECT COUNT(*) FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA = DATABASE() "
                    "AND TABLE_NAME = :table AND INDEX_NAME = :name"
                ),
                {"table": table, "name": name},
            ).scalar_one()
        )
    rows = bind.execute(sa.text(f"PRAGMA index_list({table})")).fetchall()
    return any(row[1] == name for row in rows)


def _index_is_unique(table: str, name: str) -> bool:
    """Whether an existing index enforces uniqueness (same dialect split)."""
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        return bool(
            bind.execute(
                sa.text(
                    "SELECT COUNT(*) FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA = DATABASE() "
                    "AND TABLE_NAME = :table AND INDEX_NAME = :name "
                    "AND NON_UNIQUE = 0"
                ),
                {"table": table, "name": name},
            ).scalar_one()
        )
    rows = bind.execute(sa.text(f"PRAGMA index_list({table})")).fetchall()
    return any(row[1] == name and row[2] for row in rows)


def _create_index_if_absent(
    name: str, table: str, columns: list[str], unique: bool = False
) -> None:
    if not _index_exists(table, name):
        op.create_index(name, table, columns, unique=unique)


def _drop_index_if_present(table: str, name: str) -> None:
    if _index_exists(table, name):
        op.drop_index(name, table_name=table)


# Spec Tier-1 item 4 + Tier-2 item 6: (table, index, columns, unique).
_NEW_INDEXES = [
    ("briefing_usage", "ix_briefing_usage_timestamp", ["timestamp"], False),
    (
        "cost_ledger",
        "ix_cost_ledger_service_created",
        ["service", "created_at"],
        False,
    ),
    (
        "verification_scores",
        "ix_verif_scores_obs_time",
        ["observation_time"],
        False,
    ),
    (
        "taf_verification_scores",
        "ix_taf_verif_obs_time",
        ["observation_time"],
        False,
    ),
    ("users", "uq_users_provider_sub", ["provider", "provider_sub"], True),
]

# Spec Tier-2 item 5: the 13 leftmost-prefix duplicates. Columns are kept on
# each row so the downgrade can recreate them. The 14th drop — the non-unique
# duplicate on oauth_refresh_tokens.token_hash — is handled separately in
# upgrade() because its guard is uniqueness-aware.
#
# ix_afs_hour_model is deliberately NOT in this list: the spec's premise for
# dropping it (081's "drop once serving filters by region" follow-up) is
# unmet — the map path still filters bare forecast_hour with no region
# predicate (api/maps.py:118-129, tasks/map_queries.py:262-266,
# tasks/cache_builder.py:275-279), and the region-leading
# ix_afs_region_hour_model needs a region predicate to be usable. Dropping it
# would restore the full/per-model table scans migration 077 fixed (#415).
_REDUNDANT_INDEXES = [
    ("verification_observations", "ix_verif_obs_icao", ["icao"]),
    ("verification_scores", "ix_verif_scores_icao", ["icao"]),
    ("taf_verification_scores", "ix_taf_verif_icao", ["icao"]),
    ("flight_verification_map", "ix_fvm_flight", ["flight_id"]),
    ("flight_subscriptions", "ix_flight_subs_flight", ["flight_id"]),
    ("flight_briefing_seen", "ix_flight_seen_user", ["user_id"]),
    ("verification_monthly_stats", "ix_vms_month", ["month"]),
    (
        "verification_daily_stats",
        "ix_vds_date_model",
        ["date", "source", "model", "days_out"],
    ),
    ("airport_monthly_summary", "ix_ams_month", ["month"]),
    ("airport_daily_summary", "ix_ads_date", ["date"]),
    ("analytics_event_daily", "ix_aed_day", ["day"]),
    ("analytics_briefing_feature_daily", "ix_abfd_day", ["day"]),
    ("analytics_xsection_config_daily", "ix_axcd_day", ["day"]),
]

# Spec Tier-2 item 7: (table, column, target type, nullable).
_MONEY_COLUMNS = [
    ("cost_ledger", "cost", sa.DECIMAL(12, 4), False),
    ("donation_ledger", "amount", sa.DECIMAL(12, 4), False),
    ("donation_ledger", "amount_usd", sa.DECIMAL(12, 4), False),
    ("donation_ledger", "net_usd", sa.DECIMAL(12, 4), True),
    ("donation_ledger", "fx_rate", sa.DECIMAL(12, 6), False),
]


def upgrade() -> None:
    bind = op.get_bind()
    duplicate_rows = bind.execute(
        sa.text(
            "SELECT COALESCE(SUM(cnt - 1), 0) FROM ("
            "SELECT COUNT(*) AS cnt FROM briefing_packs "
            "GROUP BY flight_id, fetch_timestamp) d"
        )
    ).scalar_one()
    if duplicate_rows:
        logger.warning(
            "briefing_packs: deleting %d duplicate "
            "(flight_id, fetch_timestamp) row(s), keeping MAX(id) per pair",
            duplicate_rows,
        )
    op.execute(
        sa.text(
            "DELETE FROM briefing_packs WHERE id NOT IN ("
            "SELECT keep_id FROM ("
            "SELECT MAX(id) AS keep_id FROM briefing_packs "
            "GROUP BY flight_id, fetch_timestamp"
            ") keep_ids)"
        )
    )
    _create_index_if_absent(
        "uq_briefing_packs_flight_ts",
        "briefing_packs",
        ["flight_id", "fetch_timestamp"],
        unique=True,
    )

    # --- users (provider, provider_sub) UNIQUE (spec Tier-2 item 6) ---
    # Duplicates would mean two accounts sharing one external identity — a
    # manual account-merge decision, never something a migration resolves
    # silently. Expected zero: refuse loudly otherwise.
    duplicate_identities = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "SELECT 1 FROM users GROUP BY provider, provider_sub "
            "HAVING COUNT(*) > 1) d"
        )
    ).scalar_one()
    if duplicate_identities:
        raise RuntimeError(
            f"users: {duplicate_identities} duplicate (provider, provider_sub) "
            "identity pair(s) found — resolve them manually, then re-run; "
            "refusing to create uq_users_provider_sub"
        )
    for table, name, columns, unique in _NEW_INDEXES:
        _create_index_if_absent(name, table, columns, unique=unique)

    # --- Drop the 14 redundant indexes (spec Tier-2 item 5) ---
    # Leftmost-prefix duplicates of wider UNIQUE keys; DROP INDEX is
    # metadata-only on MySQL 8. Kept: ix_verif_scores_lead / _model (awaiting
    # sys.schema_unused_indexes evidence), FORCE-INDEXed
    # ix_verif_scores_source_time, and ix_afs_hour_model (drop considered and
    # rejected — the map path still filters bare forecast_hour, see the note
    # above _REDUNDANT_INDEXES).
    for table, name, _columns in _REDUNDANT_INDEXES:
        _drop_index_if_present(table, name)
    # oauth_refresh_tokens.token_hash: migration 041 declared the column
    # ``unique=True, index=True``, which SQLAlchemy renders as ONE merged
    # UNIQUE index (ix_oauth_refresh_tokens_token_hash). Prod MySQL carries
    # an additional NON-UNIQUE index under that name next to the unique one —
    # drop only that plain duplicate; where the index by this name IS the
    # unique guard (SQLite dev/tests), keep it.
    if _index_exists(
        "oauth_refresh_tokens", "ix_oauth_refresh_tokens_token_hash"
    ) and not _index_is_unique(
        "oauth_refresh_tokens", "ix_oauth_refresh_tokens_token_hash"
    ):
        op.drop_index(
            "ix_oauth_refresh_tokens_token_hash",
            table_name="oauth_refresh_tokens",
        )

    # --- Money FLOAT → DECIMAL (spec Tier-2 item 7) ---
    # One-way, lossy conversion: stored FLOAT values are rounded to the new
    # scale (4 dp amounts, 6 dp fx_rate) as they convert; downgrading back to
    # FLOAT cannot restore the original binary representation. The ORM
    # mappings stay ``Float`` — they live in flyfun_common (external, not
    # editable here) and read DECIMAL columns unchanged; the proper
    # ``Numeric`` mapping is an upstream follow-up. Batch mode keeps SQLite
    # (dev/tests) working; on MySQL batch_alter_table is plain ALTER TABLE.
    for table, column, new_type, nullable in _MONEY_COLUMNS:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                column,
                existing_type=sa.Float(),
                type_=new_type,
                existing_nullable=nullable,
            )


def downgrade() -> None:
    # Money back to FLOAT — values already rounded by the upgrade do not
    # recover their original precision (one-way conversion).
    for table, column, decimal_type, nullable in _MONEY_COLUMNS:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                column,
                existing_type=decimal_type,
                type_=sa.Float(),
                existing_nullable=nullable,
            )

    for table, name, columns in _REDUNDANT_INDEXES:
        _create_index_if_absent(name, table, columns)
    # The plain token_hash duplicate only ever existed on prod MySQL; where
    # the name is taken by the UNIQUE index (SQLite), there is nothing to
    # restore.
    _create_index_if_absent(
        "ix_oauth_refresh_tokens_token_hash",
        "oauth_refresh_tokens",
        ["token_hash"],
    )

    for table, name, _columns, _unique in _NEW_INDEXES:
        _drop_index_if_present(table, name)

    op.drop_index("uq_briefing_packs_flight_ts", table_name="briefing_packs")
