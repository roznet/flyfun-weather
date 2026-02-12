"""Initial schema — users, preferences, flights, briefing_packs, usage_log.

Revision ID: 001
Revises: None
Create Date: 2026-02-12
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False, server_default="local"),
        sa.Column("provider_sub", sa.String(256), nullable=False, server_default=""),
        sa.Column("email", sa.String(256), nullable=False, server_default=""),
        sa.Column("display_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("approved", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "user_preferences",
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("defaults_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("encrypted_autorouter_creds", sa.Text, nullable=False, server_default=""),
        sa.Column("digest_config_json", sa.Text, nullable=False, server_default="{}"),
    )

    op.create_table(
        "flights",
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("route_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("waypoints_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("target_date", sa.String(10), nullable=False),
        sa.Column("target_time_utc", sa.Integer, nullable=False, server_default=sa.text("9")),
        sa.Column("cruise_altitude_ft", sa.Integer, nullable=False, server_default=sa.text("8000")),
        sa.Column("flight_ceiling_ft", sa.Integer, nullable=False, server_default=sa.text("18000")),
        sa.Column("flight_duration_hours", sa.Float, nullable=False, server_default=sa.text("0.0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "briefing_packs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "flight_id",
            sa.String(256),
            sa.ForeignKey("flights.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("fetch_timestamp", sa.String(64), nullable=False),
        sa.Column("days_out", sa.Integer, nullable=False),
        sa.Column("has_gramet", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("has_skewt", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("has_digest", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("assessment", sa.String(16), nullable=True),
        sa.Column("assessment_reason", sa.Text, nullable=True),
        sa.Column("artifact_path", sa.Text, nullable=False, server_default=""),
    )

    op.create_table(
        "usage_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("call_type", sa.String(64), nullable=False),
        sa.Column("detail_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("skipped", sa.Boolean, nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_table("usage_log")
    op.drop_table("briefing_packs")
    op.drop_table("flights")
    op.drop_table("user_preferences")
    op.drop_table("users")
