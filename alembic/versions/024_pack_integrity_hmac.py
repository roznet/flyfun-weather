"""Add integrity_hmac column to briefing_packs for tamper detection.

Revision ID: 024
Revises: 023
Create Date: 2026-03-23
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.add_column(sa.Column("integrity_hmac", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.drop_column("integrity_hmac")
