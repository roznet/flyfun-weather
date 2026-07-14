"""Index airport_forecast_snapshots by (forecast_hour, model).

``compute_day_availability`` asks "for each day in the horizon, which sample
hours and which models actually have data?" — a DISTINCT over (forecast_hour,
model) across a forward window. None of the existing indexes lead with
forecast_hour (``(icao, forecast_hour)``, ``(fetched_at)``,
``(model, model_init_time)``), so that query degenerates to a full table scan:
~865k rows in production, ~169k of them inside the window, on every map page
load, awaited before first render.

This index makes it an index-only range scan over exactly the rows in the
window, and the leading forecast_hour lets the range be seeked rather than
scanned.

Revision ID: 077
Revises: 076
"""

from typing import Sequence, Union

from alembic import op

revision: str = "077"
down_revision: Union[str, None] = "076"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_afs_hour_model",
        "airport_forecast_snapshots",
        ["forecast_hour", "model"],
    )


def downgrade() -> None:
    op.drop_index("ix_afs_hour_model", table_name="airport_forecast_snapshots")
