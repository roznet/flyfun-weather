"""User aircraft storage — database-backed CRUD."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from weatherbrief.db.models import UserAircraftRow


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
    return row


def update_aircraft(session: Session, row: UserAircraftRow, **kwargs) -> UserAircraftRow:
    """Update aircraft fields."""
    if kwargs.get("is_default"):
        _clear_default(session, row.user_id)

    for key, value in kwargs.items():
        setattr(row, key, value)
    session.flush()
    return row


def delete_aircraft(session: Session, row: UserAircraftRow) -> None:
    """Delete an aircraft."""
    session.delete(row)
    session.flush()


def _clear_default(session: Session, user_id: str) -> None:
    """Clear is_default on all aircraft for a user."""
    stmt = (
        select(UserAircraftRow)
        .where(UserAircraftRow.user_id == user_id, UserAircraftRow.is_default.is_(True))
    )
    for row in session.execute(stmt).scalars().all():
        row.is_default = False
