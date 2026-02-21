"""GRIB2 enrichment for GFS Cloud Liquid Water (CLWMR), Ice Mixing Ratio (ICMR),
and cloud layer diagnostics.

Public API: enrich_forecasts() adds CLWMR/ICMR data and cloud diagnostics to
existing cross-section forecasts by downloading targeted byte ranges from GFS
GRIB2 files on AWS S3.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

from weatherbrief.fetch.grib.cache import (
    cache_dir_for_run,
    cache_key,
    get_cached,
    purge_old_runs,
    put_cached,
)
from weatherbrief.fetch.grib.gfs_idx import plan_byte_ranges, plan_cloud_diag_byte_ranges
from weatherbrief.fetch.grib.grib_fetch import (
    bracket_forecast_hours,
    fetch_byte_ranges,
    fetch_cloud_diag_ranges,
    fetch_idx,
    find_latest_run,
)
from weatherbrief.models import ModelSource, RouteCrossSection, RoutePoint, WaypointForecast

logger = logging.getLogger(__name__)


def enrich_forecasts(
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    target_date: str,
    target_hour: int,
    *,
    data_dir: Path,
) -> None:
    """Enrich cross-section forecasts with CLWMR/ICMR and cloud diagnostics from GFS GRIB2.

    This modifies PressureLevelData and HourlyForecast objects in-place.

    Only enriches GFS cross-sections. Other models are skipped.

    Args:
        cross_sections: Route cross-sections to enrich (modified in-place).
        all_forecasts: Waypoint forecasts (also enriched in-place).
        route_points: Route points for spatial interpolation.
        target_date: ISO date string (YYYY-MM-DD).
        target_hour: Target hour (UTC).
        data_dir: Base data directory for caching.
    """
    # Only enrich GFS data
    gfs_sections = [cs for cs in cross_sections if cs.model == ModelSource.GFS]
    if not gfs_sections:
        logger.info("No GFS cross-sections to enrich")
        return

    # Shared setup: session, run discovery, bbox, forecast hours
    target_time = datetime.strptime(
        f"{target_date}T{target_hour:02d}", "%Y-%m-%dT%H"
    ).replace(tzinfo=timezone.utc)

    session = requests.Session()

    run_info = find_latest_run(target_time, session=session)
    if run_info is None:
        logger.warning("No GFS model run found for enrichment")
        return

    init_date, init_hour = run_info
    f_prev, f_next = bracket_forecast_hours(init_date, init_hour, target_time)

    # Route bounding box (rounded to nearest degree + 1° buffer)
    lats = [rp.lat for rp in route_points]
    lons = [rp.lon for rp in route_points]
    bbox = (
        int(min(lats)) - 1,
        int(max(lats)) + 2,
        int(min(lons)) - 1,
        int(max(lons)) + 2,
    )

    purge_old_runs(data_dir)
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour)

    point_lats = [rp.lat for rp in route_points]
    point_lons = [rp.lon for rp in route_points]

    # Fetch .idx text (shared by both enrichment paths)
    idx_by_fhour: dict[int, str] = {}
    for fhour in sorted({f_prev, f_next}):
        try:
            idx_by_fhour[fhour] = fetch_idx(init_date, init_hour, fhour, session=session)
        except Exception:
            logger.warning("Failed to fetch .idx for f%03d", fhour, exc_info=True)

    if not idx_by_fhour:
        logger.warning("No .idx files retrieved for enrichment")
        return

    # Run both enrichment paths
    _enrich_clwmr_icmr(
        gfs_sections, all_forecasts, route_points,
        init_date, init_hour, f_prev, f_next, bbox,
        run_dir, idx_by_fhour, point_lats, point_lons, session,
    )
    _enrich_cloud_diagnostics(
        gfs_sections, all_forecasts, route_points,
        init_date, init_hour, f_prev, f_next, bbox,
        run_dir, idx_by_fhour, point_lats, point_lons, session,
    )


def _enrich_clwmr_icmr(
    gfs_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    init_date: str,
    init_hour: int,
    f_prev: int,
    f_next: int,
    bbox: tuple[int, int, int, int],
    run_dir: Path,
    idx_by_fhour: dict[int, str],
    point_lats: list[float],
    point_lons: list[float],
    session: requests.Session,
) -> None:
    """Enrich pressure-level data with CLWMR/ICMR from GFS GRIB2."""
    # Extract target pressure levels from existing forecasts
    target_levels: list[int] = []
    for cs in gfs_sections:
        for pf in cs.point_forecasts:
            for h in pf.hourly:
                for pl in h.pressure_levels:
                    if pl.pressure_hpa not in target_levels:
                        target_levels.append(pl.pressure_hpa)
                break
            break
    target_levels.sort(reverse=True)

    # Download GRIB2 data for each bracketing forecast hour
    grib_data_by_fhour: dict[int, bytes] = {}
    for fhour in sorted({f_prev, f_next}):
        ck = cache_key(fhour, "CLWMR_ICMR", bbox)
        cached = get_cached(run_dir, ck)
        if cached is not None:
            grib_data_by_fhour[fhour] = cached
            continue

        idx_text = idx_by_fhour.get(fhour)
        if idx_text is None:
            continue

        try:
            ranges = plan_byte_ranges(idx_text, target_levels=target_levels)
            if not ranges:
                logger.warning("No CLWMR/ICMR found in .idx for f%03d", fhour)
                continue
            grib_bytes = fetch_byte_ranges(
                init_date, init_hour, fhour, ranges, session=session,
            )
            if grib_bytes:
                put_cached(run_dir, ck, grib_bytes)
                grib_data_by_fhour[fhour] = grib_bytes
                logger.info(
                    "Downloaded GRIB2 f%03d: %d ranges, %.1f KB",
                    fhour, len(ranges), len(grib_bytes) / 1024,
                )
        except Exception:
            logger.warning("Failed to fetch GRIB2 f%03d", fhour, exc_info=True)
            continue

    if not grib_data_by_fhour:
        logger.warning("No GRIB2 CLWMR/ICMR data retrieved for enrichment")
        return

    # Decode and interpolate
    from weatherbrief.fetch.grib.decode import decode_grib_per_point

    decoded_by_fhour: dict[int, list[dict[int, dict[str, float]]]] = {}
    for fhour, grib_bytes in grib_data_by_fhour.items():
        decoded_by_fhour[fhour] = decode_grib_per_point(
            grib_bytes, point_lats, point_lons,
        )

    # Merge into cross-section forecasts (nearest-neighbor in time)
    primary_fhour = f_prev if f_prev in decoded_by_fhour else f_next
    if primary_fhour not in decoded_by_fhour:
        primary_fhour = next(iter(decoded_by_fhour))
    decoded_points = decoded_by_fhour[primary_fhour]

    enriched_count = 0
    for cs in gfs_sections:
        for point_idx, wf in enumerate(cs.point_forecasts):
            if point_idx >= len(decoded_points):
                break
            point_data = decoded_points[point_idx]
            if not point_data:
                continue

            for hourly in wf.hourly:
                for pl in hourly.pressure_levels:
                    level_data = point_data.get(pl.pressure_hpa)
                    if level_data is None:
                        continue

                    clwmr = level_data.get("cloud_liquid_water_kg_kg")
                    if clwmr is not None:
                        pl.cloud_liquid_water_kg_kg = clwmr
                        enriched_count += 1

                    icmr = level_data.get("ice_mixing_ratio_kg_kg")
                    if icmr is not None:
                        pl.ice_mixing_ratio_kg_kg = icmr

    # Also enrich waypoint-only forecasts
    wp_data_lookup: dict[str, dict[int, dict[str, float]]] = {}
    for rp, pd in zip(route_points, decoded_points):
        if rp.waypoint_icao and pd:
            wp_data_lookup[rp.waypoint_icao] = pd

    for wf in all_forecasts:
        if wf.model.value != "gfs":
            continue
        wp_icao = wf.waypoint.icao
        point_data = wp_data_lookup.get(wp_icao)
        if not point_data:
            continue
        for hourly in wf.hourly:
            for pl in hourly.pressure_levels:
                level_data = point_data.get(pl.pressure_hpa)
                if level_data is None:
                    continue
                clwmr = level_data.get("cloud_liquid_water_kg_kg")
                if clwmr is not None:
                    pl.cloud_liquid_water_kg_kg = clwmr
                icmr = level_data.get("ice_mixing_ratio_kg_kg")
                if icmr is not None:
                    pl.ice_mixing_ratio_kg_kg = icmr

    logger.info("GRIB2 enrichment: %d pressure levels enriched with CLWMR/ICMR", enriched_count)


def _enrich_cloud_diagnostics(
    gfs_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    init_date: str,
    init_hour: int,
    f_prev: int,
    f_next: int,
    bbox: tuple[int, int, int, int],
    run_dir: Path,
    idx_by_fhour: dict[int, str],
    point_lats: list[float],
    point_lons: list[float],
    session: requests.Session,
) -> None:
    """Enrich forecasts with GFS cloud layer diagnostics."""
    from weatherbrief.fetch.grib.decode import (
        build_cloud_diagnostics,
        decode_cloud_diag_per_point,
    )

    # Download GRIB2 cloud diagnostic data
    grib_data_by_fhour: dict[int, bytes] = {}
    for fhour in sorted({f_prev, f_next}):
        ck = cache_key(fhour, "CLOUD_DIAG", bbox)
        cached = get_cached(run_dir, ck)
        if cached is not None:
            grib_data_by_fhour[fhour] = cached
            continue

        idx_text = idx_by_fhour.get(fhour)
        if idx_text is None:
            continue

        try:
            ranges = plan_cloud_diag_byte_ranges(idx_text)
            if not ranges:
                logger.warning("No cloud diag found in .idx for f%03d", fhour)
                continue
            grib_bytes = fetch_cloud_diag_ranges(
                init_date, init_hour, fhour, ranges, session=session,
            )
            if grib_bytes:
                put_cached(run_dir, ck, grib_bytes)
                grib_data_by_fhour[fhour] = grib_bytes
                logger.info(
                    "Downloaded cloud diag f%03d: %d ranges, %.1f KB",
                    fhour, len(ranges), len(grib_bytes) / 1024,
                )
        except Exception:
            logger.warning("Failed to fetch cloud diag f%03d", fhour, exc_info=True)
            continue

    if not grib_data_by_fhour:
        logger.warning("No cloud diagnostic GRIB2 data retrieved")
        return

    # Decode each forecast hour
    decoded_by_fhour: dict[int, list[dict[str, float]]] = {}
    for fhour, grib_bytes in grib_data_by_fhour.items():
        decoded_by_fhour[fhour] = decode_cloud_diag_per_point(
            grib_bytes, point_lats, point_lons,
        )

    # Use closest forecast hour
    primary_fhour = f_prev if f_prev in decoded_by_fhour else f_next
    if primary_fhour not in decoded_by_fhour:
        primary_fhour = next(iter(decoded_by_fhour))
    decoded_points = decoded_by_fhour[primary_fhour]

    # Build NWPCloudDiagnostics per point and merge into forecasts
    diagnostics_per_point = [build_cloud_diagnostics(raw) for raw in decoded_points]

    enriched_count = 0
    for cs in gfs_sections:
        for point_idx, wf in enumerate(cs.point_forecasts):
            if point_idx >= len(diagnostics_per_point):
                break
            diag = diagnostics_per_point[point_idx]
            if diag is None:
                continue
            for hourly in wf.hourly:
                hourly.nwp_cloud_diagnostics = diag
                enriched_count += 1

    # Also enrich waypoint-only forecasts
    wp_diag_lookup: dict[str, object] = {}
    for rp, diag in zip(route_points, diagnostics_per_point):
        if rp.waypoint_icao and diag is not None:
            wp_diag_lookup[rp.waypoint_icao] = diag

    for wf in all_forecasts:
        if wf.model.value != "gfs":
            continue
        diag = wp_diag_lookup.get(wf.waypoint.icao)
        if diag is None:
            continue
        for hourly in wf.hourly:
            hourly.nwp_cloud_diagnostics = diag

    logger.info("GRIB2 enrichment: %d hourly entries enriched with cloud diagnostics", enriched_count)
