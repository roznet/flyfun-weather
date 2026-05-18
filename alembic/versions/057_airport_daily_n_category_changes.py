"""Add n_category_changes to airport_daily_summary.

Mirrors ``airport_monthly_summary.category_changes`` at daily granularity so
the volatility map's month-to-date path can live-sum from daily rows while
completed months read straight from the monthly column.

The volatility logic (count of category transitions between consecutive obs
within the same UTC day) already exists in
``tasks/airport_summary.py:_category_changes_within_day`` and is now also
called from ``_build_daily_summary``.

Backfill at deploy time:

    python -m weatherbrief.verify rollup-summary --rebuild

This invokes ``airport_summary.rebuild_all_days`` which re-rolls every row
already in ``airport_daily_summary`` (DELETE+INSERT, idempotent). Plain
``rollup-summary``/``--all`` won't do — it skips dates already in the
table, so existing rows would keep ``n_category_changes`` at the server
default (0) and the volatility map's MTD sums would silently corrupt.

Revision ID: 057
Revises: 056
Create Date: 2026-05-17
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "057"
down_revision: Union[str, None] = "056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("airport_daily_summary") as batch:
        batch.add_column(
            sa.Column(
                "n_category_changes",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("airport_daily_summary") as batch:
        batch.drop_column("n_category_changes")
