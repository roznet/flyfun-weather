"""JWT token helpers for session management."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 7


def create_token(
    user_id: str,
    email: str,
    name: str,
    secret: str,
) -> str:
    """Create a signed JWT with user claims and 7-day expiry."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "name": name,
        "iat": now,
        "exp": now + timedelta(days=JWT_EXPIRY_DAYS),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_token(token: str, secret: str) -> dict:
    """Decode and validate a JWT. Raises jwt.ExpiredSignatureError or jwt.InvalidTokenError."""
    return jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
