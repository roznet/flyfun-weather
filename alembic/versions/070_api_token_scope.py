"""Add OAuth scope column to api_tokens for least-privilege scoped tokens.

Phase 2 of #274. OAuth access tokens issued via the authorization-code / refresh
flows now carry the granted scope (e.g. ``flights:read``) so that scope can be
enforced per-endpoint (see ``flyfun_common.db.deps.register_scope_paths``).

Nullable with no server default: existing tokens, cookie sessions, and manually
created API tokens have NULL scope, which the enforcement layer treats as
unrestricted (full access) — so this migration is backward-compatible.

Uses ``batch_alter_table`` per project convention so the ALTER works on both
SQLite (dev) and MySQL (prod). The ``api_tokens`` table lives in flyfun-common
but shares the same metadata/Base, so it is migrated here alongside the
weather-specific tables.

Revision ID: 070
Revises: 069
Create Date: 2026-06-18
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "070"
down_revision: Union[str, None] = "069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("api_tokens") as batch_op:
        batch_op.add_column(
            sa.Column("scope", sa.String(256), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("api_tokens") as batch_op:
        batch_op.drop_column("scope")
