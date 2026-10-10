"""Live-alert push on flight day (#754).

While an auto-refresh flight is inside its live window, each server live tick
(``tasks/live_tick.py``) hands its committed layer here. One push per flight
per tick carries everything that tick raised:

- **alerts** — alert-tier rows with ``new_alert`` (the classifier's own
  alert-once rule), plus rows for a key whose clear we pushed earlier (the
  re-arm, below);
- **clears** — an alert we pushed that has not been an alert-tier row for
  :data:`CLEAR_SUSTAIN_TICKS` consecutive ticks on which its source was read.

Push only, never email. A ↻ press (``trigger="user"``) never pushes: the
pilot is looking at the screen.

**Re-arm.** Airport alert memory keeps the worst level alerted for the whole
flight (§45), so after "LFAT no longer IFR" a return to IFR would stay quiet
and leave the pilot believing it is clear. Once a clear is pushed for a key,
its next alert-tier row pushes again. This amends §45 for the push path only:
the classifier, ``new_alert`` and what the screens show are unchanged
(meteorology-decisions §47).

Storm rows push their new alerts but never a clear: "the storm passed" has no
clean definition yet.

**Shadow mode.** ``WB_LIVE_PUSH_SEND`` (default off) gates only the APNs
call. Every decision is logged (``LIVE_PUSH_WOULD_SEND`` /
``LIVE_PUSH_SKIPPED``) and the push memory advances exactly as if it had been
sent, so a shadow run measures what the live stream would do.

Everything here is best-effort: a push must never break a tick.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from weatherbrief.models.live import (
    LiveChange,
    LiveChanges,
    LiveLayer,
    LivePushedAlert,
    LivePushState,
)

logger = logging.getLogger(__name__)

#: Env flag that turns the APNs call on. Unset = shadow mode.
SEND_ENV = "WB_LIVE_PUSH_SEND"
#: A clear is pushed after this many consecutive evaluated ticks without an
#: alert-tier row for the key (~20 min at the 10-min cycle): VFR↔MVFR flicker
#: must not ping-pong.
CLEAR_SUSTAIN_TICKS = 2
#: ``apns-expiration`` is at most this far after sending (and never past the
#: end of the live window). APNs keeps only the newest pending push per app per
#: device: a phone offline for a leg lands to nothing stale, one dropping in and
#: out at low level still gets the latest.
PUSH_TTL = timedelta(minutes=30)

#: Push types the app routes on (payload contract shared with #753's
#: ``flight_day``).
TYPE_ALERT = "live_alert"
TYPE_CLEAR = "live_clear"

#: Airport key prefix -> the word the clear line uses ("LFAT METAR no longer IFR").
_CLEAR_LABEL = {"metar": "METAR", "taf": "TAF at ETA", "conv": "METAR", "wx": "METAR", "wind": "wind"}


def send_enabled() -> bool:
    return os.environ.get(SEND_ENV, "").strip().lower() in ("1", "true", "yes")


# --- The decision (pure) ------------------------------------------------------


@dataclass
class PushDecision:
    """What one tick would push, and the memory before any push is applied."""

    alerts: list[LiveChange] = field(default_factory=list)
    # Keys among ``alerts`` pushed only because a clear re-armed them.
    rearmed: set[str] = field(default_factory=set)
    # (what was pushed, the clear line) for each clear due this tick.
    clears: list[tuple[LivePushedAlert, str]] = field(default_factory=list)
    # The memory with this tick's sustain counters advanced and SIGMET keys
    # followed through reissues, before this tick's pushes are applied.
    state: LivePushState = field(default_factory=LivePushState)

    @property
    def empty(self) -> bool:
        return not self.alerts and not self.clears


def _parts(key: str) -> set[str]:
    return set(key.split("+"))


def _is_sigmet(key: str) -> bool:
    return key.startswith("sigmet:")


def _evaluated(key: str, evaluated: list[str] | None) -> bool:
    """Whether this tick read ``key``'s source (a full key or a "sigmet:"
    style dimension prefix). A layer read back from disk has no record
    (None): nothing counts toward a clear."""
    if not evaluated:
        return False
    return any(key == e or (e.endswith(":") and key.startswith(e)) for e in evaluated)


def _match(key: str, rows: list[LiveChange], *, sigmet_kinds: tuple[str, ...]) -> LiveChange | None:
    """The row standing for ``key`` this tick: the same key, or for a SIGMET
    any row of ``sigmet_kinds`` sharing a member SIGMET (a reissue or a
    late partner FIR changes the key, not the phenomenon)."""
    for c in rows:
        if c.key == key:
            return c
    if _is_sigmet(key):
        parts = _parts(key)
        for c in rows:
            if c.kind in sigmet_kinds and parts & _parts(c.key):
                return c
    return None


def _rearmed_key(key: str, rearmed: dict[str, datetime]) -> str | None:
    if key in rearmed:
        return key
    if _is_sigmet(key):
        parts = _parts(key)
        for k in rearmed:
            if _is_sigmet(k) and parts & _parts(k):
                return k
    return None


def _hhmm(dt: datetime | None) -> str | None:
    return f"{dt.astimezone(timezone.utc):%H:%M}Z" if dt is not None else None


def alert_line(c: LiveChange) -> str:
    """"LFAT METAR: IFR → LIFR (SPECI) (destination) · SPECI 10:20Z"."""
    role = "" if c.role == "route" or "(at destination)" in c.message else f" ({c.role})"
    when = _hhmm(c.observed_at)
    return f"{c.message}{role}" + (f" · {c.source} {when}" if when else "")


def _airport_metar_time(layer: LiveLayer | None, icao: str | None) -> datetime | None:
    if layer is None or icao is None or layer.route_observations is None:
        return None
    for a in layer.route_observations.airports:
        if a.icao.upper() == icao.upper():
            return a.metar_time
    return None


def clear_line(pushed: LivePushedAlert, row: LiveChange | None, *, now: datetime,
               layer: LiveLayer | None = None) -> str:
    """Deterministic text for a pushed alert that cleared.

    The tick's own row when there is one (an improvement, "SIGMET … no longer
    active", "… cancelled"); for an airport back to its briefed state, which
    has no row, "LFAT METAR no longer IFR"; otherwise the pushed text.
    """
    role = "" if pushed.role == "route" else f" ({pushed.role})"
    if row is not None:
        text = row.message + ("" if "(at destination)" in row.message else role)
        when = _hhmm(row.observed_at)
        return f"Cleared: {text}" + (f" · {row.source} {when}" if when else f" · as of {_hhmm(now)}")
    prefix = pushed.key.split(":", 1)[0]
    if pushed.icao and prefix in _CLEAR_LABEL and pushed.to_value:
        text = f"{pushed.icao} {_CLEAR_LABEL[prefix]} no longer {pushed.to_value}{role}"
        metar_at = _airport_metar_time(layer, pushed.icao) if prefix != "taf" else None
        when = f"METAR {_hhmm(metar_at)}" if metar_at is not None else f"as of {_hhmm(now)}"
        return f"Cleared: {text} · {when}"
    return f"Cleared: {pushed.message} · as of {_hhmm(now)}"


def decide(
    changes: LiveChanges,
    state: LivePushState | None,
    *,
    now: datetime,
    layer: LiveLayer | None = None,
    is_pending=None,
) -> PushDecision:
    """This tick's pushes from its committed changes and the push memory.

    ``is_pending(key)`` says a SIGMET key is pending (issued, not yet valid)
    and so not cleared by its absence (a failed lookahead query, #683).
    """
    state = state.model_copy(deep=True) if state is not None else LivePushState()
    rows = changes.changes
    alert_rows = [c for c in rows if c.tier == "alert"]
    out = PushDecision(state=state)

    for c in alert_rows:
        if c.new_alert:
            out.alerts.append(c)
        elif c.kind != "storm" and _rearmed_key(c.key, state.rearmed) is not None:
            out.alerts.append(c)
            out.rearmed.add(c.key)

    for key, pushed in list(state.active.items()):
        row = _match(key, alert_rows, sigmet_kinds=("sigmet_issued",))
        if row is None and _is_sigmet(key):
            # A reissue demoted to a highlight (a briefed chain, an after-
            # arrival start) is still the SIGMET we pushed: not cleared.
            row = _match(key, [c for c in rows if c.kind == "sigmet_issued"],
                          sigmet_kinds=("sigmet_issued",))
        if row is not None:
            pushed.clear_ticks = 0
            if row.key != key:
                # Follow the SIGMET to its new key so the next match is direct.
                del state.active[key]
                state.active[row.key] = pushed.model_copy(update={"key": row.key})
            continue
        if not _evaluated(key, changes.evaluated):
            continue  # not read this tick: neither a clear nor a reset
        if _is_sigmet(key) and is_pending is not None and is_pending(key):
            continue
        pushed.clear_ticks += 1
        if pushed.clear_ticks >= CLEAR_SUSTAIN_TICKS:
            here = _match(key, rows, sigmet_kinds=("sigmet_cancelled",))
            out.clears.append((pushed, clear_line(pushed, here, now=now, layer=layer)))
    return out


def next_state(decision: PushDecision, *, pushed: bool, now: datetime) -> LivePushState:
    """The memory after this tick: ``pushed`` when the push reached a device
    (or would have, in shadow mode). A skipped or failed push leaves its alerts untracked and its
    rearmed keys armed; its clears still leave ``active`` so unmuting does
    not release a burst of stale clears."""
    state = decision.state.model_copy(deep=True)
    for p, _ in decision.clears:
        state.active.pop(p.key, None)
        if pushed:
            state.rearmed[p.key] = now
            state.clears_pushed += 1
    if not pushed:
        return state
    for c in decision.alerts:
        if c.kind != "storm":
            state.active[c.key] = LivePushedAlert(
                key=c.key, kind=c.kind, role=c.role, icao=c.icao,
                to_value=c.to_value, message=c.message, pushed_at=now,
            )
        armed = _rearmed_key(c.key, state.rearmed)
        if armed is not None:
            del state.rearmed[armed]
        state.alerts_pushed += 1
    state.rearms_fired += len(decision.rearmed)
    return state


# --- Payload ------------------------------------------------------------------


def push_title(flight_row, n_alerts: int, n_clears: int) -> str:
    route = _route_of(flight_row)
    n = n_alerts + n_clears
    if n > 1:
        return f"{route} · {n} changes"
    return f"{route} · {'live alert' if n_alerts else 'alert cleared'}"


def _route_of(flight_row) -> str:
    try:
        waypoints = json.loads(flight_row.waypoints_json or "[]")
    except (TypeError, ValueError):
        waypoints = []
    if waypoints:
        return " → ".join(str(w) for w in waypoints)
    return flight_row.route_name or "FlyFun flight"


def build_payload(flight_row, decision: PushDecision, *, tick_at: datetime) -> dict:
    """One push for the tick: alerts first, then clears, one line each."""
    lines = [alert_line(c) for c in decision.alerts] + [line for _, line in decision.clears]
    keys = [c.key for c in decision.alerts] + [p.key for p, _ in decision.clears]
    is_alert = bool(decision.alerts)
    return {
        "aps": {
            "alert": {
                "title": push_title(flight_row, len(decision.alerts), len(decision.clears)),
                "body": "\n".join(lines),
            },
            "sound": "default",
            # Groups a flight's pushes in Notification Center.
            "thread-id": flight_row.id,
            # Breaks through Focus with the Time Sensitive entitlement; without
            # it iOS treats the push as "active".
            "interruption-level": "time-sensitive" if is_alert else "active",
        },
        "flight_id": flight_row.id,
        "type": TYPE_ALERT if is_alert else TYPE_CLEAR,
        "keys": keys,
        "tick_at": tick_at.isoformat(),
    }


def push_expiry(flight_row, now: datetime) -> datetime:
    """min(now + 30 min, end of the live window); never before now."""
    from weatherbrief.storage.flights import ensure_utc
    from weatherbrief.tasks.live_tick import live_window_hours

    _, after_h = live_window_hours()
    window_end = (
        ensure_utc(flight_row.departure_time)
        + timedelta(hours=(flight_row.flight_duration_hours or 0) + after_h)
    )
    return max(now, min(now + PUSH_TTL, window_end))


# --- Eligibility --------------------------------------------------------------


def skip_reason(db: Session, flight_row, *, trigger: str) -> tuple[str | None, list[tuple[str, str]]]:
    """(why this flight's user gets no live push, or None; their devices).

    All must hold: a tick (not a ↻ press), the flight's or its trip's
    auto-refresh on, the bell not muted (the flight's, else its trip's — the
    same precedence as briefing pushes), ``notify_live_alerts`` on,
    ``notify_push`` on and at least one device. ``notify_scope`` and
    ``notify_change_only`` govern briefing updates and do not apply.
    """
    if trigger == "user":
        return "user_trigger", []
    trip = None
    if getattr(flight_row, "trip_id", None):
        from weatherbrief.db.models import FlightTripRow

        trip = db.get(FlightTripRow, flight_row.trip_id)
    if not (flight_row.auto_refresh or (trip is not None and trip.auto_refresh)):
        return "not_auto_refresh", []
    override = flight_row.notify_override or "default"
    if override == "default" and trip is not None:
        override = trip.notify_override or "default"
    if override == "mute":
        return "muted", []
    from weatherbrief.api.preferences import load_notify_prefs

    prefs = load_notify_prefs(db, flight_row.user_id)
    if not prefs.get("notify_live_alerts", True):
        return "pref_off", []
    if not prefs.get("notify_push", False):
        return "push_off", []
    from weatherbrief.notify.push import _load_devices

    devices = _load_devices(db, flight_row.user_id)
    if not devices:
        return "no_device", []
    return None, devices


# --- The sink -----------------------------------------------------------------


def notify_live_alerts(
    db: Session,
    flight_row,
    layer: LiveLayer | None,
    *,
    pack_dir: Path | str,
    trigger: str = "tick",
    now: datetime | None = None,
) -> str:
    """Decide, log and (unless in shadow mode) send this tick's live push for
    one flight, then store the push memory. Returns the outcome, for tests and
    the tick's log: ``none`` | ``skipped:<reason>`` | ``shadow`` | ``sent`` |
    ``failed``. Never raises.

    ``layer`` is the layer this tick committed, in memory (``CommitTrace``):
    a refused commit has none, and its stored ``new_alert`` flags belong to
    whoever did commit.
    """
    try:
        return _notify(db, flight_row, layer, pack_dir=pack_dir, trigger=trigger,
                       now=now or datetime.now(timezone.utc))
    except Exception:
        logger.warning("Live push failed for flight %s — tick kept", flight_row.id, exc_info=True)
        return "failed"


def _notify(db, flight_row, layer, *, pack_dir, trigger, now) -> str:
    if trigger == "user":
        # Before reading anything: the press consumed its new_alert flags and
        # reaches the screen by poll (#751 records that delivery).
        logger.info("LIVE_PUSH_SKIPPED flight=%s user=%s reason=user_trigger",
                    flight_row.id, flight_row.user_id)
        return "skipped:user_trigger"
    if layer is None or layer.changes is None:
        return "none"
    from weatherbrief.tasks.live_significance import pending_sigmet_key

    traces = {t.key: t for t in layer.sigmet_traces}
    decision = decide(
        layer.changes, layer.push_state, now=now, layer=layer,
        is_pending=lambda k: pending_sigmet_key(k, traces, now),
    )
    outcome = "none"
    pushed = False
    if not decision.empty:
        alert_keys = [c.key for c in decision.alerts]
        cleared_keys = [p.key for p, _ in decision.clears]
        reason, devices = skip_reason(db, flight_row, trigger=trigger)
        if reason is not None:
            logger.info(
                "LIVE_PUSH_SKIPPED flight=%s user=%s reason=%s alerts=%s cleared=%s",
                flight_row.id, flight_row.user_id, reason, alert_keys, cleared_keys,
            )
            outcome = f"skipped:{reason}"
        else:
            pushed = True
            logger.info(
                "LIVE_PUSH_WOULD_SEND flight=%s user=%s alerts=%s cleared=%s rearmed=%s devices=%d",
                flight_row.id, flight_row.user_id, alert_keys, cleared_keys,
                sorted(decision.rearmed), len(devices),
            )
            outcome = "shadow"
            if send_enabled():
                outcome = _send(db, flight_row, layer, decision, devices, now)
                # Only a push that reached a device advances the memory: an
                # undelivered alert must not later read as "cleared", nor
                # use up a re-arm. Its new_alert is spent either way.
                pushed = outcome == "sent"
    new_state = next_state(decision, pushed=pushed, now=now)
    if new_state != (layer.push_state or LivePushState()):
        from weatherbrief.tasks.live_layer import flight_dir_for_pack, patch_push_state

        if not patch_push_state(flight_dir_for_pack(Path(pack_dir)), new_state,
                                pack_timestamp=layer.pack_timestamp):
            logger.info("Live push memory for %s not stored: a new pack replaced the layer",
                        flight_row.id)
    return outcome


def _send(db, flight_row, layer, decision, devices, now) -> str:
    from weatherbrief.notify.push import send_live_alert_push
    from weatherbrief.tasks.live_timing import record_delivery

    payload = build_payload(flight_row, decision, tick_at=layer.live_updated_at or now)
    try:
        sent = send_live_alert_push(
            db, flight_row.user_id, payload,
            expires_at=push_expiry(flight_row, now), devices=devices,
        )
    except Exception:
        # The sender swallows its own failures; this is the one that escaped.
        logger.warning("LIVE_PUSH_FAILED flight=%s user=%s reason=exception",
                       flight_row.id, flight_row.user_id, exc_info=True)
        return "failed"
    try:
        # Dead tokens pruned while sending are the caller's session's work.
        db.commit()
    except Exception:
        logger.warning("Live push: device prune not committed", exc_info=True)
        db.rollback()
    sent_at = datetime.now(timezone.utc)
    if not sent:
        # APNs not configured, every token dead, or every device rejected
        # (the sender logs each); distinct from an exception above.
        logger.warning("LIVE_PUSH_FAILED flight=%s user=%s reason=no_device_reached devices=0/%d",
                       flight_row.id, flight_row.user_id, len(devices))
        return "failed"
    logger.info("LIVE_PUSH_SENT flight=%s user=%s devices=%d/%d",
                flight_row.id, flight_row.user_id, sent, len(devices))
    if layer.live_updated_at is not None:
        # The device hop (#751): this version reached the phone by push. A
        # poll of the same version afterwards is not a second delivery.
        record_delivery(
            db, flight_id=flight_row.id, user_id=flight_row.user_id, platform="ios",
            served=layer.live_updated_at, now=sent_at, via="push", push_sent_at=sent_at,
        )
    return "sent"
