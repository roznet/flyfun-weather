"""Server-derived, cross-surface briefing badge count.

The badge is a server-*derived* count, never a client-side increment — client
counters drift the instant a second surface (web, another device) reads an
update. This mirrors the system-message unseen pattern (``api/messages.py``),
per-flight (see ios-app-briefing-notifications.md → Badge).

State lives in ``FlightBriefingSeenRow`` per (user, flight):

- ``last_notified_ts`` — pack ts of the most recent notify-qualifying refresh.
- ``last_seen_ts`` — flight's latest pack ts when the pilot last opened it.

A flight is **unseen** iff ``last_notified_ts > last_seen_ts`` (or it has been
notified but never opened). The badge is the count of unseen flights — each
flight contributes at most 1, no matter how many packs piled up.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from weatherbrief.db.models import BriefingPackRow, FlightBriefingSeenRow

logger = logging.getLogger(__name__)


def _as_utc(dt: datetime | None) -> datetime | None:
    """Normalize a (possibly naive, SQLite-round-tripped) datetime to aware UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _is_unseen(row: FlightBriefingSeenRow) -> bool:
    """A flight is unseen iff it has a notify-qualifying pack newer than last-seen."""
    notified = _as_utc(row.last_notified_ts)
    if notified is None:
        return False
    seen = _as_utc(row.last_seen_ts)
    return seen is None or notified > seen


def _latest_pack_ts(db: Session, flight_id: str) -> datetime | None:
    """Return the flight's newest pack fetch timestamp, or None if no packs."""
    ts = (
        db.query(func.max(BriefingPackRow.fetch_timestamp))
        .filter(BriefingPackRow.flight_id == flight_id)
        .scalar()
    )
    return _as_utc(ts)


def _get_or_create_seen(
    db: Session, user_id: str, flight_id: str
) -> FlightBriefingSeenRow:
    """Load the (user, flight) seen row, creating an empty one if absent."""
    row = (
        db.query(FlightBriefingSeenRow)
        .filter(
            FlightBriefingSeenRow.user_id == user_id,
            FlightBriefingSeenRow.flight_id == flight_id,
        )
        .first()
    )
    if row is None:
        row = FlightBriefingSeenRow(user_id=user_id, flight_id=flight_id)
        db.add(row)
        db.flush()
    return row


def compute_badge_count(db: Session, user_id: str) -> int:
    """Return the number of the user's flights with an unseen briefing update.

    Server-authoritative. Cheap: flights per user are few, so we load the
    user's seen rows and count in Python (tz-normalized comparison).
    """
    rows = (
        db.query(FlightBriefingSeenRow)
        .filter(FlightBriefingSeenRow.user_id == user_id)
        .all()
    )
    return sum(1 for r in rows if _is_unseen(r))


def record_notify_qualifying(
    db: Session, user_id: str, flight_id: str, pack_ts: datetime
) -> None:
    """Advance ``last_notified_ts`` for a notify-qualifying refresh.

    Called from the notification dispatch when a completion passes the
    scope + change-filter + not-muted gate — independent of whether the alert
    channel is on (the badge follows "a device is registered"). Comparing the
    *latest* notified pack means two unopened refreshes still count once.
    """
    row = _get_or_create_seen(db, user_id, flight_id)
    prev = _as_utc(row.last_notified_ts)
    pack_ts = _as_utc(pack_ts)
    if prev is None or (pack_ts is not None and pack_ts > prev):
        row.last_notified_ts = pack_ts


def mark_flight_seen(db: Session, user_id: str, flight_id: str) -> bool:
    """Mark a flight's briefing seen (opened on web or app).

    Sets ``last_seen_ts`` to the flight's current latest pack ts, so the flight
    clears even if several packs accumulated and only the newest was viewed.
    Returns True if this transitioned the flight from unseen → seen (the caller
    uses that to fire a silent badge-sync push to the user's other devices).
    """
    row = _get_or_create_seen(db, user_id, flight_id)
    was_unseen = _is_unseen(row)

    latest = _latest_pack_ts(db, flight_id)
    if latest is None:
        # No packs yet — nothing to mark seen against; leave state untouched.
        return False
    row.last_seen_ts = latest
    return was_unseen and not _is_unseen(row)
