"""Ingest endpoint for the usage-analytics event stream.

Single endpoint, ``POST /api/events``, accepts a batch of events from the
client. The client sends the batch via ``navigator.sendBeacon`` so the
write never blocks UI; the server's job is to validate cheaply, schedule
a background write, and return ``202`` immediately.

No authentication is required. Events are anonymous by design — only an
``anon_id`` (browser-scoped UUID) and a ``session_id`` are sent. We do
*not* attach the logged-in ``user_id`` even if a session cookie is
present; see the design discussion in ``designs/usage-analytics.md`` for
the rationale.

Safeguards
----------
* Unknown event names are dropped (logged at INFO so noise stays out of
  the table).
* Client timestamps are clamped: anything more than 5 minutes in the
  future or 7 days in the past is rewritten to ``server_now``.
* Per-batch hard limits cap how much one client can submit per request.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from flyfun_common.db import SessionLocal, get_db
from sqlalchemy.orm import Session

from weatherbrief.analytics.enrich import upsert_briefing_dim, upsert_flight_dim
from weatherbrief.analytics.events import ALLOWED_EVENTS, Event
from weatherbrief.analytics.models import (
    AnalyticsEventRow,
    AnalyticsSessionRow,
)
from weatherbrief.db.models import BriefingPackRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["analytics"])

# Per-batch caps to keep a single bad client from hammering the table.
_MAX_EVENTS_PER_BATCH = 50
_MAX_PROPS_JSON_BYTES = 1_024

# Timestamp clamp window. Anything outside this is replaced with server_now.
_TS_FUTURE_TOLERANCE = timedelta(minutes=5)
_TS_PAST_TOLERANCE = timedelta(days=7)


class EventIn(BaseModel):
    """One event in the incoming batch.

    The client identifies briefings by ``(flight_id, briefing_ts)`` — the
    same composite the public API uses in URL paths. The ingest endpoint
    resolves that to the internal ``briefing_packs.id`` and stores both.
    Direct ``briefing_id`` is also accepted (handy for tests).
    """

    event: str = Field(max_length=64)
    ts: datetime | None = None
    briefing_id: int | None = None
    briefing_ts: datetime | None = None
    flight_id: str | None = Field(default=None, max_length=256)
    props: dict | None = None

    @field_validator("props")
    @classmethod
    def _props_size_cap(cls, v: dict | None) -> dict | None:
        if v is None:
            return None
        # Crude size guard. Real cardinality is enforced by code review,
        # not the API — but we don't want a single event to be a megabyte.
        if len(json.dumps(v, separators=(",", ":"))) > _MAX_PROPS_JSON_BYTES:
            raise ValueError("props JSON too large")
        return v


class EventBatchIn(BaseModel):
    """Batch payload posted to ``/api/events``."""

    anon_id: str = Field(min_length=8, max_length=36)
    session_id: str = Field(min_length=8, max_length=36)
    app_version: str | None = Field(default=None, max_length=32)
    events: list[EventIn] = Field(default_factory=list)


@router.post("", status_code=202)
def ingest_events(
    body: EventBatchIn,
    background: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Accept a batch of analytics events.

    Validates the batch synchronously (cheap), then schedules the actual
    write on FastAPI's BackgroundTasks so the response returns immediately.
    The background task opens its own DB session — request-scoped ``db``
    won't be valid after the response is sent.
    """
    if not body.events:
        return {"accepted": 0}

    # Cap batch size; drop the tail rather than 400, so a single oversized
    # batch doesn't lose us all the events.
    events = body.events[:_MAX_EVENTS_PER_BATCH]

    background.add_task(
        _persist_batch,
        anon_id=body.anon_id,
        session_id=body.session_id,
        app_version=body.app_version,
        events=[e.model_dump() for e in events],
        received_at=datetime.now(timezone.utc),
    )
    return {"accepted": len(events)}


def _persist_batch(
    *,
    anon_id: str,
    session_id: str,
    app_version: str | None,
    events: list[dict],
    received_at: datetime,
) -> None:
    """Write the batch in a dedicated DB session (background task)."""
    db = SessionLocal()
    try:
        _ensure_session_row(db, anon_id, session_id, received_at, app_version)
        for raw in events:
            try:
                _persist_one(db, anon_id, session_id, app_version, raw, received_at)
            except Exception:
                logger.warning(
                    "analytics: failed to persist event %s", raw.get("event"),
                    exc_info=True,
                )
        db.commit()
    except Exception:
        db.rollback()
        logger.error("analytics: batch persist failed", exc_info=True)
    finally:
        db.close()


def _ensure_session_row(
    db: Session,
    anon_id: str,
    session_id: str,
    started_at: datetime,
    app_version: str | None,
) -> None:
    """Insert a session row on first sight; idempotent."""
    if db.get(AnalyticsSessionRow, session_id) is not None:
        return

    # First time we see this anon_id at all? Mark the session.
    has_prior = db.scalar(
        select(AnalyticsSessionRow.session_id)
        .where(AnalyticsSessionRow.anon_id == anon_id)
        .limit(1)
    )
    db.add(
        AnalyticsSessionRow(
            session_id=session_id,
            anon_id=anon_id,
            started_at=started_at,
            is_first_session=has_prior is None,
            app_version=app_version,
        )
    )


def _persist_one(
    db: Session,
    anon_id: str,
    session_id: str,
    app_version: str | None,
    raw: dict,
    received_at: datetime,
) -> None:
    name = raw.get("event") or ""
    if name not in ALLOWED_EVENTS:
        logger.info("analytics: dropping unknown event %r", name)
        return

    ts = _clamp_ts(raw.get("ts"), received_at)

    briefing_id = raw.get("briefing_id")
    briefing_ts = raw.get("briefing_ts")
    flight_id = raw.get("flight_id")

    # Resolve briefing_id from (flight_id, briefing_ts) if the client only
    # sent the composite. Best-effort: a missing pack leaves briefing_id
    # NULL but the event is still recorded (we don't want to lose events
    # over a race against retention).
    if briefing_id is None and flight_id and briefing_ts is not None:
        if isinstance(briefing_ts, str):
            try:
                briefing_ts = datetime.fromisoformat(
                    briefing_ts.replace("Z", "+00:00")
                )
            except ValueError:
                briefing_ts = None
        if isinstance(briefing_ts, datetime):
            if briefing_ts.tzinfo is None:
                briefing_ts = briefing_ts.replace(tzinfo=timezone.utc)
            pack = db.execute(
                select(BriefingPackRow)
                .where(BriefingPackRow.flight_id == flight_id)
                .where(BriefingPackRow.fetch_timestamp == briefing_ts)
                .limit(1)
            ).scalar_one_or_none()
            if pack is not None:
                briefing_id = pack.id

    # If the client sent a briefing_id directly, derive flight_id from it.
    if briefing_id is not None and flight_id is None:
        pack = db.get(BriefingPackRow, briefing_id)
        if pack is not None:
            flight_id = pack.flight_id

    # Trigger dimension upserts for the events that carry IDs.
    if name == Event.FLIGHT_CREATED.value and flight_id:
        upsert_flight_dim(db, flight_id)
    if name == Event.BRIEFING_OPENED.value and briefing_id is not None:
        upsert_briefing_dim(db, briefing_id)

    props = raw.get("props")
    db.add(
        AnalyticsEventRow(
            ts=ts,
            anon_id=anon_id,
            session_id=session_id,
            flight_id=flight_id,
            briefing_id=briefing_id,
            event=name,
            props=json.dumps(props, separators=(",", ":")) if props else None,
            app_version=app_version,
        )
    )


def _clamp_ts(raw_ts, server_now: datetime) -> datetime:
    """Trust client ``ts`` only when it's within a reasonable window.

    Client clocks drift, get rewound, sit asleep in tabs for days. Outside
    the tolerance window the client value is replaced with ``server_now``.
    """
    if raw_ts is None:
        return server_now
    if isinstance(raw_ts, str):
        try:
            raw_ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError:
            return server_now
    if not isinstance(raw_ts, datetime):
        return server_now
    if raw_ts.tzinfo is None:
        raw_ts = raw_ts.replace(tzinfo=timezone.utc)

    if raw_ts > server_now + _TS_FUTURE_TOLERANCE:
        return server_now
    if raw_ts < server_now - _TS_PAST_TOLERANCE:
        return server_now
    return raw_ts
