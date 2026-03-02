"""Terrain elevation profile along a route using SRTM data.

Falls back to Open-Meteo Elevation API (Copernicus DEM GLO-90) for
coordinates outside SRTM coverage (~56S–60N), e.g. high-latitude routes.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
import srtm

from weatherbrief.models import ElevationPoint, ElevationProfile, RouteConfig
from weatherbrief.fetch.route_walk import walk_route

logger = logging.getLogger(__name__)

SRTM_CACHE_DIR = Path(os.environ.get("SRTM_CACHE_DIR", "data/.cache/srtm"))

_M_TO_FT = 3.28084
_OPEN_METEO_BATCH_SIZE = 100
_OPEN_METEO_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"


def _fetch_elevations_open_meteo(
    coords: list[tuple[float, float]],
) -> list[float | None]:
    """Batch-query Open-Meteo Elevation API for a list of (lat, lon) pairs.

    Returns elevation in metres for each coordinate, or None on failure.
    Batches into groups of 100 (API limit).
    """
    results: list[float | None] = []

    for start in range(0, len(coords), _OPEN_METEO_BATCH_SIZE):
        batch = coords[start : start + _OPEN_METEO_BATCH_SIZE]
        lats = ",".join(f"{lat:.5f}" for lat, _ in batch)
        lons = ",".join(f"{lon:.5f}" for _, lon in batch)

        try:
            resp = httpx.get(
                _OPEN_METEO_ELEVATION_URL,
                params={"latitude": lats, "longitude": lons},
                timeout=10.0,
            )
            resp.raise_for_status()
            elevations = resp.json().get("elevation", [])
            for elev in elevations:
                results.append(float(elev) if elev is not None else None)
        except Exception:
            logger.warning(
                "Open-Meteo elevation request failed for %d coords (batch at %d)",
                len(batch),
                start,
                exc_info=True,
            )
            results.extend([None] * len(batch))

    return results


def get_elevation_profile(
    route: RouteConfig,
    spacing_nm: float = 0.5,
) -> ElevationProfile:
    """Generate a high-resolution elevation profile along a route.

    Uses SRTM3 (90m resolution) as primary source, falling back to Open-Meteo
    Elevation API (Copernicus DEM GLO-90) for points outside SRTM coverage.

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

    # Pass 1: collect points from SRTM, track indices where it returns None
    raw_points: list[tuple[float, float, float, float | None]] = []  # lat, lon, dist, elev_m
    missing_indices: list[int] = []

    for lat, lon, dist, _icao, _name in walk_route(route, spacing_nm):
        elev_m = elevation_data.get_elevation(lat, lon)
        idx = len(raw_points)
        raw_points.append((lat, lon, dist, elev_m))
        if elev_m is None:
            missing_indices.append(idx)

    # Pass 2: fill gaps via Open-Meteo
    if missing_indices:
        logger.info(
            "SRTM returned None for %d/%d points on route %s, querying Open-Meteo fallback",
            len(missing_indices),
            len(raw_points),
            route.name,
        )
        missing_coords = [(raw_points[i][0], raw_points[i][1]) for i in missing_indices]
        fallback_elevs = _fetch_elevations_open_meteo(missing_coords)
        for i, elev_m in zip(missing_indices, fallback_elevs):
            if elev_m is not None:
                lat, lon, dist, _ = raw_points[i]
                raw_points[i] = (lat, lon, dist, elev_m)

    # Build final elevation points
    points: list[ElevationPoint] = []
    for lat, lon, dist, elev_m in raw_points:
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
