"""Add verification_cache table for pre-computed dashboard responses.

API endpoints serve cached JSON instead of running expensive aggregation
queries.  Cache is rebuilt after each standalone verification cycle.

Revision ID: 039
Revises: 038
"""

revision = "039"
down_revision = "038"

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    op.create_table(
        "verification_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("cache_key", sa.String(128), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_max_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("cache_key", name="uq_vcache_key"),
    )


def downgrade() -> None:
    op.drop_table("verification_cache")
