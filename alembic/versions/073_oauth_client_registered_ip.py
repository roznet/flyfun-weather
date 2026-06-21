"""Add registered_ip to oauth_clients for DCR rate-limiting.

Dynamic Client Registration (RFC 7591) is intentionally open, but unbounded it
is a DB-spam / phishing-client factory. flyfun-common now throttles
``POST /oauth/register`` with a per-IP (and global) sliding window over the last
hour, counted against ``oauth_clients``. That needs the source IP recorded on
each registration.

Nullable with no server default: pre-existing clients (and dev-mode
registrations) carry NULL, which the rate limiter simply doesn't count — so this
migration is backward-compatible.

Uses ``batch_alter_table`` per project convention so the ALTER works on both
SQLite (dev) and MySQL (prod). ``oauth_clients`` lives in flyfun-common but
shares the same metadata/Base, so it is migrated here alongside the
weather-specific tables (mirrors migration 070).

Revision ID: 073
Revises: 072
Create Date: 2026-06-20
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "073"
down_revision: Union[str, None] = "072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("oauth_clients") as batch_op:
        batch_op.add_column(
            sa.Column("registered_ip", sa.String(64), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("oauth_clients") as batch_op:
        batch_op.drop_column("registered_ip")
