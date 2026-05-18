"""Create verification_daily_stats — daily pre-aggregation of NWP scores.

Replaces the query-time aggregation of raw verification_scores that drives
the model accuracy dashboard and (new) optimistic-bias leaderboard. Grouped
by (date, source, model, days_out, icao); ~12K rows/day, ~4.5M/year.

SUM columns (not averages) so periods compose by simple addition; query
divides ``sum_abs / n_<field>`` for MAE. Per-field non-NULL counts are
stored separately because not every score has every delta populated.

Schema mirrors ``verification_monthly_stats`` (same direction classification,
same hit/miss/false_alarm columns) so a future monthly-roll-of-rollups can
be a GROUP BY over this table.

TAF rollup is intentionally out of scope (different key shape — no model,
days_out via lead_hours bucket). Dashboard queries for TAF continue to hit
``taf_verification_scores`` directly.

Revision ID: 058
Revises: 057
Create Date: 2026-05-17
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "058"
down_revision: Union[str, None] = "057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "verification_daily_stats",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("model", sa.String(20), nullable=False),
        sa.Column("days_out", sa.Integer(), nullable=False),
        sa.Column("icao", sa.String(4), nullable=False),
        # Sample size: total scores and per-delta-field non-NULL counts
        sa.Column("n", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_ceiling", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_wind", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_temp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_vis", sa.Integer(), nullable=False, server_default="0"),
        # Category direction counts (match + 4 directional)
        sa.Column("n_cat_match", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_opt_1", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_opt_2", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_pess_1", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_cat_pess_2", sa.Integer(), nullable=False, server_default="0"),
        # Continuous delta sums (signed and absolute) — divide by n_<field>
        # at query time for MAE/bias. NULL when n_<field>=0.
        sa.Column("sum_abs_ceiling_delta_ft", sa.Float(), nullable=True),
        sa.Column("sum_ceiling_delta_ft", sa.Float(), nullable=True),
        sa.Column("sum_abs_wind_delta_kt", sa.Float(), nullable=True),
        sa.Column("sum_abs_temp_delta_c", sa.Float(), nullable=True),
        sa.Column("sum_abs_vis_delta_m", sa.Float(), nullable=True),
        # Advisory direction counts (match + 2 directional)
        sa.Column("n_advisory_match", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_advisory_opt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_advisory_pess", sa.Integer(), nullable=False, server_default="0"),
        # Precipitation hit/miss/false_alarm — both obs and fcst must be non-NULL
        sa.Column("n_precip_hit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_precip_miss", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_precip_false_alarm", sa.Integer(), nullable=False, server_default="0"),
        # Convection hit/miss/false_alarm
        sa.Column("n_convection_hit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_convection_miss", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_convection_false_alarm", sa.Integer(), nullable=False, server_default="0"),
        # Constraints + indexes
        sa.UniqueConstraint(
            "date", "source", "model", "days_out", "icao", name="uq_vds_key",
        ),
        sa.Index("ix_vds_date_model", "date", "source", "model", "days_out"),
        sa.Index("ix_vds_icao_model", "icao", "source", "model", "days_out"),
    )


def downgrade() -> None:
    op.drop_table("verification_daily_stats")
