"""GRIB2 decoding and spatial interpolation using cfgrib + xarray.

Decodes concatenated GRIB2 bytes into xarray Datasets, then interpolates
to specific route point coordinates.

Handles two categories of GFS variables:
1. Pressure-level variables (CLWMR/ICMR) — one value per pressure level per point
2. Cloud diagnostic variables (LCDC, PRES cloud layers, etc.) — one scalar per point
"""

from __future__ import annotations

import logging
import tempfile
import warnings
from pathlib import Path

# Silence xarray FutureWarning about combine compat default change (cfgrib trigger)
warnings.filterwarnings(
    "ignore",
    message="In a future version of xarray the default value for compat",
    category=FutureWarning,
)

logger = logging.getLogger(__name__)

# Variable name mapping: GFS shortName → our field names
# GFS uses "clmr" (Cloud Liquid water Mixing Ratio), cfgrib may report
# either the shortName or parameterName depending on version.
_VAR_MAP = {
    "clmr": "cloud_liquid_water_kg_kg",
    "clwmr": "cloud_liquid_water_kg_kg",  # alias
    "icmr": "ice_mixing_ratio_kg_kg",
    "qc": "cloud_liquid_water_kg_kg",     # ICON-EU cloud liquid water
    "qi": "ice_mixing_ratio_kg_kg",       # ICON-EU cloud ice
    "clc": "cloud_area_fraction_pct",     # ICON-EU cloud area fraction (0–100%)
    "clwc": "cloud_liquid_water_kg_kg",   # ECMWF IFS cloud liquid water content
    "ciwc": "ice_mixing_ratio_kg_kg",     # ECMWF IFS cloud ice water content
    "cc": "cloud_area_fraction_pct",      # ECMWF IFS cloud cover (0–1 fraction, ×100 in decode)
}

# Extended variable map for ECMWF full sounding replacement (Phase 3).
# raw_ prefix indicates unit conversion is needed before building PressureLevelData.
_ECMWF_FULL_VAR_MAP = {
    **_VAR_MAP,
    "t": "raw_temperature_k",
    "r": "raw_relative_humidity_pct",
    "q": "raw_specific_humidity_kg_kg",
    "u": "raw_u_wind_m_s",
    "v": "raw_v_wind_m_s",
    "z": "raw_geopotential_m2_s2",
    "w": "vertical_velocity_pa_s",
}

# Extended variable map for ICON-EU full sounding replacement.
# ICON uses "qv" for specific humidity (vs ECMWF's "q").
# "fi" (geopotential) is NOT available on model levels from DWD — only pressure levels.
_ICON_FULL_VAR_MAP = {
    **_VAR_MAP,
    "t": "raw_temperature_k",
    "qv": "raw_specific_humidity_kg_kg",
    "u": "raw_u_wind_m_s",
    "v": "raw_v_wind_m_s",
    "w": "raw_w_m_s",  # Physical vertical velocity (m/s, upward positive)
}

# Cloud diagnostic field mapping: (cfgrib_shortName, cfgrib_typeOfLevel) → field_name
# cfgrib uses avg_ prefix for time-averaged stepType variables.
# Field names use a flat namespace for build_cloud_diagnostics().
_CLOUD_DIAG_FIELD_MAP: dict[tuple[str, str], str] = {
    # Cloud cover percentages (instant)
    ("lcc", "lowCloudLayer"): "low_cover_pct",
    ("mcc", "middleCloudLayer"): "mid_cover_pct",
    ("hcc", "highCloudLayer"): "high_cover_pct",
    ("tcc", "atmosphere"): "total_cover_pct",
    ("tcc", "convectiveCloudLayer"): "convective_cover_pct",
    ("tcc", "boundaryLayerCloudLayer"): "boundary_cover_pct",
    # Cloud cover percentages (time-averaged fallbacks)
    ("avg_lcc", "lowCloudLayer"): "low_cover_pct",
    ("avg_mcc", "middleCloudLayer"): "mid_cover_pct",
    ("avg_hcc", "highCloudLayer"): "high_cover_pct",
    ("avg_tcc", "atmosphere"): "total_cover_pct",
    ("avg_tcc", "convectiveCloudLayer"): "convective_cover_pct",
    ("avg_tcc", "boundaryLayerCloudLayer"): "boundary_cover_pct",
    # Cloud boundary pressures (Pa) — mostly time-averaged in GFS
    ("pres", "convectiveCloudBottom"): "convective_base_pa",
    ("pres", "convectiveCloudTop"): "convective_top_pa",
    ("avg_pres", "lowCloudBottom"): "low_base_pa",
    ("avg_pres", "lowCloudTop"): "low_top_pa",
    ("avg_pres", "middleCloudBottom"): "mid_base_pa",
    ("avg_pres", "middleCloudTop"): "mid_top_pa",
    ("avg_pres", "highCloudBottom"): "high_base_pa",
    ("avg_pres", "highCloudTop"): "high_top_pa",
    # Instantaneous pressure fallbacks
    ("pres", "lowCloudBottom"): "low_base_pa",
    ("pres", "lowCloudTop"): "low_top_pa",
    ("pres", "middleCloudBottom"): "mid_base_pa",
    ("pres", "middleCloudTop"): "mid_top_pa",
    ("pres", "highCloudBottom"): "high_base_pa",
    ("pres", "highCloudTop"): "high_top_pa",
    # Cloud top temperatures (K) — time-averaged
    ("avg_t", "lowCloudTop"): "low_top_temp_k",
    ("avg_t", "middleCloudTop"): "mid_top_temp_k",
    ("avg_t", "highCloudTop"): "high_top_temp_k",
    # Instantaneous temperature fallbacks
    ("t", "lowCloudTop"): "low_top_temp_k",
    ("t", "middleCloudTop"): "mid_top_temp_k",
    ("t", "highCloudTop"): "high_top_temp_k",
    # Cloud ceiling height (gpm)
    ("gh", "cloudCeiling"): "ceiling_gpm",
}

# ICON-EU single-level cloud diagnostic field mapping.
# ICON reports heights in meters (not gpm like GFS).
# cfgrib shortName → internal field name
_ICON_CLOUD_DIAG_FIELD_MAP: dict[str, str] = {
    "ceiling": "ceiling_m",
    "ceil": "ceiling_m",       # cfgrib shortName alias
    "hbas_con": "convective_cloud_base_m",
    "htop_con": "convective_cloud_top_m",
    "clcl": "low_cover_pct",
    "clcm": "mid_cover_pct",
    "clch": "high_cover_pct",
    "clct": "total_cover_pct",
}

# ECMWF single-level cloud diagnostic field mapping.
# ECMWF reports heights in meters, cloud covers as 0–1 fractions.
# cfgrib shortName → internal field name
_ECMWF_CLOUD_DIAG_FIELD_MAP: dict[str, str] = {
    "ceil": "ceiling_m",             # meters, 9999 = no cloud sentinel
    "cbh": "cloud_base_height_m",    # meters, 9999 = no cloud sentinel
    "lcc": "low_cover_frac",         # 0–1 fraction, ×100 during build
    "mcc": "mid_cover_frac",         # 0–1 fraction, ×100 during build
    "tcc": "total_cover_frac",       # 0–1 fraction, ×100 during build
    "hcc": "high_cover_frac",       # 0–1 fraction, ×100 during build
    # Note: deg0l (freezing level) is available but NWPCloudDiagnostics has
    # no freezing_level_ft field. Add when the model is extended.
}

# Variables that are 0–1 fractions in ECMWF GRIB and need ×100 to become %.
_ECMWF_FRAC_TO_PCT = {"cc"}


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


def _decode_pressure_vars_from_datasets(
    datasets: list,
    latitudes: list[float],
    longitudes: list[float],
    *,
    var_map: dict[str, str] | None = None,
    frac_vars: set[str] | None = None,
    first_wins: bool = False,
) -> tuple[list[dict[int, dict[str, float]]], list[bool]]:
    """Shared decode loop for pressure-level GRIB datasets.

    Iterates datasets, maps variable names via var_map, finds the pressure
    coordinate, interpolates per point, and stores results.

    Args:
        datasets: cfgrib-opened xarray Datasets.
        latitudes: Route-point latitudes.
        longitudes: Route-point longitudes (already in the grid's convention).
        var_map: GRIB shortName → field name mapping. Defaults to _VAR_MAP.
        frac_vars: Variable names (lowercase) that are 0–1 fractions needing ×100.
        first_wins: If True, skip a field at a level if already set (multi-grid).

    Returns:
        Tuple of (per-point results, coverage mask).
    """
    active_map = var_map if var_map is not None else _VAR_MAP
    frac_vars = frac_vars or set()
    n_points = len(latitudes)
    results: list[dict[int, dict[str, float]]] = [{} for _ in range(n_points)]
    covered: list[bool] = [False] * n_points

    for ds in datasets:
        for var_name, xr_var in ds.data_vars.items():
            var_lower = str(var_name).lower()
            field_name = active_map.get(var_lower)
            if field_name is None:
                continue

            is_frac = var_lower in frac_vars

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
                    values = _interpolate_per_point(xr_var, latitudes, longitudes)
                    for i, val in enumerate(values):
                        if val is not None:
                            if first_wins and field_name in results[i].get(p_hpa, {}):
                                continue
                            if is_frac:
                                val *= 100.0
                            results[i].setdefault(p_hpa, {})[field_name] = val
                            covered[i] = True
                continue

            pressures = ds.coords[pressure_coord].values
            for p_val in pressures:
                p_hpa = int(float(p_val))
                level_data = xr_var.sel({pressure_coord: p_val})
                values = _interpolate_per_point(level_data, latitudes, longitudes)
                for i, val in enumerate(values):
                    if val is not None:
                        if first_wins and field_name in results[i].get(p_hpa, {}):
                            continue
                        if is_frac:
                            val *= 100.0
                        results[i].setdefault(p_hpa, {})[field_name] = val
                        covered[i] = True

    return results, covered


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

    if not grib_bytes:
        return [{} for _ in latitudes]

    # Write to temp file for cfgrib
    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
        tmp.write(grib_bytes)
        tmp_path = Path(tmp.name)

    try:
        datasets = cfgrib.open_datasets(str(tmp_path))

        # Normalize longitudes to 0–360 (GFS convention)
        target_lons = [(lon % 360) for lon in longitudes]

        results, _ = _decode_pressure_vars_from_datasets(
            datasets, latitudes, target_lons,
        )

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


def decode_cloud_diag_per_point(
    grib_bytes: bytes,
    latitudes: list[float],
    longitudes: list[float],
) -> list[dict[str, float]]:
    """Decode cloud diagnostic GRIB2 and interpolate to each route point.

    These are surface-type scalar variables (no pressure dimension).
    Uses _CLOUD_DIAG_FIELD_MAP to identify variables by (shortName, typeOfLevel).

    Returns:
        List of flat dicts (one per point): [{field_name: raw_value, ...}, ...].
        Raw values are in native units (Pa, K, gpm, %).
    """
    import cfgrib
    import numpy as np

    n_points = len(latitudes)
    if not grib_bytes:
        return [{} for _ in range(n_points)]

    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
        tmp.write(grib_bytes)
        tmp_path = Path(tmp.name)

    try:
        datasets = cfgrib.open_datasets(str(tmp_path))
        target_lons = [(lon % 360) for lon in longitudes]
        results: list[dict[str, float]] = [{} for _ in range(n_points)]

        for ds in datasets:
            for var_name, xr_var in ds.data_vars.items():
                short_name = str(var_name)
                type_of_level = xr_var.attrs.get("GRIB_typeOfLevel", "")

                field_name = _CLOUD_DIAG_FIELD_MAP.get(
                    (short_name, type_of_level)
                )
                if field_name is None:
                    continue

                values = _interpolate_per_point(
                    xr_var, latitudes, target_lons,
                )
                for i, val in enumerate(values):
                    if val is not None:
                        # Don't overwrite — first match wins (instant over avg)
                        if field_name not in results[i]:
                            results[i][field_name] = val

        for ds in datasets:
            ds.close()

        return results
    except Exception:
        logger.warning("cfgrib failed to decode cloud diag GRIB2", exc_info=True)
        return [{} for _ in range(n_points)]
    finally:
        tmp_path.unlink(missing_ok=True)


def _decode_icon_eu_single_var(
    grib_bytes: bytes,
    latitudes: list[float],
    longitudes: list[float],
) -> dict[int, list[float | None]]:
    """Decode a single ICON-EU variable and spatially interpolate to route points.

    Returns:
        {model_level: [value_for_point_0, value_for_point_1, ...]}.
        Empty dict on failure.
    """
    import cfgrib

    if not grib_bytes:
        return {}

    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
        tmp.write(grib_bytes)
        tmp_path = Path(tmp.name)

    try:
        datasets = cfgrib.open_datasets(str(tmp_path))
        # ICON-EU uses -180 to +180 longitude convention
        target_lons = list(longitudes)
        level_values: dict[int, list[float | None]] = {}

        for ds in datasets:
            for var_name, xr_var in ds.data_vars.items():
                # Find the model-level dimension
                var_level_coord = None
                for coord_name in ("generalVerticalLayer", "generalVertical", "level", "hybrid"):
                    if coord_name in xr_var.dims:
                        var_level_coord = coord_name
                        break

                if var_level_coord is not None:
                    levels = ds.coords[var_level_coord].values
                    for lev_val in levels:
                        lev = int(float(lev_val))
                        level_data = xr_var.sel({var_level_coord: lev_val})
                        values = _interpolate_per_point(
                            level_data, latitudes, target_lons,
                        )
                        level_values[lev] = values
                else:
                    lev = int(xr_var.attrs.get("level", xr_var.attrs.get("GRIB_level", 0)))
                    if lev > 0:
                        values = _interpolate_per_point(
                            xr_var, latitudes, target_lons,
                        )
                        level_values[lev] = values

            ds.close()
        del datasets
        return level_values
    except Exception:
        logger.warning("cfgrib failed to decode ICON-EU single-var GRIB2", exc_info=True)
        return {}
    finally:
        tmp_path.unlink(missing_ok=True)


def _derive_clc_cloud_layers(
    pressure_data: dict[int, list[float | None]],
    clc_data: dict[int, list[float | None]],
    n_points: int,
    clc_threshold: float = 5.0,
) -> list[dict[str, float]]:
    """Derive ICAO-band cloud base/top from model-level CLC profiles.

    Scans each point's CLC profile (on native model levels, not interpolated)
    to find where cloud fraction exceeds the threshold, then classifies into
    low/mid/high ICAO bands and returns base_ft/top_ft for each.

    ICAO band boundaries (by pressure):
      low:  surface to 800 hPa  (~6500 ft)
      mid:  800 to 400 hPa      (~6500–23000 ft)
      high: above 400 hPa       (~23000 ft)

    Returns:
        List of dicts (one per point), each with keys like
        ``low_base_ft``, ``low_top_ft``, ``mid_base_ft``, etc.
        Only populated for bands where cloud was detected.
    """
    from weatherbrief.models.analysis import pressure_pa_to_altitude_ft

    # ICAO layer boundaries in Pa
    LOW_TOP_PA = 80_000   # 800 hPa
    MID_TOP_PA = 40_000   # 400 hPa

    model_levels = sorted(pressure_data.keys())
    results: list[dict[str, float]] = [{} for _ in range(n_points)]

    for pt_idx in range(n_points):
        # Build (pressure_pa, clc%) pairs
        profile: list[tuple[float, float]] = []
        for lev in model_levels:
            p_vals = pressure_data.get(lev)
            c_vals = clc_data.get(lev)
            if p_vals is None or c_vals is None:
                continue
            if pt_idx >= len(p_vals) or pt_idx >= len(c_vals):
                continue
            p_val = p_vals[pt_idx]
            c_val = c_vals[pt_idx]
            if p_val is not None and c_val is not None:
                profile.append((p_val, c_val))

        if not profile:
            continue

        # Sort by pressure descending (surface/high-pressure first)
        profile.sort(key=lambda x: -x[0])

        # Find contiguous cloud layers, then classify into ICAO bands
        cloud_layers: list[tuple[float, float]] = []  # (base_pa, top_pa)
        in_cloud = False
        base_pa = 0.0
        top_pa = 0.0

        for p_pa, clc in profile:
            if clc >= clc_threshold:
                if not in_cloud:
                    base_pa = p_pa
                    in_cloud = True
                top_pa = p_pa  # keep updating top (lower pressure)
            else:
                if in_cloud:
                    cloud_layers.append((base_pa, top_pa))
                    in_cloud = False
        if in_cloud:
            cloud_layers.append((base_pa, top_pa))

        if not cloud_layers:
            continue

        # Classify each cloud layer into ICAO bands.
        # When multiple disjoint clouds overlap a band, pick the lowest
        # (highest base pressure) — most relevant for flight altitude.
        for band_name, band_min_pa, band_max_pa in [
            ("low",  LOW_TOP_PA, float("inf")),
            ("mid",  MID_TOP_PA, LOW_TOP_PA),
            ("high", 0,          MID_TOP_PA),
        ]:
            best_base_pa: float | None = None
            best_top_pa: float | None = None
            for cl_base_pa, cl_top_pa in cloud_layers:
                # Clamp to band boundaries
                clamped_base = min(cl_base_pa, band_max_pa) if band_max_pa != float("inf") else cl_base_pa
                clamped_top = max(cl_top_pa, band_min_pa)
                if clamped_base <= clamped_top:
                    continue  # no overlap with this band
                # Pick the lowest cloud (highest base pressure = lowest altitude)
                if best_base_pa is None or clamped_base > best_base_pa:
                    best_base_pa = clamped_base
                    best_top_pa = clamped_top

            if best_base_pa is not None and best_top_pa is not None:
                results[pt_idx][f"{band_name}_base_ft"] = round(
                    pressure_pa_to_altitude_ft(best_base_pa),
                )
                results[pt_idx][f"{band_name}_top_ft"] = round(
                    pressure_pa_to_altitude_ft(best_top_pa),
                )

    return results


def decode_icon_eu_per_point_chunked(
    var_bytes: dict[str, bytes],
    latitudes: list[float],
    longitudes: list[float],
    target_pressures_hpa: list[int] | None = None,
) -> tuple[list[dict[int, dict[str, float]]], list[dict[str, float]]]:
    """Decode ICON-EU model-level GRIB2 per-variable to limit peak memory.

    Instead of decoding all variables at once (~800MB), this processes one
    variable at a time (~270MB peak), keeping only the small interpolated
    point values between steps.

    Args:
        var_bytes: {variable_name: grib_bytes} — one entry per variable
            (e.g. "p", "qc", "qi", "clc"). Each is concatenated GRIB2 for
            all model levels of that variable.
        latitudes: Target latitudes for interpolation.
        longitudes: Target longitudes for interpolation.
        target_pressures_hpa: Target pressure levels in hPa.

    Returns:
        Tuple of:
        - List of dicts (one per point): [{pressure_hpa: {field: value}}, ...]
        - List of dicts (one per point): CLC-derived cloud layer boundaries
          with keys like ``low_base_ft``, ``low_top_ft``, etc.
    """
    import gc

    from weatherbrief.fetch.grib.icon_eu_levels import (
        TARGET_PRESSURE_LEVELS_HPA,
        interpolate_model_to_pressure_levels,
    )

    if target_pressures_hpa is None:
        target_pressures_hpa = TARGET_PRESSURE_LEVELS_HPA

    n_points = len(latitudes)

    # Step 1: Decode P (pressure) variable — needed for vertical interpolation
    p_bytes = var_bytes.get("p", b"")
    pressure_data = _decode_icon_eu_single_var(p_bytes, latitudes, longitudes)
    del p_bytes
    gc.collect()

    empty_clc_layers: list[dict[str, float]] = [{} for _ in range(n_points)]

    if not pressure_data:
        logger.warning("No pressure (P) data found in ICON-EU GRIB — cannot interpolate")
        return [{} for _ in range(n_points)], empty_clc_layers

    model_levels = sorted(pressure_data.keys())
    results: list[dict[int, dict[str, float]]] = [{} for _ in range(n_points)]
    clc_cloud_layers = empty_clc_layers

    # Step 2: Decode each variable one at a time, interpolate, discard.
    # Cloud variables use final field names; sounding variables use raw_ prefix
    # for later unit conversion via _convert_raw_sounding().
    for var_name, field_key in (
        ("qc", "cloud_liquid_water_kg_kg"),
        ("qi", "ice_mixing_ratio_kg_kg"),
        ("clc", "cloud_area_fraction_pct"),
        ("t", "raw_temperature_k"),
        ("qv", "raw_specific_humidity_kg_kg"),
        ("u", "raw_u_wind_m_s"),
        ("v", "raw_v_wind_m_s"),
        ("w", "raw_w_m_s"),
    ):
        raw = var_bytes.get(var_name, b"")
        if not raw:
            continue
        field_data = _decode_icon_eu_single_var(raw, latitudes, longitudes)
        del raw
        gc.collect()

        if not field_data:
            continue

        for pt_idx in range(n_points):
            model_pressures: list[float] = []
            model_values: list[float] = []

            for lev in model_levels:
                p_vals = pressure_data.get(lev)
                f_vals = field_data.get(lev)
                if p_vals is None or f_vals is None:
                    continue
                if pt_idx >= len(p_vals) or pt_idx >= len(f_vals):
                    continue
                p_val = p_vals[pt_idx]
                f_val = f_vals[pt_idx]
                if p_val is not None and f_val is not None:
                    model_pressures.append(p_val)
                    model_values.append(f_val)

            if len(model_pressures) < 2:
                continue

            interp_result = interpolate_model_to_pressure_levels(
                model_pressures, model_values, target_pressures_hpa,
            )
            for p_hpa, val in interp_result.items():
                results[pt_idx].setdefault(p_hpa, {})[field_key] = val

        # Derive cloud layer boundaries from model-level CLC before discarding
        if var_name == "clc":
            clc_cloud_layers = _derive_clc_cloud_layers(
                pressure_data, field_data, n_points,
            )

        del field_data
        gc.collect()

    return results, clc_cloud_layers


def decode_icon_eu_per_point(
    grib_bytes: bytes,
    latitudes: list[float],
    longitudes: list[float],
    target_pressures_hpa: list[int] | None = None,
) -> list[dict[int, dict[str, float]]]:
    """Decode ICON-EU model-level GRIB2 and interpolate to pressure levels per point.

    Legacy interface — decodes all variables from a single concatenated blob.
    Prefer decode_icon_eu_per_point_chunked() for lower peak memory.

    Args:
        grib_bytes: Concatenated GRIB2 messages from ICON-EU.
        latitudes: Target latitudes for interpolation.
        longitudes: Target longitudes for interpolation.
        target_pressures_hpa: Target pressure levels in hPa.

    Returns:
        List of dicts (one per point): [{pressure_hpa: {field: value}}, ...].
    """
    import cfgrib

    from weatherbrief.fetch.grib.icon_eu_levels import (
        TARGET_PRESSURE_LEVELS_HPA,
        interpolate_model_to_pressure_levels,
    )

    if target_pressures_hpa is None:
        target_pressures_hpa = TARGET_PRESSURE_LEVELS_HPA

    n_points = len(latitudes)
    if not grib_bytes:
        return [{} for _ in range(n_points)]

    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
        tmp.write(grib_bytes)
        tmp_path = Path(tmp.name)

    try:
        datasets = cfgrib.open_datasets(str(tmp_path))

        # ICON-EU uses -180 to +180 longitude convention (no normalization needed)
        target_lons = list(longitudes)

        # Collect per-point, per-level values for each variable.
        # Structure: {var_name: {level: [val_for_point_0, val_for_point_1, ...]}}
        point_level_data: dict[str, dict[int, list[float | None]]] = {}

        for ds in datasets:
            # Find the vertical coordinate name for model levels
            level_coord = None
            for coord_name in ("generalVerticalLayer", "generalVertical", "level", "hybrid"):
                if coord_name in ds.dims:
                    level_coord = coord_name
                    break

            for var_name, xr_var in ds.data_vars.items():
                name_lower = str(var_name).lower()

                # Map ICON-EU variable names
                # cfgrib may decode the P field as "p" or "pres" depending
                # on the GRIB shortName mapping.
                if name_lower in ("p", "pres"):
                    field_key = "pressure_pa"
                elif name_lower in _ICON_FULL_VAR_MAP:
                    field_key = _ICON_FULL_VAR_MAP[name_lower]
                else:
                    continue

                if field_key not in point_level_data:
                    point_level_data[field_key] = {}

                # Check if this variable has the model-level dimension
                var_level_coord = None
                for coord_name in ("generalVerticalLayer", "generalVertical", "level", "hybrid"):
                    if coord_name in xr_var.dims:
                        var_level_coord = coord_name
                        break

                if var_level_coord is not None:
                    levels = ds.coords[var_level_coord].values
                    for lev_val in levels:
                        lev = int(float(lev_val))
                        level_data = xr_var.sel({var_level_coord: lev_val})
                        values = _interpolate_per_point(
                            level_data, latitudes, target_lons,
                        )
                        point_level_data[field_key][lev] = values
                else:
                    # Single level — use level from attrs if available
                    lev = int(xr_var.attrs.get("level", xr_var.attrs.get("GRIB_level", 0)))
                    values = _interpolate_per_point(
                        xr_var, latitudes, target_lons,
                    )
                    if lev > 0:
                        point_level_data[field_key][lev] = values

            # Close each dataset immediately to free memory (ICON-EU grids are large)
            ds.close()
        del datasets

        # Now interpolate from model levels to pressure levels per point
        pressure_data = point_level_data.get("pressure_pa", {})
        if not pressure_data:
            logger.warning("No pressure (P) data found in ICON-EU GRIB — cannot interpolate")
            return [{} for _ in range(n_points)]

        # Get sorted model levels
        model_levels = sorted(pressure_data.keys())

        results: list[dict[int, dict[str, float]]] = [{} for _ in range(n_points)]

        _ICON_INTERP_FIELDS = (
            "cloud_liquid_water_kg_kg", "ice_mixing_ratio_kg_kg",
            "raw_temperature_k", "raw_specific_humidity_kg_kg",
            "raw_u_wind_m_s", "raw_v_wind_m_s",
            "raw_w_m_s",
        )
        for field_key in _ICON_INTERP_FIELDS:
            field_data = point_level_data.get(field_key, {})
            if not field_data:
                continue

            for pt_idx in range(n_points):
                # Build the pressure and value columns for this point
                model_pressures: list[float] = []
                model_values: list[float] = []

                for lev in model_levels:
                    p_vals = pressure_data.get(lev)
                    f_vals = field_data.get(lev)
                    if p_vals is None or f_vals is None:
                        continue
                    if pt_idx >= len(p_vals) or pt_idx >= len(f_vals):
                        continue
                    p_val = p_vals[pt_idx]
                    f_val = f_vals[pt_idx]
                    if p_val is not None and f_val is not None:
                        model_pressures.append(p_val)
                        model_values.append(f_val)

                if len(model_pressures) < 2:
                    continue

                interp_result = interpolate_model_to_pressure_levels(
                    model_pressures, model_values, target_pressures_hpa,
                )
                for p_hpa, val in interp_result.items():
                    results[pt_idx].setdefault(p_hpa, {})[field_key] = val

        return results
    except Exception:
        logger.warning("cfgrib failed to decode ICON-EU GRIB2 data", exc_info=True)
        return [{} for _ in range(n_points)]
    finally:
        tmp_path.unlink(missing_ok=True)


def build_cloud_diagnostics(
    raw: dict[str, float],
) -> "NWPCloudDiagnostics | None":
    """Convert raw decoded values into an NWPCloudDiagnostics model.

    Unit conversions applied:
    - Pressure (Pa) → altitude (ft) via standard atmosphere
    - Temperature (K) → °C
    - Geopotential height (gpm) → ft (× 3.28084)
    - Cloud cover (%) → kept as-is

    Returns None if the raw dict is empty.
    """
    from weatherbrief.models.analysis import (
        NWPCloudDiagnostics,
        NWPCloudLayerDiag,
        pressure_pa_to_altitude_ft,
    )

    if not raw:
        return None

    def _pa_to_ft(key: str) -> float | None:
        val = raw.get(key)
        if val is None or val <= 0:
            return None
        return round(pressure_pa_to_altitude_ft(val))

    def _k_to_c(key: str) -> float | None:
        val = raw.get(key)
        if val is None:
            return None
        return round(val - 273.15, 1)

    def _gpm_to_ft(key: str) -> float | None:
        val = raw.get(key)
        if val is None:
            return None
        return round(val * 3.28084)

    def _pct(key: str) -> float | None:
        return raw.get(key)

    low = NWPCloudLayerDiag(
        cover_pct=_pct("low_cover_pct"),
        base_ft=_pa_to_ft("low_base_pa"),
        top_ft=_pa_to_ft("low_top_pa"),
        top_temp_c=_k_to_c("low_top_temp_k"),
    )
    mid = NWPCloudLayerDiag(
        cover_pct=_pct("mid_cover_pct"),
        base_ft=_pa_to_ft("mid_base_pa"),
        top_ft=_pa_to_ft("mid_top_pa"),
        top_temp_c=_k_to_c("mid_top_temp_k"),
    )
    high = NWPCloudLayerDiag(
        cover_pct=_pct("high_cover_pct"),
        base_ft=_pa_to_ft("high_base_pa"),
        top_ft=_pa_to_ft("high_top_pa"),
        top_temp_c=_k_to_c("high_top_temp_k"),
    )

    diag = NWPCloudDiagnostics(
        low=low,
        mid=mid,
        high=high,
        convective_cover_pct=_pct("convective_cover_pct"),
        convective_base_ft=_pa_to_ft("convective_base_pa"),
        convective_top_ft=_pa_to_ft("convective_top_pa"),
        total_cover_pct=_pct("total_cover_pct"),
        boundary_cover_pct=_pct("boundary_cover_pct"),
        ceiling_ft=_gpm_to_ft("ceiling_gpm"),
    )

    return diag


def decode_icon_eu_cloud_diag_per_point(
    grib_bytes: bytes,
    latitudes: list[float],
    longitudes: list[float],
) -> list[dict[str, float]]:
    """Decode ICON-EU single-level cloud diagnostic GRIB2 per route point.

    These are single-level variables (CEILING, HBAS_CON, HTOP_CON) with
    no pressure dimension. Uses _ICON_CLOUD_DIAG_FIELD_MAP to identify
    variables by shortName.

    ICON-EU uses -180 to +180 longitude convention (no normalization needed).

    Returns:
        List of flat dicts (one per point): [{field_name: raw_value_m, ...}, ...].
        Raw values are in meters (native ICON-EU units).
    """
    import cfgrib

    n_points = len(latitudes)
    if not grib_bytes:
        return [{} for _ in range(n_points)]

    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
        tmp.write(grib_bytes)
        tmp_path = Path(tmp.name)

    try:
        datasets = cfgrib.open_datasets(str(tmp_path))
        # ICON-EU uses -180 to +180 (same as route points)
        target_lons = list(longitudes)
        results: list[dict[str, float]] = [{} for _ in range(n_points)]

        for ds in datasets:
            for var_name, xr_var in ds.data_vars.items():
                short_name = str(var_name).lower()
                field_name = _ICON_CLOUD_DIAG_FIELD_MAP.get(short_name)
                if field_name is None:
                    continue

                values = _interpolate_per_point(
                    xr_var, latitudes, target_lons,
                )
                for i, val in enumerate(values):
                    if val is not None and field_name not in results[i]:
                        results[i][field_name] = val

        for ds in datasets:
            ds.close()

        return results
    except Exception:
        logger.warning("cfgrib failed to decode ICON-EU cloud diag GRIB2", exc_info=True)
        return [{} for _ in range(n_points)]
    finally:
        tmp_path.unlink(missing_ok=True)


_M_TO_FT = 3.28084


def build_icon_cloud_diagnostics(
    raw: dict[str, float],
) -> "NWPCloudDiagnostics | None":
    """Convert raw ICON-EU decoded values into an NWPCloudDiagnostics model.

    ICON-EU reports heights in meters (not gpm or Pa like GFS).
    Cloud cover percentages (CLCL/CLCM/CLCH/CLCT) are 0–100 and kept as-is.

    Unit conversions applied:
    - meters → feet (× 3.28084)

    Returns None if the raw dict is empty or no fields are populated.
    """
    from weatherbrief.models.analysis import (
        NWPCloudDiagnostics,
        NWPCloudLayerDiag,
    )

    if not raw:
        return None

    def _m_to_ft(key: str) -> float | None:
        val = raw.get(key)
        if val is None or val < 0:
            return None
        return round(val * _M_TO_FT)

    def _pct(key: str) -> float | None:
        return raw.get(key)

    ceiling_ft = _m_to_ft("ceiling_m")
    convective_base_ft = _m_to_ft("convective_cloud_base_m")
    convective_top_ft = _m_to_ft("convective_cloud_top_m")
    low_cover = _pct("low_cover_pct")
    mid_cover = _pct("mid_cover_pct")
    high_cover = _pct("high_cover_pct")
    total_cover = _pct("total_cover_pct")

    # Only create diagnostics if at least one field is populated
    has_any = (
        ceiling_ft is not None
        or convective_base_ft is not None
        or convective_top_ft is not None
        or low_cover is not None
        or mid_cover is not None
        or high_cover is not None
        or total_cover is not None
    )
    if not has_any:
        return None

    return NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=low_cover),
        mid=NWPCloudLayerDiag(cover_pct=mid_cover),
        high=NWPCloudLayerDiag(cover_pct=high_cover),
        total_cover_pct=total_cover,
        ceiling_ft=ceiling_ft,
        convective_base_ft=convective_base_ft,
        convective_top_ft=convective_top_ft,
    )


# ---------------------------------------------------------------------------
# ECMWF GRIB decode — local files, multi-grid, per-point coverage
# ---------------------------------------------------------------------------


def decode_ecmwf_pressure_per_point(
    file_path: Path,
    latitudes: list[float],
    longitudes: list[float],
) -> tuple[list[dict[int, dict[str, float]]], list[bool]]:
    """Decode ECMWF pressure-level GRIB and interpolate per route point.

    Handles multi-grid files: cfgrib.open_datasets() splits each geographic
    sub-grid into a separate xarray Dataset.  For each point we try every
    dataset until one returns non-NaN values.

    ECMWF ``cc`` (cloud cover fraction, 0–1) is converted to 0–100 % to
    match the ``cloud_area_fraction_pct`` convention used by ICON-EU CLC.

    Args:
        file_path: Path to an ECMWF a2 (pressure-level) GRIB file on disk.
        latitudes: Route-point latitudes.
        longitudes: Route-point longitudes.

    Returns:
        Tuple of:
        - Per-point data: [{pressure_hpa: {field: value, …}, …}, …]
        - Coverage mask: [True if at least one level decoded, …]
    """
    import cfgrib

    n_points = len(latitudes)
    results: list[dict[int, dict[str, float]]] = [{} for _ in range(n_points)]
    covered: list[bool] = [False] * n_points

    try:
        datasets = cfgrib.open_datasets(str(file_path))
    except Exception:
        logger.warning("cfgrib failed to open ECMWF file %s", file_path, exc_info=True)
        return results, covered

    # No longitude normalization needed: ECMWF grids use -180/+180 convention,
    # same as route points.  (GFS uses 0-360 and requires `lon % 360`.)
    try:
        results, covered = _decode_pressure_vars_from_datasets(
            datasets, latitudes, longitudes,
            var_map=_ECMWF_FULL_VAR_MAP,
            frac_vars=_ECMWF_FRAC_TO_PCT,
            first_wins=True,
        )
        return results, covered
    except Exception:
        logger.warning("Failed to decode ECMWF pressure data from %s", file_path, exc_info=True)
        return [{} for _ in range(n_points)], [False] * n_points
    finally:
        for ds in datasets:
            ds.close()


_G = 9.80665  # gravitational acceleration (m/s²)
_KT_PER_MS = 1.94384  # knots per m/s


def _convert_raw_sounding(
    raw: dict[str, float],
    pressure_hpa: int,
) -> dict[str, float]:
    """Convert raw GRIB sounding fields to PressureLevelData units.

    Input keys use raw_ prefix from _ECMWF_FULL_VAR_MAP.
    Output keys match PressureLevelData field names.
    """
    import math

    out: dict[str, float] = {}

    # Pass through fields that need no conversion
    for key in ("cloud_liquid_water_kg_kg", "ice_mixing_ratio_kg_kg",
                "cloud_area_fraction_pct", "vertical_velocity_pa_s"):
        if key in raw:
            out[key] = raw[key]

    # Temperature: K → °C
    t_k = raw.get("raw_temperature_k")
    if t_k is not None:
        out["temperature_c"] = t_k - 273.15

    # Relative humidity
    rh = raw.get("raw_relative_humidity_pct")
    if rh is None:
        # Fallback: derive RH from specific humidity + T + P
        q = raw.get("raw_specific_humidity_kg_kg")
        if q is not None and t_k is not None:
            # Saturation vapor pressure (Magnus formula, hPa)
            t_c = t_k - 273.15
            e_sat = 6.112 * math.exp(17.67 * t_c / (t_c + 243.5))
            # Mixing ratio from specific humidity
            w = q / (1.0 - q) if q < 1.0 else q
            # Saturation mixing ratio
            w_sat = 0.622 * e_sat / (pressure_hpa - e_sat) if pressure_hpa > e_sat else 1.0
            rh = min(100.0, max(0.0, (w / w_sat) * 100.0)) if w_sat > 0 else 0.0
    if rh is not None:
        out["relative_humidity_pct"] = rh

    # Dewpoint from T + RH (Magnus formula)
    if out.get("temperature_c") is not None and out.get("relative_humidity_pct") is not None:
        t_c = out["temperature_c"]
        rh_val = max(out["relative_humidity_pct"], 0.1)  # avoid log(0)
        a, b = 17.67, 243.5
        gamma = a * t_c / (b + t_c) + math.log(rh_val / 100.0)
        out["dewpoint_c"] = b * gamma / (a - gamma)

    # Geopotential → height
    z = raw.get("raw_geopotential_m2_s2")
    if z is not None:
        out["geopotential_height_m"] = z / _G

    # Wind u, v → speed (kt) and direction (deg)
    u = raw.get("raw_u_wind_m_s")
    v = raw.get("raw_v_wind_m_s")
    if u is not None and v is not None:
        speed_ms = math.sqrt(u * u + v * v)
        out["wind_speed_kt"] = speed_ms * _KT_PER_MS
        if speed_ms > 0.01:
            out["wind_direction_deg"] = (math.atan2(-u, -v) * 180.0 / math.pi) % 360.0
        else:
            out["wind_direction_deg"] = 0.0

    # Physical vertical velocity (m/s, ICON) → omega (Pa/s)
    # omega = -ρ·g·w, where ρ = P/(Rd·T_k)
    w_ms = raw.get("raw_w_m_s")
    if w_ms is not None and t_k is not None:
        _Rd = 287.05  # J/(kg·K), specific gas constant for dry air
        rho = (pressure_hpa * 100.0) / (_Rd * t_k)
        out["vertical_velocity_pa_s"] = -rho * _G * w_ms

    return out


def build_pressure_levels_from_grib(
    point_data: dict[int, dict[str, float]],
) -> list:
    """Build PressureLevelData objects from decoded ECMWF GRIB data.

    Converts raw GRIB fields (temperature in K, wind in m/s, geopotential
    in m²/s²) to PressureLevelData units and returns a sorted list (highest
    pressure / lowest altitude first).
    """
    from weatherbrief.models.analysis import PressureLevelData

    levels = []
    for p_hpa, raw_fields in sorted(point_data.items(), reverse=True):
        converted = _convert_raw_sounding(raw_fields, p_hpa)
        if not converted:
            continue
        levels.append(PressureLevelData(pressure_hpa=p_hpa, **converted))
    return levels


def decode_ecmwf_surface_per_point(
    file_path: Path,
    latitudes: list[float],
    longitudes: list[float],
) -> tuple[list[dict[str, float]], list[bool]]:
    """Decode ECMWF surface/single-level GRIB and interpolate per point.

    Handles multi-grid files the same way as the pressure-level decoder.

    Args:
        file_path: Path to an ECMWF a1 (surface) GRIB file on disk.
        latitudes: Route-point latitudes.
        longitudes: Route-point longitudes.

    Returns:
        Tuple of:
        - Per-point raw dicts: [{field_name: value, …}, …]
        - Coverage mask
    """
    import cfgrib

    n_points = len(latitudes)
    results: list[dict[str, float]] = [{} for _ in range(n_points)]
    covered: list[bool] = [False] * n_points

    try:
        datasets = cfgrib.open_datasets(str(file_path))
    except Exception:
        logger.warning("cfgrib failed to open ECMWF surface file %s", file_path, exc_info=True)
        return results, covered

    try:
        for ds in datasets:
            for var_name in ds.data_vars:
                var_lower = str(var_name).lower()
                field_name = _ECMWF_CLOUD_DIAG_FIELD_MAP.get(var_lower)
                if field_name is None:
                    continue

                xr_var = ds[var_name]
                values = _interpolate_per_point(xr_var, latitudes, longitudes)
                for i, val in enumerate(values):
                    if val is not None and field_name not in results[i]:
                        results[i][field_name] = val
                        covered[i] = True

        return results, covered
    except Exception:
        logger.warning("Failed to decode ECMWF surface data from %s", file_path, exc_info=True)
        return [{} for _ in range(n_points)], [False] * n_points
    finally:
        for ds in datasets:
            ds.close()


_ECMWF_NO_CLOUD_SENTINEL_M = 9999.0  # ECMWF uses 9999m for "no cloud"


def build_ecmwf_cloud_diagnostics(
    raw: dict[str, float],
) -> "NWPCloudDiagnostics | None":
    """Convert raw ECMWF surface values into NWPCloudDiagnostics.

    ECMWF reports heights in meters (like ICON-EU), cloud covers as
    0–1 fractions.  Heights ≥ 9998 m are treated as "no cloud" sentinel
    (ECMWF uses 9999 m; threshold at 9998 for float tolerance).

    Unit conversions:
    - meters → feet (× 3.28084)
    - 0–1 fraction → 0–100 % (× 100)
    """
    from weatherbrief.models.analysis import (
        NWPCloudDiagnostics,
        NWPCloudLayerDiag,
    )

    if not raw:
        return None

    def _m_to_ft(key: str) -> float | None:
        val = raw.get(key)
        if val is None or val < 0 or val >= _ECMWF_NO_CLOUD_SENTINEL_M:
            return None
        return round(val * _M_TO_FT)

    def _frac_to_pct(key: str) -> float | None:
        val = raw.get(key)
        if val is None:
            return None
        return round(val * 100.0, 1)

    ceiling_ft = _m_to_ft("ceiling_m")
    cloud_base_ft = _m_to_ft("cloud_base_height_m")
    low_cover = _frac_to_pct("low_cover_frac")
    mid_cover = _frac_to_pct("mid_cover_frac")
    high_cover = _frac_to_pct("high_cover_frac")
    total_cover = _frac_to_pct("total_cover_frac")

    has_any = (
        ceiling_ft is not None
        or cloud_base_ft is not None
        or low_cover is not None
        or mid_cover is not None
        or high_cover is not None
        or total_cover is not None
    )
    if not has_any:
        return None

    return NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=low_cover, base_ft=cloud_base_ft),
        mid=NWPCloudLayerDiag(cover_pct=mid_cover),
        high=NWPCloudLayerDiag(cover_pct=high_cover),
        total_cover_pct=total_cover,
        ceiling_ft=ceiling_ft,
    )
