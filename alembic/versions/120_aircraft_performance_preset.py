"""The climb / descent preset picked in the aircraft form (#1005).

Revision ID: 120
Revises: 119
Create Date: 2026-11-17

The aircraft form now has an explicit preset dropdown: the pilot picks a
preset (an id of ``analysis/flight_path.PRESETS``) and the climb / descent
columns of 119 (and ``is_turbo``) are overrides on top of it.

* ``performance_preset`` (VARCHAR(24), nullable): NULL = follow the ICAO type
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "120"
down_revision: Union[str, None] = "119"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("user_aircraft") as batch_op:
        batch_op.add_column(sa.Column("performance_preset", sa.String(length=24), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_aircraft") as batch_op:
        batch_op.drop_column("performance_preset")
