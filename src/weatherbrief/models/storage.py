"""Pydantic v2 models for flights and briefing packs (API/storage layer)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Flight(BaseModel):
    """A saved briefing target — route + date/time specifics."""

    id: str  # slug: "{route_name}-{target_date}"
    user_id: str = ""  # owner; empty in single-user / dev mode
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

    id: int | None = None  # DB primary key (auto-generated)
    flight_id: str
    fetch_timestamp: str  # ISO datetime
    days_out: int
    has_gramet: bool = False
    has_skewt: bool = False
    has_digest: bool = False
    assessment: Optional[str] = None  # GREEN/AMBER/RED from digest
    assessment_reason: Optional[str] = None
    artifact_path: str = ""  # path to pack directory
    model_init_times: dict[str, int] = Field(default_factory=dict)
