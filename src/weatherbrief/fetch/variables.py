"""Variable definitions and per-model availability for Open-Meteo API."""

from __future__ import annotations

from dataclasses import dataclass, field

PRESSURE_LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300]

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


MODEL_ENDPOINTS: dict[str, ModelEndpoint] = {
    "best_match": ModelEndpoint(
        name="Best Match",
        base_url="https://api.open-meteo.com/v1/forecast",
        max_days=16,
    ),
    "ecmwf": ModelEndpoint(
        name="ECMWF IFS",
        base_url="https://api.open-meteo.com/v1/ecmwf",
        max_days=10,
        unavailable_surface=["relative_humidity_2m", "wind_gusts_10m",
                             "precipitation_probability", "cloud_cover_low",
                             "cloud_cover_mid", "cloud_cover_high",
                             "freezing_level_height", "cape", "visibility"],
        unavailable_pressure=["dewpoint"],
    ),
    "gfs": ModelEndpoint(
        name="GFS",
        base_url="https://api.open-meteo.com/v1/gfs",
        max_days=16,
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
        unavailable_surface=["dewpoint_2m", "precipitation_probability",
                             "freezing_level_height", "cape", "visibility"],
        unavailable_pressure=["dewpoint", "vertical_velocity"],
    ),
    "meteofrance": ModelEndpoint(
        name="Météo-France",
        base_url="https://api.open-meteo.com/v1/meteofrance",
        max_days=6,
        unavailable_surface=["precipitation_probability",
                             "freezing_level_height", "cape", "visibility"],
        unavailable_pressure=["dewpoint", "vertical_velocity"],
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
        for level in PRESSURE_LEVELS:
            pressure.append(f"{var}_{level}hPa")

    return ",".join(surface + pressure)
