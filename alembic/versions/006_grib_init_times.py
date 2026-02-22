"""Add grib_init_times_json column to briefing_packs.

Revision ID: 006
Revises: 005
Create Date: 2026-02-22
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "briefing_packs",
        sa.Column(
            "grib_init_times_json",
            sa.Text,
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("briefing_packs", "grib_init_times_json")
