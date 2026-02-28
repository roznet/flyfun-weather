"""Add models_skipped_region_json column to briefing_packs.

Revision ID: 016
Revises: 015
Create Date: 2026-02-28
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "briefing_packs",
        sa.Column(
            "models_skipped_region_json",
            sa.Text,
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("briefing_packs", "models_skipped_region_json")
