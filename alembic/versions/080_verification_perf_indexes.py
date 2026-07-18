"""Verification query-shape indexes for the standalone-cycle cache rebuild (#448).

Two query families in the post-cycle cache rebuild had no index matching
their filter shape:

- ``verification_daily_stats``: the bias-leaderboard query filters
  ``(source, model, days_out)`` as constants with a ``date`` range. The
  existing indexes lead with ``date`` or ``icao``; the date-leading one only
  helps narrow ranges, and at 30d/90d MySQL fell back to full scans of the
  ~985K-row table (~9.5 s per key, 18 such keys per rebuild). The new index
  puts the equality columns first and the range column last.

- ``taf_verification_scores``: TAF pseudo-model stats are aggregated at query
  time from raw rows (no rollup — key shape differs), always filtered by
  ``(source, observation_time)``. Only ``observation_id`` and ``icao`` were
  indexed, so every TAF aggregate was a ~287K-row full scan.

Revision ID: 080
Revises: 079
"""

from typing import Sequence, Union

from alembic import op

revision: str = "080"
down_revision: Union[str, None] = "079"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_vds_source_model_days_date",
        "verification_daily_stats",
        ["source", "model", "days_out", "date"],
    )
    op.create_index(
        "ix_taf_verif_source_time",
        "taf_verification_scores",
        ["source", "observation_time"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_taf_verif_source_time", table_name="taf_verification_scores"
    )
    op.drop_index(
        "ix_vds_source_model_days_date", table_name="verification_daily_stats"
    )
