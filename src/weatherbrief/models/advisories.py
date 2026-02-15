"""Pydantic v2 models for the route advisory system.

Route advisories evaluate conditions across all route points to produce
deterministic GREEN/AMBER/RED assessments per advisory per model.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class AdvisoryStatus(str, Enum):
    """Route advisory status level."""

    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    UNAVAILABLE = "unavailable"


class AdvisoryParameterDef(BaseModel):
    """Definition of a user-tunable parameter for an advisory."""

    key: str
    label: str
    description: str
    type: str  # "number", "percent", "altitude", "speed", "boolean"
    unit: str = ""
    default: float
    min: float | None = None
    max: float | None = None
    step: float | None = None


class AdvisoryCatalogEntry(BaseModel):
    """Metadata for one advisory type — enough for the frontend to render controls."""

    id: str
    name: str
    short_description: str
    description: str
    category: str  # e.g. "icing", "cloud", "turbulence", "convective", "model"
    default_enabled: bool = True
    parameters: list[AdvisoryParameterDef] = Field(default_factory=list)


class ModelAdvisoryResult(BaseModel):
    """Result of one advisory evaluated against one model's data."""

    model: str
    status: AdvisoryStatus
    detail: str = ""
    affected_points: int = 0
    total_points: int = 0
    affected_pct: float = 0.0
    affected_nm: float = 0.0
    total_nm: float = 0.0


class RouteAdvisoryResult(BaseModel):
    """Result of one advisory evaluated across all models."""

    advisory_id: str
    aggregate_status: AdvisoryStatus
    aggregate_detail: str = ""
    per_model: list[ModelAdvisoryResult] = Field(default_factory=list)
    parameters_used: dict[str, float] = Field(default_factory=dict)


class RouteAdvisoriesManifest(BaseModel):
    """Top-level container for all route advisory results."""

    advisories: list[RouteAdvisoryResult] = Field(default_factory=list)
    catalog: list[AdvisoryCatalogEntry] = Field(default_factory=list)
    route_name: str = ""
    cruise_altitude_ft: int = 0
    flight_ceiling_ft: int = 0
    total_distance_nm: float = 0.0
    models: list[str] = Field(default_factory=list)
