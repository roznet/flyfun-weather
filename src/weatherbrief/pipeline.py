"""Core briefing pipeline — shared by CLI and API.

Orchestrates: fetch → analyze → snapshot → optional outputs (GRAMET, Skew-T, LLM digest).
Returns structured results without printing or exiting.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from weatherbrief.analysis.comparison import compare_models
from weatherbrief.analysis.sounding import analyze_sounding
from weatherbrief.analysis.sounding.advisories import compute_altitude_advisories
from weatherbrief.analysis.wind import compute_wind_components
from weatherbrief.fetch.open_meteo import OpenMeteoClient
from weatherbrief.fetch.route_points import interpolate_route
from weatherbrief.fetch.variables import MODEL_ENDPOINTS
from weatherbrief.models import (
    AltitudeAdvisories,
    ForecastSnapshot,
    HourlyForecast,
    ModelDivergence,
    ModelSource,
    RouteAnalysesManifest,
    RouteCrossSection,
    RouteConfig,
    RoutePoint,
    RoutePointAnalysis,
    SoundingAnalysis,
    WaypointAnalysis,
    WaypointForecast,
    WindComponent,
    altitude_to_pressure_hpa,
    bearing_between_coords,
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
    autorouter_credentials: tuple[str, str] | None = None  # (username, password)
    user_id: str | None = None  # for per-user token cache isolation


@dataclass
class BriefingUsage:
    """Tracks resource usage during a single briefing pipeline run."""

    open_meteo_calls: int = 0
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

    # --- Fetch forecasts (multi-point: 1 API call per model) ---
    _notify("route_interpolation")
    client = OpenMeteoClient()
    route_points = interpolate_route(route, spacing_nm=10.0)
    logger.info("Route interpolated: %d points along %.0f nm",
                len(route_points), route_points[-1].distance_from_origin_nm)

    # --- Elevation profile (high-res terrain along route) ---
    elevation_profile = None
    _notify("elevation_profile")
    try:
        from weatherbrief.fetch.elevation import get_elevation_profile

        elevation_profile = get_elevation_profile(route, spacing_nm=0.5)
        logger.info("Elevation profile: %d points, max %.0f ft",
                     len(elevation_profile.points), elevation_profile.max_elevation_ft)
    except Exception:
        logger.warning("Elevation profile failed", exc_info=True)

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
        except Exception:
            logger.warning("Failed to fetch %s", model.value, exc_info=True)

    # --- Analyze ---
    _notify("waypoint_analysis")
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

    # --- Route-point analyses (all ~20 points, pre-computed) ---
    route_analyses_manifest: RouteAnalysesManifest | None = None
    if cross_sections:
        _notify("route_analysis")
        try:
            model_names = [cs.model.value for cs in cross_sections]
            total_distance = route_points[-1].distance_from_origin_nm
            rp_analyses = analyze_all_route_points(
                cross_sections, route_points, target_dt,
                route.flight_duration_hours, route.cruise_altitude_ft,
                route.flight_ceiling_ft,
            )
            route_analyses_manifest = RouteAnalysesManifest(
                route_name=route.name,
                target_date=target_date,
                departure_time=target_dt,
                flight_duration_hours=route.flight_duration_hours,
                total_distance_nm=total_distance,
                cruise_altitude_ft=route.cruise_altitude_ft,
                models=model_names,
                analyses=rp_analyses,
            )
            logger.info("Route analyses: %d points", len(rp_analyses))
        except Exception:
            logger.warning("Route-point analysis failed", exc_info=True)

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
        if route_analyses_manifest:
            ra_path = options.output_dir / "route_analyses.json"
            ra_path.write_text(
                route_analyses_manifest.model_dump_json(
                    indent=2, exclude={"analyses": {"__all__": {"sounding": {"__all__": {"derived_levels"}}}}},
                )
            )
        if elevation_profile:
            ep_path = options.output_dir / "elevation_profile.json"
            ep_path.write_text(elevation_profile.model_dump_json(indent=2))
    else:
        snapshot_path = save_snapshot(snapshot, data_dir)
        if cross_sections:
            save_cross_section(snapshot, data_dir)
    _notify("save_snapshot")
    logger.info("Snapshot saved: %s", snapshot_path)

    result = BriefingResult(snapshot=snapshot, snapshot_path=snapshot_path)
    result.usage.open_meteo_calls = len(cross_sections)
    if elevation_profile and options.output_dir:
        result.elevation_profile_path = options.output_dir / "elevation_profile.json"

    # --- Optional: GRAMET ---
    if options.fetch_gramet:
        _notify("fetch_gramet")
        _run_gramet(route, target_date, target_hour, days_out, today, data_dir, result,
                    output_dir=options.output_dir,
                    autorouter_credentials=options.autorouter_credentials,
                    user_id=options.user_id)

    # --- Optional: Skew-T ---
    if options.generate_skewt:
        _notify("generate_skewt")
        _run_skewt(snapshot, target_dt, target_date, days_out, today, data_dir, result,
                   output_dir=options.output_dir)

    # --- Optional: LLM digest ---
    if options.generate_llm_digest:
        _notify("llm_digest")
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


def _run_point_analysis(
    forecasts_by_model: dict[str, HourlyForecast],
    track_deg: float,
    cruise_altitude_ft: int,
    flight_ceiling_ft: int,
) -> tuple[
    dict[str, WindComponent],
    dict[str, SoundingAnalysis],
    Optional[AltitudeAdvisories],
    list[ModelDivergence],
]:
    """Core analysis logic shared between waypoint and route-point paths.

    Args:
        forecasts_by_model: model_key -> HourlyForecast at the target time.
        track_deg: Route bearing at this point.
        cruise_altitude_ft: Cruise altitude for wind and advisory computation.
        flight_ceiling_ft: Flight ceiling for advisory computation.

    Returns:
        (wind_components, soundings, altitude_advisories, model_divergence)
    """
    wind_components: dict[str, WindComponent] = {}
    soundings: dict[str, SoundingAnalysis] = {}

    # Comparison accumulators
    comp: dict[str, dict[str, float]] = {
        "temperature_c": {}, "wind_speed_kt": {}, "wind_direction_deg": {},
        "cloud_cover_pct": {}, "precipitation_mm": {}, "freezing_level_m": {},
        "freezing_level_ft": {}, "cape_surface_jkg": {}, "lcl_altitude_ft": {},
        "k_index": {}, "total_totals": {}, "precipitable_water_mm": {},
        "lifted_index": {}, "bulk_shear_0_6km_kt": {}, "max_omega_pa_s": {},
    }

    target_pressure = altitude_to_pressure_hpa(cruise_altitude_ft)
    for model_key, hourly in forecasts_by_model.items():
        # Cruise-altitude wind (closest level to target pressure)
        cruise_wind = None
        for level in hourly.pressure_levels:
            if level.wind_speed_kt is not None and level.wind_direction_deg is not None:
                if cruise_wind is None or abs(level.pressure_hpa - target_pressure) < abs(
                    cruise_wind.pressure_hpa - target_pressure
                ):
                    cruise_wind = level

        if cruise_wind and cruise_wind.wind_speed_kt is not None:
            wc = compute_wind_components(
                cruise_wind.wind_speed_kt, cruise_wind.wind_direction_deg, track_deg
            )
            wind_components[model_key] = wc
            comp["wind_speed_kt"][model_key] = cruise_wind.wind_speed_kt
            comp["wind_direction_deg"][model_key] = cruise_wind.wind_direction_deg

        # Sounding analysis
        sounding = analyze_sounding(hourly.pressure_levels, hourly)
        if sounding is not None:
            soundings[model_key] = sounding

            idx = sounding.indices
            if idx is not None:
                _collect_opt(comp, "freezing_level_ft", model_key, idx.freezing_level_ft)
                _collect_opt(comp, "cape_surface_jkg", model_key, idx.cape_surface_jkg)
                _collect_opt(comp, "lcl_altitude_ft", model_key, idx.lcl_altitude_ft)
                _collect_opt(comp, "k_index", model_key, idx.k_index)
                _collect_opt(comp, "total_totals", model_key, idx.total_totals)
                _collect_opt(comp, "precipitable_water_mm", model_key, idx.precipitable_water_mm)
                _collect_opt(comp, "lifted_index", model_key, idx.lifted_index)
                _collect_opt(comp, "bulk_shear_0_6km_kt", model_key, idx.bulk_shear_0_6km_kt)

            vm = sounding.vertical_motion
            if vm is not None and vm.max_omega_pa_s is not None:
                comp["max_omega_pa_s"][model_key] = abs(vm.max_omega_pa_s)

        # Surface comparison values
        _collect_opt(comp, "temperature_c", model_key, hourly.temperature_2m_c)
        _collect_opt(comp, "cloud_cover_pct", model_key, hourly.cloud_cover_pct)
        _collect_opt(comp, "precipitation_mm", model_key, hourly.precipitation_mm)
        _collect_opt(comp, "freezing_level_m", model_key, hourly.freezing_level_m)

    # Altitude advisories
    altitude_advisories = None
    if soundings:
        altitude_advisories = compute_altitude_advisories(
            soundings, cruise_altitude_ft, flight_ceiling_ft
        )

    # Model comparison
    divergences: list[ModelDivergence] = []
    for var_name, values in comp.items():
        if len(values) >= 2:
            divergences.append(compare_models(var_name, values))

    return wind_components, soundings, altitude_advisories, divergences


def _collect_opt(
    comp: dict[str, dict[str, float]], key: str, model_key: str, value: float | None,
) -> None:
    """Add a non-None value to comparison accumulators."""
    if value is not None:
        comp[key][model_key] = value


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
    forecasts_by_model: dict[str, HourlyForecast] = {}
    for wf in forecasts:
        hourly = wf.at_time(target_time)
        if hourly:
            forecasts_by_model[wf.model.value] = hourly

    wind_components, soundings, alt_advisories, divergences = _run_point_analysis(
        forecasts_by_model, track_deg, cruise_altitude_ft, flight_ceiling_ft,
    )

    return WaypointAnalysis(
        waypoint=waypoint,
        target_time=target_time,
        wind_components=wind_components,
        sounding=soundings,
        altitude_advisories=alt_advisories,
        model_divergence=divergences,
    )


# --- Route-point analysis helpers ---


def compute_route_tracks(route_points: list[RoutePoint]) -> list[float]:
    """Compute bearing at each route point using neighbor points."""
    n = len(route_points)
    tracks: list[float] = []
    for i in range(n):
        if n == 1:
            tracks.append(0.0)
        elif i == 0:
            tracks.append(bearing_between_coords(
                route_points[0].lat, route_points[0].lon,
                route_points[1].lat, route_points[1].lon,
            ))
        elif i == n - 1:
            tracks.append(bearing_between_coords(
                route_points[-2].lat, route_points[-2].lon,
                route_points[-1].lat, route_points[-1].lon,
            ))
        else:
            # Circular mean of incoming and outgoing bearings
            b1 = bearing_between_coords(
                route_points[i - 1].lat, route_points[i - 1].lon,
                route_points[i].lat, route_points[i].lon,
            )
            b2 = bearing_between_coords(
                route_points[i].lat, route_points[i].lon,
                route_points[i + 1].lat, route_points[i + 1].lon,
            )
            x = math.cos(math.radians(b1)) + math.cos(math.radians(b2))
            y = math.sin(math.radians(b1)) + math.sin(math.radians(b2))
            tracks.append(math.degrees(math.atan2(y, x)) % 360)
    return tracks


def compute_interpolated_time(
    departure: datetime, duration_hours: float,
    distance_nm: float, total_distance_nm: float,
) -> datetime:
    """Compute the flight time at a given distance along the route."""
    if total_distance_nm <= 0 or duration_hours <= 0:
        return departure
    fraction = distance_nm / total_distance_nm
    return departure + timedelta(hours=fraction * duration_hours)


def analyze_all_route_points(
    cross_sections: list[RouteCrossSection],
    route_points: list[RoutePoint],
    departure_time: datetime,
    duration_hours: float,
    cruise_altitude_ft: int,
    flight_ceiling_ft: int,
) -> list[RoutePointAnalysis]:
    """Analyze all route points across all models.

    For each route point, gathers the forecast from each model's cross-section
    at the point's interpolated time, then runs the shared analysis.
    """
    if not cross_sections or not route_points:
        return []

    total_distance = route_points[-1].distance_from_origin_nm
    tracks = compute_route_tracks(route_points)
    analyses: list[RoutePointAnalysis] = []

    for i, rp in enumerate(route_points):
        interp_time = compute_interpolated_time(
            departure_time, duration_hours, rp.distance_from_origin_nm, total_distance,
        )

        # Gather closest forecast hour from each model
        forecasts_by_model: dict[str, HourlyForecast] = {}
        forecast_hour = interp_time  # will be updated per-model
        for cs in cross_sections:
            wf = cs.point_forecasts[i]
            hourly = wf.at_time(interp_time)
            if hourly:
                forecasts_by_model[cs.model.value] = hourly
                forecast_hour = hourly.time  # last model's actual hour (they should agree)

        if not forecasts_by_model:
            continue

        wind_components, soundings, alt_advisories, divergences = _run_point_analysis(
            forecasts_by_model, tracks[i], cruise_altitude_ft, flight_ceiling_ft,
        )

        analyses.append(RoutePointAnalysis(
            point_index=i,
            lat=rp.lat,
            lon=rp.lon,
            distance_from_origin_nm=rp.distance_from_origin_nm,
            waypoint_icao=rp.waypoint_icao,
            waypoint_name=rp.waypoint_name,
            interpolated_time=interp_time,
            forecast_hour=forecast_hour,
            track_deg=tracks[i],
            wind_components=wind_components,
            sounding=soundings,
            altitude_advisories=alt_advisories,
            model_divergence=divergences,
        ))

    return analyses


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
    autorouter_credentials: tuple[str, str] | None = None,
    user_id: str | None = None,
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

        kwargs: dict = {}
        if autorouter_credentials:
            kwargs["username"], kwargs["password"] = autorouter_credentials
            # Per-user token cache dir so users don't share cached OAuth tokens
            if user_id:
                kwargs["cache_dir"] = str(data_dir / ".cache" / "autorouter" / user_id)
        gramet_client = AutorouterGramet(**kwargs)
        data = gramet_client.fetch_gramet(
            icao_codes=icao_codes,
            altitude_ft=route.cruise_altitude_ft,
            departure_time=departure_time,
            duration_hours=duration_hours,
            fmt="pdf",
        )

        if output_dir:
            out_path = output_dir / "gramet.pdf"
        else:
            out_dir = data_dir / "gramet" / target_date / f"d-{days_out}_{fetch_date}"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / "gramet.pdf"
        out_path.write_bytes(data)
        result.gramet_path = out_path
        result.usage.gramet_fetched = True
        logger.info("GRAMET saved: %s", out_path)

    except ImportError:
        logger.warning("GRAMET fetch requires euro_aip with autorouter credentials")
        result.errors.append("GRAMET: euro_aip not available")
    except Exception as exc:
        logger.warning("GRAMET fetch failed: %s", exc, exc_info=True)
        result.errors.append(f"GRAMET: {exc}")
        result.usage.gramet_failed = True


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

        # Track LLM usage
        result.usage.llm_digest = True
        result.usage.llm_model = f"{config.llm.provider}:{config.llm.model}"
        result.usage.llm_input_tokens = digest_result.get("llm_input_tokens")
        result.usage.llm_output_tokens = digest_result.get("llm_output_tokens")

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
