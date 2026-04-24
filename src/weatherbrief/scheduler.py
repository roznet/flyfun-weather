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
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from flyfun_common.db import SessionLocal
from flyfun_common.db.models import UserRow
from weatherbrief.db.models import FlightRow

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 600  # 10 minutes
_STARTUP_DELAY_SECONDS = 30
_PREFLIGHT_LEAD_HOURS = 2
_RETENTION_INTERVAL_SECONDS = 86_400  # 24 hours
_RETENTION_STARTUP_DELAY_SECONDS = 120  # let auto-refresh settle first
_VERIF_POLL_SECONDS = 600  # 10 minutes
_VERIF_STARTUP_DELAY_SECONDS = 60  # let auto-refresh settle first
_DIGEST_INTERVAL_SECONDS = 86_400  # 24 hours
_DIGEST_STARTUP_DELAY_SECONDS = 180  # let verification settle first
_STANDALONE_STARTUP_DELAY_SECONDS = 240  # let other loops settle first
_ECMWF_WATCHER_POLL_SECONDS = 300  # 5 minutes
_ECMWF_WATCHER_STARTUP_DELAY_SECONDS = 15  # run early — other loops may need ready data
# Hewson precompute fires once per init cycle. 05Z and 17Z pick up the 00Z /
# 12Z inits after ~5 h — Open-Meteo has all 3 models published by then, and
# we keep 1 h buffer before the 06Z / 18Z standalone-verification full cycles.
_HEWSON_SAMPLE_HOURS_UTC = [5, 17]
_HEWSON_STARTUP_DELAY_SECONDS = 300  # let other loops settle; Hewson is cold-cache anyway


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


# ---------------------------------------------------------------------------
# Verification loop
# ---------------------------------------------------------------------------


async def run_verification_loop(app_state) -> None:
    """Collect METAR/TAF observations for active flights.

    Polls every 10 minutes for flights in their observation window
    (departure-1h to flight_end+1h) and archives METAR/TAF data.
    """
    logger.info("Verification loop started (poll every %ds)", _VERIF_POLL_SECONDS)
    await asyncio.sleep(_VERIF_STARTUP_DELAY_SECONDS)

    while True:
        try:
            await asyncio.to_thread(_run_verification_once, app_state)
        except Exception:
            logger.error("Verification cycle failed", exc_info=True)
        await asyncio.sleep(_VERIF_POLL_SECONDS)


def _run_verification_once(app_state) -> None:
    """Execute a single verification collection cycle (called in a thread)."""
    db_path = getattr(app_state, "db_path", "")
    if not db_path:
        return

    from weatherbrief.tasks.verification import collect_and_store

    db = SessionLocal()
    try:
        result = collect_and_store(db, db_path)
        if result["flights"] > 0:
            logger.info(
                "Verification cycle: %d flight(s), %d airport(s), "
                "%d observation(s) stored, %d finalized",
                result["flights"], result["airports"],
                result["observations"], result["finalized"],
            )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Digest loop
# ---------------------------------------------------------------------------


async def run_digest_loop(app_state) -> None:
    """Send daily verification digest email at a fixed UTC hour.

    Waits until the target hour (default 06:00 UTC), sends the digest,
    then sleeps until the same hour the next day.
    """
    target_hour = int(os.environ.get("DIGEST_HOUR_UTC", "6"))
    logger.info("Digest loop started (daily at %02d:00 UTC)", target_hour)
    await asyncio.sleep(_DIGEST_STARTUP_DELAY_SECONDS)

    while True:
        now = datetime.now(timezone.utc)
        next_run = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        wait_seconds = (next_run - now).total_seconds()
        logger.info("Digest: next send at %s (in %.0fs)", next_run.isoformat(), wait_seconds)
        await asyncio.sleep(wait_seconds)

        try:
            await asyncio.to_thread(_run_digest_once)
        except Exception:
            logger.error("Digest cycle failed", exc_info=True)


def _run_digest_once() -> None:
    """Execute a single admin digest send (called in a thread)."""
    from weatherbrief.notify.admin_digest_email import send_admin_digest
    from weatherbrief.notify.admin_email import get_admin_emails
    from weatherbrief.tasks.admin_digest_stats import get_admin_digest_data

    admin_emails = get_admin_emails()
    if not admin_emails:
        logger.debug("Digest: no admin emails configured, skipping")
        return

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    date_label = now.strftime("%Y-%m-%d")
    base_url = os.environ.get("WEATHERBRIEF_BASE_URL", "https://weather.flyfun.aero")

    db = SessionLocal()
    try:
        data = get_admin_digest_data(
            db, since, now, period_label=date_label, base_url=base_url,
        )
        send_admin_digest(admin_emails, data)
        logger.info("Admin digest: sent to %d admin(s)", len(admin_emails))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Retention loop
# ---------------------------------------------------------------------------


async def run_retention_loop(app_state) -> None:
    """Periodic retention — started as an asyncio task from app lifespan.

    Runs once per day, purging old pack artifacts according to the
    tiered retention policy (see :mod:`weatherbrief.tasks.retention`).
    """
    logger.info("Retention loop started (every %ds)", _RETENTION_INTERVAL_SECONDS)
    await asyncio.sleep(_RETENTION_STARTUP_DELAY_SECONDS)

    while True:
        try:
            await asyncio.to_thread(_run_retention_once)
        except Exception:
            logger.error("Retention cycle failed", exc_info=True)
        await asyncio.sleep(_RETENTION_INTERVAL_SECONDS)


def _run_retention_once() -> None:
    """Execute a single retention pass (called in a thread)."""
    from weatherbrief.tasks.retention import RetentionConfig, run_retention

    db = SessionLocal()
    try:
        config = RetentionConfig.from_env()
        run_retention(db, config)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    # Purge old ECMWF deliveries (72h default — keeps ~2 days of runs)
    try:
        from weatherbrief.fetch.grib.ecmwf_watcher import purge_old_ecmwf_deliveries

        purge_old_ecmwf_deliveries()
    except Exception:
        logger.error("ECMWF delivery purge failed", exc_info=True)

    # Purge old GRIB download cache (GFS + ICON-EU, 24h TTL)
    try:
        from weatherbrief.fetch.grib.cache import purge_old_runs

        data_dir = Path(os.environ.get("DATA_DIR", "data"))
        for model in ("gfs", "icon-eu"):
            removed = purge_old_runs(data_dir, model=model)
            if removed:
                logger.info("Purged %d old %s GRIB cache dirs", removed, model)
    except Exception:
        logger.error("GRIB cache purge failed", exc_info=True)


# ---------------------------------------------------------------------------
# Standalone verification loop
# ---------------------------------------------------------------------------


async def run_standalone_verification_loop(app_state) -> None:
    """Run standalone airport verification at configured sample hours.

    Instead of polling every N minutes, computes the next sample hour
    and sleeps until then — no wasted cycles.

    Disableable via DISABLE_STANDALONE_VERIFICATION=1 env var.
    """
    from weatherbrief.tasks.standalone_verification import (
        FULL_CYCLE_HOURS_UTC,
        SAMPLE_HOURS_UTC,
    )

    if os.environ.get("DISABLE_STANDALONE_VERIFICATION", "").strip() in ("1", "true"):
        logger.info("Standalone verification disabled via env var")
        return

    logger.info(
        "Standalone verification loop started (sample hours: %s UTC, "
        "full cycle hours: %s UTC)",
        SAMPLE_HOURS_UTC, sorted(FULL_CYCLE_HOURS_UTC),
    )
    await asyncio.sleep(_STANDALONE_STARTUP_DELAY_SECONDS)

    while True:
        try:
            sleep_secs = _seconds_until_next_sample_hour(SAMPLE_HOURS_UTC)
            logger.info("Standalone verification: sleeping %ds until next sample hour",
                        sleep_secs)
            await asyncio.sleep(sleep_secs)
            current_hour = datetime.now(timezone.utc).hour
            fetch_forecasts = current_hour in FULL_CYCLE_HOURS_UTC
            await asyncio.to_thread(
                _run_standalone_once, app_state, fetch_forecasts,
            )
            # After running, sleep briefly to ensure we advance past the
            # current sample hour and don't re-trigger immediately.
            await asyncio.sleep(60)
        except Exception:
            logger.error("Standalone verification cycle failed", exc_info=True)
            # On failure, wait 15 min before retrying
            await asyncio.sleep(900)


def _seconds_until_next_sample_hour(sample_hours: list[int]) -> float:
    """Compute seconds until the next sample hour (top of the hour).

    If we are exactly on a sample hour, returns 0 so the cycle runs immediately.
    """
    now = datetime.now(timezone.utc)
    for hour in sorted(sample_hours):
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate >= now:
            return (candidate - now).total_seconds()

    # Next sample hour is tomorrow's first
    tomorrow = now + timedelta(days=1)
    first_hour = min(sample_hours)
    candidate = tomorrow.replace(hour=first_hour, minute=0, second=0, microsecond=0)
    return (candidate - now).total_seconds()


def _run_standalone_once(
    app_state,
    fetch_forecasts: bool = True,
) -> None:
    """Execute a single standalone verification cycle (called in a thread)."""
    db_path = getattr(app_state, "db_path", "")
    if not db_path:
        logger.warning("Standalone verification: no AIRPORTS_DB configured")
        return

    from weatherbrief.tasks.airport_watchlist import (
        get_configs_dir,
        load_watchlist_with_coords,
    )
    from weatherbrief.tasks.standalone_verification import run_standalone_cycle

    try:
        airports = load_watchlist_with_coords(get_configs_dir(), db_path)
    except FileNotFoundError:
        logger.warning(
            "Standalone verification: airport watchlist not found. "
            "Run: python -m weatherbrief.verify discover"
        )
        return

    if not airports:
        logger.warning("Standalone verification: empty watchlist")
        return

    result = run_standalone_cycle(
        airports, db_path, fetch_forecasts=fetch_forecasts,
    )
    logger.info(
        "Standalone verification %s cycle: %d models, %d snapshots, "
        "%d observations, %d scores (%dms)",
        result["cycle_type"],
        result["models_fetched"], result["snapshots_stored"],
        result["observations_stored"], result["scores_created"],
        result["duration_ms"],
    )

    # Rebuild dashboard + map caches after cycle completes
    try:
        from flyfun_common.db import SessionLocal
        from weatherbrief.tasks.cache_builder import rebuild_all

        cache_db = SessionLocal()
        try:
            cache_result = rebuild_all(cache_db, db_path)
            logger.info(
                "Standalone verification: cache rebuilt (%dms)",
                cache_result["duration_ms"],
            )
        finally:
            cache_db.close()
    except Exception:
        logger.error("Cache rebuild failed", exc_info=True)


# ---------------------------------------------------------------------------
# ECMWF delivery watcher loop
# ---------------------------------------------------------------------------


async def run_ecmwf_watcher_loop(app_state) -> None:
    """Watch for complete ECMWF deliveries — started as an asyncio task."""
    logger.info(
        "ECMWF watcher started (poll every %ds)", _ECMWF_WATCHER_POLL_SECONDS,
    )
    await asyncio.sleep(_ECMWF_WATCHER_STARTUP_DELAY_SECONDS)

    while True:
        try:
            newly_ready = await asyncio.to_thread(_run_ecmwf_watcher_once)
            if newly_ready:
                logger.info(
                    "ECMWF watcher: %d new run(s) ready: %s",
                    len(newly_ready),
                    ", ".join(bt.isoformat() for bt in newly_ready),
                )
        except Exception:
            logger.error("ECMWF watcher cycle failed", exc_info=True)
        await asyncio.sleep(_ECMWF_WATCHER_POLL_SECONDS)


def _run_ecmwf_watcher_once() -> list[datetime]:
    """Execute a single ECMWF completeness check (called in a thread)."""
    from weatherbrief.fetch.grib.ecmwf_watcher import check_ecmwf_completeness

    return check_ecmwf_completeness()


# ---------------------------------------------------------------------------
# Hewson precompute loop
# ---------------------------------------------------------------------------


async def run_hewson_precompute_loop(app_state) -> None:
    """Fire Hewson diagnostic precompute at fixed UTC hours.

    Runs at ``_HEWSON_SAMPLE_HOURS_UTC`` (05Z / 17Z by default), chosen to
    sit ~5 h after each ``00Z/12Z`` init (Open-Meteo has all 3 models
    published by then) and 1 h before the 06Z/18Z full-cycle verification
    run (avoids CPU/network overlap). See
    ``designs/future/hewson-fields-aviation-advisories.md`` § 6.1.

    The heavy lifting is in :func:`weatherbrief.hewson.precompute.run_once`,
    which is also the CLI entry point — so debugging and ad-hoc re-runs
    exercise identical code.

    Disableable via ``DISABLE_HEWSON_PRECOMPUTE=1``.
    """
    if os.environ.get("DISABLE_HEWSON_PRECOMPUTE", "").strip() in ("1", "true"):
        logger.info("Hewson precompute disabled via env var")
        return

    logger.info(
        "Hewson precompute loop started (sample hours: %s UTC)",
        _HEWSON_SAMPLE_HOURS_UTC,
    )
    await asyncio.sleep(_HEWSON_STARTUP_DELAY_SECONDS)

    while True:
        try:
            sleep_secs = _seconds_until_next_sample_hour(_HEWSON_SAMPLE_HOURS_UTC)
            logger.info(
                "Hewson precompute: sleeping %ds until next sample hour",
                sleep_secs,
            )
            await asyncio.sleep(sleep_secs)
            await asyncio.to_thread(_run_hewson_precompute_once)
            # Advance past the current sample hour before the next loop so
            # we don't re-trigger in the same minute.
            await asyncio.sleep(60)
        except Exception:
            logger.error("Hewson precompute cycle failed", exc_info=True)
            # Back off 15 min on failure (same as standalone verification)
            await asyncio.sleep(900)


def _run_hewson_precompute_once() -> None:
    """Execute a single Hewson precompute cycle (called in a thread).

    After the compute + write, persists the Open-Meteo call count to
    ``ApiUsageRow`` via :func:`weatherbrief.api.usage.log_api_usage` so
    the precompute's API consumption shows up in the shared usage
    dashboard alongside the other pipelines (briefing / verification /
    standalone). Matches the pattern in
    :mod:`weatherbrief.tasks.standalone_verification`.
    """
    from weatherbrief.hewson import run_once

    result = run_once()
    logger.info(
        "Hewson precompute: %d written, %d skipped, %d purged, "
        "%d Open-Meteo calls (%.1fs)",
        len([p for p in result.snapshots.values() if p is not None]),
        len(result.skipped),
        result.purged,
        result.api_calls_total,
        result.elapsed_seconds,
    )

    if result.api_calls_total > 0:
        from flyfun_common.db import SessionLocal

        from weatherbrief.api.usage import log_api_usage

        db = SessionLocal()
        try:
            log_api_usage(
                db,
                service="open_meteo",
                pipeline="hewson_precompute",
                api_calls=result.api_calls_total,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.error(
                "Hewson precompute: failed to log API usage", exc_info=True,
            )
        finally:
            db.close()
