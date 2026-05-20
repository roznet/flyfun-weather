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
# METAR ingest fires every 30 min on :00/:30. Most EU airports issue at HH:20
# and HH:50, so by HH:00/HH:30 aviationweather.gov has fully absorbed the
# previous batch — no offset needed. Decoupled from forecast fetch and scoring
# so the observation table is populated continuously instead of only at
# sample hours.
_METAR_INGEST_INTERVAL_SECONDS = 1800  # 30 minutes
_METAR_INGEST_OFFSET_SECONDS = 0  # fire at :00/:30 sharp
_METAR_INGEST_STARTUP_DELAY_SECONDS = 200  # land before standalone (240s)
# Verification scoring fires 15 min past each synoptic hour, giving the HH:00
# ingest plenty of margin (METAR fetch is ~30-60s) and ensuring freshly-stored
# METARs are scored against snapshots already in DB.
_VERIFICATION_HOUR_OFFSET_SECONDS = 900  # 15 min
_ECMWF_WATCHER_POLL_SECONDS = 300  # 5 minutes
_ECMWF_WATCHER_STARTUP_DELAY_SECONDS = 15  # run early — other loops may need ready data
_FRESHNESS_LOOP_STARTUP_DELAY_SECONDS = 20  # let ECMWF watcher run once first
# Forecast fetch runs at HH:15 (not on the hour) so Open-Meteo GFS (~+6h45m
# after 00Z/12Z init) and ECMWF direct (~+6h40m) have a margin to land.
_FORECAST_FETCH_HOUR_OFFSET_SECONDS = 900  # 15 min
# If a slow-publisher marker's next_expected falls within this window after the
# offset, wait for it (capped) — saves a stale-init fetch when delivery is
# running a few minutes late.
_FORECAST_FETCH_FRESHNESS_WAIT_MAX_SECONDS = 900  # 15 min
_FORECAST_FETCH_FRESHNESS_SOURCES: list[tuple[str, str]] = [
    ("gfs:openmeteo", "gfs"),
    ("icon:openmeteo", "icon"),
]
# Hewson precompute fires once per init cycle. 06Z and 18Z pick up the 00Z /
# 12Z inits after ~6 h — Open-Meteo has all 3 models published by then, and
# we keep ~1 h buffer before the 07Z / 19Z standalone forecast-fetch cycles.
_HEWSON_SAMPLE_HOURS_UTC = [6, 18]
_HEWSON_STARTUP_DELAY_SECONDS = 300  # let other loops settle; Hewson is cold-cache anyway
# Usage-analytics rollup runs daily — at the same UTC hour each day so the
# rollup of "yesterday" lands well after the day has actually rolled over.
_ANALYTICS_ROLLUP_INTERVAL_SECONDS = 86_400
_ANALYTICS_ROLLUP_STARTUP_DELAY_SECONDS = 90  # land after retention/scheduler
# Weekly digest fires once a week on Monday at 08:00 UTC by default.
_ANALYTICS_DIGEST_STARTUP_DELAY_SECONDS = 120
# GRIB pre-cache loop polls the freshness MarkerStore every 5 min and pre-fetches
# the airport-profile (forecast-map) byte ranges as soon as a new ICON-EU / GFS
# main run lands. See issue #126.
_GRIB_PRECACHE_POLL_SECONDS = 300
_GRIB_PRECACHE_STARTUP_DELAY_SECONDS = 60  # let freshness loop bootstrap first


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
        _build_data_status, _days_out_now, _finalize_refresh, _prepare_refresh,
        decide_refresh, refresh_registry,
    )
    from weatherbrief.storage.flights import _row_to_flight, list_packs

    db = SessionLocal()
    try:
        flight = _row_to_flight(flight_row)

        # Tiered refresh gate (issue #167): the scheduler applies the same
        # full/none policy as the manual button but never the realtime
        # fallback — live METAR/TAF is the verification loop's job.
        packs = list_packs(db, flight_row.id)
        if packs:
            latest = packs[0]
            status = _build_data_status(latest, flight)
            decision = decide_refresh(status, _days_out_now(flight))
            if decision.mode != "full":
                logger.info(
                    "Auto-refresh: gate=%s for %s (%s), skipping",
                    decision.mode, flight_row.id, decision.reason,
                )
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
    target_hour = int(os.environ.get("DIGEST_HOUR_UTC", "8"))
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

    # Purge old ECMWF deliveries. Readers always pick max(base_time) of
    # ready runs, so older inits are never consulted; 36 h keeps the latest
    # run plus ~24 h of prior runs as headroom for in-flight deliveries.
    try:
        from weatherbrief.fetch.grib.ecmwf_watcher import purge_old_ecmwf_deliveries

        purge_old_ecmwf_deliveries(max_age_hours=36)
    except Exception:
        logger.error("ECMWF delivery purge failed", exc_info=True)

    # Purge old GRIB download cache; per-model TTL in MODEL_TTL_SECONDS
    try:
        from weatherbrief.fetch.grib.cache import purge_old_runs

        data_dir = Path(os.environ.get("DATA_DIR", "data"))
        for model in ("gfs", "icon-eu"):
            removed = purge_old_runs(data_dir, model=model)
            if removed:
                logger.info("Purged %d old %s GRIB cache dirs", removed, model)
    except Exception:
        logger.error("GRIB cache purge failed", exc_info=True)

    # Age-based safety wipe for DWD chart cycles. The primary eviction is
    # count-based and lives inside refresh_charts (keep N=8 most recent).
    # This is a backstop for cycles older than 14 days that survived a
    # long quiet period.
    try:
        from weatherbrief.fetch.dwd_charts import evict_cycles_older_than

        data_dir = Path(os.environ.get("DATA_DIR", "data"))
        evicted = evict_cycles_older_than(data_dir, max_age_hours=14 * 24)
        if evicted:
            logger.info("Age-evicted %d DWD chart cycles", len(evicted))
    except Exception:
        logger.error("DWD chart cache age-eviction failed", exc_info=True)

    # Roll up completed months/days into the airport summary tables, then
    # ensure future MySQL partitions exist, then prune raw obs older than
    # retention (effectively a no-op until VERIFICATION_RAW_RETENTION_DAYS
    # is set < 9999). Each step is wrapped independently so a single
    # failure doesn't skip the others.
    try:
        from weatherbrief.tasks.airport_summary import (
            rollup_all_complete_days,
            rollup_all_complete_months,
        )

        db = SessionLocal()
        try:
            n_months = rollup_all_complete_months(db)
            n_days = rollup_all_complete_days(db)
            db.commit()
            if n_months or n_days:
                logger.info(
                    "Airport summary rollup: %d months, %d days",
                    n_months, n_days,
                )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception:
        logger.error("Airport summary rollup failed", exc_info=True)

    try:
        from weatherbrief.tasks.retention import ensure_future_partitions

        db = SessionLocal()
        try:
            added = ensure_future_partitions(db, months_ahead=3)
            db.commit()
            if added:
                logger.info("Created %d future verification_observations partition(s)", added)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception:
        logger.error("Partition maintenance failed", exc_info=True)

    try:
        from weatherbrief.tasks.retention import prune_raw_observations

        db = SessionLocal()
        try:
            result = prune_raw_observations(db)
            db.commit()
            if any(v for v in result.values()):
                logger.info(
                    "Raw retention: pruned obs=%d scores=%d taf=%d map=%d",
                    result["observations"], result["scores"],
                    result["taf_scores"], result["map_rows"],
                )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception:
        logger.error("Raw observation retention failed", exc_info=True)


# ---------------------------------------------------------------------------
# METAR ingest, forecast fetch, and standalone verification loops
#
# Three independent loops driven by the standalone subsystem:
#
#   * METAR ingest fires every 30 min at :00/:30 sharp. Most EU airports
#     issue METARs at HH:20/HH:50, so by HH:00/HH:30 aviationweather has
#     fully absorbed them. Pulls METAR/TAF for the watchlist and upserts
#     into verification_observations. Source tag: 'metar_ingest'.
#   * Forecast fetch fires at FORECAST_FETCH_HOURS_UTC + 15 min (07:15/19:15)
#     so Open-Meteo GFS (~+6h45m) and ECMWF direct (~+6h40m) deliveries have
#     landed. Additionally waits up to 15 min more if a marker's next_expected
#     is within the window. Stores fresh snapshots only.
#   * Verification fires at VERIFICATION_HOURS_UTC + 15 min (e.g. 06:15,
#     09:15, ...) so the HH:00 METAR ingest has fully landed. Reads
#     observations + snapshots already in DB and scores them — does NOT
#     call aviationweather.gov (METAR ingest owns that).
#
# Decoupled so observations accumulate continuously, scoring runs on a
# synoptic cadence, and forecast fetch waits for the late ECMWF delivery.
# ---------------------------------------------------------------------------


async def run_metar_ingest_loop(app_state) -> None:
    """Fetch METAR/TAF for the airport watchlist every 30 min.

    Disableable via ``DISABLE_METAR_INGEST=1`` env var.
    """
    if os.environ.get("DISABLE_METAR_INGEST", "").strip() in ("1", "true"):
        logger.info("METAR ingest disabled via env var")
        return

    logger.info(
        "METAR ingest loop started (every %ds, offset +%ds)",
        _METAR_INGEST_INTERVAL_SECONDS, _METAR_INGEST_OFFSET_SECONDS,
    )
    await asyncio.sleep(_METAR_INGEST_STARTUP_DELAY_SECONDS)

    while True:
        try:
            sleep_secs = _seconds_until_next_30min_boundary(
                _METAR_INGEST_OFFSET_SECONDS,
            )
            logger.info(
                "METAR ingest: sleeping %ds until next ingest tick", sleep_secs,
            )
            await asyncio.sleep(sleep_secs)
            await asyncio.to_thread(_run_metar_ingest_once, app_state)
            # Advance past the current bucket so we don't re-trigger immediately
            await asyncio.sleep(60)
        except Exception:
            logger.error("METAR ingest cycle failed", exc_info=True)
            await asyncio.sleep(900)


def _seconds_until_next_30min_boundary(offset_seconds: int) -> float:
    """Seconds until the next ``:offset_minutes`` past either ``:00`` or ``:30``.

    For ``offset_seconds=300`` (5 min), fires at ``HH:05`` and ``HH:35``.
    Must be ``< 1800`` (30 min) — anything larger lands in the next bucket
    and would silently overflow ``minute=30+offset_minutes`` past 59.
    """
    if not 0 <= offset_seconds < 1800:
        raise ValueError(
            f"offset_seconds must be in [0, 1800), got {offset_seconds}"
        )
    now = datetime.now(timezone.utc)
    offset_minutes = offset_seconds // 60
    candidates = [
        now.replace(minute=offset_minutes, second=0, microsecond=0),
        now.replace(minute=30 + offset_minutes, second=0, microsecond=0),
    ]
    candidates = [c for c in candidates if c > now]
    if candidates:
        return (min(candidates) - now).total_seconds()
    # Both offsets in this hour have passed — first slot of the next hour
    next_hour = (now + timedelta(hours=1)).replace(
        minute=offset_minutes, second=0, microsecond=0,
    )
    return (next_hour - now).total_seconds()


def _run_metar_ingest_once(app_state) -> None:
    """Execute a single METAR ingest cycle (called in a thread)."""
    db_path = getattr(app_state, "db_path", "")
    if not db_path:
        logger.warning("METAR ingest: no AIRPORTS_DB configured")
        return

    from weatherbrief.tasks.airport_watchlist import (
        get_configs_dir,
        load_watchlist_with_coords,
    )
    from weatherbrief.tasks.standalone_verification import run_metar_ingest_cycle

    try:
        airports = load_watchlist_with_coords(get_configs_dir(), db_path)
    except FileNotFoundError:
        logger.warning(
            "METAR ingest: airport watchlist not found. "
            "Run: python -m weatherbrief.verify discover"
        )
        return

    if not airports:
        logger.warning("METAR ingest: empty watchlist")
        return

    result = run_metar_ingest_cycle(airports, db_path)
    logger.info(
        "METAR ingest cycle: %d airports, %d new observations (%dms)",
        result["airports"], result["observations_stored"], result["duration_ms"],
    )


# ---------------------------------------------------------------------------
# Forecast fetch + standalone verification loops
# ---------------------------------------------------------------------------


async def run_forecast_fetch_loop(app_state) -> None:
    """Fetch standalone forecast snapshots at configured fetch hours.

    Disableable via DISABLE_FORECAST_FETCH=1 env var.
    """
    from weatherbrief.tasks.standalone_verification import FORECAST_FETCH_HOURS_UTC

    if os.environ.get("DISABLE_FORECAST_FETCH", "").strip() in ("1", "true"):
        logger.info("Forecast fetch disabled via env var")
        return

    logger.info(
        "Forecast fetch loop started (fetch hours: %s UTC)",
        FORECAST_FETCH_HOURS_UTC,
    )
    await asyncio.sleep(_STANDALONE_STARTUP_DELAY_SECONDS)

    while True:
        try:
            sleep_secs = _seconds_until_next_sample_hour(FORECAST_FETCH_HOURS_UTC)
            logger.info(
                "Forecast fetch: sleeping %ds until next fetch hour", sleep_secs,
            )
            await asyncio.sleep(sleep_secs)
            await asyncio.sleep(_FORECAST_FETCH_HOUR_OFFSET_SECONDS)
            await _wait_for_marker_freshness(_FORECAST_FETCH_FRESHNESS_WAIT_MAX_SECONDS)
            await asyncio.to_thread(
                _run_standalone_once, app_state,
                True,   # fetch_forecasts
                False,  # score_observations
            )
            await asyncio.sleep(60)  # advance past the current hour
        except Exception:
            logger.error("Forecast fetch cycle failed", exc_info=True)
            await asyncio.sleep(900)


async def _wait_for_marker_freshness(max_wait_seconds: float) -> None:
    """Sleep up to ``max_wait_seconds`` if a tracked marker is about to publish.

    For each (source, model) in :data:`_FORECAST_FETCH_FRESHNESS_SOURCES`, if
    its ``next_expected`` falls between *now* and *now+max_wait_seconds*, hold
    off until the latest such time so the fetch picks up the fresh init rather
    than the previous one. Capped at ``max_wait_seconds`` so a long-slipping
    provider doesn't block the cycle indefinitely.
    """
    from weatherbrief.fetch.freshness.markers import get_store

    store = get_store()
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(seconds=max_wait_seconds)
    target = now
    waited_for: list[str] = []
    for source, model in _FORECAST_FETCH_FRESHNESS_SOURCES:
        marker = store.get_sync(source, model)
        if marker is None:
            continue
        if now < marker.next_expected <= deadline:
            if marker.next_expected > target:
                target = marker.next_expected
            waited_for.append(f"{source}/{model}@{marker.next_expected.isoformat()}")

    wait = (target - now).total_seconds()
    if wait > 0:
        logger.info(
            "Forecast fetch: freshness wait %.0fs for %s",
            wait, ", ".join(waited_for),
        )
        await asyncio.sleep(wait)


async def run_standalone_verification_loop(app_state) -> None:
    """Score METAR/TAF observations at configured verification hours.

    Reads forecast snapshots already in DB (populated by the fetch loop) —
    does not call Open-Meteo / GRIB. Disableable via
    DISABLE_STANDALONE_VERIFICATION=1 env var.
    """
    from weatherbrief.tasks.standalone_verification import VERIFICATION_HOURS_UTC

    if os.environ.get("DISABLE_STANDALONE_VERIFICATION", "").strip() in ("1", "true"):
        logger.info("Standalone verification disabled via env var")
        return

    logger.info(
        "Standalone verification loop started (verification hours: %s UTC)",
        VERIFICATION_HOURS_UTC,
    )
    await asyncio.sleep(_STANDALONE_STARTUP_DELAY_SECONDS)

    while True:
        try:
            sleep_secs = _seconds_until_next_sample_hour(VERIFICATION_HOURS_UTC)
            logger.info(
                "Standalone verification: sleeping %ds until next verification hour",
                sleep_secs,
            )
            await asyncio.sleep(sleep_secs)
            # Offset by 15 min so the HH:00 METAR ingest (~30-60s fetch) has
            # fully landed before scoring picks the nearest obs.
            await asyncio.sleep(_VERIFICATION_HOUR_OFFSET_SECONDS)
            await asyncio.to_thread(
                _run_standalone_once, app_state,
                False,  # fetch_forecasts
                True,   # score_observations
            )
            await asyncio.sleep(60)  # advance past the current offset
        except Exception:
            logger.error("Standalone verification cycle failed", exc_info=True)
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
    fetch_forecasts: bool,
    score_observations: bool,
) -> None:
    """Execute a single standalone cycle (called in a thread).

    Used by both the forecast-fetch loop (fetch=True, score=False) and the
    verification loop (fetch=False, score=True).
    """
    db_path = getattr(app_state, "db_path", "")
    if not db_path:
        logger.warning("Standalone cycle: no AIRPORTS_DB configured")
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
            "Standalone cycle: airport watchlist not found. "
            "Run: python -m weatherbrief.verify discover"
        )
        return

    if not airports:
        logger.warning("Standalone cycle: empty watchlist")
        return

    result = run_standalone_cycle(
        airports, db_path,
        fetch_forecasts=fetch_forecasts,
        score_observations=score_observations,
    )
    logger.info(
        "Standalone %s cycle: %d models, %d snapshots, "
        "%d observations, %d scores (%dms)",
        result["cycle_type"],
        result["models_fetched"], result["snapshots_stored"],
        result["observations_stored"], result["scores_created"],
        result["duration_ms"],
    )

    # Roll up freshly-scored days into verification_daily_stats BEFORE the
    # cache rebuild so the cache reflects today's scores. Idempotent — re-
    # rolls "yesterday" once per cycle, plus any older missing days (first-
    # deploy backfill of ~43 days). Cheap (~12K rows/day, pure SQL).
    try:
        from flyfun_common.db import SessionLocal
        from weatherbrief.tasks.verification_daily_rollup import (
            rollup_today_and_pending,
        )

        rollup_db = SessionLocal()
        try:
            n_rows = rollup_today_and_pending(rollup_db)
            rollup_db.commit()
            if n_rows:
                logger.info(
                    "Standalone %s cycle: %d daily-stats rows rolled up",
                    result["cycle_type"], n_rows,
                )
        except Exception:
            rollup_db.rollback()
            raise
        finally:
            rollup_db.close()
    except Exception:
        logger.error("verification_daily_stats rollup failed", exc_info=True)

    # Rebuild dashboard + map caches after cycle completes. Light (score-only)
    # cycles don't touch forecast snapshots, so the forecast_map cache would be
    # regenerated to identical content — skip it to halve the rebuild cost on
    # the four hourly light cycles.
    try:
        from flyfun_common.db import SessionLocal
        from weatherbrief.tasks.cache_builder import rebuild_all

        cache_db = SessionLocal()
        try:
            cache_result = rebuild_all(
                cache_db, db_path,
                include_forecast_map=(result["cycle_type"] != "light"),
            )
            logger.info(
                "Standalone %s cycle: cache rebuilt (%dms)",
                result["cycle_type"], cache_result["duration_ms"],
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
# Freshness marker loop (issue #108)
# ---------------------------------------------------------------------------


async def run_freshness_loop(app_state) -> None:
    """Marker-based freshness loop — started as an asyncio task.

    Bootstraps the in-memory ``MarkerStore`` from the registry, then runs a
    dynamic readiness check on each (source, model) only when its marker's
    ``next_expected`` has passed.  Most ticks are no-ops and produce no I/O.
    """
    from weatherbrief.fetch.freshness import LOOP_INTERVAL
    from weatherbrief.fetch.freshness.markers import get_store
    from weatherbrief.fetch.freshness.sources import all_tracked_sources

    poll_seconds = int(LOOP_INTERVAL.total_seconds())
    logger.info("Freshness loop started (poll every %ds)", poll_seconds)
    store = get_store()
    await store.bootstrap(all_tracked_sources())
    await asyncio.sleep(_FRESHNESS_LOOP_STARTUP_DELAY_SECONDS)

    while True:
        try:
            await _run_freshness_check_once()
        except Exception:
            logger.error("Freshness loop cycle failed", exc_info=True)
        await asyncio.sleep(poll_seconds)


async def _run_freshness_check_once() -> None:
    """Run a single freshness-loop tick.

    For every (source, model) marker:
    - If ``next_expected`` hasn't elapsed yet, just bump ``last_check``
      so the heartbeat stays fresh — no I/O.  Without this, sources whose
      next-expected is hours away would have stale heartbeats and force
      every freshness HTTP call into the inline-fallback (sync I/O on the
      event loop) until the loop got around to checking them.
    - Otherwise, run the dynamic ``check_source`` (offloaded to a thread)
      and either advance the marker or record a slip.
    """
    from weatherbrief.fetch.freshness.markers import get_store
    from weatherbrief.fetch.freshness.sources import all_tracked_sources, check_source

    store = get_store()
    now = datetime.now(timezone.utc)
    for source, model in all_tracked_sources():
        marker = store.get_sync(source, model)
        if marker is None:
            continue
        if now < marker.next_expected:
            await store.mark_check(source, model, now=now)
            continue
        observed = await asyncio.to_thread(check_source, source, model)
        if observed is None:
            await store.mark_check(source, model, now=now)
            continue
        await store.update(
            source, model, observed.init, now=now,
            published_at=observed.published_at,
            data_end=observed.data_end,
        )


# ---------------------------------------------------------------------------
# Hewson precompute loop
# ---------------------------------------------------------------------------


async def run_hewson_precompute_loop(app_state) -> None:
    """Fire Hewson diagnostic precompute at fixed UTC hours.

    Runs at ``_HEWSON_SAMPLE_HOURS_UTC`` (06Z / 18Z by default), chosen to
    sit ~6 h after each ``00Z/12Z`` init (Open-Meteo has all 3 models
    published by then) and ~1 h before the 07Z/19Z standalone forecast-fetch
    cycles (avoids CPU/network overlap). See
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


# ---------------------------------------------------------------------------
# GRIB pre-cache loop (issue #126)
# ---------------------------------------------------------------------------


async def run_grib_precache_loop(app_state) -> None:
    """Watch MarkerStore; pre-cache each new main-cycle ICON-EU/GFS run.

    Event-driven: piggybacks on the freshness loop's marker advancement.
    When a marker advances to a main-cycle init (00/06/12/18 Z) we pre-fetch
    the 9 ICON-EU pressure-level variables × the 64 forecast hours covering
    the ``/maps.html`` D-0..D-3 controls (and the GFS equivalent). Bytes
    land in the shared GRIB byte-range cache so flight briefings overlapping
    the same window also benefit.

    Disable via ``WB_GRIB_PRECACHE_ENABLED=false`` (default in dev).
    """
    from weatherbrief.fetch.grib.precache import (
        MAIN_CYCLE_HOURS,
        precache_gfs_run,
        precache_icon_eu_run,
    )
    from weatherbrief.fetch.freshness.markers import get_store

    logger.info(
        "GRIB pre-cache loop started (poll every %ds)",
        _GRIB_PRECACHE_POLL_SECONDS,
    )
    await asyncio.sleep(_GRIB_PRECACHE_STARTUP_DELAY_SECONDS)

    last_done: dict[str, str] = {}  # source_key -> "YYYYMMDD_HHz"
    targets = [
        ("icon_eu:dwd", "icon_eu", precache_icon_eu_run),
        ("gfs:noaa", "gfs", precache_gfs_run),
    ]
    main_cycles = set(MAIN_CYCLE_HOURS)

    while True:
        try:
            store = get_store()
            for source_key, model, fn in targets:
                marker = store.get_sync(source_key, model)
                if marker is None or marker.init.hour not in main_cycles:
                    continue
                key = marker.init.strftime("%Y%m%d_%Hz")
                if last_done.get(source_key) == key:
                    continue
                logger.info(
                    "Pre-caching %s %s for airport-profile",
                    source_key, key,
                )
                stats = await asyncio.to_thread(fn, marker.init)
                logger.info(
                    "Pre-cache %s %s done: %s",
                    source_key, key, stats,
                )
                last_done[source_key] = key
        except Exception:
            logger.error("GRIB pre-cache loop cycle failed", exc_info=True)
        await asyncio.sleep(_GRIB_PRECACHE_POLL_SECONDS)


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


# ---------------------------------------------------------------------------
# Usage analytics: daily rollup + weekly digest
# ---------------------------------------------------------------------------


async def run_analytics_rollup_loop(app_state) -> None:
    """Daily rollup of yesterday + raw-event retention purge."""
    logger.info(
        "Analytics rollup loop started (every %ds)",
        _ANALYTICS_ROLLUP_INTERVAL_SECONDS,
    )
    await asyncio.sleep(_ANALYTICS_ROLLUP_STARTUP_DELAY_SECONDS)

    while True:
        try:
            await asyncio.to_thread(_run_analytics_rollup_once)
        except Exception:
            logger.error("Analytics rollup cycle failed", exc_info=True)
        await asyncio.sleep(_ANALYTICS_ROLLUP_INTERVAL_SECONDS)


def _run_analytics_rollup_once() -> None:
    from weatherbrief.analytics.rollup import run_rollup_and_retention

    db = SessionLocal()
    try:
        run_rollup_and_retention(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def run_analytics_digest_loop(app_state) -> None:
    """Weekly digest — fires Monday at the configured UTC hour.

    Log-only for v1 (no email). Pure read against rollup tables, so it
    survives raw-event retention.
    """
    target_hour = int(os.environ.get("ANALYTICS_DIGEST_HOUR_UTC", "8"))
    logger.info(
        "Analytics digest loop started (Mondays at %02d:00 UTC)", target_hour,
    )
    await asyncio.sleep(_ANALYTICS_DIGEST_STARTUP_DELAY_SECONDS)

    while True:
        now = datetime.now(timezone.utc)
        # Days until next Monday at target hour. Monday = weekday 0.
        days_ahead = (0 - now.weekday()) % 7
        next_run = now.replace(
            hour=target_hour, minute=0, second=0, microsecond=0,
        ) + timedelta(days=days_ahead)
        if next_run <= now:
            next_run += timedelta(days=7)
        wait_seconds = (next_run - now).total_seconds()
        logger.info(
            "Analytics digest: next emit at %s (in %.0fs)",
            next_run.isoformat(), wait_seconds,
        )
        await asyncio.sleep(wait_seconds)

        try:
            await asyncio.to_thread(_run_analytics_digest_once)
        except Exception:
            logger.error("Analytics digest cycle failed", exc_info=True)


def _run_analytics_digest_once() -> None:
    from weatherbrief.analytics.digest import build_and_emit_digest

    db = SessionLocal()
    try:
        build_and_emit_digest(db)
    finally:
        db.close()
