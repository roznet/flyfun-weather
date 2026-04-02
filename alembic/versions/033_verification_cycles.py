"""Add verification_cycles table for performance metrics.

Tracks timing and counts for each verification collection cycle
to monitor performance trends and detect server overload.

Revision ID: 033
Revises: 032
Create Date: 2026-04-02
"""

revision = "033"
down_revision = "032"

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.create_table(
        "verification_cycles",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.DateTime, nullable=False, index=True),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.Column("phase_finalize_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("phase_find_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("phase_gather_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("phase_fetch_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("phase_store_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("phase_score_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("flights", sa.Integer, nullable=False, server_default="0"),
        sa.Column("airports", sa.Integer, nullable=False, server_default="0"),
        sa.Column("observations_stored", sa.Integer, nullable=False, server_default="0"),
        sa.Column("finalized", sa.Integer, nullable=False, server_default="0"),
        sa.Column("scored", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("verification_cycles")
