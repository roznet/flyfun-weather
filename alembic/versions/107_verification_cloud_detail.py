"""Cloud detail for the verification cycle (#793).

Revision ID: 107
Revises: 106
Create Date: 2026-10-17

Three tables, all columns nullable, no defaults:

- ``airport_forecast_snapshots.cloud_cover_mid_pct`` / ``cloud_cover_high_pct``
  (FLOAT). Every model already fetched them, but only total and low cover were
  stored. Forward-only: snapshots taken before this migration cannot gain them.
- ``verification_observations.cloud_layers`` (TEXT, JSON list),
  ``lowest_cloud_base_ft`` (INT) and ``convective_cloud`` (VARCHAR(3),
  ``CB``/``TCU``). Derived from ``metar_raw``, so history is recoverable with
  ``verify backfill-cloud-layers`` — deliberately not here: like 091's
  ``report_type`` backfill, scanning the whole table inside a deploy on the
  shared MySQL is the wrong place for it. NULL ``cloud_layers`` means "not
  derived yet".
- ``verification_scores.lowest_base_delta_ft`` (FLOAT): standalone model cloud
  base (AGL) minus that observed lowest base. Filled for scores written after
  the observation's cloud detail exists; old scores are not re-scored.

**Archive note.** All three tables are archived to Parquet (#522); files written
before this migration lack the columns. DuckDB reads across the boundary with
``union_by_name=true``, and ``archive.read_archived_rows`` returns None for them.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "107"
down_revision: Union[str, None] = "106"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.add_column(sa.Column("cloud_cover_mid_pct", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("cloud_cover_high_pct", sa.Float(), nullable=True))
    with op.batch_alter_table("verification_observations") as batch_op:
        batch_op.add_column(sa.Column("cloud_layers", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("lowest_cloud_base_ft", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("convective_cloud", sa.String(length=3), nullable=True))
    with op.batch_alter_table("verification_scores") as batch_op:
        batch_op.add_column(sa.Column("lowest_base_delta_ft", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("verification_scores") as batch_op:
        batch_op.drop_column("lowest_base_delta_ft")
    with op.batch_alter_table("verification_observations") as batch_op:
        batch_op.drop_column("convective_cloud")
        batch_op.drop_column("lowest_cloud_base_ft")
        batch_op.drop_column("cloud_layers")
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.drop_column("cloud_cover_high_pct")
        batch_op.drop_column("cloud_cover_mid_pct")
