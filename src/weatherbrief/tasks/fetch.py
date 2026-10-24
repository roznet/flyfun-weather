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

import requests

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


def model_failure_reason(exc: BaseException) -> str:
    """Short, stable reason for a failed model fetch (#835).

    One of ``timeout``, ``connection``, ``http_<code>`` or ``error``. Read by
    the fetch diagnostic, the digest context and the model-agreement summary,
    so keep the vocabulary small.
    """
    # Timeout first: ConnectTimeout is both a Timeout and a ConnectionError.
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.ConnectionError):
        return "connection"
    if isinstance(exc, requests.HTTPError):
        code = getattr(exc.response, "status_code", None)
        return f"http_{code}" if code is not None else "error"
    return "error"


def _should_skip_for_region(endpoint, route_region: ModelRegion, route=None) -> bool:
    """Return True if a model's coverage doesn't match the route.

    Two-level check:
    1. Broad region (EUROPE / NORTH_AMERICA / GLOBAL), from origin/destination.
    2. Country presence (e.g. Météo-France requires the route to touch France),
       detected from route geometry — catches overflight, not just landings.
    """
    if endpoint.region != ModelRegion.GLOBAL and route_region != ModelRegion.GLOBAL:
        if endpoint.region != route_region:
            return True
    if endpoint.required_country and route is not None:
        if not _route_in_required_country(endpoint, route):
            return True
    return False


def _route_in_required_country(endpoint, route) -> bool:
    """Whether ``route`` passes through ``endpoint.required_country``.

    Uses coordinate-based detection (timezone polygons over the route's
    great-circle samples). If that can't run (e.g. timezone data unavailable),
    falls back to the airport ICAO-prefix heuristic; if even that is absent we
    keep the model rather than wrongly dropping it.
    """
    try:
        from weatherbrief.airports import route_countries

        return endpoint.required_country in route_countries(route)
    except (ImportError, OSError, RuntimeError):
        # Degraded environment only: timezone library missing (ImportError) or
        # its data unavailable (OSError/RuntimeError). Any other exception is a
        # code defect and must propagate rather than silently fall back.
        logger.warning(
            "Country detection failed for %s; falling back to ICAO prefixes",
            endpoint.name,
            exc_info=True,
        )
        if endpoint.required_icao_prefixes:
            return route_covers_prefixes(route, endpoint.required_icao_prefixes)
        return True


@dataclass
class FetchResult:
    """Output of the fetch stage."""

    route_points: list[RoutePoint]
    all_forecasts: list[WaypointForecast]
    cross_sections: list[RouteCrossSection]
    elevation_profile: ElevationProfile | None
    models_fetched: list[str]
    models_skipped_region: list[str] = field(default_factory=list)
    # model → short reason (see `model_failure_reason`) for every requested
    # model whose fetch raised after retries (#835). Skips are not failures.
    models_failed: dict[str, str] = field(default_factory=dict)
    grib_enriched: bool = False
    grib_enrichment_failed: bool = False
    grib_init_times: dict[str, int] = field(default_factory=dict)
    # model → freshness source key actually used for the direct-GRIB run.
    # Distinguishes the icon slot's variant (icon_eu:dwd vs icon_d2:dwd, #456).
    grib_sources: dict[str, str] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    open_meteo_api_calls: int = 0
    open_meteo_weighted_calls: float = 0.0
    # Models built from GRIB without an up-front Open-Meteo fetch (#912),
    # with the number of points that needed the Open-Meteo fallback.
    grib_primary: dict[str, int] = field(default_factory=dict)


def _build_fetch_diagnostics(
    *,
    requested_models: list[str],
    models_fetched: list[str],
    models_skipped_region: list[str],
    models_skipped_range: list[tuple[str, int, int]] | None = None,
    models_skipped_horizon: list[tuple[str, datetime]] | None = None,
    models_returned_empty: list[str] | None = None,
    models_failed: dict[str, str] | None = None,
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

    # Models that failed to fetch, with the reason recorded where the
    # exception was caught. The subtraction below stays as a fallback for
    # any requested model that is neither fetched, skipped nor recorded.
    failed = models_failed or {}
    for m in requested_models:
        if m in failed:
            diags.append(Diagnostic.create(
                level="warn", stage="fetch",
                code=FetchCode.MODEL_FETCH_FAILED,
                message=f"{m.upper()} forecast fetch failed ({failed[m]})",
            ))
    fetched_or_skipped = (
        set(models_fetched)
        | set(models_skipped_region)
        | range_skipped_names
        | horizon_skipped_names
        | empty_names
        | set(failed)
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
            # meteofrance gains AROME condensate (#529) only where the
            # deployment has it switched on; elsewhere its missing GRIB is
            # the expected state, not a warning.
            from weatherbrief.fetch.grib.arome_fetch import arome_enabled
            if arome_enabled():
                grib_capable.add("meteofrance")
            for m in models_fetched:
                if m in grib_capable and m not in enriched_models:
                    skip_reason = skip_reasons.get(m)
                    if skip_reason == "out_of_range":
                        diags.append(Diagnostic.create(
                            level="info", stage="fetch",
                            code=FetchCode.GRIB_SKIPPED_OUT_OF_RANGE,
                            message=f"{m.upper()} GRIB skipped — flight exceeds forecast range",
                        ))
                    elif skip_reason == "out_of_domain":
                        diags.append(Diagnostic.create(
                            level="info", stage="fetch",
                            code=FetchCode.GRIB_SKIPPED_OUT_OF_DOMAIN,
                            message=f"{m.upper()} GRIB skipped — route outside model coverage area",
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
        logger.info("Elevation profile: %d points, max %s ft",
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
    models_failed: dict[str, str] = {}

    route_region = detect_model_region(route)
    window_end_dt = departure_time + timedelta(
        hours=math.ceil(route.flight_duration_hours or 0)
    )
    if window_end_dt.tzinfo is None:
        window_end_dt = window_end_dt.replace(tzinfo=timezone.utc)
    end_date = window_end_dt.strftime("%Y-%m-%d")

    # Filter models up-front so we don't waste worker threads on skipped models.
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
        # a structurally-valid response of all-null values. Compared against
        # the actual window end (departure + ceil(duration)), NOT midnight of
        # the arrival day: that was up to ~24 h early, so a model ending
        # mid-window shipped null tails with no MODEL_SKIPPED_HORIZON (#807).
        live_end = live_horizons.get(model.value)
        if live_end is not None and window_end_dt > live_end:
            logger.info(
                "Skipping %s: flight window ends %s, but current run only "
                "serves data through %s",
                model.value, window_end_dt.isoformat(), live_end.isoformat(),
            )
            models_skipped_horizon.append((model.value, live_end))
            continue
        models_to_fetch.append(model)

    # --- GRIB-primary gate (#912) ---
    # ECMWF and ICON skip the up-front Open-Meteo fetch when GRIB should cover
    # them: they start as empty skeletons that enrichment fills, and
    # fill_grib_primary_gaps fetches Open-Meteo afterwards only for the points
    # GRIB left short. Needs enrichment on; everything else is unchanged.
    grib_primary_models: list[ModelSource] = []
    if enrich_grib:
        from weatherbrief.fetch.grib.primary import (
            GRIB_PRIMARY_MODELS,
            grib_primary_skip_reason,
        )

        for model in list(models_to_fetch):
            if model not in GRIB_PRIMARY_MODELS:
                continue
            reason = grib_primary_skip_reason(
                model, route_points, departure_time,
                route.flight_duration_hours or 0.0,
                as_of_time=as_of_time, historical_mode=historical_mode,
            )
            if reason is not None:
                logger.info(
                    "GRIB-primary %s: not eligible (%s), Open-Meteo first as before",
                    model.value, reason,
                )
                continue
            models_to_fetch.remove(model)
            grib_primary_models.append(model)

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
                        models_failed[model.value] = model_failure_reason(exc)
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
                except Exception as exc:
                    logger.warning("Failed to fetch %s", model.value, exc_info=True)
                    models_failed[model.value] = model_failure_reason(exc)
                    continue
                _record_success(model, point_forecasts)

    if grib_primary_models:
        from weatherbrief.fetch.grib.primary import build_skeleton

        for model in grib_primary_models:
            logger.info("GRIB-primary %s: built from GRIB, no up-front Open-Meteo fetch", model.value)
            _record_success(model, build_skeleton(
                route_points, model, start_date=target_date, end_date=end_date,
            ))

    # --- GRIB2 enrichment (optional) ---
    grib_enriched = False
    grib_enrichment_failed = False
    grib_init_times: dict[str, int] = {}
    grib_skip_reasons: dict[str, str] = {}
    grib_sources: dict[str, str] = {}
    if enrich_grib and cross_sections:
        _notify("grib_enrichment")
        try:
            from weatherbrief.fetch.grib import enrich_forecasts

            grib_init_times, grib_skip_reasons, grib_sources = enrich_forecasts(
                cross_sections, all_forecasts, route_points,
                departure_time, data_dir=data_dir,
                flight_duration_hours=route.flight_duration_hours,
                progress_callback=progress_callback,
                # `as_of_time` pins GRIB run-selection to runs initialized before
                # it. Set for historical flights (departure) and for in-progress
                # flights (also departure, so the run covers the window); None for
                # normal future flights, which then use the freshest run.
                as_of_time=as_of_time,
            )
            grib_enriched = True
            logger.info("GRIB2 enrichment applied")
        except Exception:
            grib_enrichment_failed = True
            logger.warning(
                "GRIB2 enrichment failed, using Open-Meteo data only",
                exc_info=True,
            )

    # --- GRIB-primary Open-Meteo fallback (#912) ---
    grib_primary: dict[str, int] = {}
    if grib_primary_models:
        from weatherbrief.fetch.grib.primary import fill_grib_primary_gaps

        reasons = []
        if grib_enrichment_failed:
            reasons.append("GRIB enrichment raised")
        for m in grib_primary_models:
            if m.value in grib_skip_reasons:
                reasons.append(f"{m.value} GRIB skipped: {grib_skip_reasons[m.value]}")
        outcomes = fill_grib_primary_gaps(
            cross_sections, route_points, grib_primary_models,
            departure_time, route.flight_duration_hours or 0.0,
            client=client, start_date=target_date, end_date=end_date,
            reason="; ".join(reasons),
        )
        for outcome in outcomes:
            if not outcome.failed:
                grib_primary[outcome.model] = outcome.points_fallback
                continue
            # GRIB left points short and the Open-Meteo fallback for them
            # failed: drop the slot, exactly as a failed (or empty) Open-Meteo
            # fetch leaves it today.
            logger.warning("GRIB-primary %s: slot dropped", outcome.model)
            dropped = [cs for cs in cross_sections if cs.model.value == outcome.model]
            dropped_ids = {id(wf) for cs in dropped for wf in cs.point_forecasts}
            cross_sections[:] = [cs for cs in cross_sections if cs.model.value != outcome.model]
            all_forecasts[:] = [wf for wf in all_forecasts if id(wf) not in dropped_ids]
            models_fetched_names[:] = [m for m in models_fetched_names if m != outcome.model]
            if outcome.empty:
                models_returned_empty.append(outcome.model)
            else:
                models_failed[outcome.model] = model_failure_reason(outcome.error)
            grib_init_times.pop(outcome.model, None)
            grib_sources.pop(outcome.model, None)

    # --- Build diagnostics ---
    diagnostics = _build_fetch_diagnostics(
        requested_models=[m.value for m in models],
        models_fetched=models_fetched_names,
        models_skipped_region=models_skipped_region,
        models_skipped_range=models_skipped_range,
        models_skipped_horizon=models_skipped_horizon,
        models_returned_empty=models_returned_empty,
        models_failed=models_failed,
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
            models_failed=models_failed,
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
        models_failed=models_failed,
        grib_enriched=grib_enriched,
        grib_enrichment_failed=grib_enrichment_failed,
        grib_init_times=grib_init_times,
        grib_sources=grib_sources,
        diagnostics=diagnostics,
        open_meteo_api_calls=client.call_count,
        open_meteo_weighted_calls=client.call_weight,
        grib_primary=grib_primary,
    )
