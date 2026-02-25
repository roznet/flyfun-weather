"""Background auto-refresh scheduler.

Polls every 10 minutes for flights with auto_refresh enabled that are due
for a refresh. Runs the briefing pipeline and optionally sends an email.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from weatherbrief.db.engine import SessionLocal
from weatherbrief.db.models import FlightRow, UserRow

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 600  # 10 minutes
_STARTUP_DELAY_SECONDS = 30


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
                # Mark as refreshed today (in a fresh session to avoid stale state)
                mark_db = SessionLocal()
                try:
                    mark_row = mark_db.get(FlightRow, row.id)
                    if mark_row:
                        mark_row.last_auto_refresh_date = _today_str()
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


def _find_due_flights(db: Session) -> list[FlightRow]:
    """Return flights that are due for auto-refresh."""
    today = _today_str()
    now_utc = datetime.now(timezone.utc)
    current_hour = now_utc.hour

    stmt = select(FlightRow).where(FlightRow.auto_refresh.is_(True))
    rows = db.execute(stmt).scalars().all()

    due = []
    for row in rows:
        # Already refreshed today
        if row.last_auto_refresh_date == today:
            continue
        # Flight is past
        if _is_flight_past(row):
            continue
        # Not yet the scheduled hour
        effective_hour = row.auto_refresh_hour
        if effective_hour is None:
            effective_hour = (row.target_time_utc - 1) % 24
        if current_hour < effective_hour:
            continue
        due.append(row)

    return due


def _is_flight_past(row: FlightRow) -> bool:
    """Check if a flight's target time + duration has passed."""
    try:
        flight_date = date.fromisoformat(row.target_date)
        flight_start = datetime(
            flight_date.year, flight_date.month, flight_date.day,
            row.target_time_utc, tzinfo=timezone.utc,
        )
        duration_hours = row.flight_duration_hours or 0.0
        flight_end = flight_start + timedelta(hours=duration_hours)
        return datetime.now(timezone.utc) > flight_end
    except (ValueError, TypeError):
        return True  # Treat malformed dates as past


def _auto_refresh_one(flight_row: FlightRow, app_state, user_id: str) -> None:
    """Run the briefing pipeline for a single flight (called in a thread)."""
    from weatherbrief.api.packs import _build_data_status, _finalize_refresh, _prepare_refresh
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
            target_date=flight.target_date,
            target_hour=flight.target_time_utc,
            options=options,
        )

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


def _today_str() -> str:
    """Return today's date as YYYY-MM-DD in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
