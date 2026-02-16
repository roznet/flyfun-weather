"""Variable definitions and per-model availability for Open-Meteo API."""

from __future__ import annotations

from dataclasses import dataclass, field

BASE_PRESSURE_LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300]

# 25 levels — 25 hPa spacing below 500, 50 hPa above.
# Gives ~1000ft vertical resolution in the lower atmosphere (4x improvement).
# GFS supports all 25; best_match (auto-blend) also supports them.
EXTENDED_PRESSURE_LEVELS = [
    1000, 975, 950, 925, 900, 875, 850, 825, 800, 775,
    750, 725, 700, 675, 650, 625, 600, 575, 550, 525,
    500, 450, 400, 350, 300,
]

# ECMWF IFS only supports 13 levels on Open-Meteo; these are the ones
# within our 1000–300 hPa range (250/200/150/100/50 are above our ceiling).
ECMWF_PRESSURE_LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300]

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
    "visibility",
]

PRESSURE_LEVEL_VARIABLES = [
    "temperature",
    "relative_humidity",
    "dewpoint",
    "wind_speed",
    "wind_direction",
    "geopotential_height",
    "vertical_velocity",
]


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


MODEL_ENDPOINTS: dict[str, ModelEndpoint] = {
    "best_match": ModelEndpoint(
        name="Best Match",
        base_url="https://api.open-meteo.com/v1/forecast",
        max_days=16,
        pressure_levels=list(EXTENDED_PRESSURE_LEVELS),
    ),
    "ecmwf": ModelEndpoint(
        name="ECMWF IFS",
        base_url="https://api.open-meteo.com/v1/ecmwf",
        max_days=10,
        unavailable_surface=["freezing_level_height", "visibility"],
        pressure_levels=list(ECMWF_PRESSURE_LEVELS),
    ),
    "gfs": ModelEndpoint(
        name="GFS",
        base_url="https://api.open-meteo.com/v1/gfs",
        max_days=16,
        pressure_levels=list(EXTENDED_PRESSURE_LEVELS),
    ),
    "icon": ModelEndpoint(
        name="DWD ICON",
        base_url="https://api.open-meteo.com/v1/dwd-icon",
        max_days=7,
        unavailable_surface=["precipitation_probability"],
        unavailable_pressure=["vertical_velocity"],
    ),
    "ukmo": ModelEndpoint(
        name="UK Met Office",
        base_url="https://api.open-meteo.com/v1/forecast",
        max_days=7,
        model_param="ukmo_seamless",
        unavailable_surface=["precipitation_probability"],
    ),
    "meteofrance": ModelEndpoint(
        name="Météo-France",
        base_url="https://api.open-meteo.com/v1/meteofrance",
        max_days=6,
        unavailable_surface=["precipitation_probability",
                             "freezing_level_height", "visibility"],
        unavailable_pressure=["vertical_velocity"],
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
