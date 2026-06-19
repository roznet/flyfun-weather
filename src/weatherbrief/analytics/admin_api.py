"""Admin-facing read endpoints for the usage-analytics dashboard.

Endpoints read from the rollup tables and dimension snapshots; the raw
``analytics_events`` table is never queried here, so the dashboard stays
cheap even when the events table has grown large.
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
    AnalyticsXsectionConfigDailyRow,
)
from weatherbrief.api.admin import require_admin

router = APIRouter(prefix="/admin/usage", tags=["analytics-admin"])

_DEFAULT_WINDOW_DAYS = 30
_MAX_WINDOW_DAYS = 365


# Explicit display order for each briefing-shape dimension. These buckets are
# categorical/ordinal strings that don't sort naturally — distance and lead
# time have a logical progression, not an alphabetical one. ``None``/unknown
# always sorts last (handled in ``_sort_buckets``).
_BUCKET_ORDER: dict[str, list[str]] = {
    "by_region": ["EU", "US", "OTHER"],
    "by_distance": ["short", "medium", "long"],
    "by_route_points": ["2", "3-5", "6+"],
    "by_lead_time": [
        "post_departure", "same_day", "1d", "2_3d", "4_7d", "7d_plus", "no_etd",
    ],
    "by_alternate_etd": ["true", "false"],
}
# Dimensions whose keys are plain integers ("1", "2", ... "10") — string sort
# would order "10" before "2", so sort numerically with unknown last.
_NUMERIC_DIMS: frozenset[str] = frozenset({"by_model_count", "by_seq"})


def _sort_buckets(dim: str, buckets: list[dict]) -> list[dict]:
    """Sort one dimension's buckets into a stable, human-sensible order.

    Numeric dims sort ascending by integer value; ordinal dims follow their
    explicit ``_BUCKET_ORDER`` list. Unknown/``None`` keys go last in both
    cases so they never interrupt the meaningful sequence.
    """
    if dim in _NUMERIC_DIMS:
        def num_key(b: dict) -> tuple[int, int]:
            try:
                return (0, int(b["key"]))
            except (TypeError, ValueError):
                return (1, 0)
        return sorted(buckets, key=num_key)

    order = _BUCKET_ORDER.get(dim)
    if order is None:
        return buckets
    index = {key: i for i, key in enumerate(order)}
    last = len(order)
    return sorted(buckets, key=lambda b: index.get(b["key"], last))


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

    # Summing daily rows double-counts briefings the user opened on more
    # than one day — both numerator and denominator grow with the revisit,
    # but the denominator (briefings_total) grows faster on average since
    # not every revisit uses every feature. The bias pulls attachment %
    # slightly *down*. Acceptable trade-off here: the rollup is per-day
    # and the cross-day exact denominator would require querying
    # analytics_briefings_dim, sacrificing the cheap rollup-only read.
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

    # Raw per-event counts from the daily rollup. The feature table above is
    # derived/curated; this is the ground truth for every event the client
    # emits — including standalone (no-briefing) events like
    # ``climatology.opened`` that have nowhere else to show. ``unique_anons``
    # is summed across days, so a user active on multiple days is counted
    # once per day (slight overcount — fine for a sanity-check table).
    event_rows = db.execute(
        select(
            AnalyticsEventDailyRow.event,
            func.coalesce(func.sum(AnalyticsEventDailyRow.total_count), 0),
            func.coalesce(func.sum(AnalyticsEventDailyRow.unique_anons), 0),
        )
        .where(AnalyticsEventDailyRow.day >= start_day)
        .where(AnalyticsEventDailyRow.day <= end_day)
        .group_by(AnalyticsEventDailyRow.event)
    ).all()
    events = sorted(
        (
            {"event": ev, "total_count": int(c), "unique_anons": int(u)}
            for ev, c, u in event_rows
        ),
        key=lambda r: r["total_count"],
        reverse=True,
    )

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
        "events": events,
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
        # ``str(True)`` → "True" / "False" with capital first letter; the
        # frontend's formatKey check expects lowercase. Normalise booleans
        # so the dialect (SQLAlchemy's Boolean processor) doesn't leak into
        # the wire format.
        def _key(k):
            if k is None:
                return None
            if isinstance(k, bool):
                return "true" if k else "false"
            return str(k)

        return [
            {"key": _key(k), "count": int(c)}
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

    raw = {
        "by_region": _count_by(AnalyticsFlightDimRow.region),
        "by_distance": _count_by(AnalyticsFlightDimRow.distance_bucket),
        "by_route_points": _count_by(route_points_bucket),
        "by_lead_time": _count_by(AnalyticsBriefingDimRow.lead_time_bucket),
        "by_model_count": _count_by(AnalyticsBriefingDimRow.model_count),
        "by_alternate_etd": _count_by(AnalyticsFlightDimRow.has_alternate_etd),
        "by_seq": _count_by(AnalyticsBriefingDimRow.briefing_seq),
    }
    return {dim: _sort_buckets(dim, buckets) for dim, buckets in raw.items()}


@router.get("/xsection-config")
def xsection_config(
    _admin_id: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    days: int = Query(_DEFAULT_WINDOW_DAYS, ge=1, le=_MAX_WINDOW_DAYS),
) -> dict:
    """Per-dimension breakdown of the cross-section display config.

    Sums ``analytics_xsection_config_daily`` over the window grouped by
    ``dimension`` → sorted ``[{value, views, unique_anons}]``. ``total_views``
    / ``unique_viewers`` (the denominator for shares and per-layer attachment)
    come from ``analytics_event_daily`` for ``xsection.viewed``.
    """
    start_day, end_day, _start_dt, _end_dt = _window(days)

    # Denominator: total xsection views + unique viewers in the window, from
    # the per-event daily rollup (same source the events table uses, so the
    # numbers reconcile). unique_viewers is summed across days → a user active
    # on multiple days counts once per day (slight overcount — acceptable for
    # a share denominator, matches the feature-table convention).
    total_views = int(db.scalar(
        select(func.coalesce(func.sum(AnalyticsEventDailyRow.total_count), 0))
        .where(AnalyticsEventDailyRow.event == Event.XSECTION_VIEWED.value)
        .where(AnalyticsEventDailyRow.day >= start_day)
        .where(AnalyticsEventDailyRow.day <= end_day)
    ) or 0)
    unique_viewers = int(db.scalar(
        select(func.coalesce(func.sum(AnalyticsEventDailyRow.unique_anons), 0))
        .where(AnalyticsEventDailyRow.event == Event.XSECTION_VIEWED.value)
        .where(AnalyticsEventDailyRow.day >= start_day)
        .where(AnalyticsEventDailyRow.day <= end_day)
    ) or 0)

    rows = db.execute(
        select(
            AnalyticsXsectionConfigDailyRow.dimension,
            AnalyticsXsectionConfigDailyRow.value,
            func.coalesce(func.sum(AnalyticsXsectionConfigDailyRow.views), 0),
            func.coalesce(func.sum(AnalyticsXsectionConfigDailyRow.unique_anons), 0),
        )
        .where(AnalyticsXsectionConfigDailyRow.day >= start_day)
        .where(AnalyticsXsectionConfigDailyRow.day <= end_day)
        .group_by(
            AnalyticsXsectionConfigDailyRow.dimension,
            AnalyticsXsectionConfigDailyRow.value,
        )
    ).all()

    dimensions: dict[str, list[dict]] = {}
    for dimension, value, views, uniq in rows:
        dimensions.setdefault(dimension, []).append(
            {"value": value, "views": int(views), "unique_anons": int(uniq)}
        )
    # Within each dimension, biggest bucket first (matches the "what's most
    # used" reading); the layer dimension's attachment % then sorts desc too.
    for buckets in dimensions.values():
        buckets.sort(key=lambda b: b["views"], reverse=True)

    return {
        "window": {
            "start": start_day.isoformat(),
            "end": end_day.isoformat(),
            "days": days,
        },
        "total_views": total_views,
        "unique_viewers": unique_viewers,
        "dimensions": dimensions,
    }


@router.get("/digest")
def usage_digest(
    _admin_id: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Plain-text weekly digest (same string the cron logs)."""
    return {"text": build_digest(db)}
