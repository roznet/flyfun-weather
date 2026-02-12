"""FastAPI dependencies for database sessions."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from weatherbrief.db.engine import SessionLocal


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
