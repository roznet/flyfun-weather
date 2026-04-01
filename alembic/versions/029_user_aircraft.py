"""Add user_aircraft table and aircraft_id to flights.

Revision ID: 029
Revises: 028
Create Date: 2026-03-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_aircraft",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("icao_type", sa.String(4), nullable=False),
        sa.Column("tail_number", sa.String(10), nullable=True),
        sa.Column("nickname", sa.String(50), nullable=True),
        sa.Column("is_ifr", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("is_fiki", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("cruise_speed_kt", sa.Integer, nullable=True),
        sa.Column("ceiling_ft", sa.Integer, nullable=True),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    with op.batch_alter_table("flights") as batch_op:
        batch_op.add_column(sa.Column("aircraft_id", sa.Integer, nullable=True))
        batch_op.create_foreign_key(
            "fk_flights_aircraft_id", "user_aircraft", ["aircraft_id"], ["id"], ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.drop_constraint("fk_flights_aircraft_id", type_="foreignkey")
        batch_op.drop_column("aircraft_id")
    op.drop_table("user_aircraft")
