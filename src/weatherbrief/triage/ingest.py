"""Ingest feedback from the app DB into the triage queue."""

from __future__ import annotations

import logging
import sqlite3

from sqlalchemy.orm import Session

from weatherbrief.db import SessionLocal, get_engine
from weatherbrief.db.models import FeedbackRow, UserRow

from weatherbrief.triage.db import connect, log_action

logger = logging.getLogger(__name__)


def _fetch_new_feedback(
    app_session: Session,
    existing_ids: set[int],
) -> list[dict]:
    """Query app DB for feedback not yet in the triage queue."""
    rows = (
        app_session.query(FeedbackRow, UserRow)
        .join(UserRow, FeedbackRow.user_id == UserRow.id)
        .all()
    )
    items = []
    for fb, user in rows:
        if fb.id in existing_ids:
            continue
        items.append(
            {
                "app_feedback_id": fb.id,
                "user_email": user.email or "",
                "user_name": user.display_name or "",
                "user_id": fb.user_id,
                "flight_id": fb.flight_id or "",
                "pack_timestamp": (
                    fb.pack_timestamp.isoformat() if fb.pack_timestamp else None
                ),
                "category": fb.category or "",
                "comment": fb.comment or "",
                "feedback_created_at": fb.created_at.isoformat(),
            }
        )
    return items


def _insert_queue_item(triage_conn: sqlite3.Connection, item: dict) -> int:
    """Insert a single feedback item into the triage queue. Returns row id."""
    cur = triage_conn.execute(
        """\
        INSERT INTO queue (
            app_feedback_id, user_email, user_name, user_id,
            flight_id, pack_timestamp, category, comment,
            feedback_created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item["app_feedback_id"],
            item["user_email"],
            item["user_name"],
            item["user_id"],
            item["flight_id"],
            item["pack_timestamp"],
            item["category"],
            item["comment"],
            item["feedback_created_at"],
        ),
    )
    return cur.lastrowid  # type: ignore[return-value]


def _update_user_rate(triage_conn: sqlite3.Connection, item: dict) -> None:
    """Upsert user_rate row for rate-limit tracking."""
    triage_conn.execute(
        """\
        INSERT INTO user_rate (user_id, feedback_count, first_feedback_at, last_feedback_at)
        VALUES (?, 1, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            feedback_count   = feedback_count + 1,
            last_feedback_at = excluded.last_feedback_at""",
        (
            item["user_id"],
            item["feedback_created_at"],
            item["feedback_created_at"],
        ),
    )


def ingest(*, dry_run: bool = False) -> int:
    """Pull new feedback from the app DB into the triage queue.

    Returns the number of items ingested.
    """
    # Ensure app DB engine is initialised
    get_engine()
    app_session = SessionLocal()

    triage_conn = connect()
    try:
        # Existing app_feedback_ids already in the triage queue
        existing = {
            row[0]
            for row in triage_conn.execute(
                "SELECT app_feedback_id FROM queue"
            ).fetchall()
        }

        items = _fetch_new_feedback(app_session, existing)
        if not items:
            logger.info("No new feedback to ingest")
            return 0

        if dry_run:
            logger.info("[dry-run] Would ingest %d item(s)", len(items))
            return len(items)

        for item in items:
            queue_id = _insert_queue_item(triage_conn, item)
            _update_user_rate(triage_conn, item)
            log_action(triage_conn, queue_id, "ingested")

        triage_conn.commit()
        logger.info("Ingested %d feedback item(s)", len(items))
        return len(items)
    finally:
        app_session.close()
        triage_conn.close()
