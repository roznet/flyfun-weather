"""Terrain elevation profile along a route using SRTM data.

Falls back to Open-Meteo Elevation API (Copernicus DEM GLO-90) for
coordinates outside SRTM coverage (~56S–60N), e.g. high-latitude routes.
"""

from __future__ import annotations

import bisect
import logging
import os
from pathlib import Path

import httpx
import srtm

from weatherbrief.models import ElevationPoint, ElevationProfile, RouteConfig
from weatherbrief.models.diagnostic import Diagnostic
from weatherbrief.models.diagnostic_codes import FetchCode
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

    # Build final elevation points. A point neither source could resolve keeps
    # elevation_ft=None: recording it as 0 ft (sea level) is what let a void
    # on steep terrain defeat the terrain-feasibility guards built to survive
    # it (#807, review 2026-08 §2.7).
    points: list[ElevationPoint] = []
    for lat, lon, dist, elev_m in raw_points:
        elevation_ft = round(elev_m * _M_TO_FT) if elev_m is not None else None
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
            max_elevation_ft=None,
            total_distance_nm=0,
        )

    known = [p.elevation_ft for p in points if p.elevation_ft is not None]
    missing = len(points) - len(known)
    if missing:
        logger.warning(
            "Terrain elevation unknown for %d/%d points on route %s "
            "(SRTM void and Open-Meteo fallback failed)",
            missing, len(points), route.name,
        )

    return ElevationProfile(
        route_name=route.name,
        points=points,
        max_elevation_ft=max(known) if known else None,
        total_distance_nm=points[-1].distance_nm,
        missing_point_count=missing,
    )


def elevation_diagnostic(profile: ElevationProfile | None) -> Diagnostic | None:
    """User-facing diagnostic when the terrain profile has unknown samples.

    ``None`` when the profile is complete (or absent — an absent profile is
    already logged by the fetch stage). Terrain-dependent advisories treat the
    unknown stretches conservatively (no escape / no altitude recommendation
    over them), so the pilot should know why.
    """
    if profile is None or not profile.points or profile.missing_point_count == 0:
        return None
    total = len(profile.points)
    void_dists = [p.distance_nm for p in profile.points if p.elevation_ft is None]
    return Diagnostic.create(
        level="warn",
        stage="fetch",
        code=FetchCode.TERRAIN_ELEVATION_INCOMPLETE,
        message=(
            f"Terrain elevation unavailable for {profile.missing_point_count}/{total} "
            f"route samples ({min(void_dists):.0f}-{max(void_dists):.0f} nm) — "
            "terrain checks there are treated as unknown"
        ),
    )


def display_elevation_profile(profile: ElevationProfile) -> dict:
    """Wire projection of a profile for the cross-section clients.

    The web and iOS clients decode ``elevation_ft`` / ``max_elevation_ft`` as
    non-optional numbers, so unknown samples are filled **for drawing only**
    by linear interpolation between the nearest known neighbours (or the one
    known neighbour at an edge; 0 when nothing is known), and flagged with
    ``elevation_known: false`` plus a profile-level ``missing_point_count``.
    Analysis never reads this projection — it uses the stored profile, where
    the unknown samples stay ``None``.
    """
    data = profile.model_dump(mode="json")
    pts = data["points"]
    known_idx = [i for i, p in enumerate(pts) if p["elevation_ft"] is not None]
    for i, p in enumerate(pts):
        p["elevation_known"] = p["elevation_ft"] is not None
        if p["elevation_known"]:
            continue
        pos = bisect.bisect_left(known_idx, i)
        before = known_idx[:pos][-1:]
        after = known_idx[pos:pos + 1]
        if before and after:
            a, b = pts[before[-1]], pts[after[0]]
            span = b["distance_nm"] - a["distance_nm"]
            frac = (p["distance_nm"] - a["distance_nm"]) / span if span else 0.0
            p["elevation_ft"] = round(a["elevation_ft"] + frac * (b["elevation_ft"] - a["elevation_ft"]))
        elif before:
            p["elevation_ft"] = pts[before[-1]]["elevation_ft"]
        elif after:
            p["elevation_ft"] = pts[after[0]]["elevation_ft"]
        else:
            p["elevation_ft"] = 0
    if data.get("max_elevation_ft") is None:
        data["max_elevation_ft"] = max((p["elevation_ft"] for p in pts), default=0)
    return data
