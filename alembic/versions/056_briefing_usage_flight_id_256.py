"""Widen briefing_usage.flight_id from String(100) to String(256).

Matches the canonical ``flights.id`` width and every other table that
references a flight_id. The 100-char limit caused DataError(1406) for long
multi-waypoint routes (e.g. 119-char ids).

Revision ID: 056
Revises: 055
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "056"
down_revision: Union[str, None] = "055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("briefing_usage") as batch_op:
        batch_op.alter_column(
            "flight_id",
            existing_type=sa.String(100),
            type_=sa.String(256),
            existing_nullable=False,
            existing_server_default="",
        )


def downgrade() -> None:
    with op.batch_alter_table("briefing_usage") as batch_op:
        batch_op.alter_column(
            "flight_id",
            existing_type=sa.String(256),
            type_=sa.String(100),
            existing_nullable=False,
            existing_server_default="",
        )
