"""CLI entry point — thin wrapper around the pipeline."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from weatherbrief.airports import resolve_waypoints
from weatherbrief.config import list_routes, load_route
from weatherbrief.models import ModelSource, RouteConfig
from weatherbrief.pipeline import BriefingOptions, execute_briefing

logger = logging.getLogger(__name__)


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
        # Override altitude/duration/ceiling from CLI if given
        overrides: dict = {}
        if args.alt != 8000:
            overrides["cruise_altitude_ft"] = args.alt
        if args.ceiling != 18000:
            overrides["flight_ceiling_ft"] = args.ceiling
        if args.duration and route.flight_duration_hours == 0.0:
            overrides["flight_duration_hours"] = args.duration
        if overrides:
            route = route.model_copy(update=overrides)
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
        flight_ceiling_ft=args.ceiling,
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
    """Full pipeline: fetch -> analyze -> snapshot -> digest.

    CLI wrapper that prints results to console.
    """
    options = BriefingOptions(
        models=models or BriefingOptions().models,
        fetch_gramet=fetch_gramet,
        generate_skewt=generate_skewt,
        generate_llm_digest=generate_llm_digest,
        digest_config_name=digest_config_name,
    )

    print(f"Route: {route.name}")
    print(f"Target: {target_date}")
    print(f"Models: {', '.join(m.value for m in options.models)}")
    print()

    try:
        result = execute_briefing(route, target_date, target_hour, options)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    print(f"\nSnapshot saved: {result.snapshot_path}")

    if result.gramet_path:
        print(f"  GRAMET saved: {result.gramet_path}")

    for p in result.skewt_paths:
        print(f"  Skew-T saved: {p}")

    if result.digest_path:
        print(f"  LLM digest saved: {result.digest_path}")

    if result.digest_text:
        print()
        print(result.digest_text)

    for err in result.errors:
        print(f"  Warning: {err}")

    # Print text digest
    if result.text_digest:
        print()
        print(result.text_digest)


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
        "--ceiling", type=int, default=18000, help="Flight ceiling in feet (default: 18000)"
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
