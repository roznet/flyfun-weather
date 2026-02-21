"""Add auto-refresh columns to flights.

Revision ID: 005
Revises: 004
Create Date: 2026-02-21
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "flights",
        sa.Column("auto_refresh", sa.Boolean, nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "flights",
        sa.Column("auto_refresh_hour", sa.Integer, nullable=True),
    )
    op.add_column(
        "flights",
        sa.Column("last_auto_refresh_date", sa.String(10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("flights", "last_auto_refresh_date")
    op.drop_column("flights", "auto_refresh_hour")
    op.drop_column("flights", "auto_refresh")
