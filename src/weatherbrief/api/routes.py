"""API endpoints for route management."""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/routes", tags=["routes"])

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config"


class RouteInfo(BaseModel):
    """Route summary for API responses."""

    name: str
    display_name: str
    waypoints: list[str]
    cruise_altitude_ft: int = 8000
    flight_duration_hours: float = 0.0


def _load_routes_yaml() -> dict:
    routes_file = CONFIG_DIR / "routes.yaml"
    if not routes_file.exists():
        return {}
    with open(routes_file) as f:
        data = yaml.safe_load(f)
    return data.get("routes", {})


@router.get("", response_model=list[RouteInfo])
def list_routes():
    """List all available named routes."""
    routes = _load_routes_yaml()
    result = []
    for key, r in routes.items():
        result.append(RouteInfo(
            name=key,
            display_name=r.get("name", key),
            waypoints=r.get("waypoints", []),
            cruise_altitude_ft=r.get("cruise_altitude_ft", 8000),
            flight_duration_hours=r.get("flight_duration_hours", 0.0),
        ))
    return result


@router.get("/{name}", response_model=RouteInfo)
def get_route(name: str):
    """Get details for a named route."""
    routes = _load_routes_yaml()
    if name not in routes:
        raise HTTPException(status_code=404, detail=f"Route '{name}' not found")
    r = routes[name]
    return RouteInfo(
        name=name,
        display_name=r.get("name", name),
        waypoints=r.get("waypoints", []),
        cruise_altitude_ft=r.get("cruise_altitude_ft", 8000),
        flight_duration_hours=r.get("flight_duration_hours", 0.0),
    )
