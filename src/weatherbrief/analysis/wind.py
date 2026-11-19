"""Headwind/tailwind and crosswind computation."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from weatherbrief.models import WindComponent
from weatherbrief.models.analysis import (
    HourlyForecast,
    PressureLevelData,
    bearing_between_coords,
    pressure_hpa_to_altitude_m,
)

if TYPE_CHECKING:
    from weatherbrief.models.analysis import RoutePoint


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


def compute_route_tracks(route_points: list["RoutePoint"]) -> list[float]:
    """Compute bearing at each route point using neighbor points."""
    n = len(route_points)
    tracks: list[float] = []
    for i in range(n):
        if n == 1:
            tracks.append(0.0)
        elif i == 0:
            tracks.append(bearing_between_coords(
                route_points[0].lat, route_points[0].lon,
                route_points[1].lat, route_points[1].lon,
            ))
        elif i == n - 1:
            tracks.append(bearing_between_coords(
                route_points[-2].lat, route_points[-2].lon,
                route_points[-1].lat, route_points[-1].lon,
            ))
        else:
            # Circular mean of incoming and outgoing bearings
            b1 = bearing_between_coords(
                route_points[i - 1].lat, route_points[i - 1].lon,
                route_points[i].lat, route_points[i].lon,
            )
            b2 = bearing_between_coords(
                route_points[i].lat, route_points[i].lon,
                route_points[i + 1].lat, route_points[i + 1].lon,
            )
            x = math.cos(math.radians(b1)) + math.cos(math.radians(b2))
            y = math.sin(math.radians(b1)) + math.sin(math.radians(b2))
            tracks.append(math.degrees(math.atan2(y, x)) % 360)
    return tracks


# --- pooled headwind along the route (#1007) ---------------------------------

#: Altitude spacing of the flight path's headwind grid. Model winds come on
#: pressure levels 25-100 hPa apart (~700-3,000 ft at piston levels), so a
#: finer grid would only interpolate between them.
HEADWIND_GRID_STEP_FT = 1000.0


def route_headwind_grid(
    cross_sections,
    samples: Sequence[tuple[int, float, float, datetime]],
    top_ft: float,
    *,
    models: Sequence[str] | None = None,
):
    """The briefing's pooled headwind along the route, or None without winds.

    ``samples`` are ``(point_index, distance_nm, track_deg, time)`` in route
    order: the cross-section index to read, where it is, the route track
    there, and when the aircraft is planned there. For every sample and every
    ``HEADWIND_GRID_STEP_FT`` from sea level to ``top_ft``, each model's wind
    (``pick_wind_at_pressure``: u/v interpolated between levels, ``None``
    beyond the 2,000 ft snap) is projected on the track
    (``compute_wind_components``) and the models' **median** is kept (the
    mean of the middle pair for an even count)
    (meteorology-decisions §87). A cell no model has a wind for reads 0
    (still air). ``models`` restricts the pool (default every cross-section).

    Returns ``None`` when no cell has any model wind (a pack without
    cross-section winds keeps the still-air path).
    """
    from weatherbrief.models import altitude_to_pressure_hpa
    from weatherbrief.models.flight_path import HeadwindGrid

    if not cross_sections or not samples:
        return None
    wanted = set(models) if models is not None else None
    sections = [cs for cs in cross_sections if wanted is None or cs.model.value in wanted]
    if not sections:
        return None
    n_alt = max(int(math.ceil(max(top_ft, 0.0) / HEADWIND_GRID_STEP_FT)), 1) + 1
    altitudes = [i * HEADWIND_GRID_STEP_FT for i in range(n_alt)]
    pressures = [altitude_to_pressure_hpa(int(a)) for a in altitudes]

    rows: list[list[float]] = []
    distances: list[float] = []
    any_wind = False
    for point_index, distance_nm, track_deg, when in samples:
        hourlies = []
        for cs in sections:
            if 0 <= point_index < len(cs.point_forecasts):
                hourly = cs.point_forecasts[point_index].at_time(when)
                if hourly is not None:
                    hourlies.append(hourly)
        row: list[float] = []
        for pressure in pressures:
            values = []
            for hourly in hourlies:
                lv = pick_wind_at_pressure(hourly, pressure)
                if lv is None or lv.wind_speed_kt is None or lv.wind_direction_deg is None:
                    continue
                values.append(
                    compute_wind_components(lv.wind_speed_kt, lv.wind_direction_deg, track_deg).headwind_kt
                )
            if values:
                any_wind = True
                row.append(round(statistics.median(values), 1))
            else:
                row.append(0.0)
        distances.append(float(distance_nm))
        rows.append(row)
    if not any_wind:
        return None
    return HeadwindGrid(
        distances_nm=distances, altitudes_ft=altitudes, headwind_kt=rows,
        models=[cs.model.value for cs in sections],
    )
