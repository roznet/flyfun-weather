"""Add model_init_times_json column to briefing_packs.

Revision ID: 003
Revises: 002
Create Date: 2026-02-14
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "briefing_packs",
        sa.Column(
            "model_init_times_json",
            sa.Text,
            server_default="{}",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("briefing_packs", "model_init_times_json")
