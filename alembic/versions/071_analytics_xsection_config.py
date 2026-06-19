"""Add cross-section config snapshot rollup table.

``analytics_xsection_config_daily`` preserves the per-dimension breakdown of
the cross-section display config (theme/preset/layout/cloud-style/display-mode/
model + per-layer attachment) rolled up from ``xsection.viewed`` snapshot
events. Like the other ``*_daily`` rollups it is kept forever, so the config
history survives the 60-day raw-event retention purge.

Composite natural PK ``(day, dimension, value)`` matches the existing
``*_daily`` tables — no surrogate id needed. Pure ``op.create_table`` (works
identically on SQLite + MySQL without batch mode).

Revision ID: 071
Revises: 070
Create Date: 2026-06-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "071"
down_revision: Union[str, None] = "070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analytics_xsection_config_daily",
        sa.Column("day", sa.Date(), nullable=False),
        # Scalar dim key ('theme', 'preset', 'layout', …) or 'layer'.
        sa.Column("dimension", sa.String(32), nullable=False),
        # The bucket value: enum/id/bool-as-string, or a layer id.
        sa.Column("value", sa.String(64), nullable=False),
        # Views with this (dimension, value); for 'layer', views with the
        # layer enabled.
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
        # Distinct anonymous viewers contributing to this bucket.
        sa.Column("unique_anons", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("day", "dimension", "value", name="pk_axcd_day_dim_val"),
        sa.Index("ix_axcd_day", "day"),
    )


def downgrade() -> None:
    op.drop_table("analytics_xsection_config_daily")
