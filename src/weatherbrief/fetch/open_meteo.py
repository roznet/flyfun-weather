"""Open-Meteo API client for multi-model weather forecasts."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import requests

from weatherbrief.fetch.variables import (
    MODEL_ENDPOINTS,
    PRESSURE_LEVELS,
    PRESSURE_LEVEL_VARIABLES,
    build_hourly_params,
)
from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    PressureLevelData,
    Waypoint,
    WaypointForecast,
)

logger = logging.getLogger(__name__)

# Magnus formula constants
MAGNUS_B = 17.67
MAGNUS_C = 243.5  # °C


def magnus_dewpoint(temp_c: float, rh_pct: float) -> float:
    """Derive dewpoint from temperature and relative humidity using the Magnus formula.

    γ = ln(RH/100) + (b × T) / (c + T)
    Td = (c × γ) / (b - γ)
    """
    if rh_pct <= 0:
        return temp_c - 30.0  # very dry fallback
    gamma = math.log(rh_pct / 100.0) + (MAGNUS_B * temp_c) / (MAGNUS_C + temp_c)
    return (MAGNUS_C * gamma) / (MAGNUS_B - gamma)


class OpenMeteoClient:
    """Client for fetching forecasts from the Open-Meteo API."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()

    def fetch_forecast(
        self, waypoint: Waypoint, model: ModelSource
    ) -> WaypointForecast:
        """Fetch forecast for a single waypoint from a single model."""
        model_key = model.value
        endpoint = MODEL_ENDPOINTS[model_key]
        hourly_params = build_hourly_params(endpoint)

        params = {
            "latitude": waypoint.lat,
            "longitude": waypoint.lon,
            "hourly": hourly_params,
            "wind_speed_unit": "kn",
            "forecast_days": min(endpoint.max_days, 16),
            "timezone": "UTC",
        }

        logger.info("Fetching %s for %s (%s)", endpoint.name, waypoint.icao, waypoint.name)

        resp = self.session.get(endpoint.base_url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()

        hourly_data = data.get("hourly", {})
        timestamps = hourly_data.get("time", [])
        forecasts = []

        for i, ts in enumerate(timestamps):
            forecast = self._parse_hourly(hourly_data, i, ts, endpoint.unavailable_pressure)
            forecasts.append(forecast)

        return WaypointForecast(
            waypoint=waypoint,
            model=model,
            fetched_at=datetime.now(timezone.utc),
            hourly=forecasts,
        )

    def fetch_all_models(
        self, waypoint: Waypoint, models: list[ModelSource]
    ) -> list[WaypointForecast]:
        """Fetch forecasts from multiple models, continuing on individual failures."""
        results = []
        for model in models:
            try:
                result = self.fetch_forecast(waypoint, model)
                results.append(result)
            except Exception:
                logger.warning(
                    "Failed to fetch %s for %s", model.value, waypoint.icao,
                    exc_info=True,
                )
        return results

    def _parse_hourly(
        self,
        data: dict,
        idx: int,
        timestamp: str,
        unavailable_pressure: list[str],
    ) -> HourlyForecast:
        """Parse one hourly time step from the flat API response."""

        def get(key: str) -> float | None:
            arr = data.get(key)
            if arr is None or idx >= len(arr):
                return None
            return arr[idx]

        # Parse pressure level data
        pressure_levels = []
        for level in PRESSURE_LEVELS:
            temp = get(f"temperature_{level}hPa")
            rh = get(f"relative_humidity_{level}hPa")
            dp = get(f"dewpoint_{level}hPa")

            # Derive dewpoint from temp + RH if not directly available
            if dp is None and temp is not None and rh is not None:
                dp = magnus_dewpoint(temp, rh)

            pressure_levels.append(
                PressureLevelData(
                    pressure_hpa=level,
                    temperature_c=temp,
                    relative_humidity_pct=rh,
                    dewpoint_c=dp,
                    wind_speed_kt=get(f"wind_speed_{level}hPa"),
                    wind_direction_deg=get(f"wind_direction_{level}hPa"),
                    geopotential_height_m=get(f"geopotential_height_{level}hPa"),
                )
            )

        return HourlyForecast(
            time=datetime.fromisoformat(timestamp),
            temperature_2m_c=get("temperature_2m"),
            relative_humidity_2m_pct=get("relative_humidity_2m"),
            dewpoint_2m_c=get("dewpoint_2m"),
            surface_pressure_hpa=get("surface_pressure"),
            pressure_msl_hpa=get("pressure_msl"),
            wind_speed_10m_kt=get("wind_speed_10m"),
            wind_direction_10m_deg=get("wind_direction_10m"),
            wind_gusts_10m_kt=get("wind_gusts_10m"),
            precipitation_mm=get("precipitation"),
            precipitation_probability_pct=get("precipitation_probability"),
            cloud_cover_pct=get("cloud_cover"),
            cloud_cover_low_pct=get("cloud_cover_low"),
            cloud_cover_mid_pct=get("cloud_cover_mid"),
            cloud_cover_high_pct=get("cloud_cover_high"),
            freezing_level_m=get("freezing_level_height"),
            cape_jkg=get("cape"),
            visibility_m=get("visibility"),
            pressure_levels=pressure_levels,
        )
