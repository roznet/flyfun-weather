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
from functools import cached_property
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

    @cached_property
    def cruise_groundspeed_kt(self) -> float:
        """Route-average groundspeed at cruise (kt), for the extent time axis (#571).

        Cruise TAS (``resolve_cruise_tas``: aircraft/profile speed → the flight's
        own planned speed → a generic light-GA fallback) less the route-average
        headwind. Floored at ``MIN_GROUNDSPEED_KT`` so a headwind near TAS cannot
        turn a 20 nm band into hours.

        The headwind is resolved with the **same precedence the headwind advisory
        uses**: the cross-section wind at the *evaluated* ``cruise_altitude_ft``
        first, falling back to the per-point component baked into the pack. That
        matters because the altitude table re-evaluates the whole advisory set at
        other levels — reading only the baked component would quietly keep the
        wind from the pack's original cruise altitude and report the same minutes
        at every level (#571 review).

        Restricted to ``self.models``: the pack can carry components for slots
        this run excludes, and averaging those in would report a groundspeed for
        models the advisory never graded.

        Route-average and model-agnostic by choice: this feeds a **display**
        figure ("about 8 min in it"), never a gate. Per-model precision would be
        spurious for a number whose input is often a profile default, and would
        print three different minutes for one flight across three cards. Cached
        because ~13 evaluators read it per run and the lookup walks every point.
        """
        from weatherbrief.analysis.advisories._helpers import (
            MIN_GROUNDSPEED_KT,
            resolve_cruise_tas,
            wind_at_altitude,
            headwind_component,
        )

        tas = resolve_cruise_tas(self)
        headwinds: list[float] = []
        for rpa in self.analyses:
            for model in self.models:
                wind = wind_at_altitude(
                    self.cross_sections, model, rpa.point_index,
                    self.cruise_altitude_ft, rpa.forecast_hour,
                )
                if wind is not None:
                    speed_kt, direction_deg = wind
                    headwinds.append(
                        headwind_component(speed_kt, direction_deg, rpa.track_deg)
                    )
                    continue
                # Pack without cross-section winds: the precomputed component at
                # the pack's original cruise level, same fallback as ``headwind``.
                wc = rpa.wind_components.get(model)
                if wc is not None and wc.headwind_kt is not None:
                    headwinds.append(wc.headwind_kt)
        if not headwinds:
            return tas
        return max(tas - sum(headwinds) / len(headwinds), MIN_GROUNDSPEED_KT)


@runtime_checkable
class AdvisoryEvaluator(Protocol):
    """Protocol for advisory evaluator classes."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry: ...

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult: ...


from weatherbrief.analysis.advisories.registry import evaluate_all, get_altitude_dependent_ids, get_catalog, get_category_order, resolve_enabled_ids  # noqa: E402, F401
from weatherbrief.analysis.advisories.interview import get_interview  # noqa: E402, F401
