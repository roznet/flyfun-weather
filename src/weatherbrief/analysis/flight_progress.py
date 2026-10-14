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
- **position fixes** (#761, app GPS; ADS-B #762 later): the freshest fix
  anchors the position, extrapolated at its along-track ground speed. A fix
  also infers the take-off (the first one moving at ``TAKEOFF_GS_KT``) and,
  on the ground at the departure, holds the departure back. The source reads
  the fix's (``app``). The rules are under "Fixes" below.

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

**Fixes** (#759 rules, #761):

- Projected on the route when received (``FixData.along_nm`` / ``cross_nm``,
  against the latest pack's route); re-projected here when the route in hand
  has another length (a pack with a different route). Without a route (the
  live window, :meth:`schedule`) the stored projection is all there is.
- The anchor is the freshest fix after the departure (by fix time, not
  source); ``flown(t) = along0 + v·(t − t0)``, capped at the route's end.
  ``v`` is the along-track ground speed, or the planned speed when it is
  under ``SLOW_GS_KT``, unknown, or the fix is off route.
- ``off_route`` beyond ``OFF_ROUTE_NM`` cross-track; the along-route
  projection is kept (diversions are not detected).
- ``estimated`` once the fix is older than ``STALE_FIX``. A stale fix never
  snaps back to the plan: it keeps extrapolating.
- Never backwards: a fix behind the position the previous anchor showed when
  it arrived holds that position as a floor (the marker waits for the
  aircraft) unless it is more than ``REVERSAL_NM`` behind for two fixes in a
  row. The first fix of a flight always replaces the plan: the plan is not
  an observation, and a late departure is exactly a fix behind it.
- Take-off: the first fix moving at ``TAKEOFF_GS_KT`` or more (or of unknown
  speed beyond ``NEAR_DEPARTURE_NM`` of the departure) after the last fix on
  the ground at the departure, back-extrapolated to along 0 at its speed,
  never before that ground fix. A later ground fix at the departure resets
  it (a fast fix on the road to the airfield, a return to the departure).
- On the ground: the latest fix at taxi speed within ``NEAR_DEPARTURE_NM``
  of the departure, nothing seen airborne, after the planned departure: not
  departed, ETAs measured from now. The hold lasts ``GROUND_HOLD`` after the
  fix; then the flight is taken to have left when it ran out, so a phone
  pocketed on the ground cannot keep the flight "not departed" all day.
"""

from __future__ import annotations

import math
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
#: Under this: taxi speed on the ground, and a speed not worth extrapolating
#: with in the air (the planned speed is used instead).
SLOW_GS_KT = 40.0
#: "At the departure": within this of the route's first point, along and across.
NEAR_DEPARTURE_NM = 3.0
#: Cross-track beyond this is off route (planned speed, ``off_route``).
OFF_ROUTE_NM = 10.0
#: A fix older than this makes the position ``estimated``.
STALE_FIX = timedelta(minutes=20)
#: A fix on the ground at the departure holds the departure back this long.
GROUND_HOLD = STALE_FIX
#: A fix may move the position back only by more than this, two fixes in a row.
REVERSAL_NM = 10.0
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
    route's length. ``client_*`` is the device's own projection, kept as a
    cross-check only (it projects on the briefing's analysis points).
    """

    alt_ft: float | None = None
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
    def gs(self) -> float | None:
        return self.event.fix.gs_kt if self.event.fix is not None else None

    @property
    def along_gs(self) -> float | None:
        """The ground speed's component along the route, None when unknown."""
        gs = self.gs
        if gs is None or gs < 0:
            return None
        track = self.event.fix.track_deg
        if track is None or track < 0 or self.course is None:
            return gs
        return gs * math.cos(math.radians(track - self.course))

    @property
    def on_ground_at_departure(self) -> bool:
        gs = self.gs
        return (gs is not None and 0 <= gs < SLOW_GS_KT
                and self.along <= NEAR_DEPARTURE_NM and self.cross <= NEAR_DEPARTURE_NM)

    @property
    def shows_departed(self) -> bool:
        gs = self.gs
        if gs is not None and gs >= 0:
            return gs >= TAKEOFF_GS_KT
        return self.along > NEAR_DEPARTURE_NM


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
        return self.seen.event.at

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
        landing are known (entered or inferred from fixes), ``partial`` with
        only one of them (#760)."""
        observed = (self.takeoff_at is not None) + (self.landed_at is not None)
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
        """The observed take-off (entered, else inferred from the fixes),
        None when only the plan says so."""
        return self._latest(EVENT_TAKEOFF) or self._inferred_takeoff

    @property
    def landed_at(self) -> datetime | None:
        """The observed landing, None when only the plan says so (or the
        entry is not after the departure it would close)."""
        landed = self._latest(EVENT_LANDED)
        dep = self.departed_at
        if landed is None or (dep is not None and landed <= dep):
            return None
        return landed

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
        """Observed take-off minus planned departure, minutes (+ = late)."""
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
        """The fixes known as of ``as_of``, projected, in fix-time order."""
        seen = [s for e in self.events
                if e.kind == EVENT_FIX and e.at is not None and e.recorded_at <= self.as_of
                and (s := self._project(e)) is not None]
        return tuple(sorted(seen, key=lambda s: s.event.at))

    @cached_property
    def _inferred_takeoff(self) -> datetime | None:
        """The take-off the fixes show: the first fix seen departed after the
        last one on the ground at the departure, back-extrapolated to the
        departure, never before that ground fix. A ground fix resets it: a
        fast fix on the road to the airfield, or a return to the departure,
        is not the take-off."""
        takeoff = last_ground = None
        for s in self._fixes:
            if s.on_ground_at_departure:
                takeoff, last_ground = None, s.event.at
            elif takeoff is None and s.shows_departed:
                v = self._speed(s)
                t = s.event.at - timedelta(hours=s.along / v) if v > 0 else s.event.at
                takeoff = max(t, last_ground) if last_ground is not None else t
        return takeoff

    @cached_property
    def _ground_fix(self) -> ProgressEvent | None:
        """The newest fix, when it puts the aircraft on the ground at the
        departure and no take-off is known."""
        if self.takeoff_at is not None or not self._fixes:
            return None
        newest = self._fixes[-1]
        return newest.event if newest.on_ground_at_departure else None

    @property
    def _holding(self) -> bool:
        """On the ground at the departure past the planned time, with a fix
        recent enough to say so: not departed yet."""
        g, planned = self._ground_fix, self.planned_departure
        return (g is not None and planned is not None
                and planned < self.as_of < g.at + GROUND_HOLD)

    @property
    def _planned_kt(self) -> float:
        return self._route_nm / self.duration_h if self.duration_h > 0 else 0.0

    def _speed(self, s: _Seen) -> float:
        """Along-track ground speed to extrapolate with, or the planned one."""
        v = s.along_gs
        if v is None or v < SLOW_GS_KT or s.cross > OFF_ROUTE_NM:
            return self._planned_kt
        return v

    @cached_property
    def _anchor(self) -> _Anchor | None:
        """The freshest fix after the departure (and before a landing), with
        the never-backwards floor. Fixes are replayed in the order the server
        learned them, each compared with what the previous anchor showed then."""
        if self.takeoff_at is None:
            return None
        dep, landed = self.departed_at, self.landed_at
        candidates = [s for s in self._fixes
                      if s.event.at >= dep and (landed is None or s.event.at <= landed)
                      and not s.on_ground_at_departure]
        total = self._route_nm
        anchor: _Anchor | None = None
        pending_reversal = False
        for s in sorted(candidates, key=lambda s: (s.event.recorded_at, s.event.at)):
            if anchor is not None and s.event.at <= anchor.t0:
                continue  # older than the fix in use: arrived late, changes nothing
            new = _Anchor(s, self._speed(s))
            if anchor is not None:
                when = s.event.recorded_at
                shown, mine = anchor.flown(when, total), new.flown(when, total)
                back = shown - mine
                if back > 0:
                    if back > REVERSAL_NM and pending_reversal:
                        pending_reversal = False  # sustained: accept the reversal
                    else:
                        pending_reversal = back > REVERSAL_NM
                        new = replace(new, floor_nm=shown, floor_at=when)
                else:
                    pending_reversal = False
            anchor = new
        return anchor

    @property
    def fix(self) -> ProgressEvent | None:
        """The fix the position comes from: the anchor, else the ground fix
        holding the departure, else the fix the take-off was inferred from."""
        if self._anchor is not None:
            return self._anchor.seen.event
        if self._ground_fix is not None and self.planned_departure is not None \
                and self.as_of > self.planned_departure:
            return self._ground_fix
        if self._latest(EVENT_TAKEOFF) is None and self._inferred_takeoff is not None:
            return next(s.event for s in self._fixes if s.shows_departed)
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
        the observed take-off, else the plan (held back by a ground fix).
        Read this, never ``planned_departure``, so a better source moves
        them all."""
        if self.takeoff_at is not None:
            return self.takeoff_at
        planned, g = self.planned_departure, self._ground_fix
        if g is not None and planned is not None and self.as_of > planned:
            return max(planned, min(self.as_of, g.at + GROUND_HOLD))
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
        return self.departed_at is not None and self.as_of >= self.departed_at and not self._holding

    @property
    def arrival(self) -> datetime | None:
        """When the flight lands: the observed landing, else extrapolated
        from the fix in use, else the departure plus the planned duration."""
        if self.landed_at is not None:
            return self.landed_at
        if not self.timed:
            return None
        a = self._anchor
        if a is not None and a.v_kt > 0:
            return a.t0 + timedelta(hours=(self._route_nm - a.seen.along) / a.v_kt)
        return self.departed_at + timedelta(hours=self._duration_h)

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
