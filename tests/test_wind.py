"""Tests for wind component analysis."""

import math
from datetime import datetime

from weatherbrief.analysis.wind import (
    compute_wind_components,
    pick_wind_speed_at_pressure,
)
from weatherbrief.models import HourlyForecast, PressureLevelData


def test_pure_headwind():
    """Wind directly on the nose."""
    wc = compute_wind_components(20, 155, 155)
    assert wc.headwind_kt == 20.0
    assert abs(wc.crosswind_kt) < 0.1


def test_pure_tailwind():
    """Wind directly from behind."""
    wc = compute_wind_components(20, 335, 155)
    assert wc.headwind_kt == -20.0
    assert abs(wc.crosswind_kt) < 0.1


def test_pure_crosswind_from_right():
    """Wind directly from the right (90 degrees clockwise from track)."""
    wc = compute_wind_components(20, 245, 155)
    assert abs(wc.headwind_kt) < 0.1
    assert wc.crosswind_kt == 20.0


def test_pure_crosswind_from_left():
    """Wind directly from the left (90 degrees counterclockwise from track)."""
    wc = compute_wind_components(20, 65, 155)
    assert abs(wc.headwind_kt) < 0.1
    assert wc.crosswind_kt == -20.0


def test_quartering_headwind():
    """45-degree quartering headwind: both components should be ~14kt for 20kt wind."""
    wc = compute_wind_components(20, 200, 155)
    expected = 20 * math.cos(math.radians(45))
    assert abs(wc.headwind_kt - expected) < 0.2
    assert abs(wc.crosswind_kt - expected) < 0.2


def test_zero_wind():
    """Zero wind should produce zero components."""
    wc = compute_wind_components(0, 0, 155)
    assert wc.headwind_kt == 0
    assert wc.crosswind_kt == 0


def test_calm_wind_direction():
    """Track direction in the result matches input."""
    wc = compute_wind_components(10, 270, 180)
    assert wc.track_deg == 180
    assert wc.wind_direction_deg == 270
    assert wc.wind_speed_kt == 10


def test_pick_wind_speed_at_pressure_accepts_missing_direction():
    hourly = HourlyForecast(
        time=datetime(2026, 3, 1, 12, 0),
        pressure_levels=[
            PressureLevelData(
                pressure_hpa=800,
                wind_speed_kt=45,
                wind_direction_deg=None,
            ),
            PressureLevelData(
                pressure_hpa=600,
                wind_speed_kt=10,
                wind_direction_deg=270,
            ),
        ],
    )

    chosen = pick_wind_speed_at_pressure(hourly, 790)

    assert chosen is not None
    assert chosen.pressure_hpa == 800
    assert chosen.wind_speed_kt == 45
    assert chosen.wind_direction_deg is None
