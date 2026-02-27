"""Fix MySQL DATETIME columns to support microsecond precision.

Migration 014 used DATETIME which truncates to seconds on MySQL.
This upgrades to DATETIME(6) and restores microseconds from artifact_path.

Revision ID: 015
Revises: 014
Create Date: 2026-02-27
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_mysql() -> bool:
    return op.get_bind().dialect.name == "mysql"


def upgrade() -> None:
    if not _is_mysql():
        return  # SQLite stores text — no precision issue

    conn = op.get_bind()

    # Upgrade columns to DATETIME(6) for microsecond precision
    conn.execute(sa.text("ALTER TABLE briefing_packs MODIFY fetch_timestamp DATETIME(6) NOT NULL"))
    conn.execute(sa.text("ALTER TABLE feedback MODIFY pack_timestamp DATETIME(6) NULL"))
    conn.execute(sa.text("ALTER TABLE flights MODIFY departure_time DATETIME(6) NOT NULL"))
    conn.execute(sa.text("ALTER TABLE flights MODIFY last_auto_refresh_at DATETIME(6) NULL"))

    # Restore microseconds in briefing_packs.fetch_timestamp from artifact_path.
    # artifact_path ends with a sanitized timestamp dir like:
    #   .../2026-02-27T08-16-00.731966p00-00
    # Strategy: extract the last path component, then reconstruct the datetime
    # using fixed positions (the format is always 26+7 chars: YYYY-MM-DDTHH-MM-SS.ffffffp00-00).
    #
    # SUBSTRING_INDEX(path, '/', -1) → '2026-02-27T08-16-00.731966p00-00'
    # We need: '2026-02-27 08:16:00.731966'
    # Positions in the dir name (1-indexed):
    #   1-10:  YYYY-MM-DD
    #   11:    T
    #   12-13: HH
    #   14:    -
    #   15-16: MM
    #   17:    -
    #   18-26: SS.ffffff
    #   27-33: p00-00
    conn.execute(sa.text("""
        UPDATE briefing_packs
        SET fetch_timestamp = STR_TO_DATE(
            CONCAT(
                SUBSTRING(SUBSTRING_INDEX(artifact_path, '/', -1), 1, 10),
                ' ',
                SUBSTRING(SUBSTRING_INDEX(artifact_path, '/', -1), 12, 2),
                ':',
                SUBSTRING(SUBSTRING_INDEX(artifact_path, '/', -1), 15, 2),
                ':',
                SUBSTRING(SUBSTRING_INDEX(artifact_path, '/', -1), 18, 9)
            ),
            '%Y-%m-%d %H:%i:%s.%f'
        )
        WHERE artifact_path IS NOT NULL
          AND artifact_path != ''
    """))


def downgrade() -> None:
    if not _is_mysql():
        return

    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE briefing_packs MODIFY fetch_timestamp DATETIME NOT NULL"))
    conn.execute(sa.text("ALTER TABLE feedback MODIFY pack_timestamp DATETIME NULL"))
    conn.execute(sa.text("ALTER TABLE flights MODIFY departure_time DATETIME NOT NULL"))
    conn.execute(sa.text("ALTER TABLE flights MODIFY last_auto_refresh_at DATETIME NULL"))
