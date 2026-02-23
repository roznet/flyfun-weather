"""METAR/TAF observation models for route weather integration."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AirportObservation(BaseModel):
    """METAR/TAF data for one airport along the route."""

    icao: str
    name: str | None = None
    distance_from_route_nm: float
    enroute_distance_nm: float | None = None
    nearest_waypoint_icao: str
    metar_raw: str | None = None
    metar_time: datetime | None = None
    metar_flight_category: str | None = None  # "VFR"/"MVFR"/"IFR"/"LIFR"
    metar_ceiling_ft: int | None = None
    metar_visibility_m: int | None = None
    metar_wind_dir: int | None = None
    metar_wind_speed_kt: int | None = None
    metar_wind_gust_kt: int | None = None
    metar_weather: list[str] = Field(default_factory=list)
    metar_temperature_c: int | None = None
    metar_dewpoint_c: int | None = None
    metar_qnh: float | None = None
    taf_raw: str | None = None
    taf_flight_category_at_eta: str | None = None
    taf_trend_type: str | None = None
    has_metar: bool = False
    has_taf: bool = False


class ObservationComparison(BaseModel):
    """Comparison of one airport's observations vs nearest model prediction."""

    icao: str
    obs_category: str | None = None
    model_category: str | None = None
    category_match: str  # "CONFIRMING" / "MINOR_DELTA" / "SIGNIFICANT" / "CONFLICTING"
    ceiling_delta_ft: int | None = None
    visibility_delta_m: float | None = None
    wind_speed_delta_kt: float | None = None
    detail: str = ""


class RouteObservations(BaseModel):
    """Complete METAR/TAF picture along the route."""

    corridor_nm: float
    fetch_time: datetime
    airports_found: int
    airports_with_metar: int
    airports_with_taf: int
    airports: list[AirportObservation] = Field(default_factory=list)
    comparisons: list[ObservationComparison] = Field(default_factory=list)
    worst_metar_category: str | None = None
    worst_taf_category: str | None = None
    has_conflicts: bool = False
    phenomena_along_route: list[str] = Field(default_factory=list)
