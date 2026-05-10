"""Add airport_monthly_summary and airport_daily_summary rollup tables.

Pre-aggregated observation climatology per airport. Powers the public
Patterns map and Spotlight leaderboards/heatmaps. Both tables are pure
``op.create_table`` so they apply identically on SQLite (dev) and MySQL
(prod) without batch mode.

Revision ID: 053
Revises: 052
Create Date: 2026-05-10
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "053"
down_revision: Union[str, None] = "052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "airport_monthly_summary",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("month", sa.DateTime(timezone=True), nullable=False),
        sa.Column("icao", sa.String(4), nullable=False),
        # Sample size
        sa.Column("n_obs", sa.Integer(), nullable=False, server_default="0"),
        # Flight category hour counts
        sa.Column("n_vfr", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_mvfr", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_ifr", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_lifr", sa.Integer(), nullable=False, server_default="0"),
        # Phenomena hour counts
        sa.Column("n_obscuration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_fg", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_br", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_haze", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_precip", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_ra", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_sn", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_ts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_freezing", sa.Integer(), nullable=False, server_default="0"),
        # Ceiling percentiles (ft)
        sa.Column("ceiling_p10_ft", sa.Integer(), nullable=True),
        sa.Column("ceiling_p25_ft", sa.Integer(), nullable=True),
        sa.Column("ceiling_p50_ft", sa.Integer(), nullable=True),
        sa.Column("ceiling_p75_ft", sa.Integer(), nullable=True),
        sa.Column("ceiling_p90_ft", sa.Integer(), nullable=True),
        # Visibility percentiles (m)
        sa.Column("visibility_p10_m", sa.Integer(), nullable=True),
        sa.Column("visibility_p25_m", sa.Integer(), nullable=True),
        sa.Column("visibility_p50_m", sa.Integer(), nullable=True),
        # Wind aggregates (kt)
        sa.Column("wind_mean_kt", sa.Float(), nullable=True),
        sa.Column("wind_p50_kt", sa.Float(), nullable=True),
        sa.Column("wind_p95_kt", sa.Float(), nullable=True),
        sa.Column("gust_max_kt", sa.Integer(), nullable=True),
        # Hours-below-threshold convenience counts
        sa.Column("n_ceiling_below_500", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_ceiling_below_1500", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_visibility_below_5km", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_wind_over_25kt", sa.Integer(), nullable=False, server_default="0"),
        # Temperature extremes
        sa.Column("temp_min_c", sa.Float(), nullable=True),
        sa.Column("temp_max_c", sa.Float(), nullable=True),
        # Volatility
        sa.Column("category_changes", sa.Integer(), nullable=False, server_default="0"),
        # Per-hour-of-day breakdown JSON
        sa.Column("diurnal_json", sa.Text(), nullable=True),
        # Constraints
        sa.UniqueConstraint("month", "icao", name="uq_ams_key"),
        sa.Index("ix_ams_month", "month"),
        sa.Index("ix_ams_icao", "icao"),
    )

    op.create_table(
        "airport_daily_summary",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("icao", sa.String(4), nullable=False),
        sa.Column("n_obs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("worst_category", sa.String(4), nullable=True),
        sa.Column("n_vfr", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_mvfr", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_ifr", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_lifr", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_obscuration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_fg", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_br", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_precip", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_ts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_freezing", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ceiling_min_ft", sa.Integer(), nullable=True),
        sa.Column("ceiling_p10_ft", sa.Integer(), nullable=True),
        sa.Column("ceiling_p50_ft", sa.Integer(), nullable=True),
        sa.Column("visibility_min_m", sa.Integer(), nullable=True),
        sa.Column("visibility_p50_m", sa.Integer(), nullable=True),
        sa.Column("wind_max_kt", sa.Integer(), nullable=True),
        sa.Column("gust_max_kt", sa.Integer(), nullable=True),
        sa.Column("temp_min_c", sa.Float(), nullable=True),
        sa.Column("temp_max_c", sa.Float(), nullable=True),
        sa.UniqueConstraint("date", "icao", name="uq_ads_key"),
        sa.Index("ix_ads_date", "date"),
        sa.Index("ix_ads_icao", "icao"),
    )


def downgrade() -> None:
    op.drop_table("airport_daily_summary")
    op.drop_table("airport_monthly_summary")
