"""Pydantic v2 models for the route advisory system.

Route advisories evaluate conditions across all route points to produce
deterministic GREEN/AMBER/RED assessments per advisory per model.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

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
        """Return the most severe status, ignoring UNAVAILABLE.

        Nothing to grade — an empty list, or every input UNAVAILABLE — is
        UNAVAILABLE, never GREEN. Absent data is not a clear sky, and the
        callers that hand us an empty list (an evaluator with no airport
        domain, say) mean "we could not assess this", not "we assessed it
        and it is fine".
        """
        _ORDER = [cls.GREEN, cls.AMBER, cls.RED]
        valid = [s for s in statuses if s in _ORDER]
        if not valid:
            return cls.UNAVAILABLE
        result = cls.GREEN
        for s in valid:
            if _ORDER.index(s) > _ORDER.index(result):
                result = s
        return result

    @classmethod
    def majority(cls, statuses: list[AdvisoryStatus]) -> AdvisoryStatus:
        """Return the most common status; ties broken by worst among tied.

        UNAVAILABLE values are ignored. If all are UNAVAILABLE or empty,
        returns UNAVAILABLE — see :meth:`worst`.
        """
        _ORDER = [cls.GREEN, cls.AMBER, cls.RED]
        valid = [s for s in statuses if s in _ORDER]
        if not valid:
            return cls.UNAVAILABLE
        counts: dict[AdvisoryStatus, int] = {}
        for s in valid:
            counts[s] = counts.get(s, 0) + 1
        max_count = max(counts.values())
        tied = [s for s, c in counts.items() if c == max_count]
        return cls.worst(tied)


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


class HighlightSeverity(str, Enum):
    """Severity of a highlight element (scrim cutout / ribbon segment).

    A superset of the amber/red used on scrim cutouts: the ribbon partition
    also carries GREEN ("checked, clear here") and UNAVAILABLE ("no sounding
    for this model here"). Distinct from :class:`AdvisoryStatus` because the
    highlight is a *per-point / per-region* verdict, not the route-level grade.
    """

    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    UNAVAILABLE = "unavailable"


class RibbonSegment(BaseModel):
    """One run of the 1-D route verdict. Segments tile ``[0, total_nm]`` exactly.

    The ribbon grades the whole route along the x-axis — where along the route
    this advisory's verdict is green/amber/red/unavailable — as a gapless
    partition so that GREEN is never inferred from silence (an UNAVAILABLE gap
    must not render as "clear"). Distance-space (``nm``), same convention as
    :class:`MitigationSegment`.
    """

    dist_from_nm: float
    dist_to_nm: float
    severity: HighlightSeverity


class HighlightRegion(BaseModel):
    """One 2-D scrim cutout — where the hazard physically is. Flagged areas only.

    A region is a spotlight the scrim punches out of its dim wash, framed by a
    severity-colored outline. Emitted only for flagged (AMBER/RED) areas — a
    clean chart gets no scrim at all. ``base_ft``/``top_ft`` both ``None`` means
    a full column (terrain-to-top), e.g. a depth-unresolved convective ghost.
    """

    dist_from_nm: float
    dist_to_nm: float
    base_ft: int | None = None   # None (both) = full column
    top_ft: int | None = None
    kind: str                    # stable machine token, e.g. "cruise_imc", "tower"
    severity: HighlightSeverity  # amber | red (cutouts are only emitted for flagged areas)
    # Stable, non-localised provenance tokens (#393). All additive and
    # legacy-safe — old packs deserialize with them absent:
    #   ``reason_code`` — the machine token for *why* this region fired (the
    #     evaluator's per-point reason, e.g. "no_escape", "severe_cat"). Distinct
    #     from ``kind`` (the geometry class) — several reasons can share a kind.
    #   ``metric_id`` — the cross-section layer this region localises to, so a
    #     chip can jump the user to the *right* layer rather than "the chart".
    #   ``method_id`` — the analysis method that produced the region's evidence:
    #     the *effective* one, so it names the method that actually ran rather
    #     than the one the user asked for (#408). Values are the `*_method_effective`
    #     tokens — clouds "dd" / "nwp" / "nwp_synthesized", icing "ogimet_dd" /
    #     "ogimet_nwp" / "sfip_nwp", convection "nwp" / "thermo". Never a compound
    #     token: when the convective thermo floor raises a quiet NWP grade it moves
    #     the severity, not the source, so the region still reads "nwp" — the
    #     geometry a chip draws is the NWP track's. ``None`` for the evaluators
    #     with no user-selectable method axis (turbulence, mountain_wind, precip).
    reason_code: str | None = None
    metric_id: str | None = None
    method_id: str | None = None


class AdvisoryHighlights(BaseModel):
    """Cross-section highlight geometry for one advisory evaluated against one model.

    Two elements, each doing exactly one job (issue #373):

    - ``ribbon`` — a gapless 1-D partition of ``[0, total_nm]`` (the route
      verdict along the x-axis).
    - ``regions`` — 2-D scrim cutouts for flagged areas only (where the hazard
      physically is).

    Backend owns the geometry (evaluator logic decides *which* zones fired), so
    web and iOS never re-derive thresholds/altitude buffers client-side. Old
    packs deserialize with ``highlights=None`` on :class:`ModelAdvisoryResult`;
    both lists default empty. Payload cost is negligible (~20 route points).
    """

    ribbon: list[RibbonSegment] = Field(default_factory=list)
    regions: list[HighlightRegion] = Field(default_factory=list)
    peak_dist_nm: float | None = None  # optional "worst point" for jump-to-worst


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
    # Curation tier driving progressive disclosure on the settings page (#387).
    # "pilot"    — a personal-minimum / aircraft-capability choice, rendered
    #              inline on the advisory row (high value, per-pilot/per-plane).
    # "advanced" — meteorological calibration; hidden behind the collapsed
    #              "Advanced" expander. Default so newly added params are
    #              safe-by-default (never leak into the compact view unasked).
    # Deliberately exactly two values — this is a curation mechanism, not a tag
    # taxonomy or a user-facing filter.
    audience: Literal["pilot", "advanced"] = "advanced"


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


class InterviewOption(BaseModel):
    """One answer to a setup-interview question (#387, slice 3).

    An option is a declarative patch over advisory settings: which advisories it
    enables/disables and which parameter values it sets. Applying an option
    writes ONLY the keys it declares. By construction every option of a question
    declares the same key set (so switching answers is reversible), and sibling
    questions declare *disjoint* keys (so answers never conflict on merge).
    """

    id: str
    label: str
    description: str = ""
    # {advisory_id: enabled}
    enabled: dict[str, bool] = Field(default_factory=dict)
    # {advisory_id: {param_key: value}}
    params: dict[str, dict[str, float]] = Field(default_factory=dict)


class InterviewQuestion(BaseModel):
    """One setup-interview question — an ordered set of mutually exclusive options."""

    id: str
    title: str
    help: str = ""
    options: list[InterviewOption] = Field(default_factory=list)


class Interview(BaseModel):
    """The declarative setup-interview structure served to clients (#387).

    Ordered questions; each option carries the patch it would apply. Lives in the
    advisories package (backend) so web and iOS share one definition. The client
    stores the chosen answers under ``settings_json.interview`` for idempotent
    re-runs.
    """

    questions: list[InterviewQuestion] = Field(default_factory=list)


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
    # Per-model mitigations: alternative/mitigating decisions that would improve
    # a flagged sub-issue (advice only — never alters ``status``). Defaults empty
    # so old packs deserialize cleanly.
    mitigations: list[Mitigation] = Field(default_factory=list)
    # Per-model cross-section highlight geometry (scrim regions + verdict ribbon,
    # issue #373). None on old packs and for evaluators that don't emit it. Never
    # affects the grade — it locates *where* the verdict comes from.
    highlights: AdvisoryHighlights | None = None
    # Stable id of the analysis method that actually controlled this model's
    # grade (#393) — ``sfip``, ``ogimet_nwp``, ``dewpoint_depression``, or a
    # compound like ``nwp_with_dd_floor``. This is what a method badge on the
    # chip must source from — NOT the user's selected method in settings, which
    # may differ from the one that drove the result. None when the evaluator has
    # no selectable method, and on old packs (legacy-safe).
    primary_method_id: str | None = None

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
        highlights: AdvisoryHighlights | None = None,
        primary_method_id: str | None = None,
        affected_nm: float | None = None,
    ) -> ModelAdvisoryResult:
        """Build a result, computing pct and nm from point counts.

        ``affected_mod`` is an optional higher-threshold count (e.g. convective
        MODERATE+); its pct/nm are derived the same way as the primary extent.

        ``affected_nm`` is an optional geometry-accurate extent (#393): when the
        evaluator derives grade and geometry from one ``EvidenceSample`` list, it
        passes the midpoint-owned-cell distance of the *affected* points here. It
        cannot then contradict ``affected_pct`` because both count the same
        samples — the drift that reverted the earlier ribbon-derived attempt
        (#391) is gone once one predicate feeds both. When omitted, ``affected_nm``
        falls back to the proportional ``total_distance_nm * affected / total``.
        """
        # Explicit None check (not `or 0`) so a future caller can pass a genuine
        # 0 meaning "tracked this threshold, zero points qualified" (#302 review).
        mod = affected_mod if affected_mod is not None else 0

        # affected_nm is the nm form of affected_points and must stay consistent
        # with affected_pct — both key off the same ``affected`` count. Under the
        # single-assessment refactor (#393) the evaluator may pass a
        # geometry-accurate ``affected_nm`` (midpoint-owned cells of the affected
        # points); it stays consistent because it counts the same samples that
        # produced ``affected``. Absent it, we fall back to the proportion.
        proportional_nm = round(total_distance_nm * affected / total, 1) if total > 0 else 0
        return cls(
            model=model,
            status=status,
            detail=detail,
            affected_points=affected,
            total_points=total,
            affected_pct=round(100 * affected / total, 1) if total > 0 else 0,
            affected_nm=round(affected_nm, 1) if affected_nm is not None else proportional_nm,
            total_nm=round(total_distance_nm, 1),
            affected_mod_points=mod,
            affected_mod_pct=round(100 * mod / total, 1) if total > 0 else 0,
            affected_mod_nm=round(total_distance_nm * mod / total, 1) if total > 0 else 0,
            cross_check=cross_check,
            mitigations=mitigations if mitigations is not None else [],
            highlights=highlights,
            primary_method_id=primary_method_id,
        )


def _aggregate_mitigations(
    per_model: list[ModelAdvisoryResult],
    agg_status: AdvisoryStatus,
) -> list[Mitigation]:
    """Aggregate per-model mitigations (representative-model policy).

    Returns the mitigations of the first per-model result whose status equals
    the aggregate status — the same representative used to choose
    ``aggregate_detail`` — else an empty list. Kept as a standalone module-level
    function so the policy can later be swapped for a "conservative,
    all-or-nothing per kind" merge by editing this one place.
    """
    for m in per_model:
        if m.status == agg_status:
            return list(m.mitigations)
    return []


class RouteAdvisoryResult(BaseModel):
    """Result of one advisory evaluated across all models."""

    advisory_id: str
    aggregate_status: AdvisoryStatus
    aggregate_detail: str = ""
    per_model: list[ModelAdvisoryResult] = Field(default_factory=list)
    parameters_used: dict[str, float] = Field(default_factory=dict)
    # Aggregate mitigations chosen by ``_aggregate_mitigations`` (representative
    # model). Advice only — never alters ``aggregate_status``. Defaults empty so
    # old packs deserialize cleanly.
    aggregate_mitigations: list[Mitigation] = Field(default_factory=list)
    # The model whose per-model result sources the aggregate view (#393): the
    # first ``per_model`` entry whose status equals ``aggregate_status`` — the
    # same representative that sets ``aggregate_detail`` / ``aggregate_mitigations``.
    # Emitted so the client reads "which model's geometry do we highlight?" from
    # the backend instead of reimplementing the rule (the deleted TS
    # ``representativeModel()`` copy). None only when there are no per-model
    # results, and on old packs (legacy-safe).
    representative_model: str | None = None

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
            aggregate_mitigations=_aggregate_mitigations(per_model, agg),
            representative_model=representative.model if representative else None,
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
