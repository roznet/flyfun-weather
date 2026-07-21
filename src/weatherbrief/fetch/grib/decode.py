"""GRIB2 decoding and spatial interpolation using cfgrib + xarray.

Decodes concatenated GRIB2 bytes into xarray Datasets, then interpolates
to specific route point coordinates.

Handles two categories of GFS variables:
1. Pressure-level variables (CLWMR/ICMR) — one value per pressure level per point
2. Cloud diagnostic variables (LCDC, PRES cloud layers, etc.) — one scalar per point
"""

from __future__ import annotations

import logging
import math
import tempfile
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

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
    "gh": "geopotential_height_m",  # Delivered on every pressure level post-amendment (gpm ≈ m).
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
    "cape_ml": "ml_cape_jkg",   # J/kg, mixed-layer CAPE (#283)
    "cin_ml": "ml_cin_jkg",     # J/kg, mixed-layer CIN (#283)
    # Convective rain, kg/m² ≡ mm, ACCUMULATED since init (#421). The near-
    # equivalent of ECMWF `cp`, but already mm (no ×1000). The rate is
    # de-accumulated in the ICON enrichment loop (the only place that knows the
    # previous step), so build_icon_cloud_diagnostics stays unaware of it.
    #
    # DWD's RAIN_CON product decodes under cfgrib shortName `crr` (paramId
    # 228218, "Convective rain rate") — NOT `rain_con`. Despite the "rate" name
    # eccodes attaches, the stored values are the accumulated field (verified
    # against real 20260716_12z GRIB: monotonically increasing across forecast
    # hours), so the de-accumulation is correct. This map is keyed on the
    # lowercased cfgrib var name (see decode_icon_eu_cloud_diag_per_point), so
    # the key MUST be `crr` for the field to be picked up at all.
    "crr": "conv_rain_kg_m2",
}

# ECMWF single-level (a1) field mapping.
# Combines cloud diagnostics (consumed by build_ecmwf_cloud_diagnostics) and
# the surface variables consumed by build_ecmwf_surface_snapshot for the
# standalone verification pipeline.  Each builder picks only the keys it
# cares about, so adding entries here is non-invasive.
# cfgrib shortName → internal raw field name.
_ECMWF_CLOUD_DIAG_FIELD_MAP: dict[str, str] = {
    # Cloud diagnostics (used by build_ecmwf_cloud_diagnostics).
    # DATUM: ECMWF ceil/cbh/hcct/deg0l are all metres ABOVE GROUND (AGL),
    # confirmed against eccodes names + the ECMWF Parameter DB — NOT MSL. See
    # issue #441 finding #3 (datum normalization for airport-vs-en-route use is
    # a separate follow-up; these are AGL as delivered).
    "ceil": "ceiling_m",                   # meters AGL, 9999 = no cloud sentinel
    "cbh": "cloud_base_height_m",          # meters AGL, 9999 = no cloud sentinel
    "hcct": "convective_cloud_top_m",      # meters AGL, 9999 = no cloud sentinel
    "deg0l": "freezing_level_m",           # meters AGL (geometric height above ground, not MSL)
    # Native convective realization + stability (#283 Phase 2, delivered in a1).
    "cp": "conv_precip_m",                 # m water equiv, ACCUMULATED since init
    "kx": "k_index_c",                     # °C (K-index)
    "totalx": "total_totals_c",            # °C (Total Totals)
    "mlcape100": "ml_cape_jkg",            # J/kg (mixed-layer CAPE, lowest 100 hPa)
    "mlcin100": "ml_cin_jkg",              # J/kg (mixed-layer CIN, lowest 100 hPa)
    "lcc": "low_cover_frac",               # 0–1 fraction, ×100 during build
    "mcc": "mid_cover_frac",               # 0–1 fraction, ×100 during build
    "tcc": "total_cover_frac",             # 0–1 fraction, ×100 during build
    "hcc": "high_cover_frac",              # 0–1 fraction, ×100 during build
    # Surface variables (used by build_ecmwf_surface_snapshot)
    "t2m": "temperature_2m_k",             # K
    "d2m": "dewpoint_2m_k",                # K
    "u10": "u_wind_10m_ms",                # m/s
    "v10": "v_wind_10m_ms",                # m/s
    "fg10": "wind_gust_10m_ms",            # m/s (10-minute max gust)
    "vis": "visibility_m",                 # m
    "tp": "total_precip_m",                # m water equivalent, accumulated since init
    "sf": "snowfall_m_we",                 # m water equivalent, accumulated since init
    "mucape": "mucape_jkg",                # J/kg (most-unstable parcel CAPE)
    "sp": "surface_pressure_pa",           # Pa (used as anchor for sounding analysis)
    # NOTE: ``kx``/``totalx`` are mapped once above (k_index_c / total_totals_c).
    # The model-native convective indices feeding the convective character
    # advisory (issue #294, nwp_k_index / nwp_total_totals) derive from those
    # same decoded values in build_ecmwf_surface_snapshot — kx normalized from
    # Kelvin via _k_index_to_c (#283 established the unit), totalx offset-immune.
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
        # indexpath="" disables cfgrib's on-disk .idx sidecar. These are
        # one-shot temp files: only the .grib2 gets unlinked, so a written
        # .idx would be orphaned (604 found in the wild). (#441 efficiency)
        datasets = cfgrib.open_datasets(str(tmp_path), backend_kwargs={"indexpath": ""})

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


def _frac_grid_indices(
    coord_arr: "np.ndarray",
    targets: "np.ndarray | list[float]",
) -> "tuple[np.ndarray, np.ndarray]":
    """Map target coordinates to fractional indices in a 1-D coord array.

    Handles ascending or descending coords, uniform or non-uniform spacing.
    Out-of-range targets receive NaN — matching xarray's
    ``.interp(method='linear')`` default which fills outside the grid with NaN.

    Returns:
        (frac, in_bounds) — both shape (n,). ``frac`` is the fractional index
        in the original (non-reversed) coord array; ``in_bounds`` masks
        targets strictly outside the grid extent.
    """
    import numpy as np

    n = coord_arr.size
    targets = np.asarray(targets, dtype=np.float64)
    if n < 2:
        return np.full(targets.shape, np.nan), np.zeros(targets.shape, dtype=bool)

    if coord_arr[0] > coord_arr[-1]:
        # Descending: reverse so xp ascends; remap fp to original positions.
        xp = coord_arr[::-1]
        fp = np.arange(n - 1, -1, -1, dtype=np.float64)
    else:
        xp = coord_arr
        fp = np.arange(n, dtype=np.float64)

    frac = np.interp(targets, xp, fp, left=np.nan, right=np.nan)
    return frac, ~np.isnan(frac)


class _GridWeights(NamedTuple):
    """Bilinear corner indices + weights for target points on a lat/lon grid."""
    i0: np.ndarray
    j0: np.ndarray
    i1: np.ndarray
    j1: np.ndarray
    w00: np.ndarray
    w01: np.ndarray
    w10: np.ndarray
    w11: np.ndarray
    inb_idx: np.ndarray  # indices into the target list that fall in-bounds


def _bilinear_grid_weights(
    lat_arr: np.ndarray,
    lon_arr: np.ndarray,
    targets_lat: np.ndarray,
    targets_lon: np.ndarray,
) -> "_GridWeights | None":
    """Compute bilinear corner indices + weights once per dataset.

    Single source of truth shared by ``_decode_pressure_vars_from_datasets`` and
    ``_decode_icon_eu_single_var`` — the same gather then reuses these across
    every variable/level. Returns None for a degenerate grid (< 2 points on an
    axis); ``inb_idx`` may be empty when no target is inside the grid, which each
    caller handles per its own out-of-bounds semantics.
    """
    import numpy as np

    H, W = lat_arr.size, lon_arr.size
    if H < 2 or W < 2:
        return None
    frac_lat, lat_ok = _frac_grid_indices(lat_arr, targets_lat)
    frac_lon, lon_ok = _frac_grid_indices(lon_arr, targets_lon)
    in_bounds = lat_ok & lon_ok
    inb_idx = np.flatnonzero(in_bounds)
    fl = frac_lat[in_bounds]
    fln = frac_lon[in_bounds]
    i0 = np.clip(np.floor(fl).astype(np.intp), 0, H - 2)
    j0 = np.clip(np.floor(fln).astype(np.intp), 0, W - 2)
    i1 = i0 + 1
    j1 = j0 + 1
    ai = fl - i0
    aj = fln - j0
    return _GridWeights(
        i0, j0, i1, j1,
        (1.0 - ai) * (1.0 - aj), (1.0 - ai) * aj, ai * (1.0 - aj), ai * aj,
        inb_idx,
    )


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

    Vectorised: bilinear corner indices/weights are computed once per
    dataset and the full ``(level, lat, lon)`` array is gathered with
    numpy advanced indexing. The xarray ``.sel`` + ``.interp`` loop it
    replaced was GIL-bound, blocking concurrent decode threads.

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
    import numpy as np

    active_map = var_map if var_map is not None else _VAR_MAP
    frac_vars = frac_vars or set()
    n_points = len(latitudes)
    results: list[dict[int, dict[str, float]]] = [{} for _ in range(n_points)]
    covered: list[bool] = [False] * n_points

    if n_points == 0:
        return results, covered

    targets_lat = np.asarray(latitudes, dtype=np.float64)
    targets_lon = np.asarray(longitudes, dtype=np.float64)

    for ds in datasets:
        # Detect lat/lon dim names once per dataset
        lat_dim = lon_dim = None
        for dim in ds.dims:
            dim_lower = str(dim).lower()
            if "lat" in dim_lower:
                lat_dim = dim
            elif "lon" in dim_lower:
                lon_dim = dim
        if lat_dim is None or lon_dim is None:
            continue

        try:
            lat_arr = np.asarray(ds.coords[lat_dim].values, dtype=np.float64)
            lon_arr = np.asarray(ds.coords[lon_dim].values, dtype=np.float64)
        except Exception:
            logger.debug("skip dataset: lat/lon coord extract failed", exc_info=True)
            continue

        # Bilinear corner indices + weights — computed once per dataset and
        # reused for every variable in that dataset.
        bw = _bilinear_grid_weights(lat_arr, lon_arr, targets_lat, targets_lon)
        if bw is None or bw.inb_idx.size == 0:
            continue
        i0, j0, i1, j1 = bw.i0, bw.j0, bw.i1, bw.j1
        w00, w01, w10, w11 = bw.w00, bw.w01, bw.w10, bw.w11
        inb_idx = bw.inb_idx

        for var_name, xr_var in ds.data_vars.items():
            var_lower = str(var_name).lower()
            field_name = active_map.get(var_lower)
            if field_name is None:
                continue

            is_frac = var_lower in frac_vars

            # Determine pressure coordinate + the dim count we expect for this
            # variable. Doing this *before* materialising .values avoids a full
            # numpy allocation for ensemble/time-dim variables we'd just drop.
            pressure_coord = None
            for coord_name in ("isobaricInhPa", "level", "pressure"):
                if coord_name in xr_var.dims:
                    pressure_coord = coord_name
                    break

            if pressure_coord is None:
                level = xr_var.attrs.get("level")
                if level is None:
                    continue
                expected_ndim = 2
            else:
                expected_ndim = 3

            if xr_var.ndim != expected_ndim:
                logger.debug(
                    "skip %s: expected %d-D, got %d-D %s",
                    var_name, expected_ndim, xr_var.ndim, xr_var.dims,
                )
                continue

            try:
                var_dims = list(xr_var.dims)
                lat_axis = var_dims.index(lat_dim)
                lon_axis = var_dims.index(lon_dim)
                values = np.asarray(xr_var.values, dtype=np.float64)
            except (ValueError, KeyError):
                continue

            # Move lat/lon axes to the trailing positions so we can index
            # with values[..., i, j].
            other_axes = [a for a in range(values.ndim) if a not in (lat_axis, lon_axis)]
            values = np.transpose(values, other_axes + [lat_axis, lon_axis])

            if pressure_coord is None:
                p_hpa = int(level)
                interp = (
                    w00 * values[i0, j0]
                    + w01 * values[i0, j1]
                    + w10 * values[i1, j0]
                    + w11 * values[i1, j1]
                )
                # interp shape: (n_inb,)
                for k, pt_idx in enumerate(inb_idx):
                    v = interp[k]
                    if np.isnan(v):
                        continue
                    if first_wins and field_name in results[pt_idx].get(p_hpa, {}):
                        continue
                    if is_frac:
                        v = v * 100.0
                    results[pt_idx].setdefault(p_hpa, {})[field_name] = float(v)
                    covered[pt_idx] = True
                continue

            try:
                pressures = np.asarray(ds.coords[pressure_coord].values)
            except Exception:
                logger.debug(
                    "skip %s: pressure coord %r extract failed",
                    var_name, pressure_coord, exc_info=True,
                )
                continue
            if pressures.shape[0] != values.shape[0]:
                logger.debug(
                    "skip %s: pressure coord len %d != values axis 0 len %d",
                    var_name, pressures.shape[0], values.shape[0],
                )
                continue

            # Batched bilinear across all levels — shape (L, n_inb).
            interp = (
                w00[None, :] * values[:, i0, j0]
                + w01[None, :] * values[:, i0, j1]
                + w10[None, :] * values[:, i1, j0]
                + w11[None, :] * values[:, i1, j1]
            )
            scale = 100.0 if is_frac else 1.0
            for li in range(pressures.shape[0]):
                p_hpa = int(float(pressures[li]))
                row = interp[li]
                for k, pt_idx in enumerate(inb_idx):
                    v = row[k]
                    if np.isnan(v):
                        continue
                    if first_wins and field_name in results[pt_idx].get(p_hpa, {}):
                        continue
                    results[pt_idx].setdefault(p_hpa, {})[field_name] = float(v) * scale
                    covered[pt_idx] = True

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
        # indexpath="" disables cfgrib's on-disk .idx sidecar. These are
        # one-shot temp files: only the .grib2 gets unlinked, so a written
        # .idx would be orphaned (604 found in the wild). (#441 efficiency)
        datasets = cfgrib.open_datasets(str(tmp_path), backend_kwargs={"indexpath": ""})

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
        # indexpath="" disables cfgrib's on-disk .idx sidecar. These are
        # one-shot temp files: only the .grib2 gets unlinked, so a written
        # .idx would be orphaned (604 found in the wild). (#441 efficiency)
        datasets = cfgrib.open_datasets(str(tmp_path), backend_kwargs={"indexpath": ""})
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
        import numpy as np

        # indexpath="" disables cfgrib's on-disk .idx sidecar. These are
        # one-shot temp files: only the .grib2 gets unlinked, so a written
        # .idx would be orphaned (604 found in the wild). (#441 efficiency)
        datasets = cfgrib.open_datasets(str(tmp_path), backend_kwargs={"indexpath": ""})
        n_points = len(latitudes)
        # ICON-EU uses -180..+180 longitude convention (targets pass through).
        targets_lat = np.asarray(latitudes, dtype=np.float64)
        targets_lon = np.asarray(longitudes, dtype=np.float64)
        level_values: dict[int, list[float | None]] = {}

        for ds in datasets:
            # Bilinear corner indices + weights computed ONCE per dataset (shared
            # _bilinear_grid_weights), then every model level gathered in one
            # numpy op — replaces the per-level xarray `.interp` loop, which was
            # GIL-bound (blocking concurrent decode threads). Verified
            # numerically identical to the old path (max Δ ~1e-13). (#441 #2)
            lat_dim = lon_dim = None
            for dim in ds.dims:
                dim_lower = str(dim).lower()
                if "lat" in dim_lower:
                    lat_dim = dim
                elif "lon" in dim_lower:
                    lon_dim = dim
            if lat_dim is None or lon_dim is None:
                ds.close()
                continue
            try:
                lat_arr = np.asarray(ds.coords[lat_dim].values, dtype=np.float64)
                lon_arr = np.asarray(ds.coords[lon_dim].values, dtype=np.float64)
            except Exception:
                ds.close()
                continue
            bw = _bilinear_grid_weights(lat_arr, lon_arr, targets_lat, targets_lon)
            if bw is None:
                ds.close()
                continue
            i0, j0, i1, j1 = bw.i0, bw.j0, bw.i1, bw.j1
            w00, w01, w10, w11 = bw.w00, bw.w01, bw.w10, bw.w11
            inb_idx = bw.inb_idx

            for var_name, xr_var in ds.data_vars.items():
                var_level_coord = None
                for coord_name in ("generalVerticalLayer", "generalVertical", "level", "hybrid"):
                    if coord_name in xr_var.dims:
                        var_level_coord = coord_name
                        break

                var_dims = list(xr_var.dims)
                try:
                    lat_axis = var_dims.index(lat_dim)
                    lon_axis = var_dims.index(lon_dim)
                except ValueError:
                    continue
                values = np.asarray(xr_var.values, dtype=np.float64)
                other_axes = [a for a in range(values.ndim) if a not in (lat_axis, lon_axis)]
                values = np.transpose(values, other_axes + [lat_axis, lon_axis])

                if var_level_coord is not None:
                    levels = ds.coords[var_level_coord].values
                    if values.ndim != 3:
                        # Unexpected extra dim — fall back to per-level interp.
                        for lev_val in levels:
                            lev = int(float(lev_val))
                            level_values[lev] = _interpolate_per_point(
                                xr_var.sel({var_level_coord: lev_val}),
                                latitudes, list(longitudes),
                            )
                        continue
                    if inb_idx.size:
                        interp = (
                            w00 * values[:, i0, j0] + w01 * values[:, i0, j1]
                            + w10 * values[:, i1, j0] + w11 * values[:, i1, j1]
                        )  # (L, n_inb)
                    for li, lev_val in enumerate(levels):
                        lev = int(float(lev_val))
                        col: list[float | None] = [None] * n_points
                        if inb_idx.size:
                            row = interp[li]
                            for k, pt in enumerate(inb_idx):
                                v = row[k]
                                if not np.isnan(v):
                                    col[pt] = float(v)
                        level_values[lev] = col
                else:
                    lev = int(xr_var.attrs.get("level", xr_var.attrs.get("GRIB_level", 0)))
                    if lev <= 0 or values.ndim != 2:
                        continue
                    col = [None] * n_points
                    if inb_idx.size:
                        vv = (
                            w00 * values[i0, j0] + w01 * values[i0, j1]
                            + w10 * values[i1, j0] + w11 * values[i1, j1]
                        )
                        for k, pt in enumerate(inb_idx):
                            v = vv[k]
                            if not np.isnan(v):
                                col[pt] = float(v)
                    level_values[lev] = col

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
        bounds_for_field,
        interpolate_model_to_pressure_levels,
    )

    if target_pressures_hpa is None:
        target_pressures_hpa = TARGET_PRESSURE_LEVELS_HPA

    n_points = len(latitudes)

    # Step 1: Decode P (pressure) variable — needed for vertical interpolation.
    # pop() (not get()) so the dict releases the compressed bytes as each
    # variable is consumed — otherwise the whole ~260 MB/hour input set stays
    # resident and the later `del` frees nothing. (#441 efficiency)
    p_bytes = var_bytes.pop("p", b"")
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
        raw = var_bytes.pop(var_name, b"")  # release bytes as consumed (#441)
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
                bounds=bounds_for_field(field_key),
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
        bounds_for_field,
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
        # indexpath="" disables cfgrib's on-disk .idx sidecar. These are
        # one-shot temp files: only the .grib2 gets unlinked, so a written
        # .idx would be orphaned (604 found in the wild). (#441 efficiency)
        datasets = cfgrib.open_datasets(str(tmp_path), backend_kwargs={"indexpath": ""})

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
                    bounds=bounds_for_field(field_key),
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
        # indexpath="" disables cfgrib's on-disk .idx sidecar. These are
        # one-shot temp files: only the .grib2 gets unlinked, so a written
        # .idx would be orphaned (604 found in the wild). (#441 efficiency)
        datasets = cfgrib.open_datasets(str(tmp_path), backend_kwargs={"indexpath": ""})
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


def _opt_float(raw: dict[str, float], key: str) -> float | None:
    """Pass a raw GRIB value through as a float, or None when absent.

    Shared by the ICON-EU and ECMWF cloud-diagnostic builders for fields that
    need no unit conversion (J/kg, °C). (#283)
    """
    val = raw.get(key)
    return float(val) if val is not None else None


def _normalize_model_cin(
    raw: dict[str, float],
    key: str,
    *,
    drop_at_or_below: float | None = None,
    drop_at_or_above: float | None = None,
) -> float | None:
    """Normalize a model CIN magnitude to the app's internal signed convention.

    Both ECMWF ``mlcin100`` and DWD ICON ``CIN_ML`` report convective
    inhibition as a NON-NEGATIVE magnitude in J/kg (larger = stronger cap;
    verified against cached GRIB — ECMWF values ≥ 0, ICON up to +1585 J/kg).
    The rest of the app — MetPy sounding CIN and the convective suppression
    gate (``eff_cin < -200``) — uses the opposite sign: CIN is NEGATIVE, more
    negative = stronger cap. Passing the raw positive magnitude straight
    through meant genuine strong model caps never suppressed. (#441 finding #2)

    Normalization:
    - Provider "undefined" sentinels are dropped to None: ECMWF ``9999``
      (``drop_at_or_above=9998``; usually already masked to NaN by cfgrib, this
      is a defensive net) and ICON ``-999.9`` (``drop_at_or_below=-900``).
    - Any residual negative (ICON packed-zero ≈ -0.025, or an interpolation
      artifact where a sentinel bled into a neighbour) is floored to 0 — no
      cap — rather than read as an implausibly strong one.
    - The remaining non-negative magnitude is negated to the internal
      convention: ``+1585 → -1585``.
    """
    val = raw.get(key)
    if val is None:
        return None
    if drop_at_or_below is not None and val <= drop_at_or_below:
        return None
    if drop_at_or_above is not None and val >= drop_at_or_above:
        return None
    return -max(0.0, float(val))


def _k_index_to_c(raw: dict[str, float], key: str) -> float | None:
    """K-index normalized to °C.

    ECMWF GRIB2 delivers ``kx`` in Kelvin: the K-index formula
    (T₈₅₀+Td₈₅₀+Td₇₀₀ − T₅₀₀ − T₇₀₀) has 3 positive and 2 negative temperature
    terms, so a Kelvin computation carries a +273.15 offset. A real K-index in °C
    never exceeds ~45, while a Kelvin one is always >100 — so a value above 100 is
    unambiguously Kelvin and is converted. This is robust whether the source emits
    K or °C (Total Totals is immune: its 2 positive / 2 negative terms cancel the
    offset). (#283 review)

    Source unit reference: ECMWF GRIB2 parameter ``kx`` (paramId 260121,
    "K index") is documented in Kelvin in the ECMWF parameter database
    (https://codes.ecmwf.int/grib/param-db/260121). If that encoding ever
    changes, re-verify here rather than relying solely on the >100 heuristic.
    """
    val = raw.get(key)
    if val is None:
        return None
    val = float(val)
    return val - 273.15 if val > 100.0 else val


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
    ml_cape = _opt_float(raw, "ml_cape_jkg")  # instantaneous (#283)
    # ICON CIN_ML is a positive magnitude with -999.9 as the undefined
    # sentinel → convert to internal negative convention. (#441 finding #2)
    ml_cin = _normalize_model_cin(raw, "ml_cin_jkg", drop_at_or_below=-900.0)

    # Only create diagnostics if at least one field is populated
    has_any = (
        ceiling_ft is not None
        or convective_base_ft is not None
        or convective_top_ft is not None
        or low_cover is not None
        or mid_cover is not None
        or high_cover is not None
        or total_cover is not None
        or ml_cape is not None
        or ml_cin is not None
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
        ml_cape_jkg=ml_cape,
        ml_cin_jkg=ml_cin,
    )


# ---------------------------------------------------------------------------
# ICON-D2 explicit-convection decode (#462) — eccodes per-message path
# ---------------------------------------------------------------------------
#
# D2's convection-permitting storm fields CANNOT go through the cfgrib path
# above: several of them pack multiple sub-hourly GRIB messages per file and
# the hourly products must be selected/constructed by validity — never merged
# (verified against the live feed 2026-07-21, eccodes inspection of 00z f012):
#
# - dbz_ctmax / lpi_max / w_ctmax / uh_max_med: ONE message per file,
#   stepType=max over the PREVIOUS hour (stepRange "11-12"-style). Interval
#   maxima attach to (H−1, H].
# - echotop (shortName min_pres, Pa, stepType=min, −999 Pa = valid "no echo"):
#   FOUR 15-min messages per file, valid :00/:15/:30/:45. The hourly echo top
#   for flight hour H is CONSTRUCTED as the min pressure over the four quarter
#   windows ending at H−45/H−30/H−15/H — three messages from file f(H−1) plus
#   the first of file f(H) — aligned with dbz_ctmax's (H−1, H] interval.
# - grau_gsp (shortName tgrp, kg/m², stepType=accum since init): ONE on-the-
#   hour message (stepUnits=hours) plus three quarter-hour accums we must NOT
#   use. De-accumulated PER CELL (accum_H − accum_{H−1}, clipped ≥ 0), then
#   corridor-max — like rain_con but on the grid, before reduction.
#
# Sentinel discipline (None ≠ 0, #421): the GRIB BITMAP alone marks missing
# data (masked domain corners, ~16.7% of cells on every field). Encoded quiet
# values are real data: −150 dBZ = genuinely quiet reflectivity floor (kept —
# it participates in the corridor max), −999 Pa = "no 18 dBZ echo this
# quarter" (excluded from the pressure min, counted as a valid cell).
# Completeness flags below are driven by bitmap-valid corridor fractions,
# never by decoded values.
#
# Corridor extrema: per-point bilinear sampling defeats a 2.2 km model — a
# cell between route points vanishes. Each channel is reduced over a disc of
# D2_CORRIDOR_RADIUS_KM around each route point; values are corridor extrema,
# not centreline values. dbz_ctmax additionally keeps the centreline bilinear
# value so a widespread stratiform shield (point ≈ corridor max) is
# distinguishable from a discrete cell (max ≫ point).

D2_CORRIDOR_RADIUS_NM = 10.0
D2_CORRIDOR_RADIUS_KM = D2_CORRIDOR_RADIUS_NM * 1.852

_D2_EXPLICIT_SHORTNAMES = frozenset({
    "dbz_ctmax", "min_pres", "lpi_max", "w_ctmax", "uh_max_med", "tgrp",
})
_D2_MISSING_VALUE = 9999.0          # eccodes missingValue on these products
_D2_ECHOTOP_NO_ECHO_PA = -999.0     # valid "no ≥18 dBZ echo" sentinel
_D2_CORRIDOR_MIN_VALID_FRAC = 0.5   # corridor coverage required per channel
_KM_PER_DEG_LAT = 111.195


class _D2Message(NamedTuple):
    """One decoded explicit-convection GRIB message as a 2-D grid."""
    var: str                # canonical field key (dbz_ctmax / echotop / ...)
    values: "np.ndarray"    # (n_lat, n_lon) float64, missing → NaN
    valid_minutes: int      # validity offset from run init, in minutes
    on_hour_accum: bool     # grau_gsp only: stepUnits=hours since-init accum


def _d2_valid_offset_minutes(gid) -> int:
    """(validityDate/validityTime − dataDate/dataTime) in minutes (eccodes)."""
    import eccodes

    def _ymdh(date_key: str, time_key: str) -> datetime:
        d = int(eccodes.codes_get(gid, date_key))
        t = int(eccodes.codes_get(gid, time_key))  # HHMM (e.g. 1115 = 11:15)
        return datetime(
            d // 10000, (d // 100) % 100, d % 100,
            t // 100, t % 100, tzinfo=timezone.utc,
        )

    delta = _ymdh("validityDate", "validityTime") - _ymdh("dataDate", "dataTime")
    return int(delta.total_seconds() // 60)


def _read_d2_explicit_messages(
    grib_bytes: bytes,
) -> "tuple[list[_D2Message], np.ndarray | None, np.ndarray | None, bytes]":
    """Split a D2 single-level blob into explicit messages + a standard remainder.

    Returns ``(messages, lat_vec, lon_vec, standard_bytes)``: the decoded
    explicit-convection messages (values as NaN-masked grids), the shared grid
    axes (None when no explicit message was found), and the concatenation of
    every NON-explicit message (ceiling/clc*/cape_ml/cin_ml) for the existing
    cfgrib path — so multi-message storm fields never reach cfgrib's merge
    logic. Grid axes come from the first explicit message's eccodes-computed
    latitudes/longitudes arrays (SIGNED, monotonic: −3.94…20.34 °E), NOT from
    the raw grid keys (which encode the first longitude as 356.06°).
    """
    import eccodes
    import numpy as np

    messages: list[_D2Message] = []
    standard = bytearray()
    lat_vec = lon_vec = None
    if not grib_bytes:
        return messages, None, None, b""

    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
        tmp.write(grib_bytes)
        tmp_path = Path(tmp.name)
    try:
        with open(tmp_path, "rb") as f:
            while True:
                gid = eccodes.codes_grib_new_from_file(f)
                if gid is None:
                    break
                try:
                    short = str(eccodes.codes_get(gid, "shortName")).lower()
                    if short not in _D2_EXPLICIT_SHORTNAMES:
                        standard.extend(eccodes.codes_get_message(gid))
                        continue
                    ni = int(eccodes.codes_get(gid, "Ni"))
                    nj = int(eccodes.codes_get(gid, "Nj"))
                    if lat_vec is None:
                        lats = np.asarray(
                            eccodes.codes_get_array(gid, "latitudes"), dtype=np.float64,
                        )
                        lons = np.asarray(
                            eccodes.codes_get_array(gid, "longitudes"), dtype=np.float64,
                        )
                        lat_vec = lats.reshape(nj, ni)[:, 0]
                        lon_vec = lons.reshape(nj, ni)[0, :]
                    values = np.asarray(
                        eccodes.codes_get_values(gid), dtype=np.float64,
                    ).reshape(nj, ni)
                    missing = float(eccodes.codes_get(gid, "missingValue"))
                    values = np.where(values == missing, np.nan, values)

                    var = {
                        "dbz_ctmax": "dbz_ctmax",
                        "min_pres": "echotop",
                        "lpi_max": "lpi_max",
                        "w_ctmax": "w_ctmax",
                        "uh_max_med": "uh_max_med",
                        "tgrp": "grau_gsp",
                    }[short]
                    step_units = int(eccodes.codes_get(gid, "stepUnits"))
                    step_type = str(eccodes.codes_get(gid, "stepType"))
                    messages.append(_D2Message(
                        var=var,
                        values=values,
                        valid_minutes=_d2_valid_offset_minutes(gid),
                        on_hour_accum=(step_type == "accum" and step_units == 1),
                    ))
                finally:
                    eccodes.codes_release(gid)
    finally:
        tmp_path.unlink(missing_ok=True)
    return messages, lat_vec, lon_vec, bytes(standard)


def read_icon_d2_grid_mask(
    grib_bytes: bytes,
) -> "tuple[np.ndarray, np.ndarray, np.ndarray] | None":
    """Grid axes + bitmap validity mask from the first message of a D2 blob.

    Used by the domain-gate hardening (#462): the delivered regular-lat-lon
    grid carries ~16.7% bitmap-masked cells (the native rotated domain's
    corners), and this mask tests the ACTUAL delivered product. Returns
    ``(lat_vec, lon_vec, valid_mask)`` with valid_mask True where the cell is
    a real model cell, or None when undecodable.
    """
    import eccodes
    import numpy as np

    if not grib_bytes:
        return None
    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
        tmp.write(grib_bytes)
        tmp_path = Path(tmp.name)
    try:
        with open(tmp_path, "rb") as f:
            gid = eccodes.codes_grib_new_from_file(f)
            if gid is None:
                return None
            try:
                ni = int(eccodes.codes_get(gid, "Ni"))
                nj = int(eccodes.codes_get(gid, "Nj"))
                lats = np.asarray(eccodes.codes_get_array(gid, "latitudes"))
                lons = np.asarray(eccodes.codes_get_array(gid, "longitudes"))
                values = np.asarray(eccodes.codes_get_values(gid), dtype=np.float64)
                missing = float(eccodes.codes_get(gid, "missingValue"))
                return (
                    lats.reshape(nj, ni)[:, 0],
                    lons.reshape(nj, ni)[0, :],
                    (values.reshape(nj, ni) != missing),
                )
            finally:
                eccodes.codes_release(gid)
    finally:
        tmp_path.unlink(missing_ok=True)


def _d2_corridor_cells(
    lat_vec: "np.ndarray",
    lon_vec: "np.ndarray",
    lat: float,
    lon: float,
    radius_km: float = D2_CORRIDOR_RADIUS_KM,
) -> "tuple[np.ndarray, np.ndarray, bool] | None":
    """Grid cells within *radius_km* of a route point.

    Returns ``(rows, cols, clipped)`` — index arrays of the corridor disc and
    whether the disc was cut by the grid boundary — or None when the point is
    outside the grid entirely. A clipped corridor is incomplete by
    construction: the extremum would be computed over a partial
    neighbourhood, so callers treat completeness as False.
    """
    import numpy as np

    frac_lat, lat_ok = _frac_grid_indices(lat_vec, [lat])
    frac_lon, lon_ok = _frac_grid_indices(lon_vec, [lon])
    if not (bool(lat_ok[0]) and bool(lon_ok[0])):
        return None

    ci = float(frac_lat[0])  # lat axis (rows)
    cj = float(frac_lon[0])  # lon axis (cols)
    dlat_km = abs(float(lat_vec[-1] - lat_vec[0])) / max(len(lat_vec) - 1, 1) * _KM_PER_DEG_LAT
    dlon_km = (
        abs(float(lon_vec[-1] - lon_vec[0])) / max(len(lon_vec) - 1, 1)
        * _KM_PER_DEG_LAT * math.cos(math.radians(lat))
    )
    if dlat_km <= 0 or dlon_km <= 0:
        return None

    ri = int(math.ceil(radius_km / dlat_km))
    rj = int(math.ceil(radius_km / dlon_km))
    n_rows, n_cols = len(lat_vec), len(lon_vec)
    i0, i1 = int(math.floor(ci)) - ri, int(math.ceil(ci)) + ri + 1
    j0, j1 = int(math.floor(cj)) - rj, int(math.ceil(cj)) + rj + 1
    clipped = i0 < 0 or j0 < 0 or i1 > n_rows or j1 > n_cols
    i0, j0 = max(i0, 0), max(j0, 0)
    i1, j1 = min(i1, n_rows), min(j1, n_cols)
    if i0 >= i1 or j0 >= j1:
        return None

    ii = np.arange(i0, i1)[:, None]
    jj = np.arange(j0, j1)[None, :]
    dist2 = ((ii - ci) * dlat_km) ** 2 + ((jj - cj) * dlon_km) ** 2
    sel = dist2 <= radius_km ** 2
    rows = np.broadcast_to(ii, dist2.shape)[sel]
    cols = np.broadcast_to(jj, dist2.shape)[sel]
    if rows.size == 0:
        return None
    return rows, cols, clipped


def _d2_corridor_stats(
    values: "np.ndarray",
    rows: "np.ndarray",
    cols: "np.ndarray",
) -> "tuple[np.ndarray, float]":
    """Valid corridor values + valid fraction for one point/channel."""
    import numpy as np

    cell_values = values[rows, cols]
    valid = ~np.isnan(cell_values)
    frac = float(valid.sum()) / float(cell_values.size) if cell_values.size else 0.0
    return cell_values[valid], frac


def corridor_fully_valid(
    lat_vec: "np.ndarray",
    lon_vec: "np.ndarray",
    valid_mask: "np.ndarray",
    lat: float,
    lon: float,
    radius_km: float = D2_CORRIDOR_RADIUS_KM,
) -> bool:
    """True when a route point's ENTIRE corridor disc lies in valid grid cells.

    Domain-gate hardening (#462): a route can pass the D2 bbox gate yet clip
    the delivered grid's bitmap-masked corners (~16.7% of cells) or its outer
    boundary — corridor extrema there would be computed over partial
    neighbourhoods. The gate requires the full disc present and unmasked.
    """
    cells = _d2_corridor_cells(lat_vec, lon_vec, lat, lon, radius_km)
    if cells is None:
        return False
    rows, cols, clipped = cells
    if clipped:
        return False
    return bool(valid_mask[rows, cols].all())


def _d2_centreline_value(
    values: "np.ndarray",
    lat_vec: "np.ndarray",
    lon_vec: "np.ndarray",
    lat: float,
    lon: float,
) -> float | None:
    """Bilinear value at the exact route point (None on any masked corner)."""
    import numpy as np

    bw = _bilinear_grid_weights(lat_vec, lon_vec, [lat], [lon])
    if bw is None or bw.inb_idx.size == 0:
        return None
    corners = np.array([
        values[bw.i0[0], bw.j0[0]], values[bw.i0[0], bw.j1[0]],
        values[bw.i1[0], bw.j0[0]], values[bw.i1[0], bw.j1[0]],
    ])
    if np.isnan(corners).any():
        return None
    v = (
        bw.w00[0] * corners[0] + bw.w01[0] * corners[1]
        + bw.w10[0] * corners[2] + bw.w11[0] * corners[3]
    )
    return float(v)


def _isa_pressure_to_ft(pressure_pa: float) -> float:
    """Pressure → altitude via the ICAO standard atmosphere (single datum).

    The echo top is a corridor extremum — its cell is generally NOT under the
    route centreline, so converting through the centreline sounding column
    would borrow a datum from the wrong place; ISA is reproducible and good
    to ~±1000 ft at storm-top levels, ample for a depth/character indicator
    that is explicitly never used for overfly clearance (#462).
    """
    if pressure_pa <= 0:
        return 0.0
    if pressure_pa >= 22632.06:  # troposphere (to 11 km)
        h_m = 44330.8 * (1.0 - (pressure_pa / 101325.0) ** 0.1902632)
    else:  # isothermal stratosphere (11–20 km)
        h_m = 11000.0 + 6341.62 * math.log(22632.06 / pressure_pa)
    return h_m * _M_TO_FT


def decode_icon_d2_explicit(
    grib_bytes: bytes,
    prev_grib_bytes: bytes | None,
    fhour: int,
    latitudes: list[float],
    longitudes: list[float],
) -> "tuple[list[dict[str, float | None]], bytes]":
    """Decode D2 explicit-convection fields to corridor extrema per route point.

    Args:
        grib_bytes: This forecast hour's single-level blob (all variables).
        prev_grib_bytes: The PREVIOUS step's blob — supplies the three leading
            echotop quarters and the grau_gsp predecessor accumulation. None
            when no predecessor was fetched (hour 0 or a failed lead file).
        fhour: This blob's forecast hour H. The decoded products describe the
            ``(H−1, H]`` interval (interval-max semantics, #462 rule 3).
        latitudes/longitudes: Route points (signed −180…+180 lons).

    Returns:
        ``(raws, standard_bytes)`` — one raw dict per route point (corridor
        extrema + per-channel valid fractions + corridor-clipped flag), and
        the non-explicit remainder of THIS hour's blob for the cfgrib path.
        A route point outside the grid gets an all-None dict with
        ``corridor_clipped=True`` (unknown, never quiet).
    """
    import numpy as np

    n_points = len(latitudes)
    messages, lat_vec, lon_vec, standard = _read_d2_explicit_messages(grib_bytes)
    prev_messages: list[_D2Message] = []
    if prev_grib_bytes:
        prev_messages, _, _, _ = _read_d2_explicit_messages(prev_grib_bytes)

    raws: list[dict[str, float | None]] = [
        {
            "reflectivity_hour_max_dbz": None,
            "reflectivity_point_dbz": None,
            "dbz_valid_frac": 0.0,
            "echo_top_18dbz_pa": None,
            "echo_top_quarters_valid": 0,
            "lightning_potential_hour_max_jkg": None,
            "lpi_valid_frac": 0.0,
            "updraft_hour_max_ms": None,
            "w_valid_frac": 0.0,
            "updraft_helicity_2_5km_hour_max_m2s2": None,
            "uh_valid_frac": 0.0,
            "graupel_hour_mm": None,
            "grau_valid_frac": 0.0,
            "corridor_clipped": True,
        }
        for _ in range(n_points)
    ]
    if lat_vec is None:
        return raws, standard

    # Hour-max channels from THIS hour's file (one message each).
    hour_max: dict[str, np.ndarray] = {}
    # echotop quarter windows keyed by end-minute offset (both files).
    echo_quarters: dict[int, np.ndarray] = {}
    # grau_gsp on-the-hour accumulations keyed by whole-hour offset.
    grau_accums: dict[int, np.ndarray] = {}
    for msg in messages:
        if msg.var in ("dbz_ctmax", "lpi_max", "w_ctmax", "uh_max_med"):
            hour_max[msg.var] = msg.values
        elif msg.var == "echotop":
            echo_quarters[msg.valid_minutes] = msg.values
        elif msg.var == "grau_gsp" and msg.on_hour_accum:
            grau_accums[msg.valid_minutes // 60] = msg.values
    for msg in prev_messages:
        if msg.var == "echotop":
            echo_quarters.setdefault(msg.valid_minutes, msg.values)
        elif msg.var == "grau_gsp" and msg.on_hour_accum:
            grau_accums.setdefault(msg.valid_minutes // 60, msg.values)

    # dbz_ctmax's interval is (H−1, H]; the four echo-top quarter windows
    # ENDING in that interval are H−45/H−30/H−15/H (minutes since init).
    quarter_ends = [fhour * 60 - 45, fhour * 60 - 30, fhour * 60 - 15, fhour * 60]

    # Per-cell graupel de-accumulation (both accums must exist; clip ≥ 0 so a
    # reset accumulation never yields negative rates — rain_con precedent).
    grau_hourly: np.ndarray | None = None
    accum_h = grau_accums.get(fhour)
    accum_prev = grau_accums.get(fhour - 1)
    if accum_h is not None and accum_prev is not None:
        grau_hourly = np.clip(accum_h - accum_prev, 0.0, None)

    for pt in range(n_points):
        lat, lon = latitudes[pt], longitudes[pt]
        cells = _d2_corridor_cells(lat_vec, lon_vec, lat, lon)
        if cells is None:
            continue  # all-None + corridor_clipped=True (already initialised)
        rows, cols, clipped = cells
        raw = raws[pt]
        raw["corridor_clipped"] = clipped

        grid = hour_max.get("dbz_ctmax")
        if grid is not None:
            valid, frac = _d2_corridor_stats(grid, rows, cols)
            raw["dbz_valid_frac"] = frac
            if valid.size:
                # −150 dBZ is a valid quiet floor — it participates in the max.
                raw["reflectivity_hour_max_dbz"] = float(np.max(valid))
            raw["reflectivity_point_dbz"] = _d2_centreline_value(
                grid, lat_vec, lon_vec, lat, lon,
            )

        grid = hour_max.get("lpi_max")
        if grid is not None:
            valid, frac = _d2_corridor_stats(grid, rows, cols)
            raw["lpi_valid_frac"] = frac
            if valid.size:
                raw["lightning_potential_hour_max_jkg"] = float(np.max(valid))

        grid = hour_max.get("w_ctmax")
        if grid is not None:
            valid, frac = _d2_corridor_stats(grid, rows, cols)
            raw["w_valid_frac"] = frac
            if valid.size:
                raw["updraft_hour_max_ms"] = float(np.max(valid))

        grid = hour_max.get("uh_max_med")
        if grid is not None:
            valid, frac = _d2_corridor_stats(grid, rows, cols)
            raw["uh_valid_frac"] = frac
            if valid.size:
                # argmax |uh| but store the SIGNED value (rotation sense kept).
                raw["updraft_helicity_2_5km_hour_max_m2s2"] = float(
                    valid[np.argmax(np.abs(valid))]
                )

        if grau_hourly is not None:
            valid, frac = _d2_corridor_stats(grau_hourly, rows, cols)
            raw["grau_valid_frac"] = frac
            if valid.size:
                raw["graupel_hour_mm"] = float(np.max(valid))

        # Hourly echo top: corridor min pressure per quarter (sentinel −999 Pa
        # excluded — it is "no echo", not a high top), then the min across the
        # four quarters (highest echo of the hour). Quarters missing from both
        # files simply don't count — completeness records how many were valid.
        quarter_mins: list[float] = []
        quarters_valid = 0
        for q_end in quarter_ends:
            q_grid = echo_quarters.get(q_end)
            if q_grid is None:
                continue
            cell_values, frac = _d2_corridor_stats(q_grid, rows, cols)
            if frac < _D2_CORRIDOR_MIN_VALID_FRAC:
                continue
            real = cell_values[cell_values > _D2_ECHOTOP_NO_ECHO_PA]
            quarters_valid += 1
            if real.size:
                quarter_mins.append(float(np.min(real)))
        raw["echo_top_quarters_valid"] = quarters_valid
        if quarters_valid == len(quarter_ends) and quarter_mins:
            # Only a FULL set of quarters yields the hourly value — a partial
            # min would understate storm depth while looking complete.
            raw["echo_top_18dbz_pa"] = min(quarter_mins)

    return raws, standard


def build_icon_d2_explicit_diagnostics(
    raw: dict[str, float | None],
    lead_hours: float | None,
) -> "NWPExplicitConvectiveDiagnostics":
    """Convert one corridor-extrema raw dict into the payload model (#462).

    Completeness is bitmap-driven per channel: a corridor needs ≥ 50% valid
    cells (and no grid-boundary clipping) for its channel to count as
    complete; quiet floors (−150 dBZ, −999 Pa) are valid data, never missing.
    """
    from weatherbrief.models.analysis import NWPExplicitConvectiveDiagnostics

    clipped = bool(raw.get("corridor_clipped"))

    def _enough(frac_key: str) -> bool:
        return not clipped and (raw.get(frac_key) or 0.0) >= _D2_CORRIDOR_MIN_VALID_FRAC

    detection_complete = _enough("dbz_valid_frac")
    strength_complete = (
        _enough("lpi_valid_frac") and _enough("w_valid_frac") and _enough("grau_valid_frac")
    )
    echo_top_complete = not clipped and raw.get("echo_top_quarters_valid") == 4

    echo_top_pa = raw.get("echo_top_18dbz_pa")
    echo_top_ft = (
        round(_isa_pressure_to_ft(echo_top_pa)) if echo_top_pa is not None else None
    )

    return NWPExplicitConvectiveDiagnostics(
        source="icon_d2",
        reflectivity_hour_max_dbz=raw.get("reflectivity_hour_max_dbz"),
        reflectivity_point_dbz=raw.get("reflectivity_point_dbz"),
        echo_top_18dbz_ft=echo_top_ft,
        lightning_potential_hour_max_jkg=raw.get("lightning_potential_hour_max_jkg"),
        updraft_hour_max_ms=raw.get("updraft_hour_max_ms"),
        updraft_helicity_2_5km_hour_max_m2s2=raw.get(
            "updraft_helicity_2_5km_hour_max_m2s2"
        ),
        graupel_hour_mm=raw.get("graupel_hour_mm"),
        lead_hours=lead_hours,
        detection_complete=detection_complete,
        strength_complete=strength_complete,
        echo_top_complete=echo_top_complete,
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
_RD_DRY_AIR = 287.05  # specific gas constant for dry air, J/(kg·K)


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

    # Geopotential height: prefer delivered gh (gpm ≈ m), fall back to z/g.
    # ECMWF delivers z only at level 1 by catalogue design; for pre-amendment
    # archives or ICON the hypsometric fill below is the final safety net.
    gh_m = raw.get("geopotential_height_m")
    if gh_m is not None:
        out["geopotential_height_m"] = gh_m
    else:
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
        rho = (pressure_hpa * 100.0) / (_RD_DRY_AIR * t_k)
        out["vertical_velocity_pa_s"] = -rho * _G * w_ms

    return out


def build_pressure_levels_from_grib(
    point_data: dict[int, dict[str, float]],
) -> list:
    """Build PressureLevelData objects from decoded ECMWF GRIB data.

    Converts raw GRIB fields (temperature in K, wind in m/s, geopotential
    in m²/s²) to PressureLevelData units and returns a sorted list (highest
    pressure / lowest altitude first).

    Where ``geopotential_height_m`` is missing after ``_convert_raw_sounding``
    (e.g. ICON model levels, which DWD never ships geopotential for, or
    pre-amendment ECMWF archives), it is derived via the hypsometric equation
    from temperature + pressure. Current ECMWF runs deliver ``gh`` on every
    pressure level, so this fill is a no-op for them.
    """
    from weatherbrief.models.analysis import PressureLevelData

    # First pass: convert each level's raw fields to output units.
    converted_by_p: dict[int, dict[str, float]] = {}
    for p_hpa, raw_fields in point_data.items():
        converted = _convert_raw_sounding(raw_fields, p_hpa)
        if converted:
            converted_by_p[p_hpa] = converted

    _fill_missing_geopotential_height(converted_by_p)

    return [
        PressureLevelData(pressure_hpa=p_hpa, **converted)
        for p_hpa, converted in sorted(converted_by_p.items(), reverse=True)
    ]


def _fill_missing_geopotential_height(
    converted_by_p: dict[int, dict[str, float]],
) -> None:
    """Derive ``geopotential_height_m`` via the hypsometric equation.

    Mutates the per-level dicts in-place. Iterates from highest pressure
    (surface) to lowest (TOA), anchoring the column at the surface level
    using ISA if no real geopotential is present, then integrating upward
    with layer-mean temperature::

        Δz = (R_d / g) · T_mean · ln(p_lower / p_upper)

    Levels that already carry a real ``geopotential_height_m`` are preserved
    (so if the feed starts delivering ``z`` at more levels, those values win).

    No-op if every level already has geopotential, or if temperature is
    missing anywhere in the column (can't integrate reliably).

    Used as a safety net when the source doesn't deliver geopotential on
    every pressure level — e.g. ICON model levels (DWD never ships it) and
    pre-amendment ECMWF archives (only ``z`` at level 1). For current ECMWF
    full-coverage runs the ``gh`` field is decoded directly and the
    ``all-present`` guard below makes this function a no-op.

    Two known simplifications apply when this codepath is taken:
    (a) layer-mean uses dry-air temperature instead of virtual temperature
    — ~0.5–1% underestimate in warm moist air, accumulating ~30–50 m
    surface→500 hPa; (b) the ISA anchor at the surface-most pressure level
    is not terrain-aware — fine over flat Europe, off by the terrain-vs-ISA
    delta at high-elevation airfields. The right fix for (b) would be to
    anchor using surface ``z`` + ``sp`` from the a1 GRIB.
    """
    import math

    from weatherbrief.models.analysis import pressure_hpa_to_altitude_m

    if not converted_by_p:
        return

    if all(d.get("geopotential_height_m") is not None for d in converted_by_p.values()):
        return

    if not all(d.get("temperature_c") is not None for d in converted_by_p.values()):
        return

    # Surface (highest pressure) → TOA (lowest pressure).
    sorted_p = sorted(converted_by_p.keys(), reverse=True)

    # Anchor: use real value if present at the surface-most level, else ISA.
    anchor = converted_by_p[sorted_p[0]]
    if anchor.get("geopotential_height_m") is None:
        anchor["geopotential_height_m"] = pressure_hpa_to_altitude_m(sorted_p[0])

    for i in range(1, len(sorted_p)):
        p_lower = sorted_p[i - 1]
        p_upper = sorted_p[i]
        lower = converted_by_p[p_lower]
        upper = converted_by_p[p_upper]

        if upper.get("geopotential_height_m") is not None:
            continue

        t_lower_k = lower["temperature_c"] + 273.15
        t_upper_k = upper["temperature_c"] + 273.15
        t_mean_k = 0.5 * (t_lower_k + t_upper_k)

        dz = (_RD_DRY_AIR * t_mean_k / _G) * math.log(p_lower / p_upper)
        upper["geopotential_height_m"] = lower["geopotential_height_m"] + dz


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
    convective_top_ft = _m_to_ft("convective_cloud_top_m")
    freezing_level_ft = _m_to_ft("freezing_level_m")
    low_cover = _frac_to_pct("low_cover_frac")
    mid_cover = _frac_to_pct("mid_cover_frac")
    high_cover = _frac_to_pct("high_cover_frac")
    total_cover = _frac_to_pct("total_cover_frac")
    # Native stability indices (instantaneous — surfaced directly). Convective
    # precip (cp) is accumulated since init, so its per-hour rate is computed by
    # step-difference in the ECMWF merge loop, not here. (#283 Phase 2)
    # kx arrives in Kelvin → normalized to °C (Total Totals is offset-immune).
    k_index = _k_index_to_c(raw, "k_index_c")
    total_totals = _opt_float(raw, "total_totals_c")
    ml_cape = _opt_float(raw, "ml_cape_jkg")
    # ECMWF mlcin100 is a positive magnitude with 9999 as the missing
    # sentinel (usually already NaN-masked by cfgrib) → internal negative. (#441)
    ml_cin = _normalize_model_cin(raw, "ml_cin_jkg", drop_at_or_above=9998.0)

    has_any = (
        ceiling_ft is not None
        or cloud_base_ft is not None
        or convective_top_ft is not None
        or freezing_level_ft is not None
        or low_cover is not None
        or mid_cover is not None
        or high_cover is not None
        or total_cover is not None
        or k_index is not None
        or total_totals is not None
        or ml_cape is not None
        or ml_cin is not None
    )
    if not has_any:
        return None

    return NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=low_cover, base_ft=cloud_base_ft),
        mid=NWPCloudLayerDiag(cover_pct=mid_cover),
        high=NWPCloudLayerDiag(cover_pct=high_cover),
        total_cover_pct=total_cover,
        ceiling_ft=ceiling_ft,
        convective_top_ft=convective_top_ft,
        freezing_level_ft=freezing_level_ft,
        k_index=k_index,
        total_totals=total_totals,
        ml_cape_jkg=ml_cape,
        ml_cin_jkg=ml_cin,
    )


def build_ecmwf_surface_snapshot(raw: dict[str, float]) -> dict[str, float | None]:
    """Map ECMWF a1 raw fields into the standalone-snapshot schema.

    Input ``raw`` is the per-point dict returned by
    :func:`decode_ecmwf_surface_per_point` (cfgrib short-name → raw value
    in native ECMWF units). Output keys match the snapshot dict columns
    consumed by ``_store_snapshots`` in ``standalone_verification``.

    Unit conversions:
    - K → °C (temperature, dewpoint)
    - m/s u/v → kt speed + meteorological "from" direction
    - m/s gust → kt
    - m water-equivalent → mm water (precip) and cm snow (snowfall, ×1000
      assumes a 10:1 snow:water ratio, matching ECMWF's reference conversion)
    - 0–1 cloud fraction → 0–100 %

    ``total_precip_m`` and ``snowfall_m_we`` arrive accumulated since model
    init in ECMWF a1 — callers that need step-of-hour values must compute
    a step-difference upstream.
    """
    import math

    out: dict[str, float | None] = {
        "temperature_2m_c": None,
        "dewpoint_2m_c": None,
        "visibility_m": None,
        "wind_speed_10m_kt": None,
        "wind_direction_10m_deg": None,
        "wind_gusts_10m_kt": None,
        "precipitation_mm": None,
        "snowfall_cm": None,
        "cape_jkg": None,
        "cloud_cover_pct": None,
        "cloud_cover_low_pct": None,
        "surface_pressure_hpa": None,
        "nwp_ceiling_ft": None,
        "cloud_base_ft": None,
        "nwp_k_index": None,
        "nwp_total_totals": None,
    }

    if not raw:
        return out

    t_k = raw.get("temperature_2m_k")
    if t_k is not None:
        out["temperature_2m_c"] = t_k - 273.15

    d_k = raw.get("dewpoint_2m_k")
    if d_k is not None:
        out["dewpoint_2m_c"] = d_k - 273.15

    vis = raw.get("visibility_m")
    if vis is not None:
        out["visibility_m"] = vis

    u = raw.get("u_wind_10m_ms")
    v = raw.get("v_wind_10m_ms")
    if u is not None and v is not None:
        speed_ms = math.sqrt(u * u + v * v)
        out["wind_speed_10m_kt"] = speed_ms * _KT_PER_MS
        if speed_ms > 0.01:
            out["wind_direction_10m_deg"] = (math.atan2(-u, -v) * 180.0 / math.pi) % 360.0
        else:
            out["wind_direction_10m_deg"] = 0.0

    fg = raw.get("wind_gust_10m_ms")
    if fg is not None:
        out["wind_gusts_10m_kt"] = fg * _KT_PER_MS

    tp = raw.get("total_precip_m")
    if tp is not None:
        out["precipitation_mm"] = tp * 1000.0

    sf = raw.get("snowfall_m_we")
    if sf is not None:
        out["snowfall_cm"] = sf * 1000.0

    cape = raw.get("mucape_jkg")
    if cape is not None:
        out["cape_jkg"] = cape

    tcc = raw.get("total_cover_frac")
    if tcc is not None:
        out["cloud_cover_pct"] = tcc * 100.0

    lcc = raw.get("low_cover_frac")
    if lcc is not None:
        out["cloud_cover_low_pct"] = lcc * 100.0

    sp = raw.get("surface_pressure_pa")
    if sp is not None:
        out["surface_pressure_hpa"] = sp / 100.0

    ceil_m = raw.get("ceiling_m")
    if ceil_m is not None and 0 <= ceil_m < _ECMWF_NO_CLOUD_SENTINEL_M:
        out["nwp_ceiling_ft"] = ceil_m * _M_TO_FT

    cbh_m = raw.get("cloud_base_height_m")
    if cbh_m is not None and 0 <= cbh_m < _ECMWF_NO_CLOUD_SENTINEL_M:
        out["cloud_base_ft"] = cbh_m * _M_TO_FT

    # Native convective indices for the character advisory (#294). Derived from
    # the same decoded kx/totalx as the cloud-diag K/TT (#283): kx is delivered
    # in Kelvin, so normalize to °C via _k_index_to_c — feeding it raw would make
    # the K≥40 character nudge fire unconditionally. Total Totals is offset-immune.
    kx = _k_index_to_c(raw, "k_index_c")
    if kx is not None:
        out["nwp_k_index"] = kx
    totalx = raw.get("total_totals_c")
    if totalx is not None:
        out["nwp_total_totals"] = totalx

    return out
