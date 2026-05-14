"""Add magic-link auth tables and users.email index.

Two new tables owned by flyfun-common's ``MagicLinkTokenRow`` and
``MagicLinkConsumeAttemptRow``. Created here so weather's migration
chain stays self-contained — each consumer app of flyfun-common ships
its own copy of the same schema change.

Also indexes ``users.email``: magic-link consume looks users up by
email on every sign-in, and the column was previously unindexed.

Revision ID: 056
Revises: 055
Create Date: 2026-05-14
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "056"
down_revision: Union[str, None] = "055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "magic_link_tokens",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(256), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("otp_code_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_ip", sa.String(64), nullable=True),
        sa.Index("ix_magic_link_tokens_email", "email"),
        sa.Index("ix_magic_link_tokens_token_hash", "token_hash", unique=True),
        sa.Index("ix_magic_link_tokens_created_at", "created_at"),
        sa.Index("ix_magic_link_tokens_requested_ip", "requested_ip"),
    )

    op.create_table(
        "magic_link_consume_attempts",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Index("ix_magic_link_consume_attempts_ip", "ip"),
        sa.Index("ix_magic_link_consume_attempts_attempted_at", "attempted_at"),
    )

    # users.email was previously unindexed. Magic-link consume runs
    # ``filter(UserRow.email == :email)`` on every sign-in, and the
    # column is also probed by admin-style email lookups.
    with op.batch_alter_table("users") as batch:
        batch.create_index("ix_users_email", ["email"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_email")
    op.drop_table("magic_link_consume_attempts")
    op.drop_table("magic_link_tokens")
