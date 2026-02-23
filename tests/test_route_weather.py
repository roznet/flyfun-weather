"""Tests for route METAR/TAF integration."""

from datetime import datetime
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
)
from weatherbrief.tasks.route_weather import (
    _classify_discrepancy,
    _compute_route_distances,
    _find_nearest_waypoint,
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
