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
- Projected-grid bilinear interpolation: TMP 850 mb at KDEN lands within
  0.5 K of the nearest grid cell read straight from the message (nearest
  cell computed via the same projection), and an off-grid point is reported
  uncovered.
- Wind rotation: at KDEN (>= 5° of longitude from LoV 262.5°) the decoded
  earth-relative UGRD/VGRD differ from the raw grid-relative vector and
  match the ``_rotate_grid_wind_to_earth`` closed form applied to the raw
  bilinear sample.
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
    _rotate_grid_wind_to_earth,
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
        finally:
            for ds in datasets:
                ds.close()

        exp_u, exp_v = _rotate_grid_wind_to_earth(
            [u_raw], [v_raw], [KDEN_LON], lov_deg=HRRR_GRID.lov,
        )

        # The earth-relative result differs from the raw grid-relative vector…
        assert math.hypot(
            decoded["raw_u_wind_m_s"] - u_raw, decoded["raw_v_wind_m_s"] - v_raw,
        ) > 1e-3
        # …and matches the closed-form rotation of that raw vector.
        assert decoded["raw_u_wind_m_s"] == pytest.approx(exp_u[0], abs=1e-6)
        assert decoded["raw_v_wind_m_s"] == pytest.approx(exp_v[0], abs=1e-6)


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
