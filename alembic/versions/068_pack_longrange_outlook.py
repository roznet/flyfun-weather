"""Add long-range outlook to briefing_packs.

Beyond the ECMWF GRIB horizon (~7 days) the digest is a soft long-range
*outlook* (TRENDING_SETTLED / MIXED_SIGNALS / TRENDING_UNSETTLED) rather than a
GREEN/AMBER/RED assessment. These columns let the pack carry the outlook so the
flight list and briefing page can show it instead of a traffic-light verdict.
Both nullable: NULL for short-range packs (which use ``assessment``) and legacy
packs created before this column existed. Mutually exclusive with ``assessment``.

Revision ID: 068
Revises: 067
Create Date: 2026-06-16
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "068"
down_revision: Union[str, None] = "067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.add_column(
            sa.Column("outlook", sa.String(length=32), nullable=True),
        )
        batch_op.add_column(
            sa.Column("outlook_reason", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.drop_column("outlook_reason")
        batch_op.drop_column("outlook")
