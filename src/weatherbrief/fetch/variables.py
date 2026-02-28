"""Variable definitions and per-model availability for Open-Meteo API."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

BASE_PRESSURE_LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300]

# 25 levels — 25 hPa spacing below 500, 50 hPa above.
# Gives ~1000ft vertical resolution in the lower atmosphere (4x improvement).
# GFS and best_match support all of these plus upper-atmosphere levels.
EXTENDED_PRESSURE_LEVELS = [
    1000, 975, 950, 925, 900, 875, 850, 825, 800, 775,
    750, 725, 700, 675, 650, 625, 600, 575, 550, 525,
    500, 450, 400, 350, 300, 250, 200, 150,
]

# ECMWF IFS: 13 levels on Open-Meteo (verified Feb 2025).
# All pressure-level variables available including vertical_velocity.
ECMWF_PRESSURE_LEVELS = [
    1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150,
]

# DWD ICON: 12 levels (verified via API; 250/200/150 return null).
ICON_PRESSURE_LEVELS = [
    1000, 975, 950, 925, 900, 850, 800, 700, 600, 500, 400, 300,
]

# Météo-France ARPEGE: 19 levels (verified Feb 2025).
METEOFRANCE_PRESSURE_LEVELS = [
    1000, 950, 925, 900, 850, 800, 750, 700, 650, 600,
    550, 500, 450, 400, 350, 300, 250, 200, 150,
]

# UK Met Office: 20 levels including upper atmosphere (verified Feb 2025).
# Supports vertical_velocity at all levels.
UKMO_PRESSURE_LEVELS = [
    1000, 975, 950, 925, 900, 850, 800, 750, 700, 650,
    600, 550, 500, 450, 400, 350, 300, 250, 200, 150,
]

# Canadian GEM: 20 levels (verified Feb 2025).
GEM_PRESSURE_LEVELS = [
    1000, 950, 925, 900, 875, 850, 800, 750, 700, 650,
    600, 550, 500, 450, 400, 350, 300, 250, 200, 150,
]

# Backwards-compatible alias
PRESSURE_LEVELS = BASE_PRESSURE_LEVELS

SURFACE_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "dewpoint_2m",
    "surface_pressure",
    "pressure_msl",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "precipitation",
    "precipitation_probability",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "freezing_level_height",
    "cape",
    "convective_inhibition",
    "lifted_index",
    "visibility",
    "rain",
    "showers",
    "snowfall",
    "weather_code",
]

# What type of CAPE each model's surface "cape" variable represents.
# GFS/best_match: surface-based, ECMWF: most-unstable, ICON: mixed-layer.
NWP_CAPE_TYPE: dict[str, str] = {
    "gfs": "sb",
    "best_match": "sb",
    "ecmwf": "mu",
    "icon": "ml",
}

PRESSURE_LEVEL_VARIABLES = [
    "temperature",
    "relative_humidity",
    "dewpoint",
    "wind_speed",
    "wind_direction",
    "geopotential_height",
    "vertical_velocity",
]


class ModelRegion(Enum):
    """Geographic coverage region for a weather model."""

    GLOBAL = "global"
    NORTH_AMERICA = "north_america"
    EUROPE = "europe"


@dataclass
class ModelEndpoint:
    """Open-Meteo model endpoint configuration."""

    name: str
    base_url: str
    max_days: int
    # If set, passed as ?models= query param (for generic /v1/forecast endpoint)
    model_param: str | None = None
    unavailable_surface: list[str] = field(default_factory=list)
    unavailable_pressure: list[str] = field(default_factory=list)
    pressure_levels: list[int] = field(default_factory=lambda: list(BASE_PRESSURE_LEVELS))
    # Whether this model is included in the default selection for new users
    default: bool = False
    # Geographic coverage region — used to skip irrelevant models for a route
    region: ModelRegion = ModelRegion.GLOBAL


MODEL_ENDPOINTS: dict[str, ModelEndpoint] = {
    "best_match": ModelEndpoint(
        name="Best Match",
        base_url="https://api.open-meteo.com/v1/forecast",
        max_days=16,
        pressure_levels=list(EXTENDED_PRESSURE_LEVELS),
        default=True,
    ),
    "ecmwf": ModelEndpoint(
        name="ECMWF IFS",
        base_url="https://api.open-meteo.com/v1/ecmwf",
        max_days=10,
        unavailable_surface=["freezing_level_height", "visibility",
                             "lifted_index"],
        # vertical_velocity now available (verified Feb 2025)
        pressure_levels=list(ECMWF_PRESSURE_LEVELS),
        default=True,
    ),
    "gfs": ModelEndpoint(
        name="GFS",
        base_url="https://api.open-meteo.com/v1/gfs",
        max_days=16,
        pressure_levels=list(EXTENDED_PRESSURE_LEVELS),
        default=True,
    ),
    "icon": ModelEndpoint(
        name="DWD ICON",
        base_url="https://api.open-meteo.com/v1/dwd-icon",
        max_days=7,
        unavailable_surface=["convective_inhibition", "lifted_index"],
        unavailable_pressure=["vertical_velocity"],
        pressure_levels=list(ICON_PRESSURE_LEVELS),
        default=True,
        region=ModelRegion.EUROPE,
    ),
    "ukmo": ModelEndpoint(
        name="UK Met Office",
        base_url="https://api.open-meteo.com/v1/forecast",
        max_days=7,
        model_param="ukmo_seamless",
        unavailable_surface=["precipitation_probability",
                             "convective_inhibition", "lifted_index"],
        pressure_levels=list(UKMO_PRESSURE_LEVELS),
        region=ModelRegion.EUROPE,
    ),
    "meteofrance": ModelEndpoint(
        name="Météo-France",
        base_url="https://api.open-meteo.com/v1/meteofrance",
        max_days=6,
        unavailable_surface=["precipitation_probability",
                             "freezing_level_height", "visibility",
                             "convective_inhibition", "lifted_index"],
        unavailable_pressure=["vertical_velocity"],
        pressure_levels=list(METEOFRANCE_PRESSURE_LEVELS),
        region=ModelRegion.EUROPE,
    ),
    "gem": ModelEndpoint(
        name="GEM",
        base_url="https://api.open-meteo.com/v1/gem",
        max_days=10,
        unavailable_surface=["freezing_level_height", "visibility",
                             "convective_inhibition", "lifted_index"],
        unavailable_pressure=["vertical_velocity"],
        pressure_levels=list(GEM_PRESSURE_LEVELS),
        region=ModelRegion.NORTH_AMERICA,
    ),
}


def build_hourly_params(endpoint: ModelEndpoint) -> str:
    """Build the comma-separated hourly parameter string for a model endpoint."""
    # Surface variables (excluding unavailable)
    surface = [v for v in SURFACE_VARIABLES if v not in endpoint.unavailable_surface]

    # Pressure level variables (excluding unavailable)
    pressure = []
    for var in PRESSURE_LEVEL_VARIABLES:
        if var in endpoint.unavailable_pressure:
            continue
        for level in endpoint.pressure_levels:
            pressure.append(f"{var}_{level}hPa")

    return ",".join(surface + pressure)


# ICAO prefixes that indicate North American airspace
_NORTH_AMERICA_PREFIXES = ("K", "C", "P")


def detect_model_region(route) -> ModelRegion:
    """Classify a route's region for model coverage filtering.

    Checks ALL waypoint ICAO prefixes:
    - K, C, P prefixes → NORTH_AMERICA
    - Everything else → EUROPE

    If mixed, defaults to GLOBAL (no filtering — safe fallback).

    Takes a RouteConfig but declared untyped to avoid circular imports.
    """
    if not route or not route.waypoints:
        return ModelRegion.GLOBAL

    regions: set[ModelRegion] = set()
    for wp in route.waypoints:
        icao = wp.icao.upper()
        if any(icao.startswith(p) for p in _NORTH_AMERICA_PREFIXES):
            regions.add(ModelRegion.NORTH_AMERICA)
        else:
            regions.add(ModelRegion.EUROPE)

    if len(regions) == 1:
        return regions.pop()
    return ModelRegion.GLOBAL
