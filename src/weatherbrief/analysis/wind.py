"""Headwind/tailwind and crosswind computation."""

from __future__ import annotations

import math
from typing import Optional

from weatherbrief.models import WindComponent
from weatherbrief.models.analysis import (
    HourlyForecast,
    PressureLevelData,
    pressure_hpa_to_altitude_m,
)


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


#: When the target is NOT bracketed by two levels with valid wind (above the
#: top / below the bottom of the delivered column, or one side missing), the
#: nearest level is used only if it lies within this ISA pressure-altitude
#: distance. 2,000 ft is one cruise-level step: at ordinary shears (2–5 kt per
#: 1,000 ft) a 2,000 ft offset is a ~5–10 kt error, comparable to model spread;
#: farther than that the value describes a different altitude and is withheld.
MAX_WIND_SNAP_FT = 2000.0

_M_TO_FT = 1.0 / 0.3048


def _isa_alt_ft(pressure_hpa: float) -> float:
    return pressure_hpa_to_altitude_m(pressure_hpa) * _M_TO_FT


def _to_uv(speed_kt: float, direction_deg: float) -> tuple[float, float]:
    """Meteorological (from-direction) wind → (u, v) components."""
    rad = math.radians(direction_deg)
    return -speed_kt * math.sin(rad), -speed_kt * math.cos(rad)


def _from_uv(u: float, v: float) -> tuple[float, float]:
    """(u, v) → (speed_kt, from-direction °, in [0, 360))."""
    speed = math.hypot(u, v)
    if speed < 1e-9:
        return 0.0, 0.0
    return speed, math.degrees(math.atan2(-u, -v)) % 360.0


def pick_wind_at_pressure(
    hourly: HourlyForecast,
    target_pressure_hpa: float,
    max_snap_ft: float = MAX_WIND_SNAP_FT,
) -> Optional[PressureLevelData]:
    """Wind at *target_pressure_hpa* from a model's vertical sounding.

    Used to pick a single representative wind at a planned cruise altitude
    (or an override altitude). Returns a ``PressureLevelData`` carrying only
    ``pressure_hpa`` / ``wind_speed_kt`` / ``wind_direction_deg``:

    - **Bracketed** (a valid-wind level on each side): u/v components are
      interpolated linearly in ISA pressure altitude between the two nearest
      bracketing levels — the same altitude scale the target came from
      (``altitude_to_pressure_hpa``). Vector, not speed/direction, so a veer
      across north does not average to south.
    - **Exact level**: that level's wind.
    - **Not bracketed**: the nearest valid level, only if within
      ``max_snap_ft``; otherwise ``None`` (wind unavailable at this altitude).

    Before #807 this snapped to the nearest level with no distance guard:
    ECMWF's Open-Meteo table has nothing between 850 and 700 hPa, so an
    8,000 ft cruise read the ~9,900 ft wind while GFS read 750 hPa — a level-
    table artefact masquerading as model divergence (review 2026-08 §3.8).
    """
    valid = [
        lv for lv in hourly.pressure_levels
        if lv.wind_speed_kt is not None and lv.wind_direction_deg is not None
    ]
    if not valid:
        return None

    target = float(target_pressure_hpa)
    # Higher pressure = lower altitude.
    below = [lv for lv in valid if lv.pressure_hpa >= target]   # at/under target
    above = [lv for lv in valid if lv.pressure_hpa <= target]   # at/over target
    lo = min(below, key=lambda lv: lv.pressure_hpa) if below else None
    hi = max(above, key=lambda lv: lv.pressure_hpa) if above else None

    def _result(speed: float, direction: float) -> PressureLevelData:
        return PressureLevelData(
            pressure_hpa=int(round(target)),
            wind_speed_kt=speed,
            wind_direction_deg=direction,
        )

    if lo is not None and hi is not None:
        if lo.pressure_hpa == hi.pressure_hpa:  # exact level
            return _result(lo.wind_speed_kt, lo.wind_direction_deg)
        z_lo, z_hi = _isa_alt_ft(lo.pressure_hpa), _isa_alt_ft(hi.pressure_hpa)
        frac = (_isa_alt_ft(target) - z_lo) / (z_hi - z_lo) if z_hi != z_lo else 0.0
        u_lo, v_lo = _to_uv(lo.wind_speed_kt, lo.wind_direction_deg)
        u_hi, v_hi = _to_uv(hi.wind_speed_kt, hi.wind_direction_deg)
        speed, direction = _from_uv(
            u_lo + frac * (u_hi - u_lo), v_lo + frac * (v_hi - v_lo),
        )
        return _result(speed, direction)

    nearest = lo if lo is not None else hi
    if abs(_isa_alt_ft(nearest.pressure_hpa) - _isa_alt_ft(target)) > max_snap_ft:
        return None
    return _result(nearest.wind_speed_kt, nearest.wind_direction_deg)
