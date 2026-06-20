"""Pydantic models for the per-briefing front-detection artifact.

These are the serialization boundary for ``route_fronts.json`` — the data half
of the Hewson front-detection feature (issue #195, Part 1). The compute layer
(``weatherbrief.frontal.route_sampling``) works in frozen dataclasses; the
``run_fronts`` stage projects those onto these models so the artifact has a
validated, typed schema that the endpoint serves and Part 2 (#196) consumes.

The active :class:`~weatherbrief.frontal.gates.FrontGateConfig` is stamped in as
a plain dict (``gate_config``) so there is a single source of truth for the
recipe — the dataclass — rather than a mirrored Pydantic copy that could drift.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FrontCrossingModel(BaseModel):
    """A front the track crosses (accepted on-track candidate)."""

    lat: float
    lon: float
    distance_km: float
    gradient: float            # |∇θe|  K/100km
    neg_laplacian: float       # −∇²θe  K/(100km)²  (diagnostic)
    advection: float           # −V·∇θe  K/h
    tfp_before: float
    tfp_after: float
    delta_theta_e: float       # θe jump across the window, K
    kind: str                  # "cold" | "warm" | "quasi-stationary"
    intensity: str             # "significant" | "classical" | "sharp"
    # Relevance enrichment (None until the pipeline stage attaches it). The θe
    # boundary's level is not the weather's level — ``weather_top_ft`` is what a
    # pilot actually encounters (e.g. convective towers above an overflown front).
    co_location: str | None = None       # "dry"|"partly"|"wet"|"convective"
    weather_top_ft: float | None = None  # cloud/convective top at the crossing, ft
    persistence: float | None = None     # [0,1] fraction of ±window frames the gate holds
    # Vertical coherence: how many of this model's stored levels (925/850/700)
    # detect this same along-route feature. A real front slopes through several
    # levels; a single-level (==1) detection is shallow/suspect. Stamped across
    # levels by the pipeline (compute_route_fronts); None on the bare detector.
    vertical_levels: int | None = None


class FrontChainNodeModel(BaseModel):
    """One pressure level's view of a vertically-linked front (a chain node)."""

    level_hPa: int             # 925 / 850 / 700
    distance_km: float         # along-route position of the crossing at this level
    kind: str                  # "cold" | "warm" | "quasi-stationary"
    intensity: str             # "significant" | "classical" | "sharp"
    gradient: float            # |∇θe|  K/100km
    delta_theta_e: float       # signed θe jump across the window, K
    # Weather co-located with this node's crossing — lets the cross-section flag a
    # convective node (towers above an overflown front) the way the vertical
    # markers do. None when no per-model column was available.
    co_location: str | None = None   # "dry"|"partly"|"wet"|"convective"


class FrontChainModel(BaseModel):
    """A single physical front linked across pressure levels (issue #196).

    A front is a sloping 3-D surface: the same air-mass boundary shows up at
    ~the same along-route distance on 925 / 850 / 700, displaced toward the cold
    air with height. ``nodes`` are the per-level crossings the linker associated
    into one feature, ordered bottom→top (925→850→700). ``n_levels`` is the depth
    (1 = shallow / single-level / suspect; ≥2 = a front that slopes through the
    GA flight band). ``tilt`` describes the geometry the cross-section draws:
    ``coldward`` is the meteorological norm (sloping back over cold air),
    ``upright`` is near-vertical, ``warmward`` is anomalous (occlusion or coarse-
    data noise). The cross-section connects the nodes into one slanted line.
    """

    nodes: list[FrontChainNodeModel] = Field(default_factory=list)
    kind: str                  # consensus kind across nodes
    n_levels: int              # distinct levels in the chain (depth)
    tilt: str = "upright"      # "coldward" | "upright" | "warmward"


class FrontProximityModel(BaseModel):
    """Nearest gated front to the route (on- or off-track) + approach sense."""

    distance_km: float
    lat: float
    lon: float
    gradient: float
    delta_theta_e: float
    on_track: bool
    trend: str                 # "closing" | "receding" | "steady" | "unknown"
    closing_km_per_h: float | None = None


class FrontDecisionModel(BaseModel):
    """One candidate's verdict — the rejection trace for calibration/debugging."""

    lat: float
    lon: float
    distance_km: float
    gradient: float
    delta_theta_e: float
    advection: float
    accepted: bool
    rejected_by: str | None = None     # "gradient"|"anomaly"|"terrain"|"delta_theta_e"|None
    kind: str
    intensity: str
    margins: dict[str, float] = Field(default_factory=dict)


class RouteFrontAnalysisModel(BaseModel):
    """Per-route frontal picture for one model at one level/valid hour."""

    model: str
    level_hPa: int
    hour: float                # snapshot-relative forecast hour the off-track scan ran at
    crossings: list[FrontCrossingModel] = Field(default_factory=list)
    nearest: FrontProximityModel | None = None
    decisions: list[FrontDecisionModel] = Field(default_factory=list)


class RouteFrontsManifest(BaseModel):
    """Top-level ``route_fronts.json`` container.

    ``per_model`` maps each detected model to one
    :class:`RouteFrontAnalysisModel` per stored pressure level (the level
    nearest cruise is ``primary_level_hPa``; the others are kept so Part 2's
    cross-section bands have data).

    **Consumers must match each analysis by its ``level_hPa`` field, not by
    position.** ``levels`` is the union across all models; a model whose
    snapshot is partial may contribute fewer entries, so ``per_model[m]`` is
    *not* guaranteed to align positionally with ``levels``.

    ``gate_config`` is the active recipe stamp. Detection currently runs every
    level with the same threshold gates (only ``level_hPa`` varies), so this
    single dict describes all of them; if per-level presets land it would need
    keying by level.
    """

    schema_version: int = 1
    route_name: str = ""
    generated_at: datetime
    # Representative (modal) nearest-cruise level across models — kept for
    # display / back-compat. Per-model grading must use ``per_model_primary_hPa``
    # instead: a partial snapshot may expose different levels per model, so a
    # single manifest-wide value would mis-grade a model whose cruise level
    # differs (e.g. GFS 850 judged "overflown" against ECMWF's 700).
    primary_level_hPa: int                       # representative level nearest cruise
    # model → that model's own nearest-cruise level. Empty on pre-#203 packs;
    # consumers fall back to ``primary_level_hPa`` then.
    per_model_primary_hPa: dict[str, int] = Field(default_factory=dict)
    levels: list[int] = Field(default_factory=list)
    gate_config: dict = Field(default_factory=dict)
    models: list[str] = Field(default_factory=list)
    # model → analyses (one per level the model has; match by level_hPa).
    per_model: dict[str, list[RouteFrontAnalysisModel]] = Field(default_factory=dict)
    # model → vertically-linked front chains (the same boundary associated across
    # 925/850/700). Drives the cross-section's slanted front lines. Empty on
    # pre-#196-linking packs; consumers fall back to the per-level crossings.
    front_chains: dict[str, list[FrontChainModel]] = Field(default_factory=dict)
    # Models requested but with no precompute snapshot (degraded gracefully).
    models_without_snapshot: list[str] = Field(default_factory=list)
    # Snapshot init each model's detection read (model → ISO 8601 Z).
    snapshot_inits: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
