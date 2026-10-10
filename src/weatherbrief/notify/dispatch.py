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

The single WHEN decision is that gate AND not *present* — the user was not
watching this refresh finish (``refresh_registry.is_watched``, read by the
caller; the same signal for web and iOS, #371). It gates the badge and both
channels alike. HOW is then pure user preference: email if ``notify_email``,
push if ``notify_push``, whoever or whatever triggered the refresh
(``triggered_by`` / ``?source=`` is usage attribution only).

The T-2h preflight auto-refresh is the exception: it sends the **flight-day
brief** (``notify_flight_day``, #753) instead of the ordinary notification,
whether or not a new pack was built, with ``notify_change_only`` ignored.

Everything is best-effort: a notification must NEVER break a refresh, so the
whole thing is wrapped and each channel is guarded independently.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel
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


def effective_notify_override(db: Session, flight: Flight) -> str | None:
    """The per-flight override to apply, with trip precedence: an explicit
    per-flight override wins, else the trip's, else None (the caller falls
    back to ``flight.notify_override``, i.e. the account scope)."""
    if not flight.trip_id or flight.notify_override != "default":
        return None
    from weatherbrief.db.models import FlightTripRow

    trip_row = db.get(FlightTripRow, flight.trip_id)
    return trip_row.notify_override if trip_row is not None else None


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


class NotifyOutcome(BaseModel):
    """What the WHEN gate decided for one completed refresh.

    Returned so a *coalescing* caller — the trip-refresh driver, which must fire
    one push for an N-leg chain rather than N — can run the identical decision
    per leg, suppress the per-leg delivery, and still know which legs would have
    notified and what to say about them.
    """

    qualified: bool = False
    badge: int = 0
    assessment: str | None = None
    outlook: str | None = None
    #: Short "was GREEN"-style transition line when the traffic light worsened.
    worsened_message: str | None = None


def notify_briefing_refresh(
    db: Session,
    flight: Flight,
    meta: BriefingPackMeta,
    pack_dir: Path,
    *,
    user_id: str,
    present: bool,
    override: str | None = None,
    deliver: bool = True,
) -> NotifyOutcome:
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

    ``override`` lets the caller supply the *effective* per-flight override
    instead of ``flight.notify_override``. Only the trip layer uses it, to apply
    the documented precedence — an explicit per-flight override wins, else the
    trip's, else the account scope — without teaching this module about trips.

    ``deliver=False`` runs the whole decision, including the badge advance, but
    sends nothing. The trip driver uses it to coalesce: the badge must still
    move per leg (it counts unopened *flights*), while the push and email fire
    once for the chain.
    """
    outcome = NotifyOutcome()
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
            return outcome

        prefs = load_notify_prefs(db, user_id)
        changed, delta = detect_change(db, flight.id, meta)

        # WHEN: one decision, channel- and trigger-agnostic. A user actively
        # watching the refresh finish needs no notification (they saw it live).
        if present or not notify_qualifies(
            notify_override=override or flight.notify_override,
            scope=prefs["notify_scope"],
            change_only=prefs["notify_change_only"],
            changed=changed,
        ):
            return outcome

        # Advance the badge (gated by the same single WHEN decision above) and
        # read the authoritative count for aps.badge.
        record_notify_qualifying(db, user_id, flight.id, meta.fetch_timestamp)
        badge = compute_badge_count(db, user_id)

        outcome = NotifyOutcome(
            qualified=True,
            badge=badge,
            assessment=meta.assessment,
            outlook=meta.outlook,
            worsened_message=(
                delta.messages[0] if delta and delta.worsened and delta.messages else None
            ),
        )
        if not deliver:
            # Coalescing caller: the decision and the badge stand, the channels
            # are its job. Nothing else about the gate changes.
            return outcome

        # HOW: pure channel preference — nothing about the trigger or surface.
        if prefs["notify_email"]:
            _send_email(db, user_id, flight, meta, pack_dir)
        if prefs["notify_push"]:
            _send_push(db, user_id, flight, meta, delta, badge)
    except Exception:
        logger.warning("notify: dispatch failed for %s", getattr(flight, "id", "?"), exc_info=True)
    return outcome


# ---------------------------------------------------------------------------
# Flight-day brief (#753)
# ---------------------------------------------------------------------------


def _advisory_statuses(pack_dir: Path | None) -> tuple[dict[str, str], dict[str, str]]:
    """``({advisory_id: aggregate_status}, {advisory_id: name})`` of a pack;
    empty when it has no advisories file."""
    if pack_dir is None:
        return {}, {}
    path = Path(pack_dir) / "route_advisories.json"
    if not path.exists():
        return {}, {}
    import json

    data = json.loads(path.read_text())
    names = {e["id"]: e.get("name", e["id"]) for e in data.get("catalog", []) if "id" in e}
    statuses = {
        r["advisory_id"]: r.get("aggregate_status", "")
        for r in data.get("advisories", []) if "advisory_id" in r
    }
    return statuses, names


def flight_day_since(
    db: Session,
    flight_id: str,
    meta: BriefingPackMeta,
    pack_dir: Path,
    *,
    refreshed: bool,
):
    """The "Since the last briefing" section: the grade and advisory statuses
    of the pack this preflight run built, against the pack it replaced.

    Read from the same prior pack ``detect_change`` uses, but both directions
    are reported (``detect_change`` only words a worsening). Without a new
    pack there is nothing to compare: the email says so instead.
    """
    from weatherbrief.notify.email import AdvisoryStatusChange, FlightDaySince

    since = FlightDaySince(
        refreshed=refreshed, briefing_at=meta.fetch_timestamp, assessment=meta.assessment,
    )
    if not refreshed:
        return since
    prior = _prior_pack(db, flight_id, meta.fetch_timestamp)
    if prior is None:
        return since
    since.prior_briefing_at = prior.fetch_timestamp
    since.prior_assessment = prior.assessment
    try:
        from weatherbrief.storage.flights import _resolve_artifact_path

        now_status, names = _advisory_statuses(pack_dir)
        old_status, old_names = _advisory_statuses(
            Path(_resolve_artifact_path(prior.artifact_path)) if prior.artifact_path else None
        )
        if old_status:
            for adv_id, status in now_status.items():
                was = old_status.get(adv_id)
                if was != status:
                    since.advisory_changes.append(AdvisoryStatusChange(
                        name=names.get(adv_id) or old_names.get(adv_id) or adv_id,
                        from_status=was, to_status=status,
                    ))
    except Exception:
        # The grade line still stands; only the advisory list is lost.
        logger.warning("notify: advisory diff failed for %s", flight_id, exc_info=True)
    return since


def notify_flight_day(
    db: Session,
    flight: Flight,
    meta: BriefingPackMeta,
    pack_dir: Path,
    *,
    user_id: str,
    refreshed: bool,
    live: dict | None,
    present: bool = False,
    override: str | None = None,
) -> bool:
    """Send the flight-day brief for the T-2h preflight slot (#753).

    Called by the scheduler for every preflight attempt of an auto-refresh
    flight, whether or not a new pack was built (``refreshed``), and in place
    of the ordinary refresh notification. ``live`` is the live layer's
    ``summarize_live`` block for ``meta``'s pack, refreshed just before.

    The gate differs from :func:`notify_briefing_refresh` on purpose:

    - ``notify_change_only`` is **ignored**: the brief is the point, not a
      change report. Scope "off" and the per-flight override still hold
      (``mute`` silences; ``notify`` always sends).
    - ``present`` (someone watching the refresh finish) still suppresses.
    - UNAVAILABLE (#392) only drops the grade from the subject and push, and
      keeps the badge still: the observed section is worth sending even when
      the forecast could not be assessed.

    Returns True when at least one channel was attempted. Never raises. The
    caller commits (the badge write rides its session).
    """
    try:
        from weatherbrief.api.preferences import load_notify_prefs
        from weatherbrief.notify.badge import compute_badge_count, record_notify_qualifying

        prefs = load_notify_prefs(db, user_id)
        if present or not notify_qualifies(
            notify_override=override or flight.notify_override,
            scope=prefs["notify_scope"],
            change_only=False,
            changed=True,
        ):
            logger.info("notify: flight-day brief for %s not sent (gate)", flight.id)
            return False

        badge: int | None = None
        if (meta.assessment or "").upper() != ASSESSMENT_UNAVAILABLE:
            # Lights the badge only when this pack is newer than the last one
            # the pilot opened: an observed-only brief on a pack they have
            # already read leaves it as it is.
            record_notify_qualifying(db, user_id, flight.id, meta.fetch_timestamp)
            badge = compute_badge_count(db, user_id)

        since = flight_day_since(db, flight.id, meta, pack_dir, refreshed=refreshed)
        from weatherbrief.notify.push import count_user_devices

        has_device = count_user_devices(db, user_id) > 0

        sent = False
        if prefs["notify_email"]:
            _send_flight_day_email(
                db, user_id, flight, meta, pack_dir,
                live=live, since=since, has_device=has_device,
            )
            sent = True
        if prefs["notify_push"]:
            _send_flight_day_push(db, user_id, flight, meta, live=live, badge=badge)
            sent = True
        return sent
    except Exception:
        logger.warning(
            "notify: flight-day dispatch failed for %s", getattr(flight, "id", "?"), exc_info=True,
        )
        return False


def _send_flight_day_email(
    db: Session, user_id: str, flight: Flight, meta: BriefingPackMeta, pack_dir: Path,
    *, live: dict | None, since, has_device: bool,
) -> None:
    """Guarded like :func:`_send_email`: logs and skips on any failure."""
    try:
        from weatherbrief.notify.email import SmtpConfig, send_flight_day_email

        SmtpConfig.from_env()
    except (ValueError, ImportError):
        logger.debug("notify: email not configured, skipping flight-day for %s", flight.id)
        return

    from flyfun_common.db.models import UserRow
    from weatherbrief.privacy import mask_email

    user = db.query(UserRow).filter(UserRow.id == user_id).first()
    if not user or not user.email:
        return
    try:
        send_flight_day_email(
            [user.email], flight, meta, pack_dir,
            live=live, since=since, has_device=has_device, base_url=_base_url(),
        )
        logger.info("notify: flight-day email sent for %s to %s", flight.id, mask_email(user.email))
    except Exception:
        logger.warning("notify: flight-day email failed for %s", flight.id, exc_info=True)


def _send_flight_day_push(
    db: Session, user_id: str, flight: Flight, meta: BriefingPackMeta,
    *, live: dict | None, badge: int | None,
) -> None:
    """Guarded like :func:`_send_push`."""
    try:
        from weatherbrief.notify.email import flight_day_grade, flight_day_headline
        from weatherbrief.notify.push import send_flight_day_push

        n = send_flight_day_push(
            db, user_id, flight, meta,
            headline=flight_day_headline(live), grade=flight_day_grade(meta), badge=badge,
        )
        if n:
            logger.info("notify: flight-day push sent for %s to %d device(s)", flight.id, n)
    except Exception:
        logger.warning("notify: flight-day push failed for %s", flight.id, exc_info=True)
