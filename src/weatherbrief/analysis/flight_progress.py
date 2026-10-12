"""Where the flight is along its route, and when it gets everywhere else (#759).

One object answers every progress question the live layer asks: distance flown,
departed / arrived, the ETA at a point along the route, the arrival time and the
position at a time. Every consumer (classifier, radar storms, glance and ribbon,
read-time trails, the ↻ path, TAF-at-ETA, the live window) goes through it, so a
better source than the plan changes them all at once.

Sources so far (#760):

- **plan**: departure on time, constant speed over ``duration_h``.
- **manual take-off / landed** events (``ProgressEvent``, posted by the owner,
  stored in ``live_history.jsonl``): a take-off replaces the planned departure,
  a landing replaces the planned arrival. With both, the speed is the one
  actually flown. The source reads ``plan+takeoff`` once either is in effect.

``fix`` events (app GPS #761, ADS-B #762) are parsed and carried but not fused
yet: their rules (freshest fix, never backwards, off-route) land with #761.

``timed`` (a departure and a duration) is what flown distance, departed and the
arrival need. ETAs and positions also need a route of non-zero length; on a
zero-length route (a local flight with the same airport at both ends) they are
None and the flown distance is 0.

**As of a time.** Each event carries ``recorded_at``, when the server learned
it; :meth:`FlightProgress.at` only counts events recorded by then, so a
read-time trail rebuilds exactly what a past tick knew. Within a kind, the
event recorded last wins (an edit replaces the earlier entry); one with
``at=None`` withdraws that kind (back to the plan).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from weatherbrief.analysis.route_geometry import RouteTrack
from weatherbrief.models.analysis import RouteConfig

SOURCE_PLAN = "plan"
#: The plan corrected by a manual take-off and/or landing (#760). Named for
#: the common case; a landing alone reads the same (both are manual entries
#: over the plan's speed).
SOURCE_PLAN_TAKEOFF = "plan+takeoff"

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


@dataclass(frozen=True)
class ProgressEvent:
    """One observed progress event: a take-off, a landing or a position fix.

    ``at`` is when it happened (None withdraws the kind); ``recorded_at`` is
    when the server learned it (the history record's ``tick_at``).
    """

    kind: str
    at: datetime | None
    recorded_at: datetime
    source: str = "manual"
    lat: float | None = None
    lon: float | None = None

    def history_record(self) -> dict:
        """The ``live_history.jsonl`` record for this event."""
        rec = {
            "tick_at": self.recorded_at.isoformat(), "type": HISTORY_TYPE,
            "event": self.kind, "at": self.at.isoformat() if self.at is not None else None,
            "source": self.source,
        }
        if self.lat is not None and self.lon is not None:
            rec["lat"], rec["lon"] = self.lat, self.lon
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
            kind=r["event"], at=at, recorded_at=recorded, source=r.get("source") or "manual",
            lat=r.get("lat"), lon=r.get("lon"),
        ))
    return tuple(out)


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
        positions read as a zero-length route."""
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
        landing are entered, ``partial`` with only one of them (#760)."""
        observed = (self.takeoff_at is not None) + (self.landed_at is not None)
        return (TIME_BASE_PLANNED, TIME_BASE_PARTIAL, TIME_BASE_OBSERVED)[observed]

    # --- Observed events -------------------------------------------------------

    def latest_event(self, kind: str) -> ProgressEvent | None:
        """The last ``kind`` event recorded by ``as_of`` (a withdrawal
        included), None when there is none."""
        latest: ProgressEvent | None = None
        for e in self.events:
            if e.kind == kind and e.recorded_at <= self.as_of and (
                latest is None or e.recorded_at >= latest.recorded_at
            ):
                latest = e
        return latest

    def _latest(self, kind: str) -> datetime | None:
        """The ``at`` of :meth:`latest_event` (None when withdrawn)."""
        latest = self.latest_event(kind)
        return latest.at if latest is not None else None

    @property
    def takeoff_at(self) -> datetime | None:
        """The observed take-off, None when only the plan says so."""
        return self._latest(EVENT_TAKEOFF)

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
        if self.takeoff_at is not None or self.landed_at is not None:
            return SOURCE_PLAN_TAKEOFF
        return SOURCE_PLAN

    @property
    def delay_min(self) -> float | None:
        """Observed take-off minus planned departure, minutes (+ = late)."""
        if self.takeoff_at is None or self.planned_departure is None:
            return None
        return (self.takeoff_at - self.planned_departure).total_seconds() / 60.0

    # --- Schedule --------------------------------------------------------------

    @property
    def total_nm(self) -> float:
        return self.track.total_nm if self.track is not None else 0.0

    @property
    def departed_at(self) -> datetime | None:
        """The effective departure every schedule computation measures from:
        the observed take-off, else the plan. Read this, never
        ``planned_departure``, so a better source moves them all."""
        return self.takeoff_at or self.planned_departure

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
        """When the flight lands: the observed landing, else the departure
        plus the planned duration."""
        if self.landed_at is not None:
            return self.landed_at
        if not self.timed:
            return None
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
        frac = max(0.0, min(1.0, along_nm / self.total_nm))
        return self.departed_at + timedelta(hours=frac * self._duration_h)

    def position(self, t: datetime) -> tuple[float, float] | None:
        """(lat, lon) of the aircraft at ``t``, None when not timed."""
        flown = self.flown_at(t)
        return None if flown is None or self.track is None else self.track.position_at(flown)
