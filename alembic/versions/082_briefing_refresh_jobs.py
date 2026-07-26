"""Durable briefing-refresh job tracking (issue #499).

Refresh state used to live only in the in-memory ``_RefreshRegistry``. When the
container died mid-refresh (OOM, deploy, crash) nothing recorded that a refresh
had ever been in flight: the pack row is written at the *end*, and the pack
directory created up front is an orphan with no row pointing at it. This table
is the write-through mirror of the registry, so an interrupted refresh leaves a
durable record that boot-time reconciliation can resume — and that a
post-mortem can read.

``flight_id`` is deliberately **not** a foreign key: the row must outlive the
flight so reconciliation can distinguish "flight was deleted, don't resume"
from "no record at all". ``user_id`` does cascade so account deletion takes the
job history with it (the explicit sweep in ``app._on_delete_user`` covers the
bulk-delete path, which doesn't emit ORM cascades).

``create_table`` / ``create_index`` work on SQLite (dev) and MySQL (prod)
without batch mode.

Revision ID: 082
Revises: 081
Create Date: 2026-07-26
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "082"
down_revision: Union[str, None] = "081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "briefing_refresh_jobs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("flight_id", sa.String(256), nullable=False),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        # "user" | "scheduler" | "resume" — the registry's queue-cap class.
        sa.Column("triggered_by", sa.String(16), nullable=False, server_default="user"),
        # Client-declared attribution ("user" | "siri" | "mcp"), carried so a
        # resumed refresh keeps the original surface in usage accounting.
        sa.Column("source", sa.String(16), nullable=True),
        sa.Column("as_of_date", sa.Date, nullable=True),
        # queued | running | succeeded | skipped | failed | abandoned
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        # Last pipeline stage seen — the "where did it die" half of a post-mortem.
        sa.Column("stage", sa.String(64), nullable=True),
        sa.Column("pack_path", sa.String(512), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
    )
    # Boot-time reconciliation scans by status (non-terminal rows only).
    op.create_index("ix_refresh_jobs_status", "briefing_refresh_jobs", ["status"])
    # /refresh/status falls back to the newest row for a flight.
    op.create_index(
        "ix_refresh_jobs_flight", "briefing_refresh_jobs", ["flight_id", "created_at"]
    )
    # Account deletion sweeps this table by user_id (app._on_delete_user), and
    # it only grows — matches the indexed FK-to-users pattern used elsewhere.
    op.create_index("ix_refresh_jobs_user", "briefing_refresh_jobs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_refresh_jobs_user", table_name="briefing_refresh_jobs")
    op.drop_index("ix_refresh_jobs_flight", table_name="briefing_refresh_jobs")
    op.drop_index("ix_refresh_jobs_status", table_name="briefing_refresh_jobs")
    op.drop_table("briefing_refresh_jobs")
