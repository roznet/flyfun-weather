"""Add pireps and device_tokens tables.

Revision ID: 030
Revises: 029
Create Date: 2026-03-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    """Check whether a table already exists (handles dev DBs where
    init_shared_db may have already called create_all)."""
    conn = op.get_bind()
    insp = sa.inspect(conn)
    return name in insp.get_table_names()


def _index_exists(name: str) -> bool:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    for table in insp.get_table_names():
        for idx in insp.get_indexes(table):
            if idx["name"] == name:
                return True
    return False


def upgrade() -> None:
    if not _table_exists("pireps"):
        op.create_table(
            "pireps",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("client_uuid", sa.String(36), unique=True, nullable=True),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("latitude", sa.Float, nullable=False),
            sa.Column("longitude", sa.Float, nullable=False),
            sa.Column("gps_altitude_ft", sa.Integer, nullable=True),
            sa.Column("reported_altitude_ft", sa.Integer, nullable=True),
            sa.Column("in_cloud", sa.Boolean, nullable=True),
            sa.Column("icing_intensity", sa.String(16), nullable=True),
            sa.Column("icing_type", sa.String(16), nullable=True),
            sa.Column("turbulence_intensity", sa.String(16), nullable=True),
            sa.Column("ceiling_msl_ft", sa.Integer, nullable=True),
            sa.Column("tops_msl_ft", sa.Integer, nullable=True),
            sa.Column("tops_basis", sa.String(16), nullable=True),
            sa.Column("temp_c", sa.Float, nullable=True),
            sa.Column("wind_dir", sa.Integer, nullable=True),
            sa.Column("wind_speed_kt", sa.Integer, nullable=True),
            sa.Column("remarks", sa.Text, nullable=True),
            sa.Column(
                "aircraft_id", sa.Integer,
                sa.ForeignKey("user_aircraft.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "pack_id", sa.Integer,
                sa.ForeignKey("briefing_packs.id", ondelete="SET NULL"),
                nullable=True, index=True,
            ),
            sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
            sa.Column(
                "user_id", sa.String(64),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True, index=True,
            ),
        )

    if not _index_exists("ix_pireps_observed_at"):
        op.create_index("ix_pireps_observed_at", "pireps", ["observed_at"])

    if not _table_exists("device_tokens"):
        op.create_table(
            "device_tokens",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "user_id", sa.String(64),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("token", sa.String(200), unique=True, nullable=False),
            sa.Column("environment", sa.String(16), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("device_tokens")
    op.drop_index("ix_pireps_observed_at", table_name="pireps")
    op.drop_table("pireps")
