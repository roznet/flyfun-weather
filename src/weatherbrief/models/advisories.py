"""Pydantic v2 models for the route advisory system.

Route advisories evaluate conditions across all route points to produce
deterministic GREEN/AMBER/RED assessments per advisory per model.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

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
        order = [cls.GREEN, cls.AMBER, cls.RED]
        valid = [status for status in statuses if status in order]
        if not valid:
            return cls.UNAVAILABLE
        return max(valid, key=order.index)

    @classmethod
    def majority(cls, statuses: list[AdvisoryStatus]) -> AdvisoryStatus:
        """Return the most common status; ties broken by worst among tied.

        UNAVAILABLE values are ignored. If all are UNAVAILABLE or empty,
        returns UNAVAILABLE.
        """
        order = [cls.GREEN, cls.AMBER, cls.RED]
        valid = [s for s in statuses if s in order]
        if not valid:
            return cls.UNAVAILABLE
        counts: dict[AdvisoryStatus, int] = {}
        for s in valid:
            counts[s] = counts.get(s, 0) + 1
        max_count = max(counts.values())
        tied = [s for s, c in counts.items() if c == max_count]
        return cls.worst(tied)


class AdvisoryEvidenceRegion(BaseModel):
    """One inclusive along-route evidence region for an advisory result."""

    start_point_index: int
    end_point_index: int
    lower_altitude_ft: int | None = None
    upper_altitude_ft: int | None = None
    severity: AdvisoryStatus
    reason_code: str
    metric_id: str | None = None
    method_id: str | None = None

    @model_validator(mode="after")
    def _validate_geometry(self) -> AdvisoryEvidenceRegion:
        if self.start_point_index > self.end_point_index:
            raise ValueError("start_point_index must not exceed end_point_index")
        has_lower = self.lower_altitude_ft is not None
        has_upper = self.upper_altitude_ft is not None
        if has_lower != has_upper:
            raise ValueError("altitude bounds must both be present or both absent")
        if has_lower and self.lower_altitude_ft > self.upper_altitude_ft:
            raise ValueError("lower_altitude_ft must not exceed upper_altitude_ft")
        if self.severity == AdvisoryStatus.UNAVAILABLE:
            raise ValueError("evidence severity cannot be unavailable")
        if not self.reason_code.strip():
            raise ValueError("reason_code must be non-empty")
        return self


class MitigationKind(str, Enum):
    """Axis along which an advisory's flagged issue could be mitigated."""

    ALTITUDE = "altitude"             # fly a different altitude
    ROUTE_POSITION = "route_position"  # climb/descend at a different point along the route
    TIMING = "timing"                 # reserved for future use


class MitigationSegment(BaseModel):
    """One along-route band of a mitigation's vertical profile (issue #335).

    ``(dist_from_nm, dist_to_nm)`` is the along-track extent flown at ``altitude_ft``.
    Lives in the cross-section's native ``(distance × altitude)`` space so the
    varying-altitude overlay can render a mitigation profile directly.
    """

    dist_from_nm: float
    dist_to_nm: float
    altitude_ft: int


class MitigationTransition(BaseModel):
    """A climb/descent between two adjacent bands of a mitigation profile (issue #335)."""

    from_nm: float
    to_nm: float
    from_altitude_ft: int
    to_altitude_ft: int


class MitigationProfile(BaseModel):
    """The full vertical profile behind a mitigation — bands + transitions (issue #335).

    Optional and additive: v1 renders only the flat one-line ``detail`` on
    :class:`Mitigation`, while this carries the structured profile the shared
    vertical-profile solver produced, ready for the cross-section overlay. Old packs
    (no profile) deserialize with ``profile=None``.
    """

    segments: list[MitigationSegment] = Field(default_factory=list)
    transitions: list[MitigationTransition] = Field(default_factory=list)


class Mitigation(BaseModel):
    """A decision that could improve a flagged sub-issue — advice only.

    A mitigation never changes the advisory's grade (same contract as
    ``cross_check``). It reports the status **of the specific sub-issue it
    addresses** if applied (``mitigated_status``), NOT the overall advisory
    status — an advisory grades several axes via ``worst`` and a mitigation
    on one axis says nothing about the others. The ``addresses`` tag is a
    stable English machine token (not localized); human phrasing lives in the
    localized ``detail``.
    """

    kind: MitigationKind
    addresses: str                    # stable tag of the sub-issue, e.g. "cruise_imc",
                                      # "climb_deck", "descent_deck" (NOT localized)
    detail: str                       # localized human phrasing (via adv_t)
    mitigated_status: AdvisoryStatus  # status OF THE ADDRESSED ISSUE if applied —
                                      # NOT the advisory overall status
    altitude_ft: int | None = None    # set for ALTITUDE
    distance_nm: float | None = None  # set for ROUTE_POSITION
    reference: str | None = None      # e.g. "departure" / "arrival" — disambiguates distance
    # Optional structured profile from the shared vertical-profile solver (#335).
    # Additive/backward-compatible: v1 UIs render ``detail``; the cross-section overlay
    # consumes ``profile``. None on old packs and on non-solver mitigations.
    profile: MitigationProfile | None = None


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
    # Timing-scenario participation (timing-scenario-plan.md). Declarative
    # sibling of ``altitude_dependent`` — the scan asks the registry, so a new
    # evaluator auto-participates. The unifying principle: scan-worthy ⟺
    # GRIB-dependent ⟺ OM-insufficient.
    #   "scan"  — timing-sensitive and GRIB-dependent; ranks the scan margin
    #   "cheap" — timing-sensitive but OM-sufficient; never drives the scan
    #   "none"  — timing is not the lever (default)
    timing_class: Literal["scan", "cheap", "none"] = "none"
    # Participates in the "set Flexibility to scan for a better window" hint on
    # flights with Flexibility=None even when not scan-class (e.g. airport
    # fog/ceiling burn-off is OM/TAF-graded but a classic timing case). The
    # hint set is scan-class ∪ {timing_hint=True}.
    timing_hint: bool = False
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
    # Additive evidence/provenance metadata. ``None`` means legacy/unknown and
    # must not be interpreted as complete data.
    data_state: Literal["complete", "partial", "unavailable"] | None = None
    primary_method_id: str | None = None
    evidence_regions: list[AdvisoryEvidenceRegion] = Field(default_factory=list)
    # Per-model mitigations: alternative/mitigating decisions that would improve
    # a flagged sub-issue (advice only — never alters ``status``). Defaults empty
    # so old packs deserialize cleanly.
    mitigations: list[Mitigation] = Field(default_factory=list)

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
        mitigations: list[Mitigation] | None = None,
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
            mitigations=mitigations if mitigations is not None else [],
        )


def _aggregate_mitigations(
    representative: ModelAdvisoryResult | None,
) -> list[Mitigation]:
    """Aggregate per-model mitigations (representative-model policy).

    Returns the mitigations of the representative used to choose
    ``aggregate_detail`` and ``representative_model``, else an empty list. Kept
    as a standalone module-level
    function so the policy can later be swapped for a "conservative,
    all-or-nothing per kind" merge by editing this one place.
    """
    return list(representative.mitigations) if representative else []


class RouteAdvisoryResult(BaseModel):
    """Result of one advisory evaluated across all models."""

    advisory_id: str
    aggregate_status: AdvisoryStatus
    aggregate_detail: str = ""
    representative_model: str | None = None
    per_model: list[ModelAdvisoryResult] = Field(default_factory=list)
    parameters_used: dict[str, float] = Field(default_factory=dict)
    # Aggregate mitigations chosen by ``_aggregate_mitigations`` (representative
    # model). Advice only — never alters ``aggregate_status``. Defaults empty so
    # old packs deserialize cleanly.
    aggregate_mitigations: list[Mitigation] = Field(default_factory=list)

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
            representative_model=representative.model if representative else None,
            per_model=per_model,
            parameters_used=params,
            aggregate_mitigations=_aggregate_mitigations(representative),
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
