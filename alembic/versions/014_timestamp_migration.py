"""Migrate time fields to proper timestamps.

- flights: target_date + target_time_utc → departure_time (DateTime tz-aware)
- briefing_packs: fetch_timestamp String → DateTime tz-aware
- feedback: pack_timestamp String → DateTime tz-aware, nullable

Revision ID: 014
Revises: 013
Create Date: 2026-02-27
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- flights: target_date + target_time_utc → departure_time ---
    with op.batch_alter_table("flights") as batch_op:
        batch_op.add_column(
            sa.Column("departure_time", sa.DateTime(timezone=True), nullable=True)
        )

    # Populate departure_time from target_date + target_time_utc
    # SQLite: build ISO string, then store as text (SQLAlchemy handles parsing)
    op.execute(
        """
        UPDATE flights
        SET departure_time = target_date || 'T'
            || substr('0' || target_time_utc, -2, 2)
            || ':00:00+00:00'
        """
    )

    with op.batch_alter_table("flights") as batch_op:
        batch_op.alter_column("departure_time", nullable=False)
        batch_op.drop_column("target_date")
        batch_op.drop_column("target_time_utc")

    # --- briefing_packs: fetch_timestamp String → DateTime ---
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.add_column(
            sa.Column("fetch_timestamp_dt", sa.DateTime(timezone=True), nullable=True)
        )

    op.execute(
        """
        UPDATE briefing_packs
        SET fetch_timestamp_dt = fetch_timestamp
        """
    )

    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.drop_column("fetch_timestamp")
        batch_op.alter_column("fetch_timestamp_dt", new_column_name="fetch_timestamp", nullable=False)

    # --- feedback: pack_timestamp String → DateTime, nullable ---
    with op.batch_alter_table("feedback") as batch_op:
        batch_op.add_column(
            sa.Column("pack_timestamp_dt", sa.DateTime(timezone=True), nullable=True)
        )

    op.execute(
        """
        UPDATE feedback
        SET pack_timestamp_dt = CASE
            WHEN pack_timestamp IS NOT NULL AND pack_timestamp != ''
            THEN pack_timestamp
            ELSE NULL
        END
        """
    )

    with op.batch_alter_table("feedback") as batch_op:
        batch_op.drop_column("pack_timestamp")
        batch_op.alter_column("pack_timestamp_dt", new_column_name="pack_timestamp")


def downgrade() -> None:
    # --- feedback: DateTime → String ---
    with op.batch_alter_table("feedback") as batch_op:
        batch_op.add_column(
            sa.Column("pack_timestamp_str", sa.String(64), nullable=True, server_default="")
        )

    op.execute(
        """
        UPDATE feedback
        SET pack_timestamp_str = COALESCE(pack_timestamp, '')
        """
    )

    with op.batch_alter_table("feedback") as batch_op:
        batch_op.drop_column("pack_timestamp")
        batch_op.alter_column("pack_timestamp_str", new_column_name="pack_timestamp")

    # --- briefing_packs: DateTime → String ---
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.add_column(
            sa.Column("fetch_timestamp_str", sa.String(64), nullable=True)
        )

    op.execute(
        """
        UPDATE briefing_packs
        SET fetch_timestamp_str = fetch_timestamp
        """
    )

    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.drop_column("fetch_timestamp")
        batch_op.alter_column("fetch_timestamp_str", new_column_name="fetch_timestamp", nullable=False)

    # --- flights: departure_time → target_date + target_time_utc ---
    with op.batch_alter_table("flights") as batch_op:
        batch_op.add_column(
            sa.Column("target_date", sa.String(10), nullable=True)
        )
        batch_op.add_column(
            sa.Column("target_time_utc", sa.Integer, nullable=True, server_default="9")
        )

    # Extract date and hour from departure_time
    op.execute(
        """
        UPDATE flights
        SET target_date = substr(departure_time, 1, 10),
            target_time_utc = CAST(substr(departure_time, 12, 2) AS INTEGER)
        """
    )

    with op.batch_alter_table("flights") as batch_op:
        batch_op.alter_column("target_date", nullable=False)
        batch_op.alter_column("target_time_utc", nullable=False)
        batch_op.drop_column("departure_time")
