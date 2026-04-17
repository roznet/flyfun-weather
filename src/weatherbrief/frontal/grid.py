"""Grid definition, Open-Meteo fetch, field preparation, and terrain masking.

Defines the 0.5° European grid for frontal detection, fetches 850hPa fields
via the existing OpenMeteoClient HTTP infrastructure, and prepares raw data
into NaN-free 2D numpy arrays ready for gradient computation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
from scipy.interpolate import griddata

if TYPE_CHECKING:
    from weatherbrief.fetch.open_meteo import OpenMeteoClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Grid constants
# ---------------------------------------------------------------------------

FRONTAL_GRID = {
    "lat_min": 35.0,
    "lat_max": 60.0,
    "lon_min": -20.0,
    "lon_max": 28.0,
    "resolution": 0.5,
}

# 850hPa variables — the only 4 we need for frontal detection
_GRID_VARIABLES = (
    "temperature_850hPa,"
    "dewpoint_850hPa,"
    "wind_speed_850hPa,"
    "wind_direction_850hPa"
)

# Chunk size for grid fetch. The hourly param is short (~90 chars) but each
# coordinate pair takes ~12 chars plus URL-encoding overhead (%2C = 3 chars).
# At 500 points: ~500 × 15 ≈ 7.5KB per axis, ~15KB total — safe under limits.
_GRID_CHUNK_SIZE = 500

# Knots to km/h for advection calculations
KT_TO_KMH = 1.852

# 850hPa ≈ 1500m in standard atmosphere
_TERRAIN_MASK_THRESHOLD_M = 1500


def build_grid_coords() -> tuple[np.ndarray, np.ndarray]:
    """Return 1D lat and lon arrays for the frontal analysis grid.

    lat: 35.0, 35.5, ..., 60.0  (51 points)
    lon: -12.0, -11.5, ..., 28.0 (81 points)
    """
    g = FRONTAL_GRID
    lat = np.arange(g["lat_min"], g["lat_max"] + g["resolution"] / 2, g["resolution"])
    lon = np.arange(g["lon_min"], g["lon_max"] + g["resolution"] / 2, g["resolution"])
    return lat, lon


def build_grid_points(
    lat: np.ndarray, lon: np.ndarray,
) -> list[tuple[float, float]]:
    """Flat list of (lat, lon) in lat-major order for API requests."""
    points = []
    for la in lat:
        for lo in lon:
            points.append((round(float(la), 2), round(float(lo), 2)))
    return points


# ---------------------------------------------------------------------------
# Wind conversion
# ---------------------------------------------------------------------------


def wind_to_uv(
    speed_kt: np.ndarray, direction_deg: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Convert wind speed (knots) + direction (meteorological) to u/v in km/h.

    Meteorological convention: direction is where wind comes FROM.
    u positive = westerly (from west), v positive = southerly (from south).
    """
    speed_kmh = speed_kt * KT_TO_KMH
    direction_rad = np.radians(direction_deg)
    u = -speed_kmh * np.sin(direction_rad)
    v = -speed_kmh * np.cos(direction_rad)
    return u, v


# ---------------------------------------------------------------------------
# NaN handling
# ---------------------------------------------------------------------------


def prepare_field(
    raw_field: np.ndarray, max_nan_fraction: float = 0.05,
) -> np.ndarray | None:
    """Resolve missing-data NaN before any computation.

    Returns a clean field with no NaN, or None if too much is missing.
    Uses nearest-neighbor fill — the most conservative interpolation
    for gradient analysis (cannot invent gradients that don't exist).
    """
    nan_mask = np.isnan(raw_field)
    nan_frac = nan_mask.sum() / raw_field.size

    if nan_frac >= max_nan_fraction:
        return None

    if nan_frac == 0:
        return raw_field

    valid = ~nan_mask
    coords_valid = np.argwhere(valid)
    coords_nan = np.argwhere(nan_mask)
    filled = raw_field.copy()
    filled[nan_mask] = griddata(
        coords_valid, raw_field[valid], coords_nan, method="nearest",
    )
    return filled


# ---------------------------------------------------------------------------
# Terrain masking
# ---------------------------------------------------------------------------


def build_terrain_mask(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Boolean mask (True = valid, False = terrain above 850hPa).

    Uses SRTM3 (90m) for elevation lookups. Points above 1500m are
    masked so orographic gradients don't generate false frontal detections.
    Static for a given grid — compute once and reuse.
    """
    import srtm

    from weatherbrief.fetch.elevation import SRTM_CACHE_DIR

    elevation_data = srtm.get_data(
        local_cache_dir=str(SRTM_CACHE_DIR), srtm3=True,
    )
    mask = np.ones((len(lat), len(lon)), dtype=bool)

    for i, la in enumerate(lat):
        for j, lo in enumerate(lon):
            elev = elevation_data.get_elevation(float(la), float(lo))
            if elev is not None and elev > _TERRAIN_MASK_THRESHOLD_M:
                mask[i, j] = False

    n_masked = int((~mask).sum())
    logger.info(
        "Terrain mask: %d of %d points above %dm",
        n_masked, mask.size, _TERRAIN_MASK_THRESHOLD_M,
    )
    return mask


def fill_terrain(field: np.ndarray, terrain_mask: np.ndarray) -> np.ndarray:
    """Replace terrain-masked cells with values interpolated from valid neighbors.

    Linear interpolation creates smooth transitions so gradients at
    terrain-adjacent valid cells reflect the large-scale field, not
    below-ground extrapolation artifacts. Filled values never appear
    in results — the terrain mask is applied to the frontal_mask at the end.
    """
    valid = terrain_mask  # True = below 1500m
    if valid.all():
        return field

    coords_valid = np.argwhere(valid)
    coords_invalid = np.argwhere(~valid)
    filled = field.copy()
    filled[~valid] = griddata(
        coords_valid, field[valid], coords_invalid, method="linear",
    )
    # Fall back to nearest for points outside convex hull (domain edges)
    still_nan = np.isnan(filled)
    if still_nan.any():
        filled[still_nan] = griddata(
            coords_valid, field[valid], np.argwhere(still_nan), method="nearest",
        )
    return filled


# ---------------------------------------------------------------------------
# θe computation
# ---------------------------------------------------------------------------


def compute_theta_e(
    T850: np.ndarray, Td850: np.ndarray, pressure_hPa: float = 850.0,
) -> np.ndarray:
    """Equivalent potential temperature from T and Td at 850hPa.

    Uses MetPy (already a project dependency). Returns θe in Kelvin.
    """
    import metpy.calc as mpcalc
    from metpy.units import units

    theta_e = mpcalc.equivalent_potential_temperature(
        pressure_hPa * units.hPa,
        T850 * units.degC,
        Td850 * units.degC,
    )
    return theta_e.to("kelvin").magnitude


# ---------------------------------------------------------------------------
# Open-Meteo grid fetch
# ---------------------------------------------------------------------------


def fetch_grid_fields(
    client: OpenMeteoClient,
    model_key: str,
    lat: np.ndarray,
    lon: np.ndarray,
) -> tuple[dict[str, list], list[str], int]:
    """Fetch 850hPa fields for the full frontal grid from Open-Meteo.

    Builds URLs and chunks requests using the client's HTTP infrastructure.
    Returns (raw_data, timestamps, init_time_unix):
        raw_data: {variable_name: [[hourly values per point], ...]}
        timestamps: list of ISO timestamp strings
        init_time_unix: model initialization time (unix seconds)
    """
    from weatherbrief.fetch.model_status import fetch_model_metadata
    from weatherbrief.fetch.variables import MODEL_ENDPOINTS

    endpoint = MODEL_ENDPOINTS[model_key]
    points = build_grid_points(lat, lon)

    # Record model init time before fetch
    meta = fetch_model_metadata(models=[model_key])
    init_time = meta[model_key].last_init_time if model_key in meta else 0

    # Fetch in chunks
    all_responses: list[dict] = []
    timestamps: list[str] | None = None

    for chunk_start in range(0, len(points), _GRID_CHUNK_SIZE):
        chunk = points[chunk_start : chunk_start + _GRID_CHUNK_SIZE]
        chunk_idx = chunk_start // _GRID_CHUNK_SIZE + 1
        total_chunks = (len(points) + _GRID_CHUNK_SIZE - 1) // _GRID_CHUNK_SIZE

        params: dict[str, object] = {
            "latitude": ",".join(str(p[0]) for p in chunk),
            "longitude": ",".join(str(p[1]) for p in chunk),
            "hourly": _GRID_VARIABLES,
            "wind_speed_unit": "kn",
            "timezone": "UTC",
            "forecast_days": min(endpoint.max_days, 4),  # ~72h + margin
        }
        if endpoint.model_param:
            params["models"] = endpoint.model_param

        logger.info(
            "Fetching %s chunk %d/%d (%d points)",
            model_key, chunk_idx, total_chunks, len(chunk),
        )
        response_json = client.get_json(endpoint.base_url, params)

        # Single-point returns dict; multi-point returns list
        if isinstance(response_json, dict):
            response_json = [response_json]

        # Capture timestamps from first chunk
        if timestamps is None and response_json:
            timestamps = response_json[0].get("hourly", {}).get("time", [])

        all_responses.extend(response_json)

    # Verify init time didn't change mid-fetch
    meta_after = fetch_model_metadata(models=[model_key])
    init_after = meta_after[model_key].last_init_time if model_key in meta_after else 0
    if init_after != init_time and init_time != 0:
        logger.warning(
            "%s model init changed during fetch (%d -> %d), data may be mixed",
            model_key, init_time, init_after,
        )

    # Reshape: collect all hourly arrays per variable
    variables = [v.strip() for v in _GRID_VARIABLES.split(",")]
    raw_data: dict[str, list] = {v: [] for v in variables}

    for point_data in all_responses:
        hourly = point_data.get("hourly", {})
        for var in variables:
            raw_data[var].append(hourly.get(var, []))

    return raw_data, timestamps or [], init_time


# ---------------------------------------------------------------------------
# Reshape to 2D fields
# ---------------------------------------------------------------------------


def reshape_to_fields(
    raw: dict[str, list],
    lat: np.ndarray,
    lon: np.ndarray,
    hour_index: int,
    terrain_mask: np.ndarray | None = None,
) -> dict | None:
    """Extract one forecast hour from raw response, reshape to 2D arrays.

    Returns dict with T850, Td850, theta_e, u850, v850 as (n_lat, n_lon)
    arrays, or None if too much data is missing.
    """
    n_lat, n_lon = len(lat), len(lon)

    def _extract(var_name: str) -> np.ndarray:
        values = []
        for point_series in raw[var_name]:
            if hour_index < len(point_series) and point_series[hour_index] is not None:
                values.append(float(point_series[hour_index]))
            else:
                values.append(np.nan)
        return np.array(values).reshape(n_lat, n_lon)

    T850_raw = _extract("temperature_850hPa")
    Td850_raw = _extract("dewpoint_850hPa")
    ws_raw = _extract("wind_speed_850hPa")
    wd_raw = _extract("wind_direction_850hPa")

    # Prepare fields — resolve NaN before any computation
    T850 = prepare_field(T850_raw)
    Td850 = prepare_field(Td850_raw)

    if any(f is None for f in (T850, Td850)):
        logger.warning("Hour index %d: too much missing data, skipping", hour_index)
        return None

    # Convert wind to u/v BEFORE NaN fill — direction has circular
    # wraparound (359° ≈ 1°) so nearest-neighbor on raw direction can
    # introduce large jumps. Filling on Cartesian u/v is safe.
    u850_raw, v850_raw = wind_to_uv(ws_raw, wd_raw)
    u850 = prepare_field(u850_raw)
    v850 = prepare_field(v850_raw)

    if any(f is None for f in (u850, v850)):
        logger.warning("Hour index %d: too much missing wind data, skipping", hour_index)
        return None

    # Fill terrain before θe computation (same field goes into detection)
    if terrain_mask is not None:
        T850 = fill_terrain(T850, terrain_mask)
        Td850 = fill_terrain(Td850, terrain_mask)
        u850 = fill_terrain(u850, terrain_mask)
        v850 = fill_terrain(v850, terrain_mask)

    theta_e = compute_theta_e(T850, Td850)

    return {
        "T850": T850,
        "Td850": Td850,
        "theta_e": theta_e,
        "u850": u850,
        "v850": v850,
    }
