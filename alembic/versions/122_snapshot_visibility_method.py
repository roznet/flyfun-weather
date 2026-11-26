"""Which derived method filled a snapshot's visibility (#974, #979 phase 3).

Revision ID: 122
Revises: 121
Create Date: 2026-11-26

``airport_forecast_snapshots.visibility_method``: VARCHAR(16), nullable, no
default. NULL means ``visibility_m`` is the model's own visibility (or there
is none), which is every row written before this. A method name (``rh_icon``)
means the snapshot builder derived it, as it does for ICON on DWD's
native-grid feed, which publishes no VIS. The visibility experiment reads it
so a derived value is never scored as the model's own.

Archived Parquet files written before this lack the column; DuckDB reads
across the boundary with ``union_by_name=true`` (as for 116).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "122"
down_revision: Union[str, None] = "121"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.add_column(sa.Column("visibility_method", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.drop_column("visibility_method")
