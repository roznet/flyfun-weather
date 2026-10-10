"""Where the flight is along its route, and when it gets everywhere else (#759).

One object answers every progress question the live layer asks: distance flown,
departed / arrived, the ETA at a point along the route, the arrival time and the
position at a time. Every consumer (classifier, radar storms, glance and ribbon,
read-time trails, the ↻ path) goes through it, so a better source than the plan
changes them all at once.

Today the only source is the **plan**: departure on time, constant speed over
``duration_h``. The observed sources of #759 (manual take-off / landed, app GPS,
ADS-B) replace the plan inside this object; the consumers do not change.

``timed`` (a departure and a duration) is what flown distance, departed and the
arrival need. ETAs and positions also need a route of non-zero length; on a
zero-length route (a local flight with the same airport at both ends) they are
None and the flown distance is 0.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from weatherbrief.analysis.route_geometry import RouteTrack
from weatherbrief.models.analysis import RouteConfig

#: The only source until #759's observed sources land.
SOURCE_PLAN = "plan"


@dataclass(frozen=True)
class FlightProgress:
    """The flight's progress along ``track`` as of ``as_of``."""

    track: RouteTrack
    planned_departure: datetime | None
    duration_h: float
    as_of: datetime
    source: str = SOURCE_PLAN

    @classmethod
    def from_route(cls, route: RouteConfig, departure: datetime | None, as_of: datetime) -> FlightProgress:
        """The planned progress of ``route`` departing at ``departure``."""
        return cls(RouteTrack.from_route(route), departure, route.flight_duration_hours or 0.0, as_of)

    def at(self, as_of: datetime) -> FlightProgress:
        """The same flight's progress as of another time (read-time trails
        rebuild what each past tick knew)."""
        return replace(self, as_of=as_of)

    # --- Schedule --------------------------------------------------------------

    @property
    def total_nm(self) -> float:
        return self.track.total_nm

    @property
    def timed(self) -> bool:
        """A departure time and a duration to measure progress with."""
        return self.planned_departure is not None and self.duration_h > 0

    @property
    def departed_at(self) -> datetime | None:
        return self.planned_departure

    @property
    def departed(self) -> bool:
        return self.departed_at is not None and self.as_of >= self.departed_at

    @property
    def arrival(self) -> datetime | None:
        """When the flight lands (the planned arrival for the plan source)."""
        if not self.timed:
            return None
        return self.planned_departure + timedelta(hours=self.duration_h)

    @property
    def arrived(self) -> bool:
        return self.arrival is not None and self.as_of >= self.arrival

    # --- Along the route ---------------------------------------------------------

    def flown_at(self, t: datetime) -> float | None:
        """Distance flown at ``t``: 0 before departure, the route length after
        arrival, None when the flight is not timed."""
        if not self.timed:
            return None
        frac = (t - self.planned_departure).total_seconds() / 3600.0 / self.duration_h
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
        return self.planned_departure + timedelta(hours=frac * self.duration_h)

    def position(self, t: datetime) -> tuple[float, float] | None:
        """(lat, lon) of the aircraft at ``t``, None when not timed."""
        flown = self.flown_at(t)
        return None if flown is None else self.track.position_at(flown)
