"""Replace usage_log with briefing_usage table.

Revision ID: 002
Revises: 001
Create Date: 2026-02-13
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("usage_log")

    op.create_table(
        "briefing_usage",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("flight_id", sa.String(100), nullable=False, server_default=""),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open_meteo_calls", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("gramet_fetched", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("gramet_failed", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("llm_digest", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("llm_model", sa.String(100), nullable=True),
        sa.Column("llm_input_tokens", sa.Integer, nullable=True),
        sa.Column("llm_output_tokens", sa.Integer, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("briefing_usage")

    op.create_table(
        "usage_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("call_type", sa.String(64), nullable=False),
        sa.Column("detail_json", sa.Text, nullable=False),
        sa.Column("skipped", sa.Boolean, nullable=False, server_default=sa.text("0")),
    )
