"""Session-epoch revocation + magic-link OTP attempt cap.

Two columns owned by flyfun-common (>=0.4.1), added here so weather's
migration chain stays self-contained (each consumer app ships its own copy):

* ``users.tokens_valid_after`` — session-epoch revocation. Any JWT whose
  ``iat`` predates this timestamp is rejected by ``current_user_id``. Set on
  "log out everywhere" / suspected compromise to kill all of a user's
  outstanding (and self-renewing) tokens at once. NULL = never revoked.
* ``magic_link_tokens.attempt_count`` — failed-OTP counter. The token is
  burned once it reaches the cap, bounding brute force of the 6-digit code.

Revision ID: 060
Revises: 059
Create Date: 2026-05-20
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "060"
down_revision: Union[str, None] = "059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("magic_link_tokens") as batch:
        batch.add_column(
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )

    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "tokens_valid_after",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("tokens_valid_after")

    with op.batch_alter_table("magic_link_tokens") as batch:
        batch.drop_column("attempt_count")
