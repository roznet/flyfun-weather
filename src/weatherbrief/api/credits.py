"""Credit system: balance tracking, cost charging, and transparency API."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from weatherbrief.api.admin import require_admin
from weatherbrief.costs import (
    CostBreakdown,
    breakdown_to_dict,
    compute_cost,
    config_from_row,
)
from flyfun_common.costs import record_cost
from flyfun_common.db import current_user_id, get_db
from flyfun_common.db.models import CostLedgerRow, UserRow
from weatherbrief.db.models import CostConfigRow

logger = logging.getLogger(__name__)

AUTO_RELOAD_CREDITS = 500.0
SERVICE = "flyfun-weather"
USD_PER_CREDIT = 0.01  # stable conversion factor

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def get_active_cost_config(db: Session) -> CostConfigRow | None:
    """Return the currently active cost config (active_until IS NULL)."""
    return (
        db.query(CostConfigRow)
        .filter(CostConfigRow.active_until.is_(None))
        .order_by(CostConfigRow.active_from.desc())
        .first()
    )


def charge_briefing(
    db: Session,
    user_id: str,
    usage_row_id: int,
    breakdown: CostBreakdown,
) -> CostLedgerRow:
    """Deduct credits for a briefing, auto-reload if balance drops to 0 or below."""
    user = db.query(UserRow).filter(UserRow.id == user_id).with_for_update().first()
    if not user:
        raise ValueError(f"User {user_id} not found")

    new_balance = user.spending_limit - breakdown.credits_charged
    user.spending_limit = new_balance

    entry = record_cost(
        db,
        user_id,
        service=SERVICE,
        action="briefing",
        cost=breakdown.total_usd,
        category="briefing",
        description=f"Briefing cost ({breakdown.credits_charged:.2f} credits)",
        detail_json=json.dumps(breakdown_to_dict(breakdown)),
        reference_id=str(usage_row_id),
    )

    if new_balance <= 0:
        _auto_reload(db, user)

    return entry


def _auto_reload(db: Session, user: UserRow) -> None:
    """Reset user's balance to AUTO_RELOAD_CREDITS and log a topup entry."""
    user.spending_limit = AUTO_RELOAD_CREDITS
    record_cost(
        db,
        user.id,
        service=SERVICE,
        action="topup",
        cost=0.0,
        category="topup",
        description="Auto-reload (free tier)",
    )
    logger.info("Auto-reloaded %s credits for user %s", AUTO_RELOAD_CREDITS, user.id)


def get_recent_transactions(
    db: Session, user_id: str, limit: int = 20,
) -> list[CostLedgerRow]:
    """Return the most recent ledger entries for a user."""
    return (
        db.query(CostLedgerRow)
        .filter(
            CostLedgerRow.user_id == user_id,
            CostLedgerRow.service == SERVICE,
        )
        .order_by(CostLedgerRow.created_at.desc())
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class TransactionResponse(BaseModel):
    id: int
    timestamp: str
    amount: float
    balance_after: float
    category: str
    description: str
    breakdown: dict | None = None


class CreditSummaryResponse(BaseModel):
    balance: float
    recent_transactions: list[TransactionResponse]
    credits_used_today: float
    credits_used_month: float


class CostConfigResponse(BaseModel):
    id: int
    active_from: str
    active_until: str | None
    token_cost_per_1k_input: float
    token_cost_per_1k_output: float
    droplet_monthly_usd: float
    misc_monthly_usd: float
    subscriptions_monthly_usd: float
    subscription_details_json: str
    disk_cost_per_gb_monthly: float
    estimated_monthly_briefings: int
    margin_percent: float
    usd_per_credit: float


class CostConfigUpdate(BaseModel):
    token_cost_per_1k_input: float | None = None
    token_cost_per_1k_output: float | None = None
    droplet_monthly_usd: float | None = None
    misc_monthly_usd: float | None = None
    subscriptions_monthly_usd: float | None = None
    subscription_details_json: str | None = None
    disk_cost_per_gb_monthly: float | None = None
    estimated_monthly_briefings: int | None = None
    margin_percent: float | None = None
    usd_per_credit: float | None = None

    @field_validator(
        "token_cost_per_1k_input", "token_cost_per_1k_output",
        "droplet_monthly_usd", "misc_monthly_usd",
        "subscriptions_monthly_usd", "disk_cost_per_gb_monthly",
    )
    @classmethod
    def non_negative_float(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("value must be non-negative")
        return v

    @field_validator("usd_per_credit")
    @classmethod
    def positive_usd_per_credit(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("usd_per_credit must be positive")
        return v

    @field_validator("margin_percent")
    @classmethod
    def reasonable_margin(cls, v: float | None) -> float | None:
        if v is not None and (v < 0 or v > 200):
            raise ValueError("margin_percent must be between 0 and 200")
        return v

    @field_validator("estimated_monthly_briefings")
    @classmethod
    def positive_briefings(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("estimated_monthly_briefings must be at least 1")
        return v


class TransparencyResponse(BaseModel):
    token_cost_per_1k_input: float
    token_cost_per_1k_output: float
    infra_monthly_usd: float
    subscriptions_monthly_usd: float
    disk_cost_per_gb_monthly: float
    estimated_monthly_briefings: int
    margin_percent: float
    usd_per_credit: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config_to_response(row: CostConfigRow) -> CostConfigResponse:
    return CostConfigResponse(
        id=row.id,
        active_from=row.active_from.isoformat() if row.active_from else "",
        active_until=row.active_until.isoformat() if row.active_until else None,
        token_cost_per_1k_input=row.token_cost_per_1k_input,
        token_cost_per_1k_output=row.token_cost_per_1k_output,
        droplet_monthly_usd=row.droplet_monthly_usd,
        misc_monthly_usd=row.misc_monthly_usd,
        subscriptions_monthly_usd=row.subscriptions_monthly_usd,
        subscription_details_json=row.subscription_details_json,
        disk_cost_per_gb_monthly=row.disk_cost_per_gb_monthly,
        estimated_monthly_briefings=row.estimated_monthly_briefings,
        margin_percent=row.margin_percent,
        usd_per_credit=row.usd_per_credit,
    )


def _transaction_to_response(row: CostLedgerRow) -> TransactionResponse:
    breakdown = None
    if row.detail_json:
        try:
            breakdown = json.loads(row.detail_json)
        except (json.JSONDecodeError, TypeError):
            pass
    # Backward-compat: amount in credits (negative for charges)
    if row.category == "topup":
        amount = AUTO_RELOAD_CREDITS
    else:
        amount = -row.cost / USD_PER_CREDIT if USD_PER_CREDIT > 0 else 0.0
    return TransactionResponse(
        id=row.id,
        timestamp=row.created_at.isoformat() if row.created_at else "",
        amount=round(amount, 2),
        balance_after=0.0,  # deprecated — balance is on UserRow.spending_limit
        category=row.category or row.action,
        description=row.description or "",
        breakdown=breakdown,
    )


def _credits_used_since(db: Session, user_id: str, since: datetime) -> float:
    """Sum of costs (converted to credits) since a given time."""
    result = (
        db.query(func.coalesce(func.sum(CostLedgerRow.cost), 0.0))
        .filter(
            CostLedgerRow.user_id == user_id,
            CostLedgerRow.service == SERVICE,
            CostLedgerRow.category == "briefing",
            CostLedgerRow.created_at >= since,
        )
        .scalar()
    )
    return float(result) / USD_PER_CREDIT if USD_PER_CREDIT > 0 else 0.0


# ---------------------------------------------------------------------------
# Router: user credits
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/user/credits", tags=["credits"])


@router.get("", response_model=CreditSummaryResponse)
def get_credits(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> CreditSummaryResponse:
    """Return credit balance and recent transactions for the current user."""
    user = db.query(UserRow).filter(UserRow.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    balance = user.spending_limit

    transactions = get_recent_transactions(db, user_id)

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    return CreditSummaryResponse(
        balance=round(balance, 2),
        recent_transactions=[_transaction_to_response(t) for t in transactions],
        credits_used_today=round(_credits_used_since(db, user_id, today_start), 2),
        credits_used_month=round(_credits_used_since(db, user_id, month_start), 2),
    )


# ---------------------------------------------------------------------------
# Router: admin cost config
# ---------------------------------------------------------------------------

admin_router = APIRouter(prefix="/admin/cost-config", tags=["admin"])


@admin_router.get("", response_model=CostConfigResponse | None)
def get_cost_config(
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return the current active cost configuration."""
    row = get_active_cost_config(db)
    return _config_to_response(row) if row else None


@admin_router.put("", response_model=CostConfigResponse)
def update_cost_config(
    body: CostConfigUpdate,
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a new cost config version; deactivate the previous one."""
    now = datetime.now(timezone.utc)

    # Deactivate current
    current = get_active_cost_config(db)
    if current:
        current.active_until = now
        db.flush()

    # Build new config, inheriting unchanged fields from current
    fields = {
        "token_cost_per_1k_input", "token_cost_per_1k_output",
        "droplet_monthly_usd", "misc_monthly_usd",
        "subscriptions_monthly_usd", "subscription_details_json",
        "disk_cost_per_gb_monthly", "estimated_monthly_briefings",
        "margin_percent", "usd_per_credit",
    }
    kwargs: dict = {"active_from": now}
    for f in fields:
        new_val = getattr(body, f, None)
        if new_val is not None:
            kwargs[f] = new_val
        elif current:
            kwargs[f] = getattr(current, f)

    new_row = CostConfigRow(**kwargs)
    db.add(new_row)
    db.flush()

    return _config_to_response(new_row)


@admin_router.get("/history", response_model=list[CostConfigResponse])
def get_cost_config_history(
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return all cost config versions, newest first."""
    rows = (
        db.query(CostConfigRow)
        .order_by(CostConfigRow.active_from.desc())
        .all()
    )
    return [_config_to_response(r) for r in rows]


# ---------------------------------------------------------------------------
# Router: transparency (public)
# ---------------------------------------------------------------------------

transparency_router = APIRouter(prefix="/transparency", tags=["transparency"])


@transparency_router.get("", response_model=TransparencyResponse | None)
def get_transparency(db: Session = Depends(get_db)):
    """Return the public-facing cost structure (no auth required)."""
    row = get_active_cost_config(db)
    if not row:
        return None
    return TransparencyResponse(
        token_cost_per_1k_input=row.token_cost_per_1k_input,
        token_cost_per_1k_output=row.token_cost_per_1k_output,
        infra_monthly_usd=row.droplet_monthly_usd + row.misc_monthly_usd,
        subscriptions_monthly_usd=row.subscriptions_monthly_usd,
        disk_cost_per_gb_monthly=row.disk_cost_per_gb_monthly,
        estimated_monthly_briefings=row.estimated_monthly_briefings,
        margin_percent=row.margin_percent,
        usd_per_credit=row.usd_per_credit,
    )
