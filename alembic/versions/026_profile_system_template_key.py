"""Add system_template_key column to flight_profiles.

Revision ID: 026
Revises: 025
Create Date: 2026-03-25
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("flight_profiles") as batch_op:
        batch_op.add_column(
            sa.Column("system_template_key", sa.String(50), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("flight_profiles") as batch_op:
        batch_op.drop_column("system_template_key")
