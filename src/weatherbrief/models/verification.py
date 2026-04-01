"""Pydantic models for the METAR/TAF verification system."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field


class VerificationObservation(BaseModel):
    """METAR/TAF ground truth for one airport at one time."""

    icao: str
    observation_time: datetime
    collected_at: datetime

    # METAR fields
    metar_raw: str | None = None
    flight_category: str | None = None  # VFR/MVFR/IFR/LIFR
    ceiling_ft: int | None = None
    visibility_m: int | None = None
    wind_dir: int | None = None
    wind_speed_kt: int | None = None
    wind_gust_kt: int | None = None
    temperature_c: int | None = None
    dewpoint_c: int | None = None
    qnh: float | None = None
    weather: list[str] = Field(default_factory=list)

    # TAF fields (active at observation_time)
    taf_raw: str | None = None
    taf_applicable: str | None = None
    taf_issue_time: datetime | None = None
    taf_flight_category: str | None = None
    taf_ceiling_ft: int | None = None
    taf_visibility_m: int | None = None
    taf_wind_dir: int | None = None
    taf_wind_speed_kt: int | None = None
    taf_wind_gust_kt: int | None = None

    def weather_json(self) -> str:
        """Serialize weather list to JSON for DB storage."""
        return json.dumps(self.weather)

    @staticmethod
    def weather_from_json(raw: str | None) -> list[str]:
        """Deserialize weather JSON from DB."""
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []


class VerificationSummary(BaseModel):
    """Aggregated accuracy stats (for export / display)."""

    total_observations: int = 0
    total_scores: int = 0
    flights_tracked: int = 0
    airports_tracked: int = 0
