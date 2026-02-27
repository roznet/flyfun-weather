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


def _is_mysql() -> bool:
    return op.get_bind().dialect.name == "mysql"


def upgrade() -> None:
    mysql = _is_mysql()

    # --- flights: target_date + target_time_utc → departure_time ---
    # On MySQL, the column may already exist from a partial run (DDL autocommit).
    if mysql:
        cols = [r[0] for r in op.get_bind().execute(sa.text("DESCRIBE flights"))]
        if "departure_time" not in cols:
            op.add_column("flights", sa.Column("departure_time", sa.DateTime(timezone=True), nullable=True))
    else:
        with op.batch_alter_table("flights") as batch_op:
            batch_op.add_column(
                sa.Column("departure_time", sa.DateTime(timezone=True), nullable=True)
            )

    if mysql:
        # MySQL: use CONCAT + LPAD, and STR_TO_DATE for proper DATETIME
        op.execute(sa.text(
            """
            UPDATE flights
            SET departure_time = STR_TO_DATE(
                CONCAT(target_date, ' ', LPAD(target_time_utc, 2, '0'), ':00:00'),
                '%Y-%m-%d %H:%i:%s'
            )
            """
        ))
    else:
        # SQLite: use || concatenation + substr
        op.execute(sa.text(
            """
            UPDATE flights
            SET departure_time = target_date || ' '
                || substr('0' || target_time_utc, -2, 2)
                || ':00:00'
            """
        ))

    if mysql:
        op.alter_column("flights", "departure_time", nullable=False, existing_type=sa.DateTime(timezone=True))
        op.drop_column("flights", "target_date")
        op.drop_column("flights", "target_time_utc")
    else:
        with op.batch_alter_table("flights") as batch_op:
            batch_op.alter_column("departure_time", nullable=False)
            batch_op.drop_column("target_date")
            batch_op.drop_column("target_time_utc")

    # --- briefing_packs: fetch_timestamp String → DateTime ---
    if mysql:
        # MySQL: add temp column, populate via STR_TO_DATE, drop old, rename
        op.add_column("briefing_packs", sa.Column("fetch_timestamp_dt", sa.DateTime(timezone=True), nullable=True))
        # Parse ISO string: replace T with space, strip +00:00, then cast
        op.execute(sa.text(
            """
            UPDATE briefing_packs
            SET fetch_timestamp_dt = STR_TO_DATE(
                REPLACE(REPLACE(fetch_timestamp, 'T', ' '), '+00:00', ''),
                '%Y-%m-%d %H:%i:%s.%f'
            )
            """
        ))
        op.drop_column("briefing_packs", "fetch_timestamp")
        op.alter_column("briefing_packs", "fetch_timestamp_dt", new_column_name="fetch_timestamp",
                         nullable=False, existing_type=sa.DateTime(timezone=True))
    else:
        with op.batch_alter_table("briefing_packs") as batch_op:
            batch_op.add_column(
                sa.Column("fetch_timestamp_dt", sa.DateTime(timezone=True), nullable=True)
            )
        # Normalize ISO strings to SQLAlchemy DateTime format for SQLite.
        op.execute(sa.text(
            """
            UPDATE briefing_packs
            SET fetch_timestamp_dt = REPLACE(REPLACE(fetch_timestamp, 'T', ' '), '+00:00', '')
            """
        ))
        with op.batch_alter_table("briefing_packs") as batch_op:
            batch_op.drop_column("fetch_timestamp")
            batch_op.alter_column("fetch_timestamp_dt", new_column_name="fetch_timestamp", nullable=False)

    # --- feedback: pack_timestamp String → DateTime, nullable ---
    if mysql:
        op.add_column("feedback", sa.Column("pack_timestamp_dt", sa.DateTime(timezone=True), nullable=True))
        op.execute(sa.text(
            """
            UPDATE feedback
            SET pack_timestamp_dt = CASE
                WHEN pack_timestamp IS NOT NULL AND pack_timestamp != ''
                THEN STR_TO_DATE(
                    REPLACE(REPLACE(pack_timestamp, 'T', ' '), '+00:00', ''),
                    '%Y-%m-%d %H:%i:%s.%f'
                )
                ELSE NULL
            END
            """
        ))
        op.drop_column("feedback", "pack_timestamp")
        op.alter_column("feedback", "pack_timestamp_dt", new_column_name="pack_timestamp",
                         existing_type=sa.DateTime(timezone=True))
    else:
        with op.batch_alter_table("feedback") as batch_op:
            batch_op.add_column(
                sa.Column("pack_timestamp_dt", sa.DateTime(timezone=True), nullable=True)
            )
        op.execute(sa.text(
            """
            UPDATE feedback
            SET pack_timestamp_dt = CASE
                WHEN pack_timestamp IS NOT NULL AND pack_timestamp != ''
                THEN REPLACE(REPLACE(pack_timestamp, 'T', ' '), '+00:00', '')
                ELSE NULL
            END
            """
        ))
        with op.batch_alter_table("feedback") as batch_op:
            batch_op.drop_column("pack_timestamp")
            batch_op.alter_column("pack_timestamp_dt", new_column_name="pack_timestamp")


def downgrade() -> None:
    mysql = _is_mysql()

    # --- feedback: DateTime → String ---
    if mysql:
        op.add_column("feedback", sa.Column("pack_timestamp_str", sa.String(64), nullable=True, server_default=""))
        op.execute(sa.text(
            "UPDATE feedback SET pack_timestamp_str = COALESCE(DATE_FORMAT(pack_timestamp, '%Y-%m-%dT%H:%i:%s+00:00'), '')"
        ))
        op.drop_column("feedback", "pack_timestamp")
        op.alter_column("feedback", "pack_timestamp_str", new_column_name="pack_timestamp",
                         existing_type=sa.String(64))
    else:
        with op.batch_alter_table("feedback") as batch_op:
            batch_op.add_column(
                sa.Column("pack_timestamp_str", sa.String(64), nullable=True, server_default="")
            )
        op.execute(sa.text(
            "UPDATE feedback SET pack_timestamp_str = COALESCE(pack_timestamp, '')"
        ))
        with op.batch_alter_table("feedback") as batch_op:
            batch_op.drop_column("pack_timestamp")
            batch_op.alter_column("pack_timestamp_str", new_column_name="pack_timestamp")

    # --- briefing_packs: DateTime → String ---
    if mysql:
        op.add_column("briefing_packs", sa.Column("fetch_timestamp_str", sa.String(64), nullable=True))
        op.execute(sa.text(
            "UPDATE briefing_packs SET fetch_timestamp_str = DATE_FORMAT(fetch_timestamp, '%Y-%m-%dT%H:%i:%s+00:00')"
        ))
        op.drop_column("briefing_packs", "fetch_timestamp")
        op.alter_column("briefing_packs", "fetch_timestamp_str", new_column_name="fetch_timestamp",
                         nullable=False, existing_type=sa.String(64))
    else:
        with op.batch_alter_table("briefing_packs") as batch_op:
            batch_op.add_column(
                sa.Column("fetch_timestamp_str", sa.String(64), nullable=True)
            )
        op.execute(sa.text(
            "UPDATE briefing_packs SET fetch_timestamp_str = fetch_timestamp"
        ))
        with op.batch_alter_table("briefing_packs") as batch_op:
            batch_op.drop_column("fetch_timestamp")
            batch_op.alter_column("fetch_timestamp_str", new_column_name="fetch_timestamp", nullable=False)

    # --- flights: departure_time → target_date + target_time_utc ---
    if mysql:
        op.add_column("flights", sa.Column("target_date", sa.String(10), nullable=True))
        op.add_column("flights", sa.Column("target_time_utc", sa.Integer, nullable=True, server_default="9"))
        op.execute(sa.text(
            """
            UPDATE flights
            SET target_date = DATE_FORMAT(departure_time, '%Y-%m-%d'),
                target_time_utc = HOUR(departure_time)
            """
        ))
        op.alter_column("flights", "target_date", nullable=False, existing_type=sa.String(10))
        op.alter_column("flights", "target_time_utc", nullable=False, existing_type=sa.Integer)
        op.drop_column("flights", "departure_time")
    else:
        with op.batch_alter_table("flights") as batch_op:
            batch_op.add_column(
                sa.Column("target_date", sa.String(10), nullable=True)
            )
            batch_op.add_column(
                sa.Column("target_time_utc", sa.Integer, nullable=True, server_default="9")
            )
        op.execute(sa.text(
            """
            UPDATE flights
            SET target_date = substr(departure_time, 1, 10),
                target_time_utc = CAST(substr(departure_time, 12, 2) AS INTEGER)
            """
        ))
        with op.batch_alter_table("flights") as batch_op:
            batch_op.alter_column("target_date", nullable=False)
            batch_op.alter_column("target_time_utc", nullable=False)
            batch_op.drop_column("departure_time")
