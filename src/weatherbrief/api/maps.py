"""Weather overview map endpoints.

All endpoints require authentication (current_user_id).

  GET /maps/forecast       — forecast overview for all watchlist airports
  GET /maps/verification   — per-airport verification accuracy stats
  GET /maps/forecast/hours — available sample hours for a given day
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/maps", tags=["maps"])

from weatherbrief.tasks.standalone_verification import SAMPLE_HOURS_UTC as _SAMPLE_HOURS


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


@router.get("/verification")
def get_verification_map(
    period: str = Query(default="7d", pattern=r"^(7d|30d)$"),
    model: str = Query(default="all"),
    days_out: int = Query(default=0, ge=0, le=3),
    _user_id: str = Depends(current_user_id),
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

    # Single query: distinct forecast hours that exist for this date
    hour_rows = db.execute(
        select(func.extract("hour", AirportForecastSnapshotRow.forecast_hour))
        .where(AirportForecastSnapshotRow.forecast_hour >= datetime(
            target_date.year, target_date.month, target_date.day,
            0, 0, 0, tzinfo=timezone.utc,
        ))
        .where(AirportForecastSnapshotRow.forecast_hour < datetime(
            target_date.year, target_date.month, target_date.day,
            23, 59, 59, tzinfo=timezone.utc,
        ))
        .distinct()
    ).scalars().all()
    available = sorted(int(h) for h in hour_rows if int(h) in _SAMPLE_HOURS)

    return {"day": day, "date": target_date.isoformat(), "hours": available}
