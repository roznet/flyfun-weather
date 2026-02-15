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
    ElevationProfile,
    RouteAdvisoryResult,
    RouteCrossSection,
    RoutePointAnalysis,
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


@runtime_checkable
class AdvisoryEvaluator(Protocol):
    """Protocol for advisory evaluator classes."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry: ...

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult: ...


from weatherbrief.analysis.advisories.registry import evaluate_all, get_catalog  # noqa: E402, F401
