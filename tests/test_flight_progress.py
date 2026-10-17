"""FlightProgress (#759/#760): the plan source every live consumer measures against."""

from __future__ import annotations

import math
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
    # A fix's own fields round-trip too (#761).
    full = fix(10, 1.0)
    assert progress_events([full.history_record()]) == (full,)


# --- App GPS fixes (#761) ----------------------------------------------------------
#
# Sparse corrections to the plan: ZZDP → ZZDS is ~154 NM in 1.5 h (~103 kt).

from weatherbrief.analysis.flight_progress import (  # noqa: E402
    SOURCE_APP,
    TAXI_MARGIN,
    FixData,
)

NM_PER_DEG_LON = 60.0 * math.cos(math.radians(50.0))  # ZZDP → ZZDS runs along 50°N
TOTAL_NM = FlightProgress.from_route(ROUTE, DEP, DEP).total_nm
PLANNED_KT = TOTAL_NM / 1.5


def fix(at_min, lon, gs=120.0, track=90.0, rec_min=None, lat=50.0, alt=None, terrain=None, stored=None):
    """An app fix ``at_min`` after DEP at (lat, lon), recorded ``rec_min``
    after DEP (default: when taken). ``stored``: a server projection."""
    rec = DEP + timedelta(minutes=rec_min if rec_min is not None else at_min)
    return ProgressEvent(
        kind="fix", at=DEP + timedelta(minutes=at_min), recorded_at=rec, source=SOURCE_APP,
        lat=lat, lon=lon, fix=FixData(gs_kt=gs, track_deg=track, h_accuracy_m=10.0, alt_ft=alt,
                                      terrain_ft=terrain, **(stored or {})),
    )


def along(p, f):
    return p.track.project(f.lat, f.lon).along_nm


def mins(minutes):
    return DEP + timedelta(minutes=minutes)


def test_a_fix_shifts_the_plan_by_the_delay_it_shows():
    """At +40 the aircraft is ~19 NM along, where the plan had it at ~11 min:
    ~29 min late, and from there it flies the plan's speed."""
    f = fix(40, 0.5)
    p = late(50, f)
    a0 = along(p, f)
    assert p.source == SOURCE_APP and not p.estimated and not p.off_route
    assert p.takeoff_at == mins(40) - timedelta(hours=a0 / PLANNED_KT)
    assert p.delay_min == pytest.approx(40 - a0 / PLANNED_KT * 60)
    assert p.flown_nm == pytest.approx(a0 + PLANNED_KT / 6)
    # The plan shifted by the delay: same duration from the inferred take-off.
    assert p.arrival == p.takeoff_at + timedelta(hours=1.5)
    assert p.eta(TOTAL_NM / 2) == p.takeoff_at + timedelta(minutes=45)
    assert p.fix_age_min == pytest.approx(10.0) and p.fix == f
    # Inferred, not entered: the times are still the plan's kind.
    assert p.time_base == "planned"


def test_the_instantaneous_speed_is_never_extrapolated():
    """A climb at 70 kt or a 200 kt gust: the same ETAs, the plan's speed."""
    slow, fast = late(50, fix(40, 0.5, gs=70)), late(50, fix(40, 0.5, gs=200))
    assert slow.arrival == fast.arrival == late(50, fix(40, 0.5)).arrival
    assert slow._anchor.v_kt == pytest.approx(PLANNED_KT)


def test_a_stale_fix_is_estimated_and_never_worse_than_the_plan_shifted():
    p = late(80, fix(40, 0.5))
    assert p.estimated and p.source == SOURCE_APP
    assert p.flown_nm == pytest.approx(along(p, fix(40, 0.5)) + PLANNED_KT * 40 / 60)


def test_two_fixes_far_enough_apart_measure_the_speed():
    """Flying at 80 % of the plan's speed, fixes 30 min apart."""
    a1 = 0.6 * NM_PER_DEG_LON
    f1 = fix(20, 0.6)
    f2 = fix(50, (a1 + 0.8 * PLANNED_KT * 0.5) / NM_PER_DEG_LON)
    assert late(55, f1, f2)._anchor.v_kt == pytest.approx(0.8 * PLANNED_KT, rel=0.01)
    # 10 min apart: too close to measure, the plan's speed.
    near = fix(30, (a1 + 0.8 * PLANNED_KT / 6) / NM_PER_DEG_LON)
    assert late(35, f1, near)._anchor.v_kt == pytest.approx(PLANNED_KT)
    # Twice the plan's speed is clamped to 1.4.
    far = fix(50, (a1 + 2 * PLANNED_KT * 0.5) / NM_PER_DEG_LON)
    assert late(55, f1, far)._anchor.v_kt == pytest.approx(1.4 * PLANNED_KT)


def test_an_entered_takeoff_measures_the_speed_with_one_fix():
    f = fix(30, 0.8 * PLANNED_KT * 0.5 / NM_PER_DEG_LON)
    assert late(35, ev("takeoff", 0), f)._anchor.v_kt == pytest.approx(0.8 * PLANNED_KT, rel=0.01)


def test_a_late_fix_still_measures_the_speed():
    """The older fix reaches the server after the newer one (no data in
    flight): it moves no anchor but scales the speed."""
    a1 = 0.6 * NM_PER_DEG_LON
    f2 = fix(50, (a1 + 0.8 * PLANNED_KT * 0.5) / NM_PER_DEG_LON, rec_min=51)
    f1 = fix(20, 0.6, rec_min=52)
    p = late(55, f2, f1)
    assert p._anchor.seen.event == f2
    assert p._anchor.v_kt == pytest.approx(0.8 * PLANNED_KT, rel=0.01)


def test_a_fix_of_unknown_speed_and_height_does_not_start_the_flight():
    """A one-shot fix from home, a few miles down the route, no valid speed."""
    home = fix(30, 0.2, gs=None, lat=50.02)
    p = late(40, home)
    assert p.takeoff_at is None and p.source == "plan" and p.departed_at == DEP
    # Once the flight has started (entered), the same fix places it.
    assert late(40, ev("takeoff", 20), home)._anchor.seen.event == home


def test_height_decides_airborne():
    # No valid speed, 4,200 ft above the terrain: airborne.
    up = late(40, fix(30, 0.5, gs=None, alt=4500, terrain=300))
    assert up.takeoff_at is not None
    # 60 kt on the road (100 ft above the terrain): not the take-off.
    car = late(40, fix(30, 0.5, gs=60, alt=400, terrain=300))
    assert car.takeoff_at is None and car.source == "plan"


def test_a_fix_far_off_the_route_is_not_this_flight():
    """30 NM north of the route: a weather check from elsewhere, ignored."""
    p = late(50, fix(40, 1.0, lat=50.5))
    assert p.source == "plan" and p.takeoff_at is None


def test_off_route_fix_keeps_the_projection():
    """18 NM north of the route: off route, the plan's speed, along kept."""
    off = fix(40, 2.0, lat=50.3)
    p = late(50, fix(20, 0.5), off)
    assert p.off_route and p.source == SOURCE_APP
    assert p._anchor.seen.along == pytest.approx(along(p, off))
    assert p._anchor.v_kt == pytest.approx(PLANNED_KT)


def test_a_small_reversal_holds_the_position_until_the_aircraft_catches_up():
    """Fixes 25 min apart, the second 4 NM behind where the first had
    extrapolated (a headwind): the position holds instead of going back."""
    f1 = fix(25, 0.5)
    expected_at_50 = late(50, f1).flown_nm
    f2 = fix(50, (expected_at_50 - 4.0) / NM_PER_DEG_LON, gs=100)
    flown = [late(m, f1, f2).flown_nm for m in range(25, 90, 5)]
    assert flown == sorted(flown)
    p = late(50, f1, f2)
    assert p.flown_nm == pytest.approx(expected_at_50, abs=0.2)  # held, not 4 NM back
    # A point inside the hold reads as passed when the hold began.
    assert p.eta(expected_at_50 - 1.0) <= mins(50)


def test_a_large_reversal_after_a_gap_is_taken_at_once():
    """One fix 25 min after the last, 20 NM behind its extrapolation: a new
    observation (the plan's speed was optimistic), not jitter."""
    f1 = fix(20, 1.0)
    shown = late(45, f1).flown_nm
    f2 = fix(45, (shown - 20.0) / NM_PER_DEG_LON)
    p = late(45, f1, f2)
    assert p.flown_nm == pytest.approx(along(p, f2), abs=0.1)


def test_a_large_reversal_within_the_jitter_gap_needs_two_fixes():
    f1 = fix(20, 1.0)  # ~38 NM
    back1 = fix(30, 0.4)  # ~15 NM, 10 min later: far behind the ~55 NM shown
    one = late(32, f1, back1)
    assert one.flown_nm >= late(30, f1).flown_nm - 0.01  # held
    back2 = fix(36, 0.55)
    two = late(36, f1, back1, back2)
    assert two.flown_nm == pytest.approx(along(two, back2), abs=0.1)  # accepted


def test_first_fix_replaces_the_plan_even_behind_it():
    """The plan had ~69 NM flown at +40; the first fix says 19: a late
    departure, taken as it is."""
    p = late(40, fix(40, 0.5))
    assert p.flown_nm == pytest.approx(along(p, fix(40, 0.5)))
    assert at(40).flown_nm > p.flown_nm + 40


def test_a_ground_fix_at_the_departure_moves_the_departure():
    taxi = fix(10, 0.01, gs=8)
    p = late(15, taxi)
    assert p.source == SOURCE_APP and p.takeoff_at is None
    assert p.departed_at == taxi.at + TAXI_MARGIN and not p.departed
    assert p.arrival == taxi.at + TAXI_MARGIN + timedelta(hours=1.5)
    # Nothing newer: the flight is taken to have left then.
    assert late(25, taxi).departed and late(25, taxi).flown_nm > 0
    # A one-shot on the apron (no speed, 30 ft above the terrain) is the same.
    apron = fix(10, 0.01, gs=None, alt=330, terrain=300)
    assert late(15, apron).departed_at == apron.at + TAXI_MARGIN


def test_a_ground_fix_well_before_the_planned_time_changes_nothing():
    """A weather check at the airfield an hour before: still the plan."""
    p = late(-20, fix(-60, 0.01, gs=0))
    assert p.departed_at == DEP and p.source == "plan"
    # Just before it: the take-off is at least TAXI_MARGIN after the fix.
    assert late(-2, fix(-5, 0.01, gs=0)).departed_at == mins(5)


def test_takeoff_never_before_the_last_ground_fix():
    p = late(30, fix(15, 0.01, gs=5), fix(20, 0.6, gs=150))
    assert p.takeoff_at == mins(15)


def test_a_ground_fix_resets_a_takeoff_seen_on_the_road():
    """60 kt on the way to the airfield (no height), then taxiing: not departed."""
    p = late(25, fix(0, 0.05, gs=60, lat=50.03), fix(20, 0.01, gs=10))
    assert p.takeoff_at is None and not p.departed
    off = late(45, fix(0, 0.05, gs=60, lat=50.03), fix(20, 0.01, gs=10), fix(40, 0.4, gs=120))
    assert off.takeoff_at >= mins(20)


def test_a_fix_at_the_destination_lands_the_flight():
    """A one-shot on the apron at the destination, no valid speed."""
    def apron(m):
        return fix(m, 3.99, gs=None, alt=330, terrain=300)
    # After the expected arrival (+90): landed then, not when the app opened.
    p = late(110, apron(105))
    assert p.landed_at == mins(90) and p.arrived and p.source == SOURCE_APP
    assert p.flown_nm == pytest.approx(p.total_nm) and p.time_base == "planned"
    # Earlier than expected (a fast flight): landed at the fix.
    assert late(80, apron(75)).landed_at == mins(75)
    # Too early to have flown there (+30, faster than 1.4× the plan): not landed.
    assert late(35, apron(30)).landed_at is None
    # Airborne overhead the destination is not a landing.
    assert late(95, fix(92, 3.99, gs=110)).landed_at is None


def test_an_entered_landing_wins_over_an_inferred_one():
    p = late(110, ev("landed", 85, recorded_min=100), fix(105, 3.99, gs=0))
    assert p.landed_at == mins(85) and p.time_base == "partial"


def test_manual_entries_override_the_fixes():
    p = late(50, ev("takeoff", 35), fix(40, 0.5))
    assert p.takeoff_at == mins(35)
    assert p.source == SOURCE_APP  # the position still comes from the fix
    app_landed = ProgressEvent(kind="landed", at=mins(100), recorded_at=mins(101), source=SOURCE_APP)
    manual = ev("landed", 95, recorded_min=96)
    # A manual landing wins over the app's, whichever came last.
    assert late(110, fix(40, 0.5), manual, app_landed).landed_at == mins(95)
    # A withdrawn manual landing lets a later app one count.
    cleared = ev("landed", None, recorded_min=97)
    assert late(110, fix(40, 0.5), manual, cleared, app_landed).landed_at == mins(100)


def test_an_app_landing_ends_the_fix_extrapolation_and_counts_as_observed():
    landed = ProgressEvent(kind="landed", at=mins(95), recorded_at=mins(96), source=SOURCE_APP)
    p = late(100, fix(40, 0.5), landed)
    assert p.arrived and p.arrival == mins(95)
    assert p.flown_nm == pytest.approx(p.total_nm)
    assert p.time_base == "partial"  # take-off inferred, landing detected
    assert late(100, ev("takeoff", 30), fix(40, 0.5), landed).time_base == "observed"


def test_schedule_reads_the_stored_projection():
    """No route in hand (the live window): the stored projection gives the
    same departure and arrival as the route does."""
    route_mode = late(50, fix(40, 0.5))
    seen = route_mode._anchor.seen
    stored = fix(40, 0.5, stored={"along_nm": seen.along, "cross_nm": seen.cross,
                                  "course_deg": seen.course, "route_nm": route_mode.total_nm})
    sched = FlightProgress.schedule(DEP, 1.5, mins(50), (stored,))
    assert sched.departed_at == route_mode.departed_at
    assert sched.arrival == route_mode.arrival


def test_a_projection_on_another_route_is_redone():
    stale = fix(40, 0.5, stored={"along_nm": 5.0, "cross_nm": 0.0, "course_deg": 90.0, "route_nm": 300.0})
    p = late(50, stale)
    assert p._anchor.seen.along == pytest.approx(along(p, stale))


def test_fixes_count_only_once_recorded():
    p = late(45, fix(40, 0.5, rec_min=50))
    assert p.source == "plan"
    assert p.at(mins(55)).source == SOURCE_APP
