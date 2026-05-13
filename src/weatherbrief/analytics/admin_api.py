"""Admin-facing read endpoints for the usage-analytics dashboard.

All endpoints read from the rollup tables only — never from raw
``analytics_events`` — so the dashboard is cheap even when the events
table has grown large.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from flyfun_common.db import get_db
from weatherbrief.analytics.digest import build_digest
from weatherbrief.analytics.events import Event
from weatherbrief.analytics.models import (
    AnalyticsBriefingDimRow,
    AnalyticsBriefingFeatureDailyRow,
    AnalyticsEventDailyRow,
    AnalyticsFlightDimRow,
    AnalyticsSessionRow,
)
from weatherbrief.api.admin import require_admin

router = APIRouter(prefix="/admin/usage", tags=["analytics-admin"])

_DEFAULT_WINDOW_DAYS = 30
_MAX_WINDOW_DAYS = 365


def _window(days: int) -> tuple[date, date, datetime, datetime]:
    days = max(1, min(days, _MAX_WINDOW_DAYS))
    end_day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    start_day = end_day - timedelta(days=days - 1)
    start_dt = datetime(start_day.year, start_day.month, start_day.day, tzinfo=timezone.utc)
    end_dt = datetime(end_day.year, end_day.month, end_day.day, tzinfo=timezone.utc) + timedelta(days=1)
    return start_day, end_day, start_dt, end_dt


@router.get("/summary")
def usage_summary(
    _admin_id: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    days: int = Query(_DEFAULT_WINDOW_DAYS, ge=1, le=_MAX_WINDOW_DAYS),
) -> dict:
    """High-level totals + feature attachment rates over the window."""
    start_day, end_day, start_dt, end_dt = _window(days)

    n_unique = db.scalar(
        select(func.count(distinct(AnalyticsSessionRow.anon_id)))
        .where(AnalyticsSessionRow.started_at >= start_dt)
        .where(AnalyticsSessionRow.started_at < end_dt)
    ) or 0
    n_new = db.scalar(
        select(func.count(distinct(AnalyticsSessionRow.anon_id)))
        .where(AnalyticsSessionRow.started_at >= start_dt)
        .where(AnalyticsSessionRow.started_at < end_dt)
        .where(AnalyticsSessionRow.is_first_session.is_(True))
    ) or 0
    n_briefings_opened = db.scalar(
        select(func.coalesce(func.sum(AnalyticsEventDailyRow.total_count), 0))
        .where(AnalyticsEventDailyRow.event == Event.BRIEFING_OPENED.value)
        .where(AnalyticsEventDailyRow.day >= start_day)
        .where(AnalyticsEventDailyRow.day <= end_day)
    ) or 0
    n_briefings_created = db.scalar(
        select(func.count(AnalyticsBriefingDimRow.briefing_id))
        .where(AnalyticsBriefingDimRow.created_at >= start_dt)
        .where(AnalyticsBriefingDimRow.created_at < end_dt)
    ) or 0
    n_refreshes = db.scalar(
        select(func.count(AnalyticsBriefingDimRow.briefing_id))
        .where(AnalyticsBriefingDimRow.created_at >= start_dt)
        .where(AnalyticsBriefingDimRow.created_at < end_dt)
        .where(AnalyticsBriefingDimRow.is_refresh.is_(True))
    ) or 0

    feature_rows = db.execute(
        select(
            AnalyticsBriefingFeatureDailyRow.feature,
            func.coalesce(func.sum(AnalyticsBriefingFeatureDailyRow.briefings_with_feature), 0),
            func.coalesce(func.sum(AnalyticsBriefingFeatureDailyRow.briefings_total), 0),
            func.coalesce(func.sum(AnalyticsBriefingFeatureDailyRow.total_uses), 0),
        )
        .where(AnalyticsBriefingFeatureDailyRow.day >= start_day)
        .where(AnalyticsBriefingFeatureDailyRow.day <= end_day)
        .group_by(AnalyticsBriefingFeatureDailyRow.feature)
        .order_by(AnalyticsBriefingFeatureDailyRow.feature)
    ).all()
    features = [
        {
            "feature": feature,
            "briefings_with_feature": int(with_feat),
            "briefings_total": int(total),
            "total_uses": int(uses),
            "attachment_pct": (
                round(int(with_feat) * 100 / int(total), 1) if total else 0.0
            ),
        }
        for feature, with_feat, total, uses in feature_rows
    ]

    return {
        "window": {
            "start": start_day.isoformat(),
            "end": end_day.isoformat(),
            "days": days,
        },
        "totals": {
            "unique_anons": int(n_unique),
            "new_anons": int(n_new),
            "briefings_opened": int(n_briefings_opened),
            "briefings_created": int(n_briefings_created),
            "briefings_refreshes": int(n_refreshes),
        },
        "features": features,
    }


@router.get("/timeseries")
def usage_timeseries(
    _admin_id: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    days: int = Query(_DEFAULT_WINDOW_DAYS, ge=1, le=_MAX_WINDOW_DAYS),
) -> dict:
    """Per-day series of briefings opened + new users for charts."""
    start_day, end_day, _start_dt, _end_dt = _window(days)

    briefings_by_day = {
        d.isoformat(): int(c)
        for d, c in db.execute(
            select(AnalyticsEventDailyRow.day, AnalyticsEventDailyRow.total_count)
            .where(AnalyticsEventDailyRow.event == Event.BRIEFING_OPENED.value)
            .where(AnalyticsEventDailyRow.day >= start_day)
            .where(AnalyticsEventDailyRow.day <= end_day)
        )
    }
    new_users_by_day = {
        d.isoformat(): int(c)
        for d, c in db.execute(
            select(AnalyticsEventDailyRow.day, AnalyticsEventDailyRow.unique_new_anons)
            .where(AnalyticsEventDailyRow.event == Event.SESSION_STARTED.value)
            .where(AnalyticsEventDailyRow.day >= start_day)
            .where(AnalyticsEventDailyRow.day <= end_day)
        )
    }
    series = []
    cursor = start_day
    while cursor <= end_day:
        key = cursor.isoformat()
        series.append({
            "day": key,
            "briefings_opened": briefings_by_day.get(key, 0),
            "new_users": new_users_by_day.get(key, 0),
        })
        cursor += timedelta(days=1)
    return {"series": series}


@router.get("/briefing-shape")
def briefing_shape(
    _admin_id: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    days: int = Query(_DEFAULT_WINDOW_DAYS, ge=1, le=_MAX_WINDOW_DAYS),
) -> dict:
    """Distributions over flight + briefing dimensions in the window."""
    _start_day, _end_day, start_dt, end_dt = _window(days)

    def _count_by(column):
        return [
            {"key": (str(k) if k is not None else None), "count": int(c)}
            for k, c in db.execute(
                select(column, func.count())
                .select_from(AnalyticsBriefingDimRow)
                .join(
                    AnalyticsFlightDimRow,
                    AnalyticsFlightDimRow.flight_id == AnalyticsBriefingDimRow.flight_id,
                    isouter=True,
                )
                .where(AnalyticsBriefingDimRow.created_at >= start_dt)
                .where(AnalyticsBriefingDimRow.created_at < end_dt)
                .group_by(column)
            )
        ]

    # Bucket raw route_points server-side rather than in the dim row, so the
    # boundaries can evolve without re-enriching history. Boundaries are
    # tuned for GA: nearly every flight is 2 (point-to-point), 3-5 (a couple
    # of fixes), or 6+ (pasted IFR FPLs). Three buckets is plenty.
    route_points_bucket = case(
        (AnalyticsFlightDimRow.route_points.is_(None), None),
        (AnalyticsFlightDimRow.route_points <= 2, "2"),
        (AnalyticsFlightDimRow.route_points <= 5, "3-5"),
        else_="6+",
    )
    rp_order = {"2": 0, "3-5": 1, "6+": 2}
    rp_buckets = sorted(
        _count_by(route_points_bucket),
        key=lambda b: rp_order.get(b["key"], 99),
    )

    return {
        "by_region": _count_by(AnalyticsFlightDimRow.region),
        "by_distance": _count_by(AnalyticsFlightDimRow.distance_bucket),
        "by_route_points": rp_buckets,
        "by_lead_time": _count_by(AnalyticsBriefingDimRow.lead_time_bucket),
        "by_model_count": _count_by(AnalyticsBriefingDimRow.model_count),
        "by_alternate_etd": _count_by(AnalyticsFlightDimRow.has_alternate_etd),
        "by_seq": _count_by(AnalyticsBriefingDimRow.briefing_seq),
    }


@router.get("/digest")
def usage_digest(
    _admin_id: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Plain-text weekly digest (same string the cron logs)."""
    return {"text": build_digest(db)}
