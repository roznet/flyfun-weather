"""Persistence for flight trips (issue #602).

Thin CRUD over ``flight_trips`` plus the membership helpers the API and the
flights list need. Deliberately narrow: everything *derived* about a trip —
chain order, the binding leg, what is still remaining — lives in the pure
:mod:`weatherbrief.trips` module and is computed at read time, never stored.

The one invariant this module owns is **one trip per leg**: membership is the
nullable ``FlightRow.trip_id`` column, so adding a leg to a trip silently moves
it out of any previous one, and that is the intended behaviour.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from weatherbrief.db.models import FlightRow, FlightTripRow
from weatherbrief.models import Flight, FlightTrip
from weatherbrief.storage.flights import _row_to_flight

logger = logging.getLogger(__name__)

_TRIP_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_TRIP_ID_LEN = 10

#: Below this ground gap, two consecutive legs are the same *sortie* — a fuel
#: or customs stop, not a decision point. Fixed for v1 (the alternative,
#: deriving it from turnaround time or local night, is still open in the design
#: doc); kept here as one named constant so raising it is a one-line change.
SORTIE_GAP_HOURS = 4.0


def _generate_trip_id() -> str:
    """10-char base62 token — same shape family as ``FlightRow.share_code``."""
    return "".join(secrets.choice(_TRIP_ID_ALPHABET) for _ in range(_TRIP_ID_LEN))


def allocate_trip_id(session: Session) -> str:
    """A trip id that doesn't collide with an existing row."""
    for _ in range(8):
        candidate = _generate_trip_id()
        if session.get(FlightTripRow, candidate) is None:
            return candidate
    raise RuntimeError("Could not allocate a unique trip id after 8 attempts")


def _row_to_trip(row: FlightTripRow) -> FlightTrip:
    return FlightTrip(
        id=row.id,
        user_id=row.user_id,
        name=row.name or "",
        notes=row.notes,
        auto_refresh=bool(row.auto_refresh),
        auto_refresh_hour=row.auto_refresh_hour,
        notify_override=row.notify_override or "default",
        share_code=row.share_code,
        ai_summary_text=row.ai_summary_text,
        ai_summary_key=row.ai_summary_key,
        ai_summary_at=row.ai_summary_at,
        created_at=row.created_at or datetime.now(timezone.utc),
    )


def create_trip(session: Session, user_id: str, name: str) -> FlightTrip:
    """Insert a new (empty) trip and return it."""
    row = FlightTripRow(
        id=allocate_trip_id(session),
        user_id=user_id,
        name=name[:200],
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    return _row_to_trip(row)


def load_trip_row(session: Session, trip_id: str, user_id: str) -> FlightTripRow | None:
    """The owned trip row, or None when missing or owned by someone else."""
    row = session.get(FlightTripRow, trip_id)
    if row is None or row.user_id != user_id:
        return None
    return row


def load_trip(session: Session, trip_id: str, user_id: str) -> FlightTrip | None:
    row = load_trip_row(session, trip_id, user_id)
    return _row_to_trip(row) if row else None


def list_trips(session: Session, user_id: str) -> list[FlightTrip]:
    rows = session.execute(
        select(FlightTripRow)
        .where(FlightTripRow.user_id == user_id)
        .order_by(FlightTripRow.created_at.desc())
    ).scalars().all()
    return [_row_to_trip(r) for r in rows]


def trip_members(session: Session, trip_id: str) -> list[Flight]:
    """Member legs in chain order (earliest departure first).

    Order is derived, never stored — see the design note on ``trip_position``.
    """
    rows = session.execute(
        select(FlightRow)
        .where(FlightRow.trip_id == trip_id)
        .order_by(FlightRow.departure_time.asc())
    ).scalars().all()
    return [_row_to_flight(r) for r in rows]


def member_counts(session: Session, trip_ids: list[str]) -> dict[str, int]:
    """Leg count per trip, in one query."""
    if not trip_ids:
        return {}
    rows = session.execute(
        select(FlightRow.trip_id, func.count(FlightRow.id))
        .where(FlightRow.trip_id.in_(trip_ids))
        .group_by(FlightRow.trip_id)
    ).all()
    return {trip_id: count for trip_id, count in rows}


def set_leg_trip(
    session: Session, flight_ids: list[str], user_id: str, trip_id: str | None,
) -> list[str]:
    """Point the given owned flights at ``trip_id`` (or unlink with None).

    Returns the ids actually changed. Flights the user doesn't own are silently
    skipped — the caller has already 404'd anything it cares about.

    The *destination* trip is scoped to the user as well. Every current caller
    already validates it via ``_owned_trip_row``, so this is defence in depth
    rather than a live hole — but "can a leg belong to another user's trip?" is
    the question the design doc parks before any ``share_code`` work, and the
    answer should not depend on every future caller remembering to check.
    """
    if not flight_ids:
        return []
    if trip_id is not None and load_trip_row(session, trip_id, user_id) is None:
        raise ValueError(f"Trip {trip_id} is not owned by {user_id}")
    rows = session.execute(
        select(FlightRow).where(
            FlightRow.id.in_(flight_ids), FlightRow.user_id == user_id,
        )
    ).scalars().all()
    changed = []
    for row in rows:
        if row.trip_id != trip_id:
            row.trip_id = trip_id
            changed.append(row.id)
    session.flush()
    return changed


def delete_trip(session: Session, trip_id: str, user_id: str) -> bool:
    """Delete a trip container. Its legs survive, unlinked (``SET NULL``).

    The FK does the unlinking on MySQL and on SQLite with foreign keys on, but
    the column is cleared explicitly first so the outcome does not depend on
    ``PRAGMA foreign_keys`` being set — an unlink that silently fails would
    leave every leg pointing at a trip that no longer exists.
    """
    row = load_trip_row(session, trip_id, user_id)
    if row is None:
        return False
    set_leg_trip(
        session,
        [f.id for f in trip_members(session, trip_id)],
        user_id,
        None,
    )
    session.execute(delete(FlightTripRow).where(FlightTripRow.id == trip_id))
    session.flush()
    return True


def prune_empty_trips(
    session: Session, user_id: str, *, keep: str | None = None,
) -> list[str]:
    """Delete the user's trips that have no legs left, returning their ids.

    Called after a delete / unlink / move. A **1-leg trip is valid** and is
    deliberately kept (adding legs later is a normal flow) — only the genuinely
    empty container goes, because nothing can ever be shown for it.

    ``keep`` exempts one trip id. ``create_trip`` needs it: an empty
    ``flight_ids`` would otherwise have this delete the row created moments
    earlier, and the caller would return a 201 for a trip that no longer
    exists.
    """
    trip_ids = [
        t for t in session.execute(
            select(FlightTripRow.id).where(FlightTripRow.user_id == user_id)
        ).scalars().all()
        if t != keep
    ]
    if not trip_ids:
        return []
    counts = member_counts(session, trip_ids)
    empty = [t for t in trip_ids if counts.get(t, 0) == 0]
    if empty:
        session.execute(delete(FlightTripRow).where(FlightTripRow.id.in_(empty)))
        session.flush()
    return empty


# ---------------------------------------------------------------------------
# Serial-refresh driver state
#
# Stored as one small JSON document rather than a column per field: it is
# written and read as a unit by the driver, and it exists only while a trip
# refresh is in flight.
# ---------------------------------------------------------------------------


def read_refresh_state(row: FlightTripRow) -> dict:
    """The trip's refresh state document, or an empty dict when idle."""
    if not row.refresh_state_json:
        return {}
    try:
        value = json.loads(row.refresh_state_json)
    except (TypeError, ValueError):
        logger.warning("Unparseable refresh state on trip %s — treating as idle", row.id)
        return {}
    return value if isinstance(value, dict) else {}


def write_refresh_state(
    session: Session,
    row: FlightTripRow,
    *,
    refresh_id: str | None,
    state: dict | None,
    started_at: datetime | None = None,
) -> None:
    """Persist (or clear, with ``refresh_id=None``) the driver state."""
    row.refresh_id = refresh_id
    row.refresh_state_json = json.dumps(state) if state is not None else None
    if refresh_id is None:
        row.refresh_started_at = None
    elif started_at is not None:
        row.refresh_started_at = started_at
    session.flush()
