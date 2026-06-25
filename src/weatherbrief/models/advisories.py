"""Pydantic v2 models for the route advisory system.

Route advisories evaluate conditions across all route points to produce
deterministic GREEN/AMBER/RED assessments per advisory per model.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from weatherbrief.models.airport_conditions import AirportConditions  # noqa: F401


class AdvisoryAggregation(str, Enum):
    """How per-model statuses are combined into an aggregate."""

    WORST = "worst"
    MAJORITY = "majority"


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

    @classmethod
    def majority(cls, statuses: list[AdvisoryStatus]) -> AdvisoryStatus:
        """Return the most common status; ties broken by worst among tied.

        UNAVAILABLE values are ignored. If all are UNAVAILABLE or empty,
        returns GREEN.
        """
        _ORDER = [cls.GREEN, cls.AMBER, cls.RED]
        valid = [s for s in statuses if s in _ORDER]
        if not valid:
            return cls.GREEN
        counts: dict[AdvisoryStatus, int] = {}
        for s in valid:
            counts[s] = counts.get(s, 0) + 1
        max_count = max(counts.values())
        tied = [s for s, c in counts.items() if c == max_count]
        return cls.worst(tied)


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
    altitude_dependent: bool = False
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
    # Optional second extent at a higher threshold (e.g. convective MODERATE+ vs
    # the LOW-floor primary extent), so the aggregate can anchor its headline on
    # actual concern rather than the min-threshold union (#300). Stays 0 for
    # evaluators that don't populate it; convective sets it explicitly.
    affected_mod_points: int = 0
    affected_mod_pct: float = 0.0
    affected_mod_nm: float = 0.0
    # Optional details-only metadata: divergence between the chosen method and
    # an independent second derivation (e.g. convective DD-vs-model scheme).
    # Never affects the grade; surfaced only in the info popup and LLM digest.
    cross_check: str | None = None

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
        affected_mod: int | None = None,
        cross_check: str | None = None,
    ) -> ModelAdvisoryResult:
        """Build a result, computing pct and nm from point counts.

        ``affected_mod`` is an optional higher-threshold count (e.g. convective
        MODERATE+); its pct/nm are derived the same way as the primary extent.
        """
        # Explicit None check (not `or 0`) so a future caller can pass a genuine
        # 0 meaning "tracked this threshold, zero points qualified" (#302 review).
        mod = affected_mod if affected_mod is not None else 0
        return cls(
            model=model,
            status=status,
            detail=detail,
            affected_points=affected,
            total_points=total,
            affected_pct=round(100 * affected / total, 1) if total > 0 else 0,
            affected_nm=round(total_distance_nm * affected / total, 1) if total > 0 else 0,
            total_nm=round(total_distance_nm, 1),
            affected_mod_points=mod,
            affected_mod_pct=round(100 * mod / total, 1) if total > 0 else 0,
            affected_mod_nm=round(total_distance_nm * mod / total, 1) if total > 0 else 0,
            cross_check=cross_check,
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
        aggregation: AdvisoryAggregation = AdvisoryAggregation.MAJORITY,
    ) -> RouteAdvisoryResult:
        """Build aggregate result from per-model results.

        Aggregation mode controls how per-model statuses combine:
        - MAJORITY: most common status; ties broken by worst among tied (default)
        - WORST: most severe status wins
        """
        statuses = [m.status for m in per_model]
        if aggregation == AdvisoryAggregation.MAJORITY:
            agg = AdvisoryStatus.majority(statuses)
        else:
            agg = AdvisoryStatus.worst(statuses)
        representative = next(
            (m for m in per_model if m.status == agg),
            per_model[0] if per_model else None,
        )
        return cls(
            advisory_id=advisory_id,
            aggregate_status=agg,
            aggregate_detail=representative.detail if representative else "",
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
    aggregation: str = "worst"
    airport_conditions: AirportConditions | None = None


class AltitudeAdvisoryRow(BaseModel):
    """One row in the altitude table — advisory statuses at a single altitude."""

    altitude_ft: int
    statuses: dict[str, AdvisoryStatus]  # advisory_id → aggregate status
    red_count: int = 0
    amber_count: int = 0
    green_count: int = 0


class AltitudeTableResult(BaseModel):
    """Result of sweeping altitude-dependent advisories across an altitude range."""

    rows: list[AltitudeAdvisoryRow]  # sorted by altitude descending
    advisory_ids: list[str]  # column order
    advisory_names: dict[str, str]  # advisory_id → display name
    cruise_altitude_ft: int
    flight_ceiling_ft: int
    step_ft: int
    best_below_cruise: int | None = None  # altitude_ft with best score below cruise
    best_above_cruise: int | None = None  # altitude_ft with best score at/above cruise


class AltitudeAdvisoryChange(BaseModel):
    """One advisory's status change between two altitudes."""

    advisory_id: str
    name: str
    from_status: AdvisoryStatus
    to_status: AdvisoryStatus


class AltitudeAdvisoryDelta(BaseModel):
    """Per-advisory diff between a baseline altitude row and a candidate row.

    ``improved`` = severity decreased (e.g. RED→AMBER); ``worsened`` =
    severity increased. UNAVAILABLE statuses are ignored on either side.
    """

    improved: list[AltitudeAdvisoryChange] = Field(default_factory=list)
    worsened: list[AltitudeAdvisoryChange] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True when nothing improved or worsened (note may be omitted)."""
        return not self.improved and not self.worsened
