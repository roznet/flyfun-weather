"""Drop FK cascade on cost_ledger.user_id so rows survive account deletion.

cost_ledger rows must be retained for audit/reporting after a user deletes
their account.  The CASCADE introduced in 019 would silently delete them.
This migration replaces the FK with a plain indexed column (matching the
api_tokens pattern).

Revision ID: 020
Revises: 019
Create Date: 2026-03-20
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite doesn't store FK names; use naming_convention so batch
        # mode can locate the constraint created in migration 019.
        naming = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
        with op.batch_alter_table(
            "cost_ledger", naming_convention=naming
        ) as batch_op:
            batch_op.drop_constraint(
                "fk_cost_ledger_user_id_users", type_="foreignkey"
            )
    else:
        # MySQL/PostgreSQL: introspect the actual FK name.
        from sqlalchemy import inspect
        fks = inspect(bind).get_foreign_keys("cost_ledger")
        fk_name = next(
            fk["name"] for fk in fks
            if fk["constrained_columns"] == ["user_id"]
        )
        op.drop_constraint(fk_name, "cost_ledger", type_="foreignkey")


def downgrade() -> None:
    with op.batch_alter_table("cost_ledger") as batch_op:
        batch_op.create_foreign_key(
            "fk_cost_ledger_user_id_users",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
