"""Variable definitions and per-model availability for Open-Meteo API."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

BASE_PRESSURE_LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300]

# 25 levels — 25 hPa spacing below 500, 50 hPa above.
# Gives ~1000ft vertical resolution in the lower atmosphere (4x improvement).
# GFS supports all of these plus upper-atmosphere levels.
EXTENDED_PRESSURE_LEVELS = [
    1000, 975, 950, 925, 900, 875, 850, 825, 800, 775,
    750, 725, 700, 675, 650, 625, 600, 575, 550, 525,
    500, 450, 400, 350, 300, 250, 200, 150,
]

# ECMWF IFS: 13 levels on Open-Meteo (verified Mar 2026).
# All pressure-level variables available including vertical_velocity.
# No intermediate levels (975/950/900/800/750 etc.) — Open-Meteo limitation.
ECMWF_PRESSURE_LEVELS = [
    1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50,
]

# DWD ICON: 19 levels on Open-Meteo (verified Mar 2026).
# Still no intermediate levels between 800–300 hPa from API.
# GRIB enrichment interpolates to EXTENDED_PRESSURE_LEVELS for CLW/ICMR.
ICON_PRESSURE_LEVELS = [
    1000, 975, 950, 925, 900, 850, 800, 700, 600, 500, 400, 300,
    250, 200, 150, 100, 70, 50, 30,
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
]

# What type of CAPE each model's surface "cape" variable represents.
# GFS: surface-based, ECMWF: most-unstable, ICON: mixed-layer.
NWP_CAPE_TYPE: dict[str, str] = {
    "gfs": "sb",
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
    # If set, route must contain at least one airport whose ICAO starts with
    # one of these prefixes (e.g. ["LF"] for France, ["EG"] for UK).
    required_icao_prefixes: list[str] | None = None


MODEL_ENDPOINTS: dict[str, ModelEndpoint] = {
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
        unavailable_surface=["precipitation_probability", "lifted_index"],
        pressure_levels=list(UKMO_PRESSURE_LEVELS),
        region=ModelRegion.EUROPE,
        required_icao_prefixes=["EG"],
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
        required_icao_prefixes=["LF"],
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


# Global models that must BOTH be present for a flight to be bookable. The
# booking horizon is the last lead day on which both still deliver: Open-Meteo
# drops a model once ``days_out >= max_days``, so the last available lead day is
# ``max_days - 1``.
DUAL_MODEL_KEYS = ("ecmwf", "gfs")


def dual_model_horizon_days() -> int:
    """Max ``days_out`` at which both ECMWF and GFS (Open-Meteo) are available.

    Beyond this only a single global model remains, so a flight that far out
    cannot get a meaningful two-model outlook and is not bookable. Derived from
    ``MODEL_ENDPOINTS`` so it tracks any future horizon change (currently 9:
    ECMWF ``max_days=10`` → available for ``days_out`` 0..9).
    """
    return min(MODEL_ENDPOINTS[k].max_days for k in DUAL_MODEL_KEYS) - 1


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


def _is_icao_airport(code: str) -> bool:
    """True if ``code`` looks like a 4-letter ICAO *airport* identifier.

    Country-prefix matching must only count real airports. Named navigation
    fixes (5 letters, e.g. KONAN, CINDY) and navaids (2-3 letters) can begin
    with a country prefix purely by coincidence and would otherwise produce
    false positives.
    """
    return len(code) == 4 and code.isalpha()


def route_covers_prefixes(route, prefixes: list[str]) -> bool:
    """Return True if at least one *airport* on the route has an ICAO code
    starting with any of the given prefixes.

    Only 4-letter ICAO airport codes are considered (see ``_is_icao_airport``);
    intermediate navigation fixes are ignored. Airports are matched anywhere on
    the route (not just origin/destination) so an intermediate fuel stop in,
    e.g., France still pulls in Météo-France.
    """
    if not route or not route.waypoints:
        return False
    pfx = tuple(prefixes)
    return any(
        _is_icao_airport(icao := wp.icao.upper()) and icao.startswith(pfx)
        for wp in route.waypoints
    )


def detect_model_region(route) -> ModelRegion:
    """Classify a route's region for model coverage filtering.

    Only the origin and destination airports are considered — checking the
    first letter of their ICAO codes:
    - K, C, P prefixes → NORTH_AMERICA
    - Everything else → EUROPE

    Intermediate waypoints are deliberately ignored: they are often named
    navigation fixes (e.g. KONAN, CINDY) whose names start with a
    North-American ICAO prefix and would otherwise misclassify a European
    route as NORTH_AMERICA.

    If origin and destination disagree, defaults to GLOBAL (no filtering —
    safe fallback).

    Takes a RouteConfig but declared untyped to avoid circular imports.
    """
    if not route or not route.waypoints:
        return ModelRegion.GLOBAL

    regions: set[ModelRegion] = set()
    for wp in (route.waypoints[0], route.waypoints[-1]):
        icao = wp.icao.upper()
        if any(icao.startswith(p) for p in _NORTH_AMERICA_PREFIXES):
            regions.add(ModelRegion.NORTH_AMERICA)
        else:
            regions.add(ModelRegion.EUROPE)

    if len(regions) == 1:
        return regions.pop()
    return ModelRegion.GLOBAL
