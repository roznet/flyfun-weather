"""Unified briefing-refresh notification dispatch.

Emitted **once** from the single post-commit sink
(``api/packs.py::_notify_refresh_complete``), called by each refresh path
*after* it commits the pack transaction, so one hook covers every refresh
path — auto (scheduler), in-app, and Siri/MCP — including the
``RefreshBriefingIntent`` loop (ios-app-briefing-notifications.md). Emitting
after commit means we never notify about a pack that could still roll back and
never hold the pack transaction open across SMTP/APNs I/O.

The **shared gate** (``notify_qualifies``) is channel- and trigger-agnostic —
scope + per-flight override + the change filter. It drives the badge and is the
base decision for both channels:

    if flight.notify_override == "mute":  stop
    elif flight.notify_override == "notify":  qualifies      # ALWAYS — bypasses scope AND change filter
    else:                                                    # follow global scope + change filter
        scope == "off"  → stop
        else (on)       → qualifies       # "all"; legacy "auto" also means on
        if change_only and not changed:  stop
    → advance the badge

The **per-channel trigger rule** then layers on top of a qualifying refresh
(ios-app-briefing-notifications.md → cross-cutting semantics #5):

    push  → fires on every qualifying refresh (the foregrounded client
            self-suppresses the banner, so "am I looking?" needs no server signal)
    email → fires only for a *non-user-present* refresh (scheduler / Siri / MCP /
            background), NEVER for the user's own in-app manual refresh — email
            can't self-suppress, and the user is already looking at the result.

Which refreshes are "user-present" is decided by ``triggered_by``: only a plain
in-app manual refresh is ``"user"``; Siri and MCP report their own source so
they still email (closing the Siri refresh-intent loop).

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
from weatherbrief.tasks.advise import ASSESSMENT_UNAVAILABLE

logger = logging.getLogger(__name__)

# GREEN < AMBER < RED — higher is worse (mirrors metar-taf category ranking).
# UNAVAILABLE is deliberately absent: it is not a rung on this ladder but the
# absence of one, so it never produces a "worsened" delta in either direction
# (`.get()` → None, and detect_change requires both ranks). Notification is
# suppressed for it outright — see notify_briefing_refresh.
_ASSESSMENT_RANK = {"GREEN": 0, "AMBER": 1, "RED": 2}


def notify_qualifies(
    *,
    notify_override: str,
    scope: str,
    change_only: bool,
    changed: bool,
) -> bool:
    """Shared gate: does this completion qualify to notify (and light the badge)?

    Channel- and trigger-agnostic — scope + per-flight override + the change
    filter. The caller combines this with *presence* (was the user watching the
    refresh's UI stream) to form the single WHEN decision that gates the badge
    and both channels.

    Per-flight override precedence, evaluated first:

    - ``mute`` → never, regardless of scope.
    - ``notify`` → **always**: every completion for this flight, bypassing both
      global ``scope`` and the ``change_only`` filter. This is the strong opt-in
      and the default applied when auto-refresh is enabled — it restores the
      pre-#366 "notify me whenever a new report is ready" behavior, which fires
      even when the assessment is unchanged but the detail moved.
    - ``default`` → follow global scope + the change filter. ``scope`` "off"
      silences these flights; any other value — "all", or legacy "auto" — is on.

    The manual-vs-automatic line is drawn by presence (in the caller), NOT here
    and NOT per-channel — so a refresh the user watched finish is suppressed even
    for a ``notify`` flight.
    """
    if notify_override == "mute":
        return False
    if notify_override == "notify":
        return True  # always — bypasses scope + change filter (see docstring)

    if scope == "off":
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
    present: bool,
) -> None:
    """Evaluate the notification decision for a completed refresh and dispatch.

    Called once per refresh from ``_notify_refresh_complete`` (after commit).
    Never raises — wrapped so a notification failure can't break a refresh.

    Two cleanly-separated axes:

    - **WHEN** — one channel-agnostic decision: the refresh qualifies
      (scope + per-flight override + change filter) AND the user is not
      ``present`` (actively watching the refresh's UI stream at completion).
      This single boolean gates the badge and both channels — no per-channel,
      per-trigger, or per-surface special-casing.
    - **HOW** — pure user preference: deliver on each enabled channel (email,
      push), independent of who / what / where triggered the refresh.

    ``present`` is computed by the caller from the live UI refresh stream (see
    ``api/packs.py``) — the same signal for web and iOS, so "don't notify me
    about a refresh I just watched finish" works identically on both.
    """
    try:
        from weatherbrief.api.preferences import load_notify_prefs
        from weatherbrief.notify.badge import compute_badge_count, record_notify_qualifying

        # #392: a briefing we could not assess is not news. It carries nothing a
        # pilot can act on — it says our data is missing, not that their weather
        # changed — so it stays out of push, email and the badge. The grey
        # UNAVAILABLE badge is there when they next open the flight. Deliberately
        # ahead of the WHEN gate: this holds regardless of scope or a per-flight
        # "always notify" override, because there is nothing to notify *about*.
        if (meta.assessment or "").upper() == ASSESSMENT_UNAVAILABLE:
            logger.info(
                "notify: skipping %s — assessment UNAVAILABLE (nothing to report)",
                flight.id,
            )
            return

        prefs = load_notify_prefs(db, user_id)
        changed, delta = detect_change(db, flight.id, meta)

        # WHEN: one decision, channel- and trigger-agnostic. A user actively
        # watching the refresh finish needs no notification (they saw it live).
        if present or not notify_qualifies(
            notify_override=flight.notify_override,
            scope=prefs["notify_scope"],
            change_only=prefs["notify_change_only"],
            changed=changed,
        ):
            return

        # Advance the badge (gated by the same single WHEN decision above) and
        # read the authoritative count for aps.badge.
        record_notify_qualifying(db, user_id, flight.id, meta.fetch_timestamp)
        badge = compute_badge_count(db, user_id)

        # HOW: pure channel preference — nothing about the trigger or surface.
        if prefs["notify_email"]:
            _send_email(db, user_id, flight, meta, pack_dir)
        if prefs["notify_push"]:
            _send_push(db, user_id, flight, meta, delta, badge)
    except Exception:
        logger.warning("notify: dispatch failed for %s", getattr(flight, "id", "?"), exc_info=True)
