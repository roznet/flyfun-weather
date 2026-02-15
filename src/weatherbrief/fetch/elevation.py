"""Terrain elevation profile along a route using SRTM data."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import srtm

from weatherbrief.models import ElevationPoint, ElevationProfile, RouteConfig
from weatherbrief.fetch.route_walk import walk_route

logger = logging.getLogger(__name__)

SRTM_CACHE_DIR = Path(os.environ.get("SRTM_CACHE_DIR", "data/.cache/srtm"))

_M_TO_FT = 3.28084


def get_elevation_profile(
    route: RouteConfig,
    spacing_nm: float = 0.5,
) -> ElevationProfile:
    """Generate a high-resolution elevation profile along a route.

    Uses SRTM3 (90m resolution) for terrain lookups, which is more than
    sufficient for route-scale terrain features.

    Args:
        route: Flight route definition.
        spacing_nm: Distance between elevation samples in nautical miles.

    Returns:
        ElevationProfile with terrain points along the route.
    """
    elevation_data = srtm.get_data(
        local_cache_dir=str(SRTM_CACHE_DIR),
        srtm1=False,
        srtm3=True,
    )

    points: list[ElevationPoint] = []
    for lat, lon, dist, _icao, _name in walk_route(route, spacing_nm):
        elev_m = elevation_data.get_elevation(lat, lon)
        elevation_ft = round(elev_m * _M_TO_FT) if elev_m is not None else 0
        points.append(ElevationPoint(
            distance_nm=round(dist, 2),
            elevation_ft=elevation_ft,
            lat=round(lat, 5),
            lon=round(lon, 5),
        ))

    if not points:
        return ElevationProfile(
            route_name=route.name,
            points=[],
            max_elevation_ft=0,
            total_distance_nm=0,
        )

    return ElevationProfile(
        route_name=route.name,
        points=points,
        max_elevation_ft=max(p.elevation_ft for p in points),
        total_distance_nm=points[-1].distance_nm,
    )
