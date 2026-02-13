"""Tests for Skew-T diagram generation."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from weatherbrief.digest.skewt import generate_skewt
from weatherbrief.models import HourlyForecast, PressureLevelData


@pytest.fixture
def skewt_forecast():
    """Forecast with realistic pressure level data for Skew-T."""
    levels = [
        PressureLevelData(
            pressure_hpa=1000, temperature_c=15, dewpoint_c=10,
            relative_humidity_pct=72, wind_speed_kt=5, wind_direction_deg=180,
            geopotential_height_m=110,
        ),
        PressureLevelData(
            pressure_hpa=925, temperature_c=10, dewpoint_c=6,
            relative_humidity_pct=75, wind_speed_kt=10, wind_direction_deg=200,
            geopotential_height_m=770,
        ),
        PressureLevelData(
            pressure_hpa=850, temperature_c=4, dewpoint_c=0,
            relative_humidity_pct=70, wind_speed_kt=20, wind_direction_deg=240,
            geopotential_height_m=1450,
        ),
        PressureLevelData(
            pressure_hpa=700, temperature_c=-5, dewpoint_c=-12,
            relative_humidity_pct=50, wind_speed_kt=30, wind_direction_deg=270,
            geopotential_height_m=3010,
        ),
        PressureLevelData(
            pressure_hpa=500, temperature_c=-22, dewpoint_c=-35,
            relative_humidity_pct=30, wind_speed_kt=45, wind_direction_deg=280,
            geopotential_height_m=5550,
        ),
        PressureLevelData(
            pressure_hpa=300, temperature_c=-45, dewpoint_c=-60,
            relative_humidity_pct=15, wind_speed_kt=60, wind_direction_deg=275,
            geopotential_height_m=9100,
        ),
    ]
    return HourlyForecast(
        time=datetime(2026, 2, 14, 9, 0),
        pressure_levels=levels,
    )


def test_generate_skewt_creates_png(skewt_forecast, tmp_path):
    """generate_skewt creates a valid PNG file."""
    out_path = tmp_path / "test_skewt.png"

    result = generate_skewt(skewt_forecast, "EGTK", "gfs", out_path)

    assert result == out_path
    assert out_path.exists()
    assert out_path.stat().st_size > 1000  # should be a real image

    # Check PNG magic bytes
    with open(out_path, "rb") as f:
        header = f.read(8)
    assert header[:4] == b"\x89PNG"


def test_generate_skewt_insufficient_levels(tmp_path):
    """Raises ValueError with fewer than 3 levels."""
    levels = [
        PressureLevelData(pressure_hpa=850, temperature_c=5),
        PressureLevelData(pressure_hpa=700, temperature_c=-3),
    ]
    forecast = HourlyForecast(
        time=datetime(2026, 2, 14, 9, 0),
        pressure_levels=levels,
    )
    with pytest.raises(ValueError, match="at least 3 levels"):
        generate_skewt(forecast, "EGTK", "gfs", tmp_path / "fail.png")


def test_generate_skewt_no_dewpoint(tmp_path):
    """Works without dewpoint data (no parcel profile)."""
    levels = [
        PressureLevelData(pressure_hpa=1000, temperature_c=15, geopotential_height_m=110),
        PressureLevelData(pressure_hpa=850, temperature_c=4, geopotential_height_m=1450),
        PressureLevelData(pressure_hpa=700, temperature_c=-5, geopotential_height_m=3010),
        PressureLevelData(pressure_hpa=500, temperature_c=-22, geopotential_height_m=5550),
    ]
    forecast = HourlyForecast(
        time=datetime(2026, 2, 14, 9, 0),
        pressure_levels=levels,
    )
    out_path = tmp_path / "no_dp.png"

    result = generate_skewt(forecast, "EGTK", "gfs", out_path)
    assert out_path.exists()
