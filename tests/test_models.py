"""Tests for Pydantic models."""

from datetime import datetime, timezone

from weatherbrief.models import (
    ForecastSnapshot,
    HourlyForecast,
    ModelSource,
    PressureLevelData,
    WaypointForecast,
)


def test_route_waypoints(sample_route):
    """Route.waypoints returns all waypoints in order."""
    wps = sample_route.waypoints
    assert len(wps) == 3
    assert wps[0].icao == "EGTK"
    assert wps[1].icao == "LFPB"
    assert wps[2].icao == "LSGS"


def test_route_waypoints_no_midpoint(sample_waypoint):
    """Route without midpoint returns only origin and destination."""
    from weatherbrief.models import RouteConfig, Waypoint

    route = RouteConfig(
        name="Test",
        origin=sample_waypoint,
        destination=Waypoint(icao="LSGS", name="Sion", lat=46.2, lon=7.3),
        cruise_altitude_ft=8000,
        cruise_pressure_hpa=750,
        track_deg=155,
    )
    assert len(route.waypoints) == 2


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
