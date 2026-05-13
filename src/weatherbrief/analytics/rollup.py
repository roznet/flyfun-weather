"""Nightly rollup + retention for the usage-analytics tables.

Two responsibilities:

1. **Roll up** the previous UTC day's raw ``analytics_events`` into
   ``analytics_event_daily`` (per-event totals) and
   ``analytics_briefing_feature_daily`` (per-briefing feature attachment
   rates). Both rollup tables are kept forever — they preserve long-term
   trends even after raw events are purged.
2. **Retain** raw events for ``ANALYTICS_RAW_RETENTION_DAYS`` (default
   60). Older rows are deleted from ``analytics_events``. Session,
   flight-dim, and briefing-dim rows are kept indefinitely.

Idempotent on the day boundary: ``DELETE+INSERT`` for the day being
rolled up, so re-running for the same day produces the same result.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, delete, distinct, func, select
from sqlalchemy.orm import Session

from weatherbrief.analytics.events import FEATURE_OF, KNOWN_FEATURES, Event
from weatherbrief.analytics.models import (
    AnalyticsBriefingFeatureDailyRow,
    AnalyticsEventDailyRow,
    AnalyticsEventRow,
    AnalyticsSessionRow,
)

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 60


def _retention_days() -> int:
    try:
        return int(os.environ.get("ANALYTICS_RAW_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


# ---------------------------------------------------------------------------
# Per-day aggregation
# ---------------------------------------------------------------------------


def rollup_day(db: Session, day: date) -> dict[str, int]:
    """Aggregate one UTC day. Idempotent (DELETE+INSERT on the day).

    Returns counts for logging.
    """
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    # ---- event_daily -----------------------------------------------------
    db.execute(
        delete(AnalyticsEventDailyRow).where(AnalyticsEventDailyRow.day == day)
    )

    # New anons: those whose first ever session started in this day. We do
    # this once and subtract per event so we don't re-scan sessions per row.
    new_anons_this_day = {
        anon
        for (anon,) in db.execute(
            select(AnalyticsSessionRow.anon_id)
            .where(AnalyticsSessionRow.is_first_session.is_(True))
            .where(AnalyticsSessionRow.started_at >= day_start)
            .where(AnalyticsSessionRow.started_at < day_end)
        )
    }

    per_event_rows = db.execute(
        select(
            AnalyticsEventRow.event,
            func.count(AnalyticsEventRow.id),
            func.count(distinct(AnalyticsEventRow.anon_id)),
        )
        .where(AnalyticsEventRow.ts >= day_start)
        .where(AnalyticsEventRow.ts < day_end)
        .group_by(AnalyticsEventRow.event)
    ).all()

    n_events = 0
    for event_name, total, unique_anons in per_event_rows:
        if not new_anons_this_day:
            unique_new = 0
        else:
            unique_new = db.scalar(
                select(func.count(distinct(AnalyticsEventRow.anon_id)))
                .where(AnalyticsEventRow.ts >= day_start)
                .where(AnalyticsEventRow.ts < day_end)
                .where(AnalyticsEventRow.event == event_name)
                .where(AnalyticsEventRow.anon_id.in_(new_anons_this_day))
            ) or 0
        db.add(
            AnalyticsEventDailyRow(
                day=day,
                event=event_name,
                total_count=int(total),
                unique_anons=int(unique_anons),
                unique_new_anons=int(unique_new),
            )
        )
        n_events += 1

    # ---- briefing_feature_daily ------------------------------------------
    db.execute(
        delete(AnalyticsBriefingFeatureDailyRow).where(
            AnalyticsBriefingFeatureDailyRow.day == day,
        )
    )

    # Denominator: distinct briefings opened (i.e., briefing.opened event)
    # in this UTC day. We use the event timestamp, not the briefing's own
    # created_at, because "opened today" is the user-engagement signal.
    briefings_today = {
        bid
        for (bid,) in db.execute(
            select(distinct(AnalyticsEventRow.briefing_id))
            .where(AnalyticsEventRow.event == Event.BRIEFING_OPENED.value)
            .where(AnalyticsEventRow.ts >= day_start)
            .where(AnalyticsEventRow.ts < day_end)
            .where(AnalyticsEventRow.briefing_id.is_not(None))
        )
    }
    briefings_total = len(briefings_today)
    n_features = 0

    # For each known feature, count distinct briefings within today's set
    # that saw at least one event mapped to that feature.
    feature_events: dict[str, list[str]] = {}
    for event_name, feature in FEATURE_OF.items():
        feature_events.setdefault(feature, []).append(event_name)

    for feature, event_names in feature_events.items():
        if not briefings_today:
            briefings_with = 0
            total_uses = 0
        else:
            briefings_with = db.scalar(
                select(func.count(distinct(AnalyticsEventRow.briefing_id)))
                .where(AnalyticsEventRow.event.in_(event_names))
                .where(AnalyticsEventRow.ts >= day_start)
                .where(AnalyticsEventRow.ts < day_end)
                .where(AnalyticsEventRow.briefing_id.in_(briefings_today))
            ) or 0
            total_uses = db.scalar(
                select(func.count(AnalyticsEventRow.id))
                .where(AnalyticsEventRow.event.in_(event_names))
                .where(AnalyticsEventRow.ts >= day_start)
                .where(AnalyticsEventRow.ts < day_end)
                .where(AnalyticsEventRow.briefing_id.in_(briefings_today))
            ) or 0

        db.add(
            AnalyticsBriefingFeatureDailyRow(
                day=day,
                feature=feature,
                briefings_total=briefings_total,
                briefings_with_feature=int(briefings_with),
                total_uses=int(total_uses),
            )
        )
        n_features += 1

    # Synthesise the ``detailed_mode`` feature from display_mode.changed
    # events: a briefing counts as "detailed" if its last mode change in
    # that briefing ends with ``to: detailed``. Briefings with no mode
    # change inherit the default (compact) — they're not counted.
    detailed_briefings: set[int] = set()
    total_detailed_changes = 0
    if briefings_today:
        rows = db.execute(
            select(AnalyticsEventRow.briefing_id, AnalyticsEventRow.props, AnalyticsEventRow.ts)
            .where(AnalyticsEventRow.event == Event.DISPLAY_MODE_CHANGED.value)
            .where(AnalyticsEventRow.ts >= day_start)
            .where(AnalyticsEventRow.ts < day_end)
            .where(AnalyticsEventRow.briefing_id.in_(briefings_today))
            .order_by(AnalyticsEventRow.briefing_id, AnalyticsEventRow.ts)
        ).all()
        latest_mode: dict[int, str] = {}
        for bid, props_json, _ts in rows:
            if bid is None:
                continue
            total_detailed_changes += 1
            try:
                props = json.loads(props_json or "{}")
                to_mode = str(props.get("to") or "").lower()
                if to_mode in ("compact", "detailed"):
                    latest_mode[bid] = to_mode
            except Exception:
                continue
        detailed_briefings = {b for b, m in latest_mode.items() if m == "detailed"}

    db.add(
        AnalyticsBriefingFeatureDailyRow(
            day=day,
            feature="detailed_mode",
            briefings_total=briefings_total,
            briefings_with_feature=len(detailed_briefings),
            total_uses=total_detailed_changes,
        )
    )
    n_features += 1

    return {"events": n_events, "features": n_features, "briefings": briefings_total}


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def purge_old_events(db: Session, retention_days: int | None = None) -> int:
    """Delete raw events older than the retention window.

    Returns the number of rows deleted.
    """
    days = retention_days if retention_days is not None else _retention_days()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = db.execute(
        delete(AnalyticsEventRow).where(AnalyticsEventRow.ts < cutoff)
    )
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# Public entrypoint used by the scheduler loop
# ---------------------------------------------------------------------------


def run_rollup_and_retention(db: Session) -> dict:
    """Roll up yesterday + purge old raw events.

    Called once a day from the scheduler loop. Always rolls up the previous
    UTC day; idempotent so it's safe to retry.
    """
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    counts = rollup_day(db, yesterday)
    purged = purge_old_events(db)
    logger.info(
        "analytics rollup: day=%s events=%d features=%d briefings=%d purged=%d",
        yesterday, counts["events"], counts["features"], counts["briefings"], purged,
    )
    return {"day": yesterday.isoformat(), "purged": purged, **counts}
