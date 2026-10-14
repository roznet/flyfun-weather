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

from weatherbrief.analysis.flight_progress import (  # noqa: E402
    GROUND_HOLD,
    SOURCE_APP,
    FixData,
)

NM_PER_DEG_LON = 60.0 * math.cos(math.radians(50.0))  # ZZDP → ZZDS runs along 50°N


def fix(at_min, lon, gs=120.0, track=90.0, rec_min=None, lat=50.0, stored=None):
    """An app fix ``at_min`` after DEP at (lat, lon), recorded ``rec_min``
    after DEP (default: when taken). ``stored``: a server projection."""
    rec = DEP + timedelta(minutes=rec_min if rec_min is not None else at_min)
    return ProgressEvent(
        kind="fix", at=DEP + timedelta(minutes=at_min), recorded_at=rec, source=SOURCE_APP,
        lat=lat, lon=lon, fix=FixData(gs_kt=gs, track_deg=track, h_accuracy_m=10.0, **(stored or {})),
    )


def test_a_fix_anchors_the_position_and_infers_the_takeoff():
    """30 min late: at +40 the aircraft is 0.5° (~19 NM) along at 120 kt."""
    p = late(50, fix(40, 0.5))
    along0 = p.track.project(50.0, 0.5).along_nm
    assert p.source == SOURCE_APP and not p.estimated and not p.off_route
    # Back-extrapolated to the departure at its own speed.
    assert p.takeoff_at == DEP + timedelta(minutes=40) - timedelta(hours=along0 / 120.0)
    assert p.delay_min == pytest.approx(40 - along0 / 2.0)
    assert p.flown_nm == pytest.approx(along0 + 20.0)  # 10 min at 120 kt
    assert p.arrival == DEP + timedelta(minutes=40) + timedelta(hours=(p.total_nm - along0) / 120.0)
    assert p.eta(p.total_nm) == p.arrival
    assert p.fix_age_min == pytest.approx(10.0) and p.fix.at == DEP + timedelta(minutes=40)
    assert p.time_base == "partial"


def test_a_stale_fix_keeps_extrapolating_and_is_estimated():
    p = late(65, fix(40, 0.5))
    assert p.estimated and p.source == SOURCE_APP
    assert p.flown_nm == pytest.approx(p.track.project(50.0, 0.5).along_nm + 50.0)


def test_along_track_speed_and_its_fallbacks():
    track = FlightProgress.from_route(ROUTE, DEP, DEP).track
    planned_kt = track.total_nm / 1.5
    # 60° off the route's course: half the ground speed counts.
    assert late(50, fix(40, 1.0, gs=200, track=150))._anchor.v_kt == pytest.approx(100.0, rel=0.01)
    # Unknown or slow along-track speed: the planned one.
    assert late(50, fix(40, 1.0, gs=None))._anchor.v_kt == pytest.approx(planned_kt)
    assert late(50, ev("takeoff", 20), fix(40, 1.0, gs=30))._anchor.v_kt == pytest.approx(planned_kt)


def test_off_route_fix_keeps_the_projection():
    """18 NM north of the route: off route, planned speed, along-route kept."""
    p = late(50, fix(20, 0.5), fix(40, 2.0, lat=50.3))
    assert p.off_route and p.source == SOURCE_APP
    assert p._anchor.seen.along == pytest.approx(p.track.project(50.3, 2.0).along_nm)
    assert p._anchor.v_kt == pytest.approx(p.total_nm / 1.5)


def test_never_backwards_holds_the_position_until_the_aircraft_catches_up():
    """Fixes every 25 min; the second is 4 NM behind where the first had
    extrapolated (a headwind): the position holds instead of going back."""
    f1 = fix(25, 0.5)  # ~19 NM, 120 kt → 50 NM extrapolated at +40... and 69 at +50
    expected_at_50 = late(50, f1).flown_nm
    f2 = fix(50, (expected_at_50 - 4.0) / NM_PER_DEG_LON, gs=100)
    flown = [late(m, f1, f2).flown_nm for m in range(25, 90, 5)]
    assert flown == sorted(flown)
    p = late(50, f1, f2)
    assert p.flown_nm == pytest.approx(expected_at_50, abs=0.2)  # held, not 4 NM back
    # Caught up after ~2.4 min at 100 kt; from then the fix's own speed.
    later = late(60, f1, f2)
    assert later.flown_nm == pytest.approx(f2_along(later, f2) + 100 / 6, abs=0.2)
    # A point inside the hold reads as passed when the hold began.
    assert p.eta(expected_at_50 - 1.0) <= DEP + timedelta(minutes=50)


def f2_along(p, f):
    return p.track.project(f.lat, f.lon).along_nm


def test_a_reversal_needs_two_fixes_more_than_10_nm_behind():
    f1 = fix(20, 1.0)  # ~38 NM
    back1 = fix(30, 0.4, rec_min=30)  # ~15 NM: far behind the ~58 NM shown
    one = late(32, f1, back1)
    assert one.flown_nm >= late(30, f1).flown_nm - 0.01  # held
    back2 = fix(40, 0.8)
    two = late(42, f1, back1, back2)
    assert two.flown_nm == pytest.approx(f2_along(two, back2) + 4.0, abs=0.2)  # accepted


def test_first_fix_replaces_the_plan_even_behind_it():
    """The plan had ~70 NM flown at +40; the first fix says 19: a late
    departure, taken as it is."""
    p = late(40, fix(40, 0.5))
    assert p.flown_nm == pytest.approx(p.track.project(50.0, 0.5).along_nm)
    assert at(40).flown_nm > p.flown_nm + 40


def test_a_ground_fix_at_the_departure_holds_the_departure():
    taxi = fix(10, 0.01, gs=8)
    holding = late(20, taxi)
    assert holding.source == SOURCE_APP and holding.takeoff_at is None
    assert not holding.departed and holding.flown_nm == 0.0
    assert holding.departed_at == holding.as_of
    assert holding.arrival == holding.as_of + timedelta(hours=1.5)  # ETAs from now
    # Before the planned departure the plan already says not departed.
    early = FlightProgress.from_route(ROUTE, DEP + timedelta(minutes=30), DEP + timedelta(minutes=20),
                                      (taxi,))
    assert early.departed_at == DEP + timedelta(minutes=30) and not early.departed
    # The hold runs out GROUND_HOLD after the fix: taken to have left then.
    expired = late(60, taxi)
    assert expired.departed and expired.departed_at == taxi.at + GROUND_HOLD
    assert expired.arrival == taxi.at + GROUND_HOLD + timedelta(hours=1.5)


def test_takeoff_never_before_the_last_ground_fix():
    p = late(30, fix(15, 0.01, gs=5), fix(20, 0.6, gs=150))
    assert p.takeoff_at == DEP + timedelta(minutes=15)


def test_a_ground_fix_resets_a_takeoff_seen_on_the_road():
    """60 kt on the way to the airfield, then taxiing: not departed."""
    p = late(25, fix(0, 0.05, gs=60, lat=50.03), fix(20, 0.01, gs=10))
    assert p.takeoff_at is None and not p.departed
    off = late(45, fix(0, 0.05, gs=60, lat=50.03), fix(20, 0.01, gs=10), fix(40, 0.4, gs=120))
    assert off.takeoff_at >= DEP + timedelta(minutes=20)


def test_manual_entries_override_the_fixes():
    p = late(50, ev("takeoff", 35), fix(40, 0.5))
    assert p.takeoff_at == DEP + timedelta(minutes=35)
    assert p.source == SOURCE_APP  # the position still comes from the fix
    app_landed = ProgressEvent(kind="landed", at=DEP + timedelta(minutes=100),
                               recorded_at=DEP + timedelta(minutes=101), source=SOURCE_APP)
    manual = ev("landed", 95, recorded_min=96)
    # A manual landing wins over the app's, whichever came last.
    assert late(110, fix(40, 0.5), manual, app_landed).landed_at == DEP + timedelta(minutes=95)
    # A withdrawn manual landing lets a later app one count.
    cleared = ev("landed", None, recorded_min=97)
    assert late(110, fix(40, 0.5), manual, cleared, app_landed).landed_at == DEP + timedelta(minutes=100)


def test_landed_ends_the_fix_extrapolation():
    landed = ProgressEvent(kind="landed", at=DEP + timedelta(minutes=95),
                           recorded_at=DEP + timedelta(minutes=96), source=SOURCE_APP)
    p = late(100, fix(40, 0.5), landed)
    assert p.arrived and p.arrival == DEP + timedelta(minutes=95)
    assert p.flown_nm == pytest.approx(p.total_nm) and p.time_base == "observed"


def test_schedule_reads_the_stored_projection():
    """No route in hand (the live window): the stored projection gives the
    same departure and arrival as the route does."""
    route_mode = late(50, fix(40, 0.5))
    seen = route_mode._anchor.seen
    stored = fix(40, 0.5, stored={"along_nm": seen.along, "cross_nm": seen.cross,
                                  "course_deg": seen.course, "route_nm": route_mode.total_nm})
    sched = FlightProgress.schedule(DEP, 1.5, DEP + timedelta(minutes=50), (stored,))
    assert sched.departed_at == route_mode.departed_at
    assert sched.arrival == route_mode.arrival


def test_a_projection_on_another_route_is_redone():
    stale = fix(40, 0.5, stored={"along_nm": 5.0, "cross_nm": 0.0, "course_deg": 90.0, "route_nm": 300.0})
    p = late(50, stale)
    assert p._anchor.seen.along == pytest.approx(p.track.project(50.0, 0.5).along_nm)


def test_fixes_count_only_once_recorded():
    p = late(45, fix(40, 0.5, rec_min=50))
    assert p.source == "plan"
    assert p.at(DEP + timedelta(minutes=55)).source == SOURCE_APP
