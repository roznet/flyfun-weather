"""Add verification tables for METAR/TAF accuracy tracking.

New tables:
- verification_observations: standalone METAR/TAF ground truth archive
- verification_scores: model-vs-METAR accuracy records
- taf_verification_scores: TAF-vs-METAR accuracy records
- flight_verification_map: thin flight-to-observation linkage

New column on flights:
- verification_status: NULL / "collecting" / "complete"

Revision ID: 029
Revises: 028
Create Date: 2026-04-01
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- verification_observations ---
    op.create_table(
        "verification_observations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("icao", sa.String(4), nullable=False),
        sa.Column("observation_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        # METAR fields
        sa.Column("metar_raw", sa.Text, nullable=True),
        sa.Column("flight_category", sa.String(4), nullable=True),
        sa.Column("ceiling_ft", sa.Integer, nullable=True),
        sa.Column("visibility_m", sa.Integer, nullable=True),
        sa.Column("wind_dir", sa.Integer, nullable=True),
        sa.Column("wind_speed_kt", sa.Integer, nullable=True),
        sa.Column("wind_gust_kt", sa.Integer, nullable=True),
        sa.Column("temperature_c", sa.Integer, nullable=True),
        sa.Column("dewpoint_c", sa.Integer, nullable=True),
        sa.Column("qnh", sa.Float, nullable=True),
        sa.Column("weather", sa.Text, nullable=True),
        # TAF fields
        sa.Column("taf_raw", sa.Text, nullable=True),
        sa.Column("taf_applicable", sa.Text, nullable=True),
        sa.Column("taf_issue_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("taf_flight_category", sa.String(4), nullable=True),
        sa.Column("taf_ceiling_ft", sa.Integer, nullable=True),
        sa.Column("taf_visibility_m", sa.Integer, nullable=True),
        sa.Column("taf_wind_dir", sa.Integer, nullable=True),
        sa.Column("taf_wind_speed_kt", sa.Integer, nullable=True),
        sa.Column("taf_wind_gust_kt", sa.Integer, nullable=True),
        sa.UniqueConstraint("icao", "observation_time", name="uq_verif_obs_icao_time"),
    )
    op.create_index("ix_verif_obs_icao", "verification_observations", ["icao"])
    op.create_index("ix_verif_obs_time", "verification_observations", ["observation_time"])

    # --- verification_scores ---
    op.create_table(
        "verification_scores",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "observation_id", sa.Integer,
            sa.ForeignKey("verification_observations.id", ondelete="CASCADE"), nullable=False,
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
        sa.Column("ceiling_delta_ft", sa.Integer, nullable=True),
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

    # --- taf_verification_scores ---
    op.create_table(
        "taf_verification_scores",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "observation_id", sa.Integer,
            sa.ForeignKey("verification_observations.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("icao", sa.String(4), nullable=False),
        sa.Column("observation_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("taf_issue_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_hours", sa.Integer, nullable=False),
        sa.Column("obs_flight_category", sa.String(4), nullable=True),
        sa.Column("taf_flight_category", sa.String(4), nullable=True),
        sa.Column("category_match", sa.Boolean, nullable=True),
        sa.Column("ceiling_delta_ft", sa.Integer, nullable=True),
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

    # --- flight_verification_map ---
    op.create_table(
        "flight_verification_map",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "flight_id", sa.String(256),
            sa.ForeignKey("flights.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("icao", sa.String(4), nullable=False),
        sa.Column(
            "observation_id", sa.Integer,
            sa.ForeignKey("verification_observations.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("distance_from_route_nm", sa.Float, nullable=True),
        sa.UniqueConstraint(
            "flight_id", "icao", "observation_id",
            name="uq_fvm_flight_icao_obs",
        ),
    )
    op.create_index("ix_fvm_flight", "flight_verification_map", ["flight_id"])
    op.create_index("ix_fvm_icao", "flight_verification_map", ["icao"])

    # --- verification_status column on flights ---
    with op.batch_alter_table("flights") as batch_op:
        batch_op.add_column(
            sa.Column("verification_status", sa.String(16), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.drop_column("verification_status")

    op.drop_table("flight_verification_map")
    op.drop_table("taf_verification_scores")
    op.drop_table("verification_scores")
    op.drop_table("verification_observations")
