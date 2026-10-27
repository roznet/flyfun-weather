"""Overnight surface readouts at the departure airport, for the frost advisory (#949).

Pure analysis (no I/O, no imports from ``tasks/``). The frost advisory asks
"will an aircraft parked outside overnight have frost on it at departure?",
which needs the night *before* the departure, not the conditions at the
departure time that :mod:`airport_conditions` reads. Those hours are already
in the departure route point's ``WaypointForecast.hourly`` of every
cross-section, so this is computed on demand from the ``RouteContext`` (no
pipeline artifact): recalculation, the alternate-departure re-run and the
timing scan all get it for free, and the window moves with a shifted departure.

**Honesty (timing-scenarios invariant):** an hour counts only when the model
actually carries a 2 m temperature for it. GRIB-primary slots are skeletons
whose hours outside the flight window are all None, and Open-Meteo slots start
at 00Z of the departure date, so a model can legitimately have no night at all.
Nothing here clamps to the nearest hour (``at_time``); a model without enough
real hours is reported as such and graded UNAVAILABLE by the evaluator.

The thresholds live in the evaluator (user parameters, recalc without
refetch); this module only collects the raw hourly values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from weatherbrief.models import RouteCrossSection, RoutePointAnalysis

# The window never reaches further back than this before departure, even when
# the previous sunset is earlier (long winter nights): frost that formed in the
# evening and was then mixed away by a front is not the departure's problem.
MAX_WINDOW_H = 12.0

# A model needs at least this many valued hours in the window, one of them at
# most ``LATEST_HOUR_SLACK_H`` before departure, to be graded at all. Fewer
# hours than that is a sliver of the night, not the night.
MIN_VALUED_HOURS = 3
LATEST_HOUR_SLACK_H = 1.0


@dataclass(frozen=True)
class FrostHour:
    """One forecast hour at the departure point (values as the model gave them)."""

    time: datetime
    temperature_c: float
    dewpoint_c: float | None
    cloud_cover_pct: float | None
    wind_speed_kt: float | None

    @property
    def spread_c(self) -> float | None:
        if self.dewpoint_c is None:
            return None
        return self.temperature_c - self.dewpoint_c


@dataclass(frozen=True)
class ModelNight:
    """One model's valued hours inside the night window, oldest first."""

    model: str
    hours: list[FrostHour] = field(default_factory=list)

    def gradeable(self, departure: datetime) -> bool:
        if len(self.hours) < MIN_VALUED_HOURS:
            return False
        latest = self.hours[-1].time
        return departure - latest <= timedelta(hours=LATEST_HOUR_SLACK_H)


@dataclass(frozen=True)
class DepartureFrostAnalysis:
    """The night before departure at the departure airport, per model.

    ``in_scope`` is False for a departure in daylight (after the morning
    clear-off margin, before sunset): the evaluator then answers GREEN with no
    text. ``sunrise`` is the sunrise that ended the night, when it is already
    past at departure; None while the night is still running (evening or
    pre-dawn departure).
    """

    icao: str | None
    departure_time: datetime
    night_start: datetime | None
    sunrise: datetime | None
    window_start: datetime
    models: dict[str, ModelNight] = field(default_factory=dict)

    def minutes_after_sunrise(self) -> float | None:
        if self.sunrise is None:
            return None
        return (self.departure_time - self.sunrise).total_seconds() / 60.0

    def in_scope(self, clear_after_sunrise_min: float) -> bool:
        """Departure falls between the previous sunset and sunrise + margin."""
        if self.night_start is None:
            return False
        after = self.minutes_after_sunrise()
        # Night still running at departure (no sunrise yet): in scope.
        if after is None:
            return True
        return after <= clear_after_sunrise_min


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _sun_events(lat: float, lon: float, day: date) -> dict[str, datetime | None]:
    from euro_aip.utils.solar import sun_events

    return sun_events(lat, lon, day)


def night_bounds(
    lat: float, lon: float, departure: datetime,
) -> tuple[datetime | None, datetime | None]:
    """``(night_start, sunrise)`` for the night a departure belongs to.

    ``night_start`` is the latest sunset at or before the departure;
    ``sunrise`` the first sunrise after it, or None when that sunrise is still
    ahead (an evening or pre-dawn departure, always in scope). Events are
    gathered over the UTC dates either side of the departure, so a longitude
    where the local evening falls on another UTC date than the departure
    (a western-US sunset after 00Z) still pairs the right sunset and sunrise.

    No sunset in that span (polar day/night) returns ``(None, None)``: out of
    scope.
    """
    dep = _aware(departure)
    sunsets: list[datetime] = []
    sunrises: list[datetime] = []
    for offset in (-2, -1, 0, 1):
        events = _sun_events(lat, lon, dep.date() + timedelta(days=offset))
        if events.get("evening") is not None:
            sunsets.append(events["evening"])
        if events.get("morning") is not None:
            sunrises.append(events["morning"])
    before = [s for s in sunsets if s <= dep]
    if not before:
        return None, None
    night_start = max(before)
    after_start = sorted(r for r in sunrises if r > night_start)
    if not after_start:
        return None, None
    sunrise = after_start[0]
    return night_start, (sunrise if sunrise <= dep else None)


def _departure_point(analyses: list[RoutePointAnalysis]) -> RoutePointAnalysis | None:
    return analyses[0] if analyses else None


def compute_departure_frost(
    analyses: list[RoutePointAnalysis],
    cross_sections: list[RouteCrossSection],
    models: list[str],
) -> DepartureFrostAnalysis | None:
    """Collect each model's night at the departure point; None without a route.

    The departure point is the first route point (the origin airport); its
    ``interpolated_time`` is the departure, so an alternate-departure re-run
    (shifted analyses) gets the window of the shifted departure.
    """
    point = _departure_point(analyses)
    if point is None:
        return None
    departure = _aware(point.interpolated_time)
    night_start, sunrise = night_bounds(point.lat, point.lon, departure)

    floor_start = departure - timedelta(hours=MAX_WINDOW_H)
    window_start = max(night_start, floor_start) if night_start else floor_start

    by_model = {cs.model.value: cs for cs in cross_sections}
    nights: dict[str, ModelNight] = {}
    for model in models:
        cs = by_model.get(model)
        hours: list[FrostHour] = []
        if cs is not None and point.point_index < len(cs.point_forecasts):
            for h in cs.point_forecasts[point.point_index].hourly:
                t = _aware(h.time)
                if t < window_start or t > departure:
                    continue
                if h.temperature_2m_c is None:
                    continue  # a skeleton hour: not data, never stood in for
                hours.append(FrostHour(
                    time=t,
                    temperature_c=h.temperature_2m_c,
                    dewpoint_c=h.dewpoint_2m_c,
                    cloud_cover_pct=h.cloud_cover_pct,
                    wind_speed_kt=h.wind_speed_10m_kt,
                ))
        hours.sort(key=lambda fh: fh.time)
        nights[model] = ModelNight(model=model, hours=hours)

    return DepartureFrostAnalysis(
        icao=point.waypoint_icao,
        departure_time=departure,
        night_start=night_start,
        sunrise=sunrise,
        window_start=window_start,
        models=nights,
    )
