"""Add flight_subscriptions table for read-only flight sharing.

Revision ID: 044
Revises: 043
Create Date: 2026-04-21
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "044"
down_revision: Union[str, None] = "043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flight_subscriptions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "flight_id", sa.String(256),
            sa.ForeignKey("flights.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
        ),
        sa.UniqueConstraint("flight_id", "user_id", name="uq_flight_subs_flight_user"),
    )
    op.create_index("ix_flight_subs_flight", "flight_subscriptions", ["flight_id"])
    op.create_index("ix_flight_subs_user", "flight_subscriptions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_flight_subs_user", table_name="flight_subscriptions")
    op.drop_index("ix_flight_subs_flight", table_name="flight_subscriptions")
    op.drop_table("flight_subscriptions")
