"""Weather overview map endpoints.

Public:
  GET /maps/forecast  — forecast overview for all watchlist airports
Admin:
  GET /maps/verification — per-airport verification accuracy stats
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db

from weatherbrief.api.admin import require_admin

logger = logging.getLogger(__name__)

# Public router — authenticated users
router = APIRouter(prefix="/maps", tags=["maps"])

# Admin router — reuses /admin prefix from app.py
admin_router = APIRouter(prefix="/maps", tags=["admin-maps"])

# Standalone sample hours (must match standalone_verification.py)
_SAMPLE_HOURS = [6, 9, 12, 15, 18]


def _airports_db(request: Request) -> str:
    return request.app.state.db_path


@router.get("/forecast")
def get_forecast_map(
    day: int = Query(default=0, ge=0, le=3),
    hour: int = Query(default=12),
    mode: str = Query(default="worst", pattern=r"^(worst|majority)$"),
    _user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    airports_db: str = Depends(_airports_db),
):
    """Return forecast overview data for all watchlist airports.

    Parameters:
        day: Days from today (0 = today, 1 = tomorrow, ...)
        hour: UTC hour (6, 9, 12, 15, 18)
        mode: Consensus mode — "worst" (most restrictive) or "majority" (most common)
    """
    from weatherbrief.tasks.map_queries import get_forecast_map_data

    if hour not in _SAMPLE_HOURS:
        hour = min(_SAMPLE_HOURS, key=lambda h: abs(h - hour))

    now = datetime.now(timezone.utc)
    target_date = (now + timedelta(days=day)).date()
    forecast_hour = datetime(
        target_date.year, target_date.month, target_date.day,
        hour, 0, 0, tzinfo=timezone.utc,
    )

    return get_forecast_map_data(db, forecast_hour, airports_db, consensus_mode=mode)


@admin_router.get("/verification")
def get_verification_map(
    period: str = Query(default="7d", pattern=r"^(7d|30d)$"),
    model: str = Query(default="all"),
    days_out: int = Query(default=0, ge=0, le=3),
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
    airports_db: str = Depends(_airports_db),
):
    """Return per-airport verification accuracy stats for the map."""
    from weatherbrief.tasks.map_queries import get_verification_map_data

    now = datetime.now(timezone.utc)
    hours = {"7d": 168, "30d": 720}[period]
    since = now - timedelta(hours=hours)

    return get_verification_map_data(db, since, now, model, days_out, airports_db)


@router.get("/forecast/hours")
def get_available_hours(
    day: int = Query(default=0, ge=0, le=3),
    _user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Return which sample hours have forecast data for a given day.

    Helps the frontend know which hour buttons to enable.
    """
    from sqlalchemy import func, select

    from weatherbrief.db.models import AirportForecastSnapshotRow

    now = datetime.now(timezone.utc)
    target_date = (now + timedelta(days=day)).date()

    # Check which hours have at least one snapshot
    available = []
    for h in _SAMPLE_HOURS:
        fh = datetime(
            target_date.year, target_date.month, target_date.day,
            h, 0, 0, tzinfo=timezone.utc,
        )
        count = db.execute(
            select(func.count())
            .select_from(AirportForecastSnapshotRow)
            .where(AirportForecastSnapshotRow.forecast_hour == fh)
            .limit(1)
        ).scalar()
        if count and count > 0:
            available.append(h)

    return {"day": day, "date": target_date.isoformat(), "hours": available}
