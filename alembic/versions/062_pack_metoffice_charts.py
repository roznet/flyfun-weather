"""Add Met Office surface-pressure chart reference fields to briefing_packs.

Mirror of migration 052 (DWD charts) for the second front-chart source:
  - metoffice_charts_run_cycle: e.g. "2026-05-29T00Z" — the Met Office run
    this briefing was generated against. NULL when section is unavailable.
  - metoffice_charts_default_id: "ana" / "012" / "024" / "036" / "048" /
    "060" / "072" / "084" — chart whose valid time best brackets ETD.
  - metoffice_charts_in_coverage: route falls inside the Europe chart frame.
  - metoffice_charts_within_horizon: ETD <= run_cycle + 84h at briefing time.

Bytes live in the shared ``DATA_DIR/metoffice_charts/<run_cycle>/`` cache.

Revision ID: 062
Revises: 061
Create Date: 2026-05-29
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "062"
down_revision: Union[str, None] = "061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.add_column(
            sa.Column("metoffice_charts_run_cycle", sa.String(32), nullable=True),
        )
        batch_op.add_column(
            sa.Column("metoffice_charts_default_id", sa.String(8), nullable=True),
        )
        batch_op.add_column(
            sa.Column(
                "metoffice_charts_in_coverage",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            ),
        )
        batch_op.add_column(
            sa.Column(
                "metoffice_charts_within_horizon",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.drop_column("metoffice_charts_within_horizon")
        batch_op.drop_column("metoffice_charts_in_coverage")
        batch_op.drop_column("metoffice_charts_default_id")
        batch_op.drop_column("metoffice_charts_run_cycle")
