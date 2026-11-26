"""Debrief at window close (#1023).

Every verification cycle (10 min), after the live tick, each flight whose live
window closed within the last :data:`LOOKBACK` (landing plus
``LANDED_GRACE``, or the estimated arrival plus ``WB_LIVE_WINDOW_AFTER_H``;
``tasks/live_tick.live_window_end``) and has no stored report gets its debrief
report built once (``debriefs/report.report_for_flight``) and stored in
``flight_debrief_reports``: headline, interest, the questions asked. The row
is what ``debrief_ready`` means on ``/api/flights`` and what stops a second
build, so each flight is handled at most once and gets at most one push.

**Push** (one per flight, never retried). Only when all hold:

- the report says the flight is interesting and :func:`push_text` has a body
  (at least one question, or a headline that differs from the forecast);
- the flight was started (the app's Start, ``FlightProgress.start_fix``) and
  airborne (a take-off entered or inferred from the fixes): never for a
  cancelled, monitoring, never-started or never-airborne flight;
- the bell is not muted (the flight's, else its trip's), ``notify_push`` is
  on and the user has a device.

``WB_DEBRIEF_PUSH=1`` turns the send on (the row reads ``pending`` until the
send returns: ``sent`` when it reached at least one device, else
``failed``); unset, every decision is logged (``DEBRIEF_PUSH_WOULD_SEND`` /
``DEBRIEF_PUSH_SKIPPED``) and stored as ``shadow``. Leave the flag unset
until the Debrief section (#1024) is on both stores, so the push lands
somewhere.

A flight whose build raises gets no row and is retried each cycle until it
leaves :data:`LOOKBACK`, logged at error level.

The query's ``departure_time`` lower bound allows a flight up to 24 h long
(plus the after-window and the latest take-off delay): a longer one closes
after the query stopped looking and never gets a row here (its report is
still built on request).

:data:`LOOKBACK` bounds the first run after a deploy: flights that closed
before it never get a report row from here (their report is still built on
request by ``GET /debrief/report``).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from weatherbrief.db.models import BriefingPackRow, FlightDebriefReportRow, FlightRow

logger = logging.getLogger(__name__)

PUSH_ENV = "WB_DEBRIEF_PUSH"
#: A window that closed longer ago than this is not picked up.
LOOKBACK = timedelta(hours=6)
#: Push type the apps route on: opens the flight's Debrief section.
PUSH_TYPE = "debrief"


def push_enabled() -> bool:
    return os.environ.get(PUSH_ENV, "").strip().lower() in ("1", "true", "yes")


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def find_closed_flights(db: Session, now: datetime) -> list[tuple[FlightRow, object]]:
    """(flight, its ``FlightProgress``) for each flight with packs whose live
    window closed in ``(now - LOOKBACK, now]`` and has no report row yet."""
    from weatherbrief.analysis.flight_progress import FlightProgress
    from weatherbrief.storage.flights import _resolve_artifact_path
    from weatherbrief.tasks.live_layer import flight_dir_for_pack, load_progress_events
    from weatherbrief.tasks.live_tick import MAX_TAKEOFF_DELAY_H, live_window_end, live_window_hours

    _, after_h = live_window_hours()
    # The widest a window can be: a late take-off moves it back by up to
    # MAX_TAKEOFF_DELAY_H; durations are read per flight below.
    earliest = now - LOOKBACK - timedelta(hours=after_h + MAX_TAKEOFF_DELAY_H + 24)
    stmt = (
        select(FlightRow)
        .join(BriefingPackRow)
        .outerjoin(FlightDebriefReportRow, FlightDebriefReportRow.flight_id == FlightRow.id)
        .where(FlightRow.departure_time <= now, FlightRow.departure_time >= earliest)
        .where(FlightDebriefReportRow.flight_id.is_(None))
        .distinct()
    )
    out = []
    for row in db.execute(stmt).scalars().all():
        try:
            artifact = db.execute(
                select(BriefingPackRow.artifact_path)
                .where(BriefingPackRow.flight_id == row.id, BriefingPackRow.artifact_path.is_not(None))
                .order_by(BriefingPackRow.fetch_timestamp.desc())
                .limit(1)
            ).scalar()
            events = load_progress_events(flight_dir_for_pack(_resolve_artifact_path(artifact))) if artifact else ()
            dep = _utc(row.departure_time)
            end = live_window_end(dep, row.flight_duration_hours, after_h, events, now)
            if not (now - LOOKBACK < end <= now):
                continue
            out.append((row, FlightProgress.schedule(dep, row.flight_duration_hours, now, events)))
        except Exception:
            logger.warning("Debrief close: could not read flight %s", row.id, exc_info=True)
    return out


def skip_reason(db: Session, flight: FlightRow, report: dict, progress) -> str | None:
    """Why this flight gets no debrief push, or None (module docstring)."""
    from weatherbrief.debriefs.report import push_text
    from weatherbrief.storage.debriefs import get_debrief

    debrief = get_debrief(db, flight.id)
    if report.get("cancelled") or (debrief is not None and debrief.decision.value != "flown"):
        return "cancelled_or_monitoring"
    if push_text(report) is None:
        return "not_interesting"
    if progress is None or progress.start_fix is None:
        return "not_started"
    if progress.takeoff_at is None:
        return "not_airborne"
    override = flight.notify_override or "default"
    if override == "default" and getattr(flight, "trip_id", None):
        from weatherbrief.db.models import FlightTripRow

        trip = db.get(FlightTripRow, flight.trip_id)
        if trip is not None:
            override = trip.notify_override or "default"
    if override == "mute":
        return "muted"
    from weatherbrief.api.preferences import load_notify_prefs

    if not load_notify_prefs(db, flight.user_id).get("notify_push", False):
        return "push_off"
    from weatherbrief.notify.push import _load_devices

    if not _load_devices(db, flight.user_id):
        return "no_device"
    return None


def _route_title(flight: FlightRow) -> str:
    try:
        waypoints = json.loads(flight.waypoints_json or "[]")
    except (TypeError, ValueError):
        waypoints = []
    if len(waypoints) >= 2:
        return f"{waypoints[0]} → {waypoints[-1]}"
    return flight.route_name or "Your flight"


def build_payload(flight: FlightRow, body: str) -> dict:
    """"EGKB → EHRD · debrief" / "Two quick questions about the icing
    forecast."; ``type: debrief`` opens the Debrief section (#1024)."""
    return {
        "aps": {
            "alert": {"title": f"{_route_title(flight)} · debrief", "body": body},
            "sound": "default",
            "thread-id": flight.id,
        },
        "flight_id": flight.id,
        "type": PUSH_TYPE,
    }


def close_flight(db: Session, flight: FlightRow, progress, now: datetime) -> FlightDebriefReportRow:
    """Build, store and (maybe) push one flight's report. The row is
    committed before the push, so a failed send is never retried."""
    from weatherbrief.debriefs.report import REPORT_VERSION, push_text, report_for_flight

    report = report_for_flight(db, flight.id, now=now)
    summary = report.get("summary") or {}
    interest = report.get("interest") or {}
    questions = report.get("questions") or []
    reason = skip_reason(db, flight, report, progress)
    body = push_text(report)
    row = FlightDebriefReportRow(
        flight_id=flight.id,
        built_at=now,
        rules_version=REPORT_VERSION,
        status=report.get("status") or "unknown",
        headline=summary.get("headline"),
        differs_from_forecast=bool(summary.get("differs_from_forecast")),
        interesting=bool(interest.get("interesting")),
        reasons_json=json.dumps(interest.get("reasons") or []),
        questions_json=json.dumps([{"id": q["id"], "kind": q["kind"], "text": q["text"]} for q in questions]),
        push_reason=(body or "")[:300] or None,
        # ``pending`` until the send returns: a process that dies in between
        # leaves ``pending``, never a ``shadow`` it did not decide.
        push_outcome=f"skipped:{reason}" if reason else ("pending" if push_enabled() else "shadow"),
    )
    db.add(row)
    db.commit()
    # Past-flights record (#1031): freeze the forecast tags on the default
    # pack and record the report's outcome. Never fails the close.
    from weatherbrief.storage.flight_facts import safe_update_flight_facts

    safe_update_flight_facts(db, flight.id, now=now, freeze=True)
    db.commit()
    if reason is not None:
        logger.info("DEBRIEF_PUSH_SKIPPED flight=%s user=%s reason=%s", flight.id, flight.user_id, reason)
        return row
    logger.info("DEBRIEF_PUSH_WOULD_SEND flight=%s user=%s body=%r", flight.id, flight.user_id, body)
    if push_enabled():
        from weatherbrief.notify.push import send_debrief_push

        try:
            sent = send_debrief_push(db, flight.user_id, build_payload(flight, body))
            row.push_outcome = "sent" if sent else "failed"
            row.pushed_at = now if sent else None
        except Exception:
            logger.warning("Debrief push failed for flight %s", flight.id, exc_info=True)
            row.push_outcome = "failed"
        db.commit()
    return row


def run_debrief_close(db: Session, now: datetime | None = None) -> dict:
    """Handle every flight whose window just closed. Never raises per flight."""
    now = now or datetime.now(timezone.utc)
    flights = find_closed_flights(db, now)
    built = sent = shadow = 0
    for flight, progress in flights:
        try:
            row = close_flight(db, flight, progress, now)
            built += 1
            sent += row.push_outcome == "sent"
            shadow += row.push_outcome == "shadow"
        except Exception:
            db.rollback()
            # No row is stored, so the flight is tried again each cycle until
            # it leaves LOOKBACK: an error, not a warning, so a deterministic
            # failure stands out.
            logger.error("Debrief close failed for flight %s", flight.id, exc_info=True)
    if flights:
        logger.info("Debrief close: %d flight(s) closed, %d report(s) stored, %d push(es) sent, %d shadow",
                    len(flights), built, sent, shadow)
    return {"closed": len(flights), "built": built, "sent": sent, "shadow": shadow}


def debrief_summary(db: Session, flight_id: str) -> dict | None:
    """The stored report for the agents (``get_briefing``): headline, the
    open questions, how many were answered. None before window close."""
    from weatherbrief.storage.debriefs import get_report_row, load_answers

    row = get_report_row(db, flight_id)
    if row is None:
        return None
    asked = json.loads(row.questions_json or "[]")
    answered = load_answers(db, flight_id)
    built = _utc(row.built_at)
    return {
        "built_at": built.isoformat(),
        "headline": row.headline,
        "differs_from_forecast": bool(row.differs_from_forecast),
        "questions_open": [q for q in asked if q["id"] not in answered],
        "questions_answered": sum(1 for q in asked if q["id"] in answered),
    }
