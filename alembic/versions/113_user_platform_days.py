"""``user_platform_days``: which platforms each user uses, per UTC day (#879).

One row per (day, user, platform) — ios / android / web from session
requests, mcp from logged tool calls. Plain ``create_table`` with a named
unique constraint (its leading ``day`` column also serves the admin
Activity range scans) and a user index for account deletion.

Revision ID: 113
Revises: 112
Create Date: 2026-10-23
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "113"
down_revision: Union[str, None] = "112"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_platform_days",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column(
            "user_id", sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_user_platform_days_user_id"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.UniqueConstraint("day", "user_id", "platform", name="uq_user_platform_day"),
    )
    op.create_index("ix_user_platform_days_user_id", "user_platform_days", ["user_id"])


def downgrade() -> None:
    op.drop_table("user_platform_days")
