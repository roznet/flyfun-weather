"""Route advisory evaluation framework.

Evaluates conditions across all route points to produce deterministic
GREEN/AMBER/RED per advisory per model.

Usage:
    from weatherbrief.analysis.advisories import RouteContext, evaluate_all, get_catalog

    ctx = RouteContext(analyses=..., cross_sections=..., ...)
    results = evaluate_all(ctx, enabled_ids=None, user_params={})
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AirportApproaches,
    AirportConditions,
    ElevationProfile,
    RouteAdvisoryResult,
    RouteCrossSection,
    RouteFrontsManifest,
    RoutePointAnalysis,
    RouteSunAnalysis,
)


@dataclass(frozen=True)
class RouteContext:
    """Immutable data bag passed to all evaluators."""

    analyses: list[RoutePointAnalysis]
    cross_sections: list[RouteCrossSection]
    elevation: ElevationProfile | None
    models: list[str]
    cruise_altitude_ft: int
    flight_ceiling_ft: int
    total_distance_nm: float
    airport_conditions: AirportConditions | None = None
    locale: str | None = None
    # Resolved cruise IAS (kt) for this flight — aircraft cruise speed, falling
    # back to the flight-default profile speed (see weatherbrief.atmo
    # .resolve_cruise_speed_ias). Used by the headwind advisory to derive a
    # realistic cruise TAS (converted at cruise_altitude_ft) for the trip-time
    # estimate. None when no usable aircraft/profile speed is known — the
    # advisory then falls back to flight_duration_hours.
    cruise_speed_ias_kt: float | None = None
    # The flight's planned still-air duration (h). Used by the headwind advisory
    # as a cruise-TAS fallback (distance ÷ duration) when no aircraft/profile
    # speed is set, so the trip-time estimate is always available. 0.0 = unknown.
    flight_duration_hours: float = 0.0
    # Experimental Hewson front-detection artifact (issue #196). Present only
    # when the ``auto_front_detection`` preference was on at generation time —
    # the front advisory evaluator skips (UNAVAILABLE) when this is None, so the
    # advisory surfaces *only* when the experimental feature is enabled.
    route_fronts: RouteFrontsManifest | None = None
    # Precomputed solar analysis (issue #227): night intervals + sun-side note +
    # dep/arr glare. Read by SunEvaluator. None on old packs / when unavailable.
    sun: RouteSunAnalysis | None = None
    # Published instrument approaches at the DESTINATION (issue #509), joined to
    # runway ends so the evaluator can pair each approach with the wind on the
    # end it serves. Read by ApproachFeasibilityEvaluator. ``None`` means the
    # collection step could not run (old pack, no nav.db) — the evaluator then
    # returns UNAVAILABLE, the ``route_fronts`` / ``sun`` precedent. It is NOT
    # the "airport has no approach" signal: that is a present object with an
    # empty ``approaches`` list.
    arrival_approaches: AirportApproaches | None = None
    # Every advisory's user parameter overrides, keyed by advisory id — threaded
    # in by ``evaluate_all`` so a *composite* evaluator can grade a sub-axis with
    # the owning advisory's tuning instead of duplicating its thresholds. Today
    # only ``ifr_feasibility`` reads it (for ``convective``); the alternative was
    # a second copy of the convective formula, which is exactly how the two
    # drifted apart in the first place (meteorology-decisions §22). Empty when an
    # evaluator is invoked outside ``evaluate_all`` — consumers must resolve
    # through a helper that falls back to catalog defaults (see
    # ``convective_grading.resolve_convective_params``).
    advisory_params: dict[str, dict[str, float]] = field(default_factory=dict)


@runtime_checkable
class AdvisoryEvaluator(Protocol):
    """Protocol for advisory evaluator classes."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry: ...

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult: ...


from weatherbrief.analysis.advisories.registry import evaluate_all, get_altitude_dependent_ids, get_catalog, get_category_order, resolve_enabled_ids  # noqa: E402, F401
from weatherbrief.analysis.advisories.interview import get_interview  # noqa: E402, F401
