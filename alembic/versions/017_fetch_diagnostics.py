"""Add diagnostics_json column to briefing_packs.

Revision ID: 017
Revises: 016
Create Date: 2026-03-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "briefing_packs",
        sa.Column(
            "diagnostics_json",
            sa.Text,
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("briefing_packs", "diagnostics_json")
