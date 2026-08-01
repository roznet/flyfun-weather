"""Dedupe briefing_packs and enforce UNIQUE (flight_id, fetch_timestamp).

``save_pack_meta`` inserted unconditionally while the readers
(``load_pack_meta`` / ``update_pack_meta``) use ``scalar_one_or_none()`` — so
a single duplicate ``(flight_id, fetch_timestamp)`` pair, left behind e.g. by
two concurrent refreshes racing the provisional-pack insert, makes every
subsequent read of that pack raise (HTTP 500). This migration first deletes
all but the newest row (``MAX(id)``) of each duplicated pair, then adds the
unique index that turns the race into a constraint violation the save path
can catch and convert into an update (see the ``begin_nested`` guard in
``storage/flights.py``).

The dedupe is deliberately standard SQL — no MySQL-only JOIN-delete — so the
same revision runs on SQLite (dev/tests) and MySQL (prod). The keep-set is
double-nested in a derived table because MySQL refuses a DELETE whose
subquery reads the target table directly (error 1093); the derived table
forces the keep-set to materialize first. Singletons are their own
``MAX(id)``, so they are retained by construction.

Revision ID: 085
Revises: 084
Create Date: 2026-08-01
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "085"
down_revision: Union[str, None] = "084"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    bind = op.get_bind()
    duplicate_rows = bind.execute(
        sa.text(
            "SELECT COALESCE(SUM(cnt - 1), 0) FROM ("
            "SELECT COUNT(*) AS cnt FROM briefing_packs "
            "GROUP BY flight_id, fetch_timestamp) d"
        )
    ).scalar_one()
    if duplicate_rows:
        logger.warning(
            "briefing_packs: deleting %d duplicate "
            "(flight_id, fetch_timestamp) row(s), keeping MAX(id) per pair",
            duplicate_rows,
        )
    op.execute(
        sa.text(
            "DELETE FROM briefing_packs WHERE id NOT IN ("
            "SELECT keep_id FROM ("
            "SELECT MAX(id) AS keep_id FROM briefing_packs "
            "GROUP BY flight_id, fetch_timestamp"
            ") keep_ids)"
        )
    )
    op.create_index(
        "uq_briefing_packs_flight_ts",
        "briefing_packs",
        ["flight_id", "fetch_timestamp"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_briefing_packs_flight_ts", table_name="briefing_packs")
