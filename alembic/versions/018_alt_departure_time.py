"""Add alt_departure_time to flights and alt assessment fields to briefing_packs.

Revision ID: 018
Revises: 017
Create Date: 2026-03-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "flights",
        sa.Column("alt_departure_time", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "briefing_packs",
        sa.Column("alt_assessment", sa.String(16), nullable=True),
    )
    op.add_column(
        "briefing_packs",
        sa.Column("alt_assessment_reason", sa.Text, nullable=True),
    )
    op.add_column(
        "briefing_packs",
        sa.Column("has_alt_advisories", sa.Boolean, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("briefing_packs", "has_alt_advisories")
    op.drop_column("briefing_packs", "alt_assessment_reason")
    op.drop_column("briefing_packs", "alt_assessment")
    op.drop_column("flights", "alt_departure_time")
