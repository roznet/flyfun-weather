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
    # Original resolved profile choices. Appended for positional-call backward
    # compatibility; evaluators use these stable provenance values alongside
    # the already-swapped analysis fields.
    icing_method: str | None = None
    cloud_method: str | None = None
    convective_method: str | None = None


@runtime_checkable
class AdvisoryEvaluator(Protocol):
    """Protocol for advisory evaluator classes."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry: ...

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult: ...


from weatherbrief.analysis.advisories.registry import evaluate_all, get_altitude_dependent_ids, get_catalog, resolve_enabled_ids  # noqa: E402, F401
