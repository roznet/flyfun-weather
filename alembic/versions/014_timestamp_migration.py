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
    # Format as 'YYYY-MM-DD HH:MM:SS' (space separator, no timezone suffix)
    # to match SQLAlchemy's DateTime storage format in SQLite.
    op.execute(
        """
        UPDATE flights
        SET departure_time = target_date || ' '
            || substr('0' || target_time_utc, -2, 2)
            || ':00:00'
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

    # Normalize ISO strings to SQLAlchemy DateTime format:
    # replace 'T' with space, strip '+00:00' timezone suffix.
    op.execute(
        """
        UPDATE briefing_packs
        SET fetch_timestamp_dt = REPLACE(REPLACE(fetch_timestamp, 'T', ' '), '+00:00', '')
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

    # Normalize ISO strings to SQLAlchemy DateTime format.
    op.execute(
        """
        UPDATE feedback
        SET pack_timestamp_dt = CASE
            WHEN pack_timestamp IS NOT NULL AND pack_timestamp != ''
            THEN REPLACE(REPLACE(pack_timestamp, 'T', ' '), '+00:00', '')
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
