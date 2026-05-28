"""Add highlight flag to system_messages.

Decouples "appears in the What's New stream" from "lights the notification
dot". Every message still shows in the stream; only ``highlight=true`` rows
count toward a user's unseen badge, so frequent low-key releases can land
silently while a curated few attract attention.

Revision ID: 061
Revises: 060
Create Date: 2026-05-28
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "061"
down_revision: Union[str, None] = "060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("system_messages") as batch:
        batch.add_column(
            sa.Column(
                "highlight",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("system_messages") as batch:
        batch.drop_column("highlight")
