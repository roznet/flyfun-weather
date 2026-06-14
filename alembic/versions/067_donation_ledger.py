"""Create donation_ledger and drop the retired users.spending_limit column.

Donations are money *in* — a different shape from the cost ledger's "always
positive USD = cost" invariant — so they get their own table (issue #186). The
``DonationRow`` model lives in flyfun-common (shared across apps), but
flyfun-common ships no migrations, so each consuming app creates the table in
its own Alembic history, the same way ``cost_ledger`` was.

Also drops ``users.spending_limit``: the per-user spend balance is retired
(nothing gated on it; the decrement + $5 auto-reload was write-only churn). The
column drop lives here because weatherbrief owns the migrations for the shared
``users`` table; flyfun-common already removed the column from the model.

Revision ID: 067
Revises: 066
Create Date: 2026-06-14
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "067"
down_revision: Union[str, None] = "066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- donation_ledger (works on SQLite + MySQL without batch mode) ---
    op.create_table(
        "donation_ledger",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        # Nullable, no FK: anonymous donors allowed; attributed donations must
        # survive deletion of the donor's user row.
        sa.Column("user_id", sa.String(64), nullable=True),
        sa.Column("service", sa.String(64), nullable=False),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount_usd", sa.Float, nullable=False),
        sa.Column("fx_rate", sa.Float, nullable=False),
        sa.Column("net_usd", sa.Float, nullable=True),
        sa.Column("recurring", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(32), nullable=False, server_default="succeeded"),
        sa.Column("provider", sa.String(32), nullable=False, server_default="stripe"),
        # Capped at 191 for MySQL utf8mb4 unique-index limits.
        sa.Column("provider_ref", sa.String(191), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider_ref", name="uq_donation_ledger_provider_ref"),
    )
    op.create_index("ix_donation_ledger_user_id", "donation_ledger", ["user_id"])
    op.create_index("ix_donation_ledger_created_at", "donation_ledger", ["created_at"])

    # --- Drop the retired per-user spend balance ---
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("spending_limit")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("spending_limit", sa.Float, nullable=False, server_default="500.0")
        )

    op.drop_index("ix_donation_ledger_created_at", table_name="donation_ledger")
    op.drop_index("ix_donation_ledger_user_id", table_name="donation_ledger")
    op.drop_table("donation_ledger")
