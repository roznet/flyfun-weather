"""Background auto-refresh scheduler.

Polls every 10 minutes for flights with auto_refresh enabled that are due
for a refresh. Runs the briefing pipeline and optionally sends an email.

Scheduling formula
------------------
    next_due = min(next_regular, flight_start − PREFLIGHT_LEAD_HOURS)

where *next_regular* is the next occurrence of the user's preferred hour
after the last refresh.  All comparisons use absolute UTC datetimes, so
there is no hour wrap-around or calendar-day ambiguity.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from weatherbrief.db.engine import SessionLocal
from weatherbrief.db.models import FlightRow, UserRow

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 600  # 10 minutes
_STARTUP_DELAY_SECONDS = 30
_PREFLIGHT_LEAD_HOURS = 2


async def run_scheduler_loop(app_state) -> None:
    """Main scheduler loop — started as an asyncio task from app lifespan."""
    logger.info("Auto-refresh scheduler started (poll every %ds)", _POLL_INTERVAL_SECONDS)
    await asyncio.sleep(_STARTUP_DELAY_SECONDS)

    while True:
        try:
            await process_auto_refreshes(app_state)
        except Exception:
            logger.error("Auto-refresh cycle failed", exc_info=True)
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def process_auto_refreshes(app_state) -> None:
    """Find due flights and refresh them."""
    db = SessionLocal()
    try:
        due = _find_due_flights(db)
        if not due:
            return
        logger.info("Auto-refresh: %d flight(s) due", len(due))

        for row in due:
            from weatherbrief.api.packs import refresh_registry

            entry = refresh_registry.try_register(row.id, triggered_by="scheduler")
            if entry is None:
                logger.info("Auto-refresh: skipping %s (refresh already in progress)", row.id)
                continue

            try:
                refresh_registry.set_refreshing(row.id)
                await asyncio.to_thread(
                    _auto_refresh_one, row, app_state, row.user_id
                )
                # Record the refresh timestamp
                mark_db = SessionLocal()
                try:
                    mark_row = mark_db.get(FlightRow, row.id)
                    if mark_row:
                        mark_row.last_auto_refresh_at = datetime.now(timezone.utc)
                        mark_db.commit()
                finally:
                    mark_db.close()
                logger.info("Auto-refresh completed: %s", row.id)
            except Exception:
                logger.error("Auto-refresh failed for %s", row.id, exc_info=True)
            finally:
                refresh_registry.unregister(row.id)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scheduling helpers
# ---------------------------------------------------------------------------


def _find_due_flights(db: Session) -> list[FlightRow]:
    """Return flights that are due for auto-refresh."""
    now_utc = datetime.now(timezone.utc)

    stmt = (
        select(FlightRow)
        .where(FlightRow.auto_refresh.is_(True))
        .where(FlightRow.departure_time >= now_utc)
    )
    rows = db.execute(stmt).scalars().all()

    due: list[FlightRow] = []
    for row in rows:
        flight_start = _flight_start_dt(row)
        if flight_start is None:
            continue

        # Flight already started — no auto-refresh (manual still works)
        if now_utc >= flight_start:
            continue

        due_at = _next_due_at(row, flight_start, now_utc)
        if due_at is not None and now_utc >= due_at:
            due.append(row)

    return due


def _next_due_at(
    row: FlightRow,
    flight_start_dt: datetime,
    now_utc: datetime,
) -> datetime | None:
    """Absolute UTC datetime when the next auto-refresh is due.

    Formula: ``min(next_regular, flight_start − PREFLIGHT_LEAD_HOURS)``

    *next_regular* is the next occurrence of the user's preferred hour
    (``auto_refresh_hour``, defaulting to ``target_time_utc − 1``).

    Always returns a time strictly before ``flight_start_dt`` (since
    *preflight* is ``flight_start − PREFLIGHT_LEAD_HOURS``).
    """
    effective_hour = row.auto_refresh_hour
    if effective_hour is None:
        effective_hour = (row.departure_time.hour - 1) % 24

    preflight = flight_start_dt - timedelta(hours=_PREFLIGHT_LEAD_HOURS)

    # Determine the base date for the next regular refresh slot
    if row.last_auto_refresh_at is None:
        base_date = now_utc.date()
    else:
        base_date = row.last_auto_refresh_at.date() + timedelta(days=1)

    regular = datetime(
        base_date.year, base_date.month, base_date.day,
        effective_hour, tzinfo=timezone.utc,
    )

    # If we already refreshed at or after the pre-flight time, that slot is
    # satisfied — only the regular schedule matters from here on.
    last_refresh = row.last_auto_refresh_at
    if last_refresh is not None and last_refresh.tzinfo is None:
        last_refresh = last_refresh.replace(tzinfo=timezone.utc)
    if last_refresh is not None and last_refresh >= preflight:
        return regular

    return min(regular, preflight)


def _flight_start_dt(row: FlightRow) -> datetime | None:
    """Return the absolute UTC flight-start datetime.

    Returns ``None`` if departure_time is not set.
    """
    dt = row.departure_time
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------


def _auto_refresh_one(flight_row: FlightRow, app_state, user_id: str) -> None:
    """Run the briefing pipeline for a single flight (called in a thread)."""
    from weatherbrief.api.packs import (
        _build_data_status, _finalize_refresh, _prepare_refresh, refresh_registry,
    )
    from weatherbrief.storage.flights import _row_to_flight, list_packs

    db = SessionLocal()
    try:
        flight = _row_to_flight(flight_row)

        # Check freshness — skip if data hasn't changed
        packs = list_packs(db, flight_row.id)
        if packs:
            latest = packs[0]
            status = _build_data_status(latest.model_init_times)
            if status.fresh:
                logger.info("Auto-refresh: data is fresh for %s, skipping", flight_row.id)
                return

        db_path = getattr(app_state, "db_path", "")
        if not db_path:
            logger.warning("Auto-refresh: AIRPORTS_DB not configured, skipping %s", flight_row.id)
            return

        route, fetch_ts, pack_path, options, model_metadata = _prepare_refresh(
            flight, db_path, user_id, flight_row.id, db=db, is_privileged=True,
        )

        from weatherbrief.pipeline import execute_briefing

        result = execute_briefing(
            route=route,
            departure_time=flight.departure_time,
            options=options,
        )

        # Capture timing before unregister
        queue_wait, total_elapsed = refresh_registry.get_timing(flight_row.id)
        result.usage.elapsed_seconds = total_elapsed
        result.usage.queue_wait_seconds = queue_wait
        result.usage.triggered_by = "scheduler"

        meta = _finalize_refresh(
            flight_row.id, flight, fetch_ts, pack_path, result, db,
            user_id=user_id, model_metadata=model_metadata,
        )
        db.commit()

        # Send email notification
        _try_send_email(db, flight, meta, pack_path, user_id)

    finally:
        db.close()


def _try_send_email(
    db: Session, flight, meta, pack_path, user_id: str,
) -> None:
    """Attempt to send a briefing email, logging and skipping on any failure."""
    try:
        from weatherbrief.notify.email import SmtpConfig, send_briefing_email

        SmtpConfig.from_env()  # validate config exists
    except (ValueError, ImportError):
        logger.debug("Auto-refresh: SMTP not configured, skipping email for %s", flight.id)
        return

    user = db.query(UserRow).filter(UserRow.id == user_id).first()
    if not user or not user.email:
        logger.debug("Auto-refresh: no email for user %s, skipping", user_id)
        return

    base_url = os.environ.get("WEATHERBRIEF_BASE_URL", "https://weather.flyfun.aero")
    try:
        from weatherbrief.notify.email import send_briefing_email

        send_briefing_email([user.email], flight, meta, pack_path, base_url=base_url)
        logger.info("Auto-refresh email sent for %s to %s", flight.id, user.email)
    except Exception:
        logger.warning("Auto-refresh email failed for %s", flight.id, exc_info=True)
