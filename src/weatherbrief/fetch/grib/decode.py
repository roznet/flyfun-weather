"""GRIB2 decoding and spatial interpolation using cfgrib + xarray.

Decodes concatenated GRIB2 bytes into xarray Datasets, then interpolates
to specific route point coordinates.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Variable name mapping: GFS shortName → our field names
# GFS uses "clmr" (Cloud Liquid water Mixing Ratio), cfgrib may report
# either the shortName or parameterName depending on version.
_VAR_MAP = {
    "clmr": "cloud_liquid_water_kg_kg",
    "clwmr": "cloud_liquid_water_kg_kg",  # alias
    "icmr": "ice_mixing_ratio_kg_kg",
}


def decode_grib_to_points(
    grib_bytes: bytes,
    latitudes: list[float],
    longitudes: list[float],
) -> dict[int, dict[str, float]]:
    """Decode GRIB2 bytes and interpolate to specific lat/lon points.

    Args:
        grib_bytes: Concatenated GRIB2 messages (from fetch_byte_ranges).
        latitudes: Target latitudes for interpolation.
        longitudes: Target longitudes for interpolation.

    Returns:
        Nested dict: {pressure_hpa: {field_name: value, ...}} averaged
        across all target points. Individual point values are stored as
        lists if needed by the caller.
    """
    import cfgrib
    import numpy as np
    import xarray as xr

    if not grib_bytes:
        return {}

    # Write to temp file for cfgrib
    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
        tmp.write(grib_bytes)
        tmp_path = Path(tmp.name)

    try:
        datasets = cfgrib.open_datasets(str(tmp_path))

        result: dict[int, dict[str, float]] = {}

        # Normalize longitudes to 0–360 (GFS convention)
        target_lons = [(lon % 360) for lon in longitudes]

        for ds in datasets:
            for var_name, xr_var in ds.data_vars.items():
                field_name = _VAR_MAP.get(str(var_name).lower())
                if field_name is None:
                    continue

                # Determine pressure coordinate name
                pressure_coord = None
                for coord_name in ("isobaricInhPa", "level", "pressure"):
                    if coord_name in xr_var.dims:
                        pressure_coord = coord_name
                        break

                if pressure_coord is None:
                    # Single level — try to get pressure from attributes
                    level = xr_var.attrs.get("level")
                    if level is not None:
                        p_hpa = int(level)
                        val = _interpolate_to_points(
                            xr_var, latitudes, target_lons,
                        )
                        if val is not None:
                            result.setdefault(p_hpa, {})[field_name] = val
                    continue

                # Multiple pressure levels
                pressures = ds.coords[pressure_coord].values
                for p_val in pressures:
                    p_hpa = int(float(p_val))
                    level_data = xr_var.sel({pressure_coord: p_val})
                    val = _interpolate_to_points(
                        level_data, latitudes, target_lons,
                    )
                    if val is not None:
                        result.setdefault(p_hpa, {})[field_name] = val

        # Close datasets
        for ds in datasets:
            ds.close()

        return result
    except Exception:
        logger.warning("cfgrib failed to decode GRIB2 data", exc_info=True)
        return {}
    finally:
        tmp_path.unlink(missing_ok=True)


def _interpolate_to_points(
    data_array,
    latitudes: list[float],
    longitudes: list[float],
) -> float | None:
    """Bilinear interpolation of a 2D field to target points, returning mean.

    Args:
        data_array: xarray DataArray with latitude/longitude dims.
        latitudes: Target latitudes.
        longitudes: Target longitudes (already in 0-360 convention).

    Returns:
        Mean value across all interpolated points, or None on failure.
    """
    import numpy as np
    import xarray as xr

    try:
        # Determine lat/lon dimension names
        lat_dim = None
        lon_dim = None
        for dim in data_array.dims:
            dim_lower = str(dim).lower()
            if "lat" in dim_lower:
                lat_dim = dim
            elif "lon" in dim_lower:
                lon_dim = dim

        if lat_dim is None or lon_dim is None:
            return None

        lat_arr = xr.DataArray(latitudes, dims="points")
        lon_arr = xr.DataArray(longitudes, dims="points")

        interpolated = data_array.interp(
            {lat_dim: lat_arr, lon_dim: lon_arr},
            method="linear",
        )
        values = interpolated.values
        valid = values[~np.isnan(values)]
        if len(valid) == 0:
            return None
        return float(np.mean(valid))
    except Exception:
        logger.debug("Interpolation failed", exc_info=True)
        return None


def decode_grib_per_point(
    grib_bytes: bytes,
    latitudes: list[float],
    longitudes: list[float],
) -> list[dict[int, dict[str, float]]]:
    """Decode GRIB2 and interpolate to each route point individually.

    Returns:
        List of dicts (one per point): [{pressure_hpa: {field: value}}, ...].
    """
    import cfgrib
    import numpy as np
    import xarray as xr

    if not grib_bytes:
        return [{} for _ in latitudes]

    # Write to temp file for cfgrib
    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
        tmp.write(grib_bytes)
        tmp_path = Path(tmp.name)

    try:
        datasets = cfgrib.open_datasets(str(tmp_path))

        # Normalize longitudes to 0–360
        target_lons = [(lon % 360) for lon in longitudes]
        n_points = len(latitudes)
        results: list[dict[int, dict[str, float]]] = [{} for _ in range(n_points)]

        for ds in datasets:
            for var_name, xr_var in ds.data_vars.items():
                field_name = _VAR_MAP.get(str(var_name).lower())
                if field_name is None:
                    continue

                # Determine pressure coordinate
                pressure_coord = None
                for coord_name in ("isobaricInhPa", "level", "pressure"):
                    if coord_name in xr_var.dims:
                        pressure_coord = coord_name
                        break

                if pressure_coord is None:
                    level = xr_var.attrs.get("level")
                    if level is not None:
                        p_hpa = int(level)
                        values = _interpolate_per_point(
                            xr_var, latitudes, target_lons,
                        )
                        for i, val in enumerate(values):
                            if val is not None:
                                results[i].setdefault(p_hpa, {})[field_name] = val
                    continue

                pressures = ds.coords[pressure_coord].values
                for p_val in pressures:
                    p_hpa = int(float(p_val))
                    level_data = xr_var.sel({pressure_coord: p_val})
                    values = _interpolate_per_point(
                        level_data, latitudes, target_lons,
                    )
                    for i, val in enumerate(values):
                        if val is not None:
                            results[i].setdefault(p_hpa, {})[field_name] = val

        for ds in datasets:
            ds.close()

        return results
    except Exception:
        logger.warning("cfgrib failed to decode GRIB2 data", exc_info=True)
        return [{} for _ in latitudes]
    finally:
        tmp_path.unlink(missing_ok=True)


def _interpolate_per_point(
    data_array,
    latitudes: list[float],
    longitudes: list[float],
) -> list[float | None]:
    """Bilinear interpolation returning a value for each point."""
    import numpy as np
    import xarray as xr

    n = len(latitudes)
    try:
        lat_dim = lon_dim = None
        for dim in data_array.dims:
            dim_lower = str(dim).lower()
            if "lat" in dim_lower:
                lat_dim = dim
            elif "lon" in dim_lower:
                lon_dim = dim

        if lat_dim is None or lon_dim is None:
            return [None] * n

        lat_arr = xr.DataArray(latitudes, dims="points")
        lon_arr = xr.DataArray(longitudes, dims="points")

        interpolated = data_array.interp(
            {lat_dim: lat_arr, lon_dim: lon_arr},
            method="linear",
        )
        values = interpolated.values
        return [
            float(v) if not np.isnan(v) else None
            for v in values
        ]
    except Exception:
        logger.debug("Per-point interpolation failed", exc_info=True)
        return [None] * n
