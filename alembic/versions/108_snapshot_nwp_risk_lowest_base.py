"""Native convective verdict and lowest cloud bases on snapshots (#798 follow-up).

Revision ID: 108
Revises: 107
Create Date: 2026-10-18

``airport_forecast_snapshots`` only, all columns nullable, no defaults:

- ``nwp_conv_risk`` (VARCHAR(16)): ``convective_nwp.risk_level``, the track the
  grader reads by default. ``sounding_convective_risk`` is the thermo track, so
  until now ``verify convective-score`` could only score the fallback.
- ``sounding_lowest_base_ft`` / ``nwp_layer_lowest_base_ft`` (FLOAT): lowest
  base of any DD / model-native layer, FEW and SCT included. The existing
  ``sounding_cloud_base_ft`` and ``nwp_layer_ceiling_ft`` are BKN/OVC only.

Forward-only: the forecast behind an older row is gone, and snapshots prune at
10 days. Archived Parquet files written before this lack the columns; DuckDB
reads across the boundary with ``union_by_name=true``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "108"
down_revision: Union[str, None] = "107"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.add_column(sa.Column("nwp_conv_risk", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("sounding_lowest_base_ft", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("nwp_layer_lowest_base_ft", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.drop_column("nwp_layer_lowest_base_ft")
        batch_op.drop_column("sounding_lowest_base_ft")
        batch_op.drop_column("nwp_conv_risk")
