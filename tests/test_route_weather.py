"""Tests for route METAR/TAF integration."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    RouteConfig,
    Waypoint,
    WaypointForecast,
)
from weatherbrief.models.observations import (
    AirportObservation,
    ObservationComparison,
    RouteObservations,
    RouteSigmets,
    SigmetAlongRoute,
)
from weatherbrief.tasks.route_weather import (
    _classify_discrepancy,
    _compute_route_distances,
    _departure_day_window,
    _find_nearest_waypoint,
    _sigmet_altitude_band,
    _worst_category,
    run_observation_comparison,
)


@pytest.fixture
def two_wp_route():
    """Simple two-waypoint route for testing."""
    return RouteConfig(
        name="Test Route",
        waypoints=[
            Waypoint(icao="EGTF", name="Fairoaks", lat=51.348, lon=-0.559),
            Waypoint(icao="LFQA", name="Reims", lat=49.310, lon=3.620),
        ],
        cruise_altitude_ft=6000,
        flight_duration_hours=2.0,
    )


# --- _worst_category ---

def test_worst_category_single():
    assert _worst_category(["VFR"]) == "VFR"


def test_worst_category_mixed():
    assert _worst_category(["VFR", "IFR", "MVFR"]) == "IFR"


def test_worst_category_lifr():
    assert _worst_category(["MVFR", "LIFR"]) == "LIFR"


def test_worst_category_empty():
    assert _worst_category([]) is None


# --- _classify_discrepancy ---

def test_classify_same_category():
    assert _classify_discrepancy("VFR", "VFR") == "CONFIRMING"


def test_classify_adjacent_categories():
    assert _classify_discrepancy("VFR", "MVFR") == "SIGNIFICANT"
    assert _classify_discrepancy("MVFR", "IFR") == "SIGNIFICANT"


def test_classify_two_apart():
    assert _classify_discrepancy("VFR", "IFR") == "CONFLICTING"


def test_classify_three_apart():
    assert _classify_discrepancy("VFR", "LIFR") == "CONFLICTING"


def test_classify_none_obs():
    assert _classify_discrepancy(None, "VFR") == "CONFIRMING"


def test_classify_none_model():
    assert _classify_discrepancy("VFR", None) == "CONFIRMING"


# --- _compute_route_distances ---

def test_compute_route_distances(two_wp_route):
    distances = _compute_route_distances(two_wp_route)
    assert len(distances) == 2
    assert distances[0] == 0.0
    assert distances[1] > 100  # ~180nm from Fairoaks to Reims


# --- _find_nearest_waypoint ---

def test_find_nearest_waypoint_at_start(two_wp_route):
    distances = _compute_route_distances(two_wp_route)
    result = _find_nearest_waypoint(0.0, two_wp_route, distances)
    assert result == "EGTF"


def test_find_nearest_waypoint_at_end(two_wp_route):
    distances = _compute_route_distances(two_wp_route)
    result = _find_nearest_waypoint(distances[-1], two_wp_route, distances)
    assert result == "LFQA"


def test_find_nearest_waypoint_none_distance(two_wp_route):
    distances = _compute_route_distances(two_wp_route)
    result = _find_nearest_waypoint(None, two_wp_route, distances)
    assert result == "EGTF"  # defaults to origin


# --- run_observation_comparison ---

def test_observation_comparison_confirming(two_wp_route):
    """When METAR and model agree on VFR, comparison is CONFIRMING."""
    obs = RouteObservations(
        corridor_nm=30,
        fetch_time=datetime(2024, 6, 1, 10, 0),
        airports_found=1,
        airports_with_metar=1,
        airports_with_taf=0,
        airports=[
            AirportObservation(
                icao="EGTF",
                name="Fairoaks",
                distance_from_route_nm=0.0,
                nearest_waypoint_icao="EGTF",
                has_metar=True,
                metar_flight_category="VFR",
                metar_visibility_m=10000,
                metar_wind_speed_kt=8,
            ),
        ],
    )

    target_time = datetime(2024, 6, 1, 10, 0)
    forecasts = [
        WaypointForecast(
            waypoint=Waypoint(icao="EGTF", name="Fairoaks", lat=51.348, lon=-0.559),
            model=ModelSource.GFS,
            fetched_at=target_time,
            hourly=[
                HourlyForecast(
                    time=target_time,
                    visibility_m=15000.0,  # > 5sm -> VFR
                    wind_speed_10m_kt=10.0,
                ),
            ],
        ),
    ]

    result = run_observation_comparison(obs, forecasts, target_time, two_wp_route)
    assert len(result.comparisons) == 1
    assert result.comparisons[0].category_match == "CONFIRMING"
    assert not result.has_conflicts


def test_observation_comparison_conflicting(two_wp_route):
    """When METAR says IFR but model says VFR, comparison is CONFLICTING."""
    obs = RouteObservations(
        corridor_nm=30,
        fetch_time=datetime(2024, 6, 1, 10, 0),
        airports_found=1,
        airports_with_metar=1,
        airports_with_taf=0,
        airports=[
            AirportObservation(
                icao="EGTF",
                name="Fairoaks",
                distance_from_route_nm=0.0,
                nearest_waypoint_icao="EGTF",
                has_metar=True,
                metar_flight_category="IFR",
                metar_visibility_m=2000,
                metar_wind_speed_kt=12,
            ),
        ],
    )

    target_time = datetime(2024, 6, 1, 10, 0)
    forecasts = [
        WaypointForecast(
            waypoint=Waypoint(icao="EGTF", name="Fairoaks", lat=51.348, lon=-0.559),
            model=ModelSource.GFS,
            fetched_at=target_time,
            hourly=[
                HourlyForecast(
                    time=target_time,
                    visibility_m=15000.0,  # > 5sm -> VFR
                    wind_speed_10m_kt=10.0,
                ),
            ],
        ),
    ]

    result = run_observation_comparison(obs, forecasts, target_time, two_wp_route)
    assert len(result.comparisons) == 1
    assert result.comparisons[0].category_match == "CONFLICTING"
    assert result.has_conflicts


def test_observation_comparison_skips_no_metar(two_wp_route):
    """Airports without METAR are skipped in comparison."""
    obs = RouteObservations(
        corridor_nm=30,
        fetch_time=datetime(2024, 6, 1, 10, 0),
        airports_found=1,
        airports_with_metar=0,
        airports_with_taf=0,
        airports=[
            AirportObservation(
                icao="EGTF",
                name="Fairoaks",
                distance_from_route_nm=0.0,
                nearest_waypoint_icao="EGTF",
                has_metar=False,
            ),
        ],
    )

    target_time = datetime(2024, 6, 1, 10, 0)
    result = run_observation_comparison(obs, [], target_time, two_wp_route)
    assert len(result.comparisons) == 0


# --- RouteObservations serialization ---

def test_route_observations_roundtrip():
    """RouteObservations can serialize and deserialize."""
    obs = RouteObservations(
        corridor_nm=25,
        fetch_time=datetime(2024, 6, 1, 12, 0),
        airports_found=3,
        airports_with_metar=2,
        airports_with_taf=1,
        worst_metar_category="MVFR",
        phenomena_along_route=["RA", "FG"],
        airports=[
            AirportObservation(
                icao="EGLL",
                distance_from_route_nm=5.0,
                nearest_waypoint_icao="EGLL",
                has_metar=True,
                metar_flight_category="MVFR",
                metar_raw="METAR EGLL 011200Z 24012KT 4000 -RA BKN015 12/10 Q1018",
            ),
        ],
        comparisons=[
            ObservationComparison(
                icao="EGLL",
                obs_category="MVFR",
                model_category="VFR",
                category_match="SIGNIFICANT",
                detail="METAR MVFR vs model VFR",
            ),
        ],
    )

    json_str = obs.model_dump_json()
    loaded = RouteObservations.model_validate_json(json_str)
    assert loaded.airports_found == 3
    assert loaded.airports[0].icao == "EGLL"
    assert loaded.comparisons[0].category_match == "SIGNIFICANT"
    assert loaded.phenomena_along_route == ["RA", "FG"]


# --- ForecastSnapshot with route_observations ---

def test_snapshot_includes_route_observations():
    """ForecastSnapshot can hold route_observations."""
    from weatherbrief.models import ForecastSnapshot

    snapshot = ForecastSnapshot(
        route=RouteConfig(
            name="test",
            waypoints=[
                Waypoint(icao="EGTF", name="Fairoaks", lat=51.3, lon=-0.5),
                Waypoint(icao="LFQA", name="Reims", lat=49.3, lon=3.6),
            ],
        ),
        target_date="2024-06-01",
        fetch_date="2024-06-01",
        days_out=0,
        route_observations=RouteObservations(
            corridor_nm=30,
            fetch_time=datetime(2024, 6, 1, 10, 0),
            airports_found=5,
            airports_with_metar=3,
            airports_with_taf=2,
        ),
    )

    assert snapshot.route_observations is not None
    assert snapshot.route_observations.airports_found == 5

    # Roundtrip
    json_str = snapshot.model_dump_json()
    loaded = ForecastSnapshot.model_validate_json(json_str)
    assert loaded.route_observations is not None
    assert loaded.route_observations.airports_with_metar == 3


# --- Digest formatting ---

def test_text_digest_includes_observations():
    """Text digest includes METAR/TAF section when observations present."""
    from weatherbrief.models import ForecastSnapshot

    snapshot = ForecastSnapshot(
        route=RouteConfig(
            name="test",
            waypoints=[
                Waypoint(icao="EGTF", name="Fairoaks", lat=51.3, lon=-0.5),
                Waypoint(icao="LFQA", name="Reims", lat=49.3, lon=3.6),
            ],
        ),
        target_date="2024-06-01",
        fetch_date="2024-06-01",
        days_out=0,
        route_observations=RouteObservations(
            corridor_nm=30,
            fetch_time=datetime(2024, 6, 1, 10, 0),
            airports_found=1,
            airports_with_metar=1,
            airports_with_taf=0,
            worst_metar_category="VFR",
            airports=[
                AirportObservation(
                    icao="EGTF",
                    distance_from_route_nm=0.0,
                    nearest_waypoint_icao="EGTF",
                    has_metar=True,
                    metar_flight_category="VFR",
                    metar_raw="METAR EGTF 011000Z 24008KT 9999 FEW040 15/08 Q1020",
                ),
            ],
        ),
    )

    from weatherbrief.digest.text import format_digest

    text = format_digest(snapshot, datetime(2024, 6, 1, 10, 0))
    assert "METAR/TAF" in text
    assert "EGTF" in text
    assert "VFR" in text


def test_text_digest_no_observations_when_none():
    """Text digest omits METAR/TAF section when no observations."""
    from weatherbrief.models import ForecastSnapshot

    snapshot = ForecastSnapshot(
        route=RouteConfig(
            name="test",
            waypoints=[
                Waypoint(icao="EGTF", name="Fairoaks", lat=51.3, lon=-0.5),
                Waypoint(icao="LFQA", name="Reims", lat=49.3, lon=3.6),
            ],
        ),
        target_date="2024-06-01",
        fetch_date="2024-06-01",
        days_out=1,
    )

    from weatherbrief.digest.text import format_digest

    text = format_digest(snapshot, datetime(2024, 6, 1, 10, 0))
    assert "METAR/TAF" not in text


# --- run_realtime_refresh (issue #167 Part A seam) ---

class TestRunRealtimeRefresh:
    """The cheap real-time refresh seam: re-fetch obs from stored forecasts,
    fold them into the flight's live layer, return them. No model/GRIB/LLM.
    """

    def _write_pack(self, pack_dir, two_wp_route):
        import json

        briefing = {
            "route": two_wp_route.model_dump(mode="json"),
            "departure_time": "2026-05-20T09:00:00+00:00",
            "days_out": 0,
        }
        (pack_dir / "briefing.json").write_text(json.dumps(briefing))
        (pack_dir / "forecasts.json").write_text(json.dumps({"forecasts": []}))

    def test_writes_live_layer_and_leaves_pack_untouched(self, tmp_path, two_wp_route):
        """#637: the pack is immutable — the refresh lands in the per-flight
        live layer beside it, and briefing.json keeps the briefing's view."""
        import json

        from weatherbrief.tasks.live_layer import live_for_pack
        from weatherbrief.tasks.route_weather import run_realtime_refresh

        pack_dir = tmp_path / "flight-1" / "2026-05-20T07-00-00p00-00"
        pack_dir.mkdir(parents=True)
        self._write_pack(pack_dir, two_wp_route)
        before = (pack_dir / "briefing.json").read_text()
        fresh = RouteObservations(
            corridor_nm=30.0,
            fetch_time=datetime(2026, 5, 20, 9),
            airports_found=1,
            airports_with_metar=1,
            airports_with_taf=0,
            airports=[],
        )
        fresh_sigmets = RouteSigmets(
            corridor_nm=50.0,
            fetch_time=datetime(2026, 5, 20, 9),
            sigmets=[SigmetAlongRoute(fir_id="LFFF", hazard="TURB")],
        )
        with patch(
            "weatherbrief.tasks.route_weather.run_route_weather", return_value=fresh,
        ) as mock_fetch, patch(
            "weatherbrief.tasks.route_weather.run_route_sigmets", return_value=fresh_sigmets,
        ) as mock_sigmet, patch(
            "weatherbrief.airports.get_runway_ends", return_value={},
        ):
            result = run_realtime_refresh(
                pack_dir, "/fake/db",
                flight_id="flight-1", pack_timestamp="2026-05-20T07:00:00+00:00",
            )

        # Returned the refreshed observations + SIGMETs.
        assert result.observations.corridor_nm == 30.0
        assert result.observations.airports_found == 1
        assert result.sigmets is not None
        assert result.sigmets.count == 1
        mock_fetch.assert_called_once()
        mock_sigmet.assert_called_once()
        # The pack on disk is byte-for-byte what the briefing wrote.
        assert (pack_dir / "briefing.json").read_text() == before
        # The live layer holds the refresh, relative to this pack.
        layer = live_for_pack(pack_dir)
        assert layer is not None
        assert layer.flight_id == "flight-1"
        assert layer.route_observations.airports_found == 1
        assert layer.route_sigmets.count == 1
        assert result.live_updated_at == layer.live_updated_at
        # No baseline SIGMETs on this pack, so nothing is reported as changed.
        assert result.delta is not None
        assert result.delta.worsened is False
        assert result.changes is not None and result.changes.changes == []
        # Read-time trails ride the response like /live (#669).
        assert result.changes.recently_cleared == []
        meta = json.loads((pack_dir.parent / "live_meta.json").read_text())
        assert meta["pack_dir_name"] == pack_dir.name

    def test_persist_false_writes_nothing(self, tmp_path, two_wp_route):
        """Refreshing an older pack returns data but leaves the store alone."""
        from weatherbrief.tasks.route_weather import run_realtime_refresh

        pack_dir = tmp_path / "flight-1" / "old-pack"
        pack_dir.mkdir(parents=True)
        self._write_pack(pack_dir, two_wp_route)
        fresh = RouteObservations(
            corridor_nm=30.0, fetch_time=datetime(2026, 5, 20, 9),
            airports_found=0, airports_with_metar=0, airports_with_taf=0, airports=[],
        )
        with patch(
            "weatherbrief.tasks.route_weather.run_route_weather", return_value=fresh,
        ), patch(
            "weatherbrief.tasks.route_weather.run_route_sigmets", return_value=None,
        ), patch(
            "weatherbrief.airports.get_runway_ends", return_value={},
        ):
            result = run_realtime_refresh(pack_dir, "/fake/db", persist=False)

        assert result.live_updated_at is None
        assert not (pack_dir.parent / "live.json").exists()

    def test_uses_stored_corridor_nm(self, tmp_path, two_wp_route):
        import json

        from weatherbrief.tasks.route_weather import run_realtime_refresh

        briefing = {
            "route": two_wp_route.model_dump(mode="json"),
            "departure_time": "2026-05-20T09:00:00+00:00",
            "days_out": 0,
            "route_observations": {
                "corridor_nm": 45.0,
                "fetch_time": "2026-05-20T08:00:00+00:00",
                "airports_found": 0,
                "airports_with_metar": 0,
                "airports_with_taf": 0,
            },
        }
        pack_dir = tmp_path / "flight" / "pack"
        pack_dir.mkdir(parents=True)
        (pack_dir / "briefing.json").write_text(json.dumps(briefing))
        (pack_dir / "forecasts.json").write_text(json.dumps({"forecasts": []}))
        fresh = RouteObservations(
            corridor_nm=45.0, fetch_time=datetime(2026, 5, 20, 9),
            airports_found=0, airports_with_metar=0, airports_with_taf=0, airports=[],
        )
        with patch(
            "weatherbrief.tasks.route_weather.run_route_weather", return_value=fresh,
        ) as mock_fetch, patch(
            "weatherbrief.tasks.route_weather.run_route_sigmets",
            side_effect=Exception("sigmet disabled"),
        ), patch(
            "weatherbrief.airports.get_runway_ends", return_value={},
        ):
            run_realtime_refresh(pack_dir, "/fake/db")

        assert mock_fetch.call_args.kwargs["corridor_nm"] == 45.0

    def test_missing_briefing_raises(self, tmp_path):
        from weatherbrief.tasks.route_weather import run_realtime_refresh

        with pytest.raises(FileNotFoundError):
            run_realtime_refresh(tmp_path, "/fake/db")


# --- Route SIGMETs (issue #168) ---

def test_sigmet_altitude_band(two_wp_route):
    # two_wp_route cruises at 6000ft; band is surface to cruise + buffer.
    low, high = _sigmet_altitude_band(two_wp_route)
    assert low == 0
    assert high == 6000 + 5000


def test_departure_day_window_uses_end_of_departure_day():
    from datetime import timezone

    target = datetime(2026, 5, 20, 14, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc)
    win_from, win_to = _departure_day_window(target, now=now)
    assert win_from == now
    assert win_to == datetime(2026, 5, 20, 23, 59, 59, tzinfo=timezone.utc)


def test_departure_day_window_guards_inverted_window():
    from datetime import timezone

    # A late "now" past the departure day's end must not invert the window: it
    # collapses to a zero-length window pinned at end-of-day (no active SIGMETs).
    target = datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 21, 2, 0, tzinfo=timezone.utc)
    win_from, win_to = _departure_day_window(target, now=now)
    assert win_from == win_to == datetime(2026, 5, 20, 23, 59, 59, tzinfo=timezone.utc)


def test_departure_day_window_extends_past_midnight_for_late_flight():
    from datetime import timezone

    # 22:15Z departure, 2 h flight: airborne until 00:15Z next day. The window
    # must cover that, and stay open through the live layer's post-arrival
    # hour — a collapsed window reads as every SIGMET "no longer active".
    target = datetime(2026, 10, 1, 22, 15, tzinfo=timezone.utc)
    before = datetime(2026, 10, 1, 21, 0, tzinfo=timezone.utc)
    win_from, win_to = _departure_day_window(target, now=before, duration_h=2.0)
    assert win_from == before
    assert win_to == datetime(2026, 10, 2, 3, 15, tzinfo=timezone.utc)

    after_midnight = datetime(2026, 10, 2, 0, 45, tzinfo=timezone.utc)
    win_from, win_to = _departure_day_window(target, now=after_midnight, duration_h=2.0)
    assert win_from == after_midnight < win_to


def test_run_route_sigmets_maps_result(two_wp_route):
    """run_route_sigmets maps a RouteSigmetResult into the flat RouteSigmets model."""
    from datetime import timezone

    from weatherbrief.tasks.route_weather import run_route_sigmets

    sig = SimpleNamespace(
        fir_id="LFFF", fir_name="Paris", hazard="TURB", qualifier="SEV",
        base_ft=0, top_ft=24000,
        valid_from=datetime(2026, 5, 20, 8, tzinfo=timezone.utc),
        valid_to=datetime(2026, 5, 20, 14, tzinfo=timezone.utc),
        direction="NE", speed_kt=20,
        raw_text="LFFF SIGMET 1 VALID ... SEV TURB ...",
        coords=[(2.0, 49.0), (3.0, 49.0), (3.0, 50.0)],
    )
    route_sig = SimpleNamespace(
        sigmet=sig, matched_firs=["LFFF"],
        min_distance_nm=0.0, enroute_distance_from_nm=40.0, enroute_distance_to_nm=90.0,
    )
    fake_result = SimpleNamespace(route_firs=["LFFF", "EGTT"], sigmets=[route_sig])

    fake_service = MagicMock()
    fake_service.fetch_route_sigmets.return_value = fake_result

    with patch(
        "euro_aip.briefing.weather.route_sigmet.RouteSigmetService",
        return_value=fake_service,
    ), patch(
        "weatherbrief.airports._load_airport_model", return_value=MagicMock(),
    ):
        out = run_route_sigmets(
            route=two_wp_route,
            target_time=datetime(2026, 5, 20, 9, tzinfo=timezone.utc),
            corridor_nm=50.0,
            airports_db_path="/fake/db",
        )

    assert out.count == 1
    assert out.corridor_nm == 50.0
    assert out.hazards == ["TURB"]
    assert out.has_severe is True
    assert out.route_firs == ["LFFF", "EGTT"]
    s = out.sigmets[0]
    assert s.fir_id == "LFFF"
    assert s.hazard == "TURB"
    assert s.enroute_distance_from_nm == 40.0
    assert s.enroute_distance_to_nm == 90.0
    # Polygon retained for a future cross-section/map overlay.
    assert s.coords[0] == (2.0, 49.0)
    # Altitude band passed to the service is surface -> cruise+buffer.
    kwargs = fake_service.fetch_route_sigmets.call_args.kwargs
    assert kwargs["altitude_band_ft"] == (0, 6000 + 5000)


def test_run_route_sigmets_empty(two_wp_route):
    from datetime import timezone

    from weatherbrief.tasks.route_weather import run_route_sigmets

    fake_result = SimpleNamespace(route_firs=[], sigmets=[])
    fake_service = MagicMock()
    fake_service.fetch_route_sigmets.return_value = fake_result

    with patch(
        "euro_aip.briefing.weather.route_sigmet.RouteSigmetService",
        return_value=fake_service,
    ), patch(
        "weatherbrief.airports._load_airport_model", return_value=MagicMock(),
    ):
        out = run_route_sigmets(
            route=two_wp_route,
            target_time=datetime(2026, 5, 20, 9, tzinfo=timezone.utc),
            corridor_nm=50.0,
            airports_db_path="/fake/db",
        )
    assert out.count == 0
    assert out.sigmets == []
    assert out.has_severe is False


def test_text_digest_includes_sigmets(two_wp_route):
    from weatherbrief.digest.text import _format_route_sigmets

    sig = RouteSigmets(
        corridor_nm=50.0,
        fetch_time=datetime(2026, 5, 20, 9),
        sigmets=[SigmetAlongRoute(
            fir_id="LFFF", hazard="TURB", qualifier="SEV",
            base_ft=0, top_ft=24000,
            enroute_distance_from_nm=40.0, enroute_distance_to_nm=90.0,
            raw_text="LFFF SIGMET 1 ...",
        )],
    )
    assert sig.count == 1
    assert sig.hazards == ["TURB"]
    assert sig.has_severe is True
    lines = _format_route_sigmets(sig)
    text = "\n".join(lines)
    assert "SIGMETs Along Route" in text
    assert "TURB" in text
    assert "SEV" in text
    assert "SFC-FL240" in text


def test_sigmets_roundtrip_on_snapshot():
    """RouteSigmets serializes and reloads on a ForecastSnapshot."""
    from weatherbrief.models import ForecastSnapshot

    snap = ForecastSnapshot(
        route=RouteConfig(
            name="R",
            waypoints=[
                Waypoint(icao="EGTF", name="A", lat=51.3, lon=-0.5),
                Waypoint(icao="LFQA", name="B", lat=49.3, lon=3.6),
            ],
        ),
        target_date="2026-05-20",
        fetch_date="2026-05-20",
        days_out=0,
        route_sigmets=RouteSigmets(
            corridor_nm=50.0,
            fetch_time=datetime(2026, 5, 20, 9),
            sigmets=[SigmetAlongRoute(
                fir_id="LFFF", hazard="TS", coords=[(2.0, 49.0), (3.0, 50.0)],
            )],
        ),
    )
    dumped = snap.model_dump_json()
    reloaded = ForecastSnapshot.model_validate_json(dumped)
    assert reloaded.route_sigmets is not None
    assert reloaded.route_sigmets.count == 1
    assert reloaded.route_sigmets.sigmets[0].coords[0] == (2.0, 49.0)


# --- the comparison must grade on the same cloud source as the advisories ---


def _egjb_shape_analyses():
    """RoutePointAnalysis in the EGJB 2026-08-16 shape.

    DD finds a sub-1000 ft deck in a moist marine boundary layer; the model's
    own cloud scheme reports no low cloud at all (only high cirrus).
    """
    from weatherbrief.models import RoutePointAnalysis
    from weatherbrief.models.analysis import (
        CloudCoverage,
        EnhancedCloudLayer,
        SoundingAnalysis,
        ThermodynamicIndices,
    )

    dd = [EnhancedCloudLayer(base_ft=503, top_ft=632, coverage=CloudCoverage.BKN)]
    nwp = [EnhancedCloudLayer(base_ft=34250, top_ft=36709,
                              coverage=CloudCoverage.SCT, source="nwp_3d")]
    sounding = SoundingAnalysis(
        cloud_layers=list(dd),
        dd_cloud_layers=list(dd),
        nwp_cloud_layers=list(nwp),
        indices=ThermodynamicIndices(
            sounding_ceiling_ft=503.0,
            dd_sounding_ceiling_ft=503.0,
            nwp_sounding_ceiling_ft=None,
        ),
    )
    return [RoutePointAnalysis(
        point_index=0, lat=51.348, lon=-0.559, distance_from_origin_nm=0.0,
        interpolated_time=datetime(2024, 6, 1, 10, 0),
        forecast_hour=datetime(2024, 6, 1, 10, 0), track_deg=90.0,
        waypoint_icao="EGTF", sounding={"gfs": sounding},
    )]


def _vfr_obs_and_forecast():
    target_time = datetime(2024, 6, 1, 10, 0)
    obs = RouteObservations(
        corridor_nm=30, fetch_time=target_time,
        airports_found=1, airports_with_metar=1, airports_with_taf=0,
        airports=[AirportObservation(
            icao="EGTF", name="Fairoaks", distance_from_route_nm=0.0,
            nearest_waypoint_icao="EGTF", has_metar=True,
            metar_flight_category="VFR", metar_visibility_m=10000,
            metar_wind_speed_kt=8,
        )],
    )
    forecasts = [WaypointForecast(
        waypoint=Waypoint(icao="EGTF", name="Fairoaks", lat=51.348, lon=-0.559),
        model=ModelSource.GFS, fetched_at=target_time,
        hourly=[HourlyForecast(time=target_time, visibility_m=15000.0,
                               wind_speed_10m_kt=10.0)],
    )]
    return obs, forecasts, target_time


def test_comparison_grades_ceiling_on_the_default_nwp_cloud_source(two_wp_route):
    """Default (NWP) source: no low deck, so the model agrees with a VFR METAR.

    Regression for the split this PR could otherwise open — advisories resolved
    to NWP while this panel stayed on DD, so one briefing showed a VFR headline
    beside a "model says IFR, conflicting" comparison for the same airport/hour.
    """
    obs, forecasts, target_time = _vfr_obs_and_forecast()
    result = run_observation_comparison(
        obs, forecasts, target_time, two_wp_route,
        route_analyses=_egjb_shape_analyses(),
    )
    assert result.comparisons[0].model_category == "VFR"
    assert result.comparisons[0].category_match == "CONFIRMING"
    assert not result.has_conflicts


def test_comparison_honours_an_explicit_dd_cloud_source(two_wp_route):
    """An explicit DD profile still grades the comparison on the DD deck."""
    obs, forecasts, target_time = _vfr_obs_and_forecast()
    result = run_observation_comparison(
        obs, forecasts, target_time, two_wp_route,
        route_analyses=_egjb_shape_analyses(), cloud_source="dd",
    )
    assert result.comparisons[0].model_category == "IFR"
    assert result.comparisons[0].category_match != "CONFIRMING"


def test_comparison_does_not_mutate_the_caller_analyses(two_wp_route):
    """Resolution copies; the pipeline's own analyses must be left alone."""
    obs, forecasts, target_time = _vfr_obs_and_forecast()
    analyses = _egjb_shape_analyses()
    run_observation_comparison(
        obs, forecasts, target_time, two_wp_route, route_analyses=analyses,
    )
    s = analyses[0].sounding["gfs"]
    assert s.indices.sounding_ceiling_ft == 503.0
    assert s.cloud_layers[0].base_ft == 503


def test_metar_history_records_speci_and_previous_report():
    """#637 hysteresis inputs, from real euro_aip parsing of a 3 h window."""
    from datetime import timezone

    from euro_aip.briefing.weather.collection import WeatherCollection
    from euro_aip.briefing.weather.parser import WeatherParser

    from weatherbrief.models.observations import AirportObservation
    from weatherbrief.tasks.route_weather import _apply_metar_history

    ref = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    reports = [
        WeatherParser.parse_metar("METAR ZZAA 010820Z 24010KT 9999 FEW040 12/05 Q1015", reference=ref),
        WeatherParser.parse_metar("SPECI ZZAA 010841Z 24010KT 2000 BR OVC004 11/10 Q1015", reference=ref),
        # Same bulletin twice (an alias code) must not count as a second opinion.
        WeatherParser.parse_metar("SPECI ZZAA 010841Z 24010KT 2000 BR OVC004 11/10 Q1015", reference=ref),
    ]
    raw = SimpleNamespace(reports=WeatherCollection([r for r in reports if r is not None]))
    obs = AirportObservation(icao="ZZAA", distance_from_route_nm=0, nearest_waypoint_icao="ZZAA")
    _apply_metar_history(obs, raw)

    assert obs.metar_report_type == "SPECI"
    assert obs.metar_previous_flight_category == "VFR"
    assert obs.metar_previous_time == datetime(2026, 10, 1, 8, 20, tzinfo=timezone.utc)


def test_metar_history_single_report_has_no_previous():
    from datetime import timezone

    from euro_aip.briefing.weather.collection import WeatherCollection
    from euro_aip.briefing.weather.parser import WeatherParser

    from weatherbrief.models.observations import AirportObservation
    from weatherbrief.tasks.route_weather import _apply_metar_history

    ref = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    r = WeatherParser.parse_metar("METAR ZZAA 010820Z 24010KT 9999 FEW040 12/05 Q1015", reference=ref)
    obs = AirportObservation(icao="ZZAA", distance_from_route_nm=0, nearest_waypoint_icao="ZZAA")
    _apply_metar_history(obs, SimpleNamespace(reports=WeatherCollection([r])))
    assert obs.metar_report_type == "METAR"
    assert obs.metar_previous_flight_category is None


# --- Corridor geometry follows the filed route ---

@pytest.fixture
def navaid_route():
    """Airport → navaid → airport: the navaid bends the route off the direct line."""
    return RouteConfig(
        name="Dogleg",
        waypoints=[
            Waypoint(icao="EGTF", name="Fairoaks", lat=51.348, lon=-0.559),
            Waypoint(icao="LAM", name="LAM", lat=51.646, lon=0.152, kind="VORDME"),
            Waypoint(icao="LFQA", name="Reims", lat=49.310, lon=3.620),
        ],
        cruise_altitude_ft=6000,
        flight_duration_hours=2.0,
    )


def _route_triples(points):
    return [(p.name, p.latitude, p.longitude) for p in points]


def test_run_route_weather_passes_navaids_with_coordinates(navaid_route):
    """A navaid is not an airport, so passed by code euro_aip would drop it
    (and warn) and the METAR corridor would run straight EGTF→LFQA."""
    from datetime import timezone

    from weatherbrief.tasks.route_weather import run_route_weather

    fake_service = MagicMock()
    fake_service.fetch_route_weather.return_value = SimpleNamespace(airports=[])
    with patch(
        "euro_aip.briefing.weather.route_weather.RouteWeatherService",
        return_value=fake_service,
    ), patch(
        "weatherbrief.airports._load_airport_model", return_value=MagicMock(),
    ):
        run_route_weather(
            route=navaid_route,
            target_time=datetime(2026, 5, 20, 9, tzinfo=timezone.utc),
            corridor_nm=30.0,
            airports_db_path="/fake/db",
        )

    route_points = fake_service.fetch_route_weather.call_args.kwargs["route_icaos"]
    assert _route_triples(route_points) == [
        ("EGTF", 51.348, -0.559), ("LAM", 51.646, 0.152), ("LFQA", 49.310, 3.620),
    ]


def test_run_route_sigmets_passes_navaids_with_coordinates(navaid_route):
    from datetime import timezone

    from weatherbrief.tasks.route_weather import run_route_sigmets

    fake_service = MagicMock()
    fake_service.fetch_route_sigmets.return_value = SimpleNamespace(route_firs=[], sigmets=[])
    with patch(
        "euro_aip.briefing.weather.route_sigmet.RouteSigmetService",
        return_value=fake_service,
    ), patch(
        "weatherbrief.airports._load_airport_model", return_value=MagicMock(),
    ):
        run_route_sigmets(
            route=navaid_route,
            target_time=datetime(2026, 5, 20, 9, tzinfo=timezone.utc),
            corridor_nm=50.0,
            airports_db_path="/fake/db",
        )

    route_points = fake_service.fetch_route_sigmets.call_args.kwargs["route_icaos"]
    assert [p.name for p in route_points] == ["EGTF", "LAM", "LFQA"]
