"""Add sentiment/target/contact_ok to feedback for digest thumb ratings.

``sentiment`` ('up'/'down') and ``target`` ('digest') mark quick thumb
ratings submitted from the digest footer; both stay NULL for the
traditional feedback form. ``contact_ok`` records the user's explicit
consent to be emailed about this feedback — server_default "0" so all
pre-existing rows backfill to "no consent".

Revision ID: 065
Revises: 064
Create Date: 2026-06-12
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "065"
down_revision: Union[str, None] = "064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("feedback") as batch_op:
        batch_op.add_column(sa.Column("sentiment", sa.String(8), nullable=True))
        batch_op.add_column(sa.Column("target", sa.String(16), nullable=True))
        batch_op.add_column(
            sa.Column(
                "contact_ok",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("feedback") as batch_op:
        batch_op.drop_column("contact_ok")
        batch_op.drop_column("target")
        batch_op.drop_column("sentiment")
