"""Core briefing pipeline — shared by CLI and API.

Orchestrates: fetch → analyze → snapshot → optional outputs (GRAMET, Skew-T, LLM digest).
Returns structured results without printing or exiting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from weatherbrief.fetch.variables import MODEL_ENDPOINTS
from weatherbrief.models import (
    ForecastSnapshot,
    ModelSource,
    RouteConfig,
    WaypointAnalysis,
)
from weatherbrief.storage.snapshots import DEFAULT_DATA_DIR, save_cross_section, save_snapshot
from weatherbrief.tasks.analyze import (  # noqa: F401  — backward compat re-exports
    analyze_all_route_points,
    analyze_waypoint,
    compute_interpolated_time,
    compute_route_tracks,
)
from weatherbrief.tasks.fetch import run_fetch
from weatherbrief.tasks.analyze import run_analysis
from weatherbrief.tasks.advise import run_advisories
from weatherbrief.tasks.outputs import run_gramet, run_skewt, run_llm_digest

logger = logging.getLogger(__name__)

DEFAULT_MODELS = [ModelSource(k) for k, v in MODEL_ENDPOINTS.items() if v.default]


@dataclass
class BriefingOptions:
    """Options controlling what the pipeline produces."""

    models: list[ModelSource] = field(default_factory=lambda: list(DEFAULT_MODELS))
    advisory_models: list[str] | None = None  # subset of models for advisories
    enrich_grib: bool = False  # Enable GFS GRIB2 enrichment (CLWMR/ICMR)
    fetch_gramet: bool = False
    generate_skewt: bool = False
    generate_llm_digest: bool = False
    digest_config_name: str | None = None
    data_dir: Path | None = None
    output_dir: Path | None = None  # if set, write all artifacts here (pack mode)
    autorouter_credentials: tuple[str, str] | None = None  # (username, password)
    user_id: str | None = None  # for per-user token cache isolation
    airports_db_path: str | None = None  # euro_aip database for runway data
    icing_severity_enhance: bool = False  # enable RH/PW icing severity upgrades
    icing_method: str | None = None  # "ogimet_dd", "ogimet_nwp", "sfip_nwp"
    cloud_method: str | None = None  # "dd" or "nwp"
    convective_method: str | None = None  # "thermo" or "nwp"
    flight_rules: str | None = None  # "vfr_only" or "vfr_ifr"
    metar_taf_corridor_nm: float = 30  # corridor width for METAR/TAF search
    # Advisory preferences (from user profile)
    advisory_aggregation: str | None = None  # "worst" or "majority"
    advisory_enabled: dict[str, bool] | None = None  # {advisory_id: enabled}
    advisory_params: dict[str, dict[str, float]] | None = None  # {advisory_id: {param: value}}
    historical_mode: bool = False  # Use archived NWP data for past departure times
    as_of_time: datetime | None = None  # For historical: the date "as of" which to fetch data


@dataclass
class BriefingUsage:
    """Tracks resource usage during a single briefing pipeline run."""

    open_meteo_calls: int = 0
    grib_enrichment: bool = False
    grib_enrichment_failed: bool = False
    gramet_fetched: bool = False
    gramet_failed: bool = False
    llm_digest: bool = False
    llm_model: str | None = None
    llm_input_tokens: int | None = None
    llm_output_tokens: int | None = None
    metar_taf_fetched: bool = False
    metar_taf_airports: int = 0
    elapsed_seconds: float | None = None
    queue_wait_seconds: float | None = None
    triggered_by: str | None = None


@dataclass
class BriefingResult:
    """Structured result from a briefing pipeline run."""

    snapshot: ForecastSnapshot
    snapshot_path: Path
    elevation_profile_path: Path | None = None
    route_advisories_path: Path | None = None
    gramet_path: Path | None = None
    skewt_paths: list[Path] = field(default_factory=list)
    digest_path: Path | None = None
    digest_text: str | None = None
    digest: object | None = None  # WeatherDigest (lazy import avoids hard dep)
    text_digest: str | None = None
    grib_init_times: dict[str, int] = field(default_factory=dict)
    models_skipped_region: list[str] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    usage: BriefingUsage = field(default_factory=BriefingUsage)


def _load_previous_digest(pack_dir: Path | None, days_out: int = -1):
    """Load the most recent prior WeatherDigest from a different briefing cycle.

    Pack layout: ``data/packs/{user}/{flight}/{timestamp}/digest.json``
    Looks at sibling timestamp directories (sorted descending) and returns
    the first valid WeatherDigest whose ``days_out`` differs from the current
    briefing, so we compare across briefing days rather than re-refreshes.
    """
    if pack_dir is None:
        return None
    import json

    flight_dir = pack_dir.parent
    current_ts = pack_dir.name
    try:
        siblings = sorted(
            (d for d in flight_dir.iterdir()
             if d.is_dir() and d.name != current_ts),
            key=lambda d: d.name,
            reverse=True,
        )
    except OSError:
        return None

    from weatherbrief.digest.llm_digest import WeatherDigest

    for sibling in siblings:
        digest_path = sibling / "digest.json"
        if not digest_path.exists():
            continue
        # Check days_out from briefing/snapshot to skip same-day re-refreshes
        briefing_path = sibling / "briefing.json"
        meta_path = briefing_path if briefing_path.exists() else sibling / "snapshot.json"
        if meta_path.exists() and days_out >= 0:
            try:
                snap_data = json.loads(meta_path.read_text())
                if snap_data.get("days_out") == days_out:
                    continue
            except Exception:
                pass  # can't read metadata — still try the digest
        try:
            data = json.loads(digest_path.read_text())
            prev = WeatherDigest.model_validate(data)
            logger.info("Loaded previous digest from %s", digest_path)
            return prev
        except Exception:
            logger.debug("Could not load digest from %s", digest_path, exc_info=True)
            continue
    return None


def execute_briefing(
    route: RouteConfig,
    departure_time: datetime,
    options: BriefingOptions | None = None,
    progress_callback: Callable[[str, str | None], None] | None = None,
) -> BriefingResult:
    """Run the full briefing pipeline.

    This is the single entry point shared by CLI and API.
    Does not print, does not call sys.exit — returns structured results.

    Args:
        departure_time: Aware UTC datetime for the flight departure.

    Raises:
        ValueError: If target_date is in the past.
    """
    options = options or BriefingOptions()
    data_dir = options.data_dir or DEFAULT_DATA_DIR
    pack_dir = options.output_dir  # None means legacy snapshot mode

    def _notify(stage: str, detail: str | None = None) -> None:
        if progress_callback is not None:
            progress_callback(stage, detail)

    # Derive target_date for snapshot metadata and logging
    target_date = departure_time.strftime("%Y-%m-%d")

    today_utc = datetime.now(timezone.utc).date()
    today = today_utc.isoformat()
    target_dt = departure_time
    if options.as_of_time:
        days_out = (date.fromisoformat(target_date) - options.as_of_time.date()).days
    else:
        days_out = (date.fromisoformat(target_date) - today_utc).days

    if days_out < 0 and not options.historical_mode:
        raise ValueError(f"Target date {target_date} is in the past")

    logger.info("Route: %s", route.name)
    logger.info("Target: %s (%d days out)", target_date, days_out)
    logger.info("Models: %s", ", ".join(m.value for m in options.models))

    # === 1. Fetch ===
    as_of_time = options.as_of_time or (departure_time if options.historical_mode else None)
    fetch_result = run_fetch(
        route=route,
        departure_time=departure_time,
        models=options.models,
        enrich_grib=options.enrich_grib,
        data_dir=data_dir,
        pack_dir=pack_dir,
        user_id=options.user_id,
        progress_callback=progress_callback,
        historical_mode=options.historical_mode,
        as_of_time=as_of_time,
    )

    # === 2. Analyze ===
    analysis_result = run_analysis(
        route=route,
        departure_time=departure_time,
        all_forecasts=fetch_result.all_forecasts,
        cross_sections=fetch_result.cross_sections,
        route_points=fetch_result.route_points,
        icing_severity_enhance=options.icing_severity_enhance,
        pack_dir=pack_dir,
        progress_callback=progress_callback,
    )

    # === 3. Advisories ===
    route_advisories_manifest = None
    if analysis_result.route_analyses_manifest and analysis_result.route_analyses:
        total_distance = fetch_result.route_points[-1].distance_from_origin_nm
        # Build advisory preference args from options
        adv_aggregation = None
        if options.advisory_aggregation:
            from weatherbrief.models import AdvisoryAggregation
            adv_aggregation = AdvisoryAggregation(options.advisory_aggregation)

        adv_enabled_ids = None
        if options.advisory_enabled is not None:
            adv_enabled_ids = {k for k, v in options.advisory_enabled.items() if v}

        advisory_result = run_advisories(
            rp_analyses=analysis_result.route_analyses,
            cross_sections=fetch_result.cross_sections,
            elevation_profile=fetch_result.elevation_profile,
            model_names=analysis_result.model_names,
            route=route,
            total_distance_nm=total_distance,
            advisory_models=options.advisory_models,
            airports_db_path=options.airports_db_path,
            enabled_ids=adv_enabled_ids,
            user_params=options.advisory_params,
            aggregation=adv_aggregation,
            pack_dir=pack_dir,
            progress_callback=progress_callback,
            icing_method=options.icing_method,
            cloud_method=options.cloud_method,
            convective_method=options.convective_method,
        )
        route_advisories_manifest = advisory_result.manifest

    # === 3.5 Route weather observations (D-0 only) ===
    route_observations = None
    if days_out == 0 and options.airports_db_path and not options.historical_mode:
        _notify("route_weather")
        try:
            from weatherbrief.tasks.route_weather import (
                run_observation_comparison,
                run_route_weather,
            )

            route_observations = run_route_weather(
                route=route,
                target_time=target_dt,
                corridor_nm=options.metar_taf_corridor_nm,
                airports_db_path=options.airports_db_path,
            )
            # Collect runway data for wind advisory comparison
            try:
                from weatherbrief.airports import get_runway_ends

                obs_icaos = [a.icao for a in route_observations.airports]
                obs_runway_data = get_runway_ends(obs_icaos, options.airports_db_path)
            except Exception:
                obs_runway_data = None

            route_observations = run_observation_comparison(
                observations=route_observations,
                snapshot_forecasts=fetch_result.all_forecasts,
                target_time=target_dt,
                route=route,
                runway_data=obs_runway_data,
                route_analyses=analysis_result.route_analyses,
            )
            result_usage_metar = True
            result_usage_metar_airports = route_observations.airports_with_metar
        except Exception:
            logger.warning("Route weather fetch failed", exc_info=True)
            result_usage_metar = False
            result_usage_metar_airports = 0
    else:
        result_usage_metar = False
        result_usage_metar_airports = 0

    # === 4. Build & save snapshot ===
    snapshot = ForecastSnapshot(
        route=route,
        target_date=target_date,
        fetch_date=today,
        days_out=days_out,
        departure_time=departure_time,
        forecasts=fetch_result.all_forecasts,
        analyses=analysis_result.waypoint_analyses,
        cross_sections=fetch_result.cross_sections,
        route_observations=route_observations,
    )

    if pack_dir:
        from weatherbrief.tasks.artifacts import save_analysis_artifacts

        save_analysis_artifacts(pack_dir, snapshot, analysis_result.route_analyses_manifest)
        snapshot_path = pack_dir / "briefing.json"
    else:
        snapshot_path = save_snapshot(snapshot, data_dir)
        if fetch_result.cross_sections:
            save_cross_section(snapshot, data_dir)
    _notify("save_snapshot")
    logger.info("Snapshot saved: %s", snapshot_path)

    result = BriefingResult(snapshot=snapshot, snapshot_path=snapshot_path)
    result.grib_init_times = fetch_result.grib_init_times
    result.models_skipped_region = fetch_result.models_skipped_region
    result.diagnostics = fetch_result.diagnostics
    result.usage.open_meteo_calls = len(fetch_result.cross_sections)
    result.usage.grib_enrichment = fetch_result.grib_enriched
    result.usage.grib_enrichment_failed = fetch_result.grib_enrichment_failed
    result.usage.metar_taf_fetched = result_usage_metar
    result.usage.metar_taf_airports = result_usage_metar_airports
    if fetch_result.elevation_profile and pack_dir:
        result.elevation_profile_path = pack_dir / "elevation_profile.json"
    if route_advisories_manifest and pack_dir:
        result.route_advisories_path = pack_dir / "route_advisories.json"

    # === 5. Optional: GRAMET ===
    if options.fetch_gramet:
        _notify("fetch_gramet")
        gramet_result = run_gramet(
            route=route,
            departure_time=departure_time,
            pack_dir=pack_dir,
            data_dir=data_dir,
            days_out=days_out,
            fetch_date=today,
            autorouter_credentials=options.autorouter_credentials,
            user_id=options.user_id,
        )
        if gramet_result.path:
            result.gramet_path = gramet_result.path
            result.usage.gramet_fetched = gramet_result.fetched
        if gramet_result.failed:
            result.usage.gramet_failed = True
        if gramet_result.error:
            result.errors.append(gramet_result.error)

    # === 6. Optional: Skew-T ===
    if options.generate_skewt:
        _notify("generate_skewt")
        skewt_result = run_skewt(
            snapshot=snapshot,
            target_time=target_dt,
            pack_dir=pack_dir,
            data_dir=data_dir,
        )
        if skewt_result.paths:
            result.skewt_paths = skewt_result.paths
        if skewt_result.error:
            result.errors.append(skewt_result.error)

    # === 7. Optional: LLM digest ===
    if options.generate_llm_digest:
        _notify("llm_digest")
        previous_digest = _load_previous_digest(pack_dir, days_out)
        digest_result = run_llm_digest(
            snapshot=snapshot,
            target_time=target_dt,
            digest_config_name=options.digest_config_name,
            pack_dir=pack_dir,
            data_dir=data_dir,
            route_advisories=route_advisories_manifest,
            flight_rules=options.flight_rules,
            previous_digest=previous_digest,
        )
        if digest_result.digest is not None:
            result.digest = digest_result.digest
        if digest_result.path:
            result.digest_path = digest_result.path
        if digest_result.text:
            result.digest_text = digest_result.text
        if digest_result.llm_model:
            result.usage.llm_digest = True
            result.usage.llm_model = digest_result.llm_model
            result.usage.llm_input_tokens = digest_result.llm_input_tokens
            result.usage.llm_output_tokens = digest_result.llm_output_tokens
        if digest_result.error:
            result.errors.append(digest_result.error)

    # === 8. Always: text digest ===
    from weatherbrief.digest.text import format_digest

    output_paths = [str(result.snapshot_path)]
    if result.gramet_path:
        output_paths.append(str(result.gramet_path))
    output_paths.extend(str(p) for p in result.skewt_paths)
    if result.digest_path:
        output_paths.append(str(result.digest_path))

    result.text_digest = format_digest(snapshot, target_dt, output_paths=output_paths)

    return result
