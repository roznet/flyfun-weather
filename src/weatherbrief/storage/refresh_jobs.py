"""Durable briefing-refresh job rows — CRUD plus best-effort write-through.

Two layers, deliberately separated:

* the ``*_job`` functions take a caller-supplied :class:`~sqlalchemy.orm.Session`
  and behave like any other storage module (flush, let the caller commit);
* the ``record_*`` helpers open their own short-lived session, commit, and
  **swallow every exception**. They are called from the in-memory refresh
  registry, which runs on pipeline worker threads with no session in hand. A DB
  hiccup must never fail a refresh — durability here is a diagnostic and a
  resume hint, not a correctness invariant.

See ``designs/refresh-durability.md`` and issue #499.
"""

from __future__ import annotations

import logging
from datetime import date as date_t, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from flyfun_common.db import SessionLocal
from weatherbrief.db.models import BriefingRefreshJobRow

logger = logging.getLogger(__name__)

#: Statuses that mean the job is done. A row in any other status at process
#: boot was interrupted (single uvicorn worker — nothing else can own it).
TERMINAL_STATUSES = frozenset(BriefingRefreshJobRow.TERMINAL)


# ---------------------------------------------------------------------------
# Session-taking CRUD
# ---------------------------------------------------------------------------


def create_job(
    db: Session,
    *,
    flight_id: str,
    user_id: str | None = None,
    triggered_by: str = "user",
    source: str | None = None,
    as_of_date: date_t | None = None,
    attempt: int = 1,
) -> BriefingRefreshJobRow:
    """Insert a ``queued`` job row and return it (flushed, not committed)."""
    row = BriefingRefreshJobRow(
        flight_id=flight_id,
        user_id=user_id,
        triggered_by=triggered_by,
        source=source,
        as_of_date=as_of_date,
        status="queued",
        attempt=attempt,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


def mark_running(db: Session, job_id: int) -> None:
    """Move a job to ``running`` and stamp ``started_at``."""
    row = db.get(BriefingRefreshJobRow, job_id)
    if row is None or row.status in TERMINAL_STATUSES:
        return
    row.status = "running"
    now = datetime.now(timezone.utc)
    row.started_at = now
    row.heartbeat_at = now
    db.flush()


def touch_heartbeat(db: Session, job_id: int, stage: str | None = None) -> None:
    """Bump ``heartbeat_at`` (and the last-seen pipeline stage)."""
    row = db.get(BriefingRefreshJobRow, job_id)
    if row is None or row.status in TERMINAL_STATUSES:
        return
    row.heartbeat_at = datetime.now(timezone.utc)
    if stage:
        row.stage = stage
    db.flush()


def set_pack_path(db: Session, job_id: int, pack_path: str) -> None:
    """Record the pack directory this job is writing into.

    Set as soon as the directory is created, so a killed refresh leaves a row
    pointing at its orphan artifacts instead of an untraceable directory.
    """
    row = db.get(BriefingRefreshJobRow, job_id)
    if row is None:
        return
    row.pack_path = pack_path[:512]
    db.flush()


def finish_job(
    db: Session,
    job_id: int,
    status: str,
    *,
    error: str | None = None,
) -> None:
    """Close a job with a terminal ``status`` (idempotent)."""
    row = db.get(BriefingRefreshJobRow, job_id)
    if row is None or row.status in TERMINAL_STATUSES:
        return
    row.status = status
    row.finished_at = datetime.now(timezone.utc)
    if error:
        row.last_error = error[:2000]
    db.flush()


def list_orphans(db: Session) -> list[BriefingRefreshJobRow]:
    """Every non-terminal job row, oldest first.

    At process boot these are exactly the refreshes that were in flight when
    the previous process died.
    """
    stmt = (
        select(BriefingRefreshJobRow)
        .where(BriefingRefreshJobRow.status.notin_(sorted(TERMINAL_STATUSES)))
        .order_by(BriefingRefreshJobRow.created_at)
    )
    return list(db.execute(stmt).scalars().all())


def latest_job_for_flight(db: Session, flight_id: str) -> BriefingRefreshJobRow | None:
    """Newest job row for a flight, or None."""
    stmt = (
        select(BriefingRefreshJobRow)
        .where(BriefingRefreshJobRow.flight_id == flight_id)
        .order_by(BriefingRefreshJobRow.created_at.desc(), BriefingRefreshJobRow.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


# ---------------------------------------------------------------------------
# Best-effort write-through (own session, never raises)
# ---------------------------------------------------------------------------


def _best_effort(what: str, fn):
    """Run ``fn(db)`` in its own committed session, swallowing all failures."""
    db = None
    try:
        db = SessionLocal()
        result = fn(db)
        db.commit()
        return result
    except Exception:
        # Deliberately broad and non-fatal: refresh durability is diagnostic.
        logger.debug("refresh-job write-through (%s) failed", what, exc_info=True)
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def record_queued(
    flight_id: str,
    *,
    user_id: str | None = None,
    triggered_by: str = "user",
    source: str | None = None,
    as_of_date: date_t | None = None,
    attempt: int = 1,
) -> int | None:
    """Insert a queued job row; return its id (None if the write failed)."""

    def _do(db: Session) -> int:
        row = create_job(
            db,
            flight_id=flight_id,
            user_id=user_id,
            triggered_by=triggered_by,
            source=source,
            as_of_date=as_of_date,
            attempt=attempt,
        )
        return row.id

    return _best_effort("queued", _do)


def record_running(job_id: int) -> None:
    _best_effort("running", lambda db: mark_running(db, job_id))


def record_heartbeat(job_id: int, stage: str | None = None) -> None:
    _best_effort("heartbeat", lambda db: touch_heartbeat(db, job_id, stage))


def record_pack_path(job_id: int, pack_path: str) -> None:
    _best_effort("pack_path", lambda db: set_pack_path(db, job_id, pack_path))


def record_finished(job_id: int, status: str, error: str | None = None) -> None:
    _best_effort("finished", lambda db: finish_job(db, job_id, status, error=error))
