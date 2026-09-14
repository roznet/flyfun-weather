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
from typing import Literal

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.orm import Session

from weatherbrief.db.models import FlightRow, FlightTripRow
from weatherbrief.models import Flight, FlightTrip
from weatherbrief.storage.flights import _row_to_flight

logger = logging.getLogger(__name__)

_TRIP_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_TRIP_ID_LEN = 10
#: Trip share codes are minted at the same 8 chars as ``FlightRow.share_code``,
#: so both pass the one ``SHARE_CODE_RE`` shape check the redirect routes share.
_SHARE_CODE_LEN = 8

def _generate_trip_id() -> str:
    """10-char base62 token — same shape family as ``FlightRow.share_code``."""
    return "".join(secrets.choice(_TRIP_ID_ALPHABET) for _ in range(_TRIP_ID_LEN))


def _generate_share_code() -> str:
    """8-char base62 token, the same alphabet and length flights use."""
    return "".join(secrets.choice(_TRIP_ID_ALPHABET) for _ in range(_SHARE_CODE_LEN))


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


def allocate_share_code(session: Session) -> str:
    """A trip ``share_code`` that doesn't collide with an existing row.

    Deliberately the same shape family as ``FlightRow.share_code`` (base62,
    generous length) so ``/t/{code}`` and ``/s/{code}`` validate through the
    one :data:`weatherbrief.storage.flights.SHARE_CODE_RE`.
    """
    for _ in range(8):
        candidate = _generate_share_code()
        existing = session.execute(
            select(FlightTripRow.id).where(FlightTripRow.share_code == candidate)
        ).first()
        if existing is None:
            return candidate
    raise RuntimeError("Could not allocate a unique trip share_code after 8 attempts")


def ensure_share_code(session: Session, row: FlightTripRow) -> str:
    """The trip's share code, minting one on first use.

    Trips created before sharing existed carry NULL (migration 096 backfills
    them, but a row restored from an older dump would not), and the column is
    nullable so a bare ``FlightTripRow(...)`` in a test still works. Minting
    lazily means no caller has to care which of those it is holding.

    The mint is a **conditional UPDATE**, not a read-modify-write, because two
    first-reads of the same trip can race (the web page and the iOS app opened
    together, or two tabs). Both would see NULL, generate different codes and
    write them; last commit wins in the DB, but the loser has already *returned*
    its code — and a link built from it, pasted into a message, would 404 for
    the recipient forever. ``WHERE share_code IS NULL`` makes exactly one writer
    win; the loser re-reads and returns the code that actually landed.

    That re-read is **locking**, and not belt-and-braces — the same hazard, and
    the same fix, as ``api/trip_refresh.py::start``. Prod is MySQL at REPEATABLE
    READ, where the transaction's snapshot is established at its first
    consistent read; callers here have already done one (``_trip_to_response``
    fetches the row before asking). A plain re-read is served from that
    snapshot, so the loser would still see NULL even though the winner has
    committed a code — and fall through to the "row is gone" branch, turning
    the exact concurrent-first-read this function exists to handle into a 500.
    The UPDATE itself is unaffected: writes always operate on the latest
    committed version, which is why it correctly matches zero rows. SQLite
    ignores ``FOR UPDATE``, so dev and the test suite cannot reproduce the
    difference — hence this comment carrying the reasoning.
    """
    if row.share_code:
        return row.share_code

    candidate = allocate_share_code(session)
    session.execute(
        update(FlightTripRow)
        .where(FlightTripRow.id == row.id, FlightTripRow.share_code.is_(None))
        .values(share_code=candidate)
    )
    session.flush()
    # One locking read for both outcomes rather than a branch on ``rowcount``:
    # it takes the latest committed row whether we won or lost, so there is no
    # path where the answer depends on which version the snapshot happened to
    # hold. Winning already holds the exclusive lock, so re-acquiring is free.
    session.refresh(row, ["share_code"], with_for_update=True)
    if row.share_code:
        return row.share_code
    # Still NULL against the *current* row, so the UPDATE matched nothing
    # because the row is gone (a trip deleted mid-read) — not because someone
    # else won. Say so rather than returning a code that names nothing.
    raise KeyError(f"Trip {row.id} no longer exists")


def lookup_trip_id_by_share_code(session: Session, code: str) -> str | None:
    """Resolve a short trip ``share_code`` to its trip id, or None if unknown.

    Access is *not* decided here — the code is a link shortener, exactly as it
    is for flights. Whether the resolved trip may be read is
    :func:`load_trip_row_for_viewer`'s call.
    """
    return session.execute(
        select(FlightTripRow.id).where(FlightTripRow.share_code == code)
    ).scalar_one_or_none()


def create_trip(session: Session, user_id: str, name: str) -> FlightTrip:
    """Insert a new (empty) trip and return it."""
    row = FlightTripRow(
        id=allocate_trip_id(session),
        user_id=user_id,
        name=name[:200],
        share_code=allocate_share_code(session),
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


def shareable_trip_ids(session: Session, trip_ids: list[str]) -> set[str]:
    """Which of ``trip_ids`` may be read by someone who does not own them.

    **The single definition of the all-or-nothing rule**: a trip is shareable
    only when it has at least one leg and *every* leg is shareable (not
    private). Everything that gates on sharing — the trip read path, the
    recipient's trip badge, the share button — resolves through here or through
    :func:`is_shareable`, so the rule cannot be tightened in one place and
    missed in another.

    The alternative — show the recipient the subset of legs that are public —
    is unsafe for this feature specifically. A trip is a conjunctive chain whose
    headline is "which leg decides this trip"; computed over a subset, that
    headline is not merely incomplete but wrong in the dangerous direction,
    because hiding the red return leg leaves the recipient reading a green
    chain. See designs/flight-trips.md.

    Batched so the flights list can gate a whole page of shared legs in one
    query instead of one per trip.
    """
    if not trip_ids:
        return set()
    rows = session.execute(
        select(
            FlightRow.trip_id,
            func.count(FlightRow.id),
            func.sum(case((FlightRow.private.is_(True), 1), else_=0)),
        )
        .where(FlightRow.trip_id.in_(trip_ids))
        .group_by(FlightRow.trip_id)
    ).all()
    # A trip with no legs never appears in the grouped result, and that is the
    # right answer: nothing to read is not shareable.
    return {
        trip_id
        for trip_id, total, private_count in rows
        if int(total or 0) > 0 and int(private_count or 0) == 0
    }


def is_shareable(session: Session, trip_id: str) -> bool:
    """Whether one trip may be read by a non-owner. See :func:`shareable_trip_ids`."""
    return trip_id in shareable_trip_ids(session, [trip_id])


def load_trip_row_for_viewer(
    session: Session, trip_id: str, viewer_id: str
) -> tuple[FlightTripRow, Literal["owner", "viewer"]] | None:
    """The trip row plus the viewer's role, or None when they may not read it.

    The owner always may. Anyone else may only when the trip is shareable, so a
    trip with even one private leg is a 404 for them — indistinguishable from a
    trip that does not exist, which is the point: the trip *name* is derived
    from the chain ("EGTF → LSGS → EGTF"), so acknowledging the trip at all
    would leak the route of the leg that was made private.
    """
    row = session.get(FlightTripRow, trip_id)
    if row is None:
        return None
    if row.user_id == viewer_id:
        return row, "owner"
    if is_shareable(session, trip_id):
        return row, "viewer"
    return None


def load_trip_unscoped(session: Session, trip_id: str) -> FlightTrip | None:
    """The trip container with **no** ownership filter.

    For callers that have already decided access — a shared trip read, where
    passing the viewer's id to :func:`load_trip` would come back None. Never
    call this without a preceding :func:`load_trip_row_for_viewer`.
    """
    row = session.get(FlightTripRow, trip_id)
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
