"""Add DWD chart reference fields to briefing_packs.

Four scalar fields per pack:
  - dwd_charts_run_cycle: e.g. "2026-05-08T06Z" — the ICON run cycle this
    briefing was generated against. NULL when section is unavailable.
  - dwd_charts_default_id: "ana" / "036" / "048" / "060" / "084" / "108" —
    the chart whose estimated valid time best brackets ETD.
  - dwd_charts_in_coverage: route falls inside DWD's North Atlantic / Europe
    chart frame.
  - dwd_charts_within_horizon: ETD <= run_cycle + 108h at briefing time.

Bytes are NOT stored on the pack — they live in the shared
``DATA_DIR/dwd_charts/<run_cycle>/`` cache. Cache misses at render time
render an "unavailable" placeholder.

Revision ID: 052
Revises: 051
Create Date: 2026-05-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "052"
down_revision: Union[str, None] = "051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.add_column(
            sa.Column("dwd_charts_run_cycle", sa.String(32), nullable=True),
        )
        batch_op.add_column(
            sa.Column("dwd_charts_default_id", sa.String(8), nullable=True),
        )
        batch_op.add_column(
            sa.Column(
                "dwd_charts_in_coverage",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            ),
        )
        batch_op.add_column(
            sa.Column(
                "dwd_charts_within_horizon",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.drop_column("dwd_charts_within_horizon")
        batch_op.drop_column("dwd_charts_in_coverage")
        batch_op.drop_column("dwd_charts_default_id")
        batch_op.drop_column("dwd_charts_run_cycle")
