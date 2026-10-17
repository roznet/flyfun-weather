"""Where the flight is along its route, and when it gets everywhere else (#759).

One object answers every progress question the live layer asks: distance flown,
departed / arrived, the ETA at a point along the route, the arrival time and the
position at a time. Every consumer (classifier, radar storms, glance and ribbon,
read-time trails, the ↻ path, TAF-at-ETA, the live window) goes through it, so a
better source than the plan changes them all at once.

Sources so far:

- **plan**: departure on time, constant speed over ``duration_h``.
- **manual take-off / landed** events (#760, ``ProgressEvent``, posted by the
  owner, stored in ``live_history.jsonl``): a take-off replaces the planned
  departure, a landing replaces the planned arrival. With both, the speed is
  the one actually flown. The source reads ``plan+takeoff`` once either is in
  effect.
- **position fixes** (#761, app GPS; ADS-B #762 later): sparse corrections
  to the plan, not a replacement for it. A fix says how late the flight is at
  one point; from there it flies the plan's speed (scaled once two fixes far
  enough apart measure the real one). A fix also infers the take-off, holds
  the departure back while on the ground at the departure, and infers the
  landing at the destination. The source reads the fix's (``app``). The rules
  are under "Fixes" below.

A manual entry always overrides what the fixes infer: a manual take-off wins
over the inferred one, and an app ``landed`` never replaces a manual landing.

``timed`` (a departure and a duration) is what flown distance, departed and the
arrival need. ETAs and positions also need a route of non-zero length; on a
zero-length route (a local flight with the same airport at both ends) they are
None and the flown distance is 0.

**As of a time.** Each event carries ``recorded_at``, when the server learned
it; :meth:`FlightProgress.at` only counts events recorded by then, so a
read-time trail rebuilds exactly what a past tick knew. Within a kind, the
event recorded last wins (an edit replaces the earlier entry); one with
``at=None`` withdraws that kind (back to the plan).

**Fixes** (#759 rules, #761). Expect them sparse: the app sends one when it
is open (a weather check, the locate button), so a flight may have one or two
and nothing for an hour. A fix never makes things worse than the plan.

- Projected on the route when received (``FixData.along_nm`` / ``cross_nm``,
  against the latest pack's route); re-projected here when the route in hand
  has another length (a pack with a different route). Without a route (the
  live window, :meth:`schedule`) the stored projection is all there is. A
  fix more than ``FAR_OFF_ROUTE_NM`` across is not this flight (a weather
  check from home): ignored.
- What a fix shows, from its height above the terrain under it
  (``terrain_ft``, the pack's profile) when known: **airborne** at
  ``AIRBORNE_AGL_FT`` or more, **on the ground** below it unless fast (fast
  and low is a car, or the climb-out: unknown). Without a height, from its
  speed: airborne at ``TAKEOFF_GS_KT`` or more, on the ground under
  ``SLOW_GS_KT``. Else unknown (a one-shot fix often has no valid speed).
  Only an airborne fix starts the flight; any fix after the take-off places
  it.
- A fix measures a delay, not a speed: the anchor is the freshest fix after
  the departure, ``flown(t) = along0 + v·(t − t0)``, capped at the route's
  end, with ``v`` the **planned speed**, so the ETAs are the plan's shifted
  by the delay the fix shows. The instantaneous ground speed (a climb, a
  turn, a gust) is never extrapolated. ``v`` is scaled by the speed actually
  flown between the anchor and an earlier fix (or an entered take-off) at
  least ``SPEED_MIN_GAP`` and ``SPEED_MIN_NM`` apart, the ratio clamped to
  ``SPEED_RATIO``.
- ``off_route`` beyond ``OFF_ROUTE_NM`` cross-track; the along-route
  projection is kept (diversions are not detected).
- ``estimated`` once the fix is older than ``STALE_FIX``; it keeps
  extrapolating at the plan's speed, which is the plan shifted, never worse.
- Never backwards: a fix up to ``REVERSAL_NM`` behind where the previous
  anchor put the aircraft when it arrived holds that position as a floor (the
  marker waits for the aircraft), so a passed airport does not flap. Further
  behind, it is taken as it is when it comes more than ``JITTER_GAP`` after
  the previous fix (a new observation after a gap), else only when the next
  fix agrees (jitter). The first fix of a flight always replaces the plan: a
  late departure is exactly a fix behind it.
- Take-off: the first airborne fix after the last fix on the ground,
  back-dated to along 0 at the planned speed, never before that ground fix.
  A later ground fix at the departure resets it (a return to the departure).
  It is inferred, not observed: :attr:`time_base` counts entries only.
- On the ground at the departure (the newest fix, within
  ``NEAR_DEPARTURE_NM``, nothing airborne since): the departure is
  ``max(planned, fix + TAXI_MARGIN)``. A ground fix well before the planned
  time changes nothing; one after it moves the departure to a little after
  it, and the flight is taken to leave then if nothing newer comes.
- Landing: the first fix not airborne within ``NEAR_DESTINATION_NM`` of the
  destination, once flying there at the top of ``SPEED_RATIO`` could have
  got there, lands the flight at the earlier of the fix and the expected
  arrival. An entered or app-detected landing wins over it.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from functools import cached_property

from weatherbrief.analysis.route_geometry import RouteTrack
from weatherbrief.models.analysis import RouteConfig

SOURCE_PLAN = "plan"
#: The plan corrected by a manual take-off and/or landing (#760). Named for
#: the common case; a landing alone reads the same (both are manual entries
#: over the plan's speed).
SOURCE_PLAN_TAKEOFF = "plan+takeoff"
#: Event sources: the pilot's entry, the app's GPS (#761).
SOURCE_MANUAL = "manual"
SOURCE_APP = "app"

EVENT_TAKEOFF = "takeoff"
EVENT_LANDED = "landed"
EVENT_FIX = "fix"
EVENT_KINDS = (EVENT_TAKEOFF, EVENT_LANDED, EVENT_FIX)

#: What a consumer that reports times (the debrief recap, #711) says it used:
#: the plan throughout, observed events throughout, or observed in part.
TIME_BASE_PLANNED = "planned"
TIME_BASE_PARTIAL = "partial"
TIME_BASE_OBSERVED = "observed"

#: History record type for progress events (``live_history.jsonl``).
HISTORY_TYPE = "progress"

# --- Fix rules (#759, #761) -----------------------------------------------------

#: A fix at or above this ground speed shows the aircraft airborne.
TAKEOFF_GS_KT = 50.0
#: Under this ground speed a fix is on the ground (taxiing, parked, walking).
SLOW_GS_KT = 40.0
#: A fix this high above the terrain under it is airborne; lower, on the ground.
AIRBORNE_AGL_FT = 1000.0
#: "At the departure": within this of the route's first point, along and across.
NEAR_DEPARTURE_NM = 3.0
#: "At the destination": within this of the route's last point, along and across.
NEAR_DESTINATION_NM = 5.0
#: Cross-track beyond this is off route (``off_route``).
OFF_ROUTE_NM = 10.0
#: Cross-track beyond this is not the flight at all: the fix is ignored.
FAR_OFF_ROUTE_NM = 25.0
#: A fix older than this makes the position ``estimated``.
STALE_FIX = timedelta(minutes=20)
#: On the ground at the departure: the take-off is at least this after the fix.
TAXI_MARGIN = timedelta(minutes=10)
#: A fix up to this far behind is held at the floor; beyond, it may move the
#: position back (see ``JITTER_GAP``).
REVERSAL_NM = 10.0
#: Fixes closer together than this are one stream: a reversal beyond
#: ``REVERSAL_NM`` needs two of them. Further apart, one fix is enough.
JITTER_GAP = timedelta(minutes=10)
#: Two fixes measure the speed actually flown when this far apart in time and
#: along the route; the ratio to the planned speed is clamped to ``SPEED_RATIO``.
SPEED_MIN_GAP = timedelta(minutes=15)
SPEED_MIN_NM = 20.0
SPEED_RATIO = (0.7, 1.4)
#: A stored projection is reused while the route is this close in length to
#: the one it was made against; beyond, the fix is re-projected.
ROUTE_MATCH_NM = 0.5


def _dt(value) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


@dataclass(frozen=True)
class FixData:
    """What a position fix carries beyond its time and lat/lon (#761).

    ``along_nm`` / ``cross_nm`` (unsigned) / ``course_deg`` are the server's
    projection on the route the fix was received against, ``route_nm`` that
    route's length. ``terrain_ft`` is the terrain elevation under the fix
    (the pack's profile at ``along_nm``), so ``alt_ft`` reads as a height.
    ``client_*`` is the device's own projection, kept as a cross-check only
    (it projects on the briefing's analysis points).
    """

    alt_ft: float | None = None
    terrain_ft: float | None = None
    gs_kt: float | None = None
    track_deg: float | None = None
    h_accuracy_m: float | None = None
    along_nm: float | None = None
    cross_nm: float | None = None
    course_deg: float | None = None
    route_nm: float | None = None
    client_along_nm: float | None = None
    client_cross_nm: float | None = None

    @classmethod
    def from_record(cls, rec: dict) -> FixData | None:
        """The fix fields of a history record, None when it carries none."""
        values = {f.name: _num(rec.get(f.name)) for f in fields(cls)}
        return cls(**values) if any(v is not None for v in values.values()) else None


@dataclass(frozen=True)
class ProgressEvent:
    """One observed progress event: a take-off, a landing or a position fix.

    ``at`` is when it happened (None withdraws the kind); ``recorded_at`` is
    when the server learned it (the history record's ``tick_at``).
    """

    kind: str
    at: datetime | None
    recorded_at: datetime
    source: str = SOURCE_MANUAL
    lat: float | None = None
    lon: float | None = None
    fix: FixData | None = None

    def history_record(self) -> dict:
        """The ``live_history.jsonl`` record for this event."""
        rec = {
            "tick_at": self.recorded_at.isoformat(), "type": HISTORY_TYPE,
            "event": self.kind, "at": self.at.isoformat() if self.at is not None else None,
            "source": self.source,
        }
        if self.lat is not None and self.lon is not None:
            rec["lat"], rec["lon"] = self.lat, self.lon
        if self.fix is not None:
            rec.update({f.name: getattr(self.fix, f.name) for f in fields(FixData)
                        if getattr(self.fix, f.name) is not None})
        return rec


def progress_events(history: list[dict]) -> tuple[ProgressEvent, ...]:
    """The progress events in a flight's history records, in record order.
    Malformed records are skipped (the history never raises on read)."""
    out = []
    for r in history:
        if r.get("type") != HISTORY_TYPE or r.get("event") not in EVENT_KINDS:
            continue
        recorded = _dt(r.get("tick_at"))
        if recorded is None:
            continue
        at = _dt(r.get("at"))
        if at is None and r.get("at") is not None:
            continue  # unreadable time: skip rather than read it as a withdrawal
        out.append(ProgressEvent(
            kind=r["event"], at=at, recorded_at=recorded, source=r.get("source") or SOURCE_MANUAL,
            lat=_num(r.get("lat")), lon=_num(r.get("lon")),
            fix=FixData.from_record(r) if r["event"] == EVENT_FIX else None,
        ))
    return tuple(out)


@dataclass(frozen=True)
class _Seen:
    """A fix as the fusion reads it: projected on the route in hand."""

    event: ProgressEvent
    along: float
    cross: float
    course: float | None

    @property
    def at(self) -> datetime:
        return self.event.at

    @property
    def gs(self) -> float | None:
        """The ground speed, None when unknown (the device marks it invalid)."""
        gs = self.event.fix.gs_kt if self.event.fix is not None else None
        return gs if gs is not None and gs >= 0 else None

    @property
    def height(self) -> float | None:
        """Height above the terrain under the fix, None without both."""
        f = self.event.fix
        if f is None or f.alt_ft is None or f.terrain_ft is None:
            return None
        return f.alt_ft - f.terrain_ft

    @property
    def airborne(self) -> bool:
        """High above the terrain, or fast when the height is unknown. A
        known height decides: fast and low is a car (or the climb-out)."""
        gs, h = self.gs, self.height
        if h is not None:
            return h >= AIRBORNE_AGL_FT
        return gs is not None and gs >= TAKEOFF_GS_KT

    @property
    def on_ground(self) -> bool:
        """Positively on the ground: low and not fast, or slow when the
        height is unknown. A fix with neither (or fast and low) shows
        nothing either way."""
        gs, h = self.gs, self.height
        if h is not None:
            return h < AIRBORNE_AGL_FT and (gs is None or gs < TAKEOFF_GS_KT)
        return gs is not None and gs < SLOW_GS_KT

    @property
    def at_departure(self) -> bool:
        return self.along <= NEAR_DEPARTURE_NM and self.cross <= NEAR_DEPARTURE_NM

    def at_destination(self, total: float) -> bool:
        return self.along >= total - NEAR_DESTINATION_NM and self.cross <= NEAR_DESTINATION_NM

    @property
    def on_ground_at_departure(self) -> bool:
        return self.on_ground and self.at_departure

    def landing(self, total: float) -> bool:
        """Not airborne at the destination: what a landing looks like."""
        return not self.airborne and self.at_destination(total)


@dataclass(frozen=True)
class _Anchor:
    """The fix the position is extrapolated from, and the floor the
    never-backwards rule holds it at (from ``floor_at``)."""

    seen: _Seen
    v_kt: float
    floor_nm: float | None = None
    floor_at: datetime | None = None

    @property
    def t0(self) -> datetime:
        return self.seen.at

    def flown(self, t: datetime, total: float) -> float:
        val = self.seen.along + self.v_kt * (t - self.t0).total_seconds() / 3600.0
        if self.floor_nm is not None and t >= self.floor_at:
            val = max(val, self.floor_nm)
        return max(0.0, min(total, val))


@dataclass(frozen=True)
class FlightProgress:
    """The flight's progress along ``track`` as of ``as_of``. Without a
    track (:meth:`schedule`) only the times are known: departed, arrival,
    landed, which is what the live window needs."""

    track: RouteTrack | None
    planned_departure: datetime | None
    duration_h: float
    as_of: datetime
    events: tuple[ProgressEvent, ...] = ()

    @classmethod
    def from_route(
        cls, route: RouteConfig, departure: datetime | None, as_of: datetime,
        events: tuple[ProgressEvent, ...] | list[ProgressEvent] = (),
    ) -> FlightProgress:
        """The progress of ``route`` planned to depart at ``departure``,
        corrected by ``events``."""
        return cls(
            RouteTrack.from_route(route), departure, route.flight_duration_hours or 0.0, as_of,
            tuple(events),
        )

    @classmethod
    def schedule(
        cls, departure: datetime | None, duration_h: float | None, as_of: datetime,
        events: tuple[ProgressEvent, ...] | list[ProgressEvent] = (),
    ) -> FlightProgress:
        """Times only, no route (the live window, push expiry): distances and
        positions read as a zero-length route; fixes use their stored
        projection."""
        return cls(None, departure, duration_h or 0.0, as_of, tuple(events))

    def at(self, as_of: datetime) -> FlightProgress:
        """The same flight's progress as of another time, knowing only the
        events recorded by then (read-time trails rebuild what each past
        tick knew)."""
        return replace(self, as_of=as_of)

    def planned(self) -> FlightProgress:
        """The same flight on its plan only, ignoring observed events: what a
        cancelled flight's recap uses (#711)."""
        return replace(self, events=())

    @property
    def time_base(self) -> str:
        """``planned`` / ``partial`` / ``observed``: which times the schedule
        below comes from (#711). ``observed`` once both the take-off and the
        landing are entered (or the app detected the landing), ``partial``
        with only one of them (#760). What the fixes infer is an estimate and
        does not count."""
        observed = (self._latest(EVENT_TAKEOFF) is not None) + (self.landing_event_at is not None)
        return (TIME_BASE_PLANNED, TIME_BASE_PARTIAL, TIME_BASE_OBSERVED)[observed]

    # --- Observed events -------------------------------------------------------

    def latest_event(self, kind: str) -> ProgressEvent | None:
        """The ``kind`` event in effect as of ``as_of`` (a withdrawal
        included), None when there is none: the one recorded last, except
        that an automatic one never replaces a manual entry."""
        current: ProgressEvent | None = None
        recorded = [e for e in self.events if e.kind == kind and e.recorded_at <= self.as_of]
        for e in sorted(recorded, key=lambda e: e.recorded_at):
            if (e.source != SOURCE_MANUAL and current is not None
                    and current.source == SOURCE_MANUAL and current.at is not None):
                continue
            current = e
        return current

    def _latest(self, kind: str) -> datetime | None:
        """The ``at`` of :meth:`latest_event` (None when withdrawn)."""
        latest = self.latest_event(kind)
        return latest.at if latest is not None else None

    @property
    def takeoff_at(self) -> datetime | None:
        """The take-off (entered, else inferred from the fixes), None when
        only the plan says so."""
        entered = self._latest(EVENT_TAKEOFF)
        if entered is not None:
            return entered
        inferred = self._inferred_takeoff
        return inferred[0] if inferred is not None else None

    @property
    def landing_event_at(self) -> datetime | None:
        """The landing entered (or detected by the app), None without one or
        when it is not after the departure it would close."""
        landed = self._latest(EVENT_LANDED)
        dep = self.departed_at
        if landed is None or (dep is not None and landed <= dep):
            return None
        return landed

    @property
    def landed_at(self) -> datetime | None:
        """The landing (entered or app-detected, else inferred from a fix at
        the destination), None when only the plan says so."""
        landed = self.landing_event_at
        if landed is not None:
            return landed
        inferred = self._inferred_landing
        return inferred[0] if inferred is not None else None

    @property
    def source(self) -> str:
        fix = self.fix
        if fix is not None:
            return fix.source
        if self.takeoff_at is not None or self.landed_at is not None:
            return SOURCE_PLAN_TAKEOFF
        return SOURCE_PLAN

    @property
    def delay_min(self) -> float | None:
        """Take-off minus planned departure, minutes (+ = late)."""
        if self.takeoff_at is None or self.planned_departure is None:
            return None
        return (self.takeoff_at - self.planned_departure).total_seconds() / 60.0

    # --- Fixes (#761) ------------------------------------------------------------

    @cached_property
    def _route_nm(self) -> float:
        """The route length fixes are measured on: the track's, else the one
        the newest fix was projected against (``schedule``)."""
        if self.track is not None:
            return self.track.total_nm
        for e in reversed(self.events):
            if e.fix is not None and e.fix.route_nm:
                return e.fix.route_nm
        return 0.0

    def _project(self, e: ProgressEvent) -> _Seen | None:
        f = e.fix
        stored = f is not None and f.along_nm is not None and f.cross_nm is not None
        if self.track is not None and e.lat is not None and e.lon is not None and not (
            stored and f.route_nm is not None and abs(f.route_nm - self.track.total_nm) <= ROUTE_MATCH_NM
        ):
            p = self.track.project(e.lat, e.lon)
            return _Seen(e, p.along_nm, p.offtrack_nm, p.track_deg)
        if stored:
            return _Seen(e, f.along_nm, abs(f.cross_nm), f.course_deg)
        return None

    @cached_property
    def _fixes(self) -> tuple[_Seen, ...]:
        """The fixes known as of ``as_of`` and close enough to the route to
        be this flight, projected, in fix-time order."""
        seen = [s for e in self.events
                if e.kind == EVENT_FIX and e.at is not None and e.recorded_at <= self.as_of
                and (s := self._project(e)) is not None and s.cross <= FAR_OFF_ROUTE_NM]
        return tuple(sorted(seen, key=lambda s: s.at))

    @property
    def _planned_kt(self) -> float:
        return self._route_nm / self.duration_h if self.duration_h > 0 else 0.0

    @cached_property
    def _inferred_takeoff(self) -> tuple[datetime, ProgressEvent] | None:
        """The take-off the fixes show, and the fix that shows it: the first
        airborne fix after the last one on the ground, back-dated to the
        departure at the planned speed, never before that ground fix. A
        ground fix at the departure resets it (a return to the departure)."""
        takeoff = last_ground = None
        v = self._planned_kt
        for s in self._fixes:
            if s.on_ground:
                last_ground = s.at
                if s.at_departure:
                    takeoff = None
            elif takeoff is None and s.airborne:
                t = s.at - timedelta(hours=s.along / v) if v > 0 else s.at
                takeoff = (max(t, last_ground) if last_ground is not None else t, s.event)
        return takeoff

    @cached_property
    def _ground_fix(self) -> ProgressEvent | None:
        """The newest fix that shows anything, when it puts the aircraft on
        the ground at the departure and no take-off is known."""
        if self.takeoff_at is not None:
            return None
        telling = [s for s in self._fixes if s.on_ground or s.airborne]
        if not telling or not telling[-1].on_ground_at_departure:
            return None
        return telling[-1].event

    def _speed(self, s: _Seen, known: list[_Seen]) -> float:
        """The speed to fly on from ``s``: the planned one, scaled by the
        speed actually flown since an earlier fix (or the entered take-off)
        far enough back to measure it."""
        v = self._planned_kt
        if v <= 0 or s.cross > OFF_ROUTE_NM:
            return v
        refs = [(r.at, r.along) for r in known
                if r.at < s.at and r.cross <= OFF_ROUTE_NM and not r.on_ground]
        entered = self._latest(EVENT_TAKEOFF)
        if entered is not None:
            refs.append((entered, 0.0))
        for t, along in sorted(refs, reverse=True):
            gap, nm = s.at - t, s.along - along
            if gap >= SPEED_MIN_GAP and nm >= SPEED_MIN_NM:
                lo, hi = SPEED_RATIO
                return v * max(lo, min(hi, nm / (gap.total_seconds() / 3600.0) / v))
        return v

    @cached_property
    def _anchor(self) -> _Anchor | None:
        """The freshest fix after the departure (and before a landing), with
        the never-backwards floor. Fixes are replayed in the order the server
        learned them, each compared with what the previous anchor showed then;
        a fix that arrives late changes no anchor but still measures speed."""
        if self.takeoff_at is None:
            return None
        dep, landed, total = self.departed_at, self.landing_event_at, self._route_nm
        candidates = [s for s in self._fixes
                      if s.at >= dep and (landed is None or s.at <= landed)
                      and not s.on_ground_at_departure and not s.landing(total)]
        anchor: _Anchor | None = None
        pending_reversal = False
        known: list[_Seen] = []
        for s in sorted(candidates, key=lambda s: (s.event.recorded_at, s.at)):
            known.append(s)
            if anchor is not None and s.at <= anchor.t0:
                anchor = replace(anchor, v_kt=self._speed(anchor.seen, known))
                continue
            new = _Anchor(s, self._speed(s, known))
            if anchor is not None:
                when = s.event.recorded_at
                shown = anchor.flown(when, total)
                back = shown - new.flown(when, total)
                hold = False
                if back <= 0:
                    pending_reversal = False
                elif back <= REVERSAL_NM:
                    hold, pending_reversal = True, False
                elif s.at - anchor.t0 > JITTER_GAP or pending_reversal:
                    pending_reversal = False  # a fresh observation, or sustained
                else:
                    hold, pending_reversal = True, True
                if hold:
                    new = replace(new, floor_nm=shown, floor_at=when)
            anchor = new
        return anchor

    @property
    def _arrival_unlanded(self) -> datetime | None:
        """The arrival the departure and the anchor give, ignoring any landing."""
        dep = self.departed_at
        if dep is None or self.duration_h <= 0:
            return None
        a = self._anchor
        if a is not None and a.v_kt > 0:
            return a.t0 + timedelta(hours=max(0.0, self._route_nm - a.seen.along) / a.v_kt)
        return dep + timedelta(hours=self.duration_h)

    @cached_property
    def _inferred_landing(self) -> tuple[datetime, ProgressEvent] | None:
        """The landing a fix shows, and that fix: the first not airborne at
        the destination once flying there at the fastest plausible speed
        (``SPEED_RATIO``) could have got there, landed at the earlier of the
        fix and the expected arrival (never before the anchor's fix)."""
        dep, expected = self.departed_at, self._arrival_unlanded
        if dep is None or expected is None:
            return None
        earliest = dep + (expected - dep) / SPEED_RATIO[1]
        total = self._route_nm
        for s in self._fixes:
            if s.at > dep and s.at >= earliest and s.landing(total):
                t = min(s.at, expected)
                if self._anchor is not None:
                    t = max(t, self._anchor.t0)
                return t, s.event
        return None

    @property
    def fix(self) -> ProgressEvent | None:
        """The fix the position comes from: the one the landing was inferred
        from, else the anchor, else the ground fix holding the departure,
        else the fix the take-off was inferred from."""
        if self.landing_event_at is None and self._inferred_landing is not None:
            return self._inferred_landing[1]
        if self._anchor is not None:
            return self._anchor.seen.event
        g, planned = self._ground_fix, self.planned_departure
        if g is not None and planned is not None and g.at + TAXI_MARGIN > planned:
            return g
        if self._latest(EVENT_TAKEOFF) is None and self._inferred_takeoff is not None:
            return self._inferred_takeoff[1]
        return None

    @property
    def fix_age_min(self) -> float | None:
        fix = self.fix
        return (self.as_of - fix.at).total_seconds() / 60.0 if fix is not None else None

    @property
    def estimated(self) -> bool:
        """The position is extrapolated from the plan or a fix older than
        ``STALE_FIX``."""
        age = self.fix_age_min
        return age is None or age > STALE_FIX.total_seconds() / 60.0

    @property
    def off_route(self) -> bool:
        return self._anchor is not None and self._anchor.seen.cross > OFF_ROUTE_NM

    # --- Schedule --------------------------------------------------------------

    @property
    def total_nm(self) -> float:
        return self.track.total_nm if self.track is not None else 0.0

    @property
    def departed_at(self) -> datetime | None:
        """The effective departure every schedule computation measures from:
        the take-off, else the plan (moved to ``TAXI_MARGIN`` after a ground
        fix at the departure). Read this, never ``planned_departure``, so a
        better source moves them all."""
        if self.takeoff_at is not None:
            return self.takeoff_at
        planned, g = self.planned_departure, self._ground_fix
        if g is not None and planned is not None:
            return max(planned, g.at + TAXI_MARGIN)
        return planned

    @property
    def _duration_h(self) -> float:
        """Departure to arrival, hours: the time actually flown once landed,
        else the planned duration. Kept in float hours, as the plan-only code
        computed it, so a flight without events is unchanged to the bit."""
        landed, dep = self.landed_at, self.departed_at
        if landed is not None and dep is not None:
            return (landed - dep).total_seconds() / 3600.0
        return self.duration_h

    @property
    def timed(self) -> bool:
        """A departure time and a duration to measure progress with."""
        return self.departed_at is not None and self._duration_h > 0

    @property
    def departed(self) -> bool:
        return self.departed_at is not None and self.as_of >= self.departed_at

    @property
    def arrival(self) -> datetime | None:
        """When the flight lands: the landing, else extrapolated from the fix
        in use, else the departure plus the planned duration."""
        if self.landed_at is not None:
            return self.landed_at
        if not self.timed:
            return None
        return self._arrival_unlanded

    @property
    def arrived(self) -> bool:
        return self.arrival is not None and self.as_of >= self.arrival

    # --- Along the route ---------------------------------------------------------

    def flown_at(self, t: datetime) -> float | None:
        """Distance flown at ``t``: 0 before departure, the route length after
        arrival, None when the flight is not timed."""
        if not self.timed:
            return None
        a = self._anchor
        if a is not None:
            if t < self.departed_at:
                return 0.0
            if self.landed_at is not None and t >= self.landed_at:
                return self.total_nm
            return min(a.flown(t, self._route_nm), self.total_nm)
        frac = (t - self.departed_at).total_seconds() / 3600.0 / self._duration_h
        return max(0.0, min(1.0, frac)) * self.total_nm

    @property
    def flown_nm(self) -> float | None:
        """Distance flown as of ``as_of``."""
        return self.flown_at(self.as_of)

    def eta(self, along_nm: float | None) -> datetime | None:
        """When the flight is abeam ``along_nm`` (clamped to the route)."""
        if not self.timed or along_nm is None or self.total_nm <= 0:
            return None
        a = self._anchor
        if a is not None and a.v_kt > 0:
            x = max(0.0, min(self.total_nm, along_nm))
            t = a.t0 + timedelta(hours=(x - a.seen.along) / a.v_kt)
            if a.floor_nm is not None and x <= a.floor_nm:
                t = min(t, a.floor_at)  # held past it: passed when the hold began
            t = max(t, self.departed_at)
            return min(t, self.landed_at) if self.landed_at is not None else t
        frac = max(0.0, min(1.0, along_nm / self.total_nm))
        return self.departed_at + timedelta(hours=frac * self._duration_h)

    def position(self, t: datetime) -> tuple[float, float] | None:
        """(lat, lon) of the aircraft at ``t``, None when not timed."""
        flown = self.flown_at(t)
        return None if flown is None or self.track is None else self.track.position_at(flown)
