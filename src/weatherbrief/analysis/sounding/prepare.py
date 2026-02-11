"""Convert forecast data into pint-wrapped arrays for MetPy.

Pint arrays stay within the sounding subpackage — callers pass PressureLevelData
in and get plain-number Pydantic models back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from metpy.units import units

if TYPE_CHECKING:
    from pint import Quantity

    from weatherbrief.models import HourlyForecast, PressureLevelData

logger = logging.getLogger(__name__)

MIN_LEVELS = 3


@dataclass
class PreparedProfile:
    """Pint-wrapped sounding arrays ready for MetPy calculations."""

    pressure: Quantity  # hPa, descending (surface → altitude)
    temperature: Quantity  # degC
    dewpoint: Quantity  # degC
    wind_speed: Quantity | None  # knot
    wind_direction: Quantity | None  # degree
    height: Quantity | None  # meter (geopotential)
    # Surface observations (from HourlyForecast)
    surface_pressure: Quantity | None  # hPa
    surface_temperature: Quantity | None  # degC
    surface_dewpoint: Quantity | None  # degC


def _derive_dewpoint(temperature_c: float, relative_humidity_pct: float) -> float:
    """Derive dewpoint from temperature and RH using Magnus formula."""
    a, b = 17.27, 237.7
    gamma = (a * temperature_c) / (b + temperature_c) + np.log(relative_humidity_pct / 100.0)
    return (b * gamma) / (a - gamma)


def prepare_profile(
    levels: list[PressureLevelData],
    hourly: HourlyForecast | None = None,
) -> PreparedProfile | None:
    """Convert pressure level data to pint-wrapped arrays for MetPy.

    Filters out levels missing temperature, derives dewpoint from RH where
    needed, sorts by descending pressure. Returns None if fewer than
    MIN_LEVELS valid levels remain.
    """
    valid = []
    for lv in levels:
        if lv.temperature_c is None:
            continue
        # Derive dewpoint if not directly available
        dp = lv.dewpoint_c
        if dp is None and lv.relative_humidity_pct is not None:
            dp = _derive_dewpoint(lv.temperature_c, lv.relative_humidity_pct)
        if dp is None:
            continue
        valid.append((lv, dp))

    if len(valid) < MIN_LEVELS:
        logger.debug("Only %d valid levels, need %d", len(valid), MIN_LEVELS)
        return None

    # Sort by descending pressure (surface first)
    valid.sort(key=lambda pair: pair[0].pressure_hpa, reverse=True)

    pressure = np.array([lv.pressure_hpa for lv, _ in valid]) * units.hPa
    temperature = np.array([lv.temperature_c for lv, _ in valid]) * units.degC
    dewpoint = np.array([dp for _, dp in valid]) * units.degC

    # Wind — only if all valid levels have it
    has_wind = all(
        lv.wind_speed_kt is not None and lv.wind_direction_deg is not None
        for lv, _ in valid
    )
    wind_speed = None
    wind_direction = None
    if has_wind:
        wind_speed = np.array([lv.wind_speed_kt for lv, _ in valid]) * units.knot
        wind_direction = np.array([lv.wind_direction_deg for lv, _ in valid]) * units.degree

    # Height — only if all valid levels have geopotential
    has_height = all(lv.geopotential_height_m is not None for lv, _ in valid)
    height = None
    if has_height:
        height = np.array([lv.geopotential_height_m for lv, _ in valid]) * units.meter

    # Surface values from HourlyForecast
    sfc_pressure = None
    sfc_temperature = None
    sfc_dewpoint = None
    if hourly is not None:
        if hourly.surface_pressure_hpa is not None:
            sfc_pressure = hourly.surface_pressure_hpa * units.hPa
        if hourly.temperature_2m_c is not None:
            sfc_temperature = hourly.temperature_2m_c * units.degC
        if hourly.dewpoint_2m_c is not None:
            sfc_dewpoint = hourly.dewpoint_2m_c * units.degC

    return PreparedProfile(
        pressure=pressure,
        temperature=temperature,
        dewpoint=dewpoint,
        wind_speed=wind_speed,
        wind_direction=wind_direction,
        height=height,
        surface_pressure=sfc_pressure,
        surface_temperature=sfc_temperature,
        surface_dewpoint=sfc_dewpoint,
    )
