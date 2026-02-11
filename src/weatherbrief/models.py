"""Pydantic v2 models for weatherbrief."""

from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class Waypoint(BaseModel):
    """An aviation waypoint with coordinates."""

    icao: str
    name: str
    lat: float
    lon: float


def bearing_between(wp_a: Waypoint, wp_b: Waypoint) -> float:
    """Compute great-circle initial bearing from wp_a to wp_b in degrees [0, 360)."""
    lat1 = math.radians(wp_a.lat)
    lat2 = math.radians(wp_b.lat)
    dlon = math.radians(wp_b.lon - wp_a.lon)

    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(x, y)) % 360


def altitude_to_pressure_hpa(altitude_ft: int) -> int:
    """Convert altitude in feet to pressure in hPa using standard atmosphere.

    Uses the barometric formula for the troposphere (valid up to ~36,000 ft).
    """
    altitude_m = altitude_ft * 0.3048
    # Standard atmosphere constants
    P0 = 1013.25  # sea level pressure hPa
    T0 = 288.15  # sea level temperature K
    L = 0.0065  # lapse rate K/m
    g = 9.80665  # gravity m/s^2
    M = 0.0289644  # molar mass of air kg/mol
    R = 8.31447  # gas constant J/(mol·K)

    pressure = P0 * (1 - L * altitude_m / T0) ** (g * M / (R * L))
    return round(pressure)


class RouteConfig(BaseModel):
    """A flight route definition loaded from config."""

    name: str
    waypoints: list[Waypoint] = Field(min_length=2)
    cruise_altitude_ft: int = 8000
    flight_duration_hours: float = 0.0

    @model_validator(mode="after")
    def _validate_waypoints(self) -> RouteConfig:
        if len(self.waypoints) < 2:
            raise ValueError("Route must have at least 2 waypoints")
        return self

    @property
    def origin(self) -> Waypoint:
        """First waypoint (departure)."""
        return self.waypoints[0]

    @property
    def destination(self) -> Waypoint:
        """Last waypoint (arrival)."""
        return self.waypoints[-1]

    @property
    def cruise_pressure_hpa(self) -> int:
        """Cruise pressure derived from altitude via standard atmosphere."""
        return altitude_to_pressure_hpa(self.cruise_altitude_ft)

    def leg_bearing(self, leg_index: int) -> float:
        """Bearing for leg N (from waypoint[N] to waypoint[N+1])."""
        return bearing_between(self.waypoints[leg_index], self.waypoints[leg_index + 1])

    def waypoint_track(self, waypoint_icao: str) -> float:
        """Representative track for a waypoint: average of incoming/outgoing leg bearings."""
        idx = next(
            (i for i, wp in enumerate(self.waypoints) if wp.icao == waypoint_icao),
            None,
        )
        if idx is None:
            raise ValueError(f"Waypoint {waypoint_icao} not in route")

        bearings = []
        if idx > 0:
            bearings.append(self.leg_bearing(idx - 1))
        if idx < len(self.waypoints) - 1:
            bearings.append(self.leg_bearing(idx))

        if not bearings:
            return 0.0
        if len(bearings) == 1:
            return bearings[0]

        # Circular mean of two bearings
        rads = [math.radians(b) for b in bearings]
        x = sum(math.cos(r) for r in rads)
        y = sum(math.sin(r) for r in rads)
        return math.degrees(math.atan2(y, x)) % 360


class ModelSource(str, Enum):
    """Weather model source identifiers."""

    BEST_MATCH = "best_match"
    GFS = "gfs"
    ECMWF = "ecmwf"
    ICON = "icon"
    UKMO = "ukmo"
    METEOFRANCE = "meteofrance"


class PressureLevelData(BaseModel):
    """Weather data at a single pressure level for one time step."""

    pressure_hpa: int
    temperature_c: Optional[float] = None
    relative_humidity_pct: Optional[float] = None
    dewpoint_c: Optional[float] = None
    wind_speed_kt: Optional[float] = None
    wind_direction_deg: Optional[float] = None
    geopotential_height_m: Optional[float] = None


class HourlyForecast(BaseModel):
    """Forecast data for one hour at one location."""

    time: datetime

    # Surface variables
    temperature_2m_c: Optional[float] = None
    relative_humidity_2m_pct: Optional[float] = None
    dewpoint_2m_c: Optional[float] = None
    surface_pressure_hpa: Optional[float] = None
    pressure_msl_hpa: Optional[float] = None
    wind_speed_10m_kt: Optional[float] = None
    wind_direction_10m_deg: Optional[float] = None
    wind_gusts_10m_kt: Optional[float] = None
    precipitation_mm: Optional[float] = None
    precipitation_probability_pct: Optional[float] = None
    cloud_cover_pct: Optional[float] = None
    cloud_cover_low_pct: Optional[float] = None
    cloud_cover_mid_pct: Optional[float] = None
    cloud_cover_high_pct: Optional[float] = None
    freezing_level_m: Optional[float] = None
    cape_jkg: Optional[float] = None
    visibility_m: Optional[float] = None

    # Pressure level data
    pressure_levels: list[PressureLevelData] = Field(default_factory=list)

    def level_at(self, pressure_hpa: int) -> Optional[PressureLevelData]:
        """Get data at a specific pressure level."""
        for lvl in self.pressure_levels:
            if lvl.pressure_hpa == pressure_hpa:
                return lvl
        return None


class WaypointForecast(BaseModel):
    """Complete forecast for one waypoint from one model."""

    waypoint: Waypoint
    model: ModelSource
    fetched_at: datetime
    hourly: list[HourlyForecast] = Field(default_factory=list)

    def at_time(self, target: datetime) -> Optional[HourlyForecast]:
        """Find the forecast hour closest to target time."""
        if not self.hourly:
            return None
        return min(self.hourly, key=lambda h: abs((h.time - target).total_seconds()))


# --- Analysis result models ---


class WindComponent(BaseModel):
    """Wind broken into headwind/tailwind and crosswind components."""

    wind_speed_kt: float
    wind_direction_deg: float
    track_deg: float
    headwind_kt: float  # positive = headwind, negative = tailwind
    crosswind_kt: float  # positive = from right, negative = from left


class IcingRisk(str, Enum):
    """Icing severity levels."""

    NONE = "none"
    LIGHT = "light"
    MODERATE = "moderate"
    SEVERE = "severe"


class IcingBand(BaseModel):
    """Icing assessment at a single pressure level."""

    pressure_hpa: int
    altitude_ft: Optional[float] = None
    temperature_c: Optional[float] = None
    relative_humidity_pct: Optional[float] = None
    risk: IcingRisk = IcingRisk.NONE


class CloudLayer(BaseModel):
    """Estimated cloud layer from RH profile."""

    base_ft: float
    top_ft: Optional[float] = None
    base_pressure_hpa: Optional[int] = None
    top_pressure_hpa: Optional[int] = None
    note: str = "estimated"


class AgreementLevel(str, Enum):
    """How well models agree on a variable."""

    GOOD = "good"
    MODERATE = "moderate"
    POOR = "poor"


class ModelDivergence(BaseModel):
    """Comparison of a single variable across models."""

    variable: str
    model_values: dict[str, float]
    mean: float
    spread: float
    agreement: AgreementLevel


class WaypointAnalysis(BaseModel):
    """Analysis results for a single waypoint at a target time."""

    waypoint: Waypoint
    target_time: datetime
    wind_components: dict[str, WindComponent] = Field(default_factory=dict)
    icing_bands: dict[str, list[IcingBand]] = Field(default_factory=dict)
    cloud_layers: dict[str, list[CloudLayer]] = Field(default_factory=dict)
    model_divergence: list[ModelDivergence] = Field(default_factory=list)


class ForecastSnapshot(BaseModel):
    """Root object: complete snapshot of one fetch run."""

    route: RouteConfig
    target_date: str  # ISO date string YYYY-MM-DD
    fetch_date: str  # ISO date string
    days_out: int  # D-N
    forecasts: list[WaypointForecast] = Field(default_factory=list)
    analyses: list[WaypointAnalysis] = Field(default_factory=list)
