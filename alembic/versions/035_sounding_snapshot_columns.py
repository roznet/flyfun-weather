"""Add sounding-derived columns to airport_forecast_snapshots.

Stores results from analyze_sounding() so the standalone verification
scoring path uses the same ceiling reconciliation as the briefing pipeline.

Revision ID: 035
Revises: 034
Create Date: 2026-04-05
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "035"
down_revision: Union[str, None] = "034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.add_column(sa.Column("sounding_ceiling_ft", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("sounding_cloud_base_ft", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("freezing_level_ft", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("sounding_cape_jkg", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("sounding_cin_jkg", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("sounding_lifted_index", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("sounding_convective_risk", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.drop_column("sounding_convective_risk")
        batch_op.drop_column("sounding_lifted_index")
        batch_op.drop_column("sounding_cin_jkg")
        batch_op.drop_column("sounding_cape_jkg")
        batch_op.drop_column("freezing_level_ft")
        batch_op.drop_column("sounding_cloud_base_ft")
        batch_op.drop_column("sounding_ceiling_ft")
