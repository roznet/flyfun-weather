"""Start re-times the flight (#812): the window, the 5-min started pass, the
Start update, push consent, TAF-at-ETA and replay at the fix-given times.

The fusion rules are in tests/test_flight_progress.py; the position
endpoint's Start, landing and previous-flight paths in
tests/test_api.py::TestLivePosition.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import test_live_tick as tt
from weatherbrief.analysis.flight_progress import SOURCE_APP, FixData, ProgressEvent
from weatherbrief.tasks.live_layer import record_progress_event

DEP = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def _ground_fix(at, recorded=None, along=0.2, route_nm=150.0):
    """An app fix on the apron at the departure, with its stored projection
    (what the schedule-only progress reads)."""
    return ProgressEvent(
        kind="fix", at=at, recorded_at=recorded or at, source=SOURCE_APP, lat=50.0, lon=0.0,
        fix=FixData(gs_kt=0.0, h_accuracy_m=5.0, along_nm=along, cross_nm=0.1, route_nm=route_nm),
    )


# --- The live window ------------------------------------------------------------


def test_an_early_start_never_closes_the_window_before_the_plan():
    """Start 2 h early on a 1 h flight puts the arrival before the planned
    departure: the window still runs to the planned arrival + 1 h."""
    from weatherbrief.tasks.live_tick import live_window_end

    events = (_ground_fix(DEP - timedelta(hours=2)),)
    now = DEP - timedelta(minutes=30)
    assert live_window_end(DEP, 1.0, 1.0, events, now) == DEP + timedelta(hours=2)


def test_a_leg_departing_after_its_planned_arrival_stays_open_to_its_own():
    from weatherbrief.tasks.live_tick import live_window_end

    start = DEP + timedelta(hours=1, minutes=30)  # a 1 h leg, Start 30 min after its planned arrival
    events = (_ground_fix(start),)
    now = start + timedelta(minutes=5)
    assert live_window_end(DEP, 1.0, 1.0, events, now) == start + timedelta(minutes=10, hours=2)


def test_a_landing_inferred_from_a_fix_closes_the_window():
    """A fix on the ground at the destination (the previous-flight path, or
    the app opened on the apron): the window closes a grace after it."""
    from weatherbrief.tasks.live_tick import LANDED_GRACE, live_window_end

    takeoff = ProgressEvent("takeoff", DEP, DEP)
    apron = ProgressEvent(
        kind="fix", at=DEP + timedelta(minutes=70), recorded_at=DEP + timedelta(minutes=90),
        source=SOURCE_APP, lat=50.0, lon=4.0,
        fix=FixData(gs_kt=0.0, h_accuracy_m=5.0, along_nm=149.0, cross_nm=0.1, route_nm=150.0),
    )
    end = live_window_end(DEP, 1.0, 1.0, (takeoff, apron), DEP + timedelta(minutes=95))
    assert end == DEP + timedelta(minutes=90) + LANDED_GRACE


# --- The started pass -------------------------------------------------------------


def test_find_live_flights_started_only(db_session, dev_user, tmp_path):
    from weatherbrief.tasks.live_tick import find_live_flights

    now = tt.NOW

    def flight(fid, departure):
        pack_dir = tmp_path / "packs" / dev_user / fid / "pack"
        return tt._flight(db_session, dev_user, fid, departure, artifact_path=str(pack_dir)), pack_dir.parent

    started, started_dir = flight("zz-started", now + timedelta(minutes=40))
    flight("zz-waiting", now + timedelta(minutes=40))
    airborne, airborne_dir = flight("zz-airborne", now - timedelta(minutes=30))
    record_progress_event(started_dir, _ground_fix(now - timedelta(minutes=2), route_nm=150.0))
    record_progress_event(airborne_dir, ProgressEvent("takeoff", now - timedelta(minutes=25),
                                                      now - timedelta(minutes=24)))
    assert {f.id for f in find_live_flights(db_session, now)} == {"zz-started", "zz-waiting", "zz-airborne"}
    assert {f.id for f in find_live_flights(db_session, now, started_only=True)} == {"zz-started", "zz-airborne"}


def test_scheduler_runs_the_started_pass(monkeypatch):
    from weatherbrief import scheduler

    ran = {}

    class FakeTick:
        def run(self, db, db_path, **kw):
            ran.update(kw, db_path=db_path)

    monkeypatch.setattr("weatherbrief.tasks.live_tick.LiveTick", FakeTick)
    monkeypatch.setattr("weatherbrief.tasks.live_tick.live_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: MagicMock())
    scheduler._run_started_tick_once(SimpleNamespace(db_path="/fake/db"))
    assert ran == {"started_only": True, "db_path": "/fake/db"}
    assert scheduler._STARTED_TICK_SECONDS * 2 == scheduler._VERIF_POLL_SECONDS

    ran.clear()
    monkeypatch.setattr("weatherbrief.tasks.live_tick.live_enabled", lambda: False)
    scheduler._run_started_tick_once(SimpleNamespace(db_path="/fake/db"))
    assert ran == {}


# --- The Start update ----------------------------------------------------------------


def test_start_update_refreshes_and_regenerates_the_highlight(db_session, dev_user, tmp_path, monkeypatch):
    """The ↻ path, then the highlight written against the one the pilot had."""
    from weatherbrief.models.live import LiveGlance, LiveHighlight
    from weatherbrief.tasks import live_highlight, live_layer, live_tick, route_weather

    pack_dir = tt._write_pack(tmp_path)
    tt._flight(db_session, dev_user, "zz-soon", tt.NOW + timedelta(hours=1), artifact_path=str(pack_dir))
    # The test's own session, never committed: the engine is shared with
    # later tests, and the fixture rolls back.
    monkeypatch.setattr("flyfun_common.db.SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(db_session, "commit", lambda: None)
    gate = {"phase": "before departure"}
    before = SimpleNamespace(glance=LiveGlance(
        as_of=tt.NOW, headline="h", comparison="as_briefed", lines=[],
        highlight=LiveHighlight(text="old", model="m", facts_hash="x", gate=gate, generated_at=tt.NOW),
    ))
    after = SimpleNamespace(glance=None)
    layers = iter([before, after])
    monkeypatch.setattr(live_layer, "live_for_pack", lambda _d: next(layers))
    refresh = MagicMock()
    monkeypatch.setattr(route_weather, "run_realtime_refresh", refresh)
    monkeypatch.setattr(live_highlight, "highlight_enabled", lambda: True)
    calls = []
    monkeypatch.setattr(live_highlight, "ensure_highlight",
                        lambda d, layer, **kw: calls.append((layer, kw)) or live_highlight.HighlightOutcome("written"))
    monkeypatch.setattr(live_highlight, "charge_highlight", lambda *a, **k: None)
    monkeypatch.setattr(live_tick, "_cloud_source", lambda db, f: None)

    assert live_tick.run_start_update("zz-soon", "/fake/db") is True
    assert refresh.call_args.args == (pack_dir, "/fake/db")
    assert refresh.call_args.kwargs["flight_id"] == "zz-soon"
    assert "report_source" not in refresh.call_args.kwargs  # a fresh fetch, like ↻
    assert calls == [(after, {"retimed_from": gate})]


def test_start_update_never_raises(monkeypatch):
    from weatherbrief.tasks import live_tick

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("flyfun_common.db.SessionLocal", boom)
    try:
        assert live_tick.run_start_update("zz", "/fake/db") is False
    except RuntimeError:
        raise AssertionError("run_start_update raised")


# --- Push consent --------------------------------------------------------------------


def test_start_is_consent_to_live_alerts(db_session, monkeypatch, tmp_path):
    import test_live_alert_push as tp

    from weatherbrief.notify import live_alerts

    row = tp._flight_row()
    row.auto_refresh = False
    assert live_alerts.skip_reason(db_session, row, trigger="tick")[0] == "no_consent"
    reason = live_alerts.skip_reason(db_session, row, trigger="tick", started=True)[0]
    assert reason != "no_consent"
    # A ↻ press never pushes, Start or not.
    assert live_alerts.skip_reason(db_session, row, trigger="user", started=True)[0] == "user_trigger"

    pack_dir = tmp_path / "flight" / "pack"
    assert live_alerts._pressed_start(row, pack_dir, tp.DEP) is False
    record_progress_event(pack_dir.parent, _ground_fix(tp.DEP - timedelta(minutes=20)))
    assert live_alerts._pressed_start(row, pack_dir, tp.DEP) is True


# --- TAF-at-ETA at the ribbon's ETA -----------------------------------------------------


def test_taf_at_eta_reads_the_flights_own_eta():
    """#812: a fix's delay moves an airport's ETA, not only the departure, so
    TAF-at-ETA is read at ``FlightProgress.eta`` when the refresh passes it."""
    from unittest.mock import patch

    from weatherbrief.models import RouteConfig, Waypoint
    from weatherbrief.tasks import route_weather

    raw = SimpleNamespace(
        icao="ZZMD", name="Mid", distance_from_route_nm=0.0, enroute_distance_nm=50.0, along_track_nm=50.0,
        latest_metar=None, latest_taf=SimpleNamespace(observation_time=DEP), reports=None,
    )
    service = MagicMock()
    service.fetch_route_weather.return_value = SimpleNamespace(airports=[raw])
    route = RouteConfig(name="T", flight_duration_hours=1.0,
                        waypoints=[Waypoint(icao="ZZAA", name="A", lat=50.0, lon=0.0),
                                   Waypoint(icao="ZZBB", name="B", lat=50.0, lon=2.0)])
    read_at = []
    with patch("euro_aip.briefing.weather.route_weather.RouteWeatherService", return_value=service), \
         patch("weatherbrief.airports._load_airport_model", return_value=MagicMock()), \
         patch.object(route_weather, "_apply_taf_at_eta", lambda obs, taf, t: read_at.append(t)):
        route_weather.run_route_weather(route, DEP, 30.0, "/fake/db")
        route_weather.run_route_weather(route, DEP, 30.0, "/fake/db",
                                        eta=lambda along: DEP + timedelta(minutes=along))
        route_weather.run_route_weather(route, DEP, 30.0, "/fake/db", eta=lambda along: None)
    plan = read_at[0]
    assert plan != DEP + timedelta(minutes=50)
    assert read_at[1] == DEP + timedelta(minutes=50)
    assert read_at[2] == plan  # no ETA from progress: the planned share
