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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import Integer, func
from sqlalchemy.orm import Session

from flyfun_common.auth import get_jwt_secret, is_dev_mode
from flyfun_common.db import get_db, DEV_USER_ID
from flyfun_common.db.deps import _decode_user_id
from flyfun_common.db.models import ApiTokenRow, UserPreferencesRow, UserRow
from flyfun_common.admin import (
    generate_api_token,
    hash_token,
    TOKEN_PREFIX,
)
from flyfun_common.db.models import CostLedgerRow
from weatherbrief.db.models import (
    BriefingUsageRow, FlightRow,
)
from weatherbrief.notify.admin_email import APPROVE_LINK_EXPIRY_SECONDS, get_admin_emails
from weatherbrief.api.security import audit_admin_action
from weatherbrief.storage.flights import safe_path_component

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# --- Admin dependency ---

def require_admin(
    request: Request,
    db: Session = Depends(get_db),
) -> str:
    """Validate that the current request comes from an admin user.

    In dev mode the dev user is always treated as admin.
    In production, authenticates via JWT cookie or Bearer token (using the
    shared auth logic in deps.py), then checks the user's email against
    ADMIN_EMAILS.
    Returns the user_id on success, raises 403 otherwise.
    """
    user_id = _decode_user_id(request, db)

    if is_dev_mode():
        return user_id

    user = db.query(UserRow).filter(UserRow.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    admin_emails = get_admin_emails()
    if user.email not in admin_emails:
        raise HTTPException(status_code=403, detail="Admin access required")

    return user_id


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
    period: str = Query(default="30d", pattern="^(30d|all)$"),
    limit: int = Query(default=25, ge=0),
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all users with usage summaries and disk usage.

    ``period`` controls the usage window: ``30d`` (last 30 days, default)
    or ``all`` (all time).  ``limit`` caps the number of human users
    returned (0 = unlimited).  Agents are always included.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    usage_since = (now - timedelta(days=30)) if period == "30d" else None

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

    # Batch-query usage grouped by user_id (filtered by period)
    usage_query = db.query(
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
    if usage_since is not None:
        usage_query = usage_query.filter(BriefingUsageRow.timestamp >= usage_since)
    usage_rows = usage_query.group_by(BriefingUsageRow.user_id).all()

    usage_map = {
        r.user_id: {
            "briefings": r.briefings,
            "gramet": int(r.gramet),
            "llm_digest": int(r.llm_digest),
            "total_tokens": int(r.input_tokens) + int(r.output_tokens),
        }
        for r in usage_rows
    }

    default_usage = {"briefings": 0, "gramet": 0, "llm_digest": 0, "total_tokens": 0}
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
            "usage": usage_map.get(u.id, default_usage),
            "disk_usage_bytes": disk,
        }
        if is_agent:
            ts = token_stats_map.get(u.id, {"token_count": 0, "active_tokens": 0, "token_last_used": None})
            entry["token_count"] = ts["token_count"]
            entry["active_tokens"] = ts["active_tokens"]
            entry["token_last_used"] = ts["token_last_used"].isoformat() if ts["token_last_used"] else None
        user_list.append(entry)

    # Aggregate summary from ALL users (before limiting)
    total_briefings = sum(u["usage"]["briefings"] for u in user_list)
    total_tokens = sum(u["usage"]["total_tokens"] for u in user_list)

    # Split humans / agents; limit humans only
    humans = [u for u in user_list if u["type"] != "agent"]
    agents = [u for u in user_list if u["type"] == "agent"]
    total_humans = len(humans)
    if limit > 0:
        humans = humans[:limit]

    return {
        "period": period,
        "summary": {
            "total_users": len(user_list),
            "total_briefings": total_briefings,
            "total_tokens": total_tokens,
            "total_disk_bytes": total_disk,
        },
        "total_humans": total_humans,
        "users": humans + agents,
    }


@router.get("/users/{user_id}/costs")
def get_user_costs(
    user_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return detailed cost data for a single user (admin only)."""
    user = db.query(UserRow).filter(UserRow.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    from weatherbrief.api.credits import SERVICE, _cost_since

    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    cost_this_month = round(_cost_since(db, user_id, month_start), 4)
    cost_this_week = round(_cost_since(db, user_id, week_start), 4)

    # --- Total cost (all time) + total briefings ---
    total_cost_usd = round(float(
        db.query(func.coalesce(func.sum(CostLedgerRow.cost), 0.0))
        .filter(
            CostLedgerRow.user_id == user_id,
            CostLedgerRow.service == SERVICE,
            CostLedgerRow.category == "briefing",
        )
        .scalar()
    ), 4)

    total_briefings = (
        db.query(func.count())
        .select_from(CostLedgerRow)
        .filter(
            CostLedgerRow.user_id == user_id,
            CostLedgerRow.service == SERVICE,
            CostLedgerRow.category == "briefing",
        )
        .scalar()
    ) or 0

    avg_cost_usd = round(total_cost_usd / total_briefings, 4) if total_briefings > 0 else 0.0

    # --- Last active ---
    last_active_row = (
        db.query(func.max(BriefingUsageRow.timestamp))
        .filter(BriefingUsageRow.user_id == user_id)
        .scalar()
    )

    # --- Transactions with flight_id via briefing_usage join ---
    tx_query = (
        db.query(CostLedgerRow, BriefingUsageRow.flight_id)
        .outerjoin(
            BriefingUsageRow,
            func.cast(CostLedgerRow.reference_id, Integer) == BriefingUsageRow.id,
        )
        .filter(
            CostLedgerRow.user_id == user_id,
            CostLedgerRow.service == SERVICE,
        )
        .order_by(CostLedgerRow.created_at.desc())
        .limit(limit)
        .all()
    )
    transactions = []
    for ledger_row, flight_id in tx_query:
        breakdown = None
        if ledger_row.detail_json:
            try:
                breakdown = json.loads(ledger_row.detail_json)
            except (json.JSONDecodeError, TypeError):
                pass
        transactions.append({
            "id": ledger_row.id,
            "timestamp": ledger_row.created_at.isoformat() if ledger_row.created_at else "",
            "cost_usd": round(ledger_row.cost, 4),
            "category": ledger_row.category or ledger_row.action,
            "description": ledger_row.description or "",
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
            "departure_time": f.departure_time.isoformat() if f.departure_time else None,
            "target_date": f.departure_time.strftime("%Y-%m-%d") if f.departure_time else "",
            "target_time_utc": f.departure_time.hour if f.departure_time else 0,
            "cruise_altitude_ft": f.cruise_altitude_ft,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in flights
    ]

    # --- Aggregate cost breakdown from all briefing ledger entries ---
    briefing_entries = (
        db.query(CostLedgerRow.detail_json)
        .filter(
            CostLedgerRow.user_id == user_id,
            CostLedgerRow.service == SERVICE,
            CostLedgerRow.category == "briefing",
            CostLedgerRow.detail_json.isnot(None),
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
        "summary": {
            "cost_this_week_usd": cost_this_week,
            "cost_this_month_usd": cost_this_month,
            "total_cost_usd": total_cost_usd,
            "total_briefings": total_briefings,
            "avg_cost_per_briefing_usd": avg_cost_usd,
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
    audit_admin_action(
        "approve_user", _admin_id, request,
        target_user_id=user.id, details=f"email={user.email}",
    )

    if not already:
        _send_welcome(user.email, user.display_name, request)

    return {"status": "approved", "user_id": user.id, "email": user.email}


class PirepPermissionsRequest(BaseModel):
    can_view: bool = True
    can_publish: bool = False


@router.post("/users/{user_id}/pirep-permissions")
def set_pirep_permissions(
    request: Request,
    user_id: str,
    body: PirepPermissionsRequest,
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Set PIREP permissions for a user."""
    row = db.get(UserPreferencesRow, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    data = json.loads(row.app_prefs_json) if row.app_prefs_json else {}
    data["pirep_can_view"] = body.can_view
    data["pirep_can_publish"] = body.can_publish
    row.app_prefs_json = json.dumps(data)
    db.flush()

    audit_admin_action(
        "set_pirep_permissions", _admin_id, request,
        target_user_id=user_id,
        details=f"can_view={body.can_view} can_publish={body.can_publish}",
    )
    return {"user_id": user_id, "pirep_can_view": body.can_view, "pirep_can_publish": body.can_publish}


@router.post("/pirep-permissions/bulk")
def bulk_set_pirep_permissions(
    request: Request,
    body: PirepPermissionsRequest,
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Enable PIREP permissions for all approved users."""
    rows = db.query(UserPreferencesRow).join(
        UserRow, UserPreferencesRow.user_id == UserRow.id
    ).filter(UserRow.approved.is_(True)).all()

    count = 0
    for row in rows:
        data = json.loads(row.app_prefs_json) if row.app_prefs_json else {}
        data["pirep_can_view"] = body.can_view
        data["pirep_can_publish"] = body.can_publish
        row.app_prefs_json = json.dumps(data)
        count += 1
    db.flush()

    audit_admin_action(
        "bulk_pirep_permissions", _admin_id, request,
        details=f"can_view={body.can_view} can_publish={body.can_publish} count={count}",
    )
    return {"updated": count, "pirep_can_view": body.can_view, "pirep_can_publish": body.can_publish}


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
    audit_admin_action(
        "one_click_approve", "link", request,
        target_user_id=user.id, details=f"email={user.email} already={already}",
    )

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
    """Generate a random API token."""
    return generate_api_token()


def _hash_token(token: str) -> str:
    """SHA-256 hash a plaintext token for storage."""
    return hash_token(token)


class CreateAgentRequest(BaseModel):
    name: str = Field(max_length=256)


class CreateTokenRequest(BaseModel):
    name: str = Field("", max_length=256)


@router.post("/agents")
def create_agent(
    request: Request,
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
    db.flush()
    db.add(UserPreferencesRow(user_id=user_id))

    plaintext = _generate_token()
    db.add(ApiTokenRow(
        user_id=user_id,
        token_hash=_hash_token(plaintext),
        name=body.name,
    ))
    db.flush()

    audit_admin_action(
        "create_agent", _admin_id, request,
        target_user_id=user_id, details=f"name={body.name}",
    )

    return {"user_id": user_id, "token": plaintext, "name": body.name}


@router.post("/agents/{user_id}/tokens")
def create_agent_token(
    request: Request,
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
    audit_admin_action(
        "create_agent_token", _admin_id, request,
        target_user_id=user_id, details=f"token_id={token_row.id}",
    )

    return {"token": plaintext, "token_id": token_row.id}


@router.delete("/agents/{user_id}")
def revoke_agent(
    request: Request,
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
    audit_admin_action(
        "revoke_agent", _admin_id, request,
        target_user_id=user_id, details=f"name={user.display_name}",
    )

    return {"status": "revoked", "user_id": user_id}


@router.get("/metrics")
def get_metrics(
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return refresh timing metrics and live queue state."""
    from weatherbrief.api.packs import refresh_registry

    # Live queue state
    snapshot = refresh_registry.queue_snapshot()
    current = {
        "active_refreshes": snapshot["refreshing"],
        "queued_refreshes": snapshot["queued"],
    }

    # Helper to build metrics for a time window
    def _window_metrics(since: datetime) -> dict:
        rows = (
            db.query(
                func.count().label("total"),
                func.avg(BriefingUsageRow.elapsed_seconds).label("avg_elapsed"),
                func.max(BriefingUsageRow.elapsed_seconds).label("max_elapsed"),
                func.avg(BriefingUsageRow.queue_wait_seconds).label("avg_queue_wait"),
                func.max(BriefingUsageRow.queue_wait_seconds).label("max_queue_wait"),
            )
            .filter(
                BriefingUsageRow.timestamp >= since,
                BriefingUsageRow.elapsed_seconds.isnot(None),
            )
            .one()
        )

        # p95 approximation: get the value at the 95th percentile position
        total = rows.total or 0
        p95_elapsed = None
        if total > 0:
            offset_95 = max(0, int(total * 0.95) - 1)
            p95_row = (
                db.query(BriefingUsageRow.elapsed_seconds)
                .filter(
                    BriefingUsageRow.timestamp >= since,
                    BriefingUsageRow.elapsed_seconds.isnot(None),
                )
                .order_by(BriefingUsageRow.elapsed_seconds.asc())
                .offset(offset_95)
                .limit(1)
                .first()
            )
            if p95_row:
                p95_elapsed = round(p95_row[0], 1)

        # by_trigger breakdown
        trigger_rows = (
            db.query(
                BriefingUsageRow.triggered_by,
                func.count().label("cnt"),
            )
            .filter(BriefingUsageRow.timestamp >= since)
            .group_by(BriefingUsageRow.triggered_by)
            .all()
        )
        by_trigger = {(r.triggered_by or "unknown"): r.cnt for r in trigger_rows}

        return {
            "total_refreshes": total,
            "avg_elapsed_seconds": round(float(rows.avg_elapsed), 1) if rows.avg_elapsed else None,
            "p95_elapsed_seconds": p95_elapsed,
            "avg_queue_wait_seconds": round(float(rows.avg_queue_wait), 1) if rows.avg_queue_wait else None,
            "max_queue_wait_seconds": round(float(rows.max_queue_wait), 1) if rows.max_queue_wait else None,
            "by_trigger": by_trigger,
        }

    now = datetime.now(timezone.utc)
    from datetime import timedelta

    return {
        "current": current,
        "last_24h": _window_metrics(now - timedelta(hours=24)),
        "last_7d": _window_metrics(now - timedelta(days=7)),
        "last_30d": _window_metrics(now - timedelta(days=30)),
    }


@router.delete("/agents/{user_id}/tokens/{token_id}")
def revoke_agent_token(
    request: Request,
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
    audit_admin_action(
        "revoke_token", _admin_id, request,
        target_user_id=user_id, details=f"token_id={token_id}",
    )

    return {"status": "revoked", "token_id": token_id}


# ---------------------------------------------------------------------------
# Verification stats
# ---------------------------------------------------------------------------


@router.get("/verification")
def get_verification_stats(
    period: str = Query(default="24h", pattern=r"^(24h|7d|30d)$"),
    source: str = Query(default="flight", pattern=r"^(flight|standalone)$"),
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return verification accuracy stats for the requested period."""
    from weatherbrief.tasks.verification_stats import get_digest_data

    now = datetime.now(timezone.utc)
    hours = {"24h": 24, "7d": 168, "30d": 720}[period]
    since = now - timedelta(hours=hours)

    data = get_digest_data(
        db, since, now,
        source=source,
        period_label=period,
        include_7d=(period != "7d"),
    )
    return data.model_dump(mode="json")
