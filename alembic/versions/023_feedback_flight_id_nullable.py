"""Make feedback.flight_id nullable for general feedback not tied to a flight.

Revision ID: 023
Revises: 022
Create Date: 2026-03-22
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("feedback") as batch_op:
        batch_op.alter_column("flight_id", existing_type=sa.String(256), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("feedback") as batch_op:
        batch_op.alter_column("flight_id", existing_type=sa.String(256), nullable=False)
