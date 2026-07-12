"""Advisory task — route advisory evaluation.

Consolidates advisory logic from ``pipeline.py`` (lines 261-334) and
``api/packs.py`` (recalculate_advisories).  Can run from live data
or from persisted pack_dir artifacts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from weatherbrief.analysis.advisories.engine_methods import ENGINE_METHOD_DEFAULTS
from weatherbrief.models import (
    AdvisoryAggregation,
    AdvisoryChip,
    AdvisoryStatus,
    AdvisorySummary,
    AirportConditions,
    AltitudeTableResult,
    ElevationProfile,
    IcingZone,
    RouteAdvisoriesManifest,
    RouteConfig,
    RouteCrossSection,
    RouteFrontsManifest,
    RoutePointAnalysis,
    RouteWindOverlay,
)

logger = logging.getLogger(__name__)

# The flight-level traffic light when we could not assess the flight at all
# (issue #392). Uppercase to match the GREEN/AMBER/RED assessment strings; the
# advisory-level sibling is the lowercase ``AdvisoryStatus.UNAVAILABLE``.
#
# Distinct from a NULL assessment, which keeps its existing meaning: no pack /
# no briefing yet. UNAVAILABLE means we briefed the flight and came back with
# nothing gradeable.
ASSESSMENT_UNAVAILABLE = "UNAVAILABLE"


@dataclass
class AdvisoryResult:
    """Output of the advisory stage."""

    manifest: RouteAdvisoriesManifest | None
    airport_conditions: AirportConditions | None = None
    wind_overlay: RouteWindOverlay | None = None
    altitude_table: AltitudeTableResult | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Non-mutating method resolution
# ---------------------------------------------------------------------------


def _cloud_source_from_method(cloud_method: str | None) -> str | None:
    """Extract the cloud *source* (``"dd"`` / ``"nwp"``) from a ``cloud_method``.

    Profiles persist a combined ``<style>_<source>`` string written by the
    settings page (e.g. ``soft_nwp``, ``square_dd``, ``natural_nwp``), while
    legacy profiles carry the bare source (``dd`` / ``nwp``). The render *style*
    is a frontend-only concern; backend cloud resolution only needs the source.
    Any value ending in ``nwp`` selects the NWP layers; everything else
    (``None``, bare/styled ``*_dd``) falls back to the DD source.
    """
    if not cloud_method:
        return None
    return "nwp" if cloud_method.endswith("nwp") else "dd"


def _resolve_analyses(
    rp_analyses: list[RoutePointAnalysis],
    icing_method: str | None,
    cloud_method: str | None,
    convective_method: str | None = None,
) -> list[RoutePointAnalysis]:
    """Return analyses with cloud/icing/convective layers resolved per user preference.

    Returns the original list unchanged (no copy) when no swap is needed.
    Otherwise builds new objects via ``model_copy()`` — the originals are
    never mutated.

    Cloud resolution (``cloud_method``):
        source ``nwp`` (``"nwp"`` or any ``*_nwp`` styled form) → use
        ``nwp_cloud_layers`` (fall back to DD source if None); ``dd`` / ``None``
        keep the DD-derived base layers. See ``_cloud_source_from_method``.
    Icing resolution (``icing_method``):
        ``"ogimet_nwp"`` → use ``icing_ogimet_nwp_zones``.
        ``"sfip_nwp"``   → convert ``sfip_zones`` to ``IcingZone`` list.
    Convective resolution (``convective_method``):
        ``"nwp"`` → use ``convective_nwp`` (fall back to ``convective_thermo``).

    Absence (``None``) means "follow the declared default" (#403): each method is
    resolved through :data:`ENGINE_METHOD_DEFAULTS` (NWP icing / NWP cloud / NWP
    convective) before the checks below, so a profile that never saved an engine
    method grades on the same methods its settings page has always displayed —
    not the DD/DD/thermo the old falsy fall-through silently applied.
    """
    icing_method = icing_method or ENGINE_METHOD_DEFAULTS["icing_method"]
    cloud_method = cloud_method or ENGINE_METHOD_DEFAULTS["cloud_method"]
    convective_method = convective_method or ENGINE_METHOD_DEFAULTS["convective_method"]

    cloud_source = _cloud_source_from_method(cloud_method)
    swap_icing = icing_method != "ogimet_dd"
    swap_cloud = cloud_source == "nwp"
    swap_convective = convective_method != "thermo"
    if not swap_icing and not swap_cloud and not swap_convective:
        return rp_analyses

    resolved: list[RoutePointAnalysis] = []
    for rpa in rp_analyses:
        new_soundings: dict = {}
        changed = False
        for key, sounding in rpa.sounding.items():
            updates: dict = {}

            # --- cloud resolution ---
            if swap_cloud:
                if cloud_source == "nwp" and sounding.nwp_cloud_layers is not None:
                    updates["cloud_layers"] = list(sounding.nwp_cloud_layers)
                    # Determine effective method from layer source tags
                    sources = {cl.source for cl in sounding.nwp_cloud_layers}
                    if "grib" in sources:
                        updates["cloud_method_effective"] = "nwp"
                    elif sources:
                        updates["cloud_method_effective"] = "nwp_synthesized"
                    else:
                        updates["cloud_method_effective"] = "nwp"
                else:
                    # Fallback: restore DD source
                    updates["cloud_layers"] = list(sounding.dd_cloud_layers)
                    updates["cloud_method_effective"] = "dd"

            # --- icing resolution ---
            if swap_icing:
                if icing_method == "ogimet_nwp":
                    updates["icing_zones"] = list(sounding.icing_ogimet_nwp_zones)
                    # Ogimet-NWP needs a model-native cloud envelope. Without
                    # one it returns [] (assess_icing_zones_ogimet_nwp bails on
                    # `not clouds`), which is "method unavailable", NOT "ran,
                    # found nothing". Flag it so the icing evaluators grade
                    # UNAVAILABLE rather than clear-by-absence (#391).
                    if not sounding.nwp_cloud_layers:
                        updates["active_icing_available"] = False
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

            # --- convective resolution ---
            if swap_convective:
                nwp = sounding.convective_nwp
                updates["convective"] = nwp if nwp is not None else sounding.convective_thermo

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


def _front_context(
    pack_dir: Path | None,
    enabled_ids: set[str] | None,
    advisory_enabled: dict[str, bool] | None = None,
    *,
    filename: str = "route_fronts.json",
) -> tuple[RouteFrontsManifest | None, set[str] | None]:
    """Load the front artifact (if any) and decide whether its advisory runs.

    Returns ``(route_fronts, enabled_ids)``. Two independent per-profile
    controls gate the experimental front feature (issue #196, model B):

    * ``auto_front_detection`` is the *master* — it generates ``route_fronts.json``
      and drives the map / cross-section overlays. Its artifact being on disk is
      what we detect here.
    * the ``fronts`` advisory toggle independently gates whether the front *grade*
      (the GREEN/AMBER/RED card that can move the overall assessment) surfaces.

    The advisory is ``default_enabled=False`` in the catalog, so it never runs on
    its own. When the artifact is present we inject it **by default** (so it is
    discoverable the moment the master is on), but we honor an *explicit* disable
    in the per-profile map — letting a pilot keep the overlays for situational
    awareness while opting out of the experimental grade.
    """
    if pack_dir is None:
        return None, enabled_ids
    from weatherbrief.tasks.artifacts import load_route_fronts

    route_fronts = load_route_fronts(pack_dir, filename=filename)
    if route_fronts is None:
        return None, enabled_ids

    from weatherbrief.analysis.advisories.fronts import FRONTS_ADVISORY_ID

    # Explicit per-profile opt-out: overlays stay (artifact still returned), but
    # the advisory grade is suppressed. ``is False`` so that an absent key or an
    # explicit ``True`` both fall through to the default-on injection below.
    if advisory_enabled is not None and advisory_enabled.get(FRONTS_ADVISORY_ID) is False:
        return route_fronts, enabled_ids

    if enabled_ids is None:
        # Materialize the default set so we don't accidentally disable the
        # other default advisories by passing a one-element enabled set.
        from weatherbrief.analysis.advisories import get_catalog

        enabled_ids = {e.id for e in get_catalog() if e.default_enabled}
    return route_fronts, set(enabled_ids) | {FRONTS_ADVISORY_ID}


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


def _compute_route_sun(
    rp_analyses: list[RoutePointAnalysis],
    airport_conds: AirportConditions | None,
) -> "RouteSunAnalysis | None":
    """Full route solar analysis (night + sun-side + dep/arr glare) for the advisory.

    Unlike the analyze-stage call (which has no airport wind), this passes the
    airport conditions so the wind-best-runway glare assessments are populated.
    """
    try:
        from weatherbrief.analysis.sun import compute_route_sun

        dep = airport_conds.departure if airport_conds else None
        arr = airport_conds.arrival if airport_conds else None
        return compute_route_sun(rp_analyses, dep, arr)
    except Exception:
        logger.warning("Route sun analysis failed", exc_info=True)
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
    advisory_enabled: dict[str, bool] | None = None,
    user_params: dict | None = None,
    aggregation: AdvisoryAggregation | None = None,
    pack_dir: Path | None = None,
    progress_callback: Callable[[str, str | None], None] | None = None,
    icing_method: str | None = None,
    cloud_method: str | None = None,
    convective_method: str | None = None,
    locale: str | None = None,
    cruise_speed_ias_kt: float | None = None,
    altitude_table_step_ft: int | None = None,
) -> AdvisoryResult:
    """Evaluate route advisories from analysis results.

    If *pack_dir* is set, persists the advisory manifest to disk.

    If *altitude_table_step_ft* is set, also sweeps the altitude-dependent
    advisories across the flight's altitude range (reusing the already-resolved
    analyses, cross-sections, and airport conditions — essentially free) and
    persists ``altitude_table.json``. This must happen here, while the
    cross-sections are still in memory (the pipeline clears them right after).
    """
    def _notify(stage: str, detail: str | None = None) -> None:
        if progress_callback is not None:
            progress_callback(stage, detail)

    rp_analyses = _resolve_analyses(rp_analyses, icing_method, cloud_method, convective_method)

    advisory_model_names = _compute_advisory_model_names(model_names, advisory_models)

    # Airport conditions
    airport_conds = _compute_airport_conditions(
        rp_analyses, cross_sections, advisory_model_names, route, airports_db_path,
    )

    # Evaluate advisories
    _notify("route_advisories")
    try:
        from weatherbrief.analysis.advisories import RouteContext, evaluate_all, get_catalog

        # Front artifact (experimental) — written before advisories in the
        # pipeline, so it is on disk by the time we get here when the pref is on.
        route_fronts, enabled_ids = _front_context(pack_dir, enabled_ids, advisory_enabled)

        ctx = RouteContext(
            analyses=rp_analyses,
            cross_sections=cross_sections,
            elevation=elevation_profile,
            models=advisory_model_names,
            cruise_altitude_ft=route.cruise_altitude_ft,
            flight_ceiling_ft=route.flight_ceiling_ft,
            total_distance_nm=total_distance_nm,
            airport_conditions=airport_conds,
            locale=locale,
            route_fronts=route_fronts,
            sun=_compute_route_sun(rp_analyses, airport_conds),
            cruise_speed_ias_kt=cruise_speed_ias_kt,
            flight_duration_hours=route.flight_duration_hours,
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

        # Precompute the altitude table while the cross-sections are still in
        # memory (the pipeline clears them right after advisories). Reuses the
        # resolved analyses + airport conditions — ~1ms/altitude. Failures here
        # never fail advisories: the lever falls back to the live endpoint.
        altitude_table = None
        if altitude_table_step_ft is not None:
            try:
                from weatherbrief.analysis.advisories.altitude_table import (
                    compute_altitude_table,
                )

                altitude_table = compute_altitude_table(
                    analyses=rp_analyses,
                    cross_sections=cross_sections,
                    elevation=elevation_profile,
                    models=advisory_model_names,
                    cruise_altitude_ft=route.cruise_altitude_ft,
                    flight_ceiling_ft=route.flight_ceiling_ft,
                    total_distance_nm=total_distance_nm,
                    airport_conditions=airport_conds,
                    step_ft=altitude_table_step_ft,
                    enabled_ids=enabled_ids,
                    user_params=user_params,
                    aggregation=effective_aggregation,
                    locale=locale,
                    cruise_speed_ias_kt=cruise_speed_ias_kt,
                    flight_duration_hours=route.flight_duration_hours,
                )
                if pack_dir and altitude_table is not None:
                    from weatherbrief.tasks.artifacts import save_altitude_table_artifacts

                    save_altitude_table_artifacts(pack_dir, altitude_table)
            except Exception:
                logger.warning("Altitude table precompute failed (non-fatal)", exc_info=True)
                altitude_table = None

        return AdvisoryResult(
            manifest=manifest,
            airport_conditions=airport_conds,
            altitude_table=altitude_table,
        )
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
    advisory_enabled: dict[str, bool] | None = None,
    user_params: dict | None = None,
    aggregation: AdvisoryAggregation | None = None,
    airports_db_path: str | None = None,
    airport_conditions_recompute: Callable | None = None,
    icing_method: str | None = None,
    cloud_method: str | None = None,
    convective_method: str | None = None,
    locale: str | None = None,
    cruise_speed_ias_kt: float | None = None,
    flight_duration_hours: float = 0.0,
    persist: bool = True,
) -> AdvisoryResult:
    """Re-evaluate advisories from persisted pack_dir artifacts.

    Loads route_analyses, cross_sections, and elevation from disk.

    ``persist=False`` computes the manifest without writing
    ``route_advisories.json`` back to the pack — used by the eval rerun/diff
    so a "what changed" comparison never clobbers the saved baseline.

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

    analyses = _resolve_analyses(manifest.analyses, icing_method, cloud_method, convective_method)

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

        route_fronts, enabled_ids = _front_context(pack_dir, enabled_ids, advisory_enabled)

        # Prefer a freshly computed sun analysis (glare reflects recomputed airport
        # conditions); fall back to the manifest's precomputed value from the
        # original run when airport conditions are unavailable on recalc.
        route_sun = _compute_route_sun(analyses, airport_conds)
        if route_sun is None:
            route_sun = manifest.sun

        ctx = RouteContext(
            analyses=analyses,
            cross_sections=cross_sections,
            elevation=elevation,
            models=advisory_model_names,
            cruise_altitude_ft=effective_cruise,
            flight_ceiling_ft=effective_ceiling,
            total_distance_nm=manifest.total_distance_nm,
            airport_conditions=airport_conds,
            locale=locale,
            route_fronts=route_fronts,
            sun=route_sun,
            cruise_speed_ias_kt=cruise_speed_ias_kt,
            flight_duration_hours=flight_duration_hours,
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

        if persist:
            from weatherbrief.tasks.artifacts import save_advisory_artifacts

            save_advisory_artifacts(pack_dir, result_manifest)

        # Per-point wind overlay at the effective cruise altitude.
        # Only meaningful when the caller passed an override that differs
        # from the manifest's baked cruise altitude; otherwise the
        # existing manifest wind_components already match.
        wind_overlay: RouteWindOverlay | None = None
        if cruise_altitude_ft is not None and cruise_altitude_ft != manifest.cruise_altitude_ft:
            from weatherbrief.tasks.analyze import compute_wind_overlay_at_altitude

            try:
                wind_overlay = compute_wind_overlay_at_altitude(
                    analyses, cross_sections, advisory_model_names, effective_cruise,
                )
            except Exception:
                logger.warning("Wind overlay computation failed", exc_info=True)

        return AdvisoryResult(
            manifest=result_manifest,
            airport_conditions=airport_conds,
            wind_overlay=wind_overlay,
        )
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
    convective_method: str | None = None,
    locale: str | None = None,
    cruise_speed_ias_kt: float | None = None,
    flight_duration_hours: float = 0.0,
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

    analyses = _resolve_analyses(manifest.analyses, icing_method, cloud_method, convective_method)

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
        locale=locale,
        cruise_speed_ias_kt=cruise_speed_ias_kt,
        flight_duration_hours=flight_duration_hours,
    )


# ---------------------------------------------------------------------------
# Alt departure time helpers
# ---------------------------------------------------------------------------


def advisories_ungradeable(manifest: RouteAdvisoriesManifest | None) -> bool:
    """True when a manifest exists but *nothing in it graded* (issue #392).

    The single test behind both the UNAVAILABLE assessment and the decision to
    skip the LLM digest, so the two can never disagree about whether we have
    anything to say.

    ``None`` is deliberately **not** ungradeable: a pack whose advisory stage
    never ran at all is the pre-existing NULL-assessment case (advisories are an
    optional digest input), and that is a different failure from a stage that ran
    and came back with nothing. Keeping them apart is what lets NULL go on
    meaning "not assessed" while UNAVAILABLE means "assessed, and we have
    nothing".

    An empty advisory list counts as ungradeable — a manifest that graded zero
    advisories graded nothing.
    """
    if manifest is None:
        return False
    return all(
        adv.aggregate_status == AdvisoryStatus.UNAVAILABLE.value
        for adv in manifest.advisories
    )


def derive_assessment_from_advisories(
    manifest: RouteAdvisoriesManifest,
) -> tuple[str, str]:
    """Derive an overall assessment from an advisories manifest.

    Returns ``(assessment, reason)`` where assessment is the worst aggregate
    status across all advisories and reason summarises the RED/AMBER ones.

    With nothing to grade the answer is ``UNAVAILABLE``, never GREEN: an empty
    manifest means we could not assess the flight, not that we assessed it and
    found it clear. Same rule the aggregation already follows
    (``AdvisoryStatus.worst`` on an empty list) — this function used to
    short-circuit past it.
    """
    statuses = [
        AdvisoryStatus(adv.aggregate_status)
        for adv in manifest.advisories
        if adv.aggregate_status != AdvisoryStatus.UNAVAILABLE.value
    ]
    if not statuses:
        return (
            ASSESSMENT_UNAVAILABLE,
            "No advisory could be graded — no usable model data for this route",
        )

    worst = AdvisoryStatus.worst(statuses)
    assessment = worst.value.upper()

    # Build reason from the non-green advisories
    concern_parts: list[str] = []
    for adv in manifest.advisories:
        if adv.aggregate_status in ("red", "amber"):
            concern_parts.append(f"{adv.advisory_id}={adv.aggregate_status.upper()}")
    reason = ", ".join(concern_parts) if concern_parts else "All clear"
    return (assessment, reason)


def per_model_reasons_from_manifest(
    manifest: RouteAdvisoriesManifest,
) -> dict[str, str]:
    """Per-model RED/AMBER reason strings from an advisories manifest.

    Same ``"id=STATUS, ..."`` format as :func:`derive_assessment_from_advisories`
    builds for the aggregate, split by contributing model. A model with nothing
    flagged is absent from the result — callers treat absence as all-clear.
    """
    per_model: dict[str, list[str]] = {}
    for adv in manifest.advisories:
        for m in adv.per_model:
            if m.status.value in ("red", "amber"):
                per_model.setdefault(m.model, []).append(
                    f"{adv.advisory_id}={m.status.value.upper()}"
                )
    return {model: ", ".join(parts) for model, parts in per_model.items()}


# Cap on the number of named category chips surfaced on the flights-list card.
# Mirrors the briefing-page glance summary's intent: a few "what to look at"
# cues, not an exhaustive list.
ADVISORY_SUMMARY_TOP_CAP = 3


def summarize_advisories(manifest: RouteAdvisoriesManifest) -> AdvisorySummary:
    """Denormalize a manifest into a compact RED/AMBER summary for the card.

    Counts RED and AMBER advisories and builds a severity-ordered list of up
    to ``ADVISORY_SUMMARY_TOP_CAP`` named category chips (RED first, then
    AMBER), mapping each ``advisory_id`` to its catalog display name. GREEN and
    UNAVAILABLE advisories are excluded entirely. Keeps the server-side
    severity rules consistent with the client glance summary
    (``web/ts/managers/briefing-ui.ts`` ``renderAdvisoryChips``).
    """
    names = {entry.id: entry.name for entry in manifest.catalog}

    red: list[RouteAdvisoryResult] = []
    amber: list[RouteAdvisoryResult] = []
    for adv in manifest.advisories:
        if adv.aggregate_status == AdvisoryStatus.RED:
            red.append(adv)
        elif adv.aggregate_status == AdvisoryStatus.AMBER:
            amber.append(adv)

    top = [
        AdvisoryChip(status="RED", name=names.get(adv.advisory_id, adv.advisory_id))
        for adv in red
    ] + [
        AdvisoryChip(status="AMBER", name=names.get(adv.advisory_id, adv.advisory_id))
        for adv in amber
    ]

    return AdvisorySummary(
        red=len(red),
        amber=len(amber),
        top=top[:ADVISORY_SUMMARY_TOP_CAP],
    )


def run_alt_from_pack(
    pack_dir: Path,
    alt_departure_time: datetime,
    route: RouteConfig,
    *,
    advisory_models: list[str] | None = None,
    enabled_ids: set[str] | None = None,
    advisory_enabled: dict[str, bool] | None = None,
    user_params: dict | None = None,
    aggregation: AdvisoryAggregation | None = None,
    airports_db_path: str | None = None,
    airport_conditions_recompute: Callable | None = None,
    icing_method: str | None = None,
    cloud_method: str | None = None,
    convective_method: str | None = None,
    locale: str | None = None,
    cruise_speed_ias_kt: float | None = None,
    cross_sections: list[RouteCrossSection] | None = None,
    persist: bool = True,
    detect_fronts: bool = True,
) -> AdvisoryResult:
    """Re-run analysis + advisories at an alt departure time using existing pack data.

    Loads cross-sections and route points from disk, calls
    ``analyze_all_route_points`` with the alt departure time (which picks
    different hourly forecasts via ``at_time()``), then evaluates advisories.
    Saves the result as ``route_advisories_alt.json``.

    The timing-scenario scan (``tasks/time_scan.py``) drives this in a
    per-candidate loop, so three knobs let it reuse the machinery without side
    effects: ``cross_sections`` skips the disk load and grades against the
    given (possibly enrichment-extended) sections; ``persist=False`` skips
    writing ``route_advisories_alt.json`` (candidates live in
    ``time_options.json``); ``detect_fronts=False`` skips the alt front re-run
    (``fronts`` is timing-``none`` and re-detecting per candidate would write
    files in a tight loop).
    """
    from weatherbrief.tasks.analyze import analyze_all_route_points
    from weatherbrief.tasks.artifacts import (
        load_cross_sections,
        load_elevation_profile,
        load_route_points,
        save_alt_advisory_artifacts,
    )

    if cross_sections is None:
        cross_sections = load_cross_sections(pack_dir)
    route_points = load_route_points(pack_dir)
    elevation = load_elevation_profile(pack_dir)

    if not cross_sections or route_points is None:
        return AdvisoryResult(manifest=None, error="Missing cross-section or route-point data")

    # Re-run analysis at the alt departure time
    rp_analyses = analyze_all_route_points(
        cross_sections=cross_sections,
        route_points=route_points,
        departure_time=alt_departure_time,
        duration_hours=route.flight_duration_hours,
        cruise_altitude_ft=route.cruise_altitude_ft,
        flight_ceiling_ft=route.flight_ceiling_ft,
        elevation=elevation,
    )

    if not rp_analyses:
        return AdvisoryResult(manifest=None, error="Alt analysis produced no results")

    rp_analyses = _resolve_analyses(rp_analyses, icing_method, cloud_method, convective_method)

    total_distance = route_points[-1].distance_from_origin_nm
    model_names = list(rp_analyses[0].sounding.keys()) if rp_analyses else []
    advisory_model_names = _compute_advisory_model_names(model_names, advisory_models)

    # Airport conditions
    airport_conds: AirportConditions | None = None
    if airport_conditions_recompute is not None:
        airport_conds = airport_conditions_recompute(
            rp_analyses, cross_sections, advisory_model_names,
        )
    elif airports_db_path:
        airport_conds = _compute_airport_conditions(
            rp_analyses, cross_sections, advisory_model_names,
            route, airports_db_path,
        )

    # Evaluate advisories
    try:
        from weatherbrief.analysis.advisories import RouteContext, evaluate_all, get_catalog

        # Re-run front detection at the ALT ETAs. The alt analyses carry shifted
        # ``interpolated_time`` (re-analysed at ``alt_departure_time``), so over a
        # multi-hour offset a fast front sits at a different route position than at
        # the primary departure — reusing the primary artifact would grade the alt
        # scenario against stale fronts. Re-sampling the same snapshot at the alt
        # hours costs ~nothing. Gated on the primary artifact's presence (= the
        # ``auto_front_detection`` master was on at briefing time).
        if detect_fronts and pack_dir is not None and (pack_dir / "route_fronts.json").exists():
            try:
                from weatherbrief.tasks.fronts import run_fronts

                run_fronts(
                    rp_analyses,
                    route_name=route.name,
                    cruise_altitude_ft=route.cruise_altitude_ft,
                    advisory_models=advisory_models,
                    pack_dir=pack_dir,
                    out_name="route_fronts_alt.json",
                )
            except Exception:
                logger.warning("Alt front detection failed", exc_info=True)
        route_fronts, enabled_ids = _front_context(
            pack_dir, enabled_ids, advisory_enabled, filename="route_fronts_alt.json",
        )

        ctx = RouteContext(
            analyses=rp_analyses,
            cross_sections=cross_sections,
            elevation=elevation,
            models=advisory_model_names,
            cruise_altitude_ft=route.cruise_altitude_ft,
            flight_ceiling_ft=route.flight_ceiling_ft,
            total_distance_nm=total_distance,
            airport_conditions=airport_conds,
            locale=locale,
            route_fronts=route_fronts,
            cruise_speed_ias_kt=cruise_speed_ias_kt,
            flight_duration_hours=route.flight_duration_hours,
        )
        effective_aggregation = aggregation or AdvisoryAggregation.MAJORITY
        advisory_results = evaluate_all(ctx, enabled_ids, user_params, aggregation=effective_aggregation)
        manifest = RouteAdvisoriesManifest(
            advisories=advisory_results,
            catalog=get_catalog(),
            route_name=route.name,
            cruise_altitude_ft=route.cruise_altitude_ft,
            flight_ceiling_ft=route.flight_ceiling_ft,
            total_distance_nm=total_distance,
            models=advisory_model_names,
            aggregation=effective_aggregation.value,
            airport_conditions=airport_conds,
        )

        if persist:
            save_alt_advisory_artifacts(pack_dir, manifest)
        logger.info("Alt advisory evaluation complete: %d advisories", len(advisory_results))

        return AdvisoryResult(manifest=manifest, airport_conditions=airport_conds)
    except Exception as exc:
        logger.warning("Alt advisory evaluation failed", exc_info=True)
        return AdvisoryResult(manifest=None, error=str(exc))
