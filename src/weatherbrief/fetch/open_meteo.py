"""Open-Meteo API client for multi-model weather forecasts."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone

import os

import requests

from weatherbrief.fetch.variables import (
    MODEL_ENDPOINTS,
    PRESSURE_LEVEL_VARIABLES,
    build_hourly_params,
)
from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    PressureLevelData,
    RoutePoint,
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


def _int_or_none(v: float | None) -> int | None:
    """Convert a float to int, or return None."""
    return int(v) if v is not None else None


_MAX_RETRIES = 4
_RETRY_BACKOFF = [10, 30, 60, 90]  # seconds — generous for expanded pressure levels


_FREE_HOST = "api.open-meteo.com"
_CUSTOMER_HOST = "customer-api.open-meteo.com"
_HISTORICAL_HOST = "historical-forecast-api.open-meteo.com"
_CUSTOMER_HISTORICAL_HOST = "customer-historical-forecast-api.open-meteo.com"

# Mapping from model-specific API paths to the models= param value
# used by the historical forecast API (which only supports /v1/forecast).
_HISTORICAL_MODEL_MAP: dict[str, str] = {
    "/v1/ecmwf": "ecmwf_ifs025",
    "/v1/gfs": "gfs_seamless",
    "/v1/dwd-icon": "icon_seamless",
    "/v1/meteofrance": "meteofrance_seamless",
    "/v1/gem": "gem_seamless",
}


class OpenMeteoClient:
    """Client for fetching forecasts from the Open-Meteo API."""

    def __init__(self, timeout: int = 30, historical: bool = False):
        self._historical = historical
        self.timeout = 60 if historical else timeout  # historical API is slower
        self.session = requests.Session()
        self._api_key = os.environ.get("OPENMETEO_API_KEY")
        if self._api_key:
            logger.info("Using Open-Meteo customer API (paid plan)")
        if historical:
            host = _CUSTOMER_HISTORICAL_HOST if self._api_key else _HISTORICAL_HOST
            logger.info("Using Open-Meteo historical forecast API (%s)", host)

    def _prepare_request(self, url: str, params: dict) -> tuple[str, dict]:
        """Swap to historical or customer API host as needed.

        Historical mode uses the historical forecast host.  When a paid API key
        is available, the customer historical host is used for better
        performance and higher rate limits.  Model-specific paths
        (e.g. ``/v1/gfs``) are rewritten to ``/v1/forecast`` with a
        ``models=`` parameter because the historical API only supports the
        generic forecast endpoint.
        """
        if self._historical:
            if self._api_key:
                url = url.replace(_FREE_HOST, _CUSTOMER_HISTORICAL_HOST)
                params = {**params, "apikey": self._api_key}
            else:
                url = url.replace(_FREE_HOST, _HISTORICAL_HOST)
            # Rewrite model-specific paths to /v1/forecast + models= param
            for path, model_name in _HISTORICAL_MODEL_MAP.items():
                if path in url:
                    url = url.replace(path, "/v1/forecast")
                    params = {**params, "models": model_name}
                    break
        elif self._api_key:
            url = url.replace(_FREE_HOST, _CUSTOMER_HOST)
            params = {**params, "apikey": self._api_key}
        return url, params

    def _get_with_retry(self, url: str, params: dict) -> requests.Response:
        """GET with retry on 429 rate-limit responses."""
        url, params = self._prepare_request(url, params)
        for attempt in range(_MAX_RETRIES):
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp
            # Use Retry-After header if present, otherwise use backoff schedule
            retry_after = resp.headers.get("Retry-After")
            wait = int(retry_after) if retry_after and retry_after.isdigit() else _RETRY_BACKOFF[attempt]
            logger.warning(
                "Rate limited (429) on attempt %d/%d, retrying in %ds: %s",
                attempt + 1, _MAX_RETRIES, wait, url.split("/")[-1],
            )
            time.sleep(wait)
        # Final attempt — let it raise on any error
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp

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
        if endpoint.model_param:
            params["models"] = endpoint.model_param

        logger.info("Fetching %s for %s (%s)", endpoint.name, waypoint.icao, waypoint.name)

        resp = self._get_with_retry(endpoint.base_url, params)
        data = resp.json()

        hourly_data = data.get("hourly", {})
        timestamps = hourly_data.get("time", [])
        forecasts = []

        for i, ts in enumerate(timestamps):
            forecast = self._parse_hourly(
                hourly_data, i, ts, endpoint.unavailable_pressure,
                endpoint.pressure_levels,
            )
            forecasts.append(forecast)

        return WaypointForecast(
            waypoint=waypoint,
            model=model,
            fetched_at=datetime.now(timezone.utc),
            hourly=forecasts,
        )

    def fetch_all_models(
        self,
        waypoint: Waypoint,
        models: list[ModelSource],
        days_out: int | None = None,
    ) -> list[WaypointForecast]:
        """Fetch forecasts from multiple models, continuing on individual failures.

        If days_out is provided, models whose max forecast range is shorter
        than days_out are skipped.
        """
        results = []
        for model in models:
            endpoint = MODEL_ENDPOINTS[model.value]
            if days_out is not None and days_out >= endpoint.max_days:
                logger.info(
                    "Skipping %s for %s: %d days out exceeds %d-day range",
                    model.value, waypoint.icao, days_out, endpoint.max_days,
                )
                continue
            try:
                result = self.fetch_forecast(waypoint, model)
                results.append(result)
            except Exception:
                logger.warning(
                    "Failed to fetch %s for %s", model.value, waypoint.icao,
                    exc_info=True,
                )
        return results

    def fetch_multi_point(
        self,
        points: list[RoutePoint],
        model: ModelSource,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[WaypointForecast]:
        """Fetch forecast for multiple points in a single API call.

        Open-Meteo accepts comma-separated latitude/longitude values and returns
        a list of per-location results.  When ``start_date`` / ``end_date`` are
        provided, only that time window is requested (reduces payload).

        Returns one ``WaypointForecast`` per input point, in the same order.
        """
        model_key = model.value
        endpoint = MODEL_ENDPOINTS[model_key]
        hourly_params = build_hourly_params(endpoint)

        params: dict[str, object] = {
            "latitude": ",".join(str(p.lat) for p in points),
            "longitude": ",".join(str(p.lon) for p in points),
            "hourly": hourly_params,
            "wind_speed_unit": "kn",
            "timezone": "UTC",
        }
        if start_date and end_date:
            params["start_date"] = start_date
            params["end_date"] = end_date
        else:
            params["forecast_days"] = min(endpoint.max_days, 16)
        if endpoint.model_param:
            params["models"] = endpoint.model_param

        logger.info(
            "Fetching %s for %d route points (%s–%s)",
            endpoint.name,
            len(points),
            start_date or "full",
            end_date or "range",
        )

        resp = self._get_with_retry(endpoint.base_url, params)
        response_json = resp.json()

        # Single-point returns a dict; multi-point returns a list of dicts.
        if isinstance(response_json, dict):
            response_json = [response_json]

        fetched_at = datetime.now(timezone.utc)
        results: list[WaypointForecast] = []

        for point, point_data in zip(points, response_json):
            hourly_data = point_data.get("hourly", {})
            timestamps = hourly_data.get("time", [])

            hourly_list = [
                self._parse_hourly(
                    hourly_data, i, ts, endpoint.unavailable_pressure,
                    endpoint.pressure_levels,
                )
                for i, ts in enumerate(timestamps)
            ]

            # Build a synthetic Waypoint for this route point
            if point.waypoint_icao:
                wp = Waypoint(
                    icao=point.waypoint_icao,
                    name=point.waypoint_name or point.waypoint_icao,
                    lat=point.lat,
                    lon=point.lon,
                )
            else:
                label = f"RP{int(point.distance_from_origin_nm):03d}"
                wp = Waypoint(icao=label, name=label, lat=point.lat, lon=point.lon)

            results.append(
                WaypointForecast(
                    waypoint=wp,
                    model=model,
                    fetched_at=fetched_at,
                    hourly=hourly_list,
                )
            )

        return results

    def _parse_hourly(
        self,
        data: dict,
        idx: int,
        timestamp: str,
        unavailable_pressure: list[str],
        pressure_levels_list: list[int],
    ) -> HourlyForecast:
        """Parse one hourly time step from the flat API response."""

        def get(key: str) -> float | None:
            arr = data.get(key)
            if arr is None or idx >= len(arr):
                return None
            return arr[idx]

        # Parse pressure level data
        pressure_levels = []
        for level in pressure_levels_list:
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
                    vertical_velocity_pa_s=get(f"vertical_velocity_{level}hPa"),
                )
            )

        return HourlyForecast(
            time=datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc),
            temperature_2m_c=get("temperature_2m"),
            relative_humidity_2m_pct=get("relative_humidity_2m"),
            dewpoint_2m_c=get("dewpoint_2m"),
            surface_pressure_hpa=get("surface_pressure"),
            pressure_msl_hpa=get("pressure_msl"),
            wind_speed_10m_kt=get("wind_speed_10m"),
            wind_direction_10m_deg=get("wind_direction_10m"),
            wind_gusts_10m_kt=get("wind_gusts_10m"),
            precipitation_mm=get("precipitation"),
            rain_mm=get("rain"),
            showers_mm=get("showers"),
            snowfall_cm=get("snowfall"),
            weather_code=_int_or_none(get("weather_code")),
            precipitation_probability_pct=get("precipitation_probability"),
            cloud_cover_pct=get("cloud_cover"),
            cloud_cover_low_pct=get("cloud_cover_low"),
            cloud_cover_mid_pct=get("cloud_cover_mid"),
            cloud_cover_high_pct=get("cloud_cover_high"),
            freezing_level_m=get("freezing_level_height"),
            cape_jkg=get("cape"),
            convective_inhibition_jkg=get("convective_inhibition"),
            lifted_index_raw=get("lifted_index"),
            visibility_m=get("visibility"),
            pressure_levels=pressure_levels,
        )
