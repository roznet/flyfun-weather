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

from sqlalchemy import case, delete, distinct, func, select
from sqlalchemy.orm import Session

from weatherbrief.analytics.events import FEATURE_OF, Event
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

    All large sets (new anons, today's briefings) stay inside the DB as
    scalar subqueries — never materialised into Python and re-injected as
    ``IN (...)`` clauses, which would bust ``max_allowed_packet`` once the
    table grows.
    """
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    # ---- event_daily -----------------------------------------------------
    db.execute(
        delete(AnalyticsEventDailyRow).where(AnalyticsEventDailyRow.day == day)
    )

    # New anons: those whose first ever session started in this day. We
    # pass the Select directly to ``.in_()`` so the set lives entirely in
    # the DB and never lands in Python (would bust MySQL's
    # max_allowed_packet at scale).
    new_anons_subq = (
        select(AnalyticsSessionRow.anon_id)
        .where(AnalyticsSessionRow.is_first_session.is_(True))
        .where(AnalyticsSessionRow.started_at >= day_start)
        .where(AnalyticsSessionRow.started_at < day_end)
    )

    # Single GROUP BY query — ``unique_new_anons`` is COUNT(DISTINCT) over a
    # CASE that returns the anon_id only when it's in today's new-anons set,
    # NULL otherwise. NULLs don't count in DISTINCT, so we get the right
    # numerator without a per-event correlated subquery and without
    # materialising the set.
    new_anon_expr = case(
        (AnalyticsEventRow.anon_id.in_(new_anons_subq), AnalyticsEventRow.anon_id),
        else_=None,
    )
    per_event_rows = db.execute(
        select(
            AnalyticsEventRow.event,
            func.count(AnalyticsEventRow.id),
            func.count(distinct(AnalyticsEventRow.anon_id)),
            func.count(distinct(new_anon_expr)),
        )
        .where(AnalyticsEventRow.ts >= day_start)
        .where(AnalyticsEventRow.ts < day_end)
        .group_by(AnalyticsEventRow.event)
    ).all()

    # ``session_started`` fires once per page load (each navigation
    # reinitialises the client module), so its raw event count overstates
    # actual session starts. Override total_count with the real session
    # count from the sessions table — which deduplicates by session_id.
    # The unique_anons / unique_new_anons columns are still accurate
    # because they DISTINCT on anon_id.
    sessions_started_today = int(db.scalar(
        select(func.count(AnalyticsSessionRow.session_id))
        .where(AnalyticsSessionRow.started_at >= day_start)
        .where(AnalyticsSessionRow.started_at < day_end)
    ) or 0)

    n_events = 0
    for event_name, total, unique_anons, unique_new in per_event_rows:
        true_total = (
            sessions_started_today
            if event_name == Event.SESSION_STARTED.value
            else int(total)
        )
        db.add(
            AnalyticsEventDailyRow(
                day=day,
                event=event_name,
                total_count=true_total,
                unique_anons=int(unique_anons),
                unique_new_anons=int(unique_new or 0),
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
    # Reused via ``.in_()`` in the feature joins below.
    briefings_today_subq = (
        select(AnalyticsEventRow.briefing_id)
        .distinct()
        .where(AnalyticsEventRow.event == Event.BRIEFING_OPENED.value)
        .where(AnalyticsEventRow.ts >= day_start)
        .where(AnalyticsEventRow.ts < day_end)
        .where(AnalyticsEventRow.briefing_id.is_not(None))
    )
    briefings_total = int(db.scalar(
        select(func.count(distinct(AnalyticsEventRow.briefing_id)))
        .where(AnalyticsEventRow.event == Event.BRIEFING_OPENED.value)
        .where(AnalyticsEventRow.ts >= day_start)
        .where(AnalyticsEventRow.ts < day_end)
        .where(AnalyticsEventRow.briefing_id.is_not(None))
    ) or 0)
    n_features = 0

    # Group raw events by their feature label so each feature row in the
    # rollup is the union of all events that signal "this feature was used".
    feature_events: dict[str, list[str]] = {}
    for event_name, feature in FEATURE_OF.items():
        feature_events.setdefault(feature, []).append(event_name)

    for feature, event_names in feature_events.items():
        briefings_with = int(db.scalar(
            select(func.count(distinct(AnalyticsEventRow.briefing_id)))
            .where(AnalyticsEventRow.event.in_(event_names))
            .where(AnalyticsEventRow.ts >= day_start)
            .where(AnalyticsEventRow.ts < day_end)
            .where(AnalyticsEventRow.briefing_id.in_(briefings_today_subq))
        ) or 0)
        total_uses = int(db.scalar(
            select(func.count(AnalyticsEventRow.id))
            .where(AnalyticsEventRow.event.in_(event_names))
            .where(AnalyticsEventRow.ts >= day_start)
            .where(AnalyticsEventRow.ts < day_end)
            .where(AnalyticsEventRow.briefing_id.in_(briefings_today_subq))
        ) or 0)
        db.add(
            AnalyticsBriefingFeatureDailyRow(
                day=day,
                feature=feature,
                briefings_total=briefings_total,
                briefings_with_feature=briefings_with,
                total_uses=total_uses,
            )
        )
        n_features += 1

    # ``detailed_mode`` is a *derived* feature — there's no single event
    # whose presence means "user opted into detailed mode". Instead we
    # interpret display_mode.changed by looking at the **last** mode set
    # in each briefing and counting briefings whose final mode is
    # ``detailed``. This needs the event stream + props, so it can't
    # collapse into the simple FEATURE_OF loop above.
    detailed_with, detailed_total = _extract_detailed_mode(
        db, briefings_today_subq, day_start, day_end,
    )
    db.add(
        AnalyticsBriefingFeatureDailyRow(
            day=day,
            feature="detailed_mode",
            briefings_total=briefings_total,
            briefings_with_feature=detailed_with,
            total_uses=detailed_total,
        )
    )
    n_features += 1

    return {"events": n_events, "features": n_features, "briefings": briefings_total}


def _extract_detailed_mode(
    db: Session,
    briefings_today_subq,
    day_start: datetime,
    day_end: datetime,
) -> tuple[int, int]:
    """Derive ``detailed_mode`` feature counts from display_mode.changed.

    A briefing counts as "detailed" if its **last** ``display_mode.changed``
    event of the day ended with ``to: detailed``. Briefings whose final
    transition was back to ``compact`` (or who never switched) don't count.

    ``total_uses`` only counts transitions *to* detailed mode, matching
    the "feature activated" semantic used by all other features. Counting
    every transition (including detailed → compact) would inflate the
    number for users who toggled back and forth.

    Returns ``(briefings_with_feature, total_uses)``.
    """
    rows = db.execute(
        select(AnalyticsEventRow.briefing_id, AnalyticsEventRow.props)
        .where(AnalyticsEventRow.event == Event.DISPLAY_MODE_CHANGED.value)
        .where(AnalyticsEventRow.ts >= day_start)
        .where(AnalyticsEventRow.ts < day_end)
        .where(AnalyticsEventRow.briefing_id.in_(briefings_today_subq))
        .order_by(AnalyticsEventRow.briefing_id, AnalyticsEventRow.ts)
    ).all()

    latest_mode: dict[int, str] = {}
    to_detailed_count = 0
    for bid, props_json in rows:
        if bid is None:
            continue
        try:
            props = json.loads(props_json or "{}")
        except Exception:
            continue
        to_mode = str(props.get("to") or "").lower()
        if to_mode in ("compact", "detailed"):
            latest_mode[bid] = to_mode
            if to_mode == "detailed":
                to_detailed_count += 1

    detailed_briefings = sum(1 for m in latest_mode.values() if m == "detailed")
    return detailed_briefings, to_detailed_count


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
