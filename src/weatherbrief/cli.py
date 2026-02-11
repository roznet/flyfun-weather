"""CLI entry point and pipeline orchestration."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone

from weatherbrief.analysis.clouds import estimate_cloud_layers
from weatherbrief.analysis.comparison import compare_models
from weatherbrief.analysis.icing import assess_icing_profile
from weatherbrief.analysis.wind import compute_wind_components
from weatherbrief.config import list_routes, load_route
from weatherbrief.digest.text import format_digest
from weatherbrief.fetch.open_meteo import OpenMeteoClient
from weatherbrief.models import (
    ForecastSnapshot,
    ModelSource,
    WaypointAnalysis,
    WaypointForecast,
)
from weatherbrief.storage.snapshots import save_snapshot

logger = logging.getLogger(__name__)

DEFAULT_MODELS = [ModelSource.GFS, ModelSource.ECMWF]


def run_fetch(
    route_name: str,
    target_date: str,
    target_hour: int = 9,
    models: list[ModelSource] | None = None,
) -> None:
    """Full pipeline: fetch → analyze → snapshot → digest."""
    models = models or DEFAULT_MODELS
    route = load_route(route_name)
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
        forecasts = client.fetch_all_models(waypoint, models)
        all_forecasts.extend(forecasts)
        print(f"  Fetched {len(forecasts)} models for {waypoint.icao}")

    # Run analysis at each waypoint
    analyses: list[WaypointAnalysis] = []

    for waypoint in route.waypoints:
        wp_forecasts = [f for f in all_forecasts if f.waypoint.icao == waypoint.icao]
        analysis = _analyze_waypoint(wp_forecasts, target_dt, route.track_deg)
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

    # Print digest
    print()
    digest = format_digest(snapshot, target_dt)
    print(digest)


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

        # Wind components at cruise level (find closest pressure level)
        for level in hourly.pressure_levels:
            if level.wind_speed_kt is not None and level.wind_direction_deg is not None:
                # Use the level with data; pick one representative level
                # For simplicity, use the first level with wind data (can refine later)
                pass

        # Try to find cruise-altitude wind
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
        "--route", required=True, help="Route name from routes.yaml"
    )
    fetch_parser.add_argument(
        "--date", required=True, help="Target date (YYYY-MM-DD)"
    )
    fetch_parser.add_argument(
        "--hour", type=int, default=9, help="Target hour UTC (default: 9)"
    )
    fetch_parser.add_argument(
        "--models",
        default="gfs,ecmwf",
        help="Comma-separated model list (default: gfs,ecmwf)",
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
        models = [ModelSource(m.strip()) for m in args.models.split(",")]
        run_fetch(args.route, args.date, args.hour, models)
