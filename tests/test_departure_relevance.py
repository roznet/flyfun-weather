"""A flight still on the ground keeps its departure relevant (#967).

Replays the prod tick sequence of a late departure: planned 10:15, Start
(ground fix at the departure) 10:34, another ground fix 10:45, airborne 11:05.
The schedule estimate (``departed_at``) moved past the clock twice while the
aircraft sat at the holding point, and every departure row and near-departure
route row cleared and came back each time.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.analysis.flight_progress import (
    SOURCE_APP,
    TAXI_MARGIN,
    FixData,
    FlightProgress,
    ProgressEvent,
)
from weatherbrief.models.analysis import RouteConfig, Waypoint
from weatherbrief.models.observations import AirportObservation, RouteObservations
from weatherbrief.tasks.live_significance import (
    ClassifierMemory,
    airport_roles,
    classify_changes,
)

PLAN = datetime(2026, 10, 31, 10, 15, tzinfo=timezone.utc)
ROUTE = RouteConfig(
    name="ZZDP-ZZDS",
    waypoints=[Waypoint(icao="ZZDP", name="ZZDP", lat=50.0, lon=0.0),
               Waypoint(icao="ZZMD", name="ZZMD", lat=50.0, lon=2.0),
               Waypoint(icao="ZZDS", name="ZZDS", lat=50.0, lon=4.0)],
    flight_duration_hours=1.5,
)
NM_PER_DEG_LON = 60.0 * math.cos(math.radians(50.0))


def t(hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return PLAN.replace(hour=h, minute=m)


def ground(hhmm: str) -> ProgressEvent:
    return ProgressEvent(
        kind="fix", at=t(hhmm), recorded_at=t(hhmm), source=SOURCE_APP, lat=50.0, lon=0.01,
        fix=FixData(gs_kt=0.0, h_accuracy_m=10.0),
    )


def airborne(hhmm: str, along_nm: float) -> ProgressEvent:
    return ProgressEvent(
        kind="fix", at=t(hhmm), recorded_at=t(hhmm), source=SOURCE_APP,
        lat=50.0, lon=along_nm / NM_PER_DEG_LON,
        fix=FixData(gs_kt=130.0, h_accuracy_m=10.0, alt_ft=3000.0, terrain_ft=100.0),
    )


EVENTS = (ground("10:34"), ground("10:45"), airborne("11:05", 21.4))
TICKS = ["10:05", "10:17", "10:28", "10:34", "10:35", "10:44", "10:45", "10:50", "10:55",
         "11:00", "11:05", "11:10", "11:15"]


def progress(hhmm: str, events=EVENTS) -> FlightProgress:
    return FlightProgress.from_route(ROUTE, PLAN, t(hhmm), events)


# --- FlightProgress -------------------------------------------------------------

def test_started_flight_stays_at_the_departure_until_the_airborne_fix():
    left = [progress(x).left_departure for x in TICKS]
    first_airborne = TICKS.index("11:05")
    # Plan-only before Start (10:17, 10:28: the planned 10:15 passed, no fix yet).
    assert left[:first_airborne] == [False, True, True] + [False] * (first_airborne - 3)
    assert all(left[first_airborne:])


def test_after_start_nothing_is_passed_until_the_take_off():
    for x in ["10:34", "10:35", "10:44", "10:45", "10:50", "10:55", "11:00"]:
        assert progress(x).passed_nm == 0.0, x
    # Once airborne, what is behind is what the fix shows.
    assert progress("11:10").passed_nm == pytest.approx(progress("11:10").flown_nm)
    assert progress("11:10").passed_nm > 21.0


def test_the_schedule_estimate_is_unchanged():
    """ETAs, the arrival and the tick cadence still run off ``departed_at``
    (ground fix + taxi margin), exactly as before."""
    p = progress("11:00")
    assert p.departed_at == t("10:45") + TAXI_MARGIN
    assert p.departed  # the estimate says gone; relevance does not
    assert not p.left_departure
    assert p.arrival == t("10:55") + timedelta(hours=1.5)
    assert p.started


def test_plan_only_flight_leaves_at_the_planned_departure_once():
    assert [progress(x, ()).left_departure for x in TICKS] == [False] + [True] * (len(TICKS) - 1)


def test_entered_takeoff_ends_the_departure():
    takeoff = ProgressEvent(kind="takeoff", at=t("10:58"), recorded_at=t("11:02"))
    events = (ground("10:34"), takeoff)
    assert not progress("11:00", events).left_departure  # not recorded yet
    assert progress("11:02", events).left_departure


def test_return_to_the_departure_brings_it_back():
    events = (ground("10:34"), airborne("10:50", 15.0), ground("11:20"))
    assert progress("11:00", events).left_departure
    assert not progress("11:25", events).left_departure


def test_start_then_no_fix_all_flight_ends_at_the_estimated_arrival():
    """Start, then the phone in a pocket: the departure does not stay
    relevant for ever, only until the estimated arrival."""
    events = (ground("10:34"),)
    assert not progress("11:30", events).left_departure
    p = progress("12:30", events)  # arrival 10:44 + 1:30 = 12:14
    assert p.arrived and p.left_departure


# --- The classifier, tick by tick -------------------------------------------------

def _obs(at: datetime, dep_taf: str, route_metar: str) -> RouteObservations:
    airports = [
        AirportObservation(icao="ZZDP", distance_from_route_nm=0.0, enroute_distance_nm=0.0,
                           nearest_waypoint_icao="ZZDP", metar_flight_category="VFR", metar_time=at,
                           metar_report_type="METAR", taf_flight_category_at_eta=dep_taf),
        AirportObservation(icao="ZZNR", distance_from_route_nm=2.0, enroute_distance_nm=8.0,
                           nearest_waypoint_icao="ZZDP", metar_flight_category=route_metar,
                           metar_time=at, metar_report_type="METAR",
                           taf_flight_category_at_eta="VFR"),
    ]
    return RouteObservations(corridor_nm=30.0, fetch_time=at, airports_found=2,
                             airports_with_metar=2, airports_with_taf=2, airports=airports)


def _keys(events, ticks):
    base = _obs(t("07:40"), "VFR", "VFR")
    roles = airport_roles(["ZZDP", "ZZMD", "ZZDS"])
    memory = ClassifierMemory()
    out = {}
    for x in ticks:
        p = progress(x, events)
        changes, memory = classify_changes(
            baseline_obs=base, latest_obs=_obs(t(x), "IFR", "IFR"),
            baseline_sigmets=None, latest_sigmets=None, roles=roles,
            departure_at=p.left_departure_at, arrival_at=p.arrival,
            flown_nm=p.passed_nm, memory=memory, now=t(x),
        )
        out[x] = {c.key for c in changes.changes if c.icao in ("ZZDP", "ZZNR")}
    return out


def test_departure_and_near_route_rows_never_flicker_after_start():
    keys = _keys(EVENTS, TICKS[TICKS.index("10:34"):])
    present = [bool(keys[x]) for x in keys]
    first_airborne = list(keys).index("11:05")
    dep_rows = {k for k in keys["10:34"] if "ZZDP" in k}
    route_rows = {k for k in keys["10:34"] if "ZZNR" in k}
    assert dep_rows and route_rows
    # Present at every tick on the ground, gone from the first airborne tick on.
    assert all(present[:first_airborne])
    assert all(keys[x] == keys["10:34"] for x in list(keys)[:first_airborne])
    assert not any(k for x in list(keys)[first_airborne:] for k in keys[x] & dep_rows)
    assert not any(k for x in list(keys)[first_airborne:] for k in keys[x] & route_rows)


def test_plan_only_rows_drop_once_at_the_planned_departure():
    keys = _keys((), TICKS)
    dep_seen = [any("ZZDP" in k for k in keys[x]) for x in TICKS]
    assert dep_seen == [True] + [False] * (len(TICKS) - 1)
