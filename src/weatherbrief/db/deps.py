"""FastAPI dependencies for database sessions and auth."""

from __future__ import annotations

from collections.abc import Generator

import jwt
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from weatherbrief.api.auth_config import COOKIE_NAME, get_jwt_secret, is_dev_mode
from weatherbrief.api.jwt_utils import decode_token
from weatherbrief.db.engine import DEV_USER_ID, SessionLocal


def current_user_id(request: Request) -> str:
    """Extract the authenticated user ID from the JWT session cookie.

    In dev mode, returns the hardcoded dev user (no login required).
    In production, validates the JWT and returns the ``sub`` claim.
    Raises 401 if no valid session is present.
    """
    if is_dev_mode():
        return DEV_USER_ID

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = decode_token(token, get_jwt_secret())
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except (jwt.InvalidTokenError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid session")


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
