"""Retention pin on flights.

Revision ID: 101
Revises: 100
Create Date: 2026-10-10

Adds ``flights.retention_pinned`` (BOOLEAN, default false). A pinned flight's
packs are exempt from every retention tier (T1 heavy-artifact strip, T2 full
delete, live-layer purge) — for flights a talk or a write-up links to, which
must keep rendering their full briefing long after departure. Set with
``scripts/ops/pin_flight.py``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "101"
down_revision: Union[str, None] = "100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.add_column(
            sa.Column(
                "retention_pinned", sa.Boolean(), nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.drop_column("retention_pinned")
