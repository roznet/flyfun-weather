"""FastAPI dependencies for database sessions."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from weatherbrief.db.engine import DEV_USER_ID, SessionLocal


def current_user_id() -> str:
    """Return the current user ID. Dev mode uses a hardcoded dev user.

    TODO: replace with auth middleware in multi-user phase.
    """
    return DEV_USER_ID


def get_db() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session, committing on success or rolling back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
