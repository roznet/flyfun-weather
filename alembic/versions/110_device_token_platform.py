"""Add ``platform`` to ``device_tokens`` and widen ``token`` for Android FCM.

Revision ID: 110
Revises: 109
Create Date: 2026-10-18

Android FCM registration tokens can exceed 200 characters (typically 160-250
chars), whereas APNs hex tokens are 64-160 chars. Widens ``device_tokens.token``
to ``VARCHAR(512)`` and adds ``platform`` (``ios`` | ``android``, default
``ios``) so ``notify/push.py`` can route each registered device token to APNs
or FCM HTTP v1.

Uses ``batch_alter_table`` per house rules so SQLite (dev) and MySQL (prod)
both handle the column addition and length alteration cleanly.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "110"
down_revision: Union[str, None] = "109"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("device_tokens") as batch_op:
        batch_op.add_column(
            sa.Column(
                "platform",
                sa.String(16),
                nullable=False,
                server_default="ios",
            )
        )
        batch_op.alter_column(
            "token",
            existing_type=sa.String(200),
            type_=sa.String(512),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("device_tokens") as batch_op:
        batch_op.alter_column(
            "token",
            existing_type=sa.String(512),
            type_=sa.String(200),
            existing_nullable=False,
        )
        batch_op.drop_column("platform")
