"""CLI entry point and pipeline orchestration."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from weatherbrief.analysis.clouds import estimate_cloud_layers
from weatherbrief.analysis.comparison import compare_models
from weatherbrief.analysis.icing import assess_icing_profile
from weatherbrief.analysis.wind import compute_wind_components
from weatherbrief.airports import resolve_waypoints
from weatherbrief.config import list_routes, load_route
from weatherbrief.digest.text import format_digest
from weatherbrief.fetch.open_meteo import OpenMeteoClient
from weatherbrief.models import (
    ForecastSnapshot,
    ModelSource,
    RouteConfig,
    WaypointAnalysis,
    WaypointForecast,
)
from weatherbrief.storage.snapshots import DEFAULT_DATA_DIR, save_snapshot

logger = logging.getLogger(__name__)

DEFAULT_MODELS = [ModelSource.GFS, ModelSource.ECMWF, ModelSource.ICON]


def _resolve_db_path(args_db: str | None) -> str:
    """Resolve database path from CLI arg or environment variable."""
    db_path = args_db or os.environ.get("WEATHERBRIEF_DB")
    if not db_path:
        print("Error: --db PATH or WEATHERBRIEF_DB environment variable is required.")
        sys.exit(1)
    if not Path(db_path).exists():
        print(f"Error: Database not found: {db_path}")
        sys.exit(1)
    return db_path


def _build_route(args: argparse.Namespace) -> RouteConfig:
    """Build RouteConfig from CLI arguments (inline ICAOs or --route)."""
    db_path = _resolve_db_path(args.db)

    if args.route:
        route = load_route(args.route, db_path)
        # Override altitude/duration from CLI if given
        if args.alt != 8000:
            route = route.model_copy(update={"cruise_altitude_ft": args.alt})
        if args.duration and route.flight_duration_hours == 0.0:
            route = route.model_copy(update={"flight_duration_hours": args.duration})
        return route

    # Inline ICAO codes
    if len(args.waypoints) < 2:
        print("Error: At least 2 ICAO codes required (or use --route).")
        sys.exit(1)

    waypoints = resolve_waypoints(args.waypoints, db_path)
    name = " -> ".join(wp.icao for wp in waypoints)

    return RouteConfig(
        name=name,
        waypoints=waypoints,
        cruise_altitude_ft=args.alt,
        flight_duration_hours=args.duration or 0.0,
    )


def run_fetch(
    route: RouteConfig,
    target_date: str,
    target_hour: int = 9,
    models: list[ModelSource] | None = None,
    fetch_gramet: bool = False,
    generate_skewt: bool = False,
    generate_llm_digest: bool = False,
    digest_config_name: str | None = None,
) -> None:
    """Full pipeline: fetch -> analyze -> snapshot -> digest."""
    models = models or DEFAULT_MODELS
    today = date.today().isoformat()
    target_dt = datetime.fromisoformat(f"{target_date}T{target_hour:02d}:00:00")
    days_out = (date.fromisoformat(target_date) - date.today()).days

    if days_out < 0:
        print(f"Target date {target_date} is in the past.")
        sys.exit(1)

    print(f"Route: {route.name}")
    print(f"Target: {target_date} ({days_out} days out)")
    print(f"Models: {', '.join(m.value for m in models)}")
    print()

    # Fetch forecasts for all waypoints from all models
    client = OpenMeteoClient()
    all_forecasts: list[WaypointForecast] = []

    for waypoint in route.waypoints:
        forecasts = client.fetch_all_models(waypoint, models, days_out=days_out)
        all_forecasts.extend(forecasts)
        print(f"  Fetched {len(forecasts)} models for {waypoint.icao}")

    # Run analysis at each waypoint using per-waypoint track
    analyses: list[WaypointAnalysis] = []

    for waypoint in route.waypoints:
        wp_forecasts = [f for f in all_forecasts if f.waypoint.icao == waypoint.icao]
        track_deg = route.waypoint_track(waypoint.icao)
        analysis = _analyze_waypoint(wp_forecasts, target_dt, track_deg)
        analyses.append(analysis)

    # Build snapshot
    snapshot = ForecastSnapshot(
        route=route,
        target_date=target_date,
        fetch_date=today,
        days_out=days_out,
        forecasts=all_forecasts,
        analyses=analyses,
    )

    # Save
    path = save_snapshot(snapshot)
    print(f"\nSnapshot saved: {path}")

    output_paths: list[str] = [str(path)]

    # Optional: GRAMET
    if fetch_gramet:
        _run_gramet(route, target_date, target_hour, days_out, today, output_paths)

    # Optional: Skew-T
    if generate_skewt:
        _run_skewt(snapshot, target_dt, target_date, days_out, today, output_paths)

    # Optional: LLM digest
    if generate_llm_digest:
        _run_llm_digest(
            snapshot, target_dt, target_date, days_out, today, output_paths,
            digest_config_name,
        )

    # Print text digest
    print()
    digest = format_digest(snapshot, target_dt, output_paths=output_paths)
    print(digest)


def _run_gramet(
    route: RouteConfig,
    target_date: str,
    target_hour: int,
    days_out: int,
    fetch_date: str,
    output_paths: list[str],
) -> None:
    """Fetch GRAMET cross-section if available."""
    try:
        from weatherbrief.fetch.gramet import AutorouterGramet

        departure_time = datetime.fromisoformat(f"{target_date}T{target_hour:02d}:00:00")
        icao_codes = [wp.icao for wp in route.waypoints]
        duration_hours = route.flight_duration_hours or 2.0

        gramet_client = AutorouterGramet()
        data = gramet_client.fetch_gramet(
            icao_codes=icao_codes,
            altitude_ft=route.cruise_altitude_ft,
            departure_time=departure_time,
            duration_hours=duration_hours,
        )

        # Save to data/gramet/
        from weatherbrief.storage.snapshots import DEFAULT_DATA_DIR
        out_dir = DEFAULT_DATA_DIR / "gramet" / target_date / f"d-{days_out}_{fetch_date}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "gramet.png"
        out_path.write_bytes(data)
        print(f"  GRAMET saved: {out_path}")
        output_paths.append(str(out_path))

    except ImportError:
        logger.warning("GRAMET fetch requires euro_aip with autorouter credentials")
    except Exception:
        logger.warning("GRAMET fetch failed", exc_info=True)


def _run_skewt(
    snapshot: ForecastSnapshot,
    target_time: datetime,
    target_date: str,
    days_out: int,
    fetch_date: str,
    output_paths: list[str],
) -> None:
    """Generate Skew-T plots for all waypoints."""
    try:
        from weatherbrief.digest.skewt import generate_all_skewts
        from weatherbrief.storage.snapshots import DEFAULT_DATA_DIR

        out_dir = DEFAULT_DATA_DIR / "skewt" / target_date / f"d-{days_out}_{fetch_date}"
        paths = generate_all_skewts(snapshot, target_time, out_dir)
        for p in paths:
            print(f"  Skew-T saved: {p}")
            output_paths.append(str(p))

    except ImportError:
        logger.warning("Skew-T generation requires metpy, numpy, matplotlib")
    except Exception:
        logger.warning("Skew-T generation failed", exc_info=True)


def _run_llm_digest(
    snapshot: ForecastSnapshot,
    target_time: datetime,
    target_date: str,
    days_out: int,
    fetch_date: str,
    output_paths: list[str],
    digest_config_name: str | None,
) -> None:
    """Generate LLM-powered weather digest."""
    try:
        from weatherbrief.digest.llm_config import load_digest_config
        from weatherbrief.digest.llm_digest import run_digest

        config = load_digest_config(digest_config_name)
        print(f"\n  LLM digest: {config.llm.provider}/{config.llm.model}")

        result = run_digest(snapshot, target_time, config)

        if result.get("error"):
            print(f"  LLM digest failed: {result['error']}")
            return

        # Save markdown digest
        out_dir = DEFAULT_DATA_DIR / "digests" / target_date / f"d-{days_out}_{fetch_date}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "digest.md"
        out_path.write_text(result["digest_text"])
        print(f"  LLM digest saved: {out_path}")
        output_paths.append(str(out_path))

        # Print the digest
        print()
        print(result["digest_text"])

    except Exception:
        logger.warning("LLM digest generation failed", exc_info=True)


def _analyze_waypoint(
    forecasts: list[WaypointForecast],
    target_time: datetime,
    track_deg: float,
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

        # Icing profile
        icing = assess_icing_profile(hourly.pressure_levels)
        analysis.icing_bands[model_key] = icing

        # Cloud layers
        clouds = estimate_cloud_layers(hourly.pressure_levels)
        analysis.cloud_layers[model_key] = clouds

        # Collect comparison values
        if hourly.temperature_2m_c is not None:
            model_temps[model_key] = hourly.temperature_2m_c
        if hourly.cloud_cover_pct is not None:
            model_cloud[model_key] = hourly.cloud_cover_pct
        if hourly.precipitation_mm is not None:
            model_precip[model_key] = hourly.precipitation_mm
        if hourly.freezing_level_m is not None:
            model_freezing[model_key] = hourly.freezing_level_m

    # Model comparison (need at least 2 models)
    comparisons = {
        "temperature_c": model_temps,
        "wind_speed_kt": model_winds,
        "wind_direction_deg": model_wind_dirs,
        "cloud_cover_pct": model_cloud,
        "precipitation_mm": model_precip,
        "freezing_level_m": model_freezing,
    }

    for var_name, values in comparisons.items():
        if len(values) >= 2:
            analysis.model_divergence.append(compare_models(var_name, values))

    return analysis


def main() -> None:
    """CLI entry point."""
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="weatherbrief",
        description="Medium-range weather assessment for GA flights",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # fetch subcommand
    fetch_parser = subparsers.add_parser(
        "fetch", help="Fetch forecasts, analyze, and produce a digest"
    )
    fetch_parser.add_argument(
        "waypoints", nargs="*", default=[], metavar="ICAO",
        help="ICAO codes for inline route (min 2, e.g. EGTK LFPB LSGS)",
    )
    fetch_parser.add_argument(
        "--route", help="Named route from routes.yaml (alternative to inline ICAOs)"
    )
    fetch_parser.add_argument(
        "--db", help="Path to airport database (or set WEATHERBRIEF_DB env var)"
    )
    fetch_parser.add_argument(
        "--alt", type=int, default=8000, help="Cruise altitude in feet (default: 8000)"
    )
    fetch_parser.add_argument(
        "--date", required=True, help="Target date (YYYY-MM-DD)"
    )
    fetch_parser.add_argument(
        "--time", type=int, default=9, dest="hour",
        help="Target hour UTC (default: 9)",
    )
    fetch_parser.add_argument(
        "--duration", type=float, help="Flight duration in hours"
    )
    fetch_parser.add_argument(
        "--gramet", action="store_true", help="Also fetch Autorouter GRAMET"
    )
    fetch_parser.add_argument(
        "--skewt", action="store_true", help="Also generate Skew-T plots"
    )
    fetch_parser.add_argument(
        "--llm-digest", action="store_true",
        help="Generate LLM-powered weather digest",
    )
    fetch_parser.add_argument(
        "--digest-config", default=None,
        help="Digest config name (default: env WEATHERBRIEF_DIGEST_CONFIG or 'default')",
    )
    fetch_parser.add_argument(
        "--models",
        default="gfs,ecmwf,icon",
        help="Comma-separated model list (default: gfs,ecmwf,icon)",
    )

    # routes subcommand
    subparsers.add_parser("routes", help="List available routes")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.command == "routes":
        for name in list_routes():
            print(f"  {name}")
    elif args.command == "fetch":
        if not args.waypoints and not args.route:
            print("Error: Provide ICAO codes or --route NAME.")
            sys.exit(1)

        route = _build_route(args)
        models = [ModelSource(m.strip()) for m in args.models.split(",")]
        run_fetch(
            route=route,
            target_date=args.date,
            target_hour=args.hour,
            models=models,
            fetch_gramet=args.gramet,
            generate_skewt=args.skewt,
            generate_llm_digest=args.llm_digest,
            digest_config_name=args.digest_config,
        )
