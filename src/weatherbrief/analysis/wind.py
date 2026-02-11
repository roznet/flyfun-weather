"""Headwind/tailwind and crosswind computation."""

from __future__ import annotations

import math

from weatherbrief.models import WindComponent


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
