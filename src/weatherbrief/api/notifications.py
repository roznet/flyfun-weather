"""Briefing badge & cross-surface seen-state endpoints.

- ``GET  /api/flights/badge`` — authoritative unseen count, for the app's
  foreground reconcile (mirrors ``/messages/status``).
- ``POST /api/flights/{id}/seen`` — mark a flight's briefing seen (web + app).
  The **web** calls this on briefing view; that is what decrements the app
  badge, via a silent badge-sync push to the user's iOS devices.

Registered on the ``/flights`` prefix *before* the main flights router so the
literal ``/flights/badge`` route wins over ``/flights/{flight_id}``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db

from weatherbrief.api.flights import _load_flight_or_404
from weatherbrief.notify.badge import compute_badge_count, mark_flight_seen

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/flights", tags=["notifications"])


class BadgeStatus(BaseModel):
    """Server-derived count of flights with an unseen briefing update."""

    count: int


@router.get("/badge", response_model=BadgeStatus)
def get_badge(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Return the current user's unseen-briefing badge count (authoritative)."""
    return BadgeStatus(count=compute_badge_count(db, user_id))


@router.post("/{flight_id}/seen", response_model=BadgeStatus)
def mark_seen(
    flight_id: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Mark a flight's briefing seen for the current user; return the new badge.

    Opening a flight's briefing (web or app) sets its last-seen pack to the
    current latest, clearing the flight from the badge. When this actually
    clears an unseen flight, fire a silent badge-sync push so the user's *other*
    iOS devices update too (e.g. read on the web → app badge drops).
    """
    # Any flight the user can view (404s on a private flight they don't own).
    _load_flight_or_404(db, flight_id, viewer_id=user_id)

    changed = mark_flight_seen(db, user_id, flight_id)
    db.flush()
    count = compute_badge_count(db, user_id)

    if changed:
        try:
            from weatherbrief.notify.push import send_silent_badge_push

            send_silent_badge_push(db, user_id, count)
        except Exception:
            logger.warning("Silent badge-sync push failed for %s", user_id, exc_info=True)

    return BadgeStatus(count=count)
