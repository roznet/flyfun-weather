"""Record the accumulation window behind a snapshot's precipitation.

Open-Meteo (GFS, ICON) reports hourly precipitation. ECMWF is decoded from
GRIB fields that accumulate since init, so a per-period value is a difference
against the preceding delivered step — and ECMWF thins its step cadence with
lead time (hourly to 90 h, 3-hourly to 144 h, 6-hourly beyond). A far-out row
therefore covers 3 h or 6 h, not 1 h. Storing the window makes that explicit
rather than leaving a 6 h total to be read as an hourly one.

NULL on rows written before this column existed, and on the first delivered
step of a run (nothing to difference against).

Revision ID: 076
Revises: 075
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "076"
down_revision: Union[str, None] = "075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.add_column(sa.Column("precip_period_h", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.drop_column("precip_period_h")
