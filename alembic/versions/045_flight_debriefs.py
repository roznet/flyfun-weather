"""Add flight_debriefs table for pilot post-flight judgement.

Revision ID: 045
Revises: 044
Create Date: 2026-04-25
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "045"
down_revision: Union[str, None] = "044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flight_debriefs",
        sa.Column(
            "flight_id",
            sa.String(256),
            sa.ForeignKey("flights.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reasons_json", sa.Text, nullable=True),
        sa.Column("outcomes_json", sa.Text, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("flight_debriefs")
