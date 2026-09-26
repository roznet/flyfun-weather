"""Record which client (iOS / web) submitted each feedback row.

Revision ID: 097
Revises: 096
Create Date: 2026-09-26

Adds ``feedback.client`` (``'ios'`` / ``'web'`` / ``'other'``) and the raw,
truncated ``feedback.user_agent`` it was classified from. Both nullable with
no default: existing rows genuinely don't know their client, and a guessed
backfill would be worse than NULL.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "097"
down_revision: Union[str, None] = "096"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("feedback") as batch_op:
        batch_op.add_column(sa.Column("client", sa.String(16), nullable=True))
        batch_op.add_column(sa.Column("user_agent", sa.String(256), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("feedback") as batch_op:
        batch_op.drop_column("user_agent")
        batch_op.drop_column("client")
