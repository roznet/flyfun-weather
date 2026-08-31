"""Core briefing pipeline — shared by CLI and API.

Orchestrates: fetch → analyze → snapshot → optional outputs (GRAMET, Skew-T, LLM digest).
Returns structured results without printing or exiting.
"""

from __future__ import annotations

import gc
import logging
import sys
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Callable

from weatherbrief.fetch.variables import MODEL_ENDPOINTS
from weatherbrief.models import (
    AdvisoryCode,
    Diagnostic,
    ForecastSnapshot,
    ModelSource,
    RouteAdvisoriesManifest,
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
from weatherbrief.tasks.artifacts import load_fetch_meta, write_pack_meta
from weatherbrief.tasks.fetch import run_fetch
from weatherbrief.tasks.analyze import run_analysis
from weatherbrief.tasks.advise import AdvisoryResult, advisories_ungradeable, run_advisories
from weatherbrief.tasks.dwd_charts import run_dwd_charts
from weatherbrief.tasks.meteofrance_charts import run_meteofrance_charts
from weatherbrief.tasks.metoffice_charts import run_metoffice_charts
from weatherbrief.tasks.outputs import run_gramet, run_skewt, run_llm_digest

logger = logging.getLogger(__name__)


from weatherbrief.process_memory_sampler import MemorySampler
from weatherbrief.process_rss import current_rss_mb as _current_rss_mb
from weatherbrief.process_rss import log_memory as _log_memory

#: Sampling interval for the per-refresh memory window. The standalone cycle
#: samples every 5 s, but a briefing's spikes are decode-shaped — seconds, not
#: minutes — so 5 s steps over them. Two small /proc + /sys reads every 2 s is
#: nothing against a refresh measured in minutes.
_MEMORY_SAMPLE_SECONDS = 2.0


# `_peak_rss_mb` (ru_maxrss) lived here to feed the Memory curve's
# `peak=… (+… this request)` clause. Both are gone: a process-lifetime
# monotonic mark cannot describe one request, and the sampler now measures the
# window properly. `scenario/measure.py` keeps its own copy for a different
# purpose (a one-shot lifetime figure in a report, where that IS the question).

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
    autorouter_token: str | None = None  # OAuth bearer token for Autorouter API
    user_id: str | None = None  # for per-user token cache isolation
    airports_db_path: str | None = None  # euro_aip database for runway data
    icing_severity_enhance: bool = False  # enable RH/PW icing severity upgrades
    icing_method: str | None = None  # "ogimet_dd", "ogimet_nwp", "sfip_nwp"
    cloud_source: str | None = None  # "dd" or "nwp" (#410 — split from render style)
    convective_method: str | None = None  # "thermo" or "nwp"
    flight_rules: str | None = None  # "vfr_only" or "vfr_ifr"
    metar_taf_corridor_nm: float = 30  # corridor width for METAR/TAF search
    sigmet_corridor_nm: float = 50  # corridor width for route SIGMET intersection
    # Advisory preferences (from user profile)
    advisory_aggregation: str | None = None  # "worst" or "majority"
    advisory_enabled: dict[str, bool] | None = None  # {advisory_id: enabled}
    advisory_params: dict[str, dict[str, float]] | None = None  # {advisory_id: {param: value}}
    cruise_speed_ias_kt: float | None = None  # resolved aircraft/profile cruise IAS for headwind TAS
    auto_front_detection: bool = False  # experimental: write route_fronts.json (#195)
    compute_alternates: bool = True  # default-on: weather-based divert candidates, D-2 inward (#210, graduated)
    historical_mode: bool = False  # Use archived NWP data for past departure times
    as_of_time: datetime | None = None  # For historical: the date "as of" which to fetch data
    alt_departure_time: datetime | None = None  # optional same-day alt departure for lite advisory re-run
    locale: str | None = None  # user locale for LLM digest language (en/fr/de/es)
    units_region: str | None = None  # display-units region for LLM prompt ('europe'/'us')
    profile_id: int | None = None  # flight profile ID for digest tracking
    profile_name: str | None = None  # flight profile name for digest tracking
    guidance_key: str | None = None  # digest guidance preset (conservative/balanced/tolerant)
    # Airports where the user declared an unpublished/self-briefed approach
    # (#510). Read only by ``approach_feasibility`` — never by the alternates
    # or alternate-requirement stages, where a personal declaration would be
    # making a regulatory claim.
    declared_approach_icaos: list[str] | None = None


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
    # Prompt-cache split; both are subsets of llm_input_tokens, not extras.
    llm_cache_read_tokens: int | None = None
    llm_cache_write_tokens: int | None = None
    metar_taf_fetched: bool = False
    metar_taf_airports: int = 0
    sigmet_fetched: bool = False
    sigmet_count: int = 0
    alternates_computed: bool = False
    alternates_count: int = 0
    alternates_candidates: int = 0
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
    # In-memory manifest produced by phase 3 advisories. Allows the
    # briefing_ready callback to derive the provisional assessment without
    # re-reading route_advisories.json from disk (the file was just written).
    route_advisories_manifest: RouteAdvisoriesManifest | None = None
    gramet_path: Path | None = None
    skewt_paths: list[Path] = field(default_factory=list)
    digest_path: Path | None = None
    digest_text: str | None = None
    digest: object | None = None  # WeatherDigest | LongRangeDigest (lazy import avoids hard dep)
    # Whether the LLM digest was *requested* for this run (profile toggle).
    # Distinct from ``digest_path is not None`` (= it succeeded): a pack with
    # ``llm_digest_requested=False`` skipped the digest on purpose (profile has
    # AI off), so the UI shows "AI summary off" rather than a "generating"
    # spinner. Set from ``options.generate_llm_digest`` at run start.
    llm_digest_requested: bool = False
    # LangSmith root run id for the digest LLM call (issue #244). Persisted with
    # the pack so a later 👍/👎 can attach feedback to the run that produced it.
    digest_trace_id: str | None = None
    text_digest: str | None = None
    grib_init_times: dict[str, int] = field(default_factory=dict)
    # model → freshness source key used for the direct-GRIB run (icon slot may
    # be icon_eu:dwd or icon_d2:dwd, #456). Consumed by pack building.
    grib_sources: dict[str, str] = field(default_factory=dict)
    models_fetched: list[str] = field(default_factory=list)
    models_skipped_region: list[str] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    alt_advisory_result: AdvisoryResult | None = None
    # DWD Surface Analysis & Forecast — references into the shared chart
    # cache (DATA_DIR/dwd_charts/<run_cycle>/). Bytes are not stored here.
    dwd_charts_run_cycle: str | None = None
    dwd_charts_default_id: str | None = None
    dwd_charts_in_coverage: bool = False
    dwd_charts_within_horizon: bool = False
    # Met Office surface-pressure charts — same shape, second source.
    metoffice_charts_run_cycle: str | None = None
    metoffice_charts_default_id: str | None = None
    metoffice_charts_in_coverage: bool = False
    metoffice_charts_within_horizon: bool = False
    # Météo-France TEMSI — one chosen validity per zone rather than a shared
    # run + offset (AEROWEB has no run/offset split, and the zones don't
    # publish in lockstep). See BriefingPackMeta for the full note.
    meteofrance_charts_options: list[dict[str, str]] = field(default_factory=list)
    meteofrance_charts_default_id: str | None = None
    meteofrance_charts_in_coverage: bool = False
    meteofrance_charts_within_horizon: bool = False
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

    from pydantic import ValidationError

    from weatherbrief.digest.llm_digest import LongRangeDigest, WeatherDigest

    def _parse_digest(data: dict):
        """Parse either a short-range WeatherDigest or a long-range
        LongRangeDigest, so trend continuity survives across the long↔short-range
        boundary. Tries the short-range schema first (its required ``assessment``
        fields fail on a long-range dict) and falls back to the long-range one —
        robust to either schema gaining new optional fields."""
        try:
            return WeatherDigest.model_validate(data)
        except ValidationError:
            return LongRangeDigest.model_validate(data)

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
            prev = _parse_digest(data)
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
    briefing_ready_callback: Callable[["BriefingResult"], None] | None = None,
) -> BriefingResult:
    """Run the full briefing pipeline, sampling memory for its duration.

    Thin wrapper around :func:`_execute_briefing_stages`, which holds the
    actual pipeline. It exists so the memory window has a ``finally`` to close
    on without indenting six hundred lines: the sampler has to stop even when
    the refresh raises, and a refresh that died is precisely the one whose
    peak is worth having. The boundary log line moved here with it, so a
    failed refresh now reports its memory too (#506).

    The sampled cgroup peak is CONTAINER-wide, so two concurrent briefings
    each report a peak that includes the other. That is the honest answer to
    "how close to the limit did we get", which is what it is for. The
    parent-RSS peak is this process alone and excludes the decode workers.

    See :func:`_execute_briefing_stages` for arguments and return value.
    """
    sampler = MemorySampler(interval_seconds=_MEMORY_SAMPLE_SECONDS)
    sampler.start()
    try:
        return _execute_briefing_stages(
            route,
            departure_time,
            options,
            progress_callback,
            briefing_ready_callback,
        )
    finally:
        peaks = sampler.stop()
        _log_memory(
            "briefing refresh", logger,
            task_peak_rss_mb=peaks.peak_rss_mb,
            task_peak_cgroup_mb=peaks.peak_cgroup_mb,
            task_peak_samples=peaks.samples,
        )


def _execute_briefing_stages(
    route: RouteConfig,
    departure_time: datetime,
    options: BriefingOptions | None = None,
    progress_callback: Callable[[str, str | None], None] | None = None,
    briefing_ready_callback: Callable[["BriefingResult"], None] | None = None,
) -> BriefingResult:
    """Run the full briefing pipeline.

    This is the single entry point shared by CLI and API.
    Does not print, does not call sys.exit — returns structured results.

    Args:
        departure_time: Aware UTC datetime for the flight departure.
        briefing_ready_callback: Optional callback invoked after the visible
            briefing artifacts (snapshot, advisories, GRAMET, Skew-T) have
            been produced and before the LLM digest phase. Receives the
            partially-populated ``BriefingResult`` so callers can persist a
            provisional pack row and notify clients while the digest still
            runs in the background.

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

    # Memory curve checkpoints — appended through the pipeline, dumped at end.
    rss_curve: list[tuple[str, float]] = []
    rss_pipeline_start = _current_rss_mb()
    if rss_pipeline_start is not None:
        rss_curve.append(("start", rss_pipeline_start))

    # Per-stage wall-clock timings — emitted as a single INFO line at end.
    stage_timings: dict[str, float] = {}

    # === 1. Fetch ===
    as_of_time = options.as_of_time or (departure_time if options.historical_mode else None)
    _t0 = perf_counter()
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
    stage_timings["fetch"] = perf_counter() - _t0
    rss = _current_rss_mb()
    if rss is not None:
        rss_curve.append(("fetch", rss))

    # === 2. Analyze ===
    _t0 = perf_counter()
    analysis_result = run_analysis(
        route=route,
        departure_time=departure_time,
        all_forecasts=fetch_result.all_forecasts,
        cross_sections=fetch_result.cross_sections,
        route_points=fetch_result.route_points,
        icing_severity_enhance=options.icing_severity_enhance,
        pack_dir=pack_dir,
        progress_callback=progress_callback,
        elevation=fetch_result.elevation_profile,
    )
    stage_timings["analyze"] = perf_counter() - _t0
    rss = _current_rss_mb()
    if rss is not None:
        rss_curve.append(("analyze", rss))

    # === 2.5b Front detection (experimental, opt-in via auto_front_detection) ===
    # Runs *before* advisories so the front advisory evaluator can read the
    # written route_fronts.json. Reads precomputed Hewson snapshots from
    # ${DATA_DIR}/hewson — milliseconds, zero extra fetch. Skipped entirely
    # unless the preference is enabled.
    if options.auto_front_detection and analysis_result.route_analyses:
        _t0 = perf_counter()
        try:
            from weatherbrief.tasks.fronts import run_fronts

            run_fronts(
                analysis_result.route_analyses,
                route_name=route.name,
                cruise_altitude_ft=route.cruise_altitude_ft,
                advisory_models=options.advisory_models,
                pack_dir=pack_dir,
            )
        except Exception:
            logger.warning("Front detection stage failed", exc_info=True)
        stage_timings["fronts"] = perf_counter() - _t0

    # === 3. Advisories ===
    # Build advisory preference args from options (shared by primary + alt)
    adv_aggregation = None
    if options.advisory_aggregation:
        from weatherbrief.models import AdvisoryAggregation
        adv_aggregation = AdvisoryAggregation(options.advisory_aggregation)

    # Treat the saved map as overrides, not an allow-list: advisories absent from
    # it fall back to default_enabled (matches the settings UI), so new default-on
    # advisories surface on customized profiles without a re-save.
    from weatherbrief.analysis.advisories import resolve_enabled_ids
    adv_enabled_ids = resolve_enabled_ids(options.advisory_enabled)

    route_advisories_manifest = None
    altitude_table_manifest = None
    if analysis_result.route_analyses_manifest and analysis_result.route_analyses:
        total_distance = fetch_result.route_points[-1].distance_from_origin_nm

        _t0 = perf_counter()
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
            advisory_enabled=options.advisory_enabled,
            user_params=options.advisory_params,
            aggregation=adv_aggregation,
            pack_dir=pack_dir,
            progress_callback=progress_callback,
            cruise_speed_ias_kt=options.cruise_speed_ias_kt,
            icing_method=options.icing_method,
            cloud_source=options.cloud_source,
            convective_method=options.convective_method,
            locale=options.locale,
            declared_approach_icaos=options.declared_approach_icaos,
            # Precompute the altitude table now, while cross-sections are still
            # in memory (cleared just below). Feeds the digest's ALTITUDE OPTIONS
            # block and the client-side lever; default 2000ft step (#259).
            altitude_table_step_ft=2000,
        )
        route_advisories_manifest = advisory_result.manifest
        altitude_table_manifest = advisory_result.altitude_table
        stage_timings["advisories"] = perf_counter() - _t0

    rss = _current_rss_mb()
    if rss is not None:
        rss_curve.append(("advisories", rss))

    # cross_sections is no longer needed in memory after regular advisories:
    # save_fetch_artifacts persisted cross_section.json at end of fetch, alt
    # advisories load from disk, and the snapshot save excludes cross_sections.
    # Clearing here frees ~150-300 MB on long routes (12+ wp × 4 models).
    if pack_dir and fetch_result.cross_sections:
        cs_count = sum(len(cs.point_forecasts) for cs in fetch_result.cross_sections)
        fetch_result.cross_sections.clear()
        gc.collect()
        logger.info("Cleared %d cross-section forecasts after advisories", cs_count)
        rss = _current_rss_mb()
        if rss is not None:
            rss_curve.append(("post_clear", rss))

    # === 3.1 Alt departure advisories (lite re-run) ===
    alt_advisory_result: AdvisoryResult | None = None
    # Buffer the failure diagnostic — `result` doesn't exist yet (created
    # below at section 5) and even after creation `result.diagnostics`
    # gets reassigned from fetch_result.diagnostics, which would clobber
    # any earlier append. We merge this in after that reassignment.
    _alt_advisory_diagnostic: Diagnostic | None = None
    if (
        options.alt_departure_time is not None
        and analysis_result.route_analyses_manifest
        and pack_dir
    ):
        _notify("alt_advisories")
        _t0 = perf_counter()
        try:
            from weatherbrief.tasks.advise import run_alt_from_pack

            alt_advisory_result = run_alt_from_pack(
                pack_dir=pack_dir,
                alt_departure_time=options.alt_departure_time,
                route=route,
                advisory_models=options.advisory_models,
                enabled_ids=adv_enabled_ids,
                advisory_enabled=options.advisory_enabled,
                user_params=options.advisory_params,
                aggregation=adv_aggregation,
                airports_db_path=options.airports_db_path,
                icing_method=options.icing_method,
                cloud_source=options.cloud_source,
                convective_method=options.convective_method,
                locale=options.locale,
                cruise_speed_ias_kt=options.cruise_speed_ias_kt,
                declared_approach_icaos=options.declared_approach_icaos,
            )
        except Exception:
            logger.warning("Alt advisory evaluation failed (non-fatal)", exc_info=True)
            # Belt-and-suspenders: alt-advisory is an *optional* stage
            # that re-runs evaluators on a different time slice. Its
            # failure (and even our failure to *record* its failure)
            # must never bring the main briefing pipeline down. If
            # Diagnostic.create raises here for any reason — future
            # validator change, weird edge case — we log and continue;
            # the user just won't see the alt-advisory section.
            #
            # format_exc() is fine here because we're inside the active
            # except block. classify_llm_exception (the other diagnostic
            # construction site) uses the explicit format_exception(...)
            # form because it can be called from outside an except.
            try:
                _alt_advisory_diagnostic = Diagnostic.create(
                    level="warn",
                    stage="advisories",
                    code=AdvisoryCode.ALT_ADVISORY_FAILED,
                    message="Alternate-departure advisories unavailable for this briefing.",
                    detail=traceback.format_exc(),
                )
            except Exception:
                logger.warning(
                    "Could not construct alt-advisory failure diagnostic — "
                    "user won't see why alt-advisory is missing",
                    exc_info=True,
                )
                _alt_advisory_diagnostic = None
        stage_timings["alt_advisories"] = perf_counter() - _t0

    # === 3.5 Route weather observations + SIGMETs (D-0 only) ===
    route_observations = None
    route_sigmets = None
    result_usage_sigmet = False
    result_usage_sigmet_count = 0
    if days_out == 0 and options.airports_db_path and not options.historical_mode:
        _notify("route_weather")
        _t0 = perf_counter()
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
            # Collect runway data for wind advisory comparison + field
            # elevation for the ceiling AGL conversion (#441 finding #3).
            obs_elevations = None
            obs_icaos = [a.icao for a in route_observations.airports]
            try:
                from weatherbrief.airports import get_runway_ends

                obs_runway_data = get_runway_ends(obs_icaos, options.airports_db_path)
            except Exception:
                obs_runway_data = None
            # Separate try: an elevation-lookup failure must not discard
            # otherwise-good runway data. (#441 review)
            try:
                from weatherbrief.airports import get_airport_elevations

                obs_elevations = get_airport_elevations(obs_icaos, options.airports_db_path)
            except Exception:
                obs_elevations = None

            route_observations = run_observation_comparison(
                observations=route_observations,
                snapshot_forecasts=fetch_result.all_forecasts,
                target_time=target_dt,
                route=route,
                runway_data=obs_runway_data,
                route_analyses=analysis_result.route_analyses,
                airport_elevations=obs_elevations,
                cloud_source=options.cloud_source,
            )
            result_usage_metar = True
            result_usage_metar_airports = route_observations.airports_with_metar
        except Exception:
            logger.warning("Route weather fetch failed", exc_info=True)
            result_usage_metar = False
            result_usage_metar_airports = 0

        # Route SIGMETs (area hazards) — independent of METAR/TAF success.
        # Import outside the try so a genuine ImportError surfaces rather than
        # being swallowed as a SIGMET fetch failure.
        from weatherbrief.tasks.route_weather import run_route_sigmets

        try:
            route_sigmets = run_route_sigmets(
                route=route,
                target_time=target_dt,
                corridor_nm=options.sigmet_corridor_nm,
                airports_db_path=options.airports_db_path,
            )
            result_usage_sigmet = True
            result_usage_sigmet_count = route_sigmets.count
        except Exception:
            logger.warning("Route SIGMET fetch failed", exc_info=True)
        stage_timings["route_weather"] = perf_counter() - _t0
    else:
        result_usage_metar = False
        result_usage_metar_airports = 0

    # === 3.55 Observed conditions along the corridor (D-0 only) ===
    # Radar, lightning and satellite cloud tops sampled from frames the
    # collector already holds on disk — no network I/O in this stage, by
    # construction (see weatherbrief.observed).  Phase 1 displays only: it
    # computes no verdict and feeds no advisory, so a failure here costs the
    # observed panel and nothing else.
    observed_conditions = None
    if days_out == 0 and not options.historical_mode:
        from weatherbrief.observed.collect import observed_enabled

        if observed_enabled():
            _t0 = perf_counter()
            try:
                from weatherbrief.observed.payload import build_observed_conditions

                observed_conditions = build_observed_conditions(route)
            except Exception:
                logger.warning("Observed conditions stage failed", exc_info=True)
            stage_timings["observed_conditions"] = perf_counter() - _t0

    # === 3.6 Weather-based alternates (D-2 inward, opt-in) ===
    # Surfaces the closest divert candidates that fix the destination's
    # specific problem (category/wind/crosswind), via the SAME shared assembly
    # as the forecast map. Gated on the medium-range window (`days_out <= 2`)
    # and the `compute_alternates` preference — a "fast brief" skips it.
    alternates = None
    if (
        days_out <= 2
        and options.compute_alternates
        and options.airports_db_path
        and not options.historical_mode
    ):
        _notify("alternates")
        _t0 = perf_counter()
        try:
            from weatherbrief.tasks.alternates import run_alternates

            alternates = run_alternates(
                route=route,
                target_time=target_dt,
                airports_db_path=options.airports_db_path,
                # Reduce per-model data the same way the airport arrival card
                # does, so the same airport shows one category everywhere.
                # Default matches the advisory default (majority).
                aggregation=(options.advisory_aggregation or "majority"),
            )
        except Exception:
            logger.warning("Alternates stage failed", exc_info=True)
        stage_timings["alternates"] = perf_counter() - _t0

    # === 4. Build & save snapshot ===
    _t0 = perf_counter()
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
        route_sigmets=route_sigmets,
        observed_conditions=observed_conditions,
        alternates=alternates,
    )

    # === 4.1 Regulatory alternate requirement (FAA 91.169 + EASA Part-NCO) ===
    # Post-step (after alternates + route weather): does the destination need a
    # filed alternate, and does each computed divert candidate meet alternate
    # minima? Uses the destination TAF when available (D-0), else the NWP
    # consensus fallback. Mutates snapshot.alternates in place (#249).
    if (
        snapshot.alternates is not None
        and options.airports_db_path
        and not options.historical_mode
    ):
        _t0 = perf_counter()
        try:
            from weatherbrief.tasks.alternate_requirement import run_alternate_requirement

            run_alternate_requirement(snapshot, options.airports_db_path)
        except Exception:
            logger.warning("Alternate-requirement stage failed", exc_info=True)
        stage_timings["alternate_requirement"] = perf_counter() - _t0

    if pack_dir:
        from weatherbrief.tasks.artifacts import save_analysis_artifacts

        save_analysis_artifacts(pack_dir, snapshot, analysis_result.route_analyses_manifest)
        snapshot_path = pack_dir / "briefing.json"
    else:
        snapshot_path = save_snapshot(snapshot, data_dir)
        if fetch_result.cross_sections:
            save_cross_section(snapshot, data_dir)
    _notify("save_snapshot")
    stage_timings["save_snapshot"] = perf_counter() - _t0
    logger.info("Snapshot saved: %s", snapshot_path)

    result = BriefingResult(snapshot=snapshot, snapshot_path=snapshot_path)
    result.llm_digest_requested = options.generate_llm_digest
    result.grib_init_times = fetch_result.grib_init_times
    result.grib_sources = fetch_result.grib_sources
    result.models_fetched = fetch_result.models_fetched
    result.models_skipped_region = fetch_result.models_skipped_region
    result.diagnostics = fetch_result.diagnostics
    # Merge any earlier-buffered diagnostics from stages that ran before
    # `result` existed (alt-advisories runs before snapshot construction).
    if _alt_advisory_diagnostic is not None:
        result.diagnostics.append(_alt_advisory_diagnostic)
    result.usage.open_meteo_calls = fetch_result.open_meteo_api_calls
    result.usage.grib_enrichment = fetch_result.grib_enriched
    result.usage.grib_enrichment_failed = fetch_result.grib_enrichment_failed
    result.usage.metar_taf_fetched = result_usage_metar
    result.usage.metar_taf_airports = result_usage_metar_airports
    result.usage.sigmet_fetched = result_usage_sigmet
    result.usage.sigmet_count = result_usage_sigmet_count
    result.usage.alternates_computed = alternates is not None
    result.usage.alternates_count = len(alternates.alternates) if alternates else 0
    result.usage.alternates_candidates = alternates.candidates_evaluated if alternates else 0
    if fetch_result.elevation_profile and pack_dir:
        result.elevation_profile_path = pack_dir / "elevation_profile.json"
    if route_advisories_manifest and pack_dir:
        result.route_advisories_path = pack_dir / "route_advisories.json"
    if route_advisories_manifest is not None:
        result.route_advisories_manifest = route_advisories_manifest
    if alt_advisory_result is not None:
        result.alt_advisory_result = alt_advisory_result

    # === 5. Optional: GRAMET ===
    if options.fetch_gramet:
        _notify("fetch_gramet")
        _t0 = perf_counter()
        gramet_result = run_gramet(
            route=route,
            departure_time=departure_time,
            pack_dir=pack_dir,
            data_dir=data_dir,
            days_out=days_out,
            fetch_date=today,
            autorouter_token=options.autorouter_token,
            user_id=options.user_id,
        )
        if gramet_result.path:
            result.gramet_path = gramet_result.path
            result.usage.gramet_fetched = gramet_result.fetched
        if gramet_result.failed:
            result.usage.gramet_failed = True
        if gramet_result.diagnostic:
            result.diagnostics.append(gramet_result.diagnostic)
        stage_timings["fetch_gramet"] = perf_counter() - _t0

    # === 5b. DWD Surface Analysis & Forecast ===
    # Cheap (six parallel conditional GETs, ~300ms when nothing changed)
    # so it always runs — eligibility is checked inside run_dwd_charts.
    _t0 = perf_counter()
    dwd_charts_result = run_dwd_charts(
        route=route,
        departure_time=departure_time,
        data_dir=data_dir,
        pack_dir=pack_dir,
    )
    result.dwd_charts_run_cycle = dwd_charts_result.run_cycle
    result.dwd_charts_default_id = dwd_charts_result.default_chart_id
    result.dwd_charts_in_coverage = dwd_charts_result.in_coverage
    result.dwd_charts_within_horizon = dwd_charts_result.within_horizon
    stage_timings["dwd_charts"] = perf_counter() - _t0

    # === 5c. Met Office Surface Pressure charts ===
    # Same cheap conditional-GET pattern as DWD; second front-chart source
    # (admin-gated until Met Office authorise reuse). Eligibility checked
    # inside run_metoffice_charts.
    _t0 = perf_counter()
    metoffice_charts_result = run_metoffice_charts(
        route=route,
        departure_time=departure_time,
        data_dir=data_dir,
    )
    result.metoffice_charts_run_cycle = metoffice_charts_result.run_cycle
    result.metoffice_charts_default_id = metoffice_charts_result.default_chart_id
    result.metoffice_charts_in_coverage = metoffice_charts_result.in_coverage
    result.metoffice_charts_within_horizon = metoffice_charts_result.within_horizon
    stage_timings["metoffice_charts"] = perf_counter() - _t0

    # === 5d. Météo-France TEMSI (SIGWX) charts ===
    # Two small downloads at most, and skipped entirely for routes that don't
    # touch France — the licence limits redistribution to users operating in
    # French airspace. Eligibility checked inside run_meteofrance_charts.
    _t0 = perf_counter()
    meteofrance_charts_result = run_meteofrance_charts(
        route=route,
        departure_time=departure_time,
        data_dir=data_dir,
    )
    result.meteofrance_charts_options = meteofrance_charts_result.options
    result.meteofrance_charts_default_id = meteofrance_charts_result.default_id
    result.meteofrance_charts_in_coverage = meteofrance_charts_result.in_coverage
    result.meteofrance_charts_within_horizon = meteofrance_charts_result.within_horizon
    stage_timings["meteofrance_charts"] = perf_counter() - _t0

    # === 6. Optional: Skew-T ===
    if options.generate_skewt:
        _notify("generate_skewt")
        _t0 = perf_counter()
        skewt_result = run_skewt(
            snapshot=snapshot,
            target_time=target_dt,
            pack_dir=pack_dir,
            data_dir=data_dir,
        )
        if skewt_result.paths:
            result.skewt_paths = skewt_result.paths
        if skewt_result.diagnostic:
            result.diagnostics.append(skewt_result.diagnostic)
        stage_timings["generate_skewt"] = perf_counter() - _t0

    # #392: a digest cannot narrate data we do not have. When no advisory graded
    # the route the pack is UNAVAILABLE, and the digest schema only lets the LLM
    # answer GREEN/AMBER/RED — so asking it to summarise an empty manifest buys
    # a confidently-worded traffic light built on nothing, and charges us for it.
    # Skip the phase; the assessment clamp in api/packs.py owns the verdict.
    run_digest_phase = options.generate_llm_digest and not advisories_ungradeable(
        route_advisories_manifest
    )
    if options.generate_llm_digest and not run_digest_phase:
        logger.warning(
            "Skipping LLM digest — no advisory could be graded (assessment will be UNAVAILABLE)"
        )

    # === 6.5 Briefing ready milestone ===
    # Visible briefing artifacts (snapshot + advisories + optional GRAMET) are
    # on disk by this point. Notify callers so they can render the briefing
    # and persist a provisional pack row while the LLM digest still runs.
    #
    # When the digest phase is disabled there's nothing to wait for: the
    # provisional row would be inserted and immediately overwritten by
    # finalize, with no user-visible benefit. Skip both the progress event
    # and the callback in that case.
    if run_digest_phase:
        _notify("briefing_ready", str(snapshot_path))
        if briefing_ready_callback is not None:
            try:
                briefing_ready_callback(result)
            except Exception:
                logger.warning(
                    "briefing_ready_callback raised — continuing with digest",
                    exc_info=True,
                )

    # === 7. Optional: LLM digest ===
    if run_digest_phase:
        _notify("llm_digest")
        _t0 = perf_counter()
        previous_digest = _load_previous_digest(pack_dir, days_out)
        digest_result = run_llm_digest(
            snapshot=snapshot,
            target_time=target_dt,
            digest_config_name=options.digest_config_name,
            pack_dir=pack_dir,
            data_dir=data_dir,
            route_advisories=route_advisories_manifest,
            altitude_table=altitude_table_manifest,
            flight_rules=options.flight_rules,
            previous_digest=previous_digest,
            locale=options.locale,
            units_region=options.units_region,
            profile_id=options.profile_id,
            profile_name=options.profile_name,
            guidance_key=options.guidance_key,
        )
        if digest_result.digest is not None:
            result.digest = digest_result.digest
        if digest_result.digest_trace_id:
            result.digest_trace_id = digest_result.digest_trace_id
        if digest_result.path:
            result.digest_path = digest_result.path
        if digest_result.text:
            result.digest_text = digest_result.text
        if digest_result.llm_model:
            result.usage.llm_digest = True
            result.usage.llm_model = digest_result.llm_model
            result.usage.llm_input_tokens = digest_result.llm_input_tokens
            result.usage.llm_output_tokens = digest_result.llm_output_tokens
            result.usage.llm_cache_read_tokens = digest_result.llm_cache_read_tokens
            result.usage.llm_cache_write_tokens = digest_result.llm_cache_write_tokens
        if digest_result.diagnostic:
            result.diagnostics.append(digest_result.diagnostic)
        stage_timings["llm_digest"] = perf_counter() - _t0

    # === 8. Always: text digest ===
    from weatherbrief.digest.text import format_digest

    output_paths = [str(result.snapshot_path)]
    if result.gramet_path:
        output_paths.append(str(result.gramet_path))
    output_paths.extend(str(p) for p in result.skewt_paths)
    if result.digest_path:
        output_paths.append(str(result.digest_path))

    result.text_digest = format_digest(snapshot, target_dt, output_paths=output_paths)

    # Rewrite fetch_meta.json with the merged diagnostics from all stages
    # (fetch + analyze + advisories + gramet + skewt + digest). The fetch
    # stage already wrote its own subset earlier; this final write supersedes
    # it so on-disk and in-DB diagnostics agree.
    if pack_dir is not None and pack_dir.exists():
        # Preserve the original fetch timestamp written by save_fetch_artifacts.
        # `fetched_at` records when the weather data was *fetched*, not when
        # the JSON file was last rewritten — so we read it back rather than
        # letting write_pack_meta default to datetime.now().
        #
        # Timestamp recovery is best-effort: a corrupt/missing value falls
        # back to None (write_pack_meta then uses now()) but must NOT skip
        # the rewrite itself, which would silently lose every post-fetch
        # diagnostic from the persisted artifact.
        fetched_at: datetime | None = None
        try:
            existing = load_fetch_meta(pack_dir) or {}
            fetched_at_raw = existing.get("fetched_at")
            if fetched_at_raw:
                fetched_at = datetime.fromisoformat(fetched_at_raw)
        except (ValueError, TypeError, OSError):
            logger.debug(
                "Could not recover fetched_at from existing pack meta; "
                "falling back to now()",
                exc_info=True,
            )

        try:
            write_pack_meta(
                pack_dir,
                models_fetched=result.models_fetched,
                diagnostics=result.diagnostics,
                fetched_at=fetched_at,
            )
        except Exception:
            logger.warning("Failed to rewrite pack meta with full diagnostics", exc_info=True)

    rss_end = _current_rss_mb()
    if rss_end is not None:
        rss_curve.append(("end", rss_end))
    if rss_curve:
        # The curve is a SHAPE across stages, not a peak. It used to also carry
        # `peak=X (+Y this request)` from ru_maxrss — a process-lifetime,
        # monotonic, parent-only high-water mark. That figure now sat next to
        # the sampler's windowed `this task peaked at …`, two numbers built by
        # different methods, free to disagree, and looking like answers to the
        # same question — the very confusion #506 exists to remove. Dropped
        # rather than flagged: leak detection is already handled better by
        # log_memory's WARN escalation, which keeps a baseline ACROSS requests
        # instead of diffing an all-time mark within one (PR #507 review).
        curve_str = " ".join(f"{label}={int(v)}" for label, v in rss_curve)
        request_growth = rss_end - rss_curve[0][1] if rss_end is not None else 0
        logger.info(
            "Memory curve: %s MB; request_growth=%+d MB",
            curve_str, int(request_growth),
        )

    if stage_timings:
        items = sorted(stage_timings.items(), key=lambda kv: -kv[1])
        parts = " ".join(f"{label}={secs:.2f}s" for label, secs in items)
        total = sum(stage_timings.values())
        logger.info("Pipeline timing: %s total=%.2fs", parts, total)

    # The boundary memory log lives in the execute_briefing wrapper now, so it
    # also fires when this function raises (#506).
    return result
