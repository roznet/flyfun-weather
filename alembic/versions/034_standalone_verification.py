"""Standalone verification pipeline schema changes.

- New airport_forecast_snapshots table for storing NWP forecasts at METAR airports
- Drop + recreate verification_scores with source column in unique constraint,
  plus cloud_base_delta_ft and lcl_delta_ft columns
- Drop + recreate taf_verification_scores with source column in unique constraint
- Add source column to verification_cycles
- Existing flight verification data is minimal (test flights only) — clean rebuild.

Revision ID: 034
Revises: 033
Create Date: 2026-04-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "034"
down_revision: Union[str, None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- New: airport_forecast_snapshots ---
    op.create_table(
        "airport_forecast_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("icao", sa.String(4), nullable=False),
        sa.Column("model", sa.String(20), nullable=False),
        sa.Column("model_init_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forecast_hour", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        # Surface fields (from Open-Meteo API)
        sa.Column("temperature_2m_c", sa.Float, nullable=True),
        sa.Column("dewpoint_2m_c", sa.Float, nullable=True),
        sa.Column("visibility_m", sa.Float, nullable=True),
        sa.Column("wind_speed_10m_kt", sa.Float, nullable=True),
        sa.Column("wind_direction_10m_deg", sa.Float, nullable=True),
        sa.Column("wind_gusts_10m_kt", sa.Float, nullable=True),
        sa.Column("precipitation_mm", sa.Float, nullable=True),
        sa.Column("snowfall_cm", sa.Float, nullable=True),
        sa.Column("cape_jkg", sa.Float, nullable=True),
        sa.Column("weather_code", sa.Integer, nullable=True),
        sa.Column("cloud_cover_pct", sa.Float, nullable=True),
        sa.Column("cloud_cover_low_pct", sa.Float, nullable=True),
        # Ceiling estimates (from GRIB + computed)
        sa.Column("nwp_ceiling_ft", sa.Float, nullable=True),
        sa.Column("cloud_base_ft", sa.Float, nullable=True),
        sa.Column("lcl_ft", sa.Float, nullable=True),
        sa.UniqueConstraint(
            "icao", "model", "model_init_time", "forecast_hour",
            name="uq_afs_key",
        ),
    )
    op.create_index("ix_afs_icao_hour", "airport_forecast_snapshots", ["icao", "forecast_hour"])
    op.create_index("ix_afs_fetched", "airport_forecast_snapshots", ["fetched_at"])
    op.create_index("ix_afs_model_init", "airport_forecast_snapshots", ["model", "model_init_time"])

    # --- Drop + recreate verification_scores with source column ---
    op.drop_table("verification_scores")
    op.create_table(
        "verification_scores",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "observation_id", sa.Integer,
            sa.ForeignKey("verification_observations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("icao", sa.String(4), nullable=False),
        sa.Column("observation_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(20), nullable=False),
        sa.Column("model_init_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_hours", sa.Integer, nullable=False),
        sa.Column("days_out", sa.Integer, nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="flight"),
        # Flight category comparison
        sa.Column("obs_flight_category", sa.String(4), nullable=True),
        sa.Column("model_flight_category", sa.String(4), nullable=True),
        sa.Column("category_match", sa.Boolean, nullable=True),
        # Quantitative deltas (model - observation)
        sa.Column("ceiling_delta_ft", sa.Float, nullable=True),
        sa.Column("cloud_base_delta_ft", sa.Float, nullable=True),
        sa.Column("lcl_delta_ft", sa.Float, nullable=True),
        sa.Column("visibility_delta_m", sa.Float, nullable=True),
        sa.Column("wind_speed_delta_kt", sa.Float, nullable=True),
        sa.Column("wind_dir_delta_deg", sa.Float, nullable=True),
        sa.Column("temperature_delta_c", sa.Float, nullable=True),
        # Wind advisory comparison
        sa.Column("obs_wind_advisory", sa.String(10), nullable=True),
        sa.Column("model_wind_advisory", sa.String(10), nullable=True),
        sa.Column("advisory_match", sa.Boolean, nullable=True),
        # Significant weather hit/miss
        sa.Column("obs_has_precipitation", sa.Boolean, nullable=True),
        sa.Column("model_has_precipitation", sa.Boolean, nullable=True),
        sa.Column("obs_has_convection", sa.Boolean, nullable=True),
        sa.Column("model_has_convection", sa.Boolean, nullable=True),
        sa.UniqueConstraint(
            "icao", "observation_time", "model", "model_init_time", "source",
            name="uq_verif_scores_key",
        ),
    )
    op.create_index("ix_verif_scores_obs", "verification_scores", ["observation_id"])
    op.create_index("ix_verif_scores_model", "verification_scores", ["model", "days_out"])
    op.create_index("ix_verif_scores_icao", "verification_scores", ["icao"])
    op.create_index("ix_verif_scores_lead", "verification_scores", ["lead_hours"])
    op.create_index(
        "ix_verif_scores_source_model_days",
        "verification_scores", ["source", "model", "days_out"],
    )

    # --- Drop + recreate taf_verification_scores with source column ---
    op.drop_table("taf_verification_scores")
    op.create_table(
        "taf_verification_scores",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "observation_id", sa.Integer,
            sa.ForeignKey("verification_observations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("icao", sa.String(4), nullable=False),
        sa.Column("observation_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("taf_issue_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_hours", sa.Integer, nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="flight"),
        # Flight category
        sa.Column("obs_flight_category", sa.String(4), nullable=True),
        sa.Column("taf_flight_category", sa.String(4), nullable=True),
        sa.Column("category_match", sa.Boolean, nullable=True),
        # Deltas (TAF - observation)
        sa.Column("ceiling_delta_ft", sa.Float, nullable=True),
        sa.Column("visibility_delta_m", sa.Float, nullable=True),
        sa.Column("wind_speed_delta_kt", sa.Float, nullable=True),
        sa.Column("wind_dir_delta_deg", sa.Float, nullable=True),
        # Wind advisory comparison
        sa.Column("obs_wind_advisory", sa.String(10), nullable=True),
        sa.Column("taf_wind_advisory", sa.String(10), nullable=True),
        sa.Column("advisory_match", sa.Boolean, nullable=True),
        sa.UniqueConstraint(
            "icao", "observation_time", "taf_issue_time", "source",
            name="uq_taf_verif_key",
        ),
    )
    op.create_index("ix_taf_verif_obs", "taf_verification_scores", ["observation_id"])
    op.create_index("ix_taf_verif_icao", "taf_verification_scores", ["icao"])

    # --- Add source column to verification_cycles ---
    with op.batch_alter_table("verification_cycles") as batch_op:
        batch_op.add_column(
            sa.Column("source", sa.String(16), nullable=False, server_default="flight"),
        )


def downgrade() -> None:
    with op.batch_alter_table("verification_cycles") as batch_op:
        batch_op.drop_column("source")

    # Recreate original taf_verification_scores (without source)
    op.drop_table("taf_verification_scores")
    op.create_table(
        "taf_verification_scores",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "observation_id", sa.Integer,
            sa.ForeignKey("verification_observations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("icao", sa.String(4), nullable=False),
        sa.Column("observation_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("taf_issue_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_hours", sa.Integer, nullable=False),
        sa.Column("obs_flight_category", sa.String(4), nullable=True),
        sa.Column("taf_flight_category", sa.String(4), nullable=True),
        sa.Column("category_match", sa.Boolean, nullable=True),
        sa.Column("ceiling_delta_ft", sa.Float, nullable=True),
        sa.Column("visibility_delta_m", sa.Float, nullable=True),
        sa.Column("wind_speed_delta_kt", sa.Float, nullable=True),
        sa.Column("wind_dir_delta_deg", sa.Float, nullable=True),
        sa.Column("obs_wind_advisory", sa.String(10), nullable=True),
        sa.Column("taf_wind_advisory", sa.String(10), nullable=True),
        sa.Column("advisory_match", sa.Boolean, nullable=True),
        sa.UniqueConstraint(
            "icao", "observation_time", "taf_issue_time",
            name="uq_taf_verif_key",
        ),
    )
    op.create_index("ix_taf_verif_obs", "taf_verification_scores", ["observation_id"])
    op.create_index("ix_taf_verif_icao", "taf_verification_scores", ["icao"])

    # Recreate original verification_scores (without source, cloud_base_delta, lcl_delta)
    op.drop_table("verification_scores")
    op.create_table(
        "verification_scores",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "observation_id", sa.Integer,
            sa.ForeignKey("verification_observations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("icao", sa.String(4), nullable=False),
        sa.Column("observation_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(20), nullable=False),
        sa.Column("model_init_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_hours", sa.Integer, nullable=False),
        sa.Column("days_out", sa.Integer, nullable=False),
        sa.Column("obs_flight_category", sa.String(4), nullable=True),
        sa.Column("model_flight_category", sa.String(4), nullable=True),
        sa.Column("category_match", sa.Boolean, nullable=True),
        sa.Column("ceiling_delta_ft", sa.Float, nullable=True),
        sa.Column("visibility_delta_m", sa.Float, nullable=True),
        sa.Column("wind_speed_delta_kt", sa.Float, nullable=True),
        sa.Column("wind_dir_delta_deg", sa.Float, nullable=True),
        sa.Column("temperature_delta_c", sa.Float, nullable=True),
        sa.Column("obs_wind_advisory", sa.String(10), nullable=True),
        sa.Column("model_wind_advisory", sa.String(10), nullable=True),
        sa.Column("advisory_match", sa.Boolean, nullable=True),
        sa.Column("obs_has_precipitation", sa.Boolean, nullable=True),
        sa.Column("model_has_precipitation", sa.Boolean, nullable=True),
        sa.Column("obs_has_convection", sa.Boolean, nullable=True),
        sa.Column("model_has_convection", sa.Boolean, nullable=True),
        sa.UniqueConstraint(
            "icao", "observation_time", "model", "model_init_time",
            name="uq_verif_scores_key",
        ),
    )
    op.create_index("ix_verif_scores_obs", "verification_scores", ["observation_id"])
    op.create_index("ix_verif_scores_model", "verification_scores", ["model", "days_out"])
    op.create_index("ix_verif_scores_icao", "verification_scores", ["icao"])
    op.create_index("ix_verif_scores_lead", "verification_scores", ["lead_hours"])

    op.drop_table("airport_forecast_snapshots")
