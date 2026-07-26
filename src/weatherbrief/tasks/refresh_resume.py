"""Resume briefing refreshes interrupted by a container restart (issue #499).

Refresh state is mirrored into ``briefing_refresh_jobs`` as it runs (see
``weatherbrief.storage.refresh_jobs``). The terminal write happens in the
refresh path's ``finally`` — which is exactly what does *not* run when the
process is killed (OOM, deploy, crash). So a non-terminal row at process boot
means "this refresh was interrupted".

The deployment runs a **single uvicorn worker**, so there is no second instance
that could still legitimately own such a row: no leases, no instance ids, no
distributed locking. Any non-terminal row present at boot is an orphan, full
stop.

For each orphan this pass decides between:

* **abandon** — the retry budget is spent, the flight is gone/departed/beyond
  coverage, the job was a pinned ``as_of_date`` backtest, or the refresh gate
  no longer wants a full run (the scheduler already re-briefed it, or no model
  has moved). Closing the row is the whole outcome; no compute is burned.
* **resume** — re-queue the refresh through the registry with
  ``triggered_by="resume"`` and ``attempt + 1``. Registering also makes
  ``idle_seconds()`` go to zero, so the discretionary GRIB warm loop yields to
  it exactly as it does for an interactive briefing (#490).

Interruptions burn an attempt regardless of cause: we deliberately do not try
to attribute the crash to the briefing. ``WB_REFRESH_MAX_ATTEMPTS`` (default 2
= the original run plus one resume) bounds how often a briefing that genuinely
does take the container down can do it again. Set it to 1 to disable resume.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.orm import Session

from flyfun_common.db import SessionLocal
from weatherbrief.db.models import BriefingRefreshJobRow, FlightRow
from weatherbrief.storage.refresh_jobs import (
    TERMINAL_STATUSES,
    finish_job,
    list_orphans,
)

logger = logging.getLogger(__name__)

#: Original run + one resume. Sized so a briefing that genuinely does take the
#: container down can only do it twice, while a single OOM or deploy never
#: silently loses a user's refresh.
DEFAULT_MAX_ATTEMPTS = 2

#: Past the scheduler's own 30s startup delay, so a flight the scheduler is
#: about to re-brief anyway has already been picked up and the gate check below
#: closes the orphan for free.
STARTUP_DELAY_SECONDS = 90


def resume_max_attempts() -> int:
    """``WB_REFRESH_MAX_ATTEMPTS`` — total attempts allowed per refresh."""
    raw = os.environ.get("WB_REFRESH_MAX_ATTEMPTS", "").strip()
    if not raw:
        return DEFAULT_MAX_ATTEMPTS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid WB_REFRESH_MAX_ATTEMPTS=%r — using %d", raw, DEFAULT_MAX_ATTEMPTS,
        )
        return DEFAULT_MAX_ATTEMPTS
    return max(1, value)


@dataclass(frozen=True)
class ResumeDecision:
    """What to do with one interrupted refresh."""

    action: Literal["resume", "abandon"]
    reason: str


def decide_resume(
    db: Session,
    job: BriefingRefreshJobRow,
    *,
    max_attempts: int | None = None,
    now: datetime | None = None,
) -> ResumeDecision:
    """Decide whether an interrupted refresh is worth re-running.

    Ordered cheapest-check-first; every abandon carries the reason that goes
    into ``last_error`` and the log line.
    """
    from weatherbrief.api.packs import (
        _build_data_status,
        _days_out_now,
        decide_refresh,
        pending_coverage_date,
    )
    from weatherbrief.storage.flights import _row_to_flight, list_packs

    limit = resume_max_attempts() if max_attempts is None else max_attempts

    # The interruption itself consumed this attempt.
    if job.attempt >= limit:
        return ResumeDecision(
            "abandon", f"attempt {job.attempt} of {limit} — retry budget spent",
        )

    # A pinned as_of_date is an admin backtest of the forecast as it stood on a
    # given day. Re-running it live would answer a different question, so it is
    # never resumed automatically.
    if job.as_of_date is not None:
        return ResumeDecision(
            "abandon", f"pinned as_of_date={job.as_of_date.isoformat()} backtest",
        )

    flight_row = db.get(FlightRow, job.flight_id)
    if flight_row is None:
        return ResumeDecision("abandon", "flight no longer exists")

    flight = _row_to_flight(flight_row)
    now = now or datetime.now(timezone.utc)
    if flight.departure_time <= now:
        return ResumeDecision("abandon", "flight has already departed")

    coverage_date = pending_coverage_date(flight, as_of=now)
    if coverage_date is not None:
        return ResumeDecision(
            "abandon", f"beyond the forecast horizon (coverage from {coverage_date})",
        )

    # The refresh gate is a free correctness check: if a full run is no longer
    # warranted — the scheduler already re-briefed this flight, or no model has
    # moved since — there is nothing to resume.
    packs = list_packs(db, job.flight_id)
    if packs:
        status = _build_data_status(packs[0], flight)
        decision = decide_refresh(status, _days_out_now(flight))
        if decision.mode != "full":
            return ResumeDecision(
                "abandon", f"refresh gate now says {decision.mode}: {decision.reason}",
            )

    return ResumeDecision("resume", f"attempt {job.attempt + 1} of {limit}")


def snapshot_orphans() -> list[int]:
    """Ids of every non-terminal job row, read once at process boot.

    Taken before the startup delay so refreshes started by *this* process can
    never be mistaken for orphans of the previous one.
    """
    db = SessionLocal()
    try:
        return [row.id for row in list_orphans(db)]
    finally:
        db.close()


async def reconcile_one(job_id: int, app_state) -> None:
    """Abandon or resume a single interrupted refresh."""
    from weatherbrief.api.packs import refresh_registry
    from weatherbrief.scheduler import _auto_refresh_one

    db = SessionLocal()
    try:
        job = db.get(BriefingRefreshJobRow, job_id)
        if job is None or job.status in TERMINAL_STATUSES:
            return
        flight_id = job.flight_id
        attempt = job.attempt
        source = job.source
        user_id = job.user_id
        decision = decide_resume(db, job)

        if decision.action == "abandon":
            logger.warning(
                "Refresh resume: abandoning interrupted refresh for flight %s "
                "(attempt %d, pack=%s) — %s",
                flight_id, attempt, job.pack_path, decision.reason,
            )
            finish_job(
                db, job_id, "abandoned",
                error=f"interrupted by process restart; {decision.reason}",
            )
            db.commit()
            return

        # Close the interrupted row before re-queuing so the post-mortem keeps
        # one row per attempt rather than a mutating counter.
        finish_job(
            db, job_id, "failed",
            error=f"interrupted by process restart; resumed as {decision.reason}",
        )
        db.commit()
        if user_id is None:
            flight_row = db.get(FlightRow, flight_id)
            user_id = flight_row.user_id if flight_row else None
    finally:
        db.close()

    if user_id is None:
        logger.warning("Refresh resume: no user for flight %s — skipping", flight_id)
        return

    logger.warning(
        "Refresh resume: re-running interrupted refresh for flight %s (%s)",
        flight_id, decision.reason,
    )
    entry = refresh_registry.try_register(
        flight_id, triggered_by="resume", user_id=user_id,
        source=source, attempt=attempt + 1,
    )
    if entry is None:
        logger.info(
            "Refresh resume: %s already has a live refresh — nothing to resume",
            flight_id,
        )
        return

    # Own session for the duration: _auto_refresh_one reads attributes off the
    # FlightRow while it runs, so the row must stay attached to a live session
    # (same shape as the scheduler's auto-refresh cycle).
    run_db = SessionLocal()
    try:
        flight_row = run_db.get(FlightRow, flight_id)
        if flight_row is None:
            refresh_registry.mark_outcome(flight_id, "abandoned", "flight disappeared")
            return
        refresh_registry.set_refreshing(flight_id)
        await asyncio.to_thread(
            _auto_refresh_one, flight_row, app_state, user_id, triggered_by="resume",
        )
        refresh_registry.mark_outcome(flight_id, "succeeded")
        logger.info("Refresh resume: completed for flight %s", flight_id)
    except Exception as exc:
        refresh_registry.mark_outcome(flight_id, "failed", str(exc))
        logger.error("Refresh resume failed for flight %s", flight_id, exc_info=True)
    finally:
        refresh_registry.unregister(flight_id)
        run_db.close()


async def run_refresh_resume(app_state) -> None:
    """One-shot boot-time reconciliation task, started from the app lifespan."""
    try:
        orphans = await asyncio.to_thread(snapshot_orphans)
    except Exception:
        logger.error("Refresh resume: could not read interrupted refreshes", exc_info=True)
        return

    if not orphans:
        logger.info("Refresh resume: no interrupted refreshes at boot")
        return

    logger.warning(
        "Refresh resume: %d refresh(es) were in flight when the previous "
        "process died — reconciling in %ds",
        len(orphans), STARTUP_DELAY_SECONDS,
    )
    await asyncio.sleep(STARTUP_DELAY_SECONDS)

    for job_id in orphans:
        try:
            await reconcile_one(job_id, app_state)
        except Exception:
            logger.error("Refresh resume: reconciling job %d failed", job_id, exc_info=True)
