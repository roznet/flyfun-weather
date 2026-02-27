"""GRIB2 enrichment for cloud liquid water and ice mixing ratio.

Public API: enrich_forecasts() adds CLWMR/ICMR data and cloud diagnostics to
existing cross-section forecasts from GFS and ICON-EU GRIB2 data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

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
    fetch_byte_ranges,
    fetch_cloud_diag_ranges,
    fetch_idx,
    find_latest_run,
)
from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    NWPCloudDiagnostics,
    RouteCrossSection,
    RoutePoint,
    WaypointForecast,
)

logger = logging.getLogger(__name__)


def _apply_cloud_diagnostics(hourly: HourlyForecast, diag: NWPCloudDiagnostics) -> None:
    """Attach NWP cloud diagnostics and override Open-Meteo cloud cover for consistency."""
    hourly.nwp_cloud_diagnostics = diag
    if diag.low.cover_pct is not None:
        hourly.cloud_cover_low_pct = diag.low.cover_pct
    if diag.mid.cover_pct is not None:
        hourly.cloud_cover_mid_pct = diag.mid.cover_pct
    if diag.high.cover_pct is not None:
        hourly.cloud_cover_high_pct = diag.high.cover_pct


def _forecast_hour_to_utc(init_date: str, init_hour: int, fhour: int) -> datetime:
    """Convert a GRIB run + forecast hour to an aware UTC datetime."""
    init_dt = datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc,
    )
    return init_dt + timedelta(hours=fhour)


def _run_info_to_timestamp(init_date: str, init_hour: int) -> int:
    """Convert GRIB run info (date string + hour) to a Unix timestamp."""
    return int(
        datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


def enrich_forecasts(
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    target_date: str,
    target_hour: int,
    *,
    data_dir: Path,
    flight_duration_hours: float = 0.0,
    progress_callback: Callable[[str, str | None], None] | None = None,
) -> dict[str, int]:
    """Enrich cross-section forecasts with cloud water from GRIB2 sources.

    Enriches GFS cross-sections with CLWMR/ICMR and cloud diagnostics.
    Enriches ICON cross-sections with QC/QI if route is within ICON-EU domain.

    This modifies PressureLevelData and HourlyForecast objects in-place.

    Args:
        cross_sections: Route cross-sections to enrich (modified in-place).
        all_forecasts: Waypoint forecasts (also enriched in-place).
        route_points: Route points for spatial interpolation.
        target_date: ISO date string (YYYY-MM-DD).
        target_hour: Target hour (UTC).
        data_dir: Base data directory for caching.
        flight_duration_hours: Flight duration for per-hour enrichment.

    Returns:
        Dict mapping model name to GRIB init Unix timestamp (only non-None).
    """
    grib_init_times: dict[str, int] = {}

    if progress_callback is not None:
        progress_callback("grib_enrichment", "GFS")
    gfs_ts = _enrich_gfs(
        cross_sections, all_forecasts, route_points,
        target_date, target_hour, data_dir=data_dir,
        flight_duration_hours=flight_duration_hours,
    )
    if gfs_ts is not None:
        grib_init_times["gfs"] = gfs_ts

    if progress_callback is not None:
        progress_callback("grib_enrichment", "ICON-EU")
    icon_ts = _enrich_icon_eu(
        cross_sections, all_forecasts, route_points,
        target_date, target_hour, data_dir=data_dir,
        flight_duration_hours=flight_duration_hours,
    )
    if icon_ts is not None:
        grib_init_times["icon"] = icon_ts

    # Fill interpolated hours that lack GRIB diagnostics from nearest native step
    _propagate_cloud_diagnostics(cross_sections, all_forecasts)

    return grib_init_times


# ---------------------------------------------------------------------------
# GFS enrichment
# ---------------------------------------------------------------------------


def _enrich_gfs(
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    target_date: str,
    target_hour: int,
    *,
    data_dir: Path,
    flight_duration_hours: float = 0.0,
) -> int | None:
    """Enrich GFS cross-sections with CLWMR/ICMR and cloud diagnostics.

    Returns the GRIB init Unix timestamp, or None if enrichment was skipped.
    """
    from weatherbrief.fetch.grib.grib_fetch import compute_flight_window_hours

    gfs_sections = [cs for cs in cross_sections if cs.model == ModelSource.GFS]
    if not gfs_sections:
        logger.info("No GFS cross-sections to enrich")
        return None

    target_time = datetime.strptime(
        f"{target_date}T{target_hour:02d}", "%Y-%m-%dT%H"
    ).replace(tzinfo=timezone.utc)

    session = requests.Session()

    run_info = find_latest_run(target_time, session=session)
    if run_info is None:
        logger.warning("No GFS model run found for enrichment")
        return None

    init_date, init_hour = run_info
    forecast_hours = compute_flight_window_hours(
        init_date, init_hour, target_date, target_hour, flight_duration_hours,
    )

    # Route bounding box (rounded to nearest degree + 1° buffer)
    lats = [rp.lat for rp in route_points]
    lons = [rp.lon for rp in route_points]
    bbox = (
        int(min(lats)) - 1,
        int(max(lats)) + 2,
        int(min(lons)) - 1,
        int(max(lons)) + 2,
    )

    purge_old_runs(data_dir, model="gfs")
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="gfs")

    point_lats = [rp.lat for rp in route_points]
    point_lons = [rp.lon for rp in route_points]

    # Fetch .idx text (shared by both enrichment paths)
    idx_by_fhour: dict[int, str] = {}
    for fhour in forecast_hours:
        try:
            idx_by_fhour[fhour] = fetch_idx(init_date, init_hour, fhour, session=session)
        except Exception:
            logger.warning("Failed to fetch .idx for f%03d", fhour, exc_info=True)

    if not idx_by_fhour:
        logger.warning("No .idx files retrieved for enrichment")
        return None

    # Run both enrichment paths
    _enrich_clwmr_icmr(
        gfs_sections, all_forecasts, route_points,
        init_date, init_hour, forecast_hours, bbox,
        run_dir, idx_by_fhour, point_lats, point_lons, session,
    )
    _enrich_cloud_diagnostics(
        gfs_sections, all_forecasts, route_points,
        init_date, init_hour, forecast_hours, bbox,
        run_dir, idx_by_fhour, point_lats, point_lons, session,
    )

    return _run_info_to_timestamp(init_date, init_hour)


def _fetch_clwmr_icmr_for_fhour(
    init_date: str,
    init_hour: int,
    fhour: int,
    target_levels: list[int],
    bbox: tuple[int, int, int, int],
    run_dir: Path,
    idx_by_fhour: dict[int, str],
    point_lats: list[float],
    point_lons: list[float],
    session: requests.Session,
) -> list[dict[int, dict[str, float]]] | None:
    """Fetch, cache, decode CLWMR/ICMR for a single GFS forecast hour."""
    from weatherbrief.fetch.grib.decode import decode_grib_per_point

    ck = cache_key(fhour, "CLWMR_ICMR", bbox)
    grib_bytes = get_cached(run_dir, ck)
    if grib_bytes is None:
        idx_text = idx_by_fhour.get(fhour)
        if idx_text is None:
            return None
        try:
            ranges = plan_byte_ranges(idx_text, target_levels=target_levels)
            if not ranges:
                logger.warning("No CLWMR/ICMR found in .idx for f%03d", fhour)
                return None
            grib_bytes = fetch_byte_ranges(
                init_date, init_hour, fhour, ranges, session=session,
            )
            if not grib_bytes:
                return None
            put_cached(run_dir, ck, grib_bytes)
            logger.info(
                "Downloaded GRIB2 f%03d: %d ranges, %.1f KB",
                fhour, len(ranges), len(grib_bytes) / 1024,
            )
        except Exception:
            logger.warning("Failed to fetch GRIB2 f%03d", fhour, exc_info=True)
            return None

    return decode_grib_per_point(grib_bytes, point_lats, point_lons)


def _enrich_clwmr_icmr(
    gfs_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
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

    total_enriched = 0
    for fhour in forecast_hours:
        decoded_points = _fetch_clwmr_icmr_for_fhour(
            init_date, init_hour, fhour, target_levels, bbox,
            run_dir, idx_by_fhour, point_lats, point_lons, session,
        )
        if not decoded_points:
            continue

        valid_utc = _forecast_hour_to_utc(init_date, init_hour, fhour)
        total_enriched += _merge_cloud_water_into_sections(
            gfs_sections, all_forecasts, route_points, decoded_points, "gfs",
            valid_utc=valid_utc,
        )

    if total_enriched:
        logger.info(
            "GRIB2 GFS enrichment: %d pressure levels enriched with cloud water",
            total_enriched,
        )
    else:
        logger.warning("No GRIB2 CLWMR/ICMR data retrieved for enrichment")


def _fetch_cloud_diag_for_fhour(
    init_date: str,
    init_hour: int,
    fhour: int,
    bbox: tuple[int, int, int, int],
    run_dir: Path,
    idx_by_fhour: dict[int, str],
    point_lats: list[float],
    point_lons: list[float],
    session: requests.Session,
) -> list[dict[str, float]] | None:
    """Fetch, cache, decode cloud diagnostics for a single GFS forecast hour."""
    from weatherbrief.fetch.grib.decode import decode_cloud_diag_per_point

    ck = cache_key(fhour, "CLOUD_DIAG", bbox)
    grib_bytes = get_cached(run_dir, ck)
    if grib_bytes is None:
        idx_text = idx_by_fhour.get(fhour)
        if idx_text is None:
            return None
        try:
            ranges = plan_cloud_diag_byte_ranges(idx_text)
            if not ranges:
                logger.warning("No cloud diag found in .idx for f%03d", fhour)
                return None
            grib_bytes = fetch_cloud_diag_ranges(
                init_date, init_hour, fhour, ranges, session=session,
            )
            if not grib_bytes:
                return None
            put_cached(run_dir, ck, grib_bytes)
            logger.info(
                "Downloaded cloud diag f%03d: %d ranges, %.1f KB",
                fhour, len(ranges), len(grib_bytes) / 1024,
            )
        except Exception:
            logger.warning("Failed to fetch cloud diag f%03d", fhour, exc_info=True)
            return None

    return decode_cloud_diag_per_point(grib_bytes, point_lats, point_lons)


def _apply_cloud_diagnostics_to_sections(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    diagnostics_per_point: list[NWPCloudDiagnostics | None],
    model_value: str,
    valid_utc: datetime | None = None,
) -> int:
    """Merge cloud diagnostics into cross-section and waypoint forecasts.

    Args:
        valid_utc: If set, only enrich hourly entries matching this UTC hour.

    Returns:
        Number of hourly entries enriched.
    """
    enriched_count = 0
    for cs in sections:
        for point_idx, wf in enumerate(cs.point_forecasts):
            if point_idx >= len(diagnostics_per_point):
                break
            diag = diagnostics_per_point[point_idx]
            if diag is None:
                continue
            for hourly in wf.hourly:
                if valid_utc is not None and hourly.time.hour != valid_utc.hour:
                    continue
                _apply_cloud_diagnostics(hourly, diag)
                enriched_count += 1

    # Also enrich waypoint-only forecasts
    wp_diag_lookup: dict[str, NWPCloudDiagnostics] = {}
    for rp, diag in zip(route_points, diagnostics_per_point):
        if rp.waypoint_icao and diag is not None:
            wp_diag_lookup[rp.waypoint_icao] = diag

    for wf in all_forecasts:
        if wf.model.value != model_value:
            continue
        diag = wp_diag_lookup.get(wf.waypoint.icao)
        if diag is None:
            continue
        for hourly in wf.hourly:
            if valid_utc is not None and hourly.time.hour != valid_utc.hour:
                continue
            _apply_cloud_diagnostics(hourly, diag)

    return enriched_count


def _propagate_cloud_diagnostics(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
) -> None:
    """Forward-fill cloud diagnostics from native GRIB hours to interpolated hours.

    Open-Meteo provides hourly data interpolated between native GFS steps (3h at
    longer lead times).  GRIB enrichment only targets native steps, leaving
    interpolated hours without ``nwp_cloud_diagnostics``.  Without diagnostics the
    icing fallback applies the bulk NWP cloud percentage across the full altitude
    band, causing false positives.

    This fills each gap by copying diagnostics from the preceding enriched hour.
    Cloud layer geometry (base/top) changes slowly between 3-hour GFS steps, so
    the earlier step's diagnostics are a reasonable approximation.
    """
    def _fill_hourly_list(hourly_list: list[HourlyForecast]) -> int:
        filled = 0
        last_diag: NWPCloudDiagnostics | None = None
        for h in sorted(hourly_list, key=lambda h: h.time):
            if h.nwp_cloud_diagnostics is not None:
                last_diag = h.nwp_cloud_diagnostics
            elif last_diag is not None:
                _apply_cloud_diagnostics(h, last_diag)
                filled += 1
        return filled

    total = 0
    for cs in sections:
        for wf in cs.point_forecasts:
            total += _fill_hourly_list(wf.hourly)

    for wf in all_forecasts:
        total += _fill_hourly_list(wf.hourly)

    if total:
        logger.info(
            "Cloud diagnostics propagated to %d interpolated hourly entries", total,
        )


def _enrich_cloud_diagnostics(
    gfs_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    bbox: tuple[int, int, int, int],
    run_dir: Path,
    idx_by_fhour: dict[int, str],
    point_lats: list[float],
    point_lons: list[float],
    session: requests.Session,
) -> None:
    """Enrich forecasts with GFS cloud layer diagnostics."""
    from weatherbrief.fetch.grib.decode import build_cloud_diagnostics

    total_enriched = 0
    for fhour in forecast_hours:
        decoded_points = _fetch_cloud_diag_for_fhour(
            init_date, init_hour, fhour, bbox,
            run_dir, idx_by_fhour, point_lats, point_lons, session,
        )
        if not decoded_points:
            continue

        diagnostics_per_point = [build_cloud_diagnostics(raw) for raw in decoded_points]
        valid_utc = _forecast_hour_to_utc(init_date, init_hour, fhour)
        total_enriched += _apply_cloud_diagnostics_to_sections(
            gfs_sections, all_forecasts, route_points,
            diagnostics_per_point, "gfs", valid_utc=valid_utc,
        )

    if total_enriched:
        logger.info("GRIB2 enrichment: %d hourly entries enriched with cloud diagnostics", total_enriched)
    else:
        logger.warning("No cloud diagnostic GRIB2 data retrieved")


# ---------------------------------------------------------------------------
# ICON-EU enrichment
# ---------------------------------------------------------------------------


def _enrich_icon_eu(
    cross_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    target_date: str,
    target_hour: int,
    *,
    data_dir: Path,
    flight_duration_hours: float = 0.0,
) -> int | None:
    """Enrich ICON cross-sections with QC/QI from ICON-EU GRIB2.

    Returns the GRIB init Unix timestamp, or None if enrichment was skipped.
    """
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU_MODEL_LEVEL_MAX,
        ICON_EU_MODEL_LEVEL_MIN,
        ICON_EU_VARIABLES,
        compute_icon_eu_flight_window_hours,
        fetch_icon_eu_fields,
        find_latest_icon_eu_run,
        route_in_icon_eu_domain,
    )

    icon_sections = [cs for cs in cross_sections if cs.model == ModelSource.ICON]
    if not icon_sections:
        logger.debug("No ICON cross-sections to enrich")
        return None

    # Domain check — skip silently if route is outside ICON-EU bounds
    if not route_in_icon_eu_domain(route_points):
        logger.info("Route outside ICON-EU domain, skipping ICON-EU enrichment")
        return None

    target_time = datetime.strptime(
        f"{target_date}T{target_hour:02d}", "%Y-%m-%dT%H"
    ).replace(tzinfo=timezone.utc)

    session = requests.Session()

    try:
        run_info = find_latest_icon_eu_run(target_time, session=session)
    except Exception:
        logger.warning("Failed to find ICON-EU model run", exc_info=True)
        return None

    if run_info is None:
        logger.warning("No ICON-EU model run found for enrichment")
        return None

    init_date, init_hour = run_info
    forecast_hours = compute_icon_eu_flight_window_hours(
        init_date, init_hour, target_date, target_hour, flight_duration_hours,
    )

    purge_old_runs(data_dir, model="icon-eu")
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="icon-eu")

    # Route bounding box for cache key
    lats = [rp.lat for rp in route_points]
    lons = [rp.lon for rp in route_points]
    bbox = (
        int(min(lats)) - 1,
        int(max(lats)) + 2,
        int(min(lons)) - 1,
        int(max(lons)) + 2,
    )

    point_lats = [rp.lat for rp in route_points]
    point_lons = [rp.lon for rp in route_points]
    levels = list(range(ICON_EU_MODEL_LEVEL_MIN, ICON_EU_MODEL_LEVEL_MAX + 1))

    # ICON-EU GRIB data is ~190MB per fhour; decoding via cfgrib/xarray
    # expands to ~800MB+. Process one fhour at a time to limit memory.
    import gc

    from weatherbrief.fetch.grib.decode import decode_icon_eu_per_point

    def _fetch_and_decode_fhour(fhour: int) -> list[dict[int, dict[str, float]]] | None:
        ck = cache_key(fhour, "ICON_EU_QC_QI_P", bbox)
        grib_bytes = get_cached(run_dir, ck)
        if grib_bytes is None:
            try:
                grib_bytes = fetch_icon_eu_fields(
                    init_date, init_hour, fhour,
                    levels=levels,
                    variables=list(ICON_EU_VARIABLES),
                    session=session,
                )
                if grib_bytes:
                    put_cached(run_dir, ck, grib_bytes)
            except Exception:
                logger.warning("Failed to fetch ICON-EU f%03d", fhour, exc_info=True)
                return None
        if not grib_bytes:
            return None
        decoded = decode_icon_eu_per_point(grib_bytes, point_lats, point_lons)
        del grib_bytes
        gc.collect()
        return decoded

    total_enriched = 0
    for fhour in forecast_hours:
        decoded_points = _fetch_and_decode_fhour(fhour)
        if not decoded_points:
            continue

        valid_utc = _forecast_hour_to_utc(init_date, init_hour, fhour)
        total_enriched += _merge_cloud_water_into_sections(
            icon_sections, all_forecasts, route_points, decoded_points, "icon",
            valid_utc=valid_utc,
        )
        del decoded_points
        gc.collect()

    if not total_enriched:
        logger.warning("No ICON-EU GRIB2 data retrieved for enrichment")
        return None

    logger.info(
        "GRIB2 ICON enrichment: %d pressure levels enriched with cloud water",
        total_enriched,
    )

    # Cloud diagnostics (ceiling, convective base/top) from single-level files
    _enrich_icon_eu_cloud_diagnostics(
        icon_sections, all_forecasts, route_points,
        init_date, init_hour, forecast_hours, bbox,
        run_dir, point_lats, point_lons, session,
    )

    return _run_info_to_timestamp(init_date, init_hour)


def _enrich_icon_eu_cloud_diagnostics(
    icon_sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    bbox: tuple[int, int, int, int],
    run_dir: Path,
    point_lats: list[float],
    point_lons: list[float],
    session: requests.Session,
) -> None:
    """Enrich ICON forecasts with single-level cloud diagnostics (ceiling, etc.)."""
    from weatherbrief.fetch.grib.decode import (
        build_icon_cloud_diagnostics,
        decode_icon_eu_cloud_diag_per_point,
    )
    from weatherbrief.fetch.grib.icon_eu_fetch import fetch_icon_eu_single_level

    total_enriched = 0
    for fhour in forecast_hours:
        ck = cache_key(fhour, "ICON_EU_CLOUD_DIAG", bbox)
        grib_bytes = get_cached(run_dir, ck)
        if grib_bytes is None:
            try:
                fetched = fetch_icon_eu_single_level(
                    init_date, init_hour, [fhour], session=session,
                )
                grib_bytes = fetched.get(fhour)
                if grib_bytes:
                    put_cached(run_dir, ck, grib_bytes)
            except Exception:
                logger.warning("Failed to fetch ICON-EU cloud diagnostics f%03d", fhour, exc_info=True)
                continue
        if not grib_bytes:
            continue

        decoded_points = decode_icon_eu_cloud_diag_per_point(
            grib_bytes, point_lats, point_lons,
        )
        del grib_bytes
        if not decoded_points:
            continue

        diagnostics_per_point = [build_icon_cloud_diagnostics(raw) for raw in decoded_points]
        valid_utc = _forecast_hour_to_utc(init_date, init_hour, fhour)

        # Use _apply_cloud_diagnostics_to_sections with GFS-priority guard
        for cs in icon_sections:
            for point_idx, wf in enumerate(cs.point_forecasts):
                if point_idx >= len(diagnostics_per_point):
                    break
                diag = diagnostics_per_point[point_idx]
                if diag is None:
                    continue
                for hourly in wf.hourly:
                    if hourly.time.hour != valid_utc.hour:
                        continue
                    if hourly.nwp_cloud_diagnostics is None:
                        _apply_cloud_diagnostics(hourly, diag)
                        total_enriched += 1

        # Also enrich waypoint-only forecasts
        wp_diag_lookup: dict[str, NWPCloudDiagnostics] = {}
        for rp, diag in zip(route_points, diagnostics_per_point):
            if rp.waypoint_icao and diag is not None:
                wp_diag_lookup[rp.waypoint_icao] = diag

        for wf in all_forecasts:
            if wf.model.value != "icon":
                continue
            diag = wp_diag_lookup.get(wf.waypoint.icao)
            if diag is None:
                continue
            for hourly in wf.hourly:
                if hourly.time.hour != valid_utc.hour:
                    continue
                if hourly.nwp_cloud_diagnostics is None:
                    _apply_cloud_diagnostics(hourly, diag)

    if total_enriched:
        logger.info(
            "ICON-EU enrichment: %d hourly entries enriched with cloud diagnostics",
            total_enriched,
        )
    else:
        logger.debug("No ICON-EU cloud diagnostic GRIB2 data retrieved")


# ---------------------------------------------------------------------------
# Shared merge logic
# ---------------------------------------------------------------------------


def _merge_cloud_water_into_sections(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    decoded_points: list[dict[int, dict[str, float]]],
    model_value: str,
    valid_utc: datetime | None = None,
) -> int:
    """Merge decoded cloud water data into cross-section and waypoint forecasts.

    Shared between GFS and ICON-EU enrichment paths.

    Args:
        valid_utc: If set, only enrich hourly entries whose time matches
            this UTC hour. None enriches all hours (backward-compatible).

    Returns:
        Number of pressure levels enriched.
    """
    enriched_count = 0
    for cs in sections:
        for point_idx, wf in enumerate(cs.point_forecasts):
            if point_idx >= len(decoded_points):
                break
            point_data = decoded_points[point_idx]
            if not point_data:
                continue

            for hourly in wf.hourly:
                if valid_utc is not None and hourly.time.hour != valid_utc.hour:
                    continue
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
        if wf.model.value != model_value:
            continue
        wp_icao = wf.waypoint.icao
        point_data = wp_data_lookup.get(wp_icao)
        if not point_data:
            continue
        for hourly in wf.hourly:
            if valid_utc is not None and hourly.time.hour != valid_utc.hour:
                continue
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

    return enriched_count
