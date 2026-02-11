"""Route configuration loading from YAML."""

from __future__ import annotations

from pathlib import Path

import yaml

from weatherbrief.models import RouteConfig, Waypoint

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _parse_waypoint(data: dict) -> Waypoint:
    return Waypoint(
        icao=data["icao"],
        name=data["name"],
        lat=data["lat"],
        lon=data["lon"],
    )


def load_route(name: str, config_dir: Path | None = None) -> RouteConfig:
    """Load a named route from routes.yaml."""
    config_dir = config_dir or CONFIG_DIR
    routes_file = config_dir / "routes.yaml"

    with open(routes_file) as f:
        data = yaml.safe_load(f)

    routes = data.get("routes", {})
    if name not in routes:
        available = ", ".join(routes.keys())
        raise KeyError(f"Route '{name}' not found. Available: {available}")

    r = routes[name]
    midpoint = _parse_waypoint(r["midpoint"]) if "midpoint" in r else None

    return RouteConfig(
        name=r["name"],
        origin=_parse_waypoint(r["origin"]),
        midpoint=midpoint,
        destination=_parse_waypoint(r["destination"]),
        cruise_altitude_ft=r["cruise_altitude_ft"],
        cruise_pressure_hpa=r["cruise_pressure_hpa"],
        track_deg=r["track_deg"],
        estimated_eet_hours=r.get("estimated_eet_hours", 0.0),
    )


def list_routes(config_dir: Path | None = None) -> list[str]:
    """List available route names."""
    config_dir = config_dir or CONFIG_DIR
    routes_file = config_dir / "routes.yaml"

    with open(routes_file) as f:
        data = yaml.safe_load(f)

    return list(data.get("routes", {}).keys())
