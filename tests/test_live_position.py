"""App GPS fixes through the live layer (#761): the LELL → LEMI morning
replayed with sparse fixes, as the app sends them (every ~25 min).

The fusion rules themselves are in tests/test_flight_progress.py; the
endpoint in tests/test_api.py::TestLivePosition.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from live_scenario_replay import load_scenario, replay
from weatherbrief.analysis.flight_progress import SOURCE_APP, FixData, ProgressEvent
from weatherbrief.analysis.route_geometry import RouteTrack
from weatherbrief.models.analysis import RouteConfig
from weatherbrief.tasks import live_highlight as lh

LELL_LEMI = "2026-10-02_lell_lemi"
DEP = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)  # planned, 2 h
TAKEOFF = DEP + timedelta(minutes=25)
#: The real ground speed: a little slower than the plan's.
TRUE_KT = 0.9


def _track(scenario) -> RouteTrack:
    return RouteTrack.from_route(RouteConfig.model_validate(scenario["inputs"]["route"]))


def _fix_record(track: RouteTrack, at: datetime, along: float, gs: float) -> dict:
    """The record the endpoint would write for a fix ``along`` NM along the
    route at ``at``, received a minute later."""
    lat, lon = track.position_at(along)
    p = track.project(lat, lon)
    return ProgressEvent(
        kind="fix", at=at, recorded_at=at + timedelta(minutes=1), source=SOURCE_APP, lat=lat, lon=lon,
        fix=FixData(gs_kt=gs, track_deg=p.track_deg, h_accuracy_m=10.0, along_nm=p.along_nm,
                    cross_nm=p.offtrack_nm, course_deg=p.track_deg, route_nm=track.total_nm),
    ).history_record()


def _with_fixes(gs_factor: float) -> tuple[dict, float, datetime]:
    """The scenario with a taxi fix at 08:10, a take-off at 08:25 and a fix
    every 25 min after it. The aircraft flies at TRUE_KT × the planned speed;
    each fix reports ``gs_factor`` × that (above 1: every new fix lands
    behind where the last one extrapolated to, the never-backwards case)."""
    scenario = load_scenario(LELL_LEMI)
    track = _track(scenario)
    speed = TRUE_KT * track.total_nm / 2.0
    records = [_fix_record(track, DEP + timedelta(minutes=10), 0.2, 5.0)]
    at = TAKEOFF + timedelta(minutes=5)
    while True:
        along = speed * (at - TAKEOFF).total_seconds() / 3600.0
        if along >= track.total_nm - 5:
            break
        records.append(_fix_record(track, at, along, speed * gs_factor))
        at += timedelta(minutes=25)
    scenario["inputs"]["progress_events"] = records
    arrival = TAKEOFF + timedelta(hours=track.total_nm / speed)
    return scenario, speed, arrival


def _at(ticks, hhmm):
    return next(t for t in ticks if t.at.strftime("%H:%M") == hhmm)


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """Fixes reporting the true speed, and fixes 10 % fast (each new one
    behind the last one's extrapolation)."""
    exact, _, arrival = _with_fixes(1.0)
    fast, _, _ = _with_fixes(1.1)
    return (replay(exact, tmp_path_factory.mktemp("exact") / "u" / "flight"),
            replay(fast, tmp_path_factory.mktemp("fast") / "u" / "flight"),
            arrival)


def test_taxi_fix_holds_the_departure(runs):
    exact, _, _ = runs
    p = _at(exact, "08:20").layer.progress
    assert p.source == SOURCE_APP and p.departed_at is None
    assert p.along_nm == 0.0
    # ETAs measured from now, not from the planned 08:00.
    assert p.eta_destination == DEP + timedelta(minutes=20, hours=2)


def test_sparse_fixes_give_sensible_etas(runs):
    exact, fast, arrival = runs
    for ticks in (exact, fast):
        for hhmm in ("08:40", "09:10", "09:40", "10:10"):
            p = _at(ticks, hhmm).layer.progress
            assert p.source == SOURCE_APP
            # The plan says 10:00; the aircraft lands ~10:38.
            assert abs((p.eta_destination - arrival).total_seconds()) < 12 * 60, (hhmm, p.eta_destination)
            assert abs((p.departed_at - TAKEOFF).total_seconds()) < 60
    # With the true speed, the ETA is the truth.
    p = _at(exact, "09:40").layer.progress
    assert abs((p.eta_destination - arrival).total_seconds()) < 60


def test_position_never_moves_back_and_nothing_flaps(runs):
    """Fixes 10 % fast: each new fix is behind the last one's extrapolation.
    The position holds instead of going back, so no airport or glance line
    is passed and then ahead again. From the first fix on: the plan before
    it (departed 08:00) is no observation, and the taxi fix taking it back
    to 0 is the late departure being found."""
    _, fast, _ = runs
    from_fix = [t for t in fast if t.at >= DEP + timedelta(minutes=11)]
    flown = [t.layer.ribbon.flown_nm for t in from_fix]
    assert flown == sorted(flown) and flown[-1] > 0
    passed_before: set = set()
    for t in from_fix:
        passed = {ln.phase for ln in t.layer.glance.lines if ln.passed}
        assert passed >= passed_before, t.at
        passed_before = passed


def _regenerations(ticks) -> list[str]:
    """The ticks at which the #706 gate would regenerate the highlight."""
    out, last = [], None
    for t in ticks:
        _, gate = lh.facts_and_gate(t.layer.model_dump(mode="json"))
        if last is None or (lh.gate_changes(last, gate) and not lh.arrived(last)):
            out.append(t.at.strftime("%H:%M"))
            last = gate
    return out


def test_held_position_triggers_no_extra_highlight(runs):
    """The fast fixes (position held at each one) regenerate at the same
    ticks as fixes at the true speed: holding never adds one."""
    exact, fast, _ = runs
    assert _regenerations(fast) == _regenerations(exact)


def test_estimated_after_20_min(runs):
    exact, _, _ = runs
    # Fix at 08:55 (received 08:56): fresh at 09:00, still fresh at 09:10.
    assert _at(exact, "09:00").layer.progress.estimated is False
    assert _at(exact, "09:10").layer.progress.fix_age_min == pytest.approx(15.0)
    # Fix at 09:20 is the newest until 09:45: at 09:40 it is 20 min old,
    # and the next one is not in yet.
    late_fix = _at(exact, "09:40").layer.progress
    assert late_fix.fix_age_min == pytest.approx(20.0) and late_fix.estimated is False
