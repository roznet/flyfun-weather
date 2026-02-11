"""Pydantic v2 models for weatherbrief."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Waypoint(BaseModel):
    """An aviation waypoint with coordinates."""

    icao: str
    name: str
    lat: float
    lon: float


class RouteConfig(BaseModel):
    """A flight route definition loaded from config."""

    name: str
    origin: Waypoint
    midpoint: Optional[Waypoint] = None
    destination: Waypoint
    cruise_altitude_ft: int
    cruise_pressure_hpa: int
    track_deg: float
    estimated_eet_hours: float = 0.0

    @property
    def waypoints(self) -> list[Waypoint]:
        """All waypoints in route order."""
        pts = [self.origin]
        if self.midpoint:
            pts.append(self.midpoint)
        pts.append(self.destination)
        return pts


class ModelSource(str, Enum):
    """Weather model source identifiers."""

    BEST_MATCH = "best_match"
    GFS = "gfs"
    ECMWF = "ecmwf"
    ICON = "icon"
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
