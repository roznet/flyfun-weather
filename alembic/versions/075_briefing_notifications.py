"""Briefing-refresh notifications — per-flight notify override + badge seen state.

Adds the server-side state for APNs briefing-refresh notifications
(ios-app-briefing-notifications.md):

- ``flights.notify_override`` (default | notify | mute) — per-flight override of
  the global notify scope. Default ``"default"`` preserves today's behaviour
  (follow the global scope, which itself defaults to ``auto`` = scheduler-only).
- ``flight_briefing_seen`` — per-(user, flight) seen/notified state driving the
  server-derived, cross-surface badge count (mirrors the system-message unseen
  pattern, per-flight).

Batch mode per house rules: SQLite (dev) needs the copy-and-move for the
ALTER; MySQL (prod) treats it as a plain ALTER. ``create_table`` works on both
dialects without batch mode. ``server_default`` backfills existing flight rows.

Revision ID: 075
Revises: 074
Create Date: 2026-07-08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "075"
down_revision: Union[str, None] = "074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.add_column(
            sa.Column(
                "notify_override",
                sa.String(16),
                nullable=False,
                server_default="default",
            )
        )

    op.create_table(
        "flight_briefing_seen",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "flight_id",
            sa.String(256),
            sa.ForeignKey("flights.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("last_notified_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_ts", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "user_id", "flight_id", name="uq_flight_seen_user_flight"
        ),
    )
    op.create_index(
        "ix_flight_seen_user", "flight_briefing_seen", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_flight_seen_user", table_name="flight_briefing_seen")
    op.drop_table("flight_briefing_seen")
    with op.batch_alter_table("flights") as batch_op:
        batch_op.drop_column("notify_override")
