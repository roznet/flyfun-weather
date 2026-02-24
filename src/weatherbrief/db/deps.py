"""FastAPI dependencies for database sessions and auth."""

from __future__ import annotations

import hashlib
from collections.abc import Generator
from datetime import datetime, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from weatherbrief.api.auth_config import COOKIE_NAME, get_jwt_secret, is_dev_mode
from weatherbrief.api.jwt_utils import decode_token
from weatherbrief.db.engine import DEV_USER_ID, SessionLocal
from weatherbrief.db.models import ApiTokenRow, UserRow

TOKEN_PREFIX = "wb_"


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


def _authenticate_bearer(token: str, db: Session) -> str:
    """Validate a Bearer API token and return the associated user_id.

    Checks the token hash against the api_tokens table, verifies the token
    is not revoked or expired, and updates last_used_at.
    Raises 401 on any failure.
    """
    if not token.startswith(TOKEN_PREFIX):
        raise HTTPException(status_code=401, detail="Invalid token format")

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    row = (
        db.query(ApiTokenRow)
        .filter(ApiTokenRow.token_hash == token_hash)
        .first()
    )
    if not row:
        raise HTTPException(status_code=401, detail="Invalid token")
    if row.revoked:
        raise HTTPException(status_code=401, detail="Token revoked")
    if row.expires_at:
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Token expired")

    row.last_used_at = datetime.now(timezone.utc)
    db.flush()
    return row.user_id


def _decode_user_id(request: Request, db: Session) -> str:
    """Extract the user ID from JWT cookie or Bearer token.

    Priority:
    1. Dev mode → return DEV_USER_ID
    2. JWT cookie → decode and return sub claim
    3. Authorization: Bearer wb_... → hash and look up in api_tokens
    4. Neither → 401
    """
    if is_dev_mode():
        return DEV_USER_ID

    # Try JWT cookie first
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        try:
            payload = decode_token(cookie, get_jwt_secret())
            return payload["sub"]
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Session expired")
        except (jwt.InvalidTokenError, KeyError):
            raise HTTPException(status_code=401, detail="Invalid session")

    # Try Bearer token
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        bearer_token = auth_header[7:]
        return _authenticate_bearer(bearer_token, db)

    raise HTTPException(status_code=401, detail="Not authenticated")


def current_user_id(
    request: Request,
    db: Session = Depends(get_db),
) -> str:
    """Extract the authenticated user ID and verify the account is still approved.

    In dev mode, returns the hardcoded dev user (no login required).
    In production, validates the JWT or Bearer token, then checks the DB
    to ensure the account hasn't been suspended since the token was issued.
    Raises 401 if no valid session, 403 if account suspended.
    """
    user_id = _decode_user_id(request, db)

    if is_dev_mode():
        return user_id

    user = db.query(UserRow).filter(UserRow.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.approved:
        raise HTTPException(status_code=403, detail="Account suspended")

    return user_id
