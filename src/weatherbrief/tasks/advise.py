"""Advisory task — route advisory evaluation.

Consolidates advisory logic from ``pipeline.py`` (lines 261-334) and
``api/packs.py`` (recalculate_advisories).  Can run from live data
or from persisted pack_dir artifacts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from weatherbrief.models import (
    AdvisoryAggregation,
    AirportConditions,
    AltitudeTableResult,
    ElevationProfile,
    IcingZone,
    RouteAdvisoriesManifest,
    RouteConfig,
    RouteCrossSection,
    RoutePointAnalysis,
)

logger = logging.getLogger(__name__)


@dataclass
class AdvisoryResult:
    """Output of the advisory stage."""

    manifest: RouteAdvisoriesManifest | None
    airport_conditions: AirportConditions | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Non-mutating method resolution
# ---------------------------------------------------------------------------


def _resolve_analyses(
    rp_analyses: list[RoutePointAnalysis],
    icing_method: str | None,
    cloud_method: str | None,
) -> list[RoutePointAnalysis]:
    """Return analyses with cloud/icing layers resolved per user preference.

    Returns the original list unchanged (no copy) when no swap is needed.
    Otherwise builds new objects via ``model_copy()`` — the originals are
    never mutated.

    Cloud resolution (``cloud_method``):
        ``"nwp"`` → use ``nwp_cloud_layers`` (fall back to DD source if None).
    Icing resolution (``icing_method``):
        ``"ogimet_nwp"`` → use ``icing_ogimet_nwp_zones``.
        ``"sfip_nwp"``   → convert ``sfip_zones`` to ``IcingZone`` list.
    """
    swap_icing = icing_method and icing_method != "ogimet_dd"
    swap_cloud = cloud_method and cloud_method != "dd"
    if not swap_icing and not swap_cloud:
        return rp_analyses

    resolved: list[RoutePointAnalysis] = []
    for rpa in rp_analyses:
        new_soundings: dict = {}
        changed = False
        for key, sounding in rpa.sounding.items():
            updates: dict = {}

            # --- cloud resolution ---
            if swap_cloud:
                if cloud_method == "nwp" and sounding.nwp_cloud_layers is not None:
                    updates["cloud_layers"] = list(sounding.nwp_cloud_layers)
                else:
                    # Fallback: restore DD source
                    updates["cloud_layers"] = list(sounding.dd_cloud_layers)

            # --- icing resolution ---
            if swap_icing:
                if icing_method == "ogimet_nwp":
                    updates["icing_zones"] = list(sounding.icing_ogimet_nwp_zones)
                elif icing_method == "sfip_nwp":
                    updates["icing_zones"] = [
                        IcingZone(
                            base_ft=z.base_ft,
                            top_ft=z.top_ft,
                            base_pressure_hpa=z.base_pressure_hpa,
                            top_pressure_hpa=z.top_pressure_hpa,
                            risk=z.risk,
                            icing_type=z.icing_type,
                            mean_temperature_c=z.mean_temperature_c,
                            mean_rh_pct=z.mean_rh_pct,
                            mean_icing_index=z.mean_sfip_100,
                        )
                        for z in sounding.sfip_zones
                    ]

            if updates:
                new_soundings[key] = sounding.model_copy(update=updates)
                changed = True
            else:
                new_soundings[key] = sounding

        if changed:
            resolved.append(rpa.model_copy(update={"sounding": new_soundings}))
        else:
            resolved.append(rpa)
    return resolved


# ---------------------------------------------------------------------------
# Core advisory logic
# ---------------------------------------------------------------------------

def _compute_advisory_model_names(
    model_names: list[str],
    advisory_models: list[str] | None,
) -> list[str]:
    """Determine which models to use for advisory evaluation."""
    if advisory_models:
        filtered = [m for m in advisory_models if m in model_names]
    else:
        # Default: all models except best_match
        filtered = [m for m in model_names if m != "best_match"]
    # Fallback: if empty after filtering, use all models
    return filtered if filtered else model_names


def _compute_airport_conditions(
    rp_analyses: list[RoutePointAnalysis],
    cross_sections: list[RouteCrossSection],
    advisory_model_names: list[str],
    route: RouteConfig,
    airports_db_path: str | None = None,
) -> AirportConditions | None:
    """Compute airport conditions for departure/arrival."""
    try:
        from weatherbrief.analysis.airport_conditions import compute_airport_conditions

        runway_data = None
        if airports_db_path:
            from weatherbrief.airports import get_runway_ends

            runway_data = get_runway_ends(
                [route.origin.icao, route.destination.icao],
                airports_db_path,
            )

        return compute_airport_conditions(
            analyses=rp_analyses,
            cross_sections=cross_sections,
            models=advisory_model_names,
            dep_icao=route.origin.icao,
            dep_name=route.origin.name,
            arr_icao=route.destination.icao,
            arr_name=route.destination.name,
            runway_data=runway_data,
        )
    except Exception:
        logger.warning("Airport conditions computation failed", exc_info=True)
        return None


def run_advisories(
    rp_analyses: list[RoutePointAnalysis],
    cross_sections: list[RouteCrossSection],
    elevation_profile: ElevationProfile | None,
    model_names: list[str],
    route: RouteConfig,
    total_distance_nm: float,
    advisory_models: list[str] | None = None,
    airports_db_path: str | None = None,
    enabled_ids: set[str] | None = None,
    user_params: dict | None = None,
    aggregation: AdvisoryAggregation | None = None,
    pack_dir: Path | None = None,
    progress_callback: Callable[[str, str | None], None] | None = None,
    icing_method: str | None = None,
    cloud_method: str | None = None,
) -> AdvisoryResult:
    """Evaluate route advisories from analysis results.

    If *pack_dir* is set, persists the advisory manifest to disk.
    """
    def _notify(stage: str, detail: str | None = None) -> None:
        if progress_callback is not None:
            progress_callback(stage, detail)

    rp_analyses = _resolve_analyses(rp_analyses, icing_method, cloud_method)

    advisory_model_names = _compute_advisory_model_names(model_names, advisory_models)

    # Airport conditions
    airport_conds = _compute_airport_conditions(
        rp_analyses, cross_sections, advisory_model_names, route, airports_db_path,
    )

    # Evaluate advisories
    _notify("route_advisories")
    try:
        from weatherbrief.analysis.advisories import RouteContext, evaluate_all, get_catalog

        ctx = RouteContext(
            analyses=rp_analyses,
            cross_sections=cross_sections,
            elevation=elevation_profile,
            models=advisory_model_names,
            cruise_altitude_ft=route.cruise_altitude_ft,
            flight_ceiling_ft=route.flight_ceiling_ft,
            total_distance_nm=total_distance_nm,
            airport_conditions=airport_conds,
        )
        effective_aggregation = aggregation or AdvisoryAggregation.MAJORITY
        advisory_results = evaluate_all(ctx, enabled_ids, user_params, aggregation=effective_aggregation)
        manifest = RouteAdvisoriesManifest(
            advisories=advisory_results,
            catalog=get_catalog(),
            route_name=route.name,
            cruise_altitude_ft=route.cruise_altitude_ft,
            flight_ceiling_ft=route.flight_ceiling_ft,
            total_distance_nm=total_distance_nm,
            models=advisory_model_names,
            aggregation=effective_aggregation.value,
            airport_conditions=airport_conds,
        )
        logger.info("Route advisories: %d evaluated (%d models)",
                     len(advisory_results), len(advisory_model_names))

        if pack_dir:
            from weatherbrief.tasks.artifacts import save_advisory_artifacts

            save_advisory_artifacts(pack_dir, manifest)

        return AdvisoryResult(manifest=manifest, airport_conditions=airport_conds)
    except Exception as exc:
        logger.warning("Route advisory evaluation failed", exc_info=True)
        return AdvisoryResult(manifest=None, error=str(exc))


def run_advisories_from_pack(
    pack_dir: Path,
    route: RouteConfig | None = None,
    *,
    cruise_altitude_ft: int | None = None,
    flight_ceiling_ft: int | None = None,
    advisory_models: list[str] | None = None,
    enabled_ids: set[str] | None = None,
    user_params: dict | None = None,
    aggregation: AdvisoryAggregation | None = None,
    airports_db_path: str | None = None,
    airport_conditions_recompute: Callable | None = None,
    icing_method: str | None = None,
    cloud_method: str | None = None,
) -> AdvisoryResult:
    """Re-evaluate advisories from persisted pack_dir artifacts.

    Loads route_analyses, cross_sections, and elevation from disk.

    Either *route* or *flight_ceiling_ft* must be provided.  When called
    from the API recalculate endpoint (no resolved RouteConfig available),
    pass ``flight_ceiling_ft`` directly and use
    ``airport_conditions_recompute`` for airport conditions.

    ``airport_conditions_recompute`` is called with
    ``(rp_analyses, cross_sections, advisory_model_names)``
    and should return ``AirportConditions | None``.
    """
    from weatherbrief.tasks.artifacts import (
        load_cross_sections,
        load_elevation_profile,
        load_route_analyses,
    )

    manifest = load_route_analyses(pack_dir)
    cross_sections = load_cross_sections(pack_dir)
    elevation = load_elevation_profile(pack_dir)

    analyses = _resolve_analyses(manifest.analyses, icing_method, cloud_method)

    # Resolve flight_ceiling_ft from route or explicit param
    effective_ceiling = flight_ceiling_ft
    if effective_ceiling is None and route is not None:
        effective_ceiling = route.flight_ceiling_ft
    if effective_ceiling is None:
        effective_ceiling = manifest.cruise_altitude_ft  # fallback

    # Resolve effective cruise altitude from override or manifest
    effective_cruise = cruise_altitude_ft if cruise_altitude_ft is not None else manifest.cruise_altitude_ft

    model_names = manifest.models
    advisory_model_names = _compute_advisory_model_names(model_names, advisory_models)

    # Airport conditions: use callback if provided, else compute from route
    airport_conds: AirportConditions | None = None
    if airport_conditions_recompute is not None:
        airport_conds = airport_conditions_recompute(
            analyses, cross_sections, advisory_model_names,
        )
    elif route and airports_db_path:
        airport_conds = _compute_airport_conditions(
            analyses, cross_sections, advisory_model_names,
            route, airports_db_path,
        )

    # Evaluate
    try:
        from weatherbrief.analysis.advisories import RouteContext, evaluate_all, get_catalog

        ctx = RouteContext(
            analyses=analyses,
            cross_sections=cross_sections,
            elevation=elevation,
            models=advisory_model_names,
            cruise_altitude_ft=effective_cruise,
            flight_ceiling_ft=effective_ceiling,
            total_distance_nm=manifest.total_distance_nm,
            airport_conditions=airport_conds,
        )
        effective_aggregation = aggregation or AdvisoryAggregation.MAJORITY
        advisory_results = evaluate_all(ctx, enabled_ids, user_params, aggregation=effective_aggregation)
        result_manifest = RouteAdvisoriesManifest(
            advisories=advisory_results,
            catalog=get_catalog(),
            route_name=manifest.route_name,
            cruise_altitude_ft=effective_cruise,
            flight_ceiling_ft=effective_ceiling,
            total_distance_nm=manifest.total_distance_nm,
            models=advisory_model_names,
            aggregation=effective_aggregation.value,
            airport_conditions=airport_conds,
        )

        from weatherbrief.tasks.artifacts import save_advisory_artifacts

        save_advisory_artifacts(pack_dir, result_manifest)

        return AdvisoryResult(manifest=result_manifest, airport_conditions=airport_conds)
    except Exception as exc:
        logger.warning("Advisory re-evaluation from pack failed", exc_info=True)
        return AdvisoryResult(manifest=None, error=str(exc))


def run_altitude_table_from_pack(
    pack_dir: Path,
    *,
    cruise_altitude_ft: int,
    flight_ceiling_ft: int,
    step_ft: int = 2000,
    advisory_models: list[str] | None = None,
    enabled_ids: set[str] | None = None,
    user_params: dict | None = None,
    aggregation: AdvisoryAggregation | None = None,
    airport_conditions_recompute: Callable | None = None,
    icing_method: str | None = None,
    cloud_method: str | None = None,
) -> AltitudeTableResult:
    """Compute altitude advisory table from persisted pack artifacts.

    Loads analyses, cross-sections, and elevation once, then sweeps
    all altitude-dependent advisories across the altitude range.
    """
    from weatherbrief.analysis.advisories.altitude_table import compute_altitude_table
    from weatherbrief.tasks.artifacts import (
        load_cross_sections,
        load_elevation_profile,
        load_route_analyses,
    )

    manifest = load_route_analyses(pack_dir)
    cross_sections = load_cross_sections(pack_dir)
    elevation = load_elevation_profile(pack_dir)

    analyses = _resolve_analyses(manifest.analyses, icing_method, cloud_method)

    model_names = manifest.models
    advisory_model_names = _compute_advisory_model_names(model_names, advisory_models)

    # Airport conditions
    airport_conds: AirportConditions | None = None
    if airport_conditions_recompute is not None:
        airport_conds = airport_conditions_recompute(
            analyses, cross_sections, advisory_model_names,
        )

    effective_aggregation = aggregation or AdvisoryAggregation.MAJORITY

    return compute_altitude_table(
        analyses=analyses,
        cross_sections=cross_sections,
        elevation=elevation,
        models=advisory_model_names,
        cruise_altitude_ft=cruise_altitude_ft,
        flight_ceiling_ft=flight_ceiling_ft,
        total_distance_nm=manifest.total_distance_nm,
        airport_conditions=airport_conds,
        step_ft=step_ft,
        enabled_ids=enabled_ids,
        user_params=user_params,
        aggregation=effective_aggregation,
    )
