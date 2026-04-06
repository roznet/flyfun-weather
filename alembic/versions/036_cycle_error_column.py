"""Add error column to verification_cycles and widen source column.

Records failure tracebacks so failed cycles are visible in the DB.
Widens source from 16 to 24 chars for 'standalone_full'/'standalone_light'.

Revision ID: 036
Revises: 035
Create Date: 2026-04-06
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "036"
down_revision: Union[str, None] = "035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("verification_cycles") as batch_op:
        batch_op.add_column(sa.Column("error", sa.Text, nullable=True))
        batch_op.alter_column(
            "source",
            existing_type=sa.String(16),
            type_=sa.String(24),
        )


def downgrade() -> None:
    with op.batch_alter_table("verification_cycles") as batch_op:
        batch_op.drop_column("error")
        batch_op.alter_column(
            "source",
            existing_type=sa.String(24),
            type_=sa.String(16),
        )
