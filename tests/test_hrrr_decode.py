"""HRRR Lambert-grid decode tests (#457).

Covers:
- ``_rotate_grid_wind_to_earth`` closed form (identity at LoV, ±10° cases,
  sign convention, wrap-around) cross-checked against pyproj finite
  differences of the actual HRRR projection.
- ``_lcc_project_points`` vs ``hrrr_fetch.hrrr_projection``.
- ``decode_hrrr_pressure_per_point`` / ``decode_hrrr_diag_per_point`` on a
  synthetic cfgrib-readable Lambert GRIB2 written with eccodes (the
  ``lambert_grib2`` sample is not shipped in the eccodes wheel, so messages
  are built from the generic GRIB2 sample with ``gridType=lambert`` set).
  The synthetic messages use the REAL HRRR parameter ids (verified against
  a real wrfprs message, 2026-07-31 18z f01): CLMR is 0/1/22 (decodes as
  cfgrib var ``clwmr``), CIMIXR is 0/1/82 (NCEP-local — decodes as
  ``unknown`` with eccodes 2.48), PRES:surface decodes as ``sp``.
- The surface-pressure sentinel key 0 and its interaction with
  ``build_pressure_levels_from_grib`` (which must drop it).

No network access.
"""

from __future__ import annotations

import io
import math

import numpy as np
import pytest

from weatherbrief.fetch.grib.decode import (
    _HRRR_PRESSURE_VAR_MAP,
    _lcc_project_points,
    _rotate_grid_wind_to_earth,
    build_hrrr_cloud_diagnostics,
    build_hrrr_surface_extras,
    build_pressure_levels_from_grib,
    decode_hrrr_diag_per_point,
    decode_hrrr_pressure_per_point,
)
from weatherbrief.fetch.grib.hrrr_fetch import HRRR_GRID, hrrr_projection

# Small Lambert grid, HRRR projection parameters (verified constants).
_NX, _NY = 12, 10
_LAT0, _LON0 = 30.0, 250.0
_DX = _DY = 3000.0


def _plane(base: float, dj: float, di: float) -> np.ndarray:
    """Index-linear (y, x) field — bilinear interpolation reproduces it exactly."""
    j, i = np.mgrid[0:_NY, 0:_NX]
    return base + dj * j + di * i


def _lambert_message(
    discipline: int,
    category: int,
    number: int,
    type_of_level: str,
    level: float,
    values: np.ndarray,
):
    """One Lambert-grid GRIB2 message with HRRR table conventions (kwbc, v2)."""
    from eccodes import (
        CODES_PRODUCT_GRIB,
        codes_new_from_samples,
        codes_set,
        codes_set_values,
    )

    g = codes_new_from_samples("GRIB2", CODES_PRODUCT_GRIB)
    codes_set(g, "centre", "kwbc")
    codes_set(g, "tablesVersion", 2)
    codes_set(g, "gridType", "lambert")
    codes_set(g, "Nx", _NX)
    codes_set(g, "Ny", _NY)
    codes_set(g, "latitudeOfFirstGridPointInDegrees", _LAT0)
    codes_set(g, "longitudeOfFirstGridPointInDegrees", _LON0)
    codes_set(g, "LaDInDegrees", 38.5)
    codes_set(g, "LoVInDegrees", 262.5)
    codes_set(g, "Latin1InDegrees", 38.5)
    codes_set(g, "Latin2InDegrees", 38.5)
    codes_set(g, "DxInMetres", _DX)
    codes_set(g, "DyInMetres", _DY)
    codes_set(g, "jScansPositively", 1)
    codes_set(g, "packingType", "grid_simple")
    codes_set(g, "discipline", discipline)
    codes_set(g, "parameterCategory", category)
    codes_set(g, "parameterNumber", number)
    codes_set(g, "typeOfLevel", type_of_level)
    codes_set(g, "level", level)
    codes_set_values(g, np.asarray(values, dtype=np.float64).ravel())
    return g


def _write_grib(messages) -> bytes:
    from eccodes import codes_release, codes_write

    buf = io.BytesIO()
    for g in messages:
        codes_write(g, buf)
        codes_release(g)
    return buf.getvalue()


def _target_latlon(frac_j: float, frac_i: float) -> tuple[float, float]:
    """lat/lon of the point at fractional grid indices (frac_j, frac_i)."""
    proj = hrrr_projection()
    x0, y0 = proj(_LON0, _LAT0)
    lon, lat = proj(x0 + frac_i * _DX, y0 + frac_j * _DY, inverse=True)
    return lat, lon


# Real HRRR parameter ids (verified 2026-07-31, wrfprs 18z f01).
_PRES = (0, 3, 0)
_TMP = (0, 0, 0)
_DPT = (0, 0, 6)
_RH = (0, 1, 1)
_UGRD = (0, 2, 2)
_VGRD = (0, 2, 3)
_VVEL = (0, 2, 8)
_HGT = (0, 3, 5)
_CLMR = (0, 1, 22)
_CIMIXR = (0, 1, 82)


@pytest.fixture(scope="module")
def sounding_bytes() -> bytes:
    """Synthetic HRRR sounding blob: 9 pressure vars × {850, 500} + sfc PRES."""
    fields_850 = {
        _TMP: _plane(280.0, 0.5, 0.25),
        _DPT: _plane(275.0, 0.4, 0.2),
        _RH: _plane(65.0, 1.0, 0.5),
        _UGRD: np.full((_NY, _NX), 3.0),
        _VGRD: np.full((_NY, _NX), -1.5),
        _VVEL: np.full((_NY, _NX), 0.5),
        _HGT: _plane(1450.0, 2.0, 1.0),
        _CLMR: _plane(1.0e-5, 1.0e-6, 5.0e-7),
        _CIMIXR: _plane(2.0e-5, 2.0e-6, 1.0e-6),
    }
    fields_500 = {
        _TMP: _plane(250.0, 0.3, 0.15),
        _DPT: _plane(240.0, 0.3, 0.1),
        _RH: _plane(40.0, 0.5, 0.25),
        _UGRD: np.full((_NY, _NX), -2.0),
        _VGRD: np.full((_NY, _NX), 4.0),
        _VVEL: np.full((_NY, _NX), -0.2),
        _HGT: _plane(5500.0, 3.0, 1.5),
        _CLMR: _plane(3.0e-5, 1.0e-6, 5.0e-7),
        _CIMIXR: _plane(4.0e-5, 2.0e-6, 1.0e-6),
    }
    msgs = []
    for level, fields in ((850, fields_850), (500, fields_500)):
        for (disc, cat, num), values in fields.items():
            msgs.append(
                _lambert_message(disc, cat, num, "isobaricInhPa", level, values)
            )
    msgs.append(
        _lambert_message(*_PRES, "surface", 0, np.full((_NY, _NX), 101325.0))
    )
    return _write_grib(msgs)


@pytest.fixture(scope="module")
def diag_bytes() -> bytes:
    """Synthetic HRRR diagnostics blob (one message per diag variable/level)."""
    specs = [
        ((0, 6, 3), "lowCloudLayer", 0, _plane(55.0, 1.0, 0.5)),       # LCDC
        ((0, 6, 4), "middleCloudLayer", 0, _plane(33.0, 0.5, 0.25)),   # MCDC
        ((0, 6, 5), "highCloudLayer", 0, _plane(22.0, 0.25, 0.125)),   # HCDC
        ((0, 6, 1), "atmosphere", 0, _plane(80.0, 0.5, 0.25)),         # TCDC
        ((0, 3, 5), "cloudCeiling", 0, np.full((_NY, _NX), 1500.0)),   # HGT ceil
        ((0, 3, 5), "cloudBase", 0, np.full((_NY, _NX), 900.0)),       # HGT base
        ((0, 7, 6), "surface", 0, np.full((_NY, _NX), 1500.0)),        # CAPE sfc
        ((0, 7, 6), "pressureFromGroundLayer", 18000, np.full((_NY, _NX), 900.0)),
        ((0, 7, 7), "surface", 0, np.full((_NY, _NX), 50.0)),          # CIN sfc
        ((0, 7, 7), "pressureFromGroundLayer", 18000, np.full((_NY, _NX), 25.0)),
        ((0, 19, 0), "surface", 0, np.full((_NY, _NX), 8000.0)),       # VIS
        ((0, 2, 22), "surface", 0, np.full((_NY, _NX), 12.0)),         # GUST
    ]
    return _write_grib(
        [_lambert_message(*ids, tol, level, vals) for ids, tol, level, vals in specs]
    )


# ---------------------------------------------------------------------------
# _rotate_grid_wind_to_earth
# ---------------------------------------------------------------------------


def test_rotation_identity_at_lov():
    """α = 0 on the reference longitude — grid wind equals earth wind."""
    for lon in (262.5, -97.5):  # same meridian, both conventions
        u_e, v_e = _rotate_grid_wind_to_earth([3.0], [-1.5], [lon])
        assert u_e[0] == pytest.approx(3.0)
        assert v_e[0] == pytest.approx(-1.5)


def test_rotation_closed_form_plus_minus_10_deg():
    """u=(1,0) at λ = LoV ± 10° → (cos α, ∓sin α), α = ±10°·sin(38.5°).

    Sign verified against pyproj finite differences of the actual projection:
    EAST of LoV the grid x-axis tilts SOUTH of east (v_e < 0); WEST of LoV it
    tilts NORTH of east.
    """
    k = math.sin(math.radians(38.5))
    alpha = math.radians(10.0) * k

    u_e, v_e = _rotate_grid_wind_to_earth([1.0], [0.0], [272.5])  # LoV + 10°
    assert u_e[0] == pytest.approx(math.cos(alpha), abs=1e-12)
    assert v_e[0] == pytest.approx(-math.sin(alpha), abs=1e-12)
    assert v_e[0] < 0.0

    u_e, v_e = _rotate_grid_wind_to_earth([1.0], [0.0], [252.5])  # LoV − 10°
    assert u_e[0] == pytest.approx(math.cos(alpha), abs=1e-12)
    assert v_e[0] == pytest.approx(math.sin(alpha), abs=1e-12)
    assert v_e[0] > 0.0


def test_rotation_longitude_wraparound():
    """λ = LoV + 190° must normalise to LoV − 170°."""
    a_u, a_v = _rotate_grid_wind_to_earth([1.0], [0.0], [262.5 + 190.0])
    b_u, b_v = _rotate_grid_wind_to_earth([1.0], [0.0], [262.5 - 170.0])
    assert a_u[0] == pytest.approx(b_u[0], abs=1e-12)
    assert a_v[0] == pytest.approx(b_v[0], abs=1e-12)


def test_rotation_preserves_magnitude():
    u_e, v_e = _rotate_grid_wind_to_earth([3.0], [-1.5], [-104.0])
    assert math.hypot(u_e[0], v_e[0]) == pytest.approx(math.hypot(3.0, 1.5))


def test_rotation_matches_pyproj_projection_geometry():
    """Independent ground truth: finite-difference the HRRR projection.

    A grid-relative vector (1, 0) points along the grid x-axis; its
    earth-relative components are the local east/north direction of that
    axis, measured by projecting two points 100 m apart in x back to
    lat/lon. Same for (0, 1) along the y-axis.
    """
    proj = hrrr_projection()
    lat_pt, lon_pt = 39.0, -104.0  # CONUS interior, ~6.5° west of LoV
    x0, y0 = proj(lon_pt, lat_pt)
    d = 100.0

    def earth_components(dx, dy):
        lon1, lat1 = proj(x0 + dx, y0 + dy, inverse=True)
        mean_lat = math.radians((lat_pt + lat1) / 2.0)
        de = math.radians(lon1 - lon_pt) * math.cos(mean_lat)
        dn = math.radians(lat1 - lat_pt)
        norm = math.hypot(de, dn)
        return de / norm, dn / norm

    exp_x = earth_components(d, 0.0)
    exp_y = earth_components(0.0, d)

    u_e, v_e = _rotate_grid_wind_to_earth([1.0], [0.0], [lon_pt], lov_deg=262.5)
    assert u_e[0] == pytest.approx(exp_x[0], abs=1e-5)
    assert v_e[0] == pytest.approx(exp_x[1], abs=1e-5)

    u_e, v_e = _rotate_grid_wind_to_earth([0.0], [1.0], [lon_pt], lov_deg=262.5)
    assert u_e[0] == pytest.approx(exp_y[0], abs=1e-5)
    assert v_e[0] == pytest.approx(exp_y[1], abs=1e-5)


def test_rotation_cone_const_override():
    u_def, v_def = _rotate_grid_wind_to_earth([1.0], [0.0], [272.5], cone_const=1.0)
    alpha = math.radians(10.0)
    assert u_def[0] == pytest.approx(math.cos(alpha), abs=1e-12)
    assert v_def[0] == pytest.approx(-math.sin(alpha), abs=1e-12)


# ---------------------------------------------------------------------------
# _lcc_project_points
# ---------------------------------------------------------------------------


def test_lcc_project_points_matches_hrrr_projection():
    attrs = {
        "lad": HRRR_GRID.lad,
        "lov": HRRR_GRID.lov,
        "latin1": HRRR_GRID.latin1,
        "latin2": HRRR_GRID.latin2,
    }
    lats = [39.0, 25.8]
    lons = [-104.0, -80.2]
    xs, ys = _lcc_project_points(attrs, lats, lons)
    proj = hrrr_projection()
    exp_x, exp_y = proj(np.array(lons), np.array(lats))
    assert xs == pytest.approx(exp_x)
    assert ys == pytest.approx(exp_y)


def test_lcc_project_points_defaults_to_hrrr_projection():
    """Missing projection params fall back to the verified HRRR constants."""
    lats = [39.0]
    lons = [-104.0]
    xs, ys = _lcc_project_points({}, lats, lons)
    proj = hrrr_projection()
    exp_x, exp_y = proj(np.array(lons), np.array(lats))
    assert xs == pytest.approx(exp_x)
    assert ys == pytest.approx(exp_y)


# ---------------------------------------------------------------------------
# Var map aliases
# ---------------------------------------------------------------------------


def test_pressure_var_map_aliases():
    assert _HRRR_PRESSURE_VAR_MAP["clwmr"] == "cloud_liquid_water_kg_kg"
    assert _HRRR_PRESSURE_VAR_MAP["clmr"] == "cloud_liquid_water_kg_kg"
    assert _HRRR_PRESSURE_VAR_MAP["cimixr"] == "ice_mixing_ratio_kg_kg"
    # CIMIXR (0/1/82) is NCEP-local: eccodes 2.48 decodes its cfgrib var name
    # as "unknown" (verified on a real wrfprs message 2026-07-31).
    assert _HRRR_PRESSURE_VAR_MAP["unknown"] == "ice_mixing_ratio_kg_kg"
    assert _HRRR_PRESSURE_VAR_MAP["dpt"] == "raw_dewpoint_k"
    assert _HRRR_PRESSURE_VAR_MAP["w"] == "raw_omega_pa_s"
    assert _HRRR_PRESSURE_VAR_MAP["gh"] == "raw_geopotential_height_gpm"
    assert _HRRR_PRESSURE_VAR_MAP["pres"] == "surface_pressure_pa"
    assert _HRRR_PRESSURE_VAR_MAP["sp"] == "surface_pressure_pa"


# ---------------------------------------------------------------------------
# decode_hrrr_pressure_per_point
# ---------------------------------------------------------------------------


def test_decode_hrrr_pressure_bilinear_rotation_surface(sounding_bytes):
    """Full branch: Lambert gather at a fractional index + rotation + sfc key."""
    lat_in, lon_in = _target_latlon(3.5, 4.25)
    lat_out, lon_out = 51.5, -0.12  # London — outside the grid

    results, covered = decode_hrrr_pressure_per_point(
        sounding_bytes, [lat_in, lat_out], [lon_in, lon_out]
    )

    assert covered == [True, False]
    assert results[1] == {}
    pt = results[0]
    assert set(pt) == {0, 850, 500}

    lev850 = pt[850]
    # Index-linear fields interpolate exactly to base + dj*fj + di*fi
    # (packing tolerance only).
    assert lev850["raw_temperature_k"] == pytest.approx(
        280.0 + 0.5 * 3.5 + 0.25 * 4.25, rel=1e-2
    )
    assert lev850["raw_dewpoint_k"] == pytest.approx(
        275.0 + 0.4 * 3.5 + 0.2 * 4.25, rel=1e-2
    )
    assert lev850["raw_relative_humidity_pct"] == pytest.approx(
        65.0 + 1.0 * 3.5 + 0.5 * 4.25, rel=1e-2
    )
    assert lev850["raw_geopotential_height_gpm"] == pytest.approx(
        1450.0 + 2.0 * 3.5 + 1.0 * 4.25, rel=1e-2
    )
    assert lev850["raw_omega_pa_s"] == pytest.approx(0.5, rel=1e-2)
    assert lev850["cloud_liquid_water_kg_kg"] == pytest.approx(
        1.0e-5 + 1.0e-6 * 3.5 + 5.0e-7 * 4.25, rel=2e-2
    )
    # CIMIXR (0/1/82 → cfgrib var "unknown") must land on the ice field.
    assert lev850["ice_mixing_ratio_kg_kg"] == pytest.approx(
        2.0e-5 + 2.0e-6 * 3.5 + 1.0e-6 * 4.25, rel=2e-2
    )

    lev500 = pt[500]
    assert lev500["raw_temperature_k"] == pytest.approx(
        250.0 + 0.3 * 3.5 + 0.15 * 4.25, rel=1e-2
    )

    # Winds are grid-relative in the file; decode must rotate to earth-relative.
    exp_u, exp_v = _rotate_grid_wind_to_earth([3.0], [-1.5], [lon_in])
    assert lev850["raw_u_wind_m_s"] == pytest.approx(exp_u[0], rel=1e-3)
    assert lev850["raw_v_wind_m_s"] == pytest.approx(exp_v[0], rel=1e-3)
    # The point is ~12° from LoV, so rotation must actually change the vector.
    assert not (
        lev850["raw_u_wind_m_s"] == pytest.approx(3.0, rel=1e-3)
        and lev850["raw_v_wind_m_s"] == pytest.approx(-1.5, rel=1e-3)
    )
    exp_u5, exp_v5 = _rotate_grid_wind_to_earth([-2.0], [4.0], [lon_in])
    assert lev500["raw_u_wind_m_s"] == pytest.approx(exp_u5[0], rel=1e-3)
    assert lev500["raw_v_wind_m_s"] == pytest.approx(exp_v5[0], rel=1e-3)

    # Surface PRES keys at the sentinel 0, never colliding with hPa levels.
    assert pt[0]["surface_pressure_pa"] == pytest.approx(101325.0)


def test_decode_hrrr_pressure_grid_corner_point(sounding_bytes):
    """A point exactly on grid node (0, 0) decodes the corner values."""
    lat_pt, lon_pt = _target_latlon(0.0, 0.0)
    results, covered = decode_hrrr_pressure_per_point(
        sounding_bytes, [lat_pt], [lon_pt]
    )
    assert covered == [True]
    assert results[0][850]["raw_temperature_k"] == pytest.approx(280.0, rel=1e-2)


def test_decode_hrrr_pressure_empty_bytes():
    results, covered = decode_hrrr_pressure_per_point(b"", [39.0], [-104.0])
    assert results == [{}]
    assert covered == [False]


def test_surface_pressure_sentinel_dropped_by_builder():
    """Key 0 must not become a PressureLevelData — the flow reads it raw.

    build_pressure_levels_from_grib distinguishes levels from the surface
    entry by conversion: the sentinel dict carries only surface_pressure_pa,
    which _convert_raw_sounding does not convert, so the level is dropped.
    """
    levels = build_pressure_levels_from_grib(
        {
            0: {"surface_pressure_pa": 101325.0},
            850: {"raw_temperature_k": 280.0},
        }
    )
    assert [pl.pressure_hpa for pl in levels] == [850]


# ---------------------------------------------------------------------------
# decode_hrrr_diag_per_point
# ---------------------------------------------------------------------------


def test_decode_hrrr_diag_fields(diag_bytes):
    lat_in, lon_in = _target_latlon(3.5, 4.25)
    results = decode_hrrr_diag_per_point(diag_bytes, [lat_in], [lon_in])

    assert len(results) == 1
    diag = results[0]
    assert diag["low_cover_pct"] == pytest.approx(55.0 + 1.0 * 3.5 + 0.5 * 4.25, rel=1e-2)
    assert diag["mid_cover_pct"] == pytest.approx(33.0 + 0.5 * 3.5 + 0.25 * 4.25, rel=1e-2)
    assert diag["high_cover_pct"] == pytest.approx(22.0 + 0.25 * 3.5 + 0.125 * 4.25, rel=1e-2)
    assert diag["total_cover_pct"] == pytest.approx(80.0 + 0.5 * 3.5 + 0.25 * 4.25, rel=1e-2)
    assert diag["ceiling_m"] == pytest.approx(1500.0, rel=1e-2)
    assert diag["cloud_base_m"] == pytest.approx(900.0, rel=1e-2)
    assert diag["sfc_cape_jkg"] == pytest.approx(1500.0, rel=1e-2)
    assert diag["ml_cape_jkg"] == pytest.approx(900.0, rel=1e-2)
    assert diag["sfc_cin_jkg"] == pytest.approx(50.0, rel=1e-2)
    assert diag["ml_cin_jkg"] == pytest.approx(25.0, rel=1e-2)
    assert diag["visibility_m"] == pytest.approx(8000.0, rel=1e-2)
    assert diag["gust_ms"] == pytest.approx(12.0, rel=1e-2)


def test_decode_hrrr_diag_outside_grid_empty(diag_bytes):
    results = decode_hrrr_diag_per_point(diag_bytes, [51.5], [-0.12])
    assert results == [{}]


def test_decode_hrrr_diag_empty_bytes():
    assert decode_hrrr_diag_per_point(b"", [39.0], [-104.0]) == [{}]


# ---------------------------------------------------------------------------
# decode_worker entries
# ---------------------------------------------------------------------------


def test_decode_worker_hrrr_entries(tmp_path, sounding_bytes, diag_bytes):
    from weatherbrief.fetch.grib import decode_worker

    lat_in, lon_in = _target_latlon(3.5, 4.25)

    snd_path = tmp_path / "hrrr_sounding.grib2"
    snd_path.write_bytes(sounding_bytes)
    results, covered = decode_worker.decode_hrrr_pressure(
        str(snd_path), [lat_in], [lon_in]
    )
    assert covered == [True]
    assert 850 in results[0]
    assert results[0][0]["surface_pressure_pa"] == pytest.approx(101325.0)

    diag_path = tmp_path / "hrrr_diag.grib2"
    diag_path.write_bytes(diag_bytes)
    diags = decode_worker.decode_hrrr_diag(str(diag_path), [lat_in], [lon_in])
    assert diags[0]["gust_ms"] == pytest.approx(12.0, rel=1e-2)


# ---------------------------------------------------------------------------
# build_hrrr_cloud_diagnostics / build_hrrr_surface_extras
# ---------------------------------------------------------------------------
#
# CIN sign convention: measured on the real 00z f00 wrfprs file
# (2026-07-31, byte-range fetch of the CIN:surface and CIN:180-0mb
# messages from noaa-hrrr-bdp-pds) — HRRR CIN is delivered ALREADY
# NEGATIVE-or-zero in J/kg (surface min −804 / max 0 over 1.9M cells;
# 180-0mb min −776 / max 0). That is exactly the app's internal
# convention, so the builder passes CIN through unchanged.

_HRRR_RAW_FULL: dict[str, float] = {
    "low_cover_pct": 55.0,
    "mid_cover_pct": 33.0,
    "high_cover_pct": 22.0,
    "total_cover_pct": 80.0,
    "ceiling_m": 1500.0,
    "cloud_base_m": 900.0,
    "ml_cape_jkg": 900.0,
    "ml_cin_jkg": -25.0,
    "sfc_cape_jkg": 1500.0,
    "sfc_cin_jkg": -50.0,
    "visibility_m": 8000.0,
    "gust_ms": 12.0,
}


def test_build_hrrr_cloud_diagnostics_full_mapping():
    diag = build_hrrr_cloud_diagnostics(dict(_HRRR_RAW_FULL))

    assert diag is not None
    # Covers are already 0–100 % → straight passthrough.
    assert diag.low.cover_pct == 55.0
    assert diag.mid.cover_pct == 33.0
    assert diag.high.cover_pct == 22.0
    assert diag.total_cover_pct == 80.0
    # Heights convert m → ft.
    assert diag.ceiling_ft == round(1500.0 * 3.28084)
    assert diag.low.base_ft == round(900.0 * 3.28084)
    # Stability indices from the 180-0 mb (mixed-layer) entries.
    assert diag.ml_cape_jkg == 900.0
    assert diag.ml_cin_jkg == -25.0
    # HRRR has no convective scheme — those fields stay unset.
    assert diag.convective_cover_pct is None
    assert diag.convective_base_ft is None
    assert diag.convective_top_ft is None
    assert diag.convective_precip_mm_h is None
    # Mid/high layers carry no per-band geometry in the ECMWF shape.
    assert diag.mid.base_ft is None
    assert diag.high.base_ft is None


def test_build_hrrr_cloud_diagnostics_cin_negative_passthrough():
    """HRRR CIN arrives already negative — no negation, no _normalize_model_cin."""
    diag = build_hrrr_cloud_diagnostics({"ml_cin_jkg": -412.0})
    assert diag is not None
    assert diag.ml_cin_jkg == -412.0


def test_build_hrrr_cloud_diagnostics_zero_is_data():
    """None ≠ 0: a zero CIN/cover is a real value, not a missing one."""
    diag = build_hrrr_cloud_diagnostics({"ml_cin_jkg": 0.0, "total_cover_pct": 0.0})
    assert diag is not None
    assert diag.ml_cin_jkg == 0.0
    assert diag.total_cover_pct == 0.0


def test_build_hrrr_cloud_diagnostics_missing_fields_stay_none():
    diag = build_hrrr_cloud_diagnostics({"low_cover_pct": 40.0})
    assert diag is not None
    assert diag.low.cover_pct == 40.0
    assert diag.ceiling_ft is None
    assert diag.low.base_ft is None
    assert diag.ml_cape_jkg is None
    assert diag.ml_cin_jkg is None
    assert diag.total_cover_pct is None


def test_build_hrrr_cloud_diagnostics_negative_height_dropped():
    """A cloud below ground is a decode artifact (sibling precedent: ICON)."""
    diag = build_hrrr_cloud_diagnostics({"ceiling_m": -5.0, "low_cover_pct": 10.0})
    assert diag is not None
    assert diag.ceiling_ft is None


def test_build_hrrr_cloud_diagnostics_empty_or_surface_only_returns_none():
    assert build_hrrr_cloud_diagnostics({}) is None
    # Surface-only keys are not cloud diagnostics → no partial model.
    assert build_hrrr_cloud_diagnostics(
        {"visibility_m": 8000.0, "gust_ms": 12.0, "sfc_cape_jkg": 1500.0}
    ) is None


def test_build_hrrr_surface_extras_full_mapping():
    extras = build_hrrr_surface_extras(dict(_HRRR_RAW_FULL))

    assert extras["visibility_m"] == 8000.0
    assert extras["wind_gusts_10m_kt"] == pytest.approx(12.0 * 1.94384)
    assert extras["cape_jkg"] == 1500.0
    # Same sign convention as ml_cin: already negative → passthrough.
    assert extras["convective_inhibition_jkg"] == -50.0


def test_build_hrrr_surface_extras_empty_raw_all_none():
    extras = build_hrrr_surface_extras({})
    assert extras == {
        "visibility_m": None,
        "wind_gusts_10m_kt": None,
        "cape_jkg": None,
        "convective_inhibition_jkg": None,
    }
