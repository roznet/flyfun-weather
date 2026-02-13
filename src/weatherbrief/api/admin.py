"""Admin API: user management and one-click approval."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Integer, func
from sqlalchemy.orm import Session

from weatherbrief.api.auth_config import get_jwt_secret, is_dev_mode
from weatherbrief.api.jwt_utils import decode_token
from weatherbrief.db.deps import get_db
from weatherbrief.db.models import BriefingUsageRow, UserRow
from weatherbrief.db.engine import DEV_USER_ID
from weatherbrief.notify.admin_email import APPROVE_LINK_EXPIRY_SECONDS, get_admin_emails

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# --- Admin dependency ---

COOKIE_NAME = "wb_auth"


def require_admin(request: Request) -> str:
    """Validate that the current request comes from an admin user.

    In dev mode the dev user is always treated as admin.
    In production, decodes the JWT and checks the email against ADMIN_EMAILS.
    Returns the user_id on success, raises 403 otherwise.
    """
    if is_dev_mode():
        return DEV_USER_ID

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    import jwt as pyjwt

    try:
        payload = decode_token(token, get_jwt_secret())
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except (pyjwt.InvalidTokenError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid session")

    email = payload.get("email", "")
    admin_emails = get_admin_emails()
    if email not in admin_emails:
        raise HTTPException(status_code=403, detail="Admin access required")

    return payload["sub"]


# --- Endpoints ---


@router.get("/users")
def list_users(
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all users with today and month usage summaries."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    users = db.query(UserRow).order_by(UserRow.created_at.desc()).all()

    # Batch-query today's usage grouped by user_id
    today_rows = (
        db.query(
            BriefingUsageRow.user_id,
            func.count().label("briefings"),
            func.coalesce(func.sum(BriefingUsageRow.open_meteo_calls), 0).label("open_meteo"),
            func.coalesce(func.sum(func.cast(BriefingUsageRow.gramet_fetched, Integer)), 0).label("gramet"),
            func.coalesce(func.sum(func.cast(BriefingUsageRow.llm_digest, Integer)), 0).label("llm_digest"),
        )
        .filter(BriefingUsageRow.timestamp >= today_start)
        .group_by(BriefingUsageRow.user_id)
        .all()
    )
    today_map = {
        r.user_id: {
            "briefings": r.briefings,
            "open_meteo": int(r.open_meteo),
            "gramet": int(r.gramet),
            "llm_digest": int(r.llm_digest),
        }
        for r in today_rows
    }

    # Batch-query month usage grouped by user_id
    month_rows = (
        db.query(
            BriefingUsageRow.user_id,
            func.count().label("briefings"),
            func.coalesce(
                func.sum(BriefingUsageRow.llm_input_tokens), 0
            ).label("input_tokens"),
            func.coalesce(
                func.sum(BriefingUsageRow.llm_output_tokens), 0
            ).label("output_tokens"),
        )
        .filter(BriefingUsageRow.timestamp >= month_start)
        .group_by(BriefingUsageRow.user_id)
        .all()
    )
    month_map = {
        r.user_id: {
            "briefings": r.briefings,
            "total_tokens": int(r.input_tokens) + int(r.output_tokens),
        }
        for r in month_rows
    }

    default_today = {"briefings": 0, "open_meteo": 0, "gramet": 0, "llm_digest": 0}
    default_month = {"briefings": 0, "total_tokens": 0}

    result = []
    for u in users:
        result.append({
            "id": u.id,
            "email": u.email,
            "display_name": u.display_name,
            "provider": u.provider,
            "approved": u.approved,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "usage_today": today_map.get(u.id, default_today),
            "usage_month": month_map.get(u.id, default_month),
        })
    return result


@router.post("/users/{user_id}/approve")
def approve_user(
    user_id: str,
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Approve a pending user."""
    user = db.query(UserRow).filter(UserRow.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.approved = True
    db.flush()
    logger.info("User %s (%s) approved by admin", user.email, user.id)
    return {"status": "approved", "user_id": user.id, "email": user.email}


@router.get("/approve/{user_id}", response_class=HTMLResponse)
def one_click_approve(
    user_id: str,
    ts: str,
    sig: str,
    db: Session = Depends(get_db),
):
    """One-click approval from email link. Auth is via HMAC signature, no login needed."""
    # Validate HMAC
    secret = get_jwt_secret()
    expected = hmac.new(
        secret.encode(), f"approve:{user_id}:{ts}".encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=403, detail="Invalid approval link")

    # Check expiry
    try:
        link_time = int(ts)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid timestamp")

    age = time.time() - link_time
    if age > APPROVE_LINK_EXPIRY_SECONDS:
        raise HTTPException(status_code=410, detail="Approval link expired")
    if age < 0:
        raise HTTPException(status_code=400, detail="Invalid timestamp")

    # Approve
    user = db.query(UserRow).filter(UserRow.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    already = user.approved
    user.approved = True
    db.flush()

    if already:
        logger.info("One-click approve for %s — already approved", user.email)
        status_msg = f"{user.display_name} ({user.email}) was already approved."
    else:
        logger.info("One-click approve for %s — approved", user.email)
        status_msg = f"{user.display_name} ({user.email}) has been approved!"

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>User Approved — WeatherBrief</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           display: flex; justify-content: center; align-items: center; min-height: 80vh;
           background: #f8f9fa; color: #1a1a2e; }}
    .card {{ background: #fff; border: 1px solid #dee2e6; border-radius: 8px;
             padding: 2rem; text-align: center; max-width: 400px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    .check {{ font-size: 2rem; margin-bottom: 0.5rem; }}
    a {{ color: #2563eb; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="check">&#10003;</div>
    <h2>User Approved</h2>
    <p style="color:#6c757d;margin:0.75rem 0;">{status_msg}</p>
    <a href="/admin.html">Go to Admin Page</a>
  </div>
</body>
</html>"""
