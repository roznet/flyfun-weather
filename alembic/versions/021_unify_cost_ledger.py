"""Enrich cost_ledger with extended columns and backfill from credit_ledger.

Adds category, description, detail_json, reference_id to cost_ledger (the
shared cross-app ledger in flyfun-common).  Then copies credit_ledger rows
into cost_ledger so weather can use cost_ledger as its sole ledger.

Revision ID: 021
Revises: 020
Create Date: 2026-03-21
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # --- 1. Add extended columns to cost_ledger ---
    with op.batch_alter_table("cost_ledger") as batch_op:
        batch_op.add_column(sa.Column("category", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("description", sa.String(256), nullable=True))
        batch_op.add_column(sa.Column("detail_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("reference_id", sa.String(128), nullable=True))

    # --- 2. Backfill briefing rows from credit_ledger ---
    # Extract total_usd from breakdown_json; fall back to abs(amount) * 0.01
    if dialect == "sqlite":
        json_extract = "json_extract(cl.breakdown_json, '$.total_usd')"
        cast_ref = "CAST(cl.briefing_usage_id AS TEXT)"
        cast_type = "REAL"
    else:
        # MySQL
        json_extract = "JSON_UNQUOTE(JSON_EXTRACT(cl.breakdown_json, '$.total_usd'))"
        cast_ref = "CAST(cl.briefing_usage_id AS CHAR)"
        cast_type = "DECIMAL(10,6)"

    bind.execute(
        sa.text(f"""
            INSERT INTO cost_ledger
                (user_id, service, action, cost, category, description,
                 detail_json, reference_id, created_at, metadata_json)
            SELECT
                cl.user_id,
                'flyfun-weather',
                cl.category,
                COALESCE(
                    CAST({json_extract} AS {cast_type}),
                    ABS(cl.amount) * 0.01
                ),
                cl.category,
                cl.description,
                cl.breakdown_json,
                CASE WHEN cl.briefing_usage_id IS NOT NULL
                     THEN {cast_ref}
                     ELSE NULL
                END,
                cl.timestamp,
                NULL
            FROM credit_ledger cl
        """)
    )


def downgrade() -> None:
    # Remove backfilled weather rows
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM cost_ledger WHERE service = 'flyfun-weather'")
    )

    # Drop the extended columns
    with op.batch_alter_table("cost_ledger") as batch_op:
        batch_op.drop_column("reference_id")
        batch_op.drop_column("detail_json")
        batch_op.drop_column("description")
        batch_op.drop_column("category")
