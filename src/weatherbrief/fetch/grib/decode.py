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
from pathlib import Path

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
    "hbas_con": "convective_cloud_base_m",
    "htop_con": "convective_cloud_top_m",
    "clcl": "low_cover_pct",
    "clcm": "mid_cover_pct",
    "clch": "high_cover_pct",
    "clct": "total_cover_pct",
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


def decode_icon_eu_per_point(
    grib_bytes: bytes,
    latitudes: list[float],
    longitudes: list[float],
    target_pressures_hpa: list[int] | None = None,
) -> list[dict[int, dict[str, float]]]:
    """Decode ICON-EU model-level GRIB2 and interpolate to pressure levels per point.

    ICON-EU data is on model levels (not pressure levels). This function:
    1. Decodes QC, QI, and P fields from the GRIB2 bytes
    2. Spatially interpolates each field to route point coordinates
    3. Vertically interpolates from model levels to pressure levels using P

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
                if name_lower == "p":
                    field_key = "pressure_pa"
                elif name_lower in _VAR_MAP:
                    field_key = _VAR_MAP[name_lower]
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

        for ds in datasets:
            ds.close()

        # Now interpolate from model levels to pressure levels per point
        pressure_data = point_level_data.get("pressure_pa", {})
        if not pressure_data:
            logger.warning("No pressure (P) data found in ICON-EU GRIB — cannot interpolate")
            return [{} for _ in range(n_points)]

        # Get sorted model levels
        model_levels = sorted(pressure_data.keys())

        results: list[dict[int, dict[str, float]]] = [{} for _ in range(n_points)]

        for field_key in ("cloud_liquid_water_kg_kg", "ice_mixing_ratio_kg_kg"):
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
        if val is None or val <= 0:
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
