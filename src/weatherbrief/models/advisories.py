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

    @classmethod
    def worst(cls, statuses: list[AdvisoryStatus]) -> AdvisoryStatus:
        """Return the most severe status, ignoring UNAVAILABLE."""
        _ORDER = [cls.GREEN, cls.AMBER, cls.RED]
        result = cls.GREEN
        for s in statuses:
            if s in _ORDER and _ORDER.index(s) > _ORDER.index(result):
                result = s
        return result


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

    @classmethod
    def build(
        cls,
        *,
        model: str,
        status: AdvisoryStatus,
        detail: str,
        affected: int,
        total: int,
        total_distance_nm: float,
    ) -> ModelAdvisoryResult:
        """Build a result, computing pct and nm from point counts."""
        return cls(
            model=model,
            status=status,
            detail=detail,
            affected_points=affected,
            total_points=total,
            affected_pct=round(100 * affected / total, 1) if total > 0 else 0,
            affected_nm=round(total_distance_nm * affected / total, 1) if total > 0 else 0,
            total_nm=round(total_distance_nm, 1),
        )


class RouteAdvisoryResult(BaseModel):
    """Result of one advisory evaluated across all models."""

    advisory_id: str
    aggregate_status: AdvisoryStatus
    aggregate_detail: str = ""
    per_model: list[ModelAdvisoryResult] = Field(default_factory=list)
    parameters_used: dict[str, float] = Field(default_factory=dict)

    @classmethod
    def from_per_model(
        cls,
        advisory_id: str,
        per_model: list[ModelAdvisoryResult],
        params: dict[str, float],
    ) -> RouteAdvisoryResult:
        """Build aggregate result from per-model results.

        Uses worst status across models; detail comes from the worst model.
        """
        agg = AdvisoryStatus.worst([m.status for m in per_model])
        worst = next(
            (m for m in per_model if m.status == agg),
            per_model[0] if per_model else None,
        )
        return cls(
            advisory_id=advisory_id,
            aggregate_status=agg,
            aggregate_detail=worst.detail if worst else "",
            per_model=per_model,
            parameters_used=params,
        )


class RouteAdvisoriesManifest(BaseModel):
    """Top-level container for all route advisory results."""

    advisories: list[RouteAdvisoryResult] = Field(default_factory=list)
    catalog: list[AdvisoryCatalogEntry] = Field(default_factory=list)
    route_name: str = ""
    cruise_altitude_ft: int = 0
    flight_ceiling_ft: int = 0
    total_distance_nm: float = 0.0
    models: list[str] = Field(default_factory=list)
