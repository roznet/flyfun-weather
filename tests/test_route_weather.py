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
    patch briefing.json, return RouteObservations. No model/GRIB/LLM.
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

    def test_patches_briefing_and_returns_obs(self, tmp_path, two_wp_route):
        import json

        from weatherbrief.tasks.route_weather import run_realtime_refresh

        self._write_pack(tmp_path, two_wp_route)
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
            result = run_realtime_refresh(tmp_path, "/fake/db")

        # Returned the refreshed observations + SIGMETs.
        assert result.observations.corridor_nm == 30.0
        assert result.observations.airports_found == 1
        assert result.sigmets is not None
        assert result.sigmets.count == 1
        # Used the pack's stored corridor/route and target time (not network).
        mock_fetch.assert_called_once()
        mock_sigmet.assert_called_once()
        # Patched briefing.json on disk (both observations and SIGMETs).
        patched = json.loads((tmp_path / "briefing.json").read_text())
        assert patched["route_observations"]["airports_found"] == 1
        assert patched["route_sigmets"]["count"] == 1

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
        (tmp_path / "briefing.json").write_text(json.dumps(briefing))
        (tmp_path / "forecasts.json").write_text(json.dumps({"forecasts": []}))
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
            run_realtime_refresh(tmp_path, "/fake/db")

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
