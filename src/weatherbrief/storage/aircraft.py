"""User aircraft storage — database-backed CRUD."""

from __future__ import annotations

import json

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from flyfun_common.db.models import UserRow
from weatherbrief.db.models import FlightProfileRow, FlightRow, PirepRow, UserAircraftRow


def list_aircraft(session: Session, user_id: str) -> list[UserAircraftRow]:
    """List all aircraft for a user, default first then by tail number."""
    stmt = (
        select(UserAircraftRow)
        .where(UserAircraftRow.user_id == user_id)
        .order_by(UserAircraftRow.is_default.desc(), UserAircraftRow.tail_number)
    )
    return list(session.execute(stmt).scalars().all())


def get_aircraft(session: Session, aircraft_id: int) -> UserAircraftRow | None:
    """Load an aircraft by ID."""
    return session.get(UserAircraftRow, aircraft_id)


def create_aircraft(session: Session, user_id: str, **kwargs) -> UserAircraftRow:
    """Create a new aircraft for a user.

    If is_default is True, clears default on all other aircraft for this user.
    """
    if kwargs.get("is_default"):
        _clear_default(session, user_id)

    row = UserAircraftRow(user_id=user_id, **kwargs)
    session.add(row)
    session.flush()
    _forget_default(session, user_id)
    return row


def update_aircraft(session: Session, row: UserAircraftRow, **kwargs) -> UserAircraftRow:
    """Update aircraft fields."""
    if kwargs.get("is_default"):
        _clear_default(session, row.user_id)

    for key, value in kwargs.items():
        setattr(row, key, value)
    session.flush()
    _forget_default(session, row.user_id)
    return row


def delete_aircraft(session: Session, row: UserAircraftRow) -> None:
    """Delete an aircraft."""
    user_id = row.user_id
    session.delete(row)
    session.flush()
    _forget_default(session, user_id)


def _clear_default(session: Session, user_id: str) -> None:
    """Clear is_default on all aircraft for a user."""
    stmt = (
        select(UserAircraftRow)
        .where(UserAircraftRow.user_id == user_id, UserAircraftRow.is_default.is_(True))
    )
    for row in session.execute(stmt).scalars().all():
        row.is_default = False
    _forget_default(session, user_id)


def lock_user_aircraft(session: Session, user_id: str) -> None:
    """Serialise changes to one user's aircraft set for this transaction.

    Row lock on the user (``SELECT … FOR UPDATE``; a no-op on SQLite, whose
    writers are serialised anyway). Taken by every check-then-act on the set —
    synthesising the placeholder, replacing it, the last-aircraft delete guard
    — so two concurrent requests (web + iOS opened together, a double-fired
    request) can't both create a placeholder or both delete "the other one".
    """
    session.execute(select(UserRow.id).where(UserRow.id == user_id).with_for_update())


def locked_user_aircraft(session: Session, user_id: str) -> list[UserAircraftRow]:
    """The user's aircraft, oldest first, read under :func:`lock_user_aircraft`.

    A *locking* read on purpose: on MySQL (REPEATABLE READ) a plain SELECT
    after the lock would still see the transaction's first snapshot and miss a
    placeholder another request has just committed.
    """
    lock_user_aircraft(session, user_id)
    return list(
        session.execute(
            select(UserAircraftRow)
            .where(UserAircraftRow.user_id == user_id)
            .order_by(UserAircraftRow.created_at, UserAircraftRow.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars().all()
    )


# --- Default aircraft (#777) ---------------------------------------------
#
# Every user always has exactly one default aircraft. Users who never set one
# up get a synthesised placeholder ("My aircraft", type ZZZZ) flagged
# ``is_placeholder`` so clients can prompt "set up your aircraft"; the flag —
# not the ZZZZ type — is the marker, because a pilot whose type genuinely
# isn't in the catalogue may keep ZZZZ on purpose.

PLACEHOLDER_ICAO_TYPE = "ZZZZ"
PLACEHOLDER_NICKNAME = "My aircraft"


def get_default_aircraft(session: Session, user_id: str) -> UserAircraftRow | None:
    """The user's default aircraft, read-only (never creates one).

    Falls back to the oldest aircraft when none is flagged default, so a read
    path resolves the same aircraft :func:`ensure_default_aircraft` would
    promote. Request-scoped memo on ``session.info``: the flight list resolves
    the default once per owner, not once per flight.
    """
    memo = session.info.setdefault("_default_aircraft_id", {})
    if user_id in memo:
        aid = memo[user_id]
        return session.get(UserAircraftRow, aid) if aid is not None else None
    stmt = (
        select(UserAircraftRow)
        .where(UserAircraftRow.user_id == user_id)
        .order_by(
            UserAircraftRow.is_default.desc(),
            UserAircraftRow.created_at,
            UserAircraftRow.id,
        )
        .limit(1)
    )
    row = session.execute(stmt).scalars().first()
    memo[user_id] = row.id if row is not None else None
    return row


def _forget_default(session: Session, user_id: str) -> None:
    session.info.get("_default_aircraft_id", {}).pop(user_id, None)


def placeholder_fields(profile_settings: dict | None) -> dict:
    """Column values for a synthesised placeholder aircraft.

    Seeded from the user's default profile: its legacy ``speed_kt`` (so a
    flight that used to fall back to the profile speed keeps the same cruise
    IAS) and ``is_ifr`` from its flight rules. The ceiling is left unknown —
    the profile ceiling is a mission choice, not the aircraft's service
    ceiling. Shared by :func:`ensure_default_aircraft` and migration 103.
    """
    settings = profile_settings or {}
    speed = settings.get("speed_kt")
    try:
        speed = int(speed) if speed is not None and float(speed) > 0 else None
    except (TypeError, ValueError):
        speed = None
    return {
        "icao_type": PLACEHOLDER_ICAO_TYPE,
        "nickname": PLACEHOLDER_NICKNAME,
        "tail_number": None,
        "cruise_speed_kt": speed,
        "ceiling_ft": None,
        "is_ifr": settings.get("flight_rules") != "vfr_only",
        "is_fiki": False,
        "is_default": True,
        "is_placeholder": True,
    }


def ensure_default_aircraft(session: Session, user_id: str) -> UserAircraftRow:
    """Ensure the user has a default aircraft and return it.

    - has a default → returned as is
    - has aircraft but none default → the oldest is promoted
    - has none → a placeholder is created from the default profile
      (:func:`placeholder_fields`)

    Mirrors ``ensure_default_profile`` in ``storage/flights.py``. The common
    case (exactly one default) is a plain read; anything else re-reads under
    :func:`lock_user_aircraft`, and extra defaults are cleared (oldest kept).
    """
    defaults = list(
        session.execute(
            select(UserAircraftRow)
            .where(UserAircraftRow.user_id == user_id, UserAircraftRow.is_default.is_(True))
            .limit(2)
        ).scalars().all()
    )
    if len(defaults) == 1:
        return defaults[0]

    _forget_default(session, user_id)
    rows = locked_user_aircraft(session, user_id)
    defaults = [r for r in rows if r.is_default]
    if defaults:
        for extra in defaults[1:]:
            extra.is_default = False
        session.flush()
        return defaults[0]
    if rows:
        rows[0].is_default = True
        session.flush()
        return rows[0]

    row = UserAircraftRow(
        user_id=user_id, **placeholder_fields(_default_profile_settings(session, user_id)),
    )
    session.add(row)
    session.flush()
    return row


def resolve_flight_aircraft(session: Session, flight) -> UserAircraftRow | None:
    """The aircraft a flight flies: its own, else its owner's default.

    Flights created before #777 (and edits that sent the ``aircraft_id = 0``
    clear sentinel) carry a NULL ``aircraft_id``; they resolve to the owner's
    default on read, the way a NULL ``profile_id`` resolves to the default
    profile. Read-only: never synthesises an aircraft.
    """
    aircraft_id = getattr(flight, "aircraft_id", None)
    if aircraft_id:
        row = session.get(UserAircraftRow, aircraft_id)
        if row is not None:
            return row
    user_id = getattr(flight, "user_id", None)
    if not user_id:
        return None
    return get_default_aircraft(session, user_id)


def _default_profile_row(session: Session, user_id: str) -> FlightProfileRow | None:
    return session.execute(
        select(FlightProfileRow).where(
            FlightProfileRow.user_id == user_id, FlightProfileRow.is_default.is_(True),
        )
    ).scalars().first()


def _profile_settings(row: FlightProfileRow | None) -> dict:
    if row is None or not row.settings_json:
        return {}
    try:
        data = json.loads(row.settings_json)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _default_profile_settings(session: Session, user_id: str) -> dict:
    """Raw settings of the user's default profile ({} when none / unreadable)."""
    return _profile_settings(_default_profile_row(session, user_id))


def default_profile_declares_fiki(session: Session, user_id: str) -> bool:
    """The user's default profile says FIKI (the IFR FIKI template, or the
    retired FIKI Icing card on): the answer a new aircraft's FIKI flag takes
    when the client doesn't send one (#850)."""
    from weatherbrief.analysis.advisories.aircraft_icing import profile_declares_fiki

    row = _default_profile_row(session, user_id)
    return row is not None and profile_declares_fiki(_profile_settings(row), row.system_template_key)


def only_placeholder(session: Session, user_id: str) -> UserAircraftRow | None:
    """The placeholder when it is the user's only aircraft, else None.

    Takes the user's aircraft lock (:func:`locked_user_aircraft`), held until
    the caller's transaction ends.
    """
    rows = locked_user_aircraft(session, user_id)
    if len(rows) == 1 and rows[0].is_placeholder:
        return rows[0]
    return None


def replace_placeholder(
    session: Session, placeholder: UserAircraftRow, new_row: UserAircraftRow,
) -> None:
    """Move the placeholder's flights (and PIREPs) to *new_row*, make it
    default, delete the placeholder."""
    for model in (FlightRow, PirepRow):
        session.execute(
            update(model)
            .where(model.aircraft_id == placeholder.id)
            .values(aircraft_id=new_row.id)
        )
    new_row.is_default = True
    session.delete(placeholder)
    _forget_default(session, placeholder.user_id)
    session.flush()
