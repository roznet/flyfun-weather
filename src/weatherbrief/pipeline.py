"""Core briefing pipeline — shared by CLI and API.

Orchestrates: fetch → analyze → snapshot → optional outputs (GRAMET, Skew-T, LLM digest).
Returns structured results without printing or exiting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from weatherbrief.analysis.comparison import compare_models
from weatherbrief.analysis.sounding import analyze_sounding
from weatherbrief.analysis.sounding.advisories import compute_altitude_advisories
from weatherbrief.analysis.wind import compute_wind_components
from weatherbrief.fetch.open_meteo import OpenMeteoClient
from weatherbrief.fetch.route_points import interpolate_route
from weatherbrief.fetch.variables import MODEL_ENDPOINTS
from weatherbrief.models import (
    ForecastSnapshot,
    ModelSource,
    RouteCrossSection,
    RouteConfig,
    WaypointAnalysis,
    WaypointForecast,
)
from weatherbrief.storage.snapshots import DEFAULT_DATA_DIR, save_cross_section, save_snapshot

logger = logging.getLogger(__name__)

DEFAULT_MODELS = [ModelSource.GFS, ModelSource.ECMWF, ModelSource.ICON]


@dataclass
class BriefingOptions:
    """Options controlling what the pipeline produces."""

    models: list[ModelSource] = field(default_factory=lambda: list(DEFAULT_MODELS))
    fetch_gramet: bool = False
    generate_skewt: bool = False
    generate_llm_digest: bool = False
    digest_config_name: str | None = None
    data_dir: Path | None = None
    output_dir: Path | None = None  # if set, write all artifacts here (pack mode)


@dataclass
class BriefingResult:
    """Structured result from a briefing pipeline run."""

    snapshot: ForecastSnapshot
    snapshot_path: Path
    gramet_path: Path | None = None
    skewt_paths: list[Path] = field(default_factory=list)
    digest_path: Path | None = None
    digest_text: str | None = None
    digest: object | None = None  # WeatherDigest (lazy import avoids hard dep)
    text_digest: str | None = None
    errors: list[str] = field(default_factory=list)


def execute_briefing(
    route: RouteConfig,
    target_date: str,
    target_hour: int = 9,
    options: BriefingOptions | None = None,
) -> BriefingResult:
    """Run the full briefing pipeline.

    This is the single entry point shared by CLI and API.
    Does not print, does not call sys.exit — returns structured results.

    Raises:
        ValueError: If target_date is in the past.
    """
    options = options or BriefingOptions()
    data_dir = options.data_dir or DEFAULT_DATA_DIR

    today = date.today().isoformat()
    # Naive datetime — UTC by convention, matching Open-Meteo's naive timestamps
    target_dt = datetime(
        *map(int, target_date.split("-")), target_hour
    )
    days_out = (date.fromisoformat(target_date) - date.today()).days

    if days_out < 0:
        raise ValueError(f"Target date {target_date} is in the past")

    logger.info("Route: %s", route.name)
    logger.info("Target: %s (%d days out)", target_date, days_out)
    logger.info("Models: %s", ", ".join(m.value for m in options.models))

    # --- Fetch forecasts (multi-point: 1 API call per model) ---
    client = OpenMeteoClient()
    route_points = interpolate_route(route, spacing_nm=20.0)
    logger.info("Route interpolated: %d points along %.0f nm",
                len(route_points), route_points[-1].distance_from_origin_nm)

    all_forecasts: list[WaypointForecast] = []
    cross_sections: list[RouteCrossSection] = []

    for model in options.models:
        endpoint = MODEL_ENDPOINTS[model.value]
        if days_out is not None and days_out >= endpoint.max_days:
            logger.info(
                "Skipping %s: %d days out exceeds %d-day range",
                model.value, days_out, endpoint.max_days,
            )
            continue
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
        except Exception:
            logger.warning("Failed to fetch %s", model.value, exc_info=True)

    # --- Analyze ---
    analyses: list[WaypointAnalysis] = []

    for waypoint in route.waypoints:
        wp_forecasts = [
            f for f in all_forecasts if f.waypoint.icao == waypoint.icao
        ]
        track_deg = route.waypoint_track(waypoint.icao)
        analysis = analyze_waypoint(
            wp_forecasts, target_dt, track_deg,
            cruise_altitude_ft=route.cruise_altitude_ft,
            flight_ceiling_ft=route.flight_ceiling_ft,
        )
        analyses.append(analysis)

    # --- Build & save snapshot ---
    snapshot = ForecastSnapshot(
        route=route,
        target_date=target_date,
        fetch_date=today,
        days_out=days_out,
        forecasts=all_forecasts,
        analyses=analyses,
        cross_sections=cross_sections,
    )

    if options.output_dir:
        # Pack mode: write directly to flat output directory
        options.output_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = options.output_dir / "snapshot.json"
        # Exclude cross_sections from snapshot.json (saved separately)
        snapshot_path.write_text(
            snapshot.model_dump_json(indent=2, exclude={"cross_sections"})
        )
        if cross_sections:
            cs_path = options.output_dir / "cross_section.json"
            cs_path.write_text(
                snapshot.model_dump_json(indent=2, include={"cross_sections"})
            )
    else:
        snapshot_path = save_snapshot(snapshot, data_dir)
        if cross_sections:
            save_cross_section(snapshot, data_dir)
    logger.info("Snapshot saved: %s", snapshot_path)

    result = BriefingResult(snapshot=snapshot, snapshot_path=snapshot_path)

    # --- Optional: GRAMET ---
    if options.fetch_gramet:
        _run_gramet(route, target_date, target_hour, days_out, today, data_dir, result,
                    output_dir=options.output_dir)

    # --- Optional: Skew-T ---
    if options.generate_skewt:
        _run_skewt(snapshot, target_dt, target_date, days_out, today, data_dir, result,
                   output_dir=options.output_dir)

    # --- Optional: LLM digest ---
    if options.generate_llm_digest:
        _run_llm_digest(
            snapshot, target_dt, target_date, days_out, today,
            data_dir, options.digest_config_name, result,
            output_dir=options.output_dir,
        )

    # --- Always: text digest ---
    from weatherbrief.digest.text import format_digest

    output_paths = [str(result.snapshot_path)]
    if result.gramet_path:
        output_paths.append(str(result.gramet_path))
    output_paths.extend(str(p) for p in result.skewt_paths)
    if result.digest_path:
        output_paths.append(str(result.digest_path))

    result.text_digest = format_digest(snapshot, target_dt, output_paths=output_paths)

    return result


def analyze_waypoint(
    forecasts: list[WaypointForecast],
    target_time: datetime,
    track_deg: float,
    cruise_altitude_ft: int = 8000,
    flight_ceiling_ft: int = 18000,
) -> WaypointAnalysis:
    """Run all analysis on forecasts for a single waypoint."""
    if not forecasts:
        raise ValueError("No forecasts to analyze")

    waypoint = forecasts[0].waypoint
    analysis = WaypointAnalysis(waypoint=waypoint, target_time=target_time)

    # Collect model values for comparison
    model_temps: dict[str, float] = {}
    model_winds: dict[str, float] = {}
    model_wind_dirs: dict[str, float] = {}
    model_cloud: dict[str, float] = {}
    model_precip: dict[str, float] = {}
    model_freezing: dict[str, float] = {}
    # New sounding-derived comparison values
    model_freezing_ft: dict[str, float] = {}
    model_cape: dict[str, float] = {}
    model_lcl_ft: dict[str, float] = {}
    model_k_index: dict[str, float] = {}
    model_total_totals: dict[str, float] = {}
    model_pw: dict[str, float] = {}
    model_li: dict[str, float] = {}
    model_shear: dict[str, float] = {}

    for wf in forecasts:
        hourly = wf.at_time(target_time)
        if not hourly:
            continue

        model_key = wf.model.value

        # Try to find cruise-altitude wind (closest level to cruise pressure)
        cruise_wind = None
        for level in hourly.pressure_levels:
            if level.wind_speed_kt is not None and level.wind_direction_deg is not None:
                if cruise_wind is None or abs(level.pressure_hpa - 750) < abs(
                    cruise_wind.pressure_hpa - 750
                ):
                    cruise_wind = level

        if cruise_wind and cruise_wind.wind_speed_kt is not None:
            wc = compute_wind_components(
                cruise_wind.wind_speed_kt, cruise_wind.wind_direction_deg, track_deg
            )
            analysis.wind_components[model_key] = wc
            model_winds[model_key] = cruise_wind.wind_speed_kt
            model_wind_dirs[model_key] = cruise_wind.wind_direction_deg

        # Sounding analysis
        sounding = analyze_sounding(hourly.pressure_levels, hourly)
        if sounding is not None:
            analysis.sounding[model_key] = sounding

            # Collect sounding-derived comparison values
            idx = sounding.indices
            if idx is not None:
                if idx.freezing_level_ft is not None:
                    model_freezing_ft[model_key] = idx.freezing_level_ft
                if idx.cape_surface_jkg is not None:
                    model_cape[model_key] = idx.cape_surface_jkg
                if idx.lcl_altitude_ft is not None:
                    model_lcl_ft[model_key] = idx.lcl_altitude_ft
                if idx.k_index is not None:
                    model_k_index[model_key] = idx.k_index
                if idx.total_totals is not None:
                    model_total_totals[model_key] = idx.total_totals
                if idx.precipitable_water_mm is not None:
                    model_pw[model_key] = idx.precipitable_water_mm
                if idx.lifted_index is not None:
                    model_li[model_key] = idx.lifted_index
                if idx.bulk_shear_0_6km_kt is not None:
                    model_shear[model_key] = idx.bulk_shear_0_6km_kt

        # Collect comparison values
        if hourly.temperature_2m_c is not None:
            model_temps[model_key] = hourly.temperature_2m_c
        if hourly.cloud_cover_pct is not None:
            model_cloud[model_key] = hourly.cloud_cover_pct
        if hourly.precipitation_mm is not None:
            model_precip[model_key] = hourly.precipitation_mm
        if hourly.freezing_level_m is not None:
            model_freezing[model_key] = hourly.freezing_level_m

    # Altitude advisories across models
    if analysis.sounding:
        analysis.altitude_advisories = compute_altitude_advisories(
            analysis.sounding, cruise_altitude_ft, flight_ceiling_ft
        )

    # Model comparison (need at least 2 models)
    comparisons = {
        "temperature_c": model_temps,
        "wind_speed_kt": model_winds,
        "wind_direction_deg": model_wind_dirs,
        "cloud_cover_pct": model_cloud,
        "precipitation_mm": model_precip,
        "freezing_level_m": model_freezing,
        # Sounding-derived
        "freezing_level_ft": model_freezing_ft,
        "cape_surface_jkg": model_cape,
        "lcl_altitude_ft": model_lcl_ft,
        "k_index": model_k_index,
        "total_totals": model_total_totals,
        "precipitable_water_mm": model_pw,
        "lifted_index": model_li,
        "bulk_shear_0_6km_kt": model_shear,
    }

    for var_name, values in comparisons.items():
        if len(values) >= 2:
            analysis.model_divergence.append(compare_models(var_name, values))

    return analysis


def _run_gramet(
    route: RouteConfig,
    target_date: str,
    target_hour: int,
    days_out: int,
    fetch_date: str,
    data_dir: Path,
    result: BriefingResult,
    *,
    output_dir: Path | None = None,
) -> None:
    """Fetch GRAMET cross-section if available."""
    try:
        from weatherbrief.fetch.gramet import AutorouterGramet

        # UTC-aware for correct Unix timestamp in GRAMET API call
        departure_time = datetime(
            *map(int, target_date.split("-")), target_hour, tzinfo=timezone.utc
        )
        icao_codes = [wp.icao for wp in route.waypoints]
        duration_hours = route.flight_duration_hours or 2.0

        gramet_client = AutorouterGramet()
        data = gramet_client.fetch_gramet(
            icao_codes=icao_codes,
            altitude_ft=route.cruise_altitude_ft,
            departure_time=departure_time,
            duration_hours=duration_hours,
        )

        if output_dir:
            out_path = output_dir / "gramet.png"
        else:
            out_dir = data_dir / "gramet" / target_date / f"d-{days_out}_{fetch_date}"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / "gramet.png"
        out_path.write_bytes(data)
        result.gramet_path = out_path
        logger.info("GRAMET saved: %s", out_path)

    except ImportError:
        logger.warning("GRAMET fetch requires euro_aip with autorouter credentials")
        result.errors.append("GRAMET: euro_aip not available")
    except Exception as exc:
        logger.warning("GRAMET fetch failed: %s", exc, exc_info=True)
        result.errors.append(f"GRAMET: {exc}")


def _run_skewt(
    snapshot: ForecastSnapshot,
    target_time: datetime,
    target_date: str,
    days_out: int,
    fetch_date: str,
    data_dir: Path,
    result: BriefingResult,
    *,
    output_dir: Path | None = None,
) -> None:
    """Generate Skew-T plots for all waypoints."""
    try:
        from weatherbrief.digest.skewt import generate_all_skewts

        if output_dir:
            out_dir = output_dir / "skewt"
        else:
            out_dir = data_dir / "skewt" / target_date / f"d-{days_out}_{fetch_date}"
        paths = generate_all_skewts(snapshot, target_time, out_dir)
        result.skewt_paths = [Path(p) for p in paths]
        for p in paths:
            logger.info("Skew-T saved: %s", p)

    except ImportError:
        logger.warning("Skew-T generation requires metpy, numpy, matplotlib")
        result.errors.append("Skew-T: metpy not available")
    except Exception as exc:
        logger.warning("Skew-T generation failed: %s", exc, exc_info=True)
        result.errors.append(f"Skew-T: {exc}")


def _run_llm_digest(
    snapshot: ForecastSnapshot,
    target_time: datetime,
    target_date: str,
    days_out: int,
    fetch_date: str,
    data_dir: Path,
    digest_config_name: str | None,
    result: BriefingResult,
    *,
    output_dir: Path | None = None,
) -> None:
    """Generate LLM-powered weather digest."""
    try:
        from weatherbrief.digest.llm_config import load_digest_config
        from weatherbrief.digest.llm_digest import run_digest

        config = load_digest_config(digest_config_name)
        logger.info("LLM digest: %s/%s", config.llm.provider, config.llm.model)

        digest_result = run_digest(snapshot, target_time, config)

        if digest_result.get("error"):
            result.errors.append(f"LLM digest: {digest_result['error']}")
            return

        digest_obj = digest_result.get("digest")
        result.digest = digest_obj

        # Save markdown + structured JSON digest
        if output_dir:
            md_path = output_dir / "digest.md"
            json_path = output_dir / "digest.json"
        else:
            out_dir = data_dir / "digests" / target_date / f"d-{days_out}_{fetch_date}"
            out_dir.mkdir(parents=True, exist_ok=True)
            md_path = out_dir / "digest.md"
            json_path = out_dir / "digest.json"
        md_path.write_text(digest_result["digest_text"])
        if digest_obj is not None:
            json_path.write_text(digest_obj.model_dump_json(indent=2))
        result.digest_path = md_path
        result.digest_text = digest_result["digest_text"]
        logger.info("LLM digest saved: %s", md_path)

    except Exception as exc:
        logger.warning("LLM digest generation failed: %s", exc, exc_info=True)
        result.errors.append(f"LLM digest: {exc}")
