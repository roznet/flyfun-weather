"""Fetch task — weather data retrieval for all route points.

Extracted from ``pipeline.py`` lines 138-213.  Can run independently
and persist its artifacts to a pack directory.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from weatherbrief.fetch.open_meteo import OpenMeteoClient
from weatherbrief.fetch.route_points import interpolate_route
from weatherbrief.fetch.variables import MODEL_ENDPOINTS, ModelRegion, detect_model_region
from weatherbrief.models import (
    ElevationProfile,
    ModelSource,
    RouteConfig,
    RouteCrossSection,
    RoutePoint,
    WaypointForecast,
)

logger = logging.getLogger(__name__)


def _should_skip_for_region(endpoint, route_region: ModelRegion) -> bool:
    """Return True if a model's coverage region doesn't match the route."""
    if endpoint.region == ModelRegion.GLOBAL or route_region == ModelRegion.GLOBAL:
        return False
    return endpoint.region != route_region


@dataclass
class FetchResult:
    """Output of the fetch stage."""

    route_points: list[RoutePoint]
    all_forecasts: list[WaypointForecast]
    cross_sections: list[RouteCrossSection]
    elevation_profile: ElevationProfile | None
    models_fetched: list[str]
    models_skipped_region: list[str] = field(default_factory=list)
    grib_enriched: bool = False
    grib_enrichment_failed: bool = False
    grib_init_times: dict[str, int] = field(default_factory=dict)


def run_fetch(
    route: RouteConfig,
    target_date: str,
    target_hour: int,
    models: list[ModelSource],
    enrich_grib: bool = False,
    data_dir: Path | None = None,
    pack_dir: Path | None = None,
    user_id: str | None = None,
    progress_callback: Callable[[str, str | None], None] | None = None,
) -> FetchResult:
    """Execute the fetch stage of the briefing pipeline.

    Interpolates route → fetches elevation → fetches forecasts per model
    → optional GRIB enrichment.  If *pack_dir* is set, persists artifacts.
    """
    from datetime import date, datetime, timezone

    def _notify(stage: str, detail: str | None = None) -> None:
        if progress_callback is not None:
            progress_callback(stage, detail)

    today_utc = datetime.now(timezone.utc).date()
    days_out = (date.fromisoformat(target_date) - today_utc).days

    # --- Route interpolation ---
    _notify("route_interpolation")
    client = OpenMeteoClient()
    route_points = interpolate_route(route, spacing_nm=10.0)
    logger.info("Route interpolated: %d points along %.0f nm",
                len(route_points), route_points[-1].distance_from_origin_nm)

    # --- Elevation profile ---
    elevation_profile = None
    _notify("elevation_profile")
    try:
        from weatherbrief.fetch.elevation import get_elevation_profile

        elevation_profile = get_elevation_profile(route, spacing_nm=0.5)
        logger.info("Elevation profile: %d points, max %.0f ft",
                     len(elevation_profile.points), elevation_profile.max_elevation_ft)
    except Exception:
        logger.warning("Elevation profile failed", exc_info=True)

    # --- Fetch forecasts per model ---
    all_forecasts: list[WaypointForecast] = []
    cross_sections: list[RouteCrossSection] = []
    models_fetched_names: list[str] = []
    models_skipped_region: list[str] = []
    models_fetched_count = 0

    route_region = detect_model_region(route)

    for model in models:
        endpoint = MODEL_ENDPOINTS[model.value]
        if days_out is not None and days_out >= endpoint.max_days:
            logger.info(
                "Skipping %s: %d days out exceeds %d-day range",
                model.value, days_out, endpoint.max_days,
            )
            continue
        if _should_skip_for_region(endpoint, route_region):
            logger.info(
                "Skipping %s: %s model not relevant for %s route",
                model.value, endpoint.region.value, route_region.value,
            )
            models_skipped_region.append(model.value)
            continue
        # Delay between model fetches to avoid Open-Meteo rate limiting
        if models_fetched_count > 0:
            time.sleep(5)
        _notify("fetch_forecasts", model.value)
        try:
            point_forecasts = client.fetch_multi_point(
                route_points, model,
                start_date=target_date, end_date=target_date,
            )
            # Extract waypoint-only forecasts for analysis
            for rp, fc in zip(route_points, point_forecasts):
                if rp.waypoint_icao:
                    all_forecasts.append(fc)
            # Store the full cross-section
            cross_sections.append(RouteCrossSection(
                model=model,
                route_points=route_points,
                fetched_at=point_forecasts[0].fetched_at,
                point_forecasts=point_forecasts,
            ))
            logger.info("Fetched %s: %d points", model.value, len(point_forecasts))
            models_fetched_count += 1
            models_fetched_names.append(model.value)
        except Exception:
            logger.warning("Failed to fetch %s", model.value, exc_info=True)

    # --- GRIB2 enrichment (optional) ---
    grib_enriched = False
    grib_enrichment_failed = False
    grib_init_times: dict[str, int] = {}
    if enrich_grib and cross_sections:
        _notify("grib_enrichment")
        try:
            from weatherbrief.fetch.grib import enrich_forecasts

            grib_init_times = enrich_forecasts(
                cross_sections, all_forecasts, route_points,
                target_date, target_hour, data_dir=data_dir,
                flight_duration_hours=route.flight_duration_hours,
                progress_callback=progress_callback,
            )
            grib_enriched = True
            logger.info("GRIB2 enrichment applied")
        except Exception:
            grib_enrichment_failed = True
            logger.warning(
                "GRIB2 enrichment failed, using Open-Meteo data only",
                exc_info=True,
            )

    # --- Persist artifacts ---
    if pack_dir:
        from weatherbrief.tasks.artifacts import save_fetch_artifacts

        save_fetch_artifacts(
            pack_dir, cross_sections, elevation_profile, route_points,
            models_fetched=models_fetched_names,
        )

    return FetchResult(
        route_points=route_points,
        all_forecasts=all_forecasts,
        cross_sections=cross_sections,
        elevation_profile=elevation_profile,
        models_fetched=models_fetched_names,
        models_skipped_region=models_skipped_region,
        grib_enriched=grib_enriched,
        grib_enrichment_failed=grib_enrichment_failed,
        grib_init_times=grib_init_times,
    )
