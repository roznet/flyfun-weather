"""Serial, server-driven trip refresh (issue #602, checklist item 8).

Why this is not a fan-out
-------------------------
Enqueueing N legs at once is not merely impolite to other users — it fails
outright. ``_RefreshRegistry.try_register`` **rejects** rather than queues:
``MAX_PER_USER = 2`` refuses the third leg with ``UserQueueLimitError``, and
``_refresh_executor`` is a ``ThreadPoolExecutor(max_workers=2)`` for the whole
process, so the two legs that *were* admitted would occupy every refresh slot
on the server for the duration.

So: **one leg in flight at a time**. Admit exactly one into the registry,
submit the next only when the previous finishes. That

* never approaches ``MAX_PER_USER``, so no leg is ever refused;
* always leaves one of the two executor slots free, so other users interleave —
  a trip refresh costs its owner latency, never anyone else's turn;
* makes "leg 2 of 3" the natural progress readout;
* keeps every leg an ordinary durable refresh job, so a container restart
  resumes it through the existing ``decide_resume`` path with no trip-specific
  recovery.

It is deliberately **server-side**: a closed tab would otherwise strand a trip
half-refreshed. The driver state is small — the run id plus the ordered leg
list, what is pending, and per-leg outcomes — and lives on the trip row.

``triggered_by="trip"`` is a *capped* trigger. Adding it to
``UNCAPPED_TRIGGERS`` is the tempting one-liner and is precisely the change
that would let one user's trip monopolise both slots. The caps are the
protection, not the obstacle.

Where advancement happens
-------------------------
Notification coalescing hangs off ``api/packs.py::_notify_refresh_complete``,
the single post-commit notify gate — one seam for both jobs, as designed.
*Advancement*, though, runs from this module's own per-leg completion handler:
the notify gate is only reached by a leg that succeeded, and a chain that
stopped dead because leg 2 raised would be worse than one that reports leg 2 as
failed and carries on.
"""

from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from flyfun_common.db import SessionLocal
from sqlalchemy.orm import Session

from weatherbrief.db.models import FlightTripRow
from weatherbrief.storage import trips as trip_storage

if TYPE_CHECKING:  # imported lazily at runtime — api.trips imports this module
    from weatherbrief.api.trips import TripRefreshStatus

logger = logging.getLogger(__name__)

#: Legs in flight at once. Named rather than inlined: raising it adaptively when
#: the queue is otherwise idle is a legitimate later refinement, starting there
#: is not.
TRIP_REFRESH_CONCURRENCY = 1

#: Registry trigger class for trip legs. **Not** in ``UNCAPPED_TRIGGERS`` — see
#: the module docstring.
TRIP_TRIGGER = "trip"

#: A trip refresh older than this is treated as abandoned, so a process killed
#: mid-chain cannot leave the button disabled forever. Generous: a 12-leg trip
#: at ~5 min a leg is an hour of real work.
STALE_RUN_SECONDS = 3 * 3600

# Guards the read-modify-write of one trip's driver state. The state lives in
# the DB, but two leg completions could otherwise interleave between read and
# write within this process (single uvicorn worker, so process-local is enough).
_state_lock = threading.Lock()


class TripRefreshBusy(Exception):
    """A trip refresh is already in flight for this trip."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_run_id() -> str:
    return secrets.token_hex(8)


def _is_stale(row: FlightTripRow) -> bool:
    started = row.refresh_started_at
    if started is None:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (_now() - started).total_seconds() > STALE_RUN_SECONDS


def status(row: FlightTripRow) -> "TripRefreshStatus":
    """Current driver state for a trip, as the API reports it."""
    from weatherbrief.api.trips import TripRefreshStatus

    state = trip_storage.read_refresh_state(row)
    if not state:
        return TripRefreshStatus(trip_id=row.id, active=False)

    legs = state.get("legs") or []
    results = state.get("results") or {}
    pending = state.get("pending") or []
    current = state.get("current")
    # ``_finish`` clears ``refresh_id`` but leaves the results behind, so the
    # last poll after a chain lands still renders "2 of 3 legs had new data"
    # rather than blanking the moment the run ends.
    active = bool(row.refresh_id) and bool(pending or current) and not _is_stale(row)
    return TripRefreshStatus(
        trip_id=row.id,
        refresh_id=row.refresh_id,
        active=active,
        total=len(legs),
        completed=len(results),
        current_flight_id=current,
        results=results,
        message=_progress_message(legs, results, current),
    )


def _progress_message(legs: list[str], results: dict[str, str], current: str | None) -> str:
    """The "2 of 3 legs had new data" readout.

    The refresh gate skips legs with no new model run, and saying so is the
    point: without it a trip refresh that legitimately did almost nothing looks
    like a trip refresh that failed.
    """
    if current:
        position = len(results) + 1
        return f"Refreshing leg {position} of {len(legs)}…"
    if not results:
        return ""
    refreshed = sum(1 for value in results.values() if value == "succeeded")
    skipped = sum(1 for value in results.values() if value == "skipped")
    failed = sum(1 for value in results.values() if value in {"failed", "busy"})
    parts = [f"{refreshed} of {len(legs)} legs had new data"]
    if skipped:
        parts.append(f"{skipped} already current")
    if failed:
        parts.append(f"{failed} could not be refreshed")
    return "; ".join(parts) + "."


def remaining_leg_ids(db: Session, trip_id: str) -> list[str]:
    """Member legs still ahead, in chain order.

    Only the *remaining* legs are refreshed: re-running the pipeline for a leg
    already flown spends real money to answer a question nobody is asking.
    """
    from weatherbrief.api.trips import build_leg_inputs
    from weatherbrief.trips import summarize_trip

    members = trip_storage.trip_members(db, trip_id)
    summary = summarize_trip(trip_id, build_leg_inputs(db, members))
    return [leg.flight_id for leg in summary.legs if leg.state == "remaining"]


def start(db: Session, row: FlightTripRow, app_state, user_id: str) -> "TripRefreshStatus":
    """Begin a serial refresh of the trip's remaining legs.

    Returns immediately; the chain advances in the background. Raises
    :class:`TripRefreshBusy` when one is already running.

    The busy check, the state write **and its commit** all happen under
    ``_state_lock``. A plain check-then-write here is not safe: two concurrent
    presses (a double-click, a retry, web and iOS both firing) open independent
    sessions, both read a ``refresh_id`` of ``None``, both pass the check, and
    both submit — and since a submitted task claims whichever leg is at the head
    of ``pending``, the two would run *different legs of the same trip
    concurrently*, breaching ``TRIP_REFRESH_CONCURRENCY`` and taking both of the
    process's refresh slots for one user. Committing inside the lock is what
    makes the second caller's read see the first caller's write; a
    single uvicorn worker makes a process-local lock sufficient (the same
    assumption ``refresh-durability`` relies on).
    """
    from weatherbrief.api.trips import TripRefreshStatus

    with _state_lock:
        # Re-read inside the lock: ``row`` was loaded by an unlocked SELECT and
        # a concurrent caller may have written since.
        db.refresh(row)
        if row.refresh_id and not _is_stale(row):
            state = trip_storage.read_refresh_state(row)
            if state.get("pending") or state.get("current"):
                raise TripRefreshBusy(
                    "A refresh is already running for this trip. Wait for it to finish."
                )

        legs = remaining_leg_ids(db, row.id)
        if not legs:
            trip_storage.write_refresh_state(db, row, refresh_id=None, state=None)
            db.commit()
            return TripRefreshStatus(
                trip_id=row.id,
                active=False,
                message="No remaining legs to refresh.",
            )

        run_id = _new_run_id()
        trip_storage.write_refresh_state(
            db,
            row,
            refresh_id=run_id,
            state={"legs": legs, "pending": legs, "results": {}, "current": None,
                   "notices": {}, "user_id": user_id},
            started_at=_now(),
        )
        db.commit()

    # NOTE: the first leg is NOT submitted here. The worker opens its own
    # session, so it must not start until the run marker is committed —
    # otherwise it reads a trip row that does not yet carry it and exits
    # immediately. The endpoint calls ``kick()`` with the run id.
    return TripRefreshStatus(
        trip_id=row.id,
        refresh_id=run_id,
        active=True,
        total=len(legs),
        completed=0,
        message=f"Refreshing leg 1 of {len(legs)}…",
    )


def kick(trip_id: str, app_state, user_id: str, run_id: str) -> None:
    """Start the chain. Call **after** ``start`` has committed the run marker."""
    _submit_next(trip_id, app_state, user_id, run_id)


def _submit_next(trip_id: str, app_state, user_id: str, run_id: str) -> None:
    """Pop the next pending leg and run it on the shared refresh executor.

    One executor task **per leg** rather than one per chain: the slot is
    released between legs, so a single-flight refresh from another user can
    interleave instead of queueing behind an entire trip.

    ``run_id`` fences the task: a task belonging to a superseded run exits
    without claiming anything, so a stale submission can never put a second leg
    of the same trip in flight.
    """
    from weatherbrief.api.packs import _refresh_executor

    _refresh_executor.submit(_run_leg, trip_id, app_state, user_id, run_id)


def _claim_next(
    db: Session, trip_id: str, run_id: str,
) -> tuple[FlightTripRow | None, str | None]:
    """Move the head of ``pending`` into ``current`` and return it.

    Returns ``(None, None)`` when the run this task belongs to is no longer the
    trip's current one — the fence that keeps a superseded submission from
    claiming a leg alongside the live chain.
    """
    row = db.get(FlightTripRow, trip_id)
    if row is None or not row.refresh_id or row.refresh_id != run_id:
        return None, None
    state = trip_storage.read_refresh_state(row)
    pending = list(state.get("pending") or [])
    if not pending:
        return row, None
    flight_id = pending.pop(0)
    state["pending"] = pending
    state["current"] = flight_id
    trip_storage.write_refresh_state(
        db, row, refresh_id=row.refresh_id, state=state,
    )
    return row, flight_id


def _record_result(
    db: Session, trip_id: str, flight_id: str, outcome: str, run_id: str,
) -> dict:
    """Record one leg's outcome and clear ``current``. Returns the new state.

    Fenced on ``run_id`` for the same reason as ``_claim_next``: a task from a
    superseded run must not write into the live run's results.
    """
    row = db.get(FlightTripRow, trip_id)
    if row is None or not row.refresh_id or row.refresh_id != run_id:
        return {}
    state = trip_storage.read_refresh_state(row)
    results = dict(state.get("results") or {})
    results[flight_id] = outcome
    state["results"] = results
    state["current"] = None
    trip_storage.write_refresh_state(db, row, refresh_id=row.refresh_id, state=state)
    return state


def _run_leg(trip_id: str, app_state, user_id: str, run_id: str) -> None:
    """Refresh one leg, then advance the chain.

    Every exit path advances: a leg that failed, or that the registry refused,
    still hands the baton on. The alternative — a chain that stops dead on the
    first bad leg — hides exactly the leg the pilot most needs to see.
    """
    from weatherbrief.api.packs import (
        QueueFullError,
        UserQueueLimitError,
        refresh_registry,
    )
    from weatherbrief.db.models import FlightRow
    from weatherbrief.scheduler import _auto_refresh_one

    db = SessionLocal()
    flight_id: str | None = None
    outcome = "failed"
    claimed = False
    try:
        try:
            with _state_lock:
                row, flight_id = _claim_next(db, trip_id, run_id)
                db.commit()
            claimed = True
        except Exception:
            # Claiming is the one step whose failure would strand the run with
            # legs pending and nothing scheduled, so it is closed out here
            # rather than left to the advance block below.
            logger.error("Trip %s: could not claim the next leg", trip_id, exc_info=True)
            db.rollback()
            _finish(db, trip_id, user_id)
            return
        if row is None or not claimed:
            return
        if flight_id is None:
            _finish(db, trip_id, user_id)
            return

        flight_row = db.get(FlightRow, flight_id)
        if flight_row is None or flight_row.user_id != user_id:
            outcome = "failed"
            return

        try:
            entry = refresh_registry.try_register(
                flight_id, triggered_by=TRIP_TRIGGER, user_id=user_id,
                source="user",
            )
        except (QueueFullError, UserQueueLimitError) as exc:
            # Capped on purpose (see the module docstring). Report the leg as
            # busy and move on rather than blocking the chain behind a server
            # that is already saturated.
            logger.info("Trip %s: leg %s refused by the queue (%s)", trip_id, flight_id, exc)
            outcome = "busy"
            return
        if entry is None:
            outcome = "busy"
            return

        try:
            refresh_registry.set_refreshing(flight_id)
            ran = _auto_refresh_one(
                flight_row, app_state, user_id, triggered_by=TRIP_TRIGGER,
            )
            outcome = "succeeded" if ran else "skipped"
            refresh_registry.mark_outcome(
                flight_id,
                "succeeded" if ran else "skipped",
                None if ran else "refresh gate declined a full run",
            )
        except Exception as exc:  # noqa: BLE001 — one bad leg must not stop the chain
            outcome = "failed"
            refresh_registry.mark_outcome(flight_id, "failed", str(exc))
            logger.error("Trip %s: leg %s refresh failed", trip_id, flight_id, exc_info=True)
        finally:
            refresh_registry.unregister(flight_id)
    except Exception:
        # The executor swallows an escaping exception into a Future nobody
        # reads, so log it here — the advance below still runs either way.
        logger.error("Trip %s: leg %s raised", trip_id, flight_id, exc_info=True)
    finally:
        try:
            if flight_id is not None:
                with _state_lock:
                    state = _record_result(db, trip_id, flight_id, outcome, run_id)
                    db.commit()
                if state.get("pending"):
                    _submit_next(trip_id, app_state, user_id, run_id)
                else:
                    _finish(db, trip_id, user_id)
        except Exception:
            logger.error("Trip %s: advancing after %s failed", trip_id, flight_id, exc_info=True)
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Boot-time recovery
#
# Every leg is an ordinary durable refresh job, so ``tasks/refresh_resume.py``
# already resumes the *one* leg that was mid-flight when a container died. It
# knows nothing about trips, though, so without the pass below the rest of the
# chain never runs: the remaining pending legs are never submitted, and every
# per-leg notice gathered before and after the crash is stranded until
# ``STALE_RUN_SECONDS`` lapses and the next ``start()`` overwrites it.
# ---------------------------------------------------------------------------

#: Delay before the boot pass runs, so the leg-level resume in
#: ``tasks/refresh_resume.py`` gets first claim on anything it is recovering.
RESUME_STARTUP_DELAY_SECONDS = 90


def _orphaned_runs() -> list[tuple[str, str, str]]:
    """``(trip_id, run_id, user_id)`` for every trip with a run open at boot.

    Single uvicorn worker, so any non-NULL ``refresh_id`` at boot is by
    definition an orphan — the same reasoning ``snapshot_orphans`` uses for
    refresh jobs.
    """
    from sqlalchemy import select

    db = SessionLocal()
    try:
        rows = db.execute(
            select(FlightTripRow).where(FlightTripRow.refresh_id.isnot(None))
        ).scalars().all()
        return [(row.id, row.refresh_id, row.user_id) for row in rows]
    finally:
        db.close()


def _requeue_interrupted(trip_id: str, run_id: str) -> bool:
    """Put an interrupted ``current`` leg back at the head of ``pending``.

    Returns True when the chain still has work. The leg that was in flight is
    re-queued rather than dropped: the leg-level resume may or may not have
    finished it, and the refresh gate makes a redundant re-run a cheap no-op
    while a dropped leg would silently never be briefed.
    """
    db = SessionLocal()
    try:
        with _state_lock:
            row = db.get(FlightTripRow, trip_id)
            if row is None or row.refresh_id != run_id:
                return False
            state = trip_storage.read_refresh_state(row)
            pending = list(state.get("pending") or [])
            current = state.get("current")
            if current and current not in pending:
                pending.insert(0, current)
            state["pending"] = pending
            state["current"] = None
            trip_storage.write_refresh_state(
                db, row, refresh_id=run_id, state=state,
            )
            db.commit()
            return bool(pending)
    finally:
        db.close()


async def run_trip_refresh_resume(app_state) -> None:
    """One-shot boot pass: restart or close out trip refreshes killed mid-chain.

    Sibling of ``tasks/refresh_resume.run_refresh_resume`` and started from the
    same lifespan block. A chain with legs still pending is re-submitted (under
    its original run id, so the fence still holds); one with nothing left is
    closed out so its coalesced notification finally fires instead of being
    discarded three hours later.
    """
    import asyncio

    try:
        orphans = await asyncio.to_thread(_orphaned_runs)
    except Exception:
        logger.error("Trip refresh resume: could not read open runs", exc_info=True)
        return
    if not orphans:
        logger.info("Trip refresh resume: no trip refreshes open at boot")
        return

    logger.warning(
        "Trip refresh resume: %d trip refresh(es) were in flight when the "
        "previous process died — reconciling in %ds",
        len(orphans), RESUME_STARTUP_DELAY_SECONDS,
    )
    await asyncio.sleep(RESUME_STARTUP_DELAY_SECONDS)

    for trip_id, run_id, user_id in orphans:
        try:
            has_work = await asyncio.to_thread(_requeue_interrupted, trip_id, run_id)
            if has_work:
                logger.info("Trip refresh resume: restarting chain for %s", trip_id)
                _submit_next(trip_id, app_state, user_id, run_id)
            else:
                logger.info("Trip refresh resume: closing out %s", trip_id)
                await asyncio.to_thread(_finish_in_new_session, trip_id, user_id)
        except Exception:
            logger.error(
                "Trip refresh resume: reconciling %s failed", trip_id, exc_info=True,
            )


def _finish_in_new_session(trip_id: str, user_id: str) -> None:
    """``_finish`` on its own session — the boot pass holds none."""
    db = SessionLocal()
    try:
        _finish(db, trip_id, user_id)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scheduler coalescing window
#
# The auto-refresh loop drives its own (already sequential) execution, so it
# does not need the serial driver — but it does need the same coalescing, or a
# trip whose three legs all come due fires three pushes. These two helpers open
# a run the loop reports into, without the loop having to know anything about
# how a manual trip refresh is paced.
# ---------------------------------------------------------------------------


def open_scheduler_run(db: Session, due_rows: list) -> None:
    """Open a coalescing run for every auto-refresh-enabled trip in ``due_rows``.

    Skips a trip that already has a run in flight (a manual "Refresh trip"
    press mid-cycle owns the coalescing) and a trip with only one due leg
    (there is nothing to coalesce, and the per-leg notification is the honest
    one). Best-effort: a failure here degrades to per-leg notifications, which
    is noisier but never wrong.
    """
    try:
        by_trip: dict[str, list[str]] = {}
        for row in due_rows:
            if getattr(row, "trip_id", None):
                by_trip.setdefault(row.trip_id, []).append(row.id)
        for trip_id, leg_ids in by_trip.items():
            if len(leg_ids) < 2:
                continue
            trip_row = db.get(FlightTripRow, trip_id)
            if trip_row is None or not trip_row.auto_refresh:
                continue
            if trip_row.refresh_id and not _is_stale(trip_row):
                continue
            trip_storage.write_refresh_state(
                db,
                trip_row,
                refresh_id=_new_run_id(),
                state={
                    "legs": leg_ids, "pending": list(leg_ids), "results": {},
                    "current": None, "notices": {}, "user_id": trip_row.user_id,
                    "source": "scheduler",
                },
                started_at=_now(),
            )
        db.commit()
    except Exception:
        logger.warning("Opening scheduler coalescing runs failed", exc_info=True)
        db.rollback()


def note_leg_done(db: Session, trip_id: str, flight_id: str, outcome: str) -> None:
    """Report one leg finished into an open run; close the run when it is the last.

    Used by the auto-refresh loop, which owns its own pacing. Best-effort.
    """
    try:
        with _state_lock:
            row = db.get(FlightTripRow, trip_id)
            if row is None or not row.refresh_id:
                return
            state = trip_storage.read_refresh_state(row)
            if flight_id not in (state.get("legs") or []):
                return
            state["pending"] = [f for f in (state.get("pending") or []) if f != flight_id]
            results = dict(state.get("results") or {})
            results[flight_id] = outcome
            state["results"] = results
            state["current"] = None
            trip_storage.write_refresh_state(
                db, row, refresh_id=row.refresh_id, state=state,
            )
            db.commit()
            remaining = state["pending"]
            user_id = state.get("user_id") or row.user_id
        if not remaining:
            _finish(db, trip_id, user_id)
    except Exception:
        logger.warning(
            "Trip %s: recording scheduler leg %s failed", trip_id, flight_id, exc_info=True,
        )


# ---------------------------------------------------------------------------
# Notification coalescing
# ---------------------------------------------------------------------------


def active_run_for_flight(db: Session, flight_id: str) -> tuple[FlightTripRow, dict] | None:
    """The trip row + driver state when ``flight_id`` is a leg of a live run.

    Called from the single ``_notify_refresh_complete`` gate: a leg inside a
    trip refresh must not fire its own push, so the gate hands its decision here
    instead and one notification goes out when the chain lands.
    """
    from weatherbrief.db.models import FlightRow

    flight_row = db.get(FlightRow, flight_id)
    if flight_row is None or not flight_row.trip_id:
        return None
    trip_row = db.get(FlightTripRow, flight_row.trip_id)
    if trip_row is None or not trip_row.refresh_id or _is_stale(trip_row):
        return None
    state = trip_storage.read_refresh_state(trip_row)
    if flight_id not in (state.get("legs") or []):
        return None
    return trip_row, state


def record_leg_notice(
    db: Session,
    trip_row: FlightTripRow,
    flight_id: str,
    *,
    label: str,
    qualified: bool,
    assessment: str | None,
    outlook: str | None,
    worsened_message: str | None,
    badge: int,
) -> None:
    """Stash one leg's notification decision for the coalesced push."""
    with _state_lock:
        row = db.get(FlightTripRow, trip_row.id)
        if row is None or not row.refresh_id:
            return
        state = trip_storage.read_refresh_state(row)
        notices = dict(state.get("notices") or {})
        notices[flight_id] = {
            "label": label,
            "qualified": qualified,
            "assessment": assessment,
            "outlook": outlook,
            "worsened": worsened_message,
            "badge": badge,
        }
        state["notices"] = notices
        trip_storage.write_refresh_state(db, row, refresh_id=row.refresh_id, state=state)


def _leg_lines(state: dict) -> list[str]:
    """Per-leg summary lines, in the order the legs were refreshed."""
    notices = state.get("notices") or {}
    results = state.get("results") or {}
    lines: list[str] = []
    for flight_id in state.get("legs") or []:
        notice = notices.get(flight_id)
        outcome = results.get(flight_id)
        if notice:
            grade = notice.get("assessment") or (
                (notice.get("outlook") or "").replace("_", " ").lower() or "updated"
            )
            line = f"{notice.get('label') or flight_id}: {grade}"
            if notice.get("worsened"):
                line += f" ({notice['worsened']})"
        elif outcome == "skipped":
            line = f"{flight_id}: no new data"
        elif outcome in {"failed", "busy"}:
            line = f"{flight_id}: could not refresh"
        else:
            continue
        lines.append(line)
    return lines


def _finish(db: Session, trip_id: str, user_id: str) -> None:
    """Close out a run: fire the single coalesced notification, clear the state."""
    with _state_lock:
        row = db.get(FlightTripRow, trip_id)
        if row is None or not row.refresh_id:
            return
        state = trip_storage.read_refresh_state(row)
        trip_name = row.name or "Trip"
        # Keep the finished results visible for the status poll; only the run
        # marker and the pending list go, so the UI can render the final
        # "2 of 3 legs had new data" line before it next reloads.
        state["pending"] = []
        state["current"] = None
        state["finished_at"] = _now().isoformat()
        trip_storage.write_refresh_state(db, row, refresh_id=None, state=state)
        db.commit()

    # Regenerate the AI paragraph **once** per trip refresh rather than per leg:
    # N generations for one output is pure waste, and this is the same
    # coalescing seam the notification uses.
    try:
        _regenerate_ai_summary(db, trip_id, user_id)
    except Exception:
        logger.warning("Trip %s: AI summary regeneration failed", trip_id, exc_info=True)
        db.rollback()

    try:
        _send_coalesced(db, trip_id, trip_name, state, user_id)
    except Exception:
        logger.warning("Trip %s: coalesced notification failed", trip_id, exc_info=True)


def _regenerate_ai_summary(db: Session, trip_id: str, user_id: str) -> None:
    """Refresh the trip's Haiku paragraph after a completed chain refresh."""
    from weatherbrief.api.trips import build_trip_summary
    from weatherbrief.digest.trip_summary import ensure_trip_ai_summary

    trip = trip_storage.load_trip(db, trip_id, user_id)
    row = db.get(FlightTripRow, trip_id)
    if trip is None or row is None:
        return
    summary, members, leg_inputs = build_trip_summary(db, trip)
    ensure_trip_ai_summary(
        db, row, summary, members, user_id=user_id, force=True, leg_inputs=leg_inputs,
    )
    db.commit()


def _send_coalesced(
    db: Session, trip_id: str, trip_name: str, state: dict, user_id: str,
) -> None:
    """One push + one email for the whole chain, or nothing.

    Fires only if at least one leg's own WHEN decision qualified — the trip
    layer coalesces delivery, it never manufactures a notification the per-leg
    gate would have suppressed.
    """
    notices = state.get("notices") or {}
    qualifying = [n for n in notices.values() if n.get("qualified")]
    if not qualifying:
        logger.info("Trip %s: no leg qualified to notify", trip_id)
        return

    from weatherbrief.api.preferences import load_notify_prefs
    from weatherbrief.notify.dispatch import _base_url

    prefs = load_notify_prefs(db, user_id)
    lines = _leg_lines(state)
    badge = max((n.get("badge") or 0) for n in qualifying)

    # Headline the binding leg, not a count: "which leg decides this trip" is
    # the whole product value, and a notification is the one place a pilot reads
    # before opening anything.
    body = _headline_for(db, trip_id) or "; ".join(lines[:3])

    if prefs["notify_push"]:
        try:
            from weatherbrief.notify.push import send_trip_push

            send_trip_push(
                db, user_id,
                trip_id=trip_id, trip_name=trip_name,
                title=trip_name, body=body, badge=badge,
            )
        except Exception:
            logger.warning("Trip %s: push failed", trip_id, exc_info=True)

    if prefs["notify_email"]:
        try:
            from flyfun_common.db.models import UserRow
            from weatherbrief.notify.email import SmtpConfig, send_trip_email

            SmtpConfig.from_env()
            user = db.query(UserRow).filter(UserRow.id == user_id).first()
            if user and user.email:
                send_trip_email(
                    [user.email],
                    trip_name=trip_name,
                    subject=f"{trip_name} — briefings updated",
                    lines=[body, *lines],
                    trip_url=f"{_base_url()}/trip.html?id={trip_id}",
                )
        except (ValueError, ImportError):
            logger.debug("Trip %s: email not configured", trip_id)
        except Exception:
            logger.warning("Trip %s: email failed", trip_id, exc_info=True)


def _headline_for(db: Session, trip_id: str) -> str | None:
    """The deterministic binding-leg sentence for this trip, if computable."""
    try:
        from weatherbrief.api.trips import build_leg_inputs
        from weatherbrief.trips import summarize_trip

        members = trip_storage.trip_members(db, trip_id)
        if not members:
            return None
        return summarize_trip(trip_id, build_leg_inputs(db, members)).headline
    except Exception:
        logger.warning("Trip %s: headline computation failed", trip_id, exc_info=True)
        return None
