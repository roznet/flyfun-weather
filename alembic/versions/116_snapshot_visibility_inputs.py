"""Visibility-derivation inputs on forecast snapshots (#979 phase 0).

Revision ID: 116
Revises: 115
Create Date: 2026-11-08

``airport_forecast_snapshots`` only, all FLOAT, nullable, no defaults:
``surface_pressure_hpa``; the lowest model level's cloud water, cloud ice,
rain, snow (kg/kg) and cloud cover (%); T, Td and wind speed at 850 hPa.
They are what the derived-visibility and fog methods of #979 read beyond the
existing surface columns, logged now so the methods can be fitted against
DWD's ICON visibility before DWD stops publishing it on 2026-11-30.

Forward-only: snapshots prune at 10 days and the forecast behind an older row
is gone. Archived Parquet files written before this lack the columns; DuckDB
reads across the boundary with ``union_by_name=true`` (as for 108).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "116"
down_revision: Union[str, None] = "115"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    "surface_pressure_hpa",
    "lowest_level_cloud_water_kg_kg",
    "lowest_level_cloud_ice_kg_kg",
    "lowest_level_rain_kg_kg",
    "lowest_level_snow_kg_kg",
    "lowest_level_cloud_cover_pct",
    "t850_c",
    "td850_c",
    "wind_speed_850_kt",
)


def upgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        for name in _COLUMNS:
            batch_op.add_column(sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        for name in reversed(_COLUMNS):
            batch_op.drop_column(name)
