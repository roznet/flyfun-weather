"""Pydantic v2 models for flights, briefing packs, and flight profiles (API/storage layer)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, computed_field


class FlightProfile(BaseModel):
    """A named set of flight parameters and advisory settings."""

    id: int
    user_id: str = ""
    name: str = "Default"
    is_default: bool = False
    settings: dict = Field(default_factory=dict)
    system_template_key: str | None = None
    created_at: datetime
    updated_at: datetime


class Flight(BaseModel):
    """A saved briefing target — route + date/time specifics."""

    id: str  # slug: "{route_name}-{YYYY-MM-DD}-{hash}"
    user_id: str = ""  # owner; empty in single-user / dev mode
    profile_id: int | None = None  # associated flight profile
    route_name: str  # user-assigned name or derived from waypoints
    waypoints: list[str] = Field(default_factory=list)  # airports, navaids, or fixes
    departure_time: datetime  # aware UTC datetime
    cruise_altitude_ft: int = 8000
    flight_ceiling_ft: int = 18000
    flight_duration_hours: float = 0.0
    private: bool = False
    alt_departure_time: datetime | None = None  # optional same-day alt departure
    auto_refresh: bool = False
    auto_refresh_hour: int | None = None
    last_auto_refresh_at: datetime | None = None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def target_date(self) -> str:
        """YYYY-MM-DD derived from departure_time (backward compat)."""
        return self.departure_time.strftime("%Y-%m-%d")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def target_time_utc(self) -> int:
        """Departure hour derived from departure_time (backward compat)."""
        return self.departure_time.hour


class BriefingPackMeta(BaseModel):
    """Metadata for one fetch — lightweight index for history listing."""

    id: int | None = None  # DB primary key (auto-generated)
    flight_id: str
    fetch_timestamp: datetime  # aware UTC datetime
    days_out: int
    has_gramet: bool = False
    has_skewt: bool = False
    has_digest: bool = False
    assessment: Optional[str] = None  # GREEN/AMBER/RED from digest
    assessment_reason: Optional[str] = None
    artifact_path: str = ""  # path to pack directory
    model_init_times: dict[str, int] = Field(default_factory=dict)
    grib_init_times: dict[str, int] = Field(default_factory=dict)
    models_skipped_region: list[str] = Field(default_factory=list)
    diagnostics: list[dict] = Field(default_factory=list)
    alt_assessment: Optional[str] = None  # GREEN/AMBER/RED for alt departure
    alt_assessment_reason: Optional[str] = None
    has_alt_advisories: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_historical(self) -> bool:
        """True when the briefing was generated for a past departure date."""
        return self.days_out < 0
