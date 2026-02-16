"""GRIB2 enrichment for GFS Cloud Liquid Water (CLWMR) and Ice Mixing Ratio (ICMR).

Public API: enrich_forecasts() adds CLWMR/ICMR data to existing cross-section
forecasts by downloading targeted byte ranges from GFS GRIB2 files on AWS S3.
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
from weatherbrief.fetch.grib.gfs_idx import plan_byte_ranges
from weatherbrief.fetch.grib.grib_fetch import (
    bracket_forecast_hours,
    fetch_byte_ranges,
    fetch_idx,
    find_latest_run,
)
from weatherbrief.models import RouteCrossSection, RoutePoint, WaypointForecast

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
    """Enrich cross-section forecasts with CLWMR/ICMR from GFS GRIB2.

    This modifies PressureLevelData objects in-place, setting
    cloud_liquid_water_kg_kg and ice_mixing_ratio_kg_kg fields.

    Only enriches GFS cross-sections. Other models are skipped.

    Args:
        cross_sections: Route cross-sections to enrich (modified in-place).
        all_forecasts: Waypoint forecasts (also enriched in-place).
        route_points: Route points for spatial interpolation.
        target_date: ISO date string (YYYY-MM-DD).
        target_hour: Target hour (UTC).
        data_dir: Base data directory for caching.
    """
    from weatherbrief.models import ModelSource

    # Only enrich GFS data
    gfs_sections = [cs for cs in cross_sections if cs.model == ModelSource.GFS]
    if not gfs_sections:
        logger.info("No GFS cross-sections to enrich")
        return

    # Target time for finding the right model run
    target_time = datetime.strptime(
        f"{target_date}T{target_hour:02d}", "%Y-%m-%dT%H"
    ).replace(tzinfo=timezone.utc)

    session = requests.Session()

    # Find the latest available GFS run
    run_info = find_latest_run(target_time, session=session)
    if run_info is None:
        logger.warning("No GFS model run found for enrichment")
        return

    init_date, init_hour = run_info

    # Bracket the target time with forecast hours
    f_prev, f_next = bracket_forecast_hours(init_date, init_hour, target_time)

    # Route bounding box for cache key (rounded to nearest degree + 1° buffer)
    lats = [rp.lat for rp in route_points]
    lons = [rp.lon for rp in route_points]
    bbox = (
        int(min(lats)) - 1,
        int(max(lats)) + 2,
        int(min(lons)) - 1,
        int(max(lons)) + 2,
    )

    # Extract target pressure levels from existing forecasts
    target_levels: list[int] = []
    for cs in gfs_sections:
        for pf in cs.point_forecasts:
            for h in pf.hourly:
                for pl in h.pressure_levels:
                    if pl.pressure_hpa not in target_levels:
                        target_levels.append(pl.pressure_hpa)
                break  # One hourly is enough to get the level list
            break
    target_levels.sort(reverse=True)

    # Purge old cache entries
    purge_old_runs(data_dir)

    # Download GRIB2 data for each bracketing forecast hour
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour)
    grib_data_by_fhour: dict[int, bytes] = {}

    for fhour in sorted({f_prev, f_next}):
        ck = cache_key(fhour, "CLWMR_ICMR", bbox)
        cached = get_cached(run_dir, ck)
        if cached is not None:
            grib_data_by_fhour[fhour] = cached
            continue

        try:
            idx_text = fetch_idx(init_date, init_hour, fhour, session=session)
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
        logger.warning("No GRIB2 data retrieved for enrichment")
        return

    # Decode and interpolate
    from weatherbrief.fetch.grib.decode import decode_grib_per_point

    point_lats = [rp.lat for rp in route_points]
    point_lons = [rp.lon for rp in route_points]

    # Decode each forecast hour
    decoded_by_fhour: dict[int, list[dict[int, dict[str, float]]]] = {}
    for fhour, grib_bytes in grib_data_by_fhour.items():
        decoded_by_fhour[fhour] = decode_grib_per_point(
            grib_bytes, point_lats, point_lons,
        )

    # Merge into cross-section forecasts
    # Use the closest available forecast hour's data (simple nearest-neighbor
    # in time; linear temporal interpolation would be a future enhancement)
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
    # Build a lookup from route points to decoded data for waypoint ICAOs
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
