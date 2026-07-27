"""Golden decode tests against a real HRRR sample (issue #457).

The sample is a byte-ranged subset of a real ``wrfprs`` file (500 mb sounding
messages + the cloud-diagnostic / CAPE set), fetched from the public bucket:

    https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.{date}/conus/
        hrrr.t12z.wrfprsf06.grib2   (selected messages via .idx byte ranges)

Place it at ``tests/data/hrrr_samples/*.grib2`` (gitignored — ~16 MB). All
tests skip gracefully when absent, so CI without the sample stays green.

These tests verify the Lambert-grid decode end-to-end on real data: the
projected-grid interpolation against a manual nearest-neighbour readback,
the CIMIXR-decodes-as-``unknown`` mapping, grid-relative wind rotation, and
the diagnostics field map.
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

    def test_bilinear_matches_nearest_neighbour_readback(self, decoded):
        """Projected-grid interpolation vs a manual nearest-cell readback."""
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
            # Verify the projection against the file's own 2D lat/lon arrays.
            ds = next(d for d in datasets if "t" in d.data_vars)
            assert abs(float(ds["latitude"].values[j, i]) - _KBOS[0]) < 0.03
            lon_grid = float(ds["longitude"].values[j, i])
            assert abs(((lon_grid + 180) % 360 - 180) - _KBOS[1]) < 0.04
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
