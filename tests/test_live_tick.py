"""Server live-window tick (#637, phase 2): window, fetch-once, scheduler wiring."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from weatherbrief.db.models import BriefingPackRow, FlightRow
from weatherbrief.tasks.live_tick import (
    LiveTick,
    SharedReportSource,
    SharedSigmetSource,
    find_live_flights,
    in_live_window,
)
from weatherbrief.tasks.route_weather import SigmetSourceUnavailable

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_engine():
    from conftest import make_app_engine
    engine = make_app_engine()
    yield engine
    engine.dispose()


def _flight(db_session, dev_user, fid, departure, duration=2.0, artifact_path="/tmp/zz"):
    row = FlightRow(
        id=fid, user_id=dev_user, route_name="ZZAA-ZZBB",
        waypoints_json=json.dumps(["ZZAA", "ZZBB"]),
        departure_time=departure, cruise_altitude_ft=6000, flight_ceiling_ft=12000,
        flight_duration_hours=duration,
    )
    db_session.add(row)
    db_session.flush()
    db_session.add(BriefingPackRow(
        flight_id=fid, fetch_timestamp=departure - timedelta(hours=4),
        days_out=0, artifact_path=artifact_path,
    ))
    db_session.flush()
    return row


# --- Window -----------------------------------------------------------------


@pytest.mark.parametrize("offset_h, expected", [
    (-3.5, False),   # more than 3 h before departure
    (-3.0, True),    # window opens
    (0.0, True),
    (3.0, True),     # 2 h flight + 1 h after
    (3.1, False),
])
def test_in_live_window_bounds(offset_h, expected):
    dep = NOW
    assert in_live_window(dep, 2.0, dep + timedelta(hours=offset_h), 3.0, 1.0) is expected


def test_window_hours_from_env(monkeypatch):
    from weatherbrief.tasks.live_tick import live_window_hours

    monkeypatch.setenv("WB_LIVE_WINDOW_BEFORE_H", "5")
    monkeypatch.setenv("WB_LIVE_WINDOW_AFTER_H", "junk")
    assert live_window_hours() == (5.0, 1.0)


def test_find_live_flights(db_session, dev_user):
    _flight(db_session, dev_user, "zz-soon", NOW + timedelta(hours=2))
    _flight(db_session, dev_user, "zz-later", NOW + timedelta(hours=5))
    _flight(db_session, dev_user, "zz-done", NOW - timedelta(hours=4), duration=2.0)
    ids = {f.id for f in find_live_flights(db_session, NOW)}
    assert ids == {"zz-soon"}


# --- Shared sources ---------------------------------------------------------


def _report(icao):
    return SimpleNamespace(icao=icao)


def test_shared_report_source_serves_only_requested_and_dedups():
    a = _report("ZZAA")
    src = SharedReportSource({"ZZAA": [a, a], "ZZBB": [_report("ZZBB")]})
    assert src.fetch_weather(["zzaa"]) == [a]


def test_shared_sigmet_source_fetches_once():
    upstream = MagicMock()
    upstream.fetch_isigmet.return_value = ["s"]
    src = SharedSigmetSource(upstream)
    assert src.fetch_isigmet(region="eur") == ["s"]
    assert src.fetch_isigmet(region="eur") == ["s"]
    upstream.fetch_isigmet.assert_called_once()


def test_shared_sigmet_source_forwards_the_lookahead_only_when_set():
    from datetime import timedelta

    upstream = MagicMock()
    upstream.fetch_isigmet.return_value = []
    src = SharedSigmetSource(upstream)
    src.fetch_isigmet(region="eur")
    assert "lookahead" not in upstream.fetch_isigmet.call_args.kwargs
    src.fetch_isigmet(region="eur", lookahead=timedelta(hours=4))
    assert upstream.fetch_isigmet.call_args.kwargs["lookahead"] == timedelta(hours=4)
    # Cached per lookahead, so one fetch serves every flight of the tick.
    src.fetch_isigmet(region="eur", lookahead=timedelta(hours=4))
    assert upstream.fetch_isigmet.call_count == 2


def test_top_up_fetches_only_airports_verification_did_not_cover():
    upstream = MagicMock()
    upstream.fetch_weather.return_value = [_report("ZZCC")]
    tick = LiveTick(upstream=upstream)
    tick.sink("ZZAA", [_report("ZZAA")])
    tick.sink("ZZBB", [])  # covered, even with no report
    assert tick._top_up({"ZZAA", "ZZBB", "ZZCC"}) == 1
    upstream.fetch_weather.assert_called_once_with(["ZZCC"], metar_hours=3)
    # A second pass needs nothing more.
    assert tick._top_up({"ZZCC"}) == 0


# --- run() ------------------------------------------------------------------


def _write_pack(tmp_path):
    pack_dir = tmp_path / "packs" / "u" / "zz-soon" / "p1"
    pack_dir.mkdir(parents=True)
    (pack_dir / "briefing.json").write_text(json.dumps({
        "route": {
            "name": "zz",
            "waypoints": [
                {"icao": "ZZAA", "name": "A", "lat": 50.0, "lon": 1.0},
                {"icao": "ZZBB", "name": "B", "lat": 51.0, "lon": 2.0},
            ],
        },
        "route_observations": {"corridor_nm": 25.0, "fetch_time": NOW.isoformat(),
                               "airports_found": 0, "airports_with_metar": 0,
                               "airports_with_taf": 0},
    }))
    return pack_dir


def test_run_refreshes_each_flight_from_one_shared_fetch(db_session, dev_user, tmp_path):
    pack_dir = _write_pack(tmp_path)
    _flight(db_session, dev_user, "zz-soon", NOW + timedelta(hours=1), artifact_path=str(pack_dir))

    upstream = MagicMock()
    upstream.fetch_weather.return_value = []
    tick = LiveTick(upstream=upstream, sigmet_upstream=MagicMock())

    def fake_route_weather(self, route_icaos, corridor_nm, model, metar_hours=3):
        # The route arrives as NavPoints carrying the pack's coordinates, so
        # navaids and fixes keep their place in the corridor geometry.
        assert [(p.name, p.latitude, p.longitude) for p in route_icaos] == [
            ("ZZAA", 50.0, 1.0), ("ZZBB", 51.0, 2.0),
        ]
        self._get_source().fetch_weather([p.name for p in route_icaos] + ["ZZMID"])
        return SimpleNamespace(airports=[])

    with patch(
        "euro_aip.briefing.weather.route_weather.RouteWeatherService.fetch_route_weather",
        fake_route_weather,
    ), patch(
        "weatherbrief.airports._load_airport_model", return_value=MagicMock(),
    ), patch(
        "weatherbrief.tasks.route_weather.run_realtime_refresh",
    ) as mock_refresh:
        result = tick.run(db_session, "/fake/db", now=NOW)

    # ``highlighted`` is 0 here: no ANTHROPIC_API_KEY in the suite, so #697's
    # highlight pass is off and the tick is otherwise unchanged.
    assert result == {"flights": 1, "updated": 1, "fetched": 3, "highlighted": 0}
    # One batched upstream fetch for every airport the corridor needs.
    upstream.fetch_weather.assert_called_once_with(["ZZAA", "ZZBB", "ZZMID"], metar_hours=3)
    kwargs = mock_refresh.call_args.kwargs
    assert kwargs["flight_id"] == "zz-soon"
    assert isinstance(kwargs["report_source"], SharedReportSource)
    assert kwargs["sigmet_source"] is tick._sigmets
    assert mock_refresh.call_args.args[0] == pack_dir


def test_scheduler_runs_tick_even_when_verification_fails(monkeypatch):
    from weatherbrief import scheduler

    ran = {}

    class FakeTick:
        def sink(self, *a):
            pass

        def run(self, db, db_path):
            ran["tick"] = db_path

    monkeypatch.setattr("weatherbrief.tasks.live_tick.LiveTick", FakeTick)
    monkeypatch.setattr("weatherbrief.tasks.live_tick.live_enabled", lambda: True)
    monkeypatch.setattr(
        "weatherbrief.tasks.verification.collect_and_store",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: MagicMock())
    scheduler._run_verification_once(SimpleNamespace(db_path="/fake/db"))
    assert ran == {"tick": "/fake/db"}


def test_scheduler_skips_tick_when_disabled(monkeypatch):
    from weatherbrief import scheduler

    monkeypatch.setenv("DISABLE_LIVE_LAYER", "1")
    collect = MagicMock(return_value={"flights": 0})
    monkeypatch.setattr("weatherbrief.tasks.verification.collect_and_store", collect)
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: MagicMock())
    scheduler._run_verification_once(SimpleNamespace(db_path="/fake/db"))
    assert collect.call_args.kwargs["report_sink"] is None


def test_shared_source_raises_for_uncovered_airports():
    from weatherbrief.tasks.live_tick import UncoveredAirportsError

    src = SharedReportSource({"ZZAA": []}, covered={"ZZAA"})
    assert src.fetch_weather(["ZZAA"]) == []
    with pytest.raises(UncoveredAirportsError):
        src.fetch_weather(["ZZAA", "ZZBB"])


def test_failed_top_up_skips_the_flight_instead_of_blanking_it(db_session, dev_user, tmp_path):
    """A transient upstream failure must not commit empty observations."""
    pack_dir = _write_pack(tmp_path)
    _flight(db_session, dev_user, "zz-soon", NOW + timedelta(hours=1), artifact_path=str(pack_dir))
    upstream = MagicMock()
    upstream.fetch_weather.side_effect = RuntimeError("aviationweather.gov down")
    tick = LiveTick(upstream=upstream, sigmet_upstream=MagicMock())

    def fake_route_weather(self, route_icaos, corridor_nm, model, metar_hours=3):
        self._get_source().fetch_weather(route_icaos)
        return SimpleNamespace(airports=[])

    with patch(
        "euro_aip.briefing.weather.route_weather.RouteWeatherService.fetch_route_weather",
        fake_route_weather,
    ), patch(
        "weatherbrief.airports._load_airport_model", return_value=MagicMock(),
    ), patch(
        "weatherbrief.tasks.live_layer.commit_live_update",
    ) as mock_commit:
        result = tick.run(db_session, "/fake/db", now=NOW)

    assert result["updated"] == 0
    mock_commit.assert_not_called()


def test_shared_sigmet_source_caches_failure():
    upstream = MagicMock()
    upstream.fetch_isigmet.side_effect = RuntimeError("down")
    src = SharedSigmetSource(upstream)
    for _ in range(3):
        with pytest.raises(SigmetSourceUnavailable):
            src.fetch_isigmet(region="eur")
    upstream.fetch_isigmet.assert_called_once()
