"""Climb and descent performance on the aircraft (#1005).

Revision ID: 119
Revises: 118
Create Date: 2026-11-17

Pilot overrides for the flight path the briefing computes (climb, cruise,
descent). All optional: NULL means "use the class preset of the aircraft's
ICAO category". ``is_turbo`` picks the turbo-piston preset for an SEP type.

* ``is_turbo`` (BOOLEAN, default false)
* ``climb_ias_kt`` (INTEGER, nullable): climb speed, IAS
* ``climb_rate_fpm`` (INTEGER, nullable): rate of climb near sea level
* ``descent_rate_fpm`` (INTEGER, nullable): fixed descent rate
* ``descent_angle_deg`` (FLOAT, nullable): descent path angle; wins over the rate
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "119"
down_revision: Union[str, None] = "118"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("user_aircraft") as batch_op:
        batch_op.add_column(sa.Column(
            "is_turbo", sa.Boolean(), nullable=False, server_default=sa.text("0"),
        ))
        batch_op.add_column(sa.Column("climb_ias_kt", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("climb_rate_fpm", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("descent_rate_fpm", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("descent_angle_deg", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_aircraft") as batch_op:
        batch_op.drop_column("descent_angle_deg")
        batch_op.drop_column("descent_rate_fpm")
        batch_op.drop_column("climb_rate_fpm")
        batch_op.drop_column("climb_ias_kt")
        batch_op.drop_column("is_turbo")
