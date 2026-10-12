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


# --- Manual take-off / landed (#760) -------------------------------------------

from weatherbrief.analysis.flight_progress import (  # noqa: E402
    SOURCE_PLAN_TAKEOFF,
    ProgressEvent,
    progress_events,
)


def ev(kind, at_min, recorded_min=None):
    """A manual event ``at_min`` after DEP, recorded ``recorded_min`` after DEP
    (default: when it happened)."""
    at_ = DEP + timedelta(minutes=at_min) if at_min is not None else None
    rec = DEP + timedelta(minutes=recorded_min if recorded_min is not None else at_min)
    return ProgressEvent(kind=kind, at=at_, recorded_at=rec)


def late(minutes: float, *events) -> FlightProgress:
    return FlightProgress.from_route(ROUTE, DEP, DEP + timedelta(minutes=minutes), events)


def test_takeoff_40_min_late_shifts_everything_by_40_min():
    takeoff = ev("takeoff", 40)
    plan, actual = at(45), late(85, takeoff)
    assert actual.source == SOURCE_PLAN_TAKEOFF and actual.delay_min == pytest.approx(40)
    assert actual.departed_at == DEP + timedelta(minutes=40)
    assert actual.flown_nm == pytest.approx(plan.flown_nm)
    assert actual.eta(actual.total_nm / 2) == plan.eta(plan.total_nm / 2) + timedelta(minutes=40)
    assert actual.arrival == plan.arrival + timedelta(minutes=40)
    assert actual.position(actual.as_of) == pytest.approx(plan.position(plan.as_of))


def test_an_event_counts_only_once_recorded():
    # Took off at +40, entered at +50: a tick at +45 still measured the plan.
    p = late(45, ev("takeoff", 40, recorded_min=50))
    assert p.source == "plan" and p.departed_at == DEP
    later = p.at(DEP + timedelta(minutes=55))
    assert later.source == SOURCE_PLAN_TAKEOFF and later.departed_at == DEP + timedelta(minutes=40)


def test_later_entry_replaces_and_none_withdraws():
    p = late(120, ev("takeoff", 30, recorded_min=60), ev("takeoff", 20, recorded_min=70))
    assert p.departed_at == DEP + timedelta(minutes=20)
    withdrawn = late(120, ev("takeoff", 30, recorded_min=60), ev("takeoff", None, recorded_min=70))
    assert withdrawn.source == "plan" and withdrawn.departed_at == DEP


def test_landing_closes_the_flight_at_the_speed_flown():
    # Took off on time, landed after 60 min of a planned 90.
    p = late(70, ev("takeoff", 0), ev("landed", 60))
    assert p.arrived and p.arrival == DEP + timedelta(minutes=60)
    assert p.landed_at == DEP + timedelta(minutes=60)
    assert p.flown_nm == pytest.approx(p.total_nm)
    assert p.eta(p.total_nm / 2) == DEP + timedelta(minutes=30)
    # Before the landing was entered the plan's arrival held.
    assert p.at(DEP + timedelta(minutes=50)).arrival == DEP + timedelta(minutes=90)


def test_landing_alone_on_the_planned_departure():
    p = late(100, ev("landed", 80))
    assert p.source == SOURCE_PLAN_TAKEOFF and p.takeoff_at is None
    assert p.departed_at == DEP and p.arrival == DEP + timedelta(minutes=80)


def test_landing_not_after_departure_is_ignored():
    p = late(120, ev("takeoff", 50), ev("landed", 40, recorded_min=60))
    assert p.landed_at is None and p.arrival == DEP + timedelta(minutes=50 + 90)


def test_schedule_without_a_route():
    p = FlightProgress.schedule(DEP, 1.5, DEP + timedelta(minutes=100), (ev("takeoff", 40),))
    assert p.arrival == DEP + timedelta(minutes=130) and not p.arrived
    assert p.total_nm == 0.0 and p.position(p.as_of) is None


def test_history_records_round_trip_and_skip_garbage():
    events = (ev("takeoff", 40), ev("landed", None, recorded_min=60),
              ProgressEvent(kind="fix", at=DEP, recorded_at=DEP, source="app", lat=50.0, lon=1.0))
    records = [e.history_record() for e in events] + [
        {"type": "progress", "event": "takeoff", "at": "garbage", "tick_at": DEP.isoformat()},
        {"type": "progress", "event": "bogus", "at": DEP.isoformat(), "tick_at": DEP.isoformat()},
        {"type": "progress", "event": "takeoff", "at": DEP.isoformat()},  # no tick_at
        {"type": "report", "kind": "metar"},
    ]
    assert progress_events(records) == events
    # A fix is carried, not fused (#761).
    fix_only = late(10, events[2])
    assert fix_only.source == "plan" and fix_only.departed_at == DEP
