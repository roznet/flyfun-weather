"""Golden decode tests against a real HRRR sample (issue #457).

The sample is a byte-ranged subset of a real ``wrfprs`` file (500 mb sounding
messages + the cloud-diagnostic / CAPE set) from the public NOAA bucket. It is
gitignored (~16 MB) and every test here skips when it is absent, so CI without
it stays green.

    ./scripts/download_hrrr_samples.sh

creates it at ``tests/data/hrrr_samples/hrrr_sample.grib2``. Run that first.

These tests verify the Lambert-grid decode end-to-end on real data, which is
the only place several of these facts CAN be verified — a synthetic fixture
would just restate our own assumptions about the grid:

- the projection, against the file's own 2-D lat/lon arrays (the one oracle
  in the file our pyproj math does not produce);
- bilinear interpolation against a nearest-cell readback;
- the CIMIXR-decodes-as-``unknown`` mapping;
- grid-relative → earth-relative wind rotation;
- the diagnostics field map and its ECMWF-like (no per-band geometry) shape.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

SAMPLE_DIR = Path(__file__).parent / "data" / "hrrr_samples"

# KBOS — inside CONUS, used by the issue's integration route.
_KBOS = (42.3656, -71.0096)


def _sample_path() -> Path | None:
    if not SAMPLE_DIR.exists():
        return None
    files = sorted(SAMPLE_DIR.glob("*.grib2"))
    return files[0] if files else None


skip_no_sample = pytest.mark.skipif(
    _sample_path() is None,
    reason=f"No HRRR sample files in {SAMPLE_DIR}",
)


@skip_no_sample
class TestHrrrSampleSoundingDecode:
    @pytest.fixture(scope="class")
    def decoded(self):
        from weatherbrief.fetch.grib.decode import decode_hrrr_pressure_per_point

        results = decode_hrrr_pressure_per_point(
            _sample_path(), [_KBOS[0]], [_KBOS[1]],
        )
        assert len(results) == 1
        return results[0]

    def test_full_sounding_fields_at_500mb(self, decoded):
        assert 500 in decoded
        lvl = decoded[500]
        # Direct fields — all sourced without derivation.
        t = lvl["raw_temperature_k"]
        td = lvl["raw_dewpoint_k"]
        gh = lvl["geopotential_height_m"]
        assert 230.0 < t < 280.0
        assert td <= t + 0.5  # dewpoint never exceeds temperature
        assert 5200.0 < gh < 6100.0  # 500 mb surface altitude
        assert 0.0 <= lvl["raw_relative_humidity_pct"] <= 100.0
        assert abs(lvl["vertical_velocity_pa_s"]) < 30.0

    def test_cimixr_unknown_mapping_populates_ice(self, decoded):
        # CIMIXR has no eccodes shortName (decodes as ``unknown``); the HRRR
        # var map must still land it on ice_mixing_ratio_kg_kg.
        lvl = decoded[500]
        assert "ice_mixing_ratio_kg_kg" in lvl
        assert 0.0 <= lvl["ice_mixing_ratio_kg_kg"] < 0.01
        assert 0.0 <= lvl["cloud_liquid_water_kg_kg"] < 0.01

    def test_projection_matches_the_files_own_lat_lon_grid(self):
        """The projection, checked against an oracle it cannot influence.

        ``test_bilinear_matches_nearest_neighbour_readback`` picks its
        comparison cell with ``_lambert_fractional_indices`` — the function
        under test — so a projection error that shifts the target by a cell or
        two moves BOTH sides equally and the test still passes. It answers
        "is bilinear close to nearest?", not "is either one in the right
        place?". This test answers the second question, anchored to the only
        independent ground truth in the file: the 2-D ``latitude`` /
        ``longitude`` coordinate arrays eccodes derived from the grid
        definition, which our pyproj math never touches.

        Coarse then fine:

        1. the cell ``_lambert_fractional_indices`` lands in IS the cell whose
           published lat/lon is nearest the target;
        2. bilinearly resampling those same published arrays at the
           FRACTIONAL index reproduces the target to well under one 3 km cell.

        (2) is the mutation guard, and the reason the tolerance is metres
        rather than degrees. Substituting the WGS84 semi-major axis for the
        GRIB sphere radius (6371229 m) rescales the projected plane by ~1.1e-3,
        which at KBOS's ~1500 km from the grid origin displaces the target by
        ~1.6 km — over half a cell, invisible to a one-cell degree tolerance,
        and fatal here. Flipping a scan direction is catastrophic. The honest
        error budget on the other side is sub-metre: bilinear resampling of a
        smooth function over 3 km, plus the ~1e-6 degree precision of the grid
        attrs themselves. (PR #518 comparison — the idea of anchoring the
        golden test to an independent oracle is theirs.)
        """
        import warnings

        import cfgrib
        import numpy as np

        from weatherbrief.fetch.grib.decode import (
            _lambert_attrs,
            _lambert_fractional_indices,
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            datasets = cfgrib.open_datasets(
                str(_sample_path()), backend_kwargs={"indexpath": ""},
            )
        try:
            ds = next(
                d for d in datasets
                if "t" in d.data_vars and "latitude" in d.coords
            )
            attrs = _lambert_attrs(ds["t"].attrs)
            assert attrs is not None
            lat2d = np.asarray(ds["latitude"].values, dtype=np.float64)
            lon2d = np.asarray(ds["longitude"].values, dtype=np.float64)
        finally:
            for d in datasets:
                d.close()

        # eccodes publishes HRRR longitudes in 0–360; the target is signed.
        lon2d = ((lon2d + 180.0) % 360.0) - 180.0
        lat_t, lon_t = _KBOS
        cos_lat = math.cos(math.radians(lat_t))

        frac_y, frac_x = _lambert_fractional_indices(attrs, [lat_t], [lon_t])
        fy, fx = float(frac_y[0]), float(frac_x[0])

        # (1) Same cell the published grid says is nearest. Equirectangular
        # distance is exact enough to rank neighbours 3 km apart.
        d2 = (lat2d - lat_t) ** 2 + ((lon2d - lon_t) * cos_lat) ** 2
        near_y, near_x = np.unravel_index(int(np.argmin(d2)), d2.shape)
        assert (round(fy), round(fx)) == (int(near_y), int(near_x))

        # (2) Round-trip the fractional index back through the published
        # arrays and land on the target.
        i0 = min(max(int(math.floor(fy)), 0), lat2d.shape[0] - 2)
        j0 = min(max(int(math.floor(fx)), 0), lat2d.shape[1] - 2)
        ai, aj = fy - i0, fx - j0

        def _bilerp(a: "np.ndarray") -> float:
            return float(
                (1 - ai) * (1 - aj) * a[i0, j0]
                + (1 - ai) * aj * a[i0, j0 + 1]
                + ai * (1 - aj) * a[i0 + 1, j0]
                + ai * aj * a[i0 + 1, j0 + 1]
            )

        _M_PER_DEG_LAT = 111_320.0
        off_m = math.hypot(
            (_bilerp(lat2d) - lat_t) * _M_PER_DEG_LAT,
            (_bilerp(lon2d) - lon_t) * _M_PER_DEG_LAT * cos_lat,
        )
        assert off_m < 300.0, f"projection off by {off_m:.0f} m (> 1/10 cell)"

    def test_bilinear_matches_nearest_neighbour_readback(self, decoded):
        """Projected-grid interpolation vs a manual nearest-cell readback.

        Scope note: this compares VALUES, and picks the comparison cell with
        the same projection the decode used — so it cannot detect a projection
        that is consistently wrong. That is
        ``test_projection_matches_the_files_own_lat_lon_grid``'s job; keep
        both.
        """
        import warnings

        import cfgrib

        from weatherbrief.fetch.grib.decode import (
            _lambert_attrs,
            _lambert_fractional_indices,
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            datasets = cfgrib.open_datasets(
                str(_sample_path()), backend_kwargs={"indexpath": ""},
            )
        try:
            t_var = next(
                ds["t"] for ds in datasets
                if "t" in ds.data_vars and "isobaricInhPa" in ds.coords
            )
            attrs = _lambert_attrs(t_var.attrs)
            assert attrs is not None
            frac_y, frac_x = _lambert_fractional_indices(
                attrs, [_KBOS[0]], [_KBOS[1]],
            )
            j, i = round(float(frac_y[0])), round(float(frac_x[0]))
            nearest = float(t_var.sel(isobaricInhPa=500).values[j, i])
        finally:
            for d in datasets:
                d.close()

        # Bilinear vs nearest-neighbour on a 3 km grid: sub-kelvin agreement.
        assert decoded[500]["raw_temperature_k"] == pytest.approx(nearest, abs=1.0)

    def test_wind_rotated_to_earth_relative(self, decoded):
        """The rotation preserves speed and shifts direction by the local
        grid convergence (KBOS is ~26° east of the central meridian →
        ~16° of rotation — far from a no-op)."""
        import warnings

        import cfgrib

        from weatherbrief.fetch.grib.decode import (
            _decode_pressure_vars_from_datasets,
            _HRRR_FULL_VAR_MAP,
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            datasets = cfgrib.open_datasets(
                str(_sample_path()), backend_kwargs={"indexpath": ""},
            )
        try:
            raw_results, _ = _decode_pressure_vars_from_datasets(
                datasets, [_KBOS[0]], [_KBOS[1]], var_map=_HRRR_FULL_VAR_MAP,
            )
        finally:
            for d in datasets:
                d.close()

        u_g = raw_results[0][500]["raw_u_wind_m_s"]
        v_g = raw_results[0][500]["raw_v_wind_m_s"]
        u_e = decoded[500]["raw_u_wind_m_s"]
        v_e = decoded[500]["raw_v_wind_m_s"]
        # Rotation is norm-preserving...
        assert math.hypot(u_e, v_e) == pytest.approx(math.hypot(u_g, v_g), rel=1e-6)
        # ...and by the expected convergence angle for KBOS's longitude.
        expected_rot = math.radians(
            math.sin(math.radians(38.5)) * ((_KBOS[1] % 360) - 262.5)
        )
        got_rot = math.atan2(u_e * v_g - v_e * u_g, u_e * u_g + v_e * v_g)
        assert abs(got_rot - expected_rot) < math.radians(0.5)


@skip_no_sample
class TestHrrrSampleCloudDiagDecode:
    def test_diag_fields_decode_in_native_units(self):
        from weatherbrief.fetch.grib.decode import (
            build_hrrr_cloud_diagnostics,
            decode_hrrr_cloud_diag_per_point,
        )

        results = decode_hrrr_cloud_diag_per_point(
            _sample_path(), [_KBOS[0]], [_KBOS[1]],
        )
        assert len(results) == 1
        raw = results[0]
        # Band covers are already percentages.
        for key in ("low_cover_pct", "mid_cover_pct", "high_cover_pct",
                    "total_cover_pct"):
            if key in raw:
                assert 0.0 <= raw[key] <= 100.0
        # CAPE non-negative; CIN already negative (NCEP convention).
        if "ml_cape_jkg" in raw:
            assert raw["ml_cape_jkg"] >= 0.0
        if "ml_cin_jkg" in raw:
            assert raw["ml_cin_jkg"] <= 0.5

        diag = build_hrrr_cloud_diagnostics(raw)
        assert diag is not None
        # No per-band geometry beyond the overall base (ECMWF-like shape).
        assert diag.mid.base_ft is None
        assert diag.low.top_ft is None
