"""Drop weather_code from airport_forecast_snapshots.

Revision ID: 048
Revises: 047
Create Date: 2026-04-27

The WMO weather_code is not consumed by verification scoring and only
appeared in two lines of the LLM digest prompt. Direct ECMWF GRIB
doesn't deliver it, and synthesizing one isn't worth the work — so we
drop the column rather than carry it through the GRIB-first cutover.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "048"
down_revision: Union[str, None] = "047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.drop_column("weather_code")


def downgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.add_column(sa.Column("weather_code", sa.Integer, nullable=True))
