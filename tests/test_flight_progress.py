"""FlightProgress (#759/#760): the plan source every live consumer measures against."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.analysis.flight_progress import FlightProgress
from weatherbrief.models.analysis import RouteConfig, Waypoint

DEP = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
ROUTE = RouteConfig(
    name="ZZDP-ZZDS",
    waypoints=[Waypoint(icao="ZZDP", name="ZZDP", lat=50.0, lon=0.0),
               Waypoint(icao="ZZMD", name="ZZMD", lat=50.0, lon=2.0),
               Waypoint(icao="ZZDS", name="ZZDS", lat=50.0, lon=4.0)],
    flight_duration_hours=1.5,
)


def at(minutes: float) -> FlightProgress:
    return FlightProgress.from_route(ROUTE, DEP, DEP + timedelta(minutes=minutes))


def test_before_departure_nothing_flown():
    p = at(-30)
    assert p.source == "plan"
    assert p.timed and not p.departed and not p.arrived
    assert p.flown_nm == 0.0
    assert p.position(p.as_of) == (50.0, 0.0)


def test_halfway_on_the_plan():
    p = at(45)
    total = p.total_nm
    assert p.departed and not p.arrived
    assert p.flown_nm == pytest.approx(total / 2)
    assert p.eta(total / 2) == DEP + timedelta(minutes=45)
    _lat, lon = p.position(p.as_of)
    assert lon == pytest.approx(2.0, abs=0.01)


def test_after_arrival_clamped_to_the_route():
    p = at(120)
    assert p.arrived and p.arrival == DEP + timedelta(hours=1.5)
    assert p.flown_nm == pytest.approx(p.total_nm)
    # ETAs clamp to the route's ends.
    assert p.eta(-5) == DEP
    assert p.eta(p.total_nm + 50) == p.arrival


def test_at_rebuilds_as_of_another_time():
    p = at(0)
    later = p.at(DEP + timedelta(minutes=45))
    assert later.as_of == DEP + timedelta(minutes=45)
    assert later.flown_nm == pytest.approx(p.total_nm / 2)
    assert p.flown_nm == 0.0  # the original is unchanged


@pytest.mark.parametrize("departure, duration", [(None, 1.5), (DEP, 0.0)])
def test_untimed_flight_has_no_progress(departure, duration):
    route = ROUTE.model_copy(update={"flight_duration_hours": duration})
    p = FlightProgress.from_route(route, departure, DEP)
    assert not p.timed and not p.arrived
    assert p.flown_nm is None and p.arrival is None
    assert p.eta(10.0) is None and p.position(DEP) is None
    assert p.departed == (departure is not None)


def test_zero_length_route_is_timed_but_has_no_etas():
    """A local flight (same airport at both ends): departed / arrived and the
    arrival time hold, there is no distance to put an ETA on."""
    local = RouteConfig(
        name="ZZLC-ZZLC",
        waypoints=[Waypoint(icao="ZZLC", name="ZZLC", lat=50.0, lon=0.0),
                   Waypoint(icao="ZZLC", name="ZZLC", lat=50.0, lon=0.0)],
        flight_duration_hours=1.0,
    )
    p = FlightProgress.from_route(local, DEP, DEP + timedelta(minutes=90))
    assert p.timed and p.arrived and p.arrival == DEP + timedelta(hours=1)
    assert p.flown_nm == 0.0
    assert p.eta(0.0) is None
