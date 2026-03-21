"""Drop credit_ledger table — all data now lives in cost_ledger.

Revision ID: 022
Revises: 021
Create Date: 2026-03-21
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("credit_ledger")


def downgrade() -> None:
    op.create_table(
        "credit_ledger",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("balance_after", sa.Float, nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("description", sa.String(256), nullable=False, server_default=""),
        sa.Column("breakdown_json", sa.Text, nullable=True),
        sa.Column(
            "briefing_usage_id",
            sa.Integer,
            sa.ForeignKey("briefing_usage.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "cost_config_id",
            sa.Integer,
            sa.ForeignKey("cost_config.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
