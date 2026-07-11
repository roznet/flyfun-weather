"""Headwind/tailwind and crosswind computation."""

from __future__ import annotations

import math
from typing import Optional

from weatherbrief.models import WindComponent
from weatherbrief.models.analysis import HourlyForecast, PressureLevelData


def compute_wind_components(
    wind_speed_kt: float, wind_direction_deg: float, track_deg: float
) -> WindComponent:
    """Compute headwind/tailwind and crosswind components.

    headwind: positive = headwind, negative = tailwind
    crosswind: positive = from right, negative = from left
    """
    relative_wind = math.radians(wind_direction_deg - track_deg)
    headwind = wind_speed_kt * math.cos(relative_wind)
    crosswind = wind_speed_kt * math.sin(relative_wind)

    return WindComponent(
        wind_speed_kt=wind_speed_kt,
        wind_direction_deg=wind_direction_deg,
        track_deg=track_deg,
        headwind_kt=round(headwind, 1),
        crosswind_kt=round(crosswind, 1),
    )


def pick_wind_at_pressure(
    hourly: HourlyForecast, target_pressure_hpa: float,
) -> Optional[PressureLevelData]:
    """Return the pressure level with valid wind closest to *target_pressure_hpa*.

    Used to pick a single representative wind at a planned cruise altitude
    (or an override altitude) from a model's vertical sounding.
    """
    chosen: Optional[PressureLevelData] = None
    for level in hourly.pressure_levels:
        if level.wind_speed_kt is None or level.wind_direction_deg is None:
            continue
        if chosen is None or abs(level.pressure_hpa - target_pressure_hpa) < abs(
            chosen.pressure_hpa - target_pressure_hpa
        ):
            chosen = level
    return chosen


def pick_wind_speed_at_pressure(
    hourly: HourlyForecast,
    target_pressure_hpa: float,
) -> Optional[PressureLevelData]:
    """Return the nearest pressure level carrying a valid wind speed.

    Direction is deliberately not required. This selector is for scalar-speed
    consumers such as mountain-wind grading, where the route profile cannot
    establish a cross-ridge component and direction must not gate availability.
    """
    chosen: Optional[PressureLevelData] = None
    for level in hourly.pressure_levels:
        if level.wind_speed_kt is None:
            continue
        if chosen is None or abs(level.pressure_hpa - target_pressure_hpa) < abs(
            chosen.pressure_hpa - target_pressure_hpa
        ):
            chosen = level
    return chosen
