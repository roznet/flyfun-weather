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
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from flyfun_common.db import SessionLocal
from weatherbrief.db.models import FlightRow
from weatherbrief.fetch.variables import is_beyond_forecast_horizon

if TYPE_CHECKING:
    from weatherbrief.fetch.freshness.markers import MarkerStore

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 600  # 10 minutes
_STARTUP_DELAY_SECONDS = 30
_PREFLIGHT_LEAD_HOURS = 2

# --- Model-update-aware email timing (issue #192) --------------------------
# When a flight's regular auto-refresh slot is scheduled to fire shortly
# *before* a fresh, horizon-extending model run lands, defer the send so the
# briefing rides the newer run instead of being stale-at-birth. Strictly
# bounded, only ever defers (firing earlier is never useful), and never applied
# on/near the day of the flight — timeliness wins there (the preflight slot
# already guarantees a final refresh near departure).
#
# "Big" run = ECMWF full-horizon 00/12Z delivery (168h, landing ~06:40/18:40
# UTC). The 06/18Z cycles reach only 90h, so waiting for them far out would
# give a *shorter* horizon — they are excluded by next_full_horizon_run().
# ECMWF covers Europe + US and is the horizon-extending primary for both, so it
# is used region-agnostically; region-aware multi-model selection (ICON-EU /
# ARPEGE / GFS) is a documented future extension.
_MODEL_UPDATE_SOURCE = "ecmwf:direct"
_MODEL_UPDATE_MODEL = "ecmwf"
# Only defer when the regular slot is at least this many calendar days before
# the flight. On the day of / day before, timeliness beats freshness.
_MODEL_UPDATE_MIN_DAYS_OUT = 2
# A big run must be expected within this window *after* the regular slot for a
# defer to be worthwhile. Slots earlier than this already ride a fresh run.
_MODEL_UPDATE_WAIT_WINDOW = timedelta(hours=2)
# Buffer after the expected delivery so the run has actually landed + decoded
# before we build the brief (mirrors the forecast-fetch +15m offset).
_MODEL_UPDATE_MARGIN = timedelta(minutes=20)
# Hard cap on how far a slot may be deferred, so a slipping run never delays the
# email indefinitely. >= WAIT_WINDOW + MARGIN so a legitimate wait is never
# clipped, with headroom for a modest slip.
_MODEL_UPDATE_MAX_WAIT = timedelta(hours=2, minutes=30)
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

    from weatherbrief.fetch.freshness.markers import get_store
    store = get_store()
    defer_cache: dict[str, bool] = {}

    due: list[FlightRow] = []
    for row in rows:
        flight_start = _flight_start_dt(row)
        if flight_start is None:
            continue

        # Flight already started — no auto-refresh (manual still works)
        if now_utc >= flight_start:
            continue

        # Beyond the forecast horizon — no model reaches the date yet, so a
        # refresh would only build an empty pack. Skip until the flight crosses
        # into range; the next due slot after that generates the first briefing.
        if is_beyond_forecast_horizon(flight_start.date(), now_utc.date()):
            continue

        # Model-update-aware timing (issue #192) applies to the silent
        # NULL-default majority (Lever 2: snap out of the pre-delivery
        # dead-zone) and to explicit-hour users who opted in (Lever 1).
        apply_mu = row.auto_refresh_hour is None or _user_defers_for_model_update(
            db, row.user_id, defer_cache,
        )

        due_at = _next_due_at(
            row, flight_start, now_utc,
            apply_model_update=apply_mu, store=store,
        )
        if due_at is not None and now_utc >= due_at:
            due.append(row)

    return due


def _user_defers_for_model_update(
    db: Session, user_id: str, cache: dict[str, bool],
) -> bool:
    """Return the user's opt-in "defer for imminent model update" toggle.

    Cached per scheduler cycle so a batch of one user's flights costs a single
    preferences read. Defaults to ``False`` (current behaviour) on any error.
    """
    if user_id in cache:
        return cache[user_id]
    try:
        from weatherbrief.api.preferences import load_defer_email_for_model_update

        value = load_defer_email_for_model_update(db, user_id)
    except Exception:
        logger.warning("Could not load defer preference for %s", user_id, exc_info=True)
        value = False
    cache[user_id] = value
    return value


def _next_due_at(
    row: FlightRow,
    flight_start_dt: datetime,
    now_utc: datetime,
    *,
    apply_model_update: bool = False,
    store: "MarkerStore | None" = None,
) -> datetime | None:
    """Absolute UTC datetime when the next auto-refresh is due.

    Formula: ``min(next_regular, flight_start − PREFLIGHT_LEAD_HOURS)``

    *next_regular* is the next occurrence of the user's preferred hour
    (``auto_refresh_hour``, defaulting to ``target_time_utc − 1``).

    Always returns a time strictly before ``flight_start_dt`` (since
    *preflight* is ``flight_start − PREFLIGHT_LEAD_HOURS``).

    When ``apply_model_update`` is set (issue #192 — NULL-default snap or the
    opt-in account toggle), the *regular* term may be deferred a bounded amount
    so the briefing rides an imminent horizon-extending model run instead of
    being stale-at-birth. Only the regular term is touched; *preflight* is left
    untouched, and the deferral never applies within
    ``_MODEL_UPDATE_MIN_DAYS_OUT`` of the flight.
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

    if apply_model_update:
        regular = _defer_regular_for_model_update(
            regular, flight_start_dt, store,
        )

    # If we already refreshed at or after the pre-flight time, that slot is
    # satisfied — only the regular schedule matters from here on.
    last_refresh = row.last_auto_refresh_at
    if last_refresh is not None and last_refresh.tzinfo is None:
        last_refresh = last_refresh.replace(tzinfo=timezone.utc)
    if last_refresh is not None and last_refresh >= preflight:
        return regular

    return min(regular, preflight)


def _defer_regular_for_model_update(
    regular: datetime,
    flight_start_dt: datetime,
    store: "MarkerStore | None",
) -> datetime:
    """Defer the regular slot to ride an imminent full-horizon model run.

    Returns a time ``>= regular`` (deferring earlier is never useful), bounded
    by ``_MODEL_UPDATE_MAX_WAIT``. Returns ``regular`` unchanged when:

    - the slot is within ``_MODEL_UPDATE_MIN_DAYS_OUT`` of the flight (day-of /
      day-before — timeliness wins);
    - no full-horizon run lands within ``_MODEL_UPDATE_WAIT_WINDOW`` after the
      slot (it already rides a fresh run, or the next big run is far off);
    - the imminent big run is already in hand (e.g. it arrived early); or
    - the marker can't confirm the schedule (missing / stale heartbeat).

    Mirrors the forecast-fetch loop's bounded "wait for a slow marker before
    firing" pattern (``_wait_for_marker_freshness``), plus the day-of asymmetry.
    """
    # Day-of / day-before: never defer — the preflight slot owns this window.
    if (flight_start_dt.date() - regular.date()).days < _MODEL_UPDATE_MIN_DAYS_OUT:
        return regular

    from weatherbrief.fetch.freshness import LOOP_INTERVAL
    from weatherbrief.fetch.freshness.markers import get_store
    from weatherbrief.fetch.freshness.registry import (
        next_cycle_init_after,
        next_full_horizon_run,
    )

    target_init, target_delivery = next_full_horizon_run(_MODEL_UPDATE_SOURCE, regular)
    # Slot already rides a fresh run, or the next big run is beyond the window.
    if target_delivery - regular > _MODEL_UPDATE_WAIT_WINDOW:
        return regular

    if store is None:
        store = get_store()
    marker = store.get_sync(_MODEL_UPDATE_SOURCE, _MODEL_UPDATE_MODEL)
    if marker is None or marker.is_stale(LOOP_INTERVAL):
        return regular  # can't confirm an imminent delivery — don't gamble

    if marker.init >= target_init:
        return regular  # the big run is already in hand

    # Slip-aware: if the marker's next expected delivery is for the very cycle
    # we're waiting on, respect a late-running run (still capped by MAX_WAIT).
    delivery = target_delivery
    if next_cycle_init_after(_MODEL_UPDATE_SOURCE, marker.init) == target_init:
        delivery = max(delivery, marker.next_expected)

    deferred = min(delivery + _MODEL_UPDATE_MARGIN, regular + _MODEL_UPDATE_MAX_WAIT)
    # next_full_horizon_run guarantees delivery > regular and both margin/max-wait
    # are positive, so the deferred time is always strictly after the slot.
    logger.info(
        "Auto-refresh: deferring regular slot %s → %s for imminent %s %02dZ run",
        regular.isoformat(), deferred.isoformat(), _MODEL_UPDATE_MODEL, target_init.hour,
    )
    return deferred


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
        _build_data_status, _days_out_now, _finalize_refresh,
        _notify_refresh_complete, _prepare_refresh,
        decide_refresh, refresh_registry,
    )
    from weatherbrief.storage.flights import _row_to_flight, list_packs

    db = SessionLocal()
    try:
        flight = _row_to_flight(flight_row)

        # Tiered refresh gate: the scheduler applies the same
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

        route, fetch_ts, pack_path, options, model_metadata, resolved_as_of = _prepare_refresh(
            flight, db_path, user_id, flight_row.id, db=db, is_privileged=True,
        )

        from weatherbrief.fetch.grib import DecodePriority, set_decode_priority
        from weatherbrief.pipeline import execute_briefing

        # Auto-refresh decode work is SCHEDULED — below interactive user
        # refreshes / airport profiles, above background standalone cycles.
        # Runs in asyncio.to_thread, which copies the context, so this set is
        # isolated to the cycle and visible to enrich_forecasts.
        set_decode_priority(DecodePriority.SCHEDULED)

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
            as_of_time=resolved_as_of,
        )
        db.commit()

        # Briefing-refresh notifications (email + APNs push) fire from the shared
        # ``_notify_refresh_complete`` sink AFTER commit, so the same hook covers
        # auto / in-app / Siri / MCP refreshes without notifying about a pack that
        # could still roll back or holding the transaction open across network
        # I/O. A scheduled refresh normally has no one watching, so presence
        # (read inside the sink) is false and it notifies — unless a user happens
        # to be on that flight's briefing polling status as it lands.
        _notify_refresh_complete(
            db, flight, meta, pack_path, user_id=user_id,
        )

    finally:
        db.close()


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

    # Purge expired magic-link tokens and old consume-attempt rows.
    # flyfun-common ships the helper but does not schedule — consumer
    # apps own the cadence. 24h cutoff: tokens themselves expire in
    # 15 min, but we keep recent rows in-window so the rate limiter
    # has data to count against.
    try:
        from flyfun_common.auth.magic_link import purge_expired_magic_link_tokens

        db = SessionLocal()
        try:
            deleted = purge_expired_magic_link_tokens(db, older_than_hours=24)
            db.commit()
            if deleted:
                logger.info("Purged %d expired magic-link tokens", deleted)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception:
        logger.error("Magic-link token purge failed", exc_info=True)

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
            await _run_standalone_cycle_supervised(
                app_state,
                fetch_forecasts=True,
                score_observations=False,
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
            await _run_standalone_cycle_supervised(
                app_state,
                fetch_forecasts=False,
                score_observations=True,
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


# Hard ceiling on a standalone cycle subprocess. Generous — forecast cycles
# run ~70 min in production — but finite: an in-process cycle that hung used
# to wedge its thread invisibly forever; the subprocess gets killed and logged.
_STANDALONE_SUBPROCESS_TIMEOUT_S = int(
    os.environ.get("STANDALONE_SUBPROCESS_TIMEOUT_S", str(3 * 3600))
)


def _standalone_subprocess_enabled() -> bool:
    """Rollback switch for subprocess cycle isolation (issue #236).

    ``STANDALONE_SUBPROCESS=0`` reverts to running cycles in-process via
    ``asyncio.to_thread`` — same pattern as ``GRIB_DECODE_PRIORITY_ENABLED``.
    """
    return os.environ.get("STANDALONE_SUBPROCESS", "").strip() not in ("0", "false")


async def _run_standalone_cycle_supervised(
    app_state,
    *,
    fetch_forecasts: bool,
    score_observations: bool,
) -> None:
    """Run one standalone cycle, isolated in a child process when enabled.

    Why a subprocess (issue #236): the forecast cycle's transient working set
    (concurrent Open-Meteo chunk parsing + 46K sounding analyses) ratchets the
    long-lived uvicorn process's heap high-water mark. CPython/glibc never
    return that peak to the OS, so it became permanent anon memory (~3 GB)
    that the host pushed to swap. A short-lived child returns the entire peak
    on exit. Side benefits: a hung cycle is killable, and an OOM mid-cycle
    kills the disposable child (which raises its own oom_score_adj via
    ``--background``) instead of the web process.

    The child is the existing CLI — ``python -m weatherbrief.verify
    standalone`` — so the production code path and manual/worktree debugging
    are literally the same command. Results flow through the DB exactly as
    before; ``--with-rollup`` moves the post-cycle rollup + cache rebuild
    into the child too, keeping their memory out of the parent as well.
    """
    db_path = getattr(app_state, "db_path", "")
    if not db_path:
        logger.warning("Standalone cycle: no AIRPORTS_DB configured")
        return

    if not _standalone_subprocess_enabled():
        await asyncio.to_thread(
            _run_standalone_once, app_state, fetch_forecasts, score_observations,
        )
        return

    if fetch_forecasts and score_observations:
        cycle_type, mode_flag = "full", None
    elif fetch_forecasts:
        cycle_type, mode_flag = "forecast", "--forecast-only"
    else:
        cycle_type, mode_flag = "light", "--light"

    cmd = [
        sys.executable, "-m", "weatherbrief.verify", "standalone",
        "--with-rollup", "--background",
    ]
    if mode_flag:
        cmd.append(mode_flag)

    # The child needs no decode parallelism: it has a whole process to itself
    # and exits after one cycle. Inline decode (workers=0) avoids spawning a
    # second decode pool inside the cgroup next to the parent's.
    env = {**os.environ, "GRIB_DECODE_WORKERS": "0"}

    launched_at = datetime.now(timezone.utc)
    t_start = time.monotonic()
    logger.info(
        "Standalone %s cycle: launching subprocess (%s)",
        cycle_type, " ".join(cmd[2:]),
    )
    # stdout/stderr inherited — the child's log lines flow straight to the
    # container's log stream alongside the parent's.
    proc = await asyncio.create_subprocess_exec(*cmd, env=env)

    error_message: str | None = None
    try:
        returncode = await asyncio.wait_for(
            proc.wait(), timeout=_STANDALONE_SUBPROCESS_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        error_message = (
            f"standalone {cycle_type} subprocess exceeded "
            f"{_STANDALONE_SUBPROCESS_TIMEOUT_S}s, killed"
        )
        logger.error(error_message)
        try:
            await _terminate_subprocess(proc)
        except asyncio.CancelledError:
            # Cancelled (app shutdown) while waiting out the SIGTERM grace
            # period. A sibling `except CancelledError` would not catch this
            # (each try matches once), and another awaiting cleanup would
            # just re-open the race — escalate synchronously and re-raise.
            if proc.returncode is None:
                proc.kill()
            raise
        returncode = proc.returncode
    except asyncio.CancelledError:
        # App shutdown: don't leave the cycle running against a stopping
        # container. terminate() is synchronous, so there is no window for a
        # second cancellation; the child exits on SIGTERM unreaped (the OS /
        # container teardown collects it), and the cycle is idempotent
        # end-to-end (UPSERT/dup-check) so the next fire re-does the
        # truncated work.
        if proc.returncode is None:
            proc.terminate()
        raise

    if returncode == 0:
        return

    if error_message is None:
        error_message = (
            f"standalone {cycle_type} subprocess exited with code {returncode}"
        )
        logger.error(error_message)
    _ensure_failed_cycle_recorded(cycle_type, launched_at, t_start, error_message)


async def _terminate_subprocess(proc: asyncio.subprocess.Process) -> None:
    """Terminate a child, escalating to SIGKILL after a 30 s grace period."""
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


def _ensure_failed_cycle_recorded(
    cycle_type: str,
    launched_at: datetime,
    t_start: float,
    error_message: str,
) -> None:
    """Record a failed VerificationCycleRow unless the child already wrote one.

    The cycle records its own failures from inside the child
    (``_record_failed_cycle`` on the exception path), but a child killed by a
    signal — OOM kill, supervisor timeout — never reaches that path. Without
    this, such cycles would vanish from the ``verification_cycles`` audit
    trail, which the memory-anomaly baseline and admin dashboard rely on.
    """
    from weatherbrief.db.models import VerificationCycleRow
    from weatherbrief.tasks.standalone_verification import _record_failed_cycle

    source = f"standalone_{cycle_type}"
    try:
        db = SessionLocal()
        try:
            # Aware-UTC comparison param — same convention as the
            # _check_memory_anomaly baseline query on this table.
            existing = db.execute(
                select(VerificationCycleRow.id)
                .where(VerificationCycleRow.source == source)
                .where(VerificationCycleRow.started_at >= launched_at)
                .limit(1)
            ).scalar_one_or_none()
        finally:
            db.close()
    except Exception:
        logger.warning(
            "Could not check for existing %s cycle row", source, exc_info=True,
        )
        return
    if existing is not None:
        return
    _record_failed_cycle(
        launched_at, t_start, cycle_type, 0, error_message=error_message,
    )


def _run_standalone_once(
    app_state,
    fetch_forecasts: bool,
    score_observations: bool,
) -> None:
    """Execute a single standalone cycle (called in a thread).

    In-process fallback path (``STANDALONE_SUBPROCESS=0``) for
    :func:`_run_standalone_cycle_supervised`; the scheduled default runs the
    cycle in a subprocess instead.
    """
    db_path = getattr(app_state, "db_path", "")
    if not db_path:
        logger.warning("Standalone cycle: no AIRPORTS_DB configured")
        return

    # Standalone fetch/verification is the lowest-priority decode workload, so
    # a concurrent user refresh or auto-refresh always takes the next freed
    # worker slot ahead of it. Runs in asyncio.to_thread (context is copied),
    # so this set is isolated to the cycle. The explicit BACKGROUND on the
    # standalone-verification _dispatch_decode calls is belt-and-suspenders for
    # any decode that runs off this context.
    from weatherbrief.fetch.grib import DecodePriority, set_decode_priority
    set_decode_priority(DecodePriority.BACKGROUND)

    from weatherbrief.tasks.airport_watchlist import (
        get_configs_dir,
        load_watchlist_with_coords,
    )
    from weatherbrief.tasks.standalone_verification import (
        run_post_cycle_tasks,
        run_standalone_cycle,
    )

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

    # Daily-stats rollup + dashboard cache rebuild. Shared with the CLI's
    # --with-rollup so the subprocess path behaves identically.
    run_post_cycle_tasks(db_path, result["cycle_type"])


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
