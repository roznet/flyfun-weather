"""Shared test fixtures."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from weatherbrief.db.engine import DEV_USER_ID
from weatherbrief.db.models import Base, UserPreferencesRow, UserRow
from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    PressureLevelData,
    RouteConfig,
    Waypoint,
    WaypointForecast,
)


@pytest.fixture
def db_engine():
    """In-memory SQLite engine for tests."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Yield a SQLAlchemy session per test, rolled back after."""
    session = sessionmaker(bind=db_engine)()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def dev_user(db_session):
    """Insert a dev user and return the user_id."""
    user = UserRow(
        id=DEV_USER_ID,
        provider="local",
        provider_sub="dev",
        email="dev@localhost",
        display_name="Dev User",
        approved=True,
    )
    db_session.add(user)
    db_session.add(UserPreferencesRow(user_id=DEV_USER_ID))
    db_session.flush()
    return DEV_USER_ID


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


@pytest.fixture
def sample_pressure_levels_with_omega():
    """Pressure levels with vertical velocity data (GFS/ECMWF-like)."""
    return [
        PressureLevelData(pressure_hpa=1000, temperature_c=10, relative_humidity_pct=75,
                          dewpoint_c=5.5, wind_speed_kt=8, wind_direction_deg=270,
                          geopotential_height_m=110, vertical_velocity_pa_s=-0.2),
        PressureLevelData(pressure_hpa=925, temperature_c=5, relative_humidity_pct=85,
                          dewpoint_c=2.7, wind_speed_kt=15, wind_direction_deg=280,
                          geopotential_height_m=770, vertical_velocity_pa_s=-0.5),
        PressureLevelData(pressure_hpa=850, temperature_c=0, relative_humidity_pct=90,
                          dewpoint_c=-1.5, wind_speed_kt=25, wind_direction_deg=290,
                          geopotential_height_m=1450, vertical_velocity_pa_s=-1.0),
        PressureLevelData(pressure_hpa=700, temperature_c=-8, relative_humidity_pct=60,
                          dewpoint_c=-15, wind_speed_kt=35, wind_direction_deg=300,
                          geopotential_height_m=3010, vertical_velocity_pa_s=-2.0),
        PressureLevelData(pressure_hpa=600, temperature_c=-18, relative_humidity_pct=40,
                          dewpoint_c=-29, wind_speed_kt=40, wind_direction_deg=290,
                          geopotential_height_m=4200, vertical_velocity_pa_s=-1.5),
        PressureLevelData(pressure_hpa=500, temperature_c=-28, relative_humidity_pct=30,
                          dewpoint_c=-40, wind_speed_kt=50, wind_direction_deg=280,
                          geopotential_height_m=5550, vertical_velocity_pa_s=-1.0),
        PressureLevelData(pressure_hpa=400, temperature_c=-40, relative_humidity_pct=25,
                          dewpoint_c=-52, wind_speed_kt=55, wind_direction_deg=275,
                          geopotential_height_m=7150, vertical_velocity_pa_s=-0.3),
        PressureLevelData(pressure_hpa=300, temperature_c=-52, relative_humidity_pct=20,
                          dewpoint_c=-65, wind_speed_kt=60, wind_direction_deg=270,
                          geopotential_height_m=9100, vertical_velocity_pa_s=-0.1),
    ]
