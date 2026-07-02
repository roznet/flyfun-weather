"""Add flights.flexibility — the timing-scenario Flexibility toggle.

Per-flight opt-in for the timing-scenario scan (timing-scenario-plan.md):
none | alternate | same_day | prev_day | next_day. Default "none" — local
yes/no flights do no scenario work. ``alt_departure_time`` is reused as the
"alternate" mode's value (no rename).

Batch mode per house rules: SQLite (dev) needs the copy-and-move, MySQL (prod)
treats it as a plain ALTER. ``server_default`` backfills existing rows.

Revision ID: 074
Revises: 073
Create Date: 2026-07-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "074"
down_revision: Union[str, None] = "073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.add_column(
            sa.Column(
                "flexibility",
                sa.String(16),
                nullable=False,
                server_default="none",
            )
        )
    # Back-compat: flights that already use the alt-departure feature keep
    # their alt grading — it now runs via the scenario job, gated on
    # flexibility="alternate" (the synchronous in-pipeline alt stage retires).
    op.execute(
        "UPDATE flights SET flexibility = 'alternate' "
        "WHERE alt_departure_time IS NOT NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.drop_column("flexibility")
