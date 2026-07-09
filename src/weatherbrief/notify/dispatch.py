"""Unified briefing-refresh notification dispatch.

Emitted **once** from the single refresh-finalize sink
(``api/packs.py::_persist_pack_finalize``) so one hook covers every refresh
path — auto (scheduler), in-app, and Siri/MCP — including the
``RefreshBriefingIntent`` loop (ios-app-briefing-notifications.md).

The gate is channel-agnostic; channels (email, push) are independent user
choices layered on top:

    if flight.notify_override == "mute":  stop
    elif flight.notify_override == "notify":  qualifies      # any completion
    else:                                                    # follow global scope
        scope == "all"  → qualifies
        scope == "auto" → qualifies iff triggered_by == "scheduler"
        scope == "off"  → stop
    if change_only and not changed:  stop
    → advance the badge; deliver on each ON channel

Everything is best-effort: a notification must NEVER break a refresh, so the
whole thing is wrapped and each channel is guarded independently.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from weatherbrief.db.models import BriefingPackRow
from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.models.observations import RefreshDelta

logger = logging.getLogger(__name__)

# GREEN < AMBER < RED — higher is worse (mirrors metar-taf category ranking).
_ASSESSMENT_RANK = {"GREEN": 0, "AMBER": 1, "RED": 2}


def notify_qualifies(
    *,
    notify_override: str,
    scope: str,
    change_only: bool,
    changed: bool,
    triggered_by: str,
) -> bool:
    """Pure gate: does this completion qualify to notify (and light the badge)?

    Independent of channels — the same decision drives both the badge advance
    and whether any channel delivers (per-channel on/off is applied after).
    """
    if notify_override == "mute":
        return False
    if notify_override == "notify":
        qualifies = True
    elif scope == "all":
        qualifies = True
    elif scope == "auto":
        qualifies = triggered_by == "scheduler"
    else:  # scope == "off"
        qualifies = False

    if not qualifies:
        return False
    if change_only and not changed:
        return False
    return True


def _prior_pack(
    db: Session, flight_id: str, current_ts: datetime
) -> BriefingPackRow | None:
    """The most recent pack strictly older than ``current_ts`` (the one this
    refresh replaces), for assessment-change detection.

    ``current_ts`` is ``meta.fetch_timestamp`` — a freshly-built *aware* UTC
    datetime, not one round-tripped from the DB. SQLite stores this column as
    naive text (no tz suffix), so an aware bound parameter won't match the stored
    format; strip tzinfo to compare like-for-like, mirroring ``update_pack_meta``
    and the other pack-timestamp comparisons in the codebase.
    """
    if current_ts.tzinfo is not None:
        current_ts = current_ts.replace(tzinfo=None)
    return (
        db.query(BriefingPackRow)
        .filter(
            BriefingPackRow.flight_id == flight_id,
            BriefingPackRow.fetch_timestamp < current_ts,
        )
        .order_by(BriefingPackRow.fetch_timestamp.desc())
        .first()
    )


def detect_change(
    db: Session, flight_id: str, meta: BriefingPackMeta
) -> tuple[bool, RefreshDelta | None]:
    """Compare this pack's assessment/outlook against the pack it replaces.

    Returns ``(changed, delta)``. The first briefing for a flight (no prior
    pack) counts as changed — it is genuinely new information — with no
    "worsened" delta. When the traffic light worsened (GREEN→AMBER→RED), the
    delta carries a short transition message for the push body.
    """
    prior = _prior_pack(db, flight_id, meta.fetch_timestamp)
    if prior is None:
        return True, None

    if (prior.assessment, prior.outlook) == (meta.assessment, meta.outlook):
        return False, None

    delta = None
    old_rank = _ASSESSMENT_RANK.get((prior.assessment or "").upper())
    new_rank = _ASSESSMENT_RANK.get((meta.assessment or "").upper())
    if old_rank is not None and new_rank is not None and new_rank > old_rank:
        delta = RefreshDelta(
            worsened=True,
            messages=[f"was {prior.assessment}"],
            computed_at=datetime.now(timezone.utc),
        )
    return True, delta


def _base_url() -> str:
    return os.environ.get("WEATHERBRIEF_BASE_URL", "https://weather.flyfun.aero")


def _send_email(
    db: Session, user_id: str, flight: Flight, meta: BriefingPackMeta, pack_dir: Path
) -> None:
    """Deliver the briefing email if SMTP/Resend is configured and the user has
    an email. Guarded — logs and skips on any failure."""
    try:
        from weatherbrief.notify.email import SmtpConfig, send_briefing_email

        SmtpConfig.from_env()  # validate config exists (Resend path also checks)
    except (ValueError, ImportError):
        logger.debug("notify: email not configured, skipping for %s", flight.id)
        return

    from flyfun_common.db.models import UserRow
    from weatherbrief.privacy import mask_email

    user = db.query(UserRow).filter(UserRow.id == user_id).first()
    if not user or not user.email:
        logger.debug("notify: no email for user %s, skipping", user_id)
        return
    try:
        send_briefing_email([user.email], flight, meta, pack_dir, base_url=_base_url())
        logger.info("notify: briefing email sent for %s to %s", flight.id, mask_email(user.email))
    except Exception:
        logger.warning("notify: briefing email failed for %s", flight.id, exc_info=True)


def _send_push(
    db: Session,
    user_id: str,
    flight: Flight,
    meta: BriefingPackMeta,
    delta: RefreshDelta | None,
    badge: int,
) -> None:
    """Deliver the APNs alert push. Guarded — logs and skips on any failure."""
    try:
        from weatherbrief.notify.push import send_briefing_push

        n = send_briefing_push(db, user_id, flight, meta, delta=delta, badge=badge)
        if n:
            logger.info("notify: briefing push sent for %s to %d device(s)", flight.id, n)
    except Exception:
        logger.warning("notify: briefing push failed for %s", flight.id, exc_info=True)


def notify_briefing_refresh(
    db: Session,
    flight: Flight,
    meta: BriefingPackMeta,
    pack_dir: Path,
    *,
    user_id: str,
    triggered_by: str,
    force_email: bool = False,
) -> None:
    """Evaluate the notification gate for a completed refresh and dispatch.

    Called once per refresh from ``_notify_refresh_complete`` (after commit).
    Never raises — wrapped so a notification failure can't break a refresh.

    ``force_email`` ensures exactly one email is sent for this refresh regardless
    of the scope/change gate — the legacy "email me when done" checkbox routed
    through this single gate so it can't double-send with the preference email.
    """
    try:
        from weatherbrief.api.preferences import load_notify_prefs
        from weatherbrief.notify.badge import compute_badge_count, record_notify_qualifying

        prefs = load_notify_prefs(db, user_id)
        changed, delta = detect_change(db, flight.id, meta)

        email_sent = False
        if notify_qualifies(
            notify_override=flight.notify_override,
            scope=prefs["notify_scope"],
            change_only=prefs["notify_change_only"],
            changed=changed,
            triggered_by=triggered_by,
        ):
            # Advance the badge state (independent of alert channel being on) and
            # read the authoritative count for aps.badge.
            record_notify_qualifying(db, user_id, flight.id, meta.fetch_timestamp)
            badge = compute_badge_count(db, user_id)

            if prefs["notify_email"]:
                _send_email(db, user_id, flight, meta, pack_dir)
                email_sent = True
            if prefs["notify_push"]:
                _send_push(db, user_id, flight, meta, delta, badge)

        # Legacy per-refresh "email me when done" — send once if the gate above
        # didn't already email for this refresh.
        if force_email and not email_sent:
            _send_email(db, user_id, flight, meta, pack_dir)
    except Exception:
        logger.warning("notify: dispatch failed for %s", getattr(flight, "id", "?"), exc_info=True)
