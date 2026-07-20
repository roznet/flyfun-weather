"""Add a region tag to airport_forecast_snapshots (EU/US seam).

Stage 2 of the US-watchlist expansion (`designs/future/us-expansion-plan.md`)
and the compute-offload region seam: a `region` column (`eu` / `us`) lets a
region's cycle run off-box and its artifact ingest without colliding, and lets
the forecast-map cache split per region. Existing rows are all European, so they
backfill to `eu` via a server_default; the column is NOT NULL going forward
(every writer sets it).

The companion index `(region, forecast_hour, model)` is added **alongside** the
existing `ix_afs_hour_model` rather than replacing it: the map serving query only
gains a leading `region=` predicate once it becomes region-aware (a later slice),
and until then dropping `ix_afs_hour_model` would reintroduce the table scan that
migration 077 fixed. A follow-up migration drops the redundant index once serving
filters by region.

Revision ID: 081
Revises: 080
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "081"
down_revision: Union[str, None] = "080"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.add_column(
            sa.Column(
                "region",
                sa.String(length=2),
                nullable=False,
                server_default="eu",
            )
        )
    op.create_index(
        "ix_afs_region_hour_model",
        "airport_forecast_snapshots",
        ["region", "forecast_hour", "model"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_afs_region_hour_model", table_name="airport_forecast_snapshots"
    )
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.drop_column("region")
