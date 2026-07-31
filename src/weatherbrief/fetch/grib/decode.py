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
    # deg0l is AGL as delivered, but unlike ceil/cbh/hcct it feeds a field
    # (NWPCloudDiagnostics.freezing_level_ft) that every OTHER writer fills
    # with MSL. build_ecmwf_cloud_diagnostics normalizes it (#487).
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

# HRRR wrfprs sounding variables (#457): cfgrib var name → raw field.
# HRRR delivers dewpoint (DPT), omega (VVEL, already Pa/s) and geopotential
# height (HGT, gpm) DIRECTLY — no Magnus / −ρgw / hypsometric derivations.
# UGRD/VGRD are grid-relative (uvRelativeToGrid=1) and rotated to
# earth-relative after sampling (see decode_hrrr_pressure_per_point).
#
# Verified against a real wrfprs message (2026-07-31 18z f01):
# - CLMR (0/1/22) decodes as cfgrib var "clwmr".
# - CIMIXR (0/1/82) is an NCEP-local parameter eccodes 2.48 does not know —
#   it decodes as var name "unknown" (paramId 0). The "unknown" alias is
#   safe here because the sounding blob is byte-range-planned from
#   HRRR_SOUNDING_VARIABLES, so CIMIXR is the only unknown-named parameter
#   that can appear in it.
_HRRR_PRESSURE_VAR_MAP = {
    "t": "raw_temperature_k",
    "dpt": "raw_dewpoint_k",          # direct dewpoint — no Magnus derivation
    "r": "raw_relative_humidity_pct",
    "u": "raw_u_wind_m_s",            # grid-relative — rotated at decode
    "v": "raw_v_wind_m_s",
    "w": "raw_omega_pa_s",            # VVEL, already Pa/s — no −ρgw conversion
    "gh": "raw_geopotential_height_gpm",
    "clwmr": "cloud_liquid_water_kg_kg",   # cfgrib decodes CLMR as "clwmr" (verified)
    "clmr": "cloud_liquid_water_kg_kg",
    "cimixr": "ice_mixing_ratio_kg_kg",    # HRRR's ice name (verified in idx)
    "unknown": "ice_mixing_ratio_kg_kg",   # CIMIXR decodes as "unknown" (verified)
    "pres": "surface_pressure_pa",         # surface level only
    "sp": "surface_pressure_pa",
}

# Level key for HRRR surface PRES in decode_hrrr_pressure_per_point results.
# 0 hPa can never collide with a real isobaric level (HRRR tops out at 50 hPa),
# and build_pressure_levels_from_grib drops the entry: its only field,
# surface_pressure_pa, is not converted by _convert_raw_sounding, so the level
# converts to an empty dict and is skipped. The enrichment flow reads the
# surface pressure straight from the raw point dict, like
# _fill_ecmwf_orography does for the ECMWF surface decode.
_HRRR_SURFACE_PRESSURE_KEY = 0

# HRRR diagnostics (#457): (cfgrib var name, GRIB_typeOfLevel) → raw field.
# Level/typeOfLevel strings verified against real wrfprs messages.
_HRRR_DIAG_FIELD_MAP: dict[tuple[str, str], str] = {
    ("lcc", "lowCloudLayer"): "low_cover_pct",
    ("mcc", "middleCloudLayer"): "mid_cover_pct",
    ("hcc", "highCloudLayer"): "high_cover_pct",
    ("tcc", "atmosphere"): "total_cover_pct",
    ("gh", "cloudCeiling"): "ceiling_m",
    ("gh", "cloudBase"): "cloud_base_m",
    ("cape", "pressureFromGroundLayer"): "ml_cape_jkg",   # 180-0 mb above ground
    ("cin", "pressureFromGroundLayer"): "ml_cin_jkg",
    ("cape", "surface"): "sfc_cape_jkg",
    ("cin", "surface"): "sfc_cin_jkg",
    ("vis", "surface"): "visibility_m",
    ("gust", "surface"): "gust_ms",
}


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

    On a globe-spanning longitude axis the seam is closed by extending the axis
    one step past its end and wrapping the resulting column index back to 0
    (#484). Without this, targets between the last coordinate and 360° — i.e.
    route points just west of Greenwich, after ``lon % 360`` — fall outside
    ``_frac_grid_indices`` and are dropped as out-of-bounds. The xarray path
    fixes the same seam by appending a duplicate column; this path cannot,
    because the gather indexes the real data array directly, so it wraps the
    index instead. Regional grids are untouched.
    """
    import numpy as np

    H, W = lat_arr.size, lon_arr.size
    if H < 2 or W < 2:
        return None

    wrap_lon = _is_cyclic_longitude(lon_arr)
    lon_axis = np.append(lon_arr, lon_arr[0] + 360.0) if wrap_lon else lon_arr

    frac_lat, lat_ok = _frac_grid_indices(lat_arr, targets_lat)
    frac_lon, lon_ok = _frac_grid_indices(lon_axis, targets_lon)
    in_bounds = lat_ok & lon_ok
    inb_idx = np.flatnonzero(in_bounds)
    fl = frac_lat[in_bounds]
    fln = frac_lon[in_bounds]
    i0 = np.clip(np.floor(fl).astype(np.intp), 0, H - 2)
    j0 = np.clip(np.floor(fln).astype(np.intp), 0, lon_axis.size - 2)
    i1 = i0 + 1
    j1 = j0 + 1
    ai = fl - i0
    aj = fln - j0
    if wrap_lon:
        # Interpolation weights are already computed from the extended axis;
        # only the gather indices need folding back into the real array, where
        # column W is column 0.
        j0 = j0 % W
        j1 = j1 % W
    return _GridWeights(
        i0, j0, i1, j1,
        (1.0 - ai) * (1.0 - aj), (1.0 - ai) * aj, ai * (1.0 - aj), ai * aj,
        inb_idx,
    )


def _rotate_grid_wind_to_earth(u, v, lons_deg, lov_deg=262.5, cone_const=None):
    """Grid-relative → earth-relative for a Lambert conformal grid.

    θ = (λ − LoV) · k per point (k = sin(φc) = sin 38.5° when Latin1 == Latin2,
    which HRRR satisfies). Rotation is linear, so it commutes with the
    bilinear gather — applied per route point after sampling.

    Sign convention (verified against pyproj finite differences of the actual
    projection, test_rotation_matches_pyproj_projection_geometry): with
    x = ρ·sin θ, y = ρ0 − ρ·cos θ, WEST of LoV (θ < 0) the grid x-axis points
    NORTH of east — i.e. the grid→earth map is a rotation by −θ::

        u_e = u·cos θ + v·sin θ
        v_e = −u·sin θ + v·cos θ

    (The naïve "+sin/+cos" form sometimes quoted is the inverse, earth→grid,
    rotation.)
    """
    import numpy as np
    k = cone_const if cone_const is not None else math.sin(math.radians(38.5))
    dl = np.deg2rad((np.asarray(lons_deg, dtype=np.float64) - lov_deg + 180.0) % 360.0 - 180.0)
    a = dl * k
    ca, sa = np.cos(a), np.sin(a)
    u = np.asarray(u, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    return u * ca + v * sa, -u * sa + v * ca


# Lambert grid definition attrs (#457). cfgrib puts these GRIB_* keys on each
# data var's attrs (NOT on the dataset attrs, which only carry
# edition/centre/subCentre). Key names verified on a real wrfprs message.
_LAMBERT_GRID_ATTR_KEYS = {
    "lad": "GRIB_LaDInDegrees",
    "lov": "GRIB_LoVInDegrees",
    "latin1": "GRIB_Latin1InDegrees",
    "latin2": "GRIB_Latin2InDegrees",
    "dx": "GRIB_DxInMetres",
    "dy": "GRIB_DyInMetres",
    "lat0": "GRIB_latitudeOfFirstGridPointInDegrees",
    "lon0": "GRIB_longitudeOfFirstGridPointInDegrees",
}


def _lambert_grid_attrs(ds) -> dict[str, float]:
    """Lambert grid attributes of a cfgrib dataset ({} when not projected)."""
    attrs: dict[str, float] = {}
    for xr_var in ds.data_vars.values():
        for name, key in _LAMBERT_GRID_ATTR_KEYS.items():
            if name not in attrs and key in xr_var.attrs:
                try:
                    attrs[name] = float(xr_var.attrs[key])
                except (TypeError, ValueError):
                    pass
        if len(attrs) == len(_LAMBERT_GRID_ATTR_KEYS):
            break
    return attrs


def _lcc_project_points(
    grid_attrs: dict[str, float] | None,
    lats,
    lons,
) -> "tuple[np.ndarray, np.ndarray]":
    """Project lat/lon targets onto a Lambert grid's x/y axes (metres).

    Projection parameters come from ``grid_attrs`` (keys ``lad``/``lov``/
    ``latin1``/``latin2`` as read from the GRIB message); any missing key
    falls back to the verified HRRR CONUS projection
    (``hrrr_fetch.hrrr_projection``).
    """
    import numpy as np

    attrs = grid_attrs or {}
    if all(k in attrs for k in ("lad", "lov", "latin1", "latin2")):
        import pyproj
        proj = pyproj.Proj(
            proj="lcc",
            lat_0=attrs["lad"],
            lon_0=attrs["lov"],
            lat_1=attrs["latin1"],
            lat_2=attrs["latin2"],
            a=6371229.0,
            b=6371229.0,
        )
    else:
        from weatherbrief.fetch.grib.hrrr_fetch import hrrr_projection
        proj = hrrr_projection()
    xs, ys = proj(
        np.asarray(lons, dtype=np.float64),
        np.asarray(lats, dtype=np.float64),
    )
    return np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)


def _lambert_dataset_axes(ds) -> "tuple[np.ndarray, np.ndarray, dict[str, float]] | None":
    """(y_axis, x_axis, grid_attrs) for a cfgrib Lambert dataset, else None.

    A dataset is "projected" when it lacks 1-D lat/lon dims but has ``y``/``x``
    dims (cfgrib's names for lambert). cfgrib does NOT build 1-D axis
    coordinates for such datasets (lat/lon arrive as 2-D auxiliary coords), so
    the axes are rebuilt from the grid definition: the first grid point
    projected, then +Dx/+Dy per cell (jScansPositively=1 → y ascends with row
    index; verified against cfgrib's 2-D lat/lon to ~20 m ≪ 3 km spacing).
    Missing grid attrs fall back to the verified HRRR CONUS constants.
    """
    import numpy as np

    if "y" not in ds.sizes or "x" not in ds.sizes:
        return None
    from weatherbrief.fetch.grib.hrrr_fetch import HRRR_GRID
    grid_attrs = _lambert_grid_attrs(ds)
    lat0 = grid_attrs.get("lat0", HRRR_GRID.lat0)
    lon0 = grid_attrs.get("lon0", HRRR_GRID.lon0)
    dx = grid_attrs.get("dx", HRRR_GRID.dx)
    dy = grid_attrs.get("dy", HRRR_GRID.dy)
    xs0, ys0 = _lcc_project_points(grid_attrs, [lat0], [lon0])
    return (
        ys0[0] + np.arange(ds.sizes["y"], dtype=np.float64) * dy,
        xs0[0] + np.arange(ds.sizes["x"], dtype=np.float64) * dx,
        grid_attrs,
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

    Datasets without 1-D lat/lon dims take the projected-grid branch (#457):
    a cfgrib Lambert dataset has ``y``/``x`` dims with 2-D lat/lon auxiliary
    coords, so the targets are projected onto the grid's metre axes and the
    same gather runs on ``(y, x)``.

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
            # Projected-grid branch (#457): cfgrib exposes Lambert grids as
            # (y, x) dims with 2-D lat/lon auxiliary coords. Rebuild the
            # projected axes and project the targets onto them — the gather
            # below is then identical to the lat/lon path.
            projected = _lambert_dataset_axes(ds)
            if projected is None:
                continue
            lat_dim, lon_dim = "y", "x"
            lat_arr, lon_arr, grid_attrs = projected
            proj_xs, proj_ys = _lcc_project_points(
                grid_attrs, targets_lat, targets_lon,
            )
            tgt_lat, tgt_lon = proj_ys, proj_xs
        else:
            try:
                lat_arr = np.asarray(ds.coords[lat_dim].values, dtype=np.float64)
                lon_arr = np.asarray(ds.coords[lon_dim].values, dtype=np.float64)
            except Exception:
                logger.debug("skip dataset: lat/lon coord extract failed", exc_info=True)
                continue
            tgt_lat, tgt_lon = targets_lat, targets_lon

        # Bilinear corner indices + weights — computed once per dataset and
        # reused for every variable in that dataset.
        bw = _bilinear_grid_weights(lat_arr, lon_arr, tgt_lat, tgt_lon)
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
                    # HRRR surface PRES (#457): cfgrib exposes surface-type
                    # levels as scalar coords, so var attrs carry no "level".
                    # Only surface pressure is wanted here — key it at the
                    # documented sentinel 0 (never a real hPa level; the
                    # sounding builder drops it downstream).
                    if field_name != "surface_pressure_pa":
                        continue
                    level = _HRRR_SURFACE_PRESSURE_KEY
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


def decode_hrrr_pressure_per_point(
    grib_bytes: bytes,
    latitudes: list[float],
    longitudes: list[float],
) -> tuple[list[dict[int, dict[str, float]]], list[bool]]:
    """Decode HRRR wrfprs sounding bytes (Lambert grid) per route point.

    Fields per ``_HRRR_PRESSURE_VAR_MAP`` (raw units: K, %, m/s, Pa/s, gpm,
    kg/kg). UGRD/VGRD are grid-relative in the file (uvRelativeToGrid=1) and
    rotated to earth-relative AFTER sampling — the rotation is linear, so it
    commutes with the bilinear gather.

    Surface PRES keys at the documented sentinel ``_HRRR_SURFACE_PRESSURE_KEY``
    (0) as ``surface_pressure_pa`` — never a real hPa level; the sounding
    builder drops that entry (see the constant's comment) and the enrichment
    flow reads it straight from the raw point dict.

    Returns:
        Tuple of (per-point {pressure_hpa: {field: value}}, coverage mask).
        Empty dicts / all-False mask on decode failure.
    """
    import cfgrib

    n_points = len(latitudes)
    results: list[dict[int, dict[str, float]]] = [{} for _ in range(n_points)]
    covered: list[bool] = [False] * n_points
    if not grib_bytes:
        return results, covered

    # Write to temp file for cfgrib
    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
        tmp.write(grib_bytes)
        tmp_path = Path(tmp.name)

    try:
        # indexpath="" disables cfgrib's on-disk .idx sidecar. These are
        # one-shot temp files: only the .grib2 gets unlinked, so a written
        # .idx would be orphaned (604 found in the wild). (#441 efficiency)
        datasets = cfgrib.open_datasets(str(tmp_path), backend_kwargs={"indexpath": ""})
    except Exception:
        logger.warning("cfgrib failed to open HRRR sounding GRIB2", exc_info=True)
        tmp_path.unlink(missing_ok=True)
        return results, covered

    try:
        # No longitude normalization: targets are projected onto the grid's
        # metre axes (pyproj handles both conventions), and the rotation
        # formula normalizes (λ − LoV) internally.
        results, covered = _decode_pressure_vars_from_datasets(
            datasets, latitudes, longitudes, var_map=_HRRR_PRESSURE_VAR_MAP,
        )

        # LoV for the wind rotation, from the first projected dataset.
        lov_deg = 262.5
        for ds in datasets:
            projected = _lambert_dataset_axes(ds)
            if projected is not None:
                lov_deg = projected[2].get("lov", lov_deg)
                break

        for pt_idx, lon in enumerate(longitudes):
            if not covered[pt_idx]:
                continue
            for fields in results[pt_idx].values():
                u = fields.get("raw_u_wind_m_s")
                v = fields.get("raw_v_wind_m_s")
                if u is None or v is None:
                    continue
                u_e, v_e = _rotate_grid_wind_to_earth(
                    [u], [v], [lon], lov_deg=lov_deg,
                )
                fields["raw_u_wind_m_s"] = float(u_e[0])
                fields["raw_v_wind_m_s"] = float(v_e[0])

        return results, covered
    except Exception:
        logger.warning("cfgrib failed to decode HRRR sounding GRIB2", exc_info=True)
        return [{} for _ in range(n_points)], [False] * n_points
    finally:
        for ds in datasets:
            ds.close()
        tmp_path.unlink(missing_ok=True)


def decode_hrrr_diag_per_point(
    grib_bytes: bytes,
    latitudes: list[float],
    longitudes: list[float],
) -> list[dict[str, float]]:
    """Decode HRRR wrfprs diagnostics bytes (Lambert grid) per route point.

    Scalar (level-less) variables keyed by (cfgrib var name, GRIB_typeOfLevel)
    in ``_HRRR_DIAG_FIELD_MAP``; raw values in native units (%, m, J/kg, m/s).
    Key names match the Task-3 diagnostics builder contract.

    Returns:
        List of flat dicts (one per point); empty dicts on decode failure.
    """
    import cfgrib
    import numpy as np

    n_points = len(latitudes)
    results: list[dict[str, float]] = [{} for _ in range(n_points)]
    if not grib_bytes:
        return results

    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
        tmp.write(grib_bytes)
        tmp_path = Path(tmp.name)

    try:
        # indexpath="" disables cfgrib's on-disk .idx sidecar. These are
        # one-shot temp files: only the .grib2 gets unlinked, so a written
        # .idx would be orphaned (604 found in the wild). (#441 efficiency)
        datasets = cfgrib.open_datasets(str(tmp_path), backend_kwargs={"indexpath": ""})
    except Exception:
        logger.warning("cfgrib failed to open HRRR diagnostics GRIB2", exc_info=True)
        tmp_path.unlink(missing_ok=True)
        return results

    try:
        targets_lat = np.asarray(latitudes, dtype=np.float64)
        targets_lon = np.asarray(longitudes, dtype=np.float64)

        for ds in datasets:
            projected = _lambert_dataset_axes(ds)
            if projected is None:
                continue
            y_axis, x_axis, grid_attrs = projected
            proj_xs, proj_ys = _lcc_project_points(
                grid_attrs, targets_lat, targets_lon,
            )
            bw = _bilinear_grid_weights(y_axis, x_axis, proj_ys, proj_xs)
            if bw is None or bw.inb_idx.size == 0:
                continue
            i0, j0, i1, j1 = bw.i0, bw.j0, bw.i1, bw.j1
            w00, w01, w10, w11 = bw.w00, bw.w01, bw.w10, bw.w11
            inb_idx = bw.inb_idx

            for var_name, xr_var in ds.data_vars.items():
                field_name = _HRRR_DIAG_FIELD_MAP.get(
                    (str(var_name).lower(), xr_var.attrs.get("GRIB_typeOfLevel", "")),
                )
                if field_name is None or xr_var.ndim != 2:
                    continue
                var_dims = list(xr_var.dims)
                if var_dims.count("y") != 1 or var_dims.count("x") != 1:
                    continue
                values = np.asarray(xr_var.values, dtype=np.float64)
                if var_dims != ["y", "x"]:
                    values = np.transpose(
                        values, (var_dims.index("y"), var_dims.index("x")),
                    )
                interp = (
                    w00 * values[i0, j0]
                    + w01 * values[i0, j1]
                    + w10 * values[i1, j0]
                    + w11 * values[i1, j1]
                )
                for k, pt_idx in enumerate(inb_idx):
                    v = interp[k]
                    if np.isnan(v):
                        continue
                    # First match wins (same semantics as the GFS diag path).
                    if field_name not in results[pt_idx]:
                        results[pt_idx][field_name] = float(v)

        return results
    except Exception:
        logger.warning("cfgrib failed to decode HRRR diagnostics GRIB2", exc_info=True)
        return [{} for _ in range(n_points)]
    finally:
        for ds in datasets:
            ds.close()
        tmp_path.unlink(missing_ok=True)


def _is_cyclic_longitude(lon_arr) -> bool:
    """True when a 1-D ascending longitude axis wraps the globe.

    GFS route targets are normalised with ``lon % 360``, but a global 0.25°
    grid's last coordinate is 359.75 — so a longitude just west of Greenwich
    (−0.25° … 0°) maps to 359.75 … 360.0 and lands PAST the end of the axis.
    Neither interpolation path knows the axis is cyclic on its own, so both
    returned NaN for a band that every UK↔continent route crosses (#484).

    Regional grids (ICON-EU, ICON-D2, an ECMWF area subset) are not cyclic and
    must be left alone, so that they keep returning None outside their domain.

    Single source of truth for the two decode paths that need it:
    ``_close_cyclic_longitude`` (xarray) and ``_bilinear_grid_weights``
    (vectorised numpy).
    """
    import numpy as np

    if lon_arr.ndim != 1 or lon_arr.size < 2:
        return False
    step = float(lon_arr[1] - lon_arr[0])
    if step <= 0:
        return False  # descending or degenerate — not our case
    # Cyclic iff one more step past the last coordinate closes the circle.
    span = float(lon_arr[-1] - lon_arr[0]) + step
    return bool(np.isclose(span, 360.0, atol=step / 2.0))


def _close_cyclic_longitude(data_array, lon_dim, targets_lon=None):
    """Append a wrapped column at ``first + 360`` when the grid spans the globe.

    Closing the seam with a duplicate of the first column makes the last cell
    interpolate like any other. Used by the ``xarray.interp`` path; the
    vectorised numpy path closes the same seam in ``_bilinear_grid_weights``.

    ``targets_lon`` short-circuits the work when no target actually lands in
    the seam band. That matters: the concat copies the whole field (~8 MB for
    a global 0.25° grid) and this runs per variable per dataset inside the
    decode workers, which are memory-constrained and run 3-up. Only routes
    crossing the Greenwich meridian need the wrap, so the common case should
    not pay for it. Pass ``None`` to wrap unconditionally.
    """
    import numpy as np
    import xarray as xr

    lons = data_array[lon_dim].values
    if not _is_cyclic_longitude(lons):
        return data_array

    if targets_lon is not None:
        targets = np.asarray(targets_lon, dtype=np.float64)
        # The seam band is (last coordinate, first + 360) — everything else
        # already falls inside a real cell.
        if not np.any((targets > float(lons[-1])) & (targets < float(lons[0]) + 360.0)):
            return data_array

    seam = data_array.isel({lon_dim: 0})
    seam = seam.assign_coords({lon_dim: float(lons[0]) + 360.0})
    return xr.concat([data_array, seam], dim=lon_dim)


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

        data_array = _close_cyclic_longitude(data_array, lon_dim, longitudes)

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
# ICON-D2 explicit-convection decode (#462) — message-level, corridor extrema
# ---------------------------------------------------------------------------
#
# The D2 explicit storm fields are NOT decoded through the cfgrib cloud-diag
# path above, for two reasons:
# - Sub-hourly message structure: an `echotop` file carries FOUR 15-min
#   `min_pres` messages per forecast hour. cfgrib would either merge them along
#   a step dimension or split datasets unpredictably; the consumer must select
#   messages by their step window explicitly, never trust the merge (#462
#   verified structure).
# - Corridor extrema: per-point bilinear sampling defeats a 2.2 km model — a
#   storm cell between 10 NM route points (or displaced a few km by timing
#   error) vanishes, making D2 LESS likely to fire than coarse models. These
#   fields therefore reduce over a neighbourhood mask (max; min pressure for
#   echo top; signed argmax|uh| for helicity) instead of interpolating. This is
#   deliberately a different code path from `_interpolate_per_point`.

# Route-buffer kernel radius for corridor extrema (NM). v1 starting point per
# #462; values in the payload are corridor maxima over this buffer, not
# centreline point values.
ICON_D2_CORRIDOR_RADIUS_NM = 10.0

# Reflectivity at/below this is "no echo": the D2 encoder floors quiet columns
# at −150 dBZ (a VALID quiet value, not missing data) — normalize to None so a
# physically meaningless floor never shows up as a number. Distinguishing
# quiet-None from unavailable-None is the completeness flags' job.
_D2_DBZ_QUIET_MAX_DBZ = -100.0

# `min_pres` sentinel: −999 Pa means "no 18 dBZ echo in this window".
_D2_ECHOTOP_NO_ECHO_PA = -998.0

# Substitute for bitmap-missing cells during decode (native D2 domain is not a
# lat/lon rectangle; ~17% of the regular-ll grid is masked).
_D2_MISSING_VALUE = -1.0e30

_NM_PER_DEG_LAT = 60.0


def _to_180(lon: float) -> float:
    """0–360 → −180..+180, the convention route points use.

    The D2 regular-lat-lon product spans 3.94°W–20.34°E, so it does not wrap
    the antimeridian once normalized — the axis stays monotonic ascending and
    needs no seam handling.
    """
    return lon - 360.0 if lon > 180.0 else lon


def _d2_read_message_grid(gid) -> "tuple[np.ndarray, np.ndarray, np.ndarray] | None":
    """Read one regular-ll GRIB message into (lats_axis, lons_axis, values).

    Returns ascending-lat/lon axes with the values array reordered to match,
    and NaN where the bitmap marks cells missing. None for a degenerate grid.
    """
    import eccodes
    import numpy as np

    ni = int(eccodes.codes_get(gid, "Ni"))
    nj = int(eccodes.codes_get(gid, "Nj"))
    if ni < 2 or nj < 2:
        return None
    lat_first = float(eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees"))
    lat_last = float(eccodes.codes_get(gid, "latitudeOfLastGridPointInDegrees"))
    lon_first = float(eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees"))
    lon_last = float(eccodes.codes_get(gid, "longitudeOfLastGridPointInDegrees"))
    # DWD delivers these keys in the 0–360 convention: the D2 domain's western
    # edge (3.94°W) reads 356.06. Route points are −180..+180, so the raw axis
    # matches nothing — and 356.06 > 20.34 also mis-fires the descending-flip
    # below, mirroring the grid east–west. cfgrib normalizes this for us on the
    # other decode paths (verified: its axis reads −3.940 .. 20.340 for the
    # same message), which is exactly why the eccodes path has to do it here.
    lon_first, lon_last = _to_180(lon_first), _to_180(lon_last)

    eccodes.codes_set(gid, "missingValue", _D2_MISSING_VALUE)
    values = np.asarray(eccodes.codes_get_values(gid), dtype=np.float64)
    if values.size != ni * nj:
        return None
    grid = values.reshape(nj, ni)
    grid[grid == _D2_MISSING_VALUE] = np.nan

    lats = np.linspace(lat_first, lat_last, nj)
    lons = np.linspace(lon_first, lon_last, ni)
    if lats[0] > lats[-1]:
        lats = lats[::-1]
        grid = grid[::-1, :]
    if lons[0] > lons[-1]:
        lons = lons[::-1]
        grid = grid[:, ::-1]
    return lats, lons, grid


def _d2_corridor_window(
    lats_axis: "np.ndarray",
    lons_axis: "np.ndarray",
    lat: float,
    lon: float,
    radius_nm: float,
) -> "tuple[int, int, int, int, float] | None":
    """Index window ``(j0, j1, i0, i1, coslat)`` for a point's corridor buffer.

    Returns None unless the ENTIRE buffer lies inside the grid extent. Testing
    the buffer rather than just the centreline is the point: ``searchsorted``
    silently clips its window at the array bounds, so a point within
    ``radius_nm`` of the outer edge would otherwise reduce over a truncated
    corridor and still report it complete — and it defeats the validity-mask
    gate outright, whose valid-cell and all-cell counts are then truncated
    identically and can never disagree (external #462 review of PR #463).
    An edge corridor is UNAVAILABLE, the same verdict as an all-masked one.
    """
    import numpy as np

    coslat = math.cos(math.radians(lat))
    if coslat <= 0.01:
        return None
    dlat = radius_nm / _NM_PER_DEG_LAT
    dlon = radius_nm / (_NM_PER_DEG_LAT * coslat)

    if not (lats_axis[0] <= lat - dlat and lat + dlat <= lats_axis[-1]):
        return None
    if not (lons_axis[0] <= lon - dlon and lon + dlon <= lons_axis[-1]):
        return None

    j0 = int(np.searchsorted(lats_axis, lat - dlat, side="left"))
    j1 = int(np.searchsorted(lats_axis, lat + dlat, side="right"))
    i0 = int(np.searchsorted(lons_axis, lon - dlon, side="left"))
    i1 = int(np.searchsorted(lons_axis, lon + dlon, side="right"))
    if j1 <= j0 or i1 <= i0:
        return None
    return j0, j1, i0, i1, coslat


def _d2_corridor_cells(
    lats_axis: "np.ndarray",
    lons_axis: "np.ndarray",
    grid: "np.ndarray",
    lat: float,
    lon: float,
    radius_nm: float,
) -> "np.ndarray | None":
    """Return the valid (non-NaN) cell values within ``radius_nm`` of a point.

    None when the point's buffer is not wholly inside the grid extent (see
    :func:`_d2_corridor_window`) — callers treat that as an invalid corridor,
    same as an all-masked one. The kernel is an exact distance test over a
    small bounding-box window, not a lat/lon rectangle, so it is genuinely
    circular.
    """
    import numpy as np

    window = _d2_corridor_window(lats_axis, lons_axis, lat, lon, radius_nm)
    if window is None:
        return None
    j0, j1, i0, i1, coslat = window

    sub = grid[j0:j1, i0:i1]
    sub_lats = lats_axis[j0:j1][:, None]
    sub_lons = lons_axis[i0:i1][None, :]

    # Equirectangular distance is accurate to <0.1% at a 10 NM scale.
    dist_nm = _NM_PER_DEG_LAT * np.sqrt(
        (sub_lats - lat) ** 2 + ((sub_lons - lon) * coslat) ** 2
    )
    cells = sub[dist_nm <= radius_nm]
    return cells[~np.isnan(cells)]


def _d2_reduce(
    cells: "np.ndarray | None", mode: str
) -> tuple[float | None, bool]:
    """Reduce corridor cells → (value, corridor_valid).

    ``corridor_valid`` False = no valid (unmasked, in-grid) cells — the channel
    is UNAVAILABLE here, which is different from a quiet 0/None value.
    """
    import numpy as np

    if cells is None or cells.size == 0:
        return None, False
    if mode == "max":
        return float(np.max(cells)), True
    if mode == "min":
        return float(np.min(cells)), True
    if mode == "absmax":
        # Signed value at the argmax of |x| — keeps the payload's signed
        # promise for updraft helicity (anticyclonic rotation is negative).
        return float(cells[int(np.argmax(np.abs(cells)))]), True
    raise ValueError(f"unknown reduction mode {mode!r}")


def _d2_message_step_minutes(gid) -> tuple[int, int]:
    """(startStep, endStep) of a message in minutes since init.

    Parsed from the ``stepRange`` string, whose delivered forms on the D2 feed
    are ``"705-720m"`` / ``"0-735m"`` (minutes, trailing ``m``) and ``"11-12"``
    / ``"0-12"`` (hours, no suffix) — plus single-value forms for instant
    steps. Reading ``startStep``/``endStep`` as integers is NOT reliable here:
    eccodes converts them to hours (truncating sub-hourly steps), which is
    exactly the merge-blindness #462 warns about.
    """
    import eccodes

    step_range = str(eccodes.codes_get(gid, "stepRange"))
    # The 'm' suffix may sit on the range ("705-720m") or on each part
    # ("705m-720m", eccodes-written form); either way the range is minutes.
    in_minutes = "m" in step_range
    parts = [p.rstrip("m") for p in step_range.split("-")]
    try:
        if len(parts) == 2:
            start, end = int(parts[0]), int(parts[1])
        else:
            start = end = int(parts[0])
    except ValueError:
        raise ValueError(f"unparseable GRIB stepRange {step_range!r}")
    if not in_minutes:
        start, end = start * 60, end * 60
    return start, end


def _d2_iter_messages(grib_bytes: bytes):
    """Yield (gid) for each message in a GRIB byte blob; releases handles."""
    import eccodes

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
                    yield gid
                finally:
                    eccodes.codes_release(gid)
    finally:
        tmp_path.unlink(missing_ok=True)


# Reduction mode per explicit-convection variable (hour-max single-message
# fields). `echotop` has bespoke multi-message handling below.
_D2_HOUR_MAX_MODES = {
    "dbz_ctmax": "max",
    "lpi_max": "max",
    "w_ctmax": "max",
    "uh_max": "absmax",
}


def decode_icon_d2_explicit_conv_per_point(
    var_bytes: dict[str, bytes],
    latitudes: list[float],
    longitudes: list[float],
    radius_nm: float = ICON_D2_CORRIDOR_RADIUS_NM,
) -> list[dict]:
    """Decode ICON-D2 explicit-convection fields to per-point corridor extrema.

    Args:
        var_bytes: {variable_name: raw GRIB2 bytes for ONE forecast hour's
            file}, keyed by the fetch variable names in
            ``ICON_D2_EXPLICIT_CONV_VARIABLES``. Keying by variable (one file
            per variable on the DWD feed) removes any dependence on eccodes
            shortName mappings.
        latitudes / longitudes: route points.
        radius_nm: corridor buffer radius.

    Returns one dict per route point:
        - for dbz_ctmax / lpi_max / w_ctmax / uh_max:
            ``{var: (value | None, corridor_valid)}`` — the hour-max (signed
            argmax|uh| for helicity). ``corridor_valid`` False = channel
            unavailable here. dbz values at/below the −100 dBZ quiet bar
            normalize to None (valid + None = genuinely quiet).
        - ``"echotop_quarters"``: ``{end_minute: (min_pres_pa | None, valid)}``
            one entry per decoded 15-min window; value None with valid True =
            no 18 dBZ echo in that window (sentinel −999). The HOURLY echo top
            is constructed by the enrichment loop from the four windows ending
            in (H−1, H] — three of them live in the PREVIOUS hour's file, so
            it cannot be built here.

    Message selection is explicit by step window (minutes since init):
    hour-max fields take the message ending on the hour with the widest
    window; echotop keeps every quarter window keyed by its end minute.
    """
    n_points = len(latitudes)
    results: list[dict] = [
        {"echotop_quarters": {}} for _ in range(n_points)
    ]
    if not var_bytes:
        return results

    for var, grib_bytes in var_bytes.items():
        if not grib_bytes:
            continue
        try:
            if var in _D2_HOUR_MAX_MODES:
                _d2_decode_hour_field(
                    var, grib_bytes, latitudes, longitudes, radius_nm, results,
                )
            elif var == "echotop":
                _d2_decode_echotop(
                    grib_bytes, latitudes, longitudes, radius_nm, results,
                )
            else:
                logger.debug("Unknown D2 explicit-conv variable %s, skipping", var)
        except Exception:
            logger.warning(
                "Failed to decode ICON-D2 explicit-conv %s", var, exc_info=True,
            )

    return results


def _d2_decode_hour_field(
    var: str,
    grib_bytes: bytes,
    latitudes: list[float],
    longitudes: list[float],
    radius_nm: float,
    results: list[dict],
) -> None:
    """Decode a single-value-per-hour hour-max field to corridor extrema."""
    # Pick the message: end on the hour; among those, the widest window (a
    # defensive tiebreak in case DWD ever adds quarter-hour messages to these
    # hour-max files).
    best: tuple[int, tuple] | None = None  # (window_minutes, grid_tuple)
    for gid in _d2_iter_messages(grib_bytes):
        start_m, end_m = _d2_message_step_minutes(gid)
        if end_m % 60 != 0:
            continue
        parsed = _d2_read_message_grid(gid)
        if parsed is None:
            continue
        window = end_m - start_m
        if best is None or window > best[0]:
            best = (window, parsed)
    if best is None:
        return
    lats_axis, lons_axis, grid = best[1]

    mode = _D2_HOUR_MAX_MODES.get(var, "max")
    for i, (lat, lon) in enumerate(zip(latitudes, longitudes)):
        cells = _d2_corridor_cells(lats_axis, lons_axis, grid, lat, lon, radius_nm)
        value, valid = _d2_reduce(cells, mode)
        if var == "dbz_ctmax" and value is not None and value <= _D2_DBZ_QUIET_MAX_DBZ:
            value = None  # encoder floor → genuinely quiet, not a number
        results[i][var] = (value, valid)


def _d2_decode_echotop(
    grib_bytes: bytes,
    latitudes: list[float],
    longitudes: list[float],
    radius_nm: float,
    results: list[dict],
) -> None:
    """Decode all 15-min `min_pres` echo-top windows, keyed by end minute."""
    import numpy as np

    for gid in _d2_iter_messages(grib_bytes):
        _start_m, end_m = _d2_message_step_minutes(gid)
        parsed = _d2_read_message_grid(gid)
        if parsed is None:
            continue
        lats_axis, lons_axis, grid = parsed
        # The −999 sentinel means "no echo" — a VALID observation that must
        # not win the min-pressure reduction. NaN it out but remember which
        # cells were genuinely valid so completeness stays honest.
        echo_grid = grid.copy()
        echo_grid[echo_grid <= _D2_ECHOTOP_NO_ECHO_PA] = np.nan
        for i, (lat, lon) in enumerate(zip(latitudes, longitudes)):
            all_cells = _d2_corridor_cells(
                lats_axis, lons_axis, grid, lat, lon, radius_nm,
            )
            _value_any, corridor_valid = _d2_reduce(all_cells, "max")
            echo_cells = _d2_corridor_cells(
                lats_axis, lons_axis, echo_grid, lat, lon, radius_nm,
            )
            min_pres, has_echo = _d2_reduce(echo_cells, "min")
            results[i]["echotop_quarters"][end_m] = (
                min_pres if has_echo else None,
                corridor_valid,
            )


# ---------------------------------------------------------------------------
# ICON-D2 domain validity mask (#462 domain-gate hardening)
# ---------------------------------------------------------------------------
#
# The delivered D2 regular-lat-lon files are gridType=regular_ll with NO
# rotated-pole metadata, and ~17% of cells (the corners outside the native
# rotated-pole rectangle) are bitmap-masked. A route can pass the #461 bbox
# gate yet clip masked cells. The mask below is built once from any delivered
# message's bitmap (it tests the actual product, unlike hardcoded rotated-pole
# constants) and cached; the gate requires the route's ENTIRE corridor buffer
# to lie in valid cells.


def build_d2_validity_mask(grib_bytes: bytes) -> dict | None:
    """Build {lats, lons, valid} from the first message's bitmap.

    ``valid`` is a bool grid: True where the cell carries data. Returns None
    when the bytes can't be decoded.
    """
    import numpy as np

    try:
        for gid in _d2_iter_messages(grib_bytes):
            parsed = _d2_read_message_grid(gid)
            if parsed is None:
                continue
            lats_axis, lons_axis, grid = parsed
            return {
                "lats": lats_axis,
                "lons": lons_axis,
                "valid": ~np.isnan(grid),
            }
    except Exception:
        logger.warning("Failed to build ICON-D2 validity mask", exc_info=True)
    return None


def d2_corridor_fully_valid(
    mask: dict,
    latitudes: list[float],
    longitudes: list[float],
    radius_nm: float = ICON_D2_CORRIDOR_RADIUS_NM,
) -> bool:
    """True when every route point's corridor buffer lies entirely in valid cells.

    All-or-nothing, matching the #456 domain rule: one clipped corner fails
    the whole route (→ ICON-EU), never a per-point mix. Two distinct ways to
    clip, both rejected here:

    - **bitmap-masked cells** inside the buffer (the ~17% corners the native
      rotated-pole domain doesn't cover) — caught by the valid-vs-all count;
    - **the outer grid boundary** truncating the buffer — caught by
      :func:`_d2_corridor_window`, which is what makes the count comparison
      meaningful at all: without it both gathers clip identically at the array
      bounds and can never disagree (external #462 review of PR #463).
    """
    import numpy as np

    lats_axis = mask["lats"]
    lons_axis = mask["lons"]
    valid = mask["valid"]
    # Invalid cells become NaN so the corridor gather counts them as missing;
    # completeness of the gather then equals "no masked cell in the buffer".
    grid = np.where(valid, 1.0, np.nan)
    # Hoisted: the all-ones reference grid is per-product, not per-point.
    ones = np.ones_like(grid)

    for lat, lon in zip(latitudes, longitudes):
        cells = _d2_corridor_cells(lats_axis, lons_axis, grid, lat, lon, radius_nm)
        if cells is None:
            return False
        # Count cells inside the buffer INCLUDING masked ones; if any were
        # dropped as NaN the corridor clips the masked corner.
        all_cells = _d2_corridor_cells(
            lats_axis, lons_axis, ones, lat, lon, radius_nm,
        )
        if all_cells is None or cells.size != all_cells.size or cells.size == 0:
            return False
    return True


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
    reference_heights_m: dict[int, float] | None = None,
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

    Args:
        reference_heights_m: Optional ``{pressure_hpa: geopotential height m}``
            from an independent source for the SAME point and valid time —
            in practice the Open-Meteo levels already on the hourly the GRIB
            column is about to replace. Used only to give the reconstruction
            a real datum instead of ISA (#486); see
            ``_fill_missing_geopotential_height``.
    """
    from weatherbrief.models.analysis import PressureLevelData

    # First pass: convert each level's raw fields to output units.
    converted_by_p: dict[int, dict[str, float]] = {}
    for p_hpa, raw_fields in point_data.items():
        converted = _convert_raw_sounding(raw_fields, p_hpa)
        if converted:
            converted_by_p[p_hpa] = converted

    _fill_missing_geopotential_height(
        converted_by_p, reference_heights_m=reference_heights_m,
    )

    return [
        PressureLevelData(pressure_hpa=p_hpa, **converted)
        for p_hpa, converted in sorted(converted_by_p.items(), reverse=True)
    ]


def _virtual_temperature_k(
    temperature_c: float,
    relative_humidity_pct: float | None,
    pressure_hpa: float,
) -> float:
    """Virtual temperature (K) from T, RH and p — falls back to T when dry.

    ``Tv = T / (1 − (e/p)(1 − ε))`` with ``ε = R_d/R_v = 0.622`` and vapour
    pressure ``e`` from the Magnus saturation formula (over water, the same
    coefficients ``_convert_raw_sounding`` already uses for dewpoint).

    Tv is what the hypsometric equation actually calls for: moist air is less
    dense than dry air at the same temperature, so using T under-estimates
    layer thickness by ~0.5–1 % in warm moist air (#486).
    """
    import math

    if relative_humidity_pct is None or pressure_hpa <= 0:
        return temperature_c + 273.15
    rh = max(0.0, min(100.0, relative_humidity_pct))
    e_sat = 6.112 * math.exp(17.67 * temperature_c / (temperature_c + 243.5))
    e = (rh / 100.0) * e_sat
    denom = 1.0 - (e / pressure_hpa) * (1.0 - 0.622)
    if denom <= 0:
        return temperature_c + 273.15
    return (temperature_c + 273.15) / denom


def _fill_missing_geopotential_height(
    converted_by_p: dict[int, dict[str, float]],
    *,
    reference_heights_m: dict[int, float] | None = None,
) -> None:
    """Derive ``geopotential_height_m`` via the hypsometric equation.

    Mutates the per-level dicts in-place. Picks the surface-most level whose
    height is known, then integrates outward from it in both directions with
    the layer-mean VIRTUAL temperature::

        Δz = (R_d / g) · Tv_mean · ln(p_lower / p_upper)

    Levels that already carry a real ``geopotential_height_m`` are preserved
    (so if the feed starts delivering ``z`` at more levels, those values win)
    and each one re-anchors the integration above it.

    No-op if every level already has geopotential, or if temperature is
    missing anywhere in the column (can't integrate reliably).

    Used as a safety net when the source doesn't deliver geopotential on
    every pressure level — e.g. ICON model levels (DWD never ships it) and
    pre-amendment ECMWF archives (only ``z`` at level 1). For current ECMWF
    full-coverage runs the ``gh`` field is decoded directly and the
    ``all-present`` guard below makes this function a no-op.

    **Datum (#486).** ``reference_heights_m`` supplies real geopotential
    heights for the same point and valid time from an independent source —
    in practice Open-Meteo's own levels for the same model. The surface-most
    matching level becomes the datum, and ONLY that one: the reference sets
    where the column sits, never how thick its layers are. Without a
    reference the column falls back to the ISA altitude of its surface-most
    pressure level.

    Note what the ISA fallback does and does not cost. It is *not* a terrain
    error: ``pressure_hpa_to_altitude_m`` maps pressure to height, and the
    anchor level's pressure already encodes terrain (795 hPa → 2000 m, which
    is about the near-surface pressure over 2000 m terrain). The real error
    is that pressure surface's deviation from ISA, driven by MSLP and column
    mean temperature — order 100–300 m in a deep low or a cold column, and a
    *uniform* offset, since the thicknesses come from the integration below
    and are unaffected by the anchor.
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

    # Seed ONE datum from the reference: the surface-most level the two
    # sources share. Seeding every match would make the reference dictate the
    # column's thicknesses too, and it comes from a different run of the same
    # model — small disagreements would show up as kinks. One datum keeps the
    # profile's shape entirely GRIB-derived and moves it as a rigid column.
    if reference_heights_m:
        for p_hpa in sorted_p:
            level = converted_by_p[p_hpa]
            if level.get("geopotential_height_m") is not None:
                break  # the column already has a real datum at or below here
            height_m = reference_heights_m.get(p_hpa)
            if height_m is not None:
                level["geopotential_height_m"] = height_m
                break

    def _thickness_m(p_lower: int, p_upper: int) -> float:
        """Hypsometric thickness of the (p_lower, p_upper) layer, metres."""
        lower = converted_by_p[p_lower]
        upper = converted_by_p[p_upper]
        tv_lower = _virtual_temperature_k(
            lower["temperature_c"], lower.get("relative_humidity_pct"), p_lower,
        )
        tv_upper = _virtual_temperature_k(
            upper["temperature_c"], upper.get("relative_humidity_pct"), p_upper,
        )
        tv_mean = 0.5 * (tv_lower + tv_upper)
        return (_RD_DRY_AIR * tv_mean / _G) * math.log(p_lower / p_upper)

    # Datum: the surface-most level that already knows its height (delivered
    # or seeded). Only when nothing in the column does do we fall back to ISA
    # at the surface-most level.
    datum_i = next(
        (
            i for i, p in enumerate(sorted_p)
            if converted_by_p[p].get("geopotential_height_m") is not None
        ),
        None,
    )
    if datum_i is None:
        datum_i = 0
        converted_by_p[sorted_p[0]]["geopotential_height_m"] = (
            pressure_hpa_to_altitude_m(sorted_p[0])
        )

    # Downward from the datum (higher pressure, lower altitude).
    for i in range(datum_i - 1, -1, -1):
        below = converted_by_p[sorted_p[i]]
        if below.get("geopotential_height_m") is not None:
            continue
        above_p = sorted_p[i + 1]
        below["geopotential_height_m"] = (
            converted_by_p[above_p]["geopotential_height_m"]
            - _thickness_m(sorted_p[i], above_p)
        )

    # Upward from the datum (lower pressure, higher altitude).
    for i in range(datum_i + 1, len(sorted_p)):
        upper = converted_by_p[sorted_p[i]]
        if upper.get("geopotential_height_m") is not None:
            continue
        lower_p = sorted_p[i - 1]
        upper["geopotential_height_m"] = (
            converted_by_p[lower_p]["geopotential_height_m"]
            + _thickness_m(lower_p, sorted_p[i])
        )


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


def model_surface_height_m(
    pressure_levels: list,
    surface_pressure_hpa: float,
) -> float | None:
    """The model's own orography at a point, in metres MSL (#487).

    Reads the geopotential-height column where surface pressure crosses it:
    ``z(p_surface)``, log-linear in pressure between the two bracketing
    levels. When ``sp`` is higher-pressure than every level (low terrain,
    the usual case at sea level) the surface-most level is extended downward
    hypsometrically with its own temperature.

    Deriving terrain this way rather than from an elevation dataset is
    deliberate: an AGL field is referenced to the MODEL's orography, which
    at ~9 km resolution differs from real terrain by hundreds of metres in
    mountains. Converting AGL→MSL with SRTM would trade one datum error for
    another.

    Returns None when the column carries no usable heights.
    """
    import math

    if surface_pressure_hpa is None or surface_pressure_hpa <= 0:
        return None

    known = sorted(
        (
            (float(pl.pressure_hpa), float(pl.geopotential_height_m))
            for pl in pressure_levels
            if pl.pressure_hpa
            and pl.pressure_hpa > 0
            and pl.geopotential_height_m is not None
        ),
        reverse=True,  # highest pressure (lowest level) first
    )
    if not known:
        return None

    sp = float(surface_pressure_hpa)

    # Below the lowest level: extend downward with the hypsometric equation.
    if sp >= known[0][0]:
        p0, z0 = known[0]
        t_c = next(
            (
                pl.temperature_c for pl in pressure_levels
                if pl.pressure_hpa == int(p0) and pl.temperature_c is not None
            ),
            None,
        )
        if t_c is None:
            return z0
        t_k = _virtual_temperature_k(
            t_c,
            next(
                (
                    pl.relative_humidity_pct for pl in pressure_levels
                    if pl.pressure_hpa == int(p0)
                ),
                None,
            ),
            p0,
        )
        return z0 - (_RD_DRY_AIR * t_k / _G) * math.log(sp / p0)

    # Above the top of the column — nothing sane to say about the surface.
    if sp <= known[-1][0]:
        return None

    for (p_lower, z_lower), (p_upper, z_upper) in zip(known, known[1:]):
        if p_upper <= sp <= p_lower:
            frac = math.log(p_lower / sp) / math.log(p_lower / p_upper)
            return z_lower + (z_upper - z_lower) * frac
    return None


def build_ecmwf_cloud_diagnostics(
    raw: dict[str, float],
    *,
    terrain_elevation_m: float | None = None,
) -> "NWPCloudDiagnostics | None":
    """Convert raw ECMWF surface values into NWPCloudDiagnostics.

    ECMWF reports heights in meters (like ICON-EU), cloud covers as
    0–1 fractions.  Heights ≥ 9998 m are treated as "no cloud" sentinel
    (ECMWF uses 9999 m; threshold at 9998 for float tolerance).

    Unit conversions:
    - meters → feet (× 3.28084)
    - 0–1 fraction → 0–100 % (× 100)

    **Freezing-level datum (#487).** ECMWF ``deg0l`` is metres ABOVE GROUND,
    but ``NWPCloudDiagnostics.freezing_level_ft`` is an MSL field everywhere
    else it is written or read: GFS fills it from ``HGT@0degC`` (MSL
    geopotential), the sounding's own 0 °C crossing is MSL, and Open-Meteo's
    ``freezing_level_height`` is MSL. ``terrain_elevation_m`` — the model's
    own orography at this point — is added to bring ``deg0l`` onto that
    datum.

    Without it the value is **dropped rather than passed through as AGL**.
    ``HourlyForecast.attach_nwp_diagnostics`` mirrors this field onto
    ``freezing_level_m``, which already holds Open-Meteo's MSL value; a
    correctly-referenced Open-Meteo number beats a wrongly-referenced ECMWF
    one, and the mirror is skipped when the field is None.

    ``ceil`` / ``cbh`` / ``hcct`` are AGL too and are NOT converted here.
    Ceiling and cloud base are conventionally AGL in aviation, so their datum
    is a separate question (the #441 finding #3 follow-up), not this one.

    A NEGATIVE ``deg0l`` is kept and converted: it means the 0 °C isotherm
    sits below the model's ground, which is a real cold-airmass forecast, and
    with terrain in hand it maps to a perfectly ordinary MSL height. Only
    ``deg0l`` gets that latitude — see ``_agl_m``.
    """
    from weatherbrief.models.analysis import (
        NWPCloudDiagnostics,
        NWPCloudLayerDiag,
    )

    if not raw:
        return None

    def _agl_m(key: str, *, allow_below_ground: bool = False) -> float | None:
        """Validated metres-AGL for one field, or None.

        Drops the 9999 m no-value sentinel. ``allow_below_ground`` decides
        whether NEGATIVE values survive, and the two answers genuinely
        differ by field — which is why this floor lives here rather than
        being re-checked at each call site:

        - ``ceil`` / ``cbh`` / ``hcct``: a cloud below ground is
          meaningless, so a negative is a decode artifact → dropped.
        - ``deg0l``: a freezing level below ground is a REAL forecast — the
          0 °C isotherm extrapolated beneath the model surface when the
          whole near-surface column is sub-freezing. Dropping it would
          discard model-native data in exactly the cold-airmass case where
          the freezing level matters most for icing. The repo's own
          real-GRIB sanity bounds record this asymmetry: ``freezing_level_m``
          is bounded ``(-500, 6000)`` where every sibling height is
          ``(0, 25000)`` (``tests/test_ecmwf_sample.py``).

        NaN never reaches here — ``_interpolate_per_point`` maps it to None.
        """
        val = raw.get(key)
        if val is None or val >= _ECMWF_NO_CLOUD_SENTINEL_M:
            return None
        if val < 0 and not allow_below_ground:
            return None
        return val

    def _m_to_ft(key: str) -> float | None:
        val = _agl_m(key)
        return None if val is None else round(val * _M_TO_FT)

    def _frac_to_pct(key: str) -> float | None:
        val = raw.get(key)
        if val is None:
            return None
        return round(val * 100.0, 1)

    ceiling_ft = _m_to_ft("ceiling_m")
    cloud_base_ft = _m_to_ft("cloud_base_height_m")
    convective_top_ft = _m_to_ft("convective_cloud_top_m")
    # deg0l is AGL; only usable once referenced to MSL. See the datum note
    # in the docstring for why an unconvertible value is dropped, not passed
    # through. (#487)
    freezing_level_agl_m = _agl_m("freezing_level_m", allow_below_ground=True)
    freezing_level_ft: float | None = None
    if freezing_level_agl_m is not None and terrain_elevation_m is not None:
        freezing_level_ft = round(
            (freezing_level_agl_m + terrain_elevation_m) * _M_TO_FT
        )
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
