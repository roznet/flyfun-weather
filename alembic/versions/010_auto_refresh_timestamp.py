"""Replace last_auto_refresh_date (String) with last_auto_refresh_at (DateTime).

Switches from date-string tracking to full UTC timestamps, enabling the
min(last_refresh + 1d, flight_start − 2h) scheduling formula.

Revision ID: 010
Revises: 009
Create Date: 2026-02-26
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "flights",
        sa.Column("last_auto_refresh_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Best-effort migration: convert "YYYY-MM-DD" dates to midnight UTC.
    # Existing rows are few, and a NULL is fine (the scheduler will just
    # treat them as never-refreshed and pick them up on the next cycle).
    op.execute(
        sa.text("""
            UPDATE flights
            SET last_auto_refresh_at = CONCAT(last_auto_refresh_date, 'T00:00:00+00:00')
            WHERE last_auto_refresh_date IS NOT NULL
        """)
    )
    op.drop_column("flights", "last_auto_refresh_date")


def downgrade() -> None:
    op.add_column(
        "flights",
        sa.Column("last_auto_refresh_date", sa.String(10), nullable=True),
    )
    op.execute(
        sa.text("""
            UPDATE flights
            SET last_auto_refresh_date = DATE(last_auto_refresh_at)
            WHERE last_auto_refresh_at IS NOT NULL
        """)
    )
    op.drop_column("flights", "last_auto_refresh_at")
