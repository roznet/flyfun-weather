"""Fetch task — weather data retrieval for all route points.

Extracted from ``pipeline.py`` lines 138-213.  Can run independently
and persist its artifacts to a pack directory.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from weatherbrief.fetch.open_meteo import EmptyForecastError, OpenMeteoClient
from weatherbrief.fetch.route_points import interpolate_route
from weatherbrief.fetch.variables import (
    MODEL_ENDPOINTS,
    ModelRegion,
    detect_model_region,
    route_covers_prefixes,
)
from weatherbrief.models import (
    Diagnostic,
    ElevationProfile,
    FetchCode,
    ModelSource,
    RouteConfig,
    RouteCrossSection,
    RoutePoint,
    WaypointForecast,
)

# Free-tier rate limit (600/min) means we can't blast all models at once
# without occasional 429s; the paid tier has generous limits and parallelises
# safely. Free tier therefore keeps the sequential path with a small delay
# between models; paid tier dispatches to a thread pool.
_INTER_MODEL_DELAY_FREE = 1.0  # seconds between models on free tier
_BRIEFING_FETCH_CONCURRENCY = int(os.environ.get("BRIEFING_FETCH_CONCURRENCY", "4"))

logger = logging.getLogger(__name__)


# Open-Meteo registry keys per model — we only consult Open-Meteo-backed
# markers; direct-GRIB models (ECMWF direct, GFS NOAA, ICON-EU DWD) have
# their own horizon handling in the GRIB fetch path.
_OM_REGISTRY_KEYS: dict[str, str] = {
    "ecmwf": "ecmwf:openmeteo",
    "gfs": "gfs:openmeteo",
    "icon": "icon:openmeteo",
    "ukmo": "ukmo:openmeteo",
    "meteofrance": "meteofrance:openmeteo",
    "gem": "gem:openmeteo",
}


def _open_meteo_live_horizons() -> dict[str, datetime]:
    """Snapshot the marker store's ``data_end`` per Open-Meteo model.

    Returns ``{model_value: data_end_dt}`` for every known model with a
    populated ``data_end`` on its marker. Models without a marker (store
    not yet bootstrapped, or direct-GRIB key only) are omitted, in which
    case the caller falls through to the static config horizon.
    """
    try:
        from weatherbrief.fetch.freshness.markers import get_store
    except Exception:
        return {}
    store = get_store()
    out: dict[str, datetime] = {}
    for model_value, source_key in _OM_REGISTRY_KEYS.items():
        marker = store.get_sync(source_key, model_value)
        if marker is not None and marker.data_end is not None:
            out[model_value] = marker.data_end
    return out


def _should_skip_for_region(endpoint, route_region: ModelRegion, route=None) -> bool:
    """Return True if a model's coverage region doesn't match the route.

    Two-level check:
    1. Broad region (EUROPE / NORTH_AMERICA / GLOBAL)
    2. Country-level ICAO prefix (e.g. MeteoFrance requires an LF airport)
    """
    if endpoint.region != ModelRegion.GLOBAL and route_region != ModelRegion.GLOBAL:
        if endpoint.region != route_region:
            return True
    if endpoint.required_icao_prefixes and route is not None:
        if not route_covers_prefixes(route, endpoint.required_icao_prefixes):
            return True
    return False


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
    diagnostics: list[Diagnostic] = field(default_factory=list)
    open_meteo_api_calls: int = 0


def _build_fetch_diagnostics(
    *,
    requested_models: list[str],
    models_fetched: list[str],
    models_skipped_region: list[str],
    models_skipped_range: list[tuple[str, int, int]] | None = None,
    models_skipped_horizon: list[tuple[str, datetime]] | None = None,
    models_returned_empty: list[str] | None = None,
    enrich_grib: bool,
    grib_enriched: bool,
    grib_enrichment_failed: bool,
    grib_init_times: dict[str, int],
    grib_skip_reasons: dict[str, str] | None = None,
) -> list[Diagnostic]:
    """Build user-facing diagnostic messages from the completed fetch state."""
    diags: list[Diagnostic] = []

    range_skipped_names = {m for m, _, _ in (models_skipped_range or [])}
    horizon_skipped_names = {m for m, _ in (models_skipped_horizon or [])}
    empty_names = set(models_returned_empty or [])

    # Models that failed to fetch (requested but neither fetched nor skipped)
    fetched_or_skipped = (
        set(models_fetched)
        | set(models_skipped_region)
        | range_skipped_names
        | horizon_skipped_names
        | empty_names
    )
    for m in requested_models:
        if m not in fetched_or_skipped:
            diags.append(Diagnostic.create(
                level="warn", stage="fetch",
                code=FetchCode.MODEL_FETCH_FAILED,
                message=f"{m.upper()} forecast fetch failed",
            ))

    # Models skipped for range (too many days out)
    for m, days_out, max_days in (models_skipped_range or []):
        diags.append(Diagnostic.create(
            level="info", stage="fetch",
            code=FetchCode.MODEL_SKIPPED_RANGE,
            message=f"{m.upper()} skipped ({days_out} days out exceeds {max_days}-day range)",
        ))

    # Models skipped because flight window is past the run's actual horizon
    for m, data_end in (models_skipped_horizon or []):
        diags.append(Diagnostic.create(
            level="info", stage="fetch",
            code=FetchCode.MODEL_SKIPPED_HORIZON,
            message=(
                f"{m.upper()} skipped — current run only serves data through "
                f"{data_end.strftime('%Y-%m-%d %H:%MZ')}"
            ),
        ))

    # Models that fetched successfully but came back with no usable values
    for m in models_returned_empty or []:
        diags.append(Diagnostic.create(
            level="warn", stage="fetch",
            code=FetchCode.MODEL_RETURNED_EMPTY,
            message=f"{m.upper()} returned no values (likely past provider horizon)",
        ))

    # Models skipped for region
    for m in models_skipped_region:
        diags.append(Diagnostic.create(
            level="info", stage="fetch",
            code=FetchCode.MODEL_SKIPPED_REGION,
            message=f"{m.upper()} skipped (not available for this route region)",
        ))

    # GRIB enrichment diagnostics
    if enrich_grib:
        if grib_enrichment_failed:
            diags.append(Diagnostic.create(
                level="warn", stage="fetch",
                code=FetchCode.GRIB_ENRICHMENT_FAILED,
                message="GRIB enrichment failed — cloud microphysics (CLW/ICMR) and cloud diagnostics not available",
            ))
        elif grib_enriched:
            # Per-model GRIB status
            enriched_models = set(grib_init_times.keys())
            skip_reasons = grib_skip_reasons or {}
            grib_capable = {"gfs", "icon"}
            for m in models_fetched:
                if m in grib_capable and m not in enriched_models:
                    skip_reason = skip_reasons.get(m)
                    if skip_reason == "out_of_range":
                        diags.append(Diagnostic.create(
                            level="info", stage="fetch",
                            code=FetchCode.GRIB_SKIPPED_OUT_OF_RANGE,
                            message=f"{m.upper()} GRIB skipped — flight exceeds forecast range",
                        ))
                    else:
                        diags.append(Diagnostic.create(
                            level="warn", stage="fetch",
                            code=FetchCode.GRIB_UNAVAILABLE_FOR_MODEL,
                            message=f"{m.upper()} GRIB enrichment unavailable — cloud microphysics and diagnostics not available for this model",
                        ))
            for m in sorted(enriched_models):
                diags.append(Diagnostic.create(
                    level="info", stage="fetch",
                    code=FetchCode.GRIB_ENRICHMENT_APPLIED,
                    message=f"{m.upper()} GRIB enrichment applied (cloud water + cloud diagnostics)",
                ))

    return diags


def run_fetch(
    route: RouteConfig,
    departure_time: "datetime",
    models: list[ModelSource],
    enrich_grib: bool = False,
    data_dir: Path | None = None,
    pack_dir: Path | None = None,
    user_id: str | None = None,
    progress_callback: Callable[[str, str | None], None] | None = None,
    historical_mode: bool = False,
    as_of_time: "datetime | None" = None,
) -> FetchResult:
    """Execute the fetch stage of the briefing pipeline.

    Interpolates route → fetches elevation → fetches forecasts per model
    → optional GRIB enrichment.  If *pack_dir* is set, persists artifacts.
    """
    import math
    from datetime import date, datetime, timedelta, timezone

    def _notify(stage: str, detail: str | None = None) -> None:
        if progress_callback is not None:
            progress_callback(stage, detail)

    target_date = departure_time.strftime("%Y-%m-%d")
    today_utc = datetime.now(timezone.utc).date()
    days_out = (date.fromisoformat(target_date) - today_utc).days
    # Historical mode: archived API serves same-day forecasts, don't skip models
    days_out_for_range = 0 if historical_mode else days_out

    # --- Route interpolation ---
    _notify("route_interpolation")
    client = OpenMeteoClient(historical=historical_mode)
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
    models_skipped_range: list[tuple[str, int, int]] = []
    models_skipped_horizon: list[tuple[str, datetime]] = []
    models_returned_empty: list[str] = []

    route_region = detect_model_region(route)
    end_date = (
        departure_time
        + timedelta(hours=math.ceil(route.flight_duration_hours or 0))
    ).strftime("%Y-%m-%d")

    # Filter models up-front so we don't waste worker threads on skipped models.
    end_date_dt = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
    live_horizons = _open_meteo_live_horizons() if not historical_mode else {}
    models_to_fetch: list[ModelSource] = []
    for model in models:
        endpoint = MODEL_ENDPOINTS[model.value]
        if days_out_for_range is not None and days_out_for_range >= endpoint.max_days:
            logger.info(
                "Skipping %s: %d days out exceeds %d-day range",
                model.value, days_out_for_range, endpoint.max_days,
            )
            models_skipped_range.append(
                (model.value, days_out_for_range, endpoint.max_days),
            )
            continue
        if _should_skip_for_region(endpoint, route_region, route):
            logger.info(
                "Skipping %s: %s model not relevant for %s route",
                model.value, endpoint.region.value, route_region.value,
            )
            models_skipped_region.append(model.value)
            continue
        # Live horizon check: Open-Meteo's data_end_time can fall short of
        # the static config horizon for a given run. Skip rather than fetch
        # a structurally-valid response of all-null values.
        live_end = live_horizons.get(model.value)
        if live_end is not None and end_date_dt > live_end:
            logger.info(
                "Skipping %s: flight window ends %s, but current run only "
                "serves data through %s",
                model.value, end_date_dt.isoformat(), live_end.isoformat(),
            )
            models_skipped_horizon.append((model.value, live_end))
            continue
        models_to_fetch.append(model)

    def _record_success(model: ModelSource, point_forecasts: list[WaypointForecast]) -> None:
        for rp, fc in zip(route_points, point_forecasts):
            if rp.waypoint_icao:
                all_forecasts.append(fc)
        cross_sections.append(RouteCrossSection(
            model=model,
            route_points=route_points,
            fetched_at=point_forecasts[0].fetched_at,
            point_forecasts=point_forecasts,
        ))
        logger.info("Fetched %s: %d points", model.value, len(point_forecasts))
        models_fetched_names.append(model.value)

    if models_to_fetch:
        # Single notify before dispatch — per-model detail would flicker
        # between concurrent workers and isn't useful to the user.
        _notify("fetch_forecasts")

        if client.has_api_key:
            # Paid tier: parallel dispatch. Workers share one OpenMeteoClient
            # (and one requests.Session). This is safe under the current
            # invariants — unauthenticated GETs only, no cookies persisted
            # on the session (Open-Meteo doesn't Set-Cookie), no session-
            # level state mutated post-init — because urllib3's PoolManager
            # (the actual transport layer) is itself thread-safe. Redirect
            # following is on by default but Open-Meteo doesn't issue
            # redirects, so no per-redirect session state is touched. If
            # any of those invariants change (cookies, auth headers, an
            # endpoint that 30x's), revisit.
            #
            # Each worker resets its thread-local call counter, runs its
            # fetch, captures the count, and returns it — even on failure —
            # so retries that 429'd before raising still get counted.
            def _fetch_one_model(model: ModelSource) -> tuple[ModelSource, list[WaypointForecast] | None, int, Exception | None]:
                client._reset_thread_call_count()
                try:
                    point_forecasts = client.fetch_multi_point(
                        route_points, model,
                        start_date=target_date, end_date=end_date,
                    )
                    return model, point_forecasts, client._thread_call_count(), None
                except Exception as exc:
                    return model, None, client._thread_call_count(), exc

            max_workers = max(1, min(_BRIEFING_FETCH_CONCURRENCY, len(models_to_fetch)))
            total_calls = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_model = {
                    executor.submit(_fetch_one_model, model): model
                    for model in models_to_fetch
                }
                for future in concurrent.futures.as_completed(future_to_model):
                    model = future_to_model[future]
                    _, point_forecasts, calls, exc = future.result()
                    total_calls += calls
                    if isinstance(exc, EmptyForecastError):
                        logger.warning("%s returned no values: %s", model.value, exc)
                        models_returned_empty.append(model.value)
                        continue
                    if exc is not None:
                        logger.warning(
                            "Failed to fetch %s", model.value,
                            exc_info=(type(exc), exc, exc.__traceback__),
                        )
                        continue
                    _record_success(model, point_forecasts)
            # Replace the racy aggregate with the summed per-thread totals.
            client.call_count = total_calls
        else:
            # Free tier: sequential with a small inter-model delay to stay
            # under the 600/min rate limit.
            for i, model in enumerate(models_to_fetch):
                if i > 0 and _INTER_MODEL_DELAY_FREE > 0:
                    time.sleep(_INTER_MODEL_DELAY_FREE)
                try:
                    point_forecasts = client.fetch_multi_point(
                        route_points, model,
                        start_date=target_date, end_date=end_date,
                    )
                except EmptyForecastError as exc:
                    logger.warning("%s returned no values: %s", model.value, exc)
                    models_returned_empty.append(model.value)
                    continue
                except Exception:
                    logger.warning("Failed to fetch %s", model.value, exc_info=True)
                    continue
                _record_success(model, point_forecasts)

    # --- GRIB2 enrichment (optional) ---
    grib_enriched = False
    grib_enrichment_failed = False
    grib_init_times: dict[str, int] = {}
    grib_skip_reasons: dict[str, str] = {}
    if enrich_grib and cross_sections:
        _notify("grib_enrichment")
        try:
            from weatherbrief.fetch.grib import enrich_forecasts

            grib_init_times, grib_skip_reasons = enrich_forecasts(
                cross_sections, all_forecasts, route_points,
                departure_time, data_dir=data_dir,
                flight_duration_hours=route.flight_duration_hours,
                progress_callback=progress_callback,
                as_of_time=as_of_time if historical_mode else None,
            )
            grib_enriched = True
            logger.info("GRIB2 enrichment applied")
        except Exception:
            grib_enrichment_failed = True
            logger.warning(
                "GRIB2 enrichment failed, using Open-Meteo data only",
                exc_info=True,
            )

    # --- Build diagnostics ---
    diagnostics = _build_fetch_diagnostics(
        requested_models=[m.value for m in models],
        models_fetched=models_fetched_names,
        models_skipped_region=models_skipped_region,
        models_skipped_range=models_skipped_range,
        models_skipped_horizon=models_skipped_horizon,
        models_returned_empty=models_returned_empty,
        enrich_grib=enrich_grib,
        grib_enriched=grib_enriched,
        grib_enrichment_failed=grib_enrichment_failed,
        grib_init_times=grib_init_times,
        grib_skip_reasons=grib_skip_reasons,
    )

    # --- Persist artifacts ---
    if pack_dir:
        from weatherbrief.tasks.artifacts import save_fetch_artifacts

        save_fetch_artifacts(
            pack_dir, cross_sections, elevation_profile, route_points,
            models_fetched=models_fetched_names,
            diagnostics=diagnostics,
        )

    # Combine all skip/empty buckets into one list for the UI badge
    all_skipped = (
        models_skipped_region
        + [m for m, _, _ in models_skipped_range]
        + [m for m, _ in models_skipped_horizon]
        + models_returned_empty
    )

    return FetchResult(
        route_points=route_points,
        all_forecasts=all_forecasts,
        cross_sections=cross_sections,
        elevation_profile=elevation_profile,
        models_fetched=models_fetched_names,
        models_skipped_region=all_skipped,
        grib_enriched=grib_enriched,
        grib_enrichment_failed=grib_enrichment_failed,
        grib_init_times=grib_init_times,
        diagnostics=diagnostics,
        open_meteo_api_calls=client.call_count,
    )
