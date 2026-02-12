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
    flight_ceiling_ft: int = 18000
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


class RoutePoint(BaseModel):
    """A point along a route — either a named waypoint or an interpolated point."""

    lat: float
    lon: float
    distance_from_origin_nm: float
    waypoint_icao: str | None = None  # non-None if this is a named waypoint
    waypoint_name: str | None = None  # full airport name when waypoint_icao is set


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
    vertical_velocity_pa_s: Optional[float] = None  # omega (Pa/s)


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



class IcingType(str, Enum):
    """Type of icing based on wet-bulb temperature regime."""

    NONE = "none"
    RIME = "rime"
    MIXED = "mixed"
    CLEAR = "clear"


class CloudCoverage(str, Enum):
    """Cloud coverage category derived from dewpoint depression."""

    SCT = "sct"
    BKN = "bkn"
    OVC = "ovc"


class ConvectiveRisk(str, Enum):
    """Convective risk level from thermodynamic indices."""

    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    EXTREME = "extreme"


class VerticalMotionClass(str, Enum):
    """Classification of the vertical motion profile."""

    QUIESCENT = "quiescent"
    SYNOPTIC_ASCENT = "synoptic_ascent"
    SYNOPTIC_SUBSIDENCE = "synoptic_subsidence"
    CONVECTIVE = "convective"
    OSCILLATING = "oscillating"
    UNAVAILABLE = "unavailable"


class CATRiskLevel(str, Enum):
    """Clear-air turbulence risk level from Richardson number."""

    NONE = "none"
    LIGHT = "light"
    MODERATE = "moderate"
    SEVERE = "severe"


class ThermodynamicIndices(BaseModel):
    """Profile-level thermodynamic indices computed via MetPy."""

    lcl_pressure_hpa: Optional[float] = None
    lcl_altitude_ft: Optional[float] = None
    lfc_pressure_hpa: Optional[float] = None
    lfc_altitude_ft: Optional[float] = None
    el_pressure_hpa: Optional[float] = None
    el_altitude_ft: Optional[float] = None
    cape_surface_jkg: Optional[float] = None
    cape_most_unstable_jkg: Optional[float] = None
    cape_mixed_layer_jkg: Optional[float] = None
    cin_surface_jkg: Optional[float] = None
    lifted_index: Optional[float] = None
    showalter_index: Optional[float] = None
    k_index: Optional[float] = None
    total_totals: Optional[float] = None
    precipitable_water_mm: Optional[float] = None
    freezing_level_ft: Optional[float] = None
    minus10c_level_ft: Optional[float] = None
    minus20c_level_ft: Optional[float] = None
    bulk_shear_0_6km_kt: Optional[float] = None
    bulk_shear_0_1km_kt: Optional[float] = None


class DerivedLevel(BaseModel):
    """Per-pressure-level derived values for sounding analysis."""

    pressure_hpa: int
    altitude_ft: Optional[float] = None
    temperature_c: Optional[float] = None
    dewpoint_c: Optional[float] = None
    relative_humidity_pct: Optional[float] = None
    wet_bulb_c: Optional[float] = None
    dewpoint_depression_c: Optional[float] = None
    theta_e_k: Optional[float] = None
    lapse_rate_c_per_km: Optional[float] = None
    omega_pa_s: Optional[float] = None  # raw model omega (Pa/s)
    w_fpm: Optional[float] = None  # vertical velocity (ft/min)
    richardson_number: Optional[float] = None  # Ri for layer below
    bv_freq_squared_per_s2: Optional[float] = None  # N² for layer below (s⁻²)


class EnhancedCloudLayer(BaseModel):
    """Cloud layer detected from dewpoint depression analysis."""

    base_ft: float
    top_ft: float
    base_pressure_hpa: Optional[int] = None
    top_pressure_hpa: Optional[int] = None
    thickness_ft: Optional[float] = None
    mean_temperature_c: Optional[float] = None
    coverage: CloudCoverage = CloudCoverage.SCT
    mean_dewpoint_depression_c: Optional[float] = None


class IcingZone(BaseModel):
    """Grouped icing zone from wet-bulb temperature analysis."""

    base_ft: float
    top_ft: float
    base_pressure_hpa: Optional[int] = None
    top_pressure_hpa: Optional[int] = None
    risk: IcingRisk = IcingRisk.NONE
    icing_type: IcingType = IcingType.NONE
    sld_risk: bool = False
    mean_temperature_c: Optional[float] = None
    mean_wet_bulb_c: Optional[float] = None


class ConvectiveAssessment(BaseModel):
    """Convective risk assessment from thermodynamic indices."""

    risk_level: ConvectiveRisk = ConvectiveRisk.NONE
    cape_jkg: Optional[float] = None
    cin_jkg: Optional[float] = None
    lcl_altitude_ft: Optional[float] = None
    lfc_altitude_ft: Optional[float] = None
    el_altitude_ft: Optional[float] = None
    bulk_shear_0_6km_kt: Optional[float] = None
    lifted_index: Optional[float] = None
    k_index: Optional[float] = None
    total_totals: Optional[float] = None
    severe_modifiers: list[str] = Field(default_factory=list)


class CATRiskLayer(BaseModel):
    """A layer of clear-air turbulence risk identified by low Richardson number."""

    base_ft: float
    top_ft: float
    base_pressure_hpa: Optional[int] = None
    top_pressure_hpa: Optional[int] = None
    richardson_number: Optional[float] = None  # minimum Ri in layer
    risk: CATRiskLevel = CATRiskLevel.NONE


class VerticalMotionAssessment(BaseModel):
    """Vertical motion and turbulence assessment for a sounding."""

    classification: VerticalMotionClass = VerticalMotionClass.UNAVAILABLE
    max_omega_pa_s: Optional[float] = None
    max_w_fpm: Optional[float] = None
    max_w_level_ft: Optional[float] = None
    cat_risk_layers: list[CATRiskLayer] = Field(default_factory=list)
    convective_contamination: bool = False


class SoundingAnalysis(BaseModel):
    """Complete sounding analysis for one model at one waypoint/time."""

    indices: Optional[ThermodynamicIndices] = None
    derived_levels: list[DerivedLevel] = Field(default_factory=list)
    cloud_layers: list[EnhancedCloudLayer] = Field(default_factory=list)
    icing_zones: list[IcingZone] = Field(default_factory=list)
    convective: Optional[ConvectiveAssessment] = None
    vertical_motion: Optional[VerticalMotionAssessment] = None
    # NWP 3-level cloud cover from Open-Meteo (None for ECMWF)
    cloud_cover_low_pct: Optional[float] = None
    cloud_cover_mid_pct: Optional[float] = None
    cloud_cover_high_pct: Optional[float] = None


class VerticalRegime(BaseModel):
    """A vertical slice with uniform conditions, derived from weather data."""

    floor_ft: float
    ceiling_ft: float
    in_cloud: bool
    icing_risk: IcingRisk = IcingRisk.NONE
    icing_type: IcingType = IcingType.NONE
    cloud_cover_pct: Optional[float] = None  # NWP cloud % for this regime's ICAO band
    cat_risk: Optional[str] = None  # CAT turbulence risk level at this regime
    strong_vertical_motion: bool = False  # |w| > 200 fpm
    label: str  # e.g. "Clear", "In cloud 95%", "In cloud, icing MOD (mixed)"


class AltitudeAdvisory(BaseModel):
    """An actionable altitude recommendation, aggregated across models."""

    advisory_type: str  # "descend_below_icing", "climb_above_icing", etc.
    altitude_ft: Optional[float] = None  # worst-case across models
    feasible: bool = True  # achievable within constraints
    reason: str = ""  # human-readable explanation
    per_model_ft: dict[str, Optional[float]] = Field(default_factory=dict)


class AltitudeAdvisories(BaseModel):
    """Complete altitude picture for a waypoint."""

    regimes: dict[str, list[VerticalRegime]] = Field(default_factory=dict)
    advisories: list[AltitudeAdvisory] = Field(default_factory=list)
    cruise_in_icing: bool = False
    cruise_icing_risk: IcingRisk = IcingRisk.NONE


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
    sounding: dict[str, SoundingAnalysis] = Field(default_factory=dict)
    altitude_advisories: Optional[AltitudeAdvisories] = None
    model_divergence: list[ModelDivergence] = Field(default_factory=list)


class RouteCrossSection(BaseModel):
    """Cross-section forecast data along the full route for one model."""

    model: ModelSource
    route_points: list[RoutePoint]
    fetched_at: datetime
    point_forecasts: list[WaypointForecast]


class ForecastSnapshot(BaseModel):
    """Root object: complete snapshot of one fetch run."""

    route: RouteConfig
    target_date: str  # ISO date string YYYY-MM-DD
    fetch_date: str  # ISO date string
    days_out: int  # D-N
    forecasts: list[WaypointForecast] = Field(default_factory=list)
    analyses: list[WaypointAnalysis] = Field(default_factory=list)
    cross_sections: list[RouteCrossSection] = Field(default_factory=list)


# --- Flight & briefing pack models (API/web layer) ---


class Flight(BaseModel):
    """A saved briefing target — route + date/time specifics."""

    id: str  # slug: "{route_name}-{target_date}"
    route_name: str  # key in routes.yaml, or derived from waypoints
    waypoints: list[str] = Field(default_factory=list)  # ICAO codes
    target_date: str  # YYYY-MM-DD
    target_time_utc: int = 9  # departure hour
    cruise_altitude_ft: int = 8000
    flight_ceiling_ft: int = 18000
    flight_duration_hours: float = 0.0
    created_at: datetime


class BriefingPackMeta(BaseModel):
    """Metadata for one fetch — lightweight index for history listing."""

    flight_id: str
    fetch_timestamp: str  # ISO datetime
    days_out: int
    has_gramet: bool = False
    has_skewt: bool = False
    has_digest: bool = False
    assessment: Optional[str] = None  # GREEN/AMBER/RED from digest
    assessment_reason: Optional[str] = None
