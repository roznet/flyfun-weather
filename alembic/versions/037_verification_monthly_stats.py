"""Add verification_monthly_stats rollup table.

Pre-aggregated monthly accuracy metrics per airport/model/days_out.
Combines model scores (GFS, ICON, ECMWF) and TAF scores (model='taf')
into a single table for fast dashboard queries.

Revision ID: 037
Revises: 036
Create Date: 2026-04-08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "037"
down_revision: Union[str, None] = "036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "verification_monthly_stats",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("month", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("model", sa.String(20), nullable=False),
        sa.Column("days_out", sa.Integer(), nullable=False),
        sa.Column("icao", sa.String(4), nullable=False),
        # Sample size
        sa.Column("n_scores", sa.Integer(), nullable=False, server_default="0"),
        # Flight category direction counts
        sa.Column("n_cat_match", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_optimistic_1", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_optimistic_2", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_pessimistic_1", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_pessimistic_2", sa.Integer(), nullable=False, server_default="0"),
        # Wind advisory direction counts
        sa.Column("n_advisory_match", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_advisory_optimistic", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_advisory_pessimistic", sa.Integer(), nullable=False, server_default="0"),
        # Continuous error metrics
        sa.Column("ceiling_mae_ft", sa.Float(), nullable=True),
        sa.Column("ceiling_bias_ft", sa.Float(), nullable=True),
        sa.Column("visibility_mae_m", sa.Float(), nullable=True),
        sa.Column("wind_speed_mae_kt", sa.Float(), nullable=True),
        sa.Column("temperature_mae_c", sa.Float(), nullable=True),
        # Precipitation / convection (model scores only, NULL for TAF)
        sa.Column("n_precip_hit", sa.Integer(), nullable=True),
        sa.Column("n_precip_miss", sa.Integer(), nullable=True),
        sa.Column("n_precip_false_alarm", sa.Integer(), nullable=True),
        sa.Column("n_convection_hit", sa.Integer(), nullable=True),
        sa.Column("n_convection_miss", sa.Integer(), nullable=True),
        sa.Column("n_convection_false_alarm", sa.Integer(), nullable=True),
        # Constraints
        sa.UniqueConstraint("month", "source", "model", "days_out", "icao", name="uq_vms_key"),
        sa.Index("ix_vms_month", "month"),
        sa.Index("ix_vms_model_days", "model", "days_out"),
        sa.Index("ix_vms_icao", "icao"),
    )


def downgrade() -> None:
    op.drop_table("verification_monthly_stats")
