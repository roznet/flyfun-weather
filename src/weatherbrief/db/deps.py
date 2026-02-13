"""FastAPI dependencies for database sessions and auth."""

from __future__ import annotations

from collections.abc import Generator

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from weatherbrief.api.auth_config import COOKIE_NAME, get_jwt_secret, is_dev_mode
from weatherbrief.api.jwt_utils import decode_token
from weatherbrief.db.engine import DEV_USER_ID, SessionLocal
from weatherbrief.db.models import UserRow


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


def _decode_user_id(request: Request) -> str:
    """Extract the user ID from the JWT session cookie (no DB check).

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


def current_user_id(
    request: Request,
    db: Session = Depends(get_db),
) -> str:
    """Extract the authenticated user ID and verify the account is still approved.

    In dev mode, returns the hardcoded dev user (no login required).
    In production, validates the JWT, then checks the DB to ensure
    the account hasn't been suspended since the token was issued.
    Raises 401 if no valid session, 403 if account suspended.
    """
    user_id = _decode_user_id(request)

    if is_dev_mode():
        return user_id

    user = db.query(UserRow).filter(UserRow.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.approved:
        raise HTTPException(status_code=403, detail="Account suspended")

    return user_id
