"""Roll ``lowest_base_delta_ft`` up into the verification aggregates (#806).

Revision ID: 109
Revises: 108
Create Date: 2026-10-18

``verification_scores.lowest_base_delta_ft`` (migration 107) never reached a
rollup, so the dashboard-scale question "how far off is each model's lowest
cloud base?" could only be asked of raw scores. Follows the gust pattern (083):

- ``verification_daily_stats`` and ``verification_global_daily_stats`` gain the
  additive trio ``n_lowest_base`` (INT NOT NULL DEFAULT 0),
  ``sum_abs_lowest_base_delta_ft`` and ``sum_lowest_base_delta_ft`` (FLOAT
  NULL), so any window composes by addition.
- ``verification_monthly_stats`` gains the composed ``lowest_base_mae_ft`` /
  ``lowest_base_bias_ft`` (FLOAT NULL) and ``n_lowest_base`` (INT NULL).

Existing rollup rows keep 0/NULL until their day/month is re-rolled
(``verify rollup-daily-stats --rebuild``). ``server_default`` is on INTEGER
only, never a TEXT/JSON column, so it is MySQL-safe.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "109"
down_revision: Union[str, None] = "108"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DAILY_TABLES = ("verification_daily_stats", "verification_global_daily_stats")


def upgrade() -> None:
    for table in _DAILY_TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "n_lowest_base", sa.Integer(), nullable=False,
                    server_default="0",
                )
            )
            batch_op.add_column(
                sa.Column("sum_abs_lowest_base_delta_ft", sa.Float(), nullable=True)
            )
            batch_op.add_column(
                sa.Column("sum_lowest_base_delta_ft", sa.Float(), nullable=True)
            )

    with op.batch_alter_table("verification_monthly_stats") as batch_op:
        batch_op.add_column(
            sa.Column("lowest_base_mae_ft", sa.Float(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("lowest_base_bias_ft", sa.Float(), nullable=True)
        )
        batch_op.add_column(sa.Column("n_lowest_base", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("verification_monthly_stats") as batch_op:
        for col in ("n_lowest_base", "lowest_base_bias_ft", "lowest_base_mae_ft"):
            batch_op.drop_column(col)

    for table in reversed(_DAILY_TABLES):
        with op.batch_alter_table(table) as batch_op:
            for col in (
                "sum_lowest_base_delta_ft",
                "sum_abs_lowest_base_delta_ft",
                "n_lowest_base",
            ):
                batch_op.drop_column(col)
