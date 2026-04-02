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


# ---------------------------------------------------------------------------
# Digest models
# ---------------------------------------------------------------------------


class ActivitySummary(BaseModel):
    """High-level counts for a date range."""

    flights_verified: int = 0
    flights_completed: int = 0
    airports_observed: int = 0
    observations_collected: int = 0
    cycles_run: int = 0
    avg_cycle_duration_ms: float | None = None


class CategoryAccuracyRow(BaseModel):
    """One cell in the model × days-out accuracy table."""

    model: str
    days_out: int
    accuracy_pct: float | None = None
    sample_count: int = 0


class NotableMiss(BaseModel):
    """A significant flight-category bust."""

    icao: str
    observation_time: datetime
    model: str
    days_out: int
    obs_category: str
    model_category: str
    ceiling_delta_ft: int | None = None


class WindAdvisoryStats(BaseModel):
    """Per-model wind advisory match rate."""

    model: str
    accuracy_pct: float | None = None
    sample_count: int = 0


class MissedWarning(BaseModel):
    """A wind WARNING that a model failed to predict."""

    icao: str
    observation_time: datetime
    model: str
    obs_wind_advisory: str
    model_wind_advisory: str


class MAERow(BaseModel):
    """Mean absolute error per model/days-out (web-only)."""

    model: str
    days_out: int
    ceiling_mae_ft: float | None = None
    visibility_mae_m: float | None = None
    wind_speed_mae_kt: float | None = None
    temperature_mae_c: float | None = None
    sample_count: int = 0


class VerificationDigestData(BaseModel):
    """Complete digest payload for email and web dashboard."""

    period_label: str = ""
    activity: ActivitySummary = Field(default_factory=ActivitySummary)
    category_accuracy_today: list[CategoryAccuracyRow] = Field(default_factory=list)
    category_accuracy_7d: list[CategoryAccuracyRow] = Field(default_factory=list)
    notable_misses: list[NotableMiss] = Field(default_factory=list)
    wind_advisory: list[WindAdvisoryStats] = Field(default_factory=list)
    missed_warnings: list[MissedWarning] = Field(default_factory=list)
    mae_stats: list[MAERow] = Field(default_factory=list)
