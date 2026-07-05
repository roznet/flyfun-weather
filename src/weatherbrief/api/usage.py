"""Usage tracking: rate limiting, logging, and usage summary API."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Integer, func
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db
from weatherbrief.db.models import ApiUsageRow, BriefingUsageRow
from weatherbrief.pipeline import BriefingUsage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user/usage", tags=["usage"])

# --- Rate limits (per-user, per-day) ---

DAILY_LIMITS = {
    "open_meteo": 50,
    "gramet": 20,
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
    # Durable, all-time "has this user ever run a timing scan?" flag. Backs the
    # web flexibility-explainer gate (show the first-time modal only until the
    # user has genuinely run ≥1 scan). Count is surfaced too for future
    # heavy-user / rate-limit triggers (not enforced here).
    time_scan_used: bool = False
    time_scan_count: int = 0


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


def user_has_used_time_scan(db: Session, user_id: str) -> bool:
    """Return True if the user has ever executed a timing scan.

    Existence check, not a COUNT: one ``api_usage_log`` row is written per
    executed ``time_scan`` (see ``tasks/time_scan_runner.py``). The
    ``ix_api_usage_service`` index narrows to the small ``time_scan`` slice and
    the ``LIMIT 1`` short-circuits, so this stays instant even for heavy users.
    """
    row = (
        db.query(ApiUsageRow.id)
        .filter(
            ApiUsageRow.user_id == user_id,
            ApiUsageRow.service == "time_scan",
        )
        .first()
    )
    return row is not None


def count_user_time_scans(
    db: Session, user_id: str, since: datetime | None = None
) -> int:
    """Count executed timing scans for a user (optionally since a timestamp).

    Substrate for future triggers (heavy-user message, daily ``time_scan``
    rate limit) — neither enforced yet. Cheap over the ``time_scan`` slice;
    no dedicated ``user_id`` index needed at current volumes.
    """
    q = db.query(func.count()).filter(
        ApiUsageRow.user_id == user_id,
        ApiUsageRow.service == "time_scan",
    )
    if since is not None:
        q = q.filter(ApiUsageRow.timestamp >= since)
    return int(q.scalar() or 0)


def check_rate_limits(db: Session, user_id: str, *, bypass: bool = False) -> None:
    """Check daily rate limits. Raises HTTPException(429) only for core data (Open-Meteo).

    GRAMET and LLM digest limits are handled gracefully via
    ``check_service_limits`` — those services are simply skipped when
    their daily quota is exhausted.

    Pass ``bypass=True`` for admin/dev users to skip the check.
    """
    if bypass:
        return

    usage = _query_today_usage(db, user_id)

    if usage["open_meteo"] >= DAILY_LIMITS["open_meteo"]:
        raise HTTPException(
            status_code=429,
            detail=f"Daily Open-Meteo limit reached ({DAILY_LIMITS['open_meteo']} calls/day)",
        )


def check_service_limits(db: Session, user_id: str) -> dict[str, bool]:
    """Return which optional services are still within their daily limits.

    Returns dict with ``gramet`` and ``llm_digest`` booleans (True = OK to use).
    """
    usage = _query_today_usage(db, user_id)
    return {
        "gramet": usage["gramet"] < DAILY_LIMITS["gramet"],
        "llm_digest": usage["llm_digest"] < DAILY_LIMITS["llm_digest"],
    }


# briefing_usage.flight_id is VARCHAR(256); leave room for "-" + 8 hex chars
# so an over-length value still produces a unique, deterministic key.
_FLIGHT_ID_COL_LIMIT = 256


def _clip_flight_id(flight_id: str, max_len: int = _FLIGHT_ID_COL_LIMIT) -> str:
    if len(flight_id) <= max_len:
        return flight_id
    digest = hashlib.sha1(flight_id.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    prefix = flight_id[: max_len - 9]
    logger.warning(
        "flight_id length %d exceeds %d, clipping to %s-%s",
        len(flight_id), max_len, prefix, digest,
    )
    return f"{prefix}-{digest}"


def log_briefing_usage(
    db: Session,
    user_id: str,
    flight_id: str,
    usage: BriefingUsage,
    *,
    result_size_bytes: int | None = None,
) -> int:
    """Insert a BriefingUsageRow after a briefing refresh. Returns the row id."""
    flight_id = _clip_flight_id(flight_id)
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
        result_size_bytes=result_size_bytes,
        elapsed_seconds=usage.elapsed_seconds,
        queue_wait_seconds=usage.queue_wait_seconds,
        triggered_by=usage.triggered_by,
    )
    db.add(row)
    db.flush()
    logger.info(
        "Usage logged for %s flight=%s: meteo=%d gramet=%s llm=%s",
        user_id, flight_id, usage.open_meteo_calls,
        usage.gramet_fetched, usage.llm_digest,
    )
    return row.id


def log_api_usage(
    db: Session,
    *,
    service: str,
    pipeline: str,
    api_calls: int,
    user_id: str | None = None,
    flight_id: str | None = None,
) -> None:
    """Log external API calls for subscription limit monitoring."""
    if api_calls <= 0:
        return
    db.add(ApiUsageRow(
        service=service,
        pipeline=pipeline,
        api_calls=api_calls,
        user_id=user_id,
        flight_id=flight_id,
    ))
    db.flush()
    logger.info(
        "API usage: service=%s pipeline=%s calls=%d user=%s",
        service, pipeline, api_calls, user_id or "-",
    )


class ApiUsageBucket(BaseModel):
    service: str
    pipeline: str
    total_calls: int


class ApiUsagePeriod(BaseModel):
    label: str
    by_service: list[ApiUsageBucket]
    total_calls: int


class ApiUsageResponse(BaseModel):
    current_month: ApiUsagePeriod
    last_30d: ApiUsagePeriod
    all_time: ApiUsagePeriod


def _query_api_usage(db: Session, since: datetime | None = None) -> list[ApiUsageBucket]:
    q = db.query(
        ApiUsageRow.service,
        ApiUsageRow.pipeline,
        func.coalesce(func.sum(ApiUsageRow.api_calls), 0).label("total"),
    )
    if since is not None:
        q = q.filter(ApiUsageRow.timestamp >= since)
    rows = q.group_by(ApiUsageRow.service, ApiUsageRow.pipeline).all()
    return [
        ApiUsageBucket(service=r.service, pipeline=r.pipeline, total_calls=int(r.total))
        for r in rows
    ]


def _make_period(label: str, buckets: list[ApiUsageBucket]) -> ApiUsagePeriod:
    return ApiUsagePeriod(
        label=label, by_service=buckets,
        total_calls=sum(b.total_calls for b in buckets),
    )


def get_api_usage_summary(db: Session) -> ApiUsageResponse:
    """Aggregate API calls for current month, last 30 days, and all time."""
    month = _month_start()
    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)

    return ApiUsageResponse(
        current_month=_make_period(month.strftime("%Y-%m"), _query_api_usage(db, month)),
        last_30d=_make_period("last 30 days", _query_api_usage(db, thirty_days_ago)),
        all_time=_make_period("all time", _query_api_usage(db)),
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

    time_scan_count = count_user_time_scans(db, user_id)

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
        time_scan_used=time_scan_count > 0,
        time_scan_count=time_scan_count,
    )


# --- API endpoint ---


@router.get("", response_model=UsageSummary)
def get_usage(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> UsageSummary:
    """Return usage summary for the current user."""
    return get_usage_summary(db, user_id)
