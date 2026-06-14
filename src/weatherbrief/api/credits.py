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
    compute_program_cost,
    config_from_row,
    program_report_to_dict,
)
from flyfun_common.costs import record_cost
from flyfun_common.db import current_user_id, get_db, optional_user_id
from flyfun_common.db.models import CostLedgerRow, UserRow
from weatherbrief.api.preferences import fx_block_for_user
from weatherbrief.db.models import CostConfigRow

# Default fx block when there's no logged-in viewer (public transparency).
_USD_FX = {"currency": "USD", "rate": 1.0}

logger = logging.getLogger(__name__)

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
    """Record a briefing's cost in the shared cost ledger.

    The service is free: there is no per-user spend balance to decrement. We
    simply append the real USD cost (the old ``spending_limit`` decrement +
    ``$5`` auto-reload churn was retired with issue #186).
    """
    return record_cost(
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
    # USD stays canonical; the frontend renders the viewer's currency from this.
    fx: dict


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
    # USD stays canonical; the frontend renders the viewer's currency from this.
    fx: dict


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
        fx=fx_block_for_user(db, user_id),
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

    # Subscriptions are itemized in subscription_details; keep the total in sync
    # so the rate card never drifts from its line items.
    details = merged.get("subscription_details")
    if details:
        try:
            merged["subscriptions_monthly_usd"] = round(
                sum(float(v) for v in details.values()), 4
            )
        except (TypeError, ValueError, AttributeError):
            raise HTTPException(
                status_code=422,
                detail="subscription_details values must be numbers",
            )

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
# Router: admin program cost report
# ---------------------------------------------------------------------------

report_router = APIRouter(prefix="/admin/cost-report", tags=["admin"])

_ALLOWED_WINDOWS = {"7d": 7, "30d": 30}


def _program_variable_and_counts(
    db: Session, since: datetime,
) -> tuple[float, float, int, int]:
    """Sum actual token/storage cost and count briefings + distinct users since.

    Variable cost is pulled from each briefing's stored CostBreakdown so it
    reflects what was actually charged, independent of the current rate card.
    """
    rows = (
        db.query(CostLedgerRow.detail_json, CostLedgerRow.user_id)
        .filter(
            CostLedgerRow.service == SERVICE,
            CostLedgerRow.category == "briefing",
            CostLedgerRow.created_at >= since,
        )
        .all()
    )
    token_usd = 0.0
    storage_usd = 0.0
    users: set[str] = set()
    for detail_json, user_id in rows:
        users.add(user_id)
        if not detail_json:
            continue
        try:
            bd = json.loads(detail_json)
        except (json.JSONDecodeError, TypeError):
            continue
        token_usd += bd.get("token_cost_usd", 0.0)
        storage_usd += bd.get("storage_cost_usd", 0.0)
    return token_usd, storage_usd, len(rows), len(users)


def build_program_report(db: Session, window_days: int):
    """Build a ProgramCostReport for a window, or None when no config exists.

    Shared by the admin cost-report endpoint and the donation-impact endpoints
    (which derive margin-excluded run-cost economics from it).
    """
    config_row = get_active_cost_config(db)
    if not config_row:
        return None

    config, config_id = config_from_row(config_row)
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    token_usd, storage_usd, num_briefings, num_users = _program_variable_and_counts(db, since)

    return compute_program_cost(
        config=config,
        config_id=config_id,
        window_days=window_days,
        variable_token_usd=token_usd,
        variable_storage_usd=storage_usd,
        num_briefings=num_briefings,
        num_users=num_users,
    )


@report_router.get("")
def get_cost_report(
    window: str = "30d",
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Program-wide cost report for a window: real fixed cost + actual variable."""
    window_days = _ALLOWED_WINDOWS.get(window)
    if window_days is None:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid window '{window}'. Use one of: {', '.join(_ALLOWED_WINDOWS)}",
        )

    report = build_program_report(db, window_days)
    if report is None:
        return None
    return program_report_to_dict(report)


# ---------------------------------------------------------------------------
# Router: transparency (public)
# ---------------------------------------------------------------------------

transparency_router = APIRouter(prefix="/transparency", tags=["transparency"])


@transparency_router.get("", response_model=TransparencyResponse | None)
def get_transparency(
    db: Session = Depends(get_db),
    viewer_id: str | None = Depends(optional_user_id),
):
    """Return the public-facing cost structure (no auth required).

    Carries an ``fx`` block so logged-in viewers see costs in their currency;
    anonymous viewers get the USD-canonical block.
    """
    row = get_active_cost_config(db)
    if not row:
        return None
    cfg = CostConfig.from_json(row.config_json)
    fx = fx_block_for_user(db, viewer_id) if viewer_id else dict(_USD_FX)
    return TransparencyResponse(
        token_cost_per_1k_input=cfg.token_cost_per_1k_input,
        token_cost_per_1k_output=cfg.token_cost_per_1k_output,
        infra_monthly_usd=cfg.droplet_monthly_usd + cfg.misc_monthly_usd,
        subscriptions_monthly_usd=cfg.subscriptions_monthly_usd,
        subscription_details=cfg.subscription_details,
        disk_cost_per_gb_monthly=cfg.disk_cost_per_gb_monthly,
        estimated_monthly_briefings=cfg.estimated_monthly_briefings,
        margin_percent=cfg.margin_percent,
        fx=fx,
    )
