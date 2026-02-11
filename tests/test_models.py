"""Tests for Pydantic models."""

from datetime import datetime, timezone

from weatherbrief.models import (
    ForecastSnapshot,
    HourlyForecast,
    ModelSource,
    PressureLevelData,
    RouteConfig,
    Waypoint,
    WaypointForecast,
    bearing_between,
    altitude_to_pressure_hpa,
)


def test_route_waypoints(sample_route):
    """Route.waypoints returns all waypoints in order."""
    wps = sample_route.waypoints
    assert len(wps) == 3
    assert wps[0].icao == "EGTK"
    assert wps[1].icao == "LFPB"
    assert wps[2].icao == "LSGS"


def test_route_origin_destination(sample_route):
    """Origin and destination are first/last waypoints."""
    assert sample_route.origin.icao == "EGTK"
    assert sample_route.destination.icao == "LSGS"


def test_route_two_waypoints(sample_waypoint):
    """Route with only two waypoints works."""
    route = RouteConfig(
        name="Test",
        waypoints=[
            sample_waypoint,
            Waypoint(icao="LSGS", name="Sion", lat=46.2, lon=7.3),
        ],
        cruise_altitude_ft=8000,
    )
    assert len(route.waypoints) == 2
    assert route.origin.icao == "EGTK"
    assert route.destination.icao == "LSGS"


def test_cruise_pressure_from_altitude():
    """cruise_pressure_hpa derives from altitude via standard atmosphere."""
    route = RouteConfig(
        name="Test",
        waypoints=[
            Waypoint(icao="EGTK", name="Oxford", lat=51.8, lon=-1.3),
            Waypoint(icao="LSGS", name="Sion", lat=46.2, lon=7.3),
        ],
        cruise_altitude_ft=8000,
    )
    # 8000ft ≈ 752 hPa in standard atmosphere
    assert 745 <= route.cruise_pressure_hpa <= 760


def test_bearing_between_east():
    """Bearing from a point east should be ~90 degrees."""
    wp_a = Waypoint(icao="A", name="A", lat=50.0, lon=0.0)
    wp_b = Waypoint(icao="B", name="B", lat=50.0, lon=5.0)
    brg = bearing_between(wp_a, wp_b)
    assert 85 < brg < 95


def test_bearing_between_south():
    """Bearing going south should be ~180 degrees."""
    wp_a = Waypoint(icao="A", name="A", lat=52.0, lon=0.0)
    wp_b = Waypoint(icao="B", name="B", lat=48.0, lon=0.0)
    brg = bearing_between(wp_a, wp_b)
    assert 175 < brg < 185


def test_waypoint_track_middle(sample_route):
    """Middle waypoint track is average of incoming and outgoing leg bearings."""
    track = sample_route.waypoint_track("LFPB")
    # EGTK->LFPB is roughly SE, LFPB->LSGS is roughly SE, so track ~130-160
    assert 100 < track < 200


def test_waypoint_track_origin(sample_route):
    """Origin waypoint track is the first leg bearing."""
    track = sample_route.waypoint_track("EGTK")
    leg_bearing = sample_route.leg_bearing(0)
    assert abs(track - leg_bearing) < 0.01


def test_waypoint_track_destination(sample_route):
    """Destination waypoint track is the last leg bearing."""
    track = sample_route.waypoint_track("LSGS")
    leg_bearing = sample_route.leg_bearing(len(sample_route.waypoints) - 2)
    assert abs(track - leg_bearing) < 0.01


def test_altitude_to_pressure():
    """Standard atmosphere conversion for known values."""
    # Sea level
    assert altitude_to_pressure_hpa(0) == 1013
    # ~18000 ft ≈ 500 hPa
    p = altitude_to_pressure_hpa(18000)
    assert 490 < p < 510


def test_hourly_level_at():
    """HourlyForecast.level_at finds correct pressure level."""
    levels = [
        PressureLevelData(pressure_hpa=850, temperature_c=5),
        PressureLevelData(pressure_hpa=700, temperature_c=-3),
    ]
    h = HourlyForecast(time=datetime(2026, 2, 21, 9, 0), pressure_levels=levels)

    assert h.level_at(850).temperature_c == 5
    assert h.level_at(700).temperature_c == -3
    assert h.level_at(500) is None


def test_waypoint_forecast_at_time(sample_waypoint):
    """WaypointForecast.at_time returns closest hour."""
    wf = WaypointForecast(
        waypoint=sample_waypoint,
        model=ModelSource.GFS,
        fetched_at=datetime.now(timezone.utc),
        hourly=[
            HourlyForecast(time=datetime(2026, 2, 21, 6, 0)),
            HourlyForecast(time=datetime(2026, 2, 21, 9, 0)),
            HourlyForecast(time=datetime(2026, 2, 21, 12, 0)),
        ],
    )

    result = wf.at_time(datetime(2026, 2, 21, 10, 0))
    assert result.time == datetime(2026, 2, 21, 9, 0)


def test_forecast_snapshot_roundtrip(sample_route):
    """ForecastSnapshot serializes and deserializes correctly."""
    snapshot = ForecastSnapshot(
        route=sample_route,
        target_date="2026-02-21",
        fetch_date="2026-02-14",
        days_out=7,
    )

    json_str = snapshot.model_dump_json()
    restored = ForecastSnapshot.model_validate_json(json_str)

    assert restored.target_date == "2026-02-21"
    assert restored.route.origin.icao == "EGTK"
    assert restored.days_out == 7
