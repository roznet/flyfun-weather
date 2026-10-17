"""Progress source and fix time on live tick rows (#761).

Revision ID: 106
Revises: 105
Create Date: 2026-10-14

Adds ``live_tick_timing.progress_source`` (VARCHAR(16), nullable: ``plan`` /
``plan+takeoff`` / ``app``) and ``live_tick_timing.fix_at`` (the position fix
the tick measured from, null without one). The fix's age at use is
``committed_at - fix_at``, derived at read time like every other duration in
the table; it is what the ``UIBackgroundModes: location`` decision (#759) reads.
``DATETIME(6)`` on MySQL like the table's other datetimes (time-alignment-audit.md).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "106"
down_revision: Union[str, None] = "105"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("live_tick_timing") as batch_op:
        batch_op.add_column(sa.Column("progress_source", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column(
            "fix_at", sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table("live_tick_timing") as batch_op:
        batch_op.drop_column("fix_at")
        batch_op.drop_column("progress_source")
