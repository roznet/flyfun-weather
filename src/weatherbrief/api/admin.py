"""Admin API: user management, one-click approval, and API agent management."""

from __future__ import annotations

import hashlib
import hmac
import html as html_mod
import json
import logging
import os
import secrets
import time
import uuid
from base64 import urlsafe_b64encode
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import Integer, func
from sqlalchemy.orm import Session

from weatherbrief.api.auth_config import get_jwt_secret, is_dev_mode
from weatherbrief.api.jwt_utils import decode_token
from weatherbrief.db.deps import TOKEN_PREFIX, get_db
from weatherbrief.db.models import (
    ApiTokenRow, BriefingUsageRow, CreditLedgerRow, FlightRow,
    UserPreferencesRow, UserRow,
)
from weatherbrief.db.engine import DEV_USER_ID
from weatherbrief.notify.admin_email import APPROVE_LINK_EXPIRY_SECONDS, get_admin_emails
from weatherbrief.storage.flights import safe_path_component

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


def _user_disk_bytes(data_dir: Path, user_id: str) -> int:
    """Sum file sizes under data/packs/{safe_user_id}/."""
    user_dir = data_dir / "packs" / safe_path_component(user_id)
    if not user_dir.is_dir():
        return 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(user_dir):
        for f in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


@router.get("/users")
def list_users(
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all users with monthly usage summaries and disk usage."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    users = db.query(UserRow).all()

    # Batch-query last activity (most recent briefing_usage) per user
    last_active_rows = (
        db.query(
            BriefingUsageRow.user_id,
            func.max(BriefingUsageRow.timestamp).label("last_active"),
        )
        .group_by(BriefingUsageRow.user_id)
        .all()
    )
    last_active_map = {r.user_id: r.last_active for r in last_active_rows}

    # Sort by last activity (falling back to last_login_at, then created_at)
    def _sort_key(u: UserRow) -> datetime:
        return last_active_map.get(u.id) or u.last_login_at or u.created_at or datetime.min.replace(tzinfo=timezone.utc)

    users.sort(key=_sort_key, reverse=True)

    # Batch-query month usage grouped by user_id
    month_rows = (
        db.query(
            BriefingUsageRow.user_id,
            func.count().label("briefings"),
            func.coalesce(
                func.sum(func.cast(BriefingUsageRow.gramet_fetched, Integer)), 0
            ).label("gramet"),
            func.coalesce(
                func.sum(func.cast(BriefingUsageRow.llm_digest, Integer)), 0
            ).label("llm_digest"),
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
            "gramet": int(r.gramet),
            "llm_digest": int(r.llm_digest),
            "total_tokens": int(r.input_tokens) + int(r.output_tokens),
        }
        for r in month_rows
    }

    default_month = {"briefings": 0, "gramet": 0, "llm_digest": 0, "total_tokens": 0}
    data_dir = Path(os.environ.get("DATA_DIR", "data"))

    # Batch-query active token count and last-used per agent user
    token_stats_rows = (
        db.query(
            ApiTokenRow.user_id,
            func.count().label("token_count"),
            func.sum(func.cast(ApiTokenRow.revoked == False, Integer)).label("active_tokens"),  # noqa: E712
            func.max(ApiTokenRow.last_used_at).label("token_last_used"),
        )
        .group_by(ApiTokenRow.user_id)
        .all()
    )
    token_stats_map = {
        r.user_id: {
            "token_count": int(r.token_count),
            "active_tokens": int(r.active_tokens or 0),
            "token_last_used": r.token_last_used,
        }
        for r in token_stats_rows
    }

    user_list = []
    total_disk = 0
    for u in users:
        disk = _user_disk_bytes(data_dir, u.id)
        total_disk += disk
        is_agent = u.provider == "api_token"
        entry: dict = {
            "id": u.id,
            "email": u.email,
            "display_name": u.display_name,
            "provider": u.provider,
            "type": "agent" if is_agent else "human",
            "approved": u.approved,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "last_active_at": last_active_map[u.id].isoformat() if u.id in last_active_map else None,
            "usage_month": month_map.get(u.id, default_month),
            "disk_usage_bytes": disk,
        }
        if is_agent:
            ts = token_stats_map.get(u.id, {"token_count": 0, "active_tokens": 0, "token_last_used": None})
            entry["token_count"] = ts["token_count"]
            entry["active_tokens"] = ts["active_tokens"]
            entry["token_last_used"] = ts["token_last_used"].isoformat() if ts["token_last_used"] else None
        user_list.append(entry)

    # Aggregate summary from per-user month stats
    total_briefings = sum(u["usage_month"]["briefings"] for u in user_list)
    total_tokens = sum(u["usage_month"]["total_tokens"] for u in user_list)

    return {
        "summary": {
            "total_users": len(user_list),
            "total_briefings": total_briefings,
            "total_tokens": total_tokens,
            "total_disk_bytes": total_disk,
        },
        "users": user_list,
    }


@router.get("/users/{user_id}/costs")
def get_user_costs(
    user_id: str,
    limit: int = 50,
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return detailed cost data for a single user (admin only)."""
    user = db.query(UserRow).filter(UserRow.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # --- Credits used today/month (reuse pattern from credits.py) ---
    from weatherbrief.api.credits import _credits_used_since

    credits_today = round(_credits_used_since(db, user_id, today_start), 2)
    credits_month = round(_credits_used_since(db, user_id, month_start), 2)

    # --- Total credits charged (all time) + total briefings ---
    total_charged_result = (
        db.query(func.coalesce(func.sum(CreditLedgerRow.amount), 0.0))
        .filter(
            CreditLedgerRow.user_id == user_id,
            CreditLedgerRow.amount < 0,
        )
        .scalar()
    )
    total_credits_charged = round(abs(float(total_charged_result)), 2)

    total_briefings = (
        db.query(func.count())
        .select_from(CreditLedgerRow)
        .filter(
            CreditLedgerRow.user_id == user_id,
            CreditLedgerRow.category == "briefing",
        )
        .scalar()
    ) or 0

    avg_cost = round(total_credits_charged / total_briefings, 2) if total_briefings > 0 else 0.0

    # --- Last active ---
    last_active_row = (
        db.query(func.max(BriefingUsageRow.timestamp))
        .filter(BriefingUsageRow.user_id == user_id)
        .scalar()
    )

    # --- Transactions with flight_id via briefing_usage join ---
    tx_query = (
        db.query(CreditLedgerRow, BriefingUsageRow.flight_id)
        .outerjoin(
            BriefingUsageRow,
            CreditLedgerRow.briefing_usage_id == BriefingUsageRow.id,
        )
        .filter(CreditLedgerRow.user_id == user_id)
        .order_by(CreditLedgerRow.timestamp.desc())
        .limit(limit)
        .all()
    )
    transactions = []
    for ledger_row, flight_id in tx_query:
        breakdown = None
        if ledger_row.breakdown_json:
            try:
                breakdown = json.loads(ledger_row.breakdown_json)
            except (json.JSONDecodeError, TypeError):
                pass
        transactions.append({
            "id": ledger_row.id,
            "timestamp": ledger_row.timestamp.isoformat() if ledger_row.timestamp else "",
            "amount": ledger_row.amount,
            "balance_after": ledger_row.balance_after,
            "category": ledger_row.category,
            "description": ledger_row.description,
            "breakdown": breakdown,
            "flight_id": flight_id or None,
        })

    # --- Recent flights ---
    flights = (
        db.query(FlightRow)
        .filter(FlightRow.user_id == user_id)
        .order_by(FlightRow.created_at.desc())
        .limit(5)
        .all()
    )
    recent_flights = [
        {
            "flight_id": f.id,
            "route_name": f.route_name,
            "target_date": f.target_date,
            "target_time_utc": f.target_time_utc,
            "cruise_altitude_ft": f.cruise_altitude_ft,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in flights
    ]

    # --- Aggregate cost breakdown from all briefing ledger entries ---
    briefing_entries = (
        db.query(CreditLedgerRow.breakdown_json)
        .filter(
            CreditLedgerRow.user_id == user_id,
            CreditLedgerRow.category == "briefing",
            CreditLedgerRow.breakdown_json.isnot(None),
        )
        .all()
    )
    agg_breakdown = {
        "token_cost_usd": 0.0,
        "infra_share_usd": 0.0,
        "subscription_share_usd": 0.0,
        "storage_cost_usd": 0.0,
        "margin_usd": 0.0,
        "total_usd": 0.0,
    }
    for (bj,) in briefing_entries:
        if not bj:
            continue
        try:
            bd = json.loads(bj)
        except (json.JSONDecodeError, TypeError):
            continue
        agg_breakdown["token_cost_usd"] += bd.get("token_cost_usd", 0.0)
        agg_breakdown["infra_share_usd"] += bd.get("infra_share_usd", 0.0)
        agg_breakdown["subscription_share_usd"] += bd.get("subscription_share_usd", 0.0)
        agg_breakdown["storage_cost_usd"] += bd.get("storage_cost_usd", 0.0)
        agg_breakdown["margin_usd"] += bd.get("margin_usd", 0.0)
        agg_breakdown["total_usd"] += bd.get("total_usd", 0.0)
    # Round all aggregated values
    for k in agg_breakdown:
        agg_breakdown[k] = round(agg_breakdown[k], 4)

    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "approved": user.approved,
            "provider": user.provider,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            "last_active_at": last_active_row.isoformat() if last_active_row else None,
        },
        "credit_balance": round(user.credit_balance, 2),
        "summary": {
            "credits_used_today": credits_today,
            "credits_used_month": credits_month,
            "total_credits_charged": total_credits_charged,
            "total_briefings": total_briefings,
            "avg_cost_per_briefing": avg_cost,
        },
        "transactions": transactions,
        "recent_flights": recent_flights,
        "cost_breakdown": agg_breakdown,
    }


@router.post("/users/{user_id}/approve")
def approve_user(
    request: Request,
    user_id: str,
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Approve a pending user."""
    user = db.query(UserRow).filter(UserRow.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    already = user.approved
    user.approved = True
    db.flush()
    logger.info("User %s (%s) approved by admin", user.email, user.id)

    if not already:
        _send_welcome(user.email, user.display_name, request)

    return {"status": "approved", "user_id": user.id, "email": user.email}


@router.get("/approve/{user_id}", response_class=HTMLResponse)
def one_click_approve(
    request: Request,
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
        _send_welcome(user.email, user.display_name, request)

    return _approve_html(status_msg)


def _send_welcome(email: str, name: str, request: Request) -> None:
    """Fire-and-forget welcome email to the newly approved user."""
    try:
        from weatherbrief.notify.admin_email import send_welcome_email

        base_url = str(request.base_url).rstrip("/")
        if not is_dev_mode():
            base_url = base_url.replace("http://", "https://")
        send_welcome_email(email, name, base_url)
    except Exception:
        logger.warning("Failed to send welcome email to %s", email, exc_info=True)


def _approve_html(status_msg: str) -> str:
    """Render the one-click approval confirmation page."""
    safe_msg = html_mod.escape(status_msg)
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
    .status-msg {{ color: #6c757d; margin: 0.75rem 0; }}
    a {{ color: #2563eb; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="check">&#10003;</div>
    <h2>User Approved</h2>
    <p class="status-msg">{safe_msg}</p>
    <a href="/admin.html">Go to Admin Page</a>
  </div>
</body>
</html>"""


# --- API Agent management ---


def _generate_token() -> str:
    """Generate a random API token with the wb_ prefix (~48 chars total)."""
    return TOKEN_PREFIX + urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def _hash_token(token: str) -> str:
    """SHA-256 hash a plaintext token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


class CreateAgentRequest(BaseModel):
    name: str


class CreateTokenRequest(BaseModel):
    name: str = ""


@router.post("/agents")
def create_agent(
    body: CreateAgentRequest,
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a bot/agent user with an initial API token.

    Returns the plaintext token exactly once — it cannot be retrieved later.
    """
    user_id = f"agent-{uuid.uuid4().hex[:12]}"
    user = UserRow(
        id=user_id,
        provider="api_token",
        provider_sub=uuid.uuid4().hex,
        email="",
        display_name=body.name,
        approved=True,
    )
    db.add(user)
    db.add(UserPreferencesRow(user_id=user_id))

    plaintext = _generate_token()
    db.add(ApiTokenRow(
        user_id=user_id,
        token_hash=_hash_token(plaintext),
        name=body.name,
    ))
    db.flush()

    return {"user_id": user_id, "token": plaintext, "name": body.name}


@router.post("/agents/{user_id}/tokens")
def create_agent_token(
    user_id: str,
    body: CreateTokenRequest,
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Generate an additional API token for an existing agent user."""
    user = db.query(UserRow).filter(
        UserRow.id == user_id, UserRow.provider == "api_token"
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="Agent not found")

    plaintext = _generate_token()
    token_row = ApiTokenRow(
        user_id=user_id,
        token_hash=_hash_token(plaintext),
        name=body.name or user.display_name,
    )
    db.add(token_row)
    db.flush()

    return {"token": plaintext, "token_id": token_row.id}


@router.delete("/agents/{user_id}")
def revoke_agent(
    user_id: str,
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Revoke an agent user — disables account and all tokens."""
    user = db.query(UserRow).filter(
        UserRow.id == user_id, UserRow.provider == "api_token"
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="Agent not found")

    user.approved = False
    db.query(ApiTokenRow).filter(ApiTokenRow.user_id == user_id).update(
        {"revoked": True}
    )
    db.flush()

    return {"status": "revoked", "user_id": user_id}


@router.delete("/agents/{user_id}/tokens/{token_id}")
def revoke_agent_token(
    user_id: str,
    token_id: int,
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Revoke a single API token."""
    token_row = db.query(ApiTokenRow).filter(
        ApiTokenRow.id == token_id,
        ApiTokenRow.user_id == user_id,
    ).first()
    if not token_row:
        raise HTTPException(status_code=404, detail="Token not found")

    token_row.revoked = True
    db.flush()

    return {"status": "revoked", "token_id": token_id}
