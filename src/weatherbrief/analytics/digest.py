"""Weekly usage-analytics digest.

Log-only for v1. Produces a short summary of the last 7 UTC days from the
rollup tables and writes it to the application log so it shows up in the
admin's normal log stream. Email/Slack/etc. can be added later by passing
``send_fn`` to :func:`build_and_emit_digest`.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from weatherbrief.analytics.events import Event
from weatherbrief.analytics.models import (
    AnalyticsBriefingDimRow,
    AnalyticsBriefingFeatureDailyRow,
    AnalyticsEventDailyRow,
    AnalyticsSessionRow,
)

logger = logging.getLogger(__name__)


def build_digest(db: Session, end_day: date | None = None) -> str:
    """Return a human-readable 7-day summary ending on ``end_day`` (UTC).

    Default ``end_day`` is yesterday so we never include the current
    (partial) day.
    """
    if end_day is None:
        end_day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    start_day = end_day - timedelta(days=6)  # 7 days inclusive
    start_dt = datetime(start_day.year, start_day.month, start_day.day, tzinfo=timezone.utc)
    end_dt = datetime(end_day.year, end_day.month, end_day.day, tzinfo=timezone.utc) + timedelta(days=1)

    # Sessions / unique users (from session table — survives event retention).
    n_sessions = db.scalar(
        select(func.count(AnalyticsSessionRow.session_id))
        .where(AnalyticsSessionRow.started_at >= start_dt)
        .where(AnalyticsSessionRow.started_at < end_dt)
    ) or 0
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

    # Briefings opened. Read from event_daily — survives raw retention.
    n_briefings_opened = db.scalar(
        select(func.coalesce(func.sum(AnalyticsEventDailyRow.total_count), 0))
        .where(AnalyticsEventDailyRow.event == Event.BRIEFING_OPENED.value)
        .where(AnalyticsEventDailyRow.day >= start_day)
        .where(AnalyticsEventDailyRow.day <= end_day)
    ) or 0

    # Briefings created in this window (from dim table).
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

    # Feature attachment rates over briefings opened in window.
    feature_rows = db.execute(
        select(
            AnalyticsBriefingFeatureDailyRow.feature,
            func.coalesce(func.sum(AnalyticsBriefingFeatureDailyRow.briefings_with_feature), 0),
            func.coalesce(func.sum(AnalyticsBriefingFeatureDailyRow.briefings_total), 0),
        )
        .where(AnalyticsBriefingFeatureDailyRow.day >= start_day)
        .where(AnalyticsBriefingFeatureDailyRow.day <= end_day)
        .group_by(AnalyticsBriefingFeatureDailyRow.feature)
        .order_by(AnalyticsBriefingFeatureDailyRow.feature)
    ).all()

    feature_lines: list[str] = []
    for feature, with_feat, total in feature_rows:
        pct = (int(with_feat) * 100 // int(total)) if total else 0
        feature_lines.append(f"  {feature}: {pct}% ({with_feat}/{total})")

    lines = [
        f"Usage digest {start_day} → {end_day} (UTC)",
        f"  sessions: {n_sessions} (unique users: {n_unique}, new: {n_new})",
        f"  briefings opened: {n_briefings_opened}, "
        f"created: {n_briefings_created} ({n_refreshes} were refreshes)",
        "  feature attachment per briefing-opened:",
        *feature_lines,
    ]
    return "\n".join(lines)


def build_and_emit_digest(
    db: Session,
    send_fn: Callable[[str], None] | None = None,
) -> str:
    """Build the digest, log it, and optionally hand off to ``send_fn``."""
    text = build_digest(db)
    logger.info("%s", text)
    if send_fn is not None:
        try:
            send_fn(text)
        except Exception:
            logger.warning("analytics digest: send_fn failed", exc_info=True)
    return text
