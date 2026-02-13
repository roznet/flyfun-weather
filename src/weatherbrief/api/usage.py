"""Usage tracking: rate limiting, logging, and usage summary API."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Integer, func
from sqlalchemy.orm import Session

from weatherbrief.db.deps import current_user_id, get_db
from weatherbrief.db.models import BriefingUsageRow
from weatherbrief.pipeline import BriefingUsage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user/usage", tags=["usage"])

# --- Rate limits (per-user, per-day) ---

DAILY_LIMITS = {
    "open_meteo": 50,
    "gramet": 10,
    "llm_digest": 20,
}


# --- Pydantic response models ---


class ServiceUsage(BaseModel):
    used: int
    limit: int


class TodayUsage(BaseModel):
    briefings: int
    open_meteo: ServiceUsage
    gramet: ServiceUsage
    llm_digest: ServiceUsage


class MonthUsage(BaseModel):
    briefings: int
    gramet: int
    llm_digest: int
    total_tokens: int


class UsageSummary(BaseModel):
    today: TodayUsage
    month: MonthUsage


# --- Core functions ---


def _today_start() -> datetime:
    """Return midnight UTC for today."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start() -> datetime:
    """Return midnight UTC on the 1st of this month."""
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _query_today_usage(db: Session, user_id: str) -> dict:
    """Query today's aggregate usage for a user."""
    today = _today_start()
    row = (
        db.query(
            func.count().label("briefings"),
            func.coalesce(func.sum(BriefingUsageRow.open_meteo_calls), 0).label("open_meteo"),
            func.coalesce(func.sum(func.cast(BriefingUsageRow.gramet_fetched, Integer)), 0).label("gramet"),
            func.coalesce(func.sum(func.cast(BriefingUsageRow.llm_digest, Integer)), 0).label("llm_digest"),
        )
        .filter(
            BriefingUsageRow.user_id == user_id,
            BriefingUsageRow.timestamp >= today,
        )
        .one()
    )
    return {
        "briefings": row.briefings,
        "open_meteo": int(row.open_meteo),
        "gramet": int(row.gramet),
        "llm_digest": int(row.llm_digest),
    }


def check_rate_limits(db: Session, user_id: str) -> None:
    """Check daily rate limits. Raises HTTPException(429) if any limit exceeded."""
    usage = _query_today_usage(db, user_id)

    if usage["open_meteo"] >= DAILY_LIMITS["open_meteo"]:
        raise HTTPException(
            status_code=429,
            detail=f"Daily Open-Meteo limit reached ({DAILY_LIMITS['open_meteo']} calls/day)",
        )
    if usage["gramet"] >= DAILY_LIMITS["gramet"]:
        raise HTTPException(
            status_code=429,
            detail=f"Daily GRAMET limit reached ({DAILY_LIMITS['gramet']} fetches/day)",
        )
    if usage["llm_digest"] >= DAILY_LIMITS["llm_digest"]:
        raise HTTPException(
            status_code=429,
            detail=f"Daily LLM digest limit reached ({DAILY_LIMITS['llm_digest']} calls/day)",
        )


def log_briefing_usage(
    db: Session, user_id: str, flight_id: str, usage: BriefingUsage,
) -> None:
    """Insert a BriefingUsageRow after a briefing refresh."""
    row = BriefingUsageRow(
        user_id=user_id,
        flight_id=flight_id,
        open_meteo_calls=usage.open_meteo_calls,
        gramet_fetched=usage.gramet_fetched,
        gramet_failed=usage.gramet_failed,
        llm_digest=usage.llm_digest,
        llm_model=usage.llm_model,
        llm_input_tokens=usage.llm_input_tokens,
        llm_output_tokens=usage.llm_output_tokens,
    )
    db.add(row)
    db.flush()
    logger.info(
        "Usage logged for %s flight=%s: meteo=%d gramet=%s llm=%s",
        user_id, flight_id, usage.open_meteo_calls,
        usage.gramet_fetched, usage.llm_digest,
    )


def get_usage_summary(db: Session, user_id: str) -> UsageSummary:
    """Aggregate today + this month usage for a user."""
    today_data = _query_today_usage(db, user_id)

    # Month aggregation
    month = _month_start()
    month_row = (
        db.query(
            func.count().label("briefings"),
            func.coalesce(func.sum(func.cast(BriefingUsageRow.gramet_fetched, Integer)), 0).label("gramet"),
            func.coalesce(func.sum(func.cast(BriefingUsageRow.llm_digest, Integer)), 0).label("llm_digest"),
            func.coalesce(
                func.sum(BriefingUsageRow.llm_input_tokens), 0
            ).label("input_tokens"),
            func.coalesce(
                func.sum(BriefingUsageRow.llm_output_tokens), 0
            ).label("output_tokens"),
        )
        .filter(
            BriefingUsageRow.user_id == user_id,
            BriefingUsageRow.timestamp >= month,
        )
        .one()
    )

    return UsageSummary(
        today=TodayUsage(
            briefings=today_data["briefings"],
            open_meteo=ServiceUsage(
                used=today_data["open_meteo"],
                limit=DAILY_LIMITS["open_meteo"],
            ),
            gramet=ServiceUsage(
                used=today_data["gramet"],
                limit=DAILY_LIMITS["gramet"],
            ),
            llm_digest=ServiceUsage(
                used=today_data["llm_digest"],
                limit=DAILY_LIMITS["llm_digest"],
            ),
        ),
        month=MonthUsage(
            briefings=month_row.briefings,
            gramet=int(month_row.gramet),
            llm_digest=int(month_row.llm_digest),
            total_tokens=int(month_row.input_tokens) + int(month_row.output_tokens),
        ),
    )


# --- API endpoint ---


@router.get("", response_model=UsageSummary)
def get_usage(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> UsageSummary:
    """Return usage summary for the current user."""
    return get_usage_summary(db, user_id)
