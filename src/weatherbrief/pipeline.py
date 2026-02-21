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
    icing_severity_enhance: bool = True  # enable RH/PW icing severity upgrades


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
    errors: list[str] = field(default_factory=list)
    usage: BriefingUsage = field(default_factory=BriefingUsage)


def execute_briefing(
    route: RouteConfig,
    target_date: str,
    target_hour: int = 9,
    options: BriefingOptions | None = None,
    progress_callback: Callable[[str, str | None], None] | None = None,
) -> BriefingResult:
    """Run the full briefing pipeline.

    This is the single entry point shared by CLI and API.
    Does not print, does not call sys.exit — returns structured results.

    Raises:
        ValueError: If target_date is in the past.
    """
    options = options or BriefingOptions()
    data_dir = options.data_dir or DEFAULT_DATA_DIR
    pack_dir = options.output_dir  # None means legacy snapshot mode

    def _notify(stage: str, detail: str | None = None) -> None:
        if progress_callback is not None:
            progress_callback(stage, detail)

    today_utc = datetime.now(timezone.utc).date()
    today = today_utc.isoformat()
    # Naive datetime — UTC by convention, matching Open-Meteo's naive timestamps
    target_dt = datetime(
        *map(int, target_date.split("-")), target_hour
    )
    days_out = (date.fromisoformat(target_date) - today_utc).days

    if days_out < 0:
        raise ValueError(f"Target date {target_date} is in the past")

    logger.info("Route: %s", route.name)
    logger.info("Target: %s (%d days out)", target_date, days_out)
    logger.info("Models: %s", ", ".join(m.value for m in options.models))

    # === 1. Fetch ===
    fetch_result = run_fetch(
        route=route,
        target_date=target_date,
        target_hour=target_hour,
        models=options.models,
        enrich_grib=options.enrich_grib,
        data_dir=data_dir,
        pack_dir=pack_dir,
        user_id=options.user_id,
        progress_callback=progress_callback,
    )

    # === 2. Analyze ===
    analysis_result = run_analysis(
        route=route,
        target_date=target_date,
        target_hour=target_hour,
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
        advisory_result = run_advisories(
            rp_analyses=analysis_result.route_analyses,
            cross_sections=fetch_result.cross_sections,
            elevation_profile=fetch_result.elevation_profile,
            model_names=analysis_result.model_names,
            route=route,
            total_distance_nm=total_distance,
            advisory_models=options.advisory_models,
            airports_db_path=options.airports_db_path,
            pack_dir=pack_dir,
            progress_callback=progress_callback,
        )
        route_advisories_manifest = advisory_result.manifest

    # === 4. Build & save snapshot ===
    snapshot = ForecastSnapshot(
        route=route,
        target_date=target_date,
        fetch_date=today,
        days_out=days_out,
        forecasts=fetch_result.all_forecasts,
        analyses=analysis_result.waypoint_analyses,
        cross_sections=fetch_result.cross_sections,
    )

    if pack_dir:
        from weatherbrief.tasks.artifacts import save_analysis_artifacts

        save_analysis_artifacts(pack_dir, snapshot, analysis_result.route_analyses_manifest)
        snapshot_path = pack_dir / "snapshot.json"
    else:
        snapshot_path = save_snapshot(snapshot, data_dir)
        if fetch_result.cross_sections:
            save_cross_section(snapshot, data_dir)
    _notify("save_snapshot")
    logger.info("Snapshot saved: %s", snapshot_path)

    result = BriefingResult(snapshot=snapshot, snapshot_path=snapshot_path)
    result.usage.open_meteo_calls = len(fetch_result.cross_sections)
    result.usage.grib_enrichment = fetch_result.grib_enriched
    result.usage.grib_enrichment_failed = fetch_result.grib_enrichment_failed
    if fetch_result.elevation_profile and pack_dir:
        result.elevation_profile_path = pack_dir / "elevation_profile.json"
    if route_advisories_manifest and pack_dir:
        result.route_advisories_path = pack_dir / "route_advisories.json"

    # === 5. Optional: GRAMET ===
    if options.fetch_gramet:
        _notify("fetch_gramet")
        gramet_result = run_gramet(
            route=route,
            target_date=target_date,
            target_hour=target_hour,
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
            target_date=target_date,
            days_out=days_out,
            fetch_date=today,
        )
        if skewt_result.paths:
            result.skewt_paths = skewt_result.paths
        if skewt_result.error:
            result.errors.append(skewt_result.error)

    # === 7. Optional: LLM digest ===
    if options.generate_llm_digest:
        _notify("llm_digest")
        digest_result = run_llm_digest(
            snapshot=snapshot,
            target_time=target_dt,
            digest_config_name=options.digest_config_name,
            pack_dir=pack_dir,
            data_dir=data_dir,
            target_date=target_date,
            days_out=days_out,
            fetch_date=today,
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
