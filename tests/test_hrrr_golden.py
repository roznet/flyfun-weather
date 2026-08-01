"""Golden decode tests on a real HRRR wrfprs sample (#457).

Run with: pytest tests/test_hrrr_golden.py -v

The samples live in tests/data/hrrr_samples/ (gitignored — large binaries)
and are produced by scripts/download_hrrr_samples.sh, which byte-ranges the
f01 file of a recent 00z run off the public NOAA S3 bucket. The TESTS never
hit the network: they read only the local sample files and skip cleanly when
the directory is absent (same precedent as tests/test_ecmwf_sample.py).

Covered end-to-end on real data:

- The sample's Lambert grid definition matches HRRR_GRID — the constants the
  decode fallback and the domain gate rely on.
- Projection anchor (#457 review): the decode-built grid axes, inverted
  through an INDEPENDENT pyproj construction (message GRIB_* attrs + the
  published HRRR spherical radius, both written out in this file), reproduce
  the message's own 2-D latitude/longitude coordinate arrays — written by
  eccodes from the GRIB grid definition, not by our projection code. Without
  this anchor every other oracle shares the decode projection helpers, so a
  wrong formula (earth radius, LoV handling, axis order) would keep decode
  and oracle self-consistent and invisible.
- Projected-grid bilinear interpolation: TMP 850 mb at KDEN lands within
  0.5 K of the nearest grid cell read straight from the message (nearest
  cell computed via the same projection), and an off-grid point is reported
  uncovered.
- Wind rotation: at KDEN (>= 5° of longitude from LoV 262.5°) the decoded
  earth-relative UGRD/VGRD differ from the raw grid-relative vector and
  match the α closed form WRITTEN OUT in this file (#457 review — importing
  ``_rotate_grid_wind_to_earth`` to compute expectations would make a sign
  flip or wrong α formula in the implementation invisible).
- Diagnostics: ceiling/covers decode without error (clear-sky cells are
  missing, not crashes) and REFC obeys HRRR's −10 dBZ no-echo floor (or NaN
  quiet cells) without crashing.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pytest

from weatherbrief.fetch.grib.decode import (
    _frac_grid_indices,
    _lambert_dataset_axes,
    _lambert_grid_attrs,
    _lcc_project_points,
    build_hrrr_cloud_diagnostics,
    decode_hrrr_diag_per_point,
    decode_hrrr_pressure_per_point,
)
from weatherbrief.fetch.grib.hrrr_fetch import HRRR_GRID, hrrr_projection

# ---------------------------------------------------------------------------
# Sample directory resolution
# ---------------------------------------------------------------------------

_SAMPLE_DIR_ENV = os.environ.get("HRRR_GRIB_DIR")
_SAMPLE_DIR_LOCAL = Path(__file__).parent / "data" / "hrrr_samples"

SAMPLE_DIR = Path(_SAMPLE_DIR_ENV) if _SAMPLE_DIR_ENV else _SAMPLE_DIR_LOCAL

SOUNDING_FILE = SAMPLE_DIR / "hrrr_sounding_f01.grib2"
DIAG_FILE = SAMPLE_DIR / "hrrr_diag_f01.grib2"


def _has_samples() -> bool:
    return SOUNDING_FILE.is_file() and DIAG_FILE.is_file()


skip_no_samples = pytest.mark.skipif(
    not _has_samples(),
    reason=f"No HRRR sample files in {SAMPLE_DIR} (run scripts/download_hrrr_samples.sh)",
)

# KDEN — well inside CONUS and >= 5° of longitude from LoV 262.5° (|255.33 -
# 262.5| = 7.17°), so the grid→earth wind rotation is visible there.
KDEN_LAT, KDEN_LON = 39.86, -104.67

# Off-grid reference: tropical Atlantic, far outside the Lambert grid.
OFF_GRID_LAT, OFF_GRID_LON = 20.0, -60.0


# ---------------------------------------------------------------------------
# Helpers — read values straight from a message via the same projection the
# decode path uses (_lambert_dataset_axes + _lcc_project_points).
#
# The two helpers below (_message_grid_attrs / _message_lambert_proj) are the
# exception on purpose (#457 review): they rebuild the projection from the
# message's own GRIB_* attrs WITHOUT the decode module, so TestProjectionAnchor
# can check the decode projection against independent ground truth instead of
# against itself.
# ---------------------------------------------------------------------------


def _open_sample(path: Path) -> list:
    import cfgrib

    return cfgrib.open_datasets(str(path), backend_kwargs={"indexpath": ""})


def _isobaric_dataset(datasets: list):
    """The (isobaricInhPa, y, x) dataset of the sounding sample."""
    return next(ds for ds in datasets if "isobaricInhPa" in ds.sizes)


def _level_index(ds, level_hpa: int) -> int:
    levels = np.asarray(ds.coords["isobaricInhPa"].values)
    return int(np.flatnonzero(levels == level_hpa)[0])


# GRIB2 Lambert projection keys, written out here rather than imported from
# decode._LAMBERT_GRID_ATTR_KEYS — that mapping is part of the code under test.
_MESSAGE_PROJ_ATTR_KEYS = (
    "GRIB_LaDInDegrees",
    "GRIB_LoVInDegrees",
    "GRIB_Latin1InDegrees",
    "GRIB_Latin2InDegrees",
)

# Published NCEP HRRR earth shape (GRIB2 template 3.30, shape-of-earth code 6):
# spherical, 6371229 m. eccodes uses the same definition to compute the
# message's 2-D lat/lon arrays, so the anchor test below validates this
# constant against real data on every run.
_HRRR_EARTH_RADIUS_M = 6371229.0


def _message_grid_attrs(ds) -> dict[str, float]:
    """Projection attrs read STRAIGHT from a data var's GRIB_* attrs."""
    for var in ds.data_vars.values():
        if all(k in var.attrs for k in _MESSAGE_PROJ_ATTR_KEYS):
            return {k: float(var.attrs[k]) for k in _MESSAGE_PROJ_ATTR_KEYS}
    raise AssertionError("no data var carries the GRIB Lambert projection attrs")


def _message_lambert_proj(ds):
    """pyproj construction independent of the decode module and HRRR_GRID.

    Parameters come from the message itself (_message_grid_attrs) plus the
    published HRRR spherical radius. If the decode-side projection helper has
    a wrong formula (earth radius, LoV handling, axis order), this anchor
    diverges from it and TestProjectionAnchor fails.
    """
    import pyproj

    attrs = _message_grid_attrs(ds)
    return pyproj.Proj(
        proj="lcc",
        lat_0=attrs["GRIB_LaDInDegrees"],
        lon_0=attrs["GRIB_LoVInDegrees"],
        lat_1=attrs["GRIB_Latin1InDegrees"],
        lat_2=attrs["GRIB_Latin2InDegrees"],
        a=_HRRR_EARTH_RADIUS_M,
        b=_HRRR_EARTH_RADIUS_M,
    )


def _frac_indices_at(ds, lat: float, lon: float) -> tuple[float, float, bool]:
    """Fractional (j, i) grid indices of (lat, lon) on the sample's own grid."""
    projected = _lambert_dataset_axes(ds)
    assert projected is not None, "sample dataset is not a projected (y, x) grid"
    y_axis, x_axis, grid_attrs = projected
    xs, ys = _lcc_project_points(grid_attrs, [lat], [lon])
    frac_j, ok_j = _frac_grid_indices(y_axis, ys)
    frac_i, ok_i = _frac_grid_indices(x_axis, xs)
    return frac_j[0], frac_i[0], bool(ok_j[0] and ok_i[0])


def _bilinear_and_nearest(
    values2d: np.ndarray, frac_j: float, frac_i: float,
) -> tuple[float, float]:
    """(bilinear, nearest-cell) of a 2-D field at a fractional grid position."""
    j0, i0 = int(math.floor(frac_j)), int(math.floor(frac_i))
    aj, ai = frac_j - j0, frac_i - i0
    interp = (
        (1.0 - aj) * (1.0 - ai) * values2d[j0, i0]
        + (1.0 - aj) * ai * values2d[j0, i0 + 1]
        + aj * (1.0 - ai) * values2d[j0 + 1, i0]
        + aj * ai * values2d[j0 + 1, i0 + 1]
    )
    nearest = values2d[int(round(frac_j)), int(round(frac_i))]
    return float(interp), float(nearest)


# ---------------------------------------------------------------------------
# Grid definition
# ---------------------------------------------------------------------------


@skip_no_samples
class TestGridDefinition:
    """The sample's grid attrs must match the HRRR_GRID constants."""

    def test_grid_attrs_match_hrrr_grid(self):
        datasets = _open_sample(SOUNDING_FILE)
        try:
            ds = _isobaric_dataset(datasets)
            attrs = _lambert_grid_attrs(ds)
            assert ds.sizes["x"] == HRRR_GRID.nx
            assert ds.sizes["y"] == HRRR_GRID.ny
            assert attrs["dx"] == pytest.approx(HRRR_GRID.dx, abs=1.0)
            assert attrs["dy"] == pytest.approx(HRRR_GRID.dy, abs=1.0)
            assert attrs["lat0"] == pytest.approx(HRRR_GRID.lat0, abs=1e-4)
            assert attrs["lon0"] == pytest.approx(HRRR_GRID.lon0, abs=1e-4)
            assert attrs["lad"] == pytest.approx(HRRR_GRID.lad, abs=1e-6)
            assert attrs["lov"] == pytest.approx(HRRR_GRID.lov, abs=1e-6)
            assert attrs["latin1"] == pytest.approx(HRRR_GRID.latin1, abs=1e-6)
            assert attrs["latin2"] == pytest.approx(HRRR_GRID.latin2, abs=1e-6)
        finally:
            for ds in datasets:
                ds.close()


# ---------------------------------------------------------------------------
# Projection anchor (#457 review): independent ground truth for the projection
# ---------------------------------------------------------------------------


@skip_no_samples
class TestProjectionAnchor:
    """The decode projection must agree with ground truth it did NOT compute.

    Every other oracle in this file locates grid cells via the decode module's
    own projection helpers, so a wrong projection formula (earth radius, LoV
    handling, axis order) would keep decode and oracle self-consistent and the
    suite green. The anchors here break that circularity:

    - The message's own 2-D latitude/longitude coordinate arrays — computed
      by eccodes from the GRIB grid definition, independent of our pyproj
      construction — must match the inverse projection of the decode-built
      grid axes.
    - A pyproj forward of KDEN's published lat/lon, built in THIS file from
      the message attrs, must land on the same fractional grid indices the
      decode helpers produce.
    """

    # On the 2026-07-31 sample the axes agree with the message lat/lon to
    # ~1e-8 m (float round-off; eccodes and pyproj evaluate the same
    # spherical Lambert formulas). 1 m leaves enormous headroom for a
    # re-downloaded sample, while a +1 km earth-radius error moves the NE
    # corner by ~980 m and even the KDEN cell by ~430 m (verified by
    # mutation): any real formula error is caught many times over.
    _TOL_M = 1.0

    def test_inverse_projected_axes_match_message_latlon(self):
        datasets = _open_sample(SOUNDING_FILE)
        try:
            ds = _isobaric_dataset(datasets)
            proj = _message_lambert_proj(ds)
            projected = _lambert_dataset_axes(ds)
            assert projected is not None, "sample dataset is not a projected (y, x) grid"
            y_axis, x_axis, _ = projected
            lat2d = np.asarray(ds["latitude"].values, dtype=np.float64)
            lon2d = np.asarray(ds["longitude"].values, dtype=np.float64)
            ny, nx = lat2d.shape
            # Spread across the grid: SW origin, far NE corner (largest lever
            # arm against a scale error), centre, two quadrants, and KDEN's
            # nearest cell.
            cells = [
                (0, 0),
                (ny - 1, nx - 1),
                (ny // 2, nx // 2),
                (ny // 4, 3 * nx // 4),
                (3 * ny // 4, nx // 4),
                (587, 695),  # KDEN's nearest cell on the 00z sample
            ]
            for j, i in cells:
                lon_inv, lat_inv = proj(
                    float(x_axis[i]), float(y_axis[j]), inverse=True,
                )
                dlon = ((lon_inv - lon2d[j, i] + 180.0) % 360.0) - 180.0
                dlat = lat_inv - lat2d[j, i]
                err_m = math.hypot(
                    math.radians(dlat),
                    math.radians(dlon) * math.cos(math.radians(lat2d[j, i])),
                ) * _HRRR_EARTH_RADIUS_M
                assert err_m < self._TOL_M, (
                    f"cell (j={j}, i={i}): axis inverts to {err_m:.3f} m from "
                    f"the message's own lat/lon"
                )
        finally:
            for ds in datasets:
                ds.close()

    def test_kden_forward_projection_matches_helper_fractional_indices(self):
        datasets = _open_sample(SOUNDING_FILE)
        try:
            ds = _isobaric_dataset(datasets)
            proj = _message_lambert_proj(ds)
            projected = _lambert_dataset_axes(ds)
            assert projected is not None
            y_axis, x_axis, _ = projected

            # Decode path: helpers all the way down.
            frac_j_h, frac_i_h, in_bounds = _frac_indices_at(ds, KDEN_LAT, KDEN_LON)
            assert in_bounds

            # This file's independent forward of KDEN's published lat/lon,
            # indexed on the same axes.
            x_k, y_k = proj(KDEN_LON, KDEN_LAT)
            frac_j, ok_j = _frac_grid_indices(y_axis, [y_k])
            frac_i, ok_i = _frac_grid_indices(x_axis, [x_k])
            assert bool(ok_j[0] and ok_i[0])

            # Actual agreement is ~1e-9 cells (same pyproj, same parameters);
            # 0.01 cells = 30 m — far inside the review's ~0.5-cell bar, while
            # a +1 km earth-radius error splits the two constructions by
            # ~0.042 cells here and swapped args land whole degrees off.
            assert abs(frac_j_h - frac_j[0]) < 0.01
            assert abs(frac_i_h - frac_i[0]) < 0.01
        finally:
            for ds in datasets:
                ds.close()


# ---------------------------------------------------------------------------
# Projected-grid interpolation
# ---------------------------------------------------------------------------


@skip_no_samples
class TestProjectedInterpolation:
    """Bilinear interpolation on the Lambert (y, x) grid."""

    def test_tmp850_kden_within_half_kelvin_of_nearest_cell(self):
        results, covered = decode_hrrr_pressure_per_point(
            SOUNDING_FILE.read_bytes(), [KDEN_LAT], [KDEN_LON],
        )
        assert covered == [True]
        decoded_t = results[0][850]["raw_temperature_k"]

        datasets = _open_sample(SOUNDING_FILE)
        try:
            ds = _isobaric_dataset(datasets)
            frac_j, frac_i, in_bounds = _frac_indices_at(ds, KDEN_LAT, KDEN_LON)
            assert in_bounds
            t850 = np.asarray(ds["t"].values, dtype=np.float64)[_level_index(ds, 850)]
            _, nearest = _bilinear_and_nearest(t850, frac_j, frac_i)
        finally:
            for ds in datasets:
                ds.close()

        assert abs(decoded_t - nearest) < 0.5

    def test_off_grid_point_uncovered(self):
        results, covered = decode_hrrr_pressure_per_point(
            SOUNDING_FILE.read_bytes(), [OFF_GRID_LAT], [OFF_GRID_LON],
        )
        assert covered == [False]
        assert results == [{}]


# ---------------------------------------------------------------------------
# Wind rotation
# ---------------------------------------------------------------------------


@skip_no_samples
class TestWindRotation:
    """Grid-relative → earth-relative rotation on a real 850 mb wind field."""

    def test_rotated_wind_differs_and_matches_closed_form(self):
        # Precondition: KDEN is far enough from LoV for the rotation to matter.
        delta_lon = ((KDEN_LON - HRRR_GRID.lov + 180.0) % 360.0) - 180.0
        assert abs(delta_lon) >= 5.0

        results, covered = decode_hrrr_pressure_per_point(
            SOUNDING_FILE.read_bytes(), [KDEN_LAT], [KDEN_LON],
        )
        assert covered == [True]
        decoded = results[0][850]

        # Raw grid-relative vector: bilinear sample straight from the message.
        datasets = _open_sample(SOUNDING_FILE)
        try:
            ds = _isobaric_dataset(datasets)
            frac_j, frac_i, in_bounds = _frac_indices_at(ds, KDEN_LAT, KDEN_LON)
            assert in_bounds
            li = _level_index(ds, 850)
            u_raw, _ = _bilinear_and_nearest(
                np.asarray(ds["u"].values, dtype=np.float64)[li], frac_j, frac_i,
            )
            v_raw, _ = _bilinear_and_nearest(
                np.asarray(ds["v"].values, dtype=np.float64)[li], frac_j, frac_i,
            )
            # LoV / Latin read STRAIGHT from the message (not via the decode
            # module's attr mapping), for the expectation below.
            msg_attrs = _message_grid_attrs(ds)
        finally:
            for ds in datasets:
                ds.close()

        # Expected earth-relative vector: the α closed form written out HERE
        # (#457 review). Computing expectations with the decode module's
        # _rotate_grid_wind_to_earth would make the oracle circular — a sign
        # flip or wrong α formula there would change decode and expectation
        # together and stay invisible. The form below is the CORRECT
        # direction, independently verified against pyproj finite differences
        # of the actual projection (test_hrrr_decode.py
        # ::test_rotation_matches_pyproj_projection_geometry):
        #
        #   α = (λ − LoV) · sin(38.5°), normalised to ±180°
        #       (cone constant k = sin φc with Latin1 == Latin2 == 38.5°,
        #        which the message satisfies)
        #   u_e = u·cos α + v·sin α
        #   v_e = −u·sin α + v·cos α
        assert msg_attrs["GRIB_Latin1InDegrees"] == pytest.approx(38.5, abs=1e-6)
        assert msg_attrs["GRIB_Latin2InDegrees"] == pytest.approx(38.5, abs=1e-6)
        cone_const = math.sin(math.radians(38.5))
        dlon_deg = (
            (KDEN_LON - msg_attrs["GRIB_LoVInDegrees"] + 180.0) % 360.0
        ) - 180.0
        alpha = math.radians(dlon_deg) * cone_const
        exp_u = u_raw * math.cos(alpha) + v_raw * math.sin(alpha)
        exp_v = -u_raw * math.sin(alpha) + v_raw * math.cos(alpha)

        # The earth-relative result differs from the raw grid-relative vector…
        assert math.hypot(
            decoded["raw_u_wind_m_s"] - u_raw, decoded["raw_v_wind_m_s"] - v_raw,
        ) > 1e-3
        # …and matches the independently written closed-form rotation.
        assert decoded["raw_u_wind_m_s"] == pytest.approx(exp_u, abs=1e-6)
        assert decoded["raw_v_wind_m_s"] == pytest.approx(exp_v, abs=1e-6)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


@skip_no_samples
class TestDiagnostics:
    """Ceiling/covers/CAPE/CIN and REFC on the diag sample."""

    def test_covers_and_stability_decode_at_kden(self):
        results = decode_hrrr_diag_per_point(
            DIAG_FILE.read_bytes(), [KDEN_LAT], [KDEN_LON],
        )
        raw = results[0]
        for key in (
            "low_cover_pct", "mid_cover_pct", "high_cover_pct",
            "total_cover_pct", "ml_cape_jkg", "ml_cin_jkg",
        ):
            assert key in raw
        # ceiling_m is legitimately absent under clear sky (missing bitmap);
        # when present it must be a plausible height. Either way the builder
        # must not crash.
        if "ceiling_m" in raw:
            assert 0.0 < raw["ceiling_m"] < 20000.0
        assert build_hrrr_cloud_diagnostics(raw) is not None

    def test_ceiling_matches_message_at_cloudy_cell(self):
        datasets = _open_sample(DIAG_FILE)
        try:
            ceiling_ds = next(
                ds for ds in datasets
                if any(
                    v.attrs.get("GRIB_typeOfLevel") == "cloudCeiling"
                    for v in ds.data_vars.values()
                )
            )
            ceiling = np.asarray(ceiling_ds["gh"].values, dtype=np.float64)
            assert np.isfinite(ceiling).any(), "no valid ceiling cells in sample"
            projected = _lambert_dataset_axes(ceiling_ds)
            assert projected is not None
            y_axis, x_axis, _ = projected

            # Invert the projection at the centre of a fully-finite 3×3
            # ceiling block (a NaN corner would NaN out the decode-side
            # gather, and float round-trip can shift floor() by one cell).
            proj = hrrr_projection()
            lat_c = lon_c = None
            for j in range(1, ceiling.shape[0] - 1):
                for i in range(1, ceiling.shape[1] - 1):
                    if np.isfinite(ceiling[j - 1 : j + 2, i - 1 : i + 2]).all():
                        lon_c, lat_c = proj(x_axis[i], y_axis[j], inverse=True)
                        break
                if lat_c is not None:
                    break
            assert lat_c is not None, "no fully-valid ceiling block found"

            # Expected value: manual bilinear of the message field at the
            # same fractional position (same helpers the decode path uses).
            frac_j, frac_i, in_bounds = _frac_indices_at(ceiling_ds, lat_c, lon_c)
            assert in_bounds
            expected, _ = _bilinear_and_nearest(ceiling, frac_j, frac_i)
        finally:
            for ds in datasets:
                ds.close()

        results = decode_hrrr_diag_per_point(
            DIAG_FILE.read_bytes(), [lat_c], [lon_c],
        )
        assert results[0]["ceiling_m"] == pytest.approx(expected, rel=1e-6)

    def test_refc_floor_or_quiet_nan_no_crash(self):
        datasets = _open_sample(DIAG_FILE)
        try:
            refc_var = next(
                ds.data_vars["refc"] for ds in datasets if "refc" in ds.data_vars
            )
            values = np.asarray(refc_var.values, dtype=np.float64)
            finite = values[np.isfinite(values)]
            # HRRR writes no-echo cells at the −10 dBZ floor; NaN quiet cells
            # are the other valid form. Either way: nothing below the floor,
            # nothing above the physical maximum.
            assert finite.size > 0
            assert finite.min() >= -10.0
            assert finite.max() <= 80.0

            # Per-point sample at KDEN (quiet tonight): NaN or floor, no crash.
            refc_ds = next(ds for ds in datasets if "refc" in ds.data_vars)
            frac_j, frac_i, in_bounds = _frac_indices_at(refc_ds, KDEN_LAT, KDEN_LON)
            assert in_bounds
            refc_at_kden, _ = _bilinear_and_nearest(values, frac_j, frac_i)
            assert math.isnan(refc_at_kden) or refc_at_kden >= -10.0
        finally:
            for ds in datasets:
                ds.close()
