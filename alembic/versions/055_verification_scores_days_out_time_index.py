"""Add composite index on (source, days_out, observation_time) to verification_scores.

The verification map cache rebuild runs 16 aggregates per cycle, each filtering
``WHERE source=? AND days_out=? AND observation_time BETWEEN ?``.  Migration 038
added ``(source, observation_time)`` which already helps the stats queries that
group across all days_out, but the map query also pins days_out and would scan
4x more rows than necessary with that index.  This composite lets the planner
jump straight to the relevant slice.

Revision ID: 055
Revises: 054
"""

revision = "055"
down_revision = "054"

from alembic import op


def upgrade() -> None:
    op.create_index(
        "ix_verif_scores_source_days_time",
        "verification_scores",
        ["source", "days_out", "observation_time"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_verif_scores_source_days_time",
        table_name="verification_scores",
    )
