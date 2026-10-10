"""Observed latency tables (#751).

Revision ID: 102
Revises: 101
Create Date: 2026-10-10

``live_tick_timing``: one row per flight per live tick (report time, fetch,
commit, highlight). ``live_delivery``: one row per new live version a client
received (``poll`` now, ``push`` reserved).

Datetimes are ``DATETIME(6)`` on MySQL (``TZDateTime(fsp=6)``): the delivery
join is an equality on ``live_updated_at``, which carries microseconds, and a
plain MySQL DATETIME would truncate them (time-alignment-audit.md). No
``server_default`` on the Text column (MySQL error 1101). No foreign keys:
account deletion removes the rows explicitly and a retention purge bounds
them.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "102"
down_revision: Union[str, None] = "101"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _dt(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(
        name, sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql"), nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "live_tick_timing",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("flight_id", sa.String(256), nullable=False),
        sa.Column("pack_timestamp", sa.String(64), nullable=True),
        _dt("tick_started_at", nullable=False),
        _dt("committed_at", nullable=False),
        sa.Column("tick_ms", sa.Integer(), nullable=True),
        sa.Column("flight_ms", sa.Integer(), nullable=True),
        _dt("metar_observed_at"),
        _dt("metar_fetched_at"),
        _dt("taf_issued_at"),
        _dt("taf_fetched_at"),
        _dt("sigmet_issued_at"),
        _dt("sigmet_fetched_at"),
        _dt("radar_frame_at"),
        _dt("cells_frame_at"),
        _dt("cells_computed_at"),
        _dt("cells_received_at"),
        sa.Column("highlight_outcome", sa.String(16), nullable=True),
        _dt("highlight_requested_at"),
        _dt("highlight_written_at"),
        sa.Column("highlight_latency_ms", sa.Integer(), nullable=True),
        sa.Column("new_reports", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("new_alerts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("new_items_json", sa.Text(), nullable=True),
    )
    op.create_index("ix_live_tick_timing_committed_at", "live_tick_timing", ["committed_at"])
    op.create_index(
        "ix_live_tick_timing_flight_committed", "live_tick_timing", ["flight_id", "committed_at"],
    )

    op.create_table(
        "live_delivery",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("flight_id", sa.String(256), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        _dt("served_live_updated_at", nullable=False),
        _dt("requested_at", nullable=False),
        sa.Column("delivered_via", sa.String(8), nullable=False, server_default="poll"),
        _dt("push_sent_at"),
        sa.UniqueConstraint(
            "flight_id", "user_id", "platform", "served_live_updated_at",
            name="uq_live_delivery_version",
        ),
    )
    op.create_index("ix_live_delivery_requested_at", "live_delivery", ["requested_at"])
    op.create_index("ix_live_delivery_user_id", "live_delivery", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_live_delivery_user_id", table_name="live_delivery")
    op.drop_index("ix_live_delivery_requested_at", table_name="live_delivery")
    op.drop_table("live_delivery")
    op.drop_index("ix_live_tick_timing_flight_committed", table_name="live_tick_timing")
    op.drop_index("ix_live_tick_timing_committed_at", table_name="live_tick_timing")
    op.drop_table("live_tick_timing")
