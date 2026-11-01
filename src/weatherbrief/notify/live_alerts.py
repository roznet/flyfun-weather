"""Live-alert push on flight day (#754, rule #968).

While a flight is inside its live window, each server live tick
(``tasks/live_tick.py``) hands its committed layer here. Only a flight whose
pilot pressed Start (``FlightProgress.start_fix``) gets a push: a push in the
air is a distraction, so it goes out only for what the pilot needs to know.
One push per flight per tick carries everything that tick raised:

- **category alerts** (``metar_category`` / ``taf_category``, the rule below);
- **other alerts** — alert-tier rows with ``new_alert`` (the classifier's own
  alert-once rule), plus rows for a key whose clear we pushed earlier (the
  re-arm, below);
- **clears** — a non-category alert we pushed that has not been an alert-tier
  row for :data:`CLEAR_SUSTAIN_TICKS` consecutive ticks on which its source
  was read.

**Category rule (#968).** Departure (until the take-off) and destination
only; route and alternate airports never push for a category. A METAR or
TAF-at-ETA drop to IFR or LIFR pushes, for every pilot; MVFR never does. Per
airport, ``LivePushState.known`` is what the pilot already knows: seeded on
the first tick after Start with the worst of MVFR, the briefed category and
what showed then (the app is open at Start), raised again by a new pack, never
lowered. Nothing at or above it pushes. Two ratchets: a METAR pushes only when
worse than the worst METAR pushed; a TAF only when worse than the worst TAF
*or METAR* pushed (a TAF catching up with observed weather is silent; a METAR
confirming an earlier TAF push still pushes, it is happening now). A bounce
back to an already-pushed category is silent. No category clears, no category
re-arm: improvements show in the app only. The ratchet advances whatever the
push's outcome (sent, shadow, skipped or failed) so a mute, an outage or a
pref toggle never releases a burst later; a failed push is retried as below.

**Storms over the top (#968).** A route storm whose echo tops are known (not
truncated) and at least :data:`STORM_TOPS_CLEARANCE_FT` below the cruise level
does not push: the EDDC case, FL096 tops under a FL250 cruise.

Push only, never email. A ↻ press (``trigger="user"``) never pushes: the
pilot is looking at the screen.

**Re-arm** (non-category keys). Airport alert memory keeps the worst level
alerted for the whole flight (§45). Once a clear is pushed for a key, its next
alert-tier row pushes again. This amends §45 for the push path only: the
classifier, ``new_alert`` and what the screens show are unchanged
(meteorology-decisions §47; category keys left it in §77).

**Departure rows end at the take-off** (``FlightProgress.left_departure``,
#967), never at the planned time.

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
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from weatherbrief.notify.push import PushDevice

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

#: A route storm whose known echo tops are at least this far below the cruise
#: level does not push (#968). Echo tops sit under the cloud tops, and the
#: turbulence above a cell reaches higher still: the margin is generous.
STORM_TOPS_CLEARANCE_FT = 5000

#: Category kinds the #968 rule governs, and the categories that may push.
CATEGORY_KINDS = ("metar_category", "taf_category")
PUSH_CATEGORIES = ("IFR", "LIFR")
#: Below this nothing pushes for a category (the known level's floor).
KNOWN_FLOOR = "MVFR"
_RANK = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}

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
    # Keys among ``alerts`` re-sent because an earlier push of them failed.
    retried: set[str] = field(default_factory=set)
    # Clears due for alerts recorded in shadow mode, now that sending is on:
    # the pilot never got the alert, so its clear is dropped, not pushed.
    silent_clears: list[str] = field(default_factory=list)
    # The memory with this tick's sustain counters advanced and SIGMET keys
    # followed through reissues, before this tick's pushes are applied.
    state: LivePushState = field(default_factory=LivePushState)
    # Alert-tier rows the rule kept silent this tick, (key, reason): e.g.
    # "known=IFR", "ratchet=IFR", "mvfr", "role=route", "not_started",
    # "after_takeoff", "tops=FL096<FL250". For the shadow review (#968).
    suppressed: list[tuple[str, str]] = field(default_factory=list)
    # Why each category alert pushes: key -> "known=MVFR ratchet=-".
    reasons: dict[str, str] = field(default_factory=dict)

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
    # A proxied terminal's METAR time is its station's (#767).
    term = layer.route_observations.terminal(icao)
    row = term.metar_row if term is not None else None
    return row.metar_time if row is not None else None


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
        name = pushed.icao
        term = layer.route_observations.terminal(pushed.icao) if layer is not None and layer.route_observations is not None else None
        if term is not None and term.proxied:
            name = term.label  # "EGTF (via EGLL)" (#767)
        text = f"{name} {_CLEAR_LABEL[prefix]} no longer {pushed.to_value}{role}"
        metar_at = _airport_metar_time(layer, pushed.icao) if prefix != "taf" else None
        when = f"METAR {_hhmm(metar_at)}" if metar_at is not None else f"as of {_hhmm(now)}"
        return f"Cleared: {text} · {when}"
    return f"Cleared: {pushed.message} · as of {_hhmm(now)}"


def _worst(*cats: str | None) -> str | None:
    known = [c for c in cats if c in _RANK]
    return max(known, key=_RANK.__getitem__) if known else None


def _worse(cat: str | None, than: str | None) -> bool:
    return cat in _RANK and (than not in _RANK or _RANK[cat] > _RANK[than])


def _showing(layer: LiveLayer | None, rows: list[LiveChange]) -> dict[str, str]:
    """ICAO -> the worst category the layer shows there this tick: each
    corridor airport's METAR and TAF-at-ETA (through its proxy, #767), and
    every category row's briefed and current values."""
    out: dict[str, str] = {}

    def add(icao, *cats):
        w = _worst(out.get(icao), *cats)
        if icao and w is not None:
            out[icao.upper()] = w

    obs = layer.route_observations if layer is not None else None
    for a in (obs.airports if obs is not None else []):
        term = obs.terminal(a.icao)
        metar = term.metar_row.metar_flight_category if term and term.metar_row else None
        taf = term.taf_row.taf_flight_category_at_eta if term and term.taf_row else None
        add(a.icao, metar, taf)
    for c in rows:
        if c.kind in CATEGORY_KINDS:
            add(c.icao, c.from_value, c.to_value)
    return out


def _raise_known(state: LivePushState, layer: LiveLayer | None, rows: list[LiveChange], now: datetime) -> None:
    """Seed (first started tick) or raise (a new pack) the known level: the
    worst of what it was, the briefing's category and what shows now. Only
    levels above the MVFR floor are stored."""
    for icao, cat in _showing(layer, rows).items():
        w = _worst(state.known.get(icao), cat)
        if _worse(w, KNOWN_FLOOR):
            state.known[icao] = w
    if state.started_at is None:
        state.started_at = now
    state.known_pack = layer.pack_timestamp if layer is not None else None


def _category_verdict(c: LiveChange, state: LivePushState) -> tuple[bool, str]:
    """(push?, reason) for a category alert-tier row, the state seeded."""
    if c.role not in ("departure", "destination"):
        return False, f"role={c.role}"
    to = c.to_value
    if to not in PUSH_CATEGORIES:
        return False, "mvfr" if to == "MVFR" else f"cat={to}"
    icao = (c.icao or "").upper()
    known = _worst(KNOWN_FLOOR, state.known.get(icao), c.from_value)
    if not _worse(to, known):
        return False, f"known={known}"
    metar = state.metar_pushed.get(icao)
    ratchet = metar if c.kind == "metar_category" else _worst(metar, state.taf_pushed.get(icao))
    if not _worse(to, ratchet):
        return False, f"ratchet={ratchet}"
    return True, f"known={known} ratchet={ratchet or '-'}"


def _storm_over_the_top(c: LiveChange, layer: LiveLayer | None, cruise_ft: int | None) -> str | None:
    """The reason a route storm row stays silent because the cruise clears
    its echo tops (#968), else None. Every alerting member's top must be
    known and untruncated."""
    if c.role != "route" or cruise_ft is None or layer is None or layer.storms is None:
        return None
    ids = c.alert_storm_ids or c.storm_ids
    if not ids:
        return None
    by_id = {st.id: st for st in layer.storms.storms}
    members = [by_id.get(i) for i in ids]
    if any(m is None or m.top_fl is None or m.truncated for m in members):
        return None
    top = max(m.top_fl for m in members)
    if cruise_ft >= top * 100 + STORM_TOPS_CLEARANCE_FT:
        return f"tops=FL{top:03d}<FL{cruise_ft // 100:03d}"
    return None


def decide(
    changes: LiveChanges,
    state: LivePushState | None,
    *,
    now: datetime,
    layer: LiveLayer | None = None,
    is_pending=None,
    sending: bool = False,
    started: bool = False,
    left_departure: bool = False,
    cruise_ft: int | None = None,
) -> PushDecision:
    """This tick's pushes from its committed changes and the push memory.

    ``is_pending(key)`` says a SIGMET key is pending (issued, not yet valid)
    and so not cleared by its absence (a failed lookahead query, #683).
    ``sending`` is whether this push will really go out (not shadow mode).
    ``started``: the pilot pressed Start (no category push without it, and
    the first started tick seeds the known level). ``left_departure``: a
    take-off is known (#967), so departure rows no longer push. ``cruise_ft``:
    the flight's cruise level, for the storm tops check.
    """
    state = state.model_copy(deep=True) if state is not None else LivePushState()
    rows = changes.changes
    alert_rows = [c for c in rows if c.tier == "alert"]
    out = PushDecision(state=state)

    # Category keys no longer clear (#968): drop any left from before.
    for key in [k for k, p in state.active.items() if p.kind in CATEGORY_KINDS]:
        del state.active[key]
    if started and (state.started_at is None or state.known_pack != (layer.pack_timestamp if layer else None)):
        # First tick after Start, or a new pack since: what shows now is known.
        _raise_known(state, layer, rows, now)

    # A failed push is retried only while it would not have expired yet.
    state.retry = {k: t for k, t in state.retry.items() if now < t + PUSH_TTL}
    for c in alert_rows:
        if c.role == "departure" and left_departure:
            out.suppressed.append((c.key, "after_takeoff"))
            continue
        if c.kind in CATEGORY_KINDS:
            if c.key in state.retry:
                out.alerts.append(c)
                out.retried.add(c.key)
                continue
            if c.direction != "worse":
                continue
            # The push is decided on the ratchet, not on ``new_alert`` (§45's
            # on-screen memory may hold an IFR from before Start); only a row
            # the screen alerts on this tick counts as suppressed, so a
            # persisting row is not logged every tick.
            if not started:
                if c.new_alert:
                    out.suppressed.append((c.key, "not_started"))
                continue
            push, why = _category_verdict(c, state)
            if push:
                out.alerts.append(c)
                out.reasons[c.key] = why
            elif c.new_alert:
                out.suppressed.append((c.key, why))
                state.category_suppressed += 1
            continue
        if c.kind == "storm" and c.new_alert:
            tops = _storm_over_the_top(c, layer, cruise_ft)
            if tops is not None:
                out.suppressed.append((c.key, tops))
                continue
        if c.new_alert:
            out.alerts.append(c)
        elif c.kind != "storm" and _rearmed_key(c.key, state.rearmed) is not None:
            out.alerts.append(c)
            out.rearmed.add(c.key)
        elif c.key in state.retry:
            out.alerts.append(c)
            out.retried.add(c.key)

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
            if sending and pushed.shadow:
                out.silent_clears.append(key)
                continue
            here = _match(key, rows, sigmet_kinds=("sigmet_cancelled",))
            out.clears.append((pushed, clear_line(pushed, here, now=now, layer=layer)))
    return out


def next_state(
    decision: PushDecision, *, pushed: bool, now: datetime,
    failed: bool = False, shadow: bool = False,
) -> LivePushState:
    """The memory after this tick: ``pushed`` when the push reached a device
    (or would have, in shadow mode; ``shadow`` marks what it records). A
    skipped or failed push leaves its alerts untracked and its rearmed keys
    armed; its clears still leave ``active`` so unmuting does not release a
    burst of stale clears. A ``failed`` push's alerts are kept for retry."""
    state = decision.state.model_copy(deep=True)
    # The category ratchets advance whatever the outcome (#968): a skipped
    # push (muted, pref off) must not release a burst later, and a failed one
    # is re-sent through ``retry`` below, not by the ratchet.
    for c in decision.alerts:
        if c.kind in CATEGORY_KINDS and c.icao and c.to_value in _RANK:
            ratchet = state.metar_pushed if c.kind == "metar_category" else state.taf_pushed
            icao = c.icao.upper()
            ratchet[icao] = _worst(ratchet.get(icao), c.to_value)
    for key in decision.silent_clears:
        state.active.pop(key, None)
    for p, _ in decision.clears:
        state.active.pop(p.key, None)
        if pushed:
            state.rearmed[p.key] = now
            state.clears_pushed += 1
    if not pushed:
        if failed:
            for c in decision.alerts:
                state.retry.setdefault(c.key, now)
        return state
    for c in decision.alerts:
        state.retry.pop(c.key, None)
        if c.kind != "storm" and c.kind not in CATEGORY_KINDS:
            state.active[c.key] = LivePushedAlert(
                key=c.key, kind=c.kind, role=c.role, icao=c.icao,
                to_value=c.to_value, message=c.message, pushed_at=now,
                shadow=shadow,
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


def push_expiry(flight_row, now: datetime, events: tuple = ()) -> datetime:
    """min(now + 30 min, end of the live window); never before now. The
    window end follows the flight's take-off / landed entries (#760)."""
    from weatherbrief.storage.flights import ensure_utc
    from weatherbrief.tasks.live_tick import live_window_end, live_window_hours

    _, after_h = live_window_hours()
    window_end = live_window_end(
        ensure_utc(flight_row.departure_time), flight_row.flight_duration_hours, after_h, events, now,
    )
    return max(now, min(now + PUSH_TTL, window_end))


# --- Eligibility --------------------------------------------------------------


def skip_reason(
    db: Session, flight_row, *, trigger: str, started: bool = False,
) -> tuple[str | None, list[PushDevice]]:
    """(why this flight's user gets no live push, or None; their ``PushDevice`` list).

    All must hold: a tick (not a ↻ press), the flight ``started`` (#968:
    pressing Start is the only consent to live alerts; auto-refresh alone,
    the flight's or its trip's, no longer is, though it still drives
    briefing pushes), the bell not muted (the flight's, else its trip's — the
    same precedence as briefing pushes), ``notify_live_alerts`` on,
    ``notify_push`` on and at least one device. ``notify_scope`` and
    ``notify_change_only`` govern briefing updates and do not apply.
    """
    if trigger == "user":
        return "user_trigger", []
    if not started:
        return "no_consent", []
    trip = None
    if getattr(flight_row, "trip_id", None):
        from weatherbrief.db.models import FlightTripRow

        trip = db.get(FlightTripRow, flight_row.trip_id)
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


def _start_and_takeoff(flight_row, pack_dir: Path | str, now: datetime) -> tuple[bool, bool]:
    """(the app's Start was seen on this flight, a take-off is known): a fix
    on the ground at the departure (#812, ``FlightProgress.start_fix``) and
    ``FlightProgress.left_departure`` (#967). (False, False) when unreadable;
    never raises."""
    try:
        from weatherbrief.analysis.flight_progress import FlightProgress
        from weatherbrief.storage.flights import ensure_utc
        from weatherbrief.tasks.live_layer import flight_dir_for_pack, load_progress_events

        if flight_row.departure_time is None:
            return False, False
        events = load_progress_events(flight_dir_for_pack(pack_dir))
        progress = FlightProgress.schedule(
            ensure_utc(flight_row.departure_time), flight_row.flight_duration_hours, now, events,
        )
        return progress.start_fix is not None, progress.left_departure
    except Exception:
        logger.warning("Live push: could not read Start for %s", flight_row.id, exc_info=True)
        return False, False


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
    sending = send_enabled()
    started, left_departure = _start_and_takeoff(flight_row, pack_dir, now)
    decision = decide(
        layer.changes, layer.push_state, now=now, layer=layer,
        is_pending=lambda k: pending_sigmet_key(k, traces, now),
        sending=sending, started=started, left_departure=left_departure,
        cruise_ft=getattr(flight_row, "cruise_altitude_ft", None),
    )
    outcome = "none"
    pushed = False
    if decision.suppressed:
        logger.info("LIVE_PUSH_SUPPRESSED flight=%s started=%s rows=%s", flight_row.id, started,
                    [f"{k}:{why}" for k, why in decision.suppressed])
    if not decision.empty:
        alert_keys = [c.key for c in decision.alerts]
        cleared_keys = [p.key for p, _ in decision.clears]
        reason, devices = skip_reason(db, flight_row, trigger=trigger, started=started)
        if reason is not None:
            logger.info(
                "LIVE_PUSH_SKIPPED flight=%s user=%s reason=%s alerts=%s cleared=%s",
                flight_row.id, flight_row.user_id, reason, alert_keys, cleared_keys,
            )
            outcome = f"skipped:{reason}"
        else:
            pushed = True
            logger.info(
                "LIVE_PUSH_WOULD_SEND flight=%s user=%s alerts=%s cleared=%s rearmed=%s retried=%s "
                "rule=%s devices=%d",
                flight_row.id, flight_row.user_id, alert_keys, cleared_keys,
                sorted(decision.rearmed), sorted(decision.retried),
                [f"{k}:{why}" for k, why in decision.reasons.items()], len(devices),
            )
            outcome = "shadow"
            if sending:
                outcome = _send(db, flight_row, layer, decision, devices, now, pack_dir)
                # Only a push that reached a device advances the memory: an
                # undelivered alert must not later read as "cleared", nor
                # use up a re-arm. Its new_alert is spent, so a failed alert
                # is kept for retry on the next ticks (within PUSH_TTL).
                pushed = outcome == "sent"
    if decision.silent_clears:
        logger.info("LIVE_PUSH_CLEAR_DROPPED flight=%s keys=%s reason=recorded_in_shadow",
                    flight_row.id, decision.silent_clears)
    new_state = next_state(decision, pushed=pushed, now=now,
                           failed=outcome == "failed", shadow=not sending)
    if new_state != (layer.push_state or LivePushState()):
        from weatherbrief.tasks.live_layer import flight_dir_for_pack, patch_push_state

        if not patch_push_state(flight_dir_for_pack(Path(pack_dir)), new_state,
                                pack_timestamp=layer.pack_timestamp):
            logger.info("Live push memory for %s not stored: a new pack replaced the layer",
                        flight_row.id)
    return outcome


def _send(db, flight_row, layer, decision, devices, now, pack_dir=None) -> str:
    from weatherbrief.notify.push import send_live_alert_push
    from weatherbrief.tasks.live_layer import flight_dir_for_pack, load_progress_events
    from weatherbrief.tasks.live_timing import record_delivery

    events = load_progress_events(flight_dir_for_pack(Path(pack_dir))) if pack_dir else ()
    payload = build_payload(flight_row, decision, tick_at=layer.live_updated_at or now)
    try:
        sent = send_live_alert_push(
            db, flight_row.user_id, payload,
            expires_at=push_expiry(flight_row, now, events), devices=devices,
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
