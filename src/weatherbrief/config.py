"""Route configuration loading from YAML."""

from __future__ import annotations

from pathlib import Path

import yaml

from weatherbrief.airports import resolve_waypoints
from weatherbrief.models import RouteConfig

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def load_route(name: str, db_path: str, config_dir: Path | None = None) -> RouteConfig:
    """Load a named route from routes.yaml, resolving ICAO codes via database.

    Args:
        name: Route key in routes.yaml.
        db_path: Path to the euro_aip airport database.
        config_dir: Override for config directory (testing).
    """
    config_dir = config_dir or CONFIG_DIR
    routes_file = config_dir / "routes.yaml"

    with open(routes_file) as f:
        data = yaml.safe_load(f)

    routes = data.get("routes", {})
    if name not in routes:
        available = ", ".join(routes.keys())
        raise KeyError(f"Route '{name}' not found. Available: {available}")

    r = routes[name]
    icao_codes = r["waypoints"]
    waypoints = resolve_waypoints(icao_codes, db_path)

    return RouteConfig(
        name=r["name"],
        waypoints=waypoints,
        cruise_altitude_ft=r.get("cruise_altitude_ft", 8000),
        flight_ceiling_ft=r.get("flight_ceiling_ft", 18000),
        flight_duration_hours=r.get("flight_duration_hours", 0.0),
    )


def list_routes(config_dir: Path | None = None) -> list[str]:
    """List available route names."""
    config_dir = config_dir or CONFIG_DIR
    routes_file = config_dir / "routes.yaml"

    with open(routes_file) as f:
        data = yaml.safe_load(f)

    return list(data.get("routes", {}).keys())
