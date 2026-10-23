"""``users.last_seen_at`` and the ``mcp_tool_calls`` log (#873).

* ``users.last_seen_at`` — written by flyfun-common >= 0.6.13's
  ``current_user_id`` for first-party sessions (10-min resolution). The
  ``users`` table is shared by every flyfun app on this DB and only this
  Alembic creates its columns, so this revision must be live before any app
  runs flyfun-common 0.6.13 (its ``UserRow`` selects the column).
* ``mcp_tool_calls`` — one row per MCP tool call, posted by the MCP server.

Nullable column and a plain ``create_table``: no backfill, no LOB defaults.

Revision ID: 112
Revises: 111
Create Date: 2026-10-23
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "112"
down_revision: Union[str, None] = "111"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "mcp_tool_calls",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_mcp_tool_calls_user_id"),
            nullable=False,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("ok", sa.Boolean, nullable=False),
        sa.Column("elapsed_ms", sa.Integer, nullable=True),
    )
    op.create_index("ix_mcp_tool_calls_ts", "mcp_tool_calls", ["ts"])
    op.create_index("ix_mcp_tool_calls_user_id", "mcp_tool_calls", ["user_id"])


def downgrade() -> None:
    op.drop_table("mcp_tool_calls")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("last_seen_at")
