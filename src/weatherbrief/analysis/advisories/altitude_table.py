"""Altitude table — sweep altitude-dependent advisories across an altitude range.

Pure computation module in the analysis layer. Does NOT import from tasks/.
"""

from __future__ import annotations

import logging

from weatherbrief.models import (
    AdvisoryAggregation,
    AdvisoryStatus,
    AirportConditions,
    AltitudeAdvisoryRow,
    AltitudeTableResult,
    ElevationProfile,
    RouteCrossSection,
    RoutePointAnalysis,
)

logger = logging.getLogger(__name__)


def compute_altitude_table(
    analyses: list[RoutePointAnalysis],
    cross_sections: list[RouteCrossSection],
    elevation: ElevationProfile | None,
    models: list[str],
    cruise_altitude_ft: int,
    flight_ceiling_ft: int,
    total_distance_nm: float,
    airport_conditions: AirportConditions | None = None,
    step_ft: int = 2000,
    enabled_ids: set[str] | None = None,
    user_params: dict[str, dict[str, float]] | None = None,
    aggregation: AdvisoryAggregation = AdvisoryAggregation.MAJORITY,
) -> AltitudeTableResult:
    """Sweep altitude-dependent advisories across a range of altitudes.

    Args:
        analyses: Route point analyses (already method-swapped).
        cross_sections: Route cross-section data.
        elevation: Elevation profile (may be None).
        models: Model names for evaluation.
        cruise_altitude_ft: The flight's configured cruise altitude.
        flight_ceiling_ft: Maximum altitude to evaluate.
        total_distance_nm: Total route distance.
        airport_conditions: Pre-computed airport conditions.
        step_ft: Altitude step size (default 2000ft).
        enabled_ids: Advisory IDs the user has enabled (None = defaults).
        user_params: Per-advisory parameter overrides.
        aggregation: How per-model statuses combine.

    Returns:
        AltitudeTableResult with rows sorted altitude-descending.
    """
    from weatherbrief.analysis.advisories import (
        RouteContext,
        evaluate_all,
        get_altitude_dependent_ids,
        get_catalog,
    )

    # Determine which advisories are altitude-dependent
    alt_dep_ids = get_altitude_dependent_ids()
    if enabled_ids is not None:
        alt_dep_ids = alt_dep_ids & enabled_ids

    if not alt_dep_ids:
        return AltitudeTableResult(
            rows=[],
            advisory_ids=[],
            advisory_names={},
            cruise_altitude_ft=cruise_altitude_ft,
            flight_ceiling_ft=flight_ceiling_ft,
            step_ft=step_ft,
        )

    # Build altitude range: 2000 to ceiling, stepping by step_ft
    altitudes = set(range(2000, flight_ceiling_ft + 1, step_ft))
    # Ensure cruise altitude is included
    if 2000 <= cruise_altitude_ft <= flight_ceiling_ft:
        altitudes.add(cruise_altitude_ft)
    sorted_alts = sorted(altitudes)

    # Build advisory_ids list (stable order) and name map from catalog
    catalog = get_catalog()
    catalog_map = {e.id: e for e in catalog}
    advisory_ids = sorted(alt_dep_ids)
    advisory_names = {aid: catalog_map[aid].name for aid in advisory_ids if aid in catalog_map}

    # Evaluate at each altitude
    rows: list[AltitudeAdvisoryRow] = []
    for alt in sorted_alts:
        ctx = RouteContext(
            analyses=analyses,
            cross_sections=cross_sections,
            elevation=elevation,
            models=models,
            cruise_altitude_ft=alt,
            flight_ceiling_ft=flight_ceiling_ft,
            total_distance_nm=total_distance_nm,
            airport_conditions=airport_conditions,
        )
        results = evaluate_all(ctx, enabled_ids=alt_dep_ids, user_params=user_params, aggregation=aggregation)

        statuses: dict[str, AdvisoryStatus] = {}
        red_count = 0
        amber_count = 0
        green_count = 0
        for r in results:
            statuses[r.advisory_id] = r.aggregate_status
            if r.aggregate_status == AdvisoryStatus.RED:
                red_count += 1
            elif r.aggregate_status == AdvisoryStatus.AMBER:
                amber_count += 1
            elif r.aggregate_status == AdvisoryStatus.GREEN:
                green_count += 1

        rows.append(AltitudeAdvisoryRow(
            altitude_ft=alt,
            statuses=statuses,
            red_count=red_count,
            amber_count=amber_count,
            green_count=green_count,
        ))

    # Find best altitudes below and above cruise
    best_below = _find_best(rows, lambda r: r.altitude_ft < cruise_altitude_ft)
    best_above = _find_best(rows, lambda r: r.altitude_ft >= cruise_altitude_ft)

    # Sort rows descending by altitude (highest first — natural for pilots)
    rows.sort(key=lambda r: r.altitude_ft, reverse=True)

    return AltitudeTableResult(
        rows=rows,
        advisory_ids=advisory_ids,
        advisory_names=advisory_names,
        cruise_altitude_ft=cruise_altitude_ft,
        flight_ceiling_ft=flight_ceiling_ft,
        step_ft=step_ft,
        best_below_cruise=best_below,
        best_above_cruise=best_above,
    )


def _find_best(
    rows: list[AltitudeAdvisoryRow],
    predicate: object,
) -> int | None:
    """Find the altitude with the best score among rows matching predicate.

    Best = fewest reds, then fewest ambers. Ties broken by lower altitude.
    """
    candidates = [r for r in rows if predicate(r)]  # type: ignore[operator]
    if not candidates:
        return None
    candidates.sort(key=lambda r: (r.red_count, r.amber_count, r.altitude_ft))
    return candidates[0].altitude_ft
