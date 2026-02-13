"""Authentication endpoints: OAuth login/callback, logout, user info."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from weatherbrief.api.auth_config import (
    COOKIE_NAME,
    create_oauth,
    get_jwt_secret,
    is_dev_mode,
)
from weatherbrief.api.jwt_utils import create_token
from weatherbrief.db.deps import current_user_id, get_db
from weatherbrief.db.models import UserPreferencesRow, UserRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

oauth = create_oauth()


@router.get("/login/google")
async def login_google(request: Request):
    """Redirect to Google OAuth consent screen."""
    redirect_uri = request.url_for("callback_google")
    # In production behind a reverse proxy, ensure the scheme is https
    if not is_dev_mode():
        redirect_uri = str(redirect_uri).replace("http://", "https://")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback/google")
async def callback_google(request: Request, db: Session = Depends(get_db)):
    """Exchange OAuth code for ID token, create/update user, issue JWT cookie."""
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as exc:
        logger.warning("OAuth callback failed: %s", exc)
        raise HTTPException(status_code=400, detail="OAuth authentication failed")

    userinfo = token.get("userinfo")
    if not userinfo:
        raise HTTPException(status_code=400, detail="No user info from Google")

    provider = "google"
    provider_sub = userinfo["sub"]
    email = userinfo.get("email", "")
    name = userinfo.get("name", email)

    # Lookup or create user
    user = (
        db.query(UserRow)
        .filter_by(provider=provider, provider_sub=provider_sub)
        .first()
    )
    if user is None:
        user = UserRow(
            id=str(uuid.uuid4()),
            provider=provider,
            provider_sub=provider_sub,
            email=email,
            display_name=name,
            approved=False,
        )
        db.add(user)
        db.add(UserPreferencesRow(user_id=user.id))
        db.flush()
        logger.info("New user created: %s (%s)", email, user.id)

    # Update last login
    user.last_login_at = datetime.now(timezone.utc)
    if email and user.email != email:
        user.email = email
    if name and user.display_name != name:
        user.display_name = name
    db.flush()

    # Check approval
    if not user.approved:
        response = RedirectResponse(url="/login.html?status=pending", status_code=302)
        return response

    # Issue JWT cookie
    jwt_token = create_token(user.id, user.email, user.display_name, get_jwt_secret())
    response = RedirectResponse(url="/", status_code=302)
    _set_session_cookie(response, jwt_token)
    return response


@router.post("/logout")
async def logout():
    """Clear the session cookie."""
    response = RedirectResponse(url="/login.html", status_code=302)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/me")
async def get_me(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """Return current user info from the JWT session."""
    user = db.get(UserRow, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return {
        "id": user.id,
        "email": user.email,
        "name": user.display_name,
        "approved": user.approved,
    }


def _set_session_cookie(response: RedirectResponse, token: str) -> None:
    """Set the JWT session cookie with appropriate security flags."""
    secure = not is_dev_mode()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
        max_age=7 * 24 * 3600,  # 7 days
    )
