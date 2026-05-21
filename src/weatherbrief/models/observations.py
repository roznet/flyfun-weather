"""METAR/TAF observation models for route weather integration."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, computed_field


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
    taf_wind_dir: int | None = None
    taf_wind_speed_kt: int | None = None
    taf_wind_gust_kt: int | None = None
    taf_applicable_text: str | None = None
    taf_applicable_lines: list[int] = Field(default_factory=list)
    metar_wind_advisory: str | None = None
    metar_best_runway_id: str | None = None
    metar_crosswind_kt: float | None = None
    metar_headwind_kt: float | None = None
    taf_wind_advisory: str | None = None
    taf_best_runway_id: str | None = None
    taf_crosswind_kt: float | None = None
    taf_headwind_kt: float | None = None
    has_metar: bool = False
    has_taf: bool = False
    eta_hour_offset: int | None = None  # rounded hours after departure


class ObservationComparison(BaseModel):
    """Comparison of one airport's observations vs nearest model prediction."""

    icao: str
    obs_category: str | None = None
    model_category: str | None = None
    category_match: str  # "CONFIRMING" / "SIGNIFICANT" / "CONFLICTING"
    ceiling_delta_ft: int | None = None
    visibility_delta_m: float | None = None
    wind_speed_delta_kt: float | None = None
    model_wind_dir: float | None = None
    model_wind_speed_kt: float | None = None
    model_wind_gust_kt: float | None = None
    model_wind_advisory: str | None = None
    model_best_runway_id: str | None = None
    model_crosswind_kt: float | None = None
    wind_advisory_match: str | None = None
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


class SigmetAlongRoute(BaseModel):
    """One SIGMET intersecting the route corridor (flat, serializable).

    Retains the polygon outline, the affected enroute span and the vertical
    band so a later cross-section/map overlay of the impacted area can be
    built without re-fetching.
    """

    fir_id: str
    fir_name: str | None = None
    hazard: str | None = None  # TURB / ICE / TS / MTW / VA ...
    qualifier: str | None = None  # SEV / EMBD / ISOL ...
    base_ft: int | None = None
    top_ft: int | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    direction: str | None = None  # movement, e.g. "NE" (None if stationary)
    speed_kt: int | None = None
    raw_text: str = ""
    # Route-intersection metadata from RouteSigmetService.
    matched_firs: list[str] = Field(default_factory=list)
    min_distance_nm: float | None = None
    enroute_distance_from_nm: float | None = None
    enroute_distance_to_nm: float | None = None
    # Polygon outline as (lon, lat) vertices — kept for a future
    # cross-section/map overlay of the affected area.
    coords: list[tuple[float, float]] = Field(default_factory=list)


class RouteSigmets(BaseModel):
    """SIGMETs affecting the route corridor (D-0 real-time info)."""

    corridor_nm: float
    fetch_time: datetime
    altitude_low_ft: int | None = None
    altitude_high_ft: int | None = None
    time_window_from: datetime | None = None
    time_window_to: datetime | None = None
    route_firs: list[str] = Field(default_factory=list)
    sigmets: list[SigmetAlongRoute] = Field(default_factory=list)

    @computed_field
    @property
    def count(self) -> int:
        return len(self.sigmets)

    @computed_field
    @property
    def hazards(self) -> list[str]:
        """Sorted union of hazard types across the matched SIGMETs."""
        return sorted({s.hazard for s in self.sigmets if s.hazard})

    @computed_field
    @property
    def has_severe(self) -> bool:
        return any(
            s.qualifier and s.qualifier.upper() == "SEV" for s in self.sigmets
        )


class RealtimeRefreshResult(BaseModel):
    """Combined output of the cheap D-0 real-time refresh seam: fresh
    METAR/TAF observations plus route SIGMETs (issue #167 seam, #168 SIGMET)."""

    observations: RouteObservations
    sigmets: RouteSigmets | None = None
