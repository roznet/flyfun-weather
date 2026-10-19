"""Altitude table — sweep altitude-dependent advisories across an altitude range.

Pure computation module in the analysis layer. Does NOT import from tasks/.
"""

from __future__ import annotations

import logging
from typing import Callable

from weatherbrief.models import (
    AircraftEquipage,
    AdvisoryAggregation,
    AdvisoryStatus,
    AirportConditions,
    AltitudeAdvisoryChange,
    AltitudeAdvisoryDelta,
    AltitudeAdvisoryRow,
    AltitudeTableResult,
    ElevationProfile,
    RouteCrossSection,
    RoutePointAnalysis,
)

logger = logging.getLogger(__name__)

# Terrain clearance for a recommendable altitude — the same 1,000 ft the §8a
# descent-escape guard and the IcingEscape route advisory (terrain_margin_ft)
# use, so "an altitude we would offer" means one thing across the app (#807).
from weatherbrief.analysis.sounding.advisories import (  # noqa: E402
    _TERRAIN_CLEARANCE_FT as TERRAIN_CLEARANCE_FT,
)

# Severity rank for status comparison; UNAVAILABLE is excluded (not comparable).
_SEVERITY: dict[AdvisoryStatus, int] = {
    AdvisoryStatus.GREEN: 0,
    AdvisoryStatus.AMBER: 1,
    AdvisoryStatus.RED: 2,
}


def diff_altitude_rows(
    baseline: AltitudeAdvisoryRow,
    candidate: AltitudeAdvisoryRow,
    advisory_names: dict[str, str] | None = None,
) -> AltitudeAdvisoryDelta:
    """Compare two altitude rows, returning which advisories improve / worsen.

    "Improved" = the candidate's status is *less* severe than the baseline's
    (e.g. RED→AMBER, AMBER→GREEN); "worsened" = more severe. Advisories whose
    status is UNAVAILABLE on either side are skipped entirely (not comparable).
    Pure function — the canonical altitude-diff primitive shared by the digest
    prompt builder; the client carries a TypeScript twin (``altitude-diff.ts``).
    """
    names = advisory_names or {}
    improved: list[AltitudeAdvisoryChange] = []
    worsened: list[AltitudeAdvisoryChange] = []
    unchanged: list[str] = []

    # Iterate the baseline's advisories: a candidate-only advisory has no
    # baseline status to diff against, so it can't be classified either way.
    # The same evaluator set produces both rows today, so the keys match.
    for advisory_id, base_status in baseline.statuses.items():
        cand_status = candidate.statuses.get(advisory_id)
        if cand_status is None:
            continue
        if base_status not in _SEVERITY or cand_status not in _SEVERITY:
            continue
        base_rank = _SEVERITY[base_status]
        cand_rank = _SEVERITY[cand_status]
        if cand_rank == base_rank:
            unchanged.append(advisory_id)
            continue
        change = AltitudeAdvisoryChange(
            advisory_id=advisory_id,
            name=names.get(advisory_id, advisory_id),
            from_status=base_status,
            to_status=cand_status,
        )
        if cand_rank < base_rank:
            improved.append(change)
        else:
            worsened.append(change)

    return AltitudeAdvisoryDelta(improved=improved, worsened=worsened, unchanged=unchanged)


def row_for_altitude(
    table: AltitudeTableResult, altitude_ft: int | None,
) -> AltitudeAdvisoryRow | None:
    """Return the table row whose altitude exactly matches ``altitude_ft``."""
    if altitude_ft is None:
        return None
    for row in table.rows:
        if row.altitude_ft == altitude_ft:
            return row
    return None


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
    locale: str | None = None,
    cruise_speed_ias_kt: float | None = None,
    equipage: AircraftEquipage | None = None,
    flight_duration_hours: float = 0.0,
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

    # Terrain floor (#807). A row is one constant altitude for the whole route,
    # so the relevant terrain is the route's maximum; below it + clearance the
    # air is hazard-free *by construction* (the sweep never sees the ground)
    # and a GREEN there would be a recommendation into the hill.
    terrain_floor_ft = _terrain_floor_ft(elevation)
    terrain_complete = elevation is None or not elevation.points or elevation.terrain_complete

    # Build altitude range: 2000 to ceiling, stepping by step_ft
    altitudes = set(range(2000, flight_ceiling_ft + 1, step_ft))
    if terrain_floor_ft is not None:
        altitudes = {a for a in altitudes if a >= terrain_floor_ft}
    # Ensure cruise altitude is included — the planned row is shown even when
    # it sits below the floor (it is the pilot's plan), but never recommended.
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
            locale=locale,
            cruise_speed_ias_kt=cruise_speed_ias_kt,
            equipage=equipage,
            flight_duration_hours=flight_duration_hours,
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

    # Find best altitudes below and above cruise, never below the terrain floor.
    def _clear(r: AltitudeAdvisoryRow) -> bool:
        return terrain_floor_ft is None or r.altitude_ft >= terrain_floor_ft

    # With voids in the profile the floor is built from a lower bound on the
    # route's terrain, so no altitude *below* the plan is offered at all.
    best_below = (
        _find_best(
            rows,
            lambda r: r.altitude_ft < cruise_altitude_ft and _clear(r),
            cruise_altitude_ft,
        )
        if terrain_complete
        else None
    )
    best_above = _find_best(
        rows, lambda r: r.altitude_ft >= cruise_altitude_ft and _clear(r), cruise_altitude_ft
    )

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
        terrain_floor_ft=terrain_floor_ft,
    )


def _terrain_floor_ft(elevation: ElevationProfile | None) -> int | None:
    """Route terrain max (known samples) + clearance; None without a profile.

    ``max_elevation_ft`` ignores void samples (#807), so with voids this is a
    lower bound — the caller withholds below-cruise recommendations then.
    """
    if elevation is None or not elevation.points or elevation.max_elevation_ft is None:
        return None
    return int(elevation.max_elevation_ft + TERRAIN_CLEARANCE_FT)


def _find_best(
    rows: list[AltitudeAdvisoryRow],
    predicate: Callable[[AltitudeAdvisoryRow], bool],
    cruise_altitude_ft: int,
) -> int | None:
    """Find the altitude with the best score among rows matching predicate.

    Best = fewest reds, then fewest (ambers + unavailables), then fewest
    unavailables. Ties go to the altitude closest to cruise: the smallest
    change from the plan that buys the same picture. Breaking ties by *lowest*
    sent a pilot to 2,000 ft over the Channel when 8,000 ft cleared the same
    icing (EGTF-LFAT, 2026-09-28).

    UNAVAILABLE costs as much as AMBER and loses the tie against it (#807):
    scoring it like GREEN rewarded data absence (an altitude whose advisory
    could not be assessed beat one assessed AMBER). It is not ranked with RED —
    that would send the pilot to a *known* hazard over an unknown one.
    """
    candidates = [r for r in rows if predicate(r)]
    if not candidates:
        return None

    def _unavailable(r: AltitudeAdvisoryRow) -> int:
        return sum(1 for s in r.statuses.values() if s == AdvisoryStatus.UNAVAILABLE)

    candidates.sort(
        key=lambda r: (
            r.red_count,
            r.amber_count + _unavailable(r),
            _unavailable(r),
            abs(r.altitude_ft - cruise_altitude_ft),
        )
    )
    return candidates[0].altitude_ft
