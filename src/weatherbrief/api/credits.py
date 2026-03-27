"""Cost system: per-briefing cost tracking, transparency API, admin config."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from weatherbrief.api.admin import require_admin
from weatherbrief.costs import (
    CostBreakdown,
    CostConfig,
    DEFAULT_CONFIG,
    breakdown_to_dict,
    compute_cost,
    config_from_row,
)
from flyfun_common.costs import record_cost
from flyfun_common.db import current_user_id, get_db
from flyfun_common.db.models import CostLedgerRow, UserRow
from weatherbrief.db.models import CostConfigRow

logger = logging.getLogger(__name__)

AUTO_RELOAD_AMOUNT = 5.0  # USD — auto-reload threshold for spending_limit
SERVICE = "flyfun-weather"

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
    """Record briefing cost. Auto-reload spending_limit if it drops to 0."""
    user = db.query(UserRow).filter(UserRow.id == user_id).with_for_update().first()
    if not user:
        raise ValueError(f"User {user_id} not found")

    user.spending_limit = user.spending_limit - breakdown.total_usd

    entry = record_cost(
        db,
        user_id,
        service=SERVICE,
        action="briefing",
        cost=breakdown.total_usd,
        category="briefing",
        description=f"Briefing (${breakdown.total_usd:.4f})",
        detail_json=json.dumps(breakdown_to_dict(breakdown)),
        reference_id=str(usage_row_id),
    )

    if user.spending_limit <= 0:
        _auto_reload(db, user)

    return entry


def _auto_reload(db: Session, user: UserRow) -> None:
    """Reset user's spending_limit and log a topup entry."""
    user.spending_limit = AUTO_RELOAD_AMOUNT
    record_cost(
        db,
        user.id,
        service=SERVICE,
        action="topup",
        cost=0.0,
        category="topup",
        description="Auto-reload (free tier)",
    )
    logger.info("Auto-reloaded spending limit to $%.2f for user %s", AUTO_RELOAD_AMOUNT, user.id)


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
    cost_usd: float
    category: str
    description: str
    breakdown: dict | None = None


class CostSummaryResponse(BaseModel):
    total_cost_usd: float
    cost_this_month_usd: float
    cost_this_week_usd: float
    total_briefings: int
    recent_transactions: list[TransactionResponse]


class CostConfigResponse(BaseModel):
    id: int
    active_from: str
    active_until: str | None
    config: dict


class TransparencyResponse(BaseModel):
    """Public-facing cost structure.  Flat dict derived from config JSON."""
    token_cost_per_1k_input: float
    token_cost_per_1k_output: float
    infra_monthly_usd: float
    subscriptions_monthly_usd: float
    subscription_details: dict | None
    disk_cost_per_gb_monthly: float
    estimated_monthly_briefings: int
    margin_percent: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config_to_response(row: CostConfigRow) -> CostConfigResponse:
    return CostConfigResponse(
        id=row.id,
        active_from=row.active_from.isoformat() if row.active_from else "",
        active_until=row.active_until.isoformat() if row.active_until else None,
        config=json.loads(row.config_json),
    )


def _transaction_to_response(row: CostLedgerRow) -> TransactionResponse:
    breakdown = None
    if row.detail_json:
        try:
            breakdown = json.loads(row.detail_json)
        except (json.JSONDecodeError, TypeError):
            pass
    return TransactionResponse(
        id=row.id,
        timestamp=row.created_at.isoformat() if row.created_at else "",
        cost_usd=round(row.cost, 4),
        category=row.category or row.action,
        description=row.description or "",
        breakdown=breakdown,
    )


def _cost_since(db: Session, user_id: str, since: datetime) -> float:
    """Sum of USD costs since a given time."""
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
    return float(result)


# ---------------------------------------------------------------------------
# Router: user costs
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/user/credits", tags=["costs"])


@router.get("", response_model=CostSummaryResponse)
def get_costs(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> CostSummaryResponse:
    """Return cost summary and recent transactions for the current user."""
    user = db.query(UserRow).filter(UserRow.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    transactions = get_recent_transactions(db, user_id)

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    total_cost = float(
        db.query(func.coalesce(func.sum(CostLedgerRow.cost), 0.0))
        .filter(
            CostLedgerRow.user_id == user_id,
            CostLedgerRow.service == SERVICE,
            CostLedgerRow.category == "briefing",
        )
        .scalar()
    )
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

    return CostSummaryResponse(
        total_cost_usd=round(total_cost, 4),
        cost_this_month_usd=round(_cost_since(db, user_id, month_start), 4),
        cost_this_week_usd=round(_cost_since(db, user_id, week_start), 4),
        total_briefings=total_briefings,
        recent_transactions=[_transaction_to_response(t) for t in transactions],
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
    body: dict,
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a new cost config version; deactivate the previous one.

    Accepts a partial dict of config keys to update.  Unchanged keys are
    inherited from the current active config.
    """
    from dataclasses import fields as dc_fields

    # Reject unknown keys
    known_keys = {f.name for f in dc_fields(CostConfig)}
    unknown = set(body.keys()) - known_keys
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown config keys: {', '.join(sorted(unknown))}",
        )

    now = datetime.now(timezone.utc)

    # Deactivate current
    current = get_active_cost_config(db)
    current_config = (
        json.loads(current.config_json) if current else {}
    )

    if current:
        current.active_until = now
        db.flush()

    # Merge: defaults ← current values ← overrides, then validate
    defaults = json.loads(DEFAULT_CONFIG.to_json())
    merged = {**defaults, **current_config, **body}
    CostConfig.from_json(json.dumps(merged))

    new_row = CostConfigRow(
        active_from=now,
        config_json=json.dumps(merged),
    )
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
    cfg = CostConfig.from_json(row.config_json)
    return TransparencyResponse(
        token_cost_per_1k_input=cfg.token_cost_per_1k_input,
        token_cost_per_1k_output=cfg.token_cost_per_1k_output,
        infra_monthly_usd=cfg.droplet_monthly_usd + cfg.misc_monthly_usd,
        subscriptions_monthly_usd=cfg.subscriptions_monthly_usd,
        subscription_details=cfg.subscription_details,
        disk_cost_per_gb_monthly=cfg.disk_cost_per_gb_monthly,
        estimated_monthly_briefings=cfg.estimated_monthly_briefings,
        margin_percent=cfg.margin_percent,
    )
