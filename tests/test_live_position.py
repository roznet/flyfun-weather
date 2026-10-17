"""App GPS fixes through the live layer (#761): the LELL → LEMI morning
replayed with the fixes a pilot's phone would send.

Two shapes: a stream (Start Flight on, the app open: a fix every 25 min) and
the common case, occasional fixes (the app opened to check the weather: on
the apron, once in the air, once at the destination). The aircraft takes off
25 min late and flies at 90 % of the plan's speed throughout.

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
#: The real speed, as a fraction of the plan's.
TRUE_KT = 0.9
#: The terrain under every fix (the endpoint reads it from the pack's profile).
TERRAIN_FT = 100.0


def _track(scenario) -> RouteTrack:
    return RouteTrack.from_route(RouteConfig.model_validate(scenario["inputs"]["route"]))


def _fix_record(track: RouteTrack, at: datetime, along: float, gs: float | None, alt: float) -> dict:
    """The record the endpoint would write for a fix ``along`` NM along the
    route at ``at``, received a minute later."""
    lat, lon = track.position_at(along)
    p = track.project(lat, lon)
    return ProgressEvent(
        kind="fix", at=at, recorded_at=at + timedelta(minutes=1), source=SOURCE_APP, lat=lat, lon=lon,
        fix=FixData(gs_kt=gs, track_deg=p.track_deg, h_accuracy_m=10.0, alt_ft=alt, terrain_ft=TERRAIN_FT,
                    along_nm=p.along_nm, cross_nm=p.offtrack_nm, course_deg=p.track_deg,
                    route_nm=track.total_nm),
    ).history_record()


def _truth(track: RouteTrack) -> tuple[float, datetime]:
    speed = TRUE_KT * track.total_nm / 2.0
    return speed, TAKEOFF + timedelta(hours=track.total_nm / speed)


def _along(speed: float, at: datetime) -> float:
    return speed * (at - TAKEOFF).total_seconds() / 3600.0


def _stream(gs_factor: float = 1.0, jitter_nm: float | None = None) -> dict:
    """A taxi fix at 08:12 and a fix every 25 min from 08:30. Each reports
    ``gs_factor`` × the true speed (a climb, a gust: never extrapolated).
    ``jitter_nm``: a second fix 2 min after each, that far behind."""
    scenario = load_scenario(LELL_LEMI)
    track = _track(scenario)
    speed, _ = _truth(track)
    records = [_fix_record(track, DEP + timedelta(minutes=12), 0.2, 5.0, TERRAIN_FT + 30)]
    at = TAKEOFF + timedelta(minutes=5)
    while _along(speed, at) < track.total_nm - 10:
        records.append(_fix_record(track, at, _along(speed, at), speed * gs_factor, 6500.0))
        if jitter_nm is not None:
            j = at + timedelta(minutes=2)
            records.append(_fix_record(track, j, _along(speed, j) - jitter_nm, speed, 6500.0))
        at += timedelta(minutes=25)
    scenario["inputs"]["progress_events"] = records
    return scenario


def _occasional() -> dict:
    """One-shot fixes, no valid speed on the ground: on the apron at 08:20,
    in the air at 09:05, on the apron at the destination at 10:50."""
    scenario = load_scenario(LELL_LEMI)
    track = _track(scenario)
    speed, _ = _truth(track)
    air = DEP + timedelta(hours=1, minutes=5)
    scenario["inputs"]["progress_events"] = [
        _fix_record(track, DEP + timedelta(minutes=20), 0.1, None, TERRAIN_FT + 30),
        _fix_record(track, air, _along(speed, air), speed, 6500.0),
        _fix_record(track, DEP + timedelta(hours=2, minutes=50), track.total_nm - 0.5, None, TERRAIN_FT + 30),
    ]
    return scenario


def _at(ticks, hhmm):
    return next(t for t in ticks if t.at.strftime("%H:%M") == hhmm)


def _minutes_off(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds()) / 60.0


@pytest.fixture(scope="module")
def truth():
    return _truth(_track(load_scenario(LELL_LEMI)))


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    def run(name, scenario):
        return replay(scenario, tmp_path_factory.mktemp(name) / "u" / "flight")

    return {
        "stream": run("stream", _stream()),
        "climb": run("climb", _stream(gs_factor=0.6)),
        "jitter": run("jitter", _stream(jitter_nm=3.0)),
        "occasional": run("occasional", _occasional()),
    }


def test_taxi_fix_moves_the_departure(runs):
    p = _at(runs["stream"], "08:20").layer.progress
    assert p.source == SOURCE_APP and p.departed_at is None and p.along_nm == 0.0
    # ETAs from the taxi fix plus 10 min, not from the planned 08:00.
    assert p.eta_destination == DEP + timedelta(minutes=22, hours=2)


def test_a_stream_of_fixes_gives_the_true_eta(runs, truth):
    _, arrival = truth
    ticks = runs["stream"]
    # One fix: the plan's speed from it, off by the speed error only.
    first = _at(ticks, "08:40").layer.progress
    assert first.source == SOURCE_APP and _minutes_off(first.eta_destination, arrival) < 15
    assert _minutes_off(first.departed_at, TAKEOFF) < 1
    # Two fixes 25 min apart measure the speed: the ETA is the truth.
    for hhmm in ("09:10", "09:40", "10:10"):
        p = _at(ticks, hhmm).layer.progress
        assert _minutes_off(p.eta_destination, arrival) < 2, (hhmm, p.eta_destination)


def test_the_reported_ground_speed_changes_nothing(runs):
    """Every fix reporting 60 % of the true speed (a climb): the same ETAs
    and positions, since only the fixes' positions and times are used."""
    for a, b in zip(runs["stream"], runs["climb"]):
        assert a.layer.progress.eta_destination == b.layer.progress.eta_destination, a.at
        assert a.layer.ribbon.flown_nm == b.layer.ribbon.flown_nm, a.at


def test_position_never_moves_back_and_nothing_flaps(runs):
    """At the plan's speed between fixes, each new fix lands a few NM behind:
    the position holds instead of going back, so no airport or glance line
    is passed and then ahead again. From the first fix on: the plan before
    it (departed 08:00) is no observation."""
    from_fix = [t for t in runs["stream"] if t.at >= DEP + timedelta(minutes=13)]
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


def test_held_jitter_triggers_no_extra_highlight(runs):
    """A second fix 3 NM behind 2 min after each: held, so the highlight
    regenerates at the same ticks as without it."""
    assert _regenerations(runs["jitter"]) == _regenerations(runs["stream"])


def test_occasional_fixes_correct_the_plan(runs, truth):
    _, arrival = truth
    ticks = runs["occasional"]
    # The apron one-shot (no speed, on the ground by its height) at 08:20:
    # the departure is at least 08:30.
    apron = _at(ticks, "08:30").layer.progress
    assert apron.departed_at is None and apron.eta_destination == DEP + timedelta(minutes=30, hours=2)
    # Before the 09:05 fix (received 09:06) the flight left at 08:30 on the plan.
    assert _at(ticks, "09:00").layer.progress.along_nm > 0
    # One fix in the air: the take-off within a few minutes, the ETA within
    # the speed error, and it holds once the fix ages.
    for hhmm in ("09:10", "10:00"):
        p = _at(ticks, hhmm).layer.progress
        assert p.source == SOURCE_APP
        assert _minutes_off(p.departed_at, TAKEOFF) < 6
        assert _minutes_off(p.eta_destination, arrival) < 15, (hhmm, p.eta_destination)
    assert _at(ticks, "10:00").layer.progress.estimated is True
    # The one-shot at the destination at 10:50 lands the flight.
    landed = _at(ticks, "11:00").layer.progress
    assert landed.landed_at is not None and _minutes_off(landed.landed_at, arrival) < 15
    assert landed.along_nm == pytest.approx(landed.route_nm, abs=0.1)
