"""Shared test fixtures."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    PressureLevelData,
    RouteConfig,
    Waypoint,
    WaypointForecast,
)


@pytest.fixture
def sample_waypoint():
    return Waypoint(icao="EGTK", name="Oxford Kidlington", lat=51.8361, lon=-1.32)


@pytest.fixture
def sample_route():
    return RouteConfig(
        name="Oxford to Sion",
        waypoints=[
            Waypoint(icao="EGTK", name="Oxford Kidlington", lat=51.8361, lon=-1.32),
            Waypoint(icao="LFPB", name="Paris Le Bourget", lat=48.9694, lon=2.4414),
            Waypoint(icao="LSGS", name="Sion", lat=46.2192, lon=7.3267),
        ],
        cruise_altitude_ft=8000,
        flight_duration_hours=4.5,
    )


@pytest.fixture
def sample_pressure_levels():
    """Realistic pressure level data for testing."""
    return [
        PressureLevelData(pressure_hpa=1000, temperature_c=10, relative_humidity_pct=75,
                          dewpoint_c=5.5, wind_speed_kt=8, wind_direction_deg=270,
                          geopotential_height_m=110),
        PressureLevelData(pressure_hpa=925, temperature_c=5, relative_humidity_pct=85,
                          dewpoint_c=2.7, wind_speed_kt=15, wind_direction_deg=280,
                          geopotential_height_m=770),
        PressureLevelData(pressure_hpa=850, temperature_c=0, relative_humidity_pct=90,
                          dewpoint_c=-1.5, wind_speed_kt=25, wind_direction_deg=290,
                          geopotential_height_m=1450),
        PressureLevelData(pressure_hpa=700, temperature_c=-8, relative_humidity_pct=60,
                          dewpoint_c=-15, wind_speed_kt=35, wind_direction_deg=300,
                          geopotential_height_m=3010),
        PressureLevelData(pressure_hpa=600, temperature_c=-18, relative_humidity_pct=40,
                          dewpoint_c=-29, wind_speed_kt=40, wind_direction_deg=290,
                          geopotential_height_m=4200),
        PressureLevelData(pressure_hpa=500, temperature_c=-28, relative_humidity_pct=30,
                          dewpoint_c=-40, wind_speed_kt=50, wind_direction_deg=280,
                          geopotential_height_m=5550),
        PressureLevelData(pressure_hpa=400, temperature_c=-40, relative_humidity_pct=25,
                          dewpoint_c=-52, wind_speed_kt=55, wind_direction_deg=275,
                          geopotential_height_m=7150),
        PressureLevelData(pressure_hpa=300, temperature_c=-52, relative_humidity_pct=20,
                          dewpoint_c=-65, wind_speed_kt=60, wind_direction_deg=270,
                          geopotential_height_m=9100),
    ]
