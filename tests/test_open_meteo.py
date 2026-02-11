"""Tests for Open-Meteo client with mocked HTTP."""

from __future__ import annotations

from datetime import datetime, timezone

import responses

from weatherbrief.fetch.open_meteo import OpenMeteoClient, magnus_dewpoint
from weatherbrief.models import ModelSource, Waypoint


def test_magnus_dewpoint_typical():
    """Magnus formula gives reasonable dewpoint for typical conditions."""
    # At 20C and 50% RH, dewpoint should be ~9.3C
    dp = magnus_dewpoint(20, 50)
    assert 8.5 < dp < 10.5


def test_magnus_dewpoint_saturated():
    """At 100% RH, dewpoint equals temperature."""
    dp = magnus_dewpoint(15, 100)
    assert abs(dp - 15) < 0.1


def test_magnus_dewpoint_very_dry():
    """Very low RH gives very negative dewpoint."""
    dp = magnus_dewpoint(20, 5)
    assert dp < -10


@responses.activate
def test_fetch_forecast_parses_response():
    """Client correctly parses a minimal Open-Meteo response."""
    api_response = {
        "hourly": {
            "time": ["2026-02-21T09:00", "2026-02-21T10:00"],
            "temperature_2m": [5.0, 6.0],
            "relative_humidity_2m": [80, 75],
            "dewpoint_2m": [2.0, 2.5],
            "surface_pressure": [1013, 1013],
            "pressure_msl": [1015, 1015],
            "wind_speed_10m": [12.0, 14.0],
            "wind_direction_10m": [270, 280],
            "wind_gusts_10m": [20.0, 22.0],
            "precipitation": [0.0, 0.1],
            "precipitation_probability": [10, 20],
            "cloud_cover": [50, 60],
            "cloud_cover_low": [20, 30],
            "cloud_cover_mid": [30, 40],
            "cloud_cover_high": [10, 10],
            "freezing_level_height": [2000, 2100],
            "cape": [50, 60],
            "visibility": [10000, 9000],
            "temperature_850hPa": [0.0, 0.5],
            "relative_humidity_850hPa": [85, 80],
            "dewpoint_850hPa": [-1.5, -1.0],
            "wind_speed_850hPa": [25, 28],
            "wind_direction_850hPa": [290, 285],
            "geopotential_height_850hPa": [1450, 1455],
        }
    }

    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/gfs",
        json=api_response,
        status=200,
    )

    client = OpenMeteoClient()
    wp = Waypoint(icao="EGTK", name="Oxford", lat=51.836, lon=-1.32)
    result = client.fetch_forecast(wp, ModelSource.GFS)

    assert result.waypoint.icao == "EGTK"
    assert result.model == ModelSource.GFS
    assert len(result.hourly) == 2

    h = result.hourly[0]
    assert h.temperature_2m_c == 5.0
    assert h.wind_speed_10m_kt == 12.0

    # Check pressure level parsing
    level_850 = h.level_at(850)
    assert level_850 is not None
    assert level_850.temperature_c == 0.0
    assert level_850.wind_speed_kt == 25


@responses.activate
def test_fetch_all_models_continues_on_failure():
    """fetch_all_models continues when one model fails."""
    api_response = {
        "hourly": {
            "time": ["2026-02-21T09:00"],
            "temperature_2m": [5.0],
        }
    }

    # GFS succeeds
    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/gfs",
        json=api_response,
        status=200,
    )
    # ECMWF fails
    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/ecmwf",
        json={"error": "server error"},
        status=500,
    )

    client = OpenMeteoClient()
    wp = Waypoint(icao="EGTK", name="Oxford", lat=51.836, lon=-1.32)
    results = client.fetch_all_models(wp, [ModelSource.GFS, ModelSource.ECMWF])

    assert len(results) == 1
    assert results[0].model == ModelSource.GFS
