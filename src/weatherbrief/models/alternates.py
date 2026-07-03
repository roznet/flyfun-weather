"""Weather-based alternate-airport models (issue #210).

When a destination forecast is marginal, the alternates stage surfaces the
closest airports a pilot could divert to that *fix the specific problem*
(flight category, wind, crosswind), classified as reachable **before** or
**after** the destination along the route. Each alternate's assessment is
computed by the same shared code path as the pan-European forecast map
(``analysis.airport_consensus``), so the same airport reads the same
category / crosswind / consensus in both views.

See ``designs/future/alternates.md`` for the full design.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from weatherbrief.models.alternate_requirement import (
    AlternateQual,
    AlternateRequirement,
)

# Canonical per-axis labels for the nearest-improving picks, shared by the web
# UI / text digest / LLM prompt so the wording can't drift between surfaces.
ALT_AXIS_LABELS = {
    "category": "better category",
    "wind": "lower wind",
    "crosswind": "lower crosswind",
}


class OperationalFlag(BaseModel):
    """A non-weather operational-friction signal on a divert candidate.

    A single, extensible channel: the cross-border flag (#344) is the first
    consumer; future signals (water crossing #345, military/joint-use,
    drive-time) reuse the same shape with no new rendering plumbing. Severity
    reuses the traffic-light palette but is deliberately never green — an
    operational flag only ever raises friction, it never clears it.
    """

    code: str  # stable machine key, e.g. "cross_border"
    label: str  # short chip text, e.g. "Cross-border"
    detail: str  # expandable explanation
    severity: str  # "amber" | "red" (traffic-light palette; never green)


class AlternateAirport(BaseModel):
    """One candidate divert airport with geometry, assessment and suitability."""

    icao: str
    name: str | None = None
    lat: float
    lon: float

    # --- geometry (relative to route + destination) ---
    distance_from_dest_nm: float
    enroute_distance_nm: float | None = None  # along-track position
    segment_distance_nm: float | None = None  # cross-track distance from route
    position: str  # "before" | "after"
    detour_early_nm: float | None = None  # extra miles if you divert from the start
    detour_late_nm: float | None = None  # extra miles if you fly to the dest area then divert

    # --- consensus assessment (same shape as the forecast-map consensus) ---
    flight_category: str
    wind_speed_kt: float | None = None
    crosswind_kt: float | None = None
    headwind_kt: float | None = None
    best_runway_id: str | None = None
    ceiling_ft: float | None = None
    visibility_m: float | None = None
    agreement: dict[str, str] = Field(default_factory=dict)
    per_model: dict[str, dict] = Field(default_factory=dict)  # raw per-model dicts (like map "models")

    # --- suitability ---
    has_instrument_approach: bool = False
    best_approach_type: str | None = None  # ILS/RNP/RNAV/... precision tier (minima proxy)
    longest_runway_ft: int | None = None
    has_hard_runway: bool = False
    point_of_entry: bool = False  # customs/border crossing
    iso_country: str | None = None  # ISO-3166-1 alpha-2, from euro_aip (drives cross-border flag)
    is_major: bool = False  # type == large_airport — hidden by default in the UI

    # --- operational-friction flags (#344) ---
    # Non-weather friction (cross-border customs/immigration today; water
    # crossing, military, drive-time later) surfaced on one extensible channel.
    operational_flags: list[OperationalFlag] = Field(default_factory=list)

    # --- vs-destination flags ---
    better_category: bool = False
    better_wind: bool = False
    better_crosswind: bool = False
    dominates_destination: bool = False  # better-or-equal on all axes (Pareto)

    # --- regulatory alternate-minima qualification (issue #249) ---
    # Computed from the candidate's NWP-consensus ceiling/vis + best_approach_type.
    faa: AlternateQual | None = None  # Yes/No (Likely/Unlikely)
    easa: AlternateQual | None = None  # Likely/Marginal/Unlikely


class AlternateAxisPick(BaseModel):
    """The nearest improving alternate for one deficient axis."""

    axis: str  # "category" | "wind" | "crosswind"
    icao: str | None = None  # nearest improving alternate (None if none qualifies)
    distance_from_dest_nm: float | None = None
    position: str | None = None  # "before" | "after"


class RouteAlternates(BaseModel):
    """Weather-based alternates for a route's destination (D-2 inward)."""

    destination_icao: str
    destination_category: str
    destination_crosswind_kt: float | None = None
    # Destination NWP-consensus ceiling/visibility at ETA, reduced under the
    # user's advisory aggregation preference (majority = median of the winning-
    # category pool; worst = worst across models) — an intentional coupling so
    # the alternate card and the airport arrival card agree (PR #346). This is
    # the regulatory-trigger NWP fallback (#249) used ONLY when no destination
    # TAF covers the ETA; a TAF, when present, supersedes it entirely.
    destination_ceiling_ft: float | None = None
    destination_visibility_m: float | None = None
    eta: datetime | None = None
    corridor_nm: float
    radius_nm: float
    require_approach: bool = False  # informational: destination itself is MVFR/IFR/LIFR
    approach_filter_relaxed: bool = False  # per-candidate IAP gate skipped (no procedure data present)
    candidates_evaluated: int = 0
    alternates: list[AlternateAirport] = Field(default_factory=list)  # ranked, closest-first
    nearest_improving: list[AlternateAxisPick] = Field(default_factory=list)
    # Regulatory "is an alternate required?" for the destination (FAA + EASA),
    # co-located with the candidates it qualifies (#249). None until wired.
    alternate_requirement: AlternateRequirement | None = None
    computed_at: datetime | None = None
