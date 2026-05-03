"""Add model_sources_json to briefing_packs (issue #108).

Records which source produced each model's data in a pack (e.g.
``{"ecmwf": "ecmwf:direct", "gfs": "gfs:openmeteo"}``).  Used by the
new marker-based freshness check to know which freshness marker to
compare against per model.

Nullable so legacy packs created before this column work fine — the
freshness check infers source from ``grib_init_times`` presence in
that case.

Revision ID: 050
Revises: 049
Create Date: 2026-05-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "050"
down_revision: Union[str, None] = "049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.add_column(
            sa.Column("model_sources_json", sa.Text, nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.drop_column("model_sources_json")
