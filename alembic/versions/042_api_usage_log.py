"""Create api_usage_log table for tracking external API calls.

Tracks calls per pipeline run per service (open_meteo, ecmwf, etc.)
for subscription limit monitoring and per-user usage breakdown.

Revision ID: 042
Revises: 041
Create Date: 2026-04-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "042"
down_revision: Union[str, None] = "041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_usage_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("service", sa.String(32), nullable=False),
        sa.Column("pipeline", sa.String(32), nullable=False),
        sa.Column("api_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("user_id", sa.String(64), nullable=True),
        sa.Column("flight_id", sa.String(256), nullable=True),
        sa.Index("ix_api_usage_timestamp", "timestamp"),
        sa.Index("ix_api_usage_service", "service"),
    )


def downgrade() -> None:
    op.drop_table("api_usage_log")
