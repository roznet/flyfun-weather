"""Add composite index on (source, observation_time) to verification_scores.

Every dashboard query filters by source + observation_time range, but the
existing indexes don't cover this combination, forcing MySQL to scan half
the table.  This index lets the planner jump straight to the relevant rows.

Revision ID: 038
Revises: 037
"""

revision = "038"
down_revision = "037"

from alembic import op


def upgrade() -> None:
    op.create_index(
        "ix_verif_scores_source_time",
        "verification_scores",
        ["source", "observation_time"],
    )


def downgrade() -> None:
    op.drop_index("ix_verif_scores_source_time", table_name="verification_scores")
