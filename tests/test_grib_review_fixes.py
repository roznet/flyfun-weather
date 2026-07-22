"""Tests for the GRIB fetch/interpretation review fixes (2026-07-22).

Fix 1 (GFS averaging-window table) is covered in tests/test_grib_fill.py.
This file covers:
- Fix 2: ECMWF 9999 no-cloud sentinel masked BEFORE bilinear interpolation
  (no fabricated intermediate ceilings) + sentinel guards for mlcape/kx/totalx.
- Fix 3: convective_precip_mm_h (a windowed RATE) held over the covering
  interval (next anchor) instead of forward-filled into the wrong window.
- Fix 4: GFS prime-meridian cyclic longitude (xarray pad + bilinear weights)
  + ICON CAPE_ML defensive sentinel guard.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from weatherbrief.fetch.grib.decode import (
    _bilinear_grid_weights,
    _drop_ge_sentinel,
    _wrap_cyclic_lon,
    build_ecmwf_cloud_diagnostics,
    build_ecmwf_surface_snapshot,
    build_icon_cloud_diagnostics,
    decode_ecmwf_surface_per_point,
)
from weatherbrief.fetch.grib.fill import _fill_diag_hourly
from weatherbrief.models import HourlyForecast, NWPCloudDiagnostics


def _write_sample_grib(path, values, lats, lons, short_name="ceil"):
    """Minimal regular-lat-lon GRIB2 single-level file via eccodes samples."""
    import eccodes as ec

    gid = ec.codes_new_from_samples("regular_ll_sfc_grib2", ec.CODES_PRODUCT_GRIB)
    ec.codes_set(gid, "Ni", len(lons))
    ec.codes_set(gid, "Nj", len(lats))
    ec.codes_set(gid, "latitudeOfFirstGridPointInDegrees", float(lats[0]))
    ec.codes_set(gid, "latitudeOfLastGridPointInDegrees", float(lats[-1]))
    ec.codes_set(gid, "longitudeOfFirstGridPointInDegrees", float(lons[0]))
    ec.codes_set(gid, "longitudeOfLastGridPointInDegrees", float(lons[-1]))
    ec.codes_set(gid, "iDirectionIncrementInDegrees", float(lons[1] - lons[0]))
    ec.codes_set(gid, "jDirectionIncrementInDegrees", float(lats[1] - lats[0]))
    ec.codes_set(gid, "jScansPositively", 1)
    ec.codes_set(gid, "shortName", short_name)
    ec.codes_set_values(gid, [float(v) for v in values])
    path.write_bytes(ec.codes_get_message(gid))
    ec.codes_release(gid)


# ---------------------------------------------------------------------------
# Fix 2 — ECMWF 9999 sentinel masked before interpolation
# ---------------------------------------------------------------------------


class TestEcmwfSentinelPreMask:
    def test_no_fabricated_ceiling_between_real_and_sentinel(self, tmp_path):
        # 3x3 grid, all 500 m ceilings except one 9999 "no cloud" corner.
        lats = [49.0, 50.0, 51.0]
        lons = [8.0, 9.0, 10.0]
        values = [
            500.0, 500.0, 500.0,
            500.0, 500.0, 500.0,
            500.0, 500.0, 9999.0,
        ]
        grib = tmp_path / "a1.grib2"
        _write_sample_grib(grib, values, lats, lons)

        results, covered = decode_ecmwf_surface_per_point(
            grib, [49.1, 50.9], [8.1, 9.9],
        )
        clean, near_sentinel = results
        # Far point: stencil is all-real → a real ~500 m ceiling.
        assert clean["ceiling_m"] == pytest.approx(500.0, abs=1.0)
        # Near the sentinel corner the value must be None (masked corner →
        # conservative), NEVER a fabricated blend like ~2,874 m that the
        # unmasked path produced before this fix.
        assert near_sentinel.get("ceiling_m") is None
        assert not any(
            999.0 < v < 9998.0
            for v in near_sentinel.values()
        )


class TestEcmwfSentinelGuards:
    def test_mlcape_totalx_kx_sentinels_dropped(self):
        raw = {
            "total_cover_frac": 0.5,
            "ml_cape_jkg": 9999.0,
            "total_totals_c": 9999.0,
            "k_index_c": 9999.0,
        }
        diag = build_ecmwf_cloud_diagnostics(raw)
        assert diag is not None  # total cover keeps the diag alive
        assert diag.ml_cape_jkg is None
        assert diag.total_totals is None
        assert diag.k_index is None

    def test_kx_kelvin_still_converted(self):
        diag = build_ecmwf_cloud_diagnostics(
            {"total_cover_frac": 0.5, "k_index_c": 300.0},
        )
        assert diag.k_index == pytest.approx(26.85)

    def test_snapshot_mucape_sentinel_dropped(self):
        out = build_ecmwf_surface_snapshot({"mucape_jkg": 9999.0})
        assert out["cape_jkg"] is None
        out = build_ecmwf_surface_snapshot({"mucape_jkg": 800.0})
        assert out["cape_jkg"] == 800.0

    def test_drop_ge_sentinel_passthrough(self):
        assert _drop_ge_sentinel({"a": 42.0}, "a") == 42.0
        assert _drop_ge_sentinel({"a": 9999.0}, "a") is None
        assert _drop_ge_sentinel({}, "a") is None


# ---------------------------------------------------------------------------
# Fix 3 — rate field held over the covering interval
# ---------------------------------------------------------------------------


class TestRateCoveringIntervalFill:
    def _hour(self, t: datetime, diag: NWPCloudDiagnostics | None = None):
        h = HourlyForecast(time=t)
        h.nwp_cloud_diagnostics = diag
        return h

    def test_gap_hours_get_next_anchor_rate_prev_anchor_geometry(self):
        t0 = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        dry = NWPCloudDiagnostics(
            ceiling_ft=3000.0, convective_precip_mm_h=0.0,
        )
        firing = NWPCloudDiagnostics(
            ceiling_ft=5000.0, convective_precip_mm_h=2.5,
        )
        hours = [
            self._hour(t0, dry),
            self._hour(t0 + timedelta(hours=1)),   # gap
            self._hour(t0 + timedelta(hours=2)),   # gap
            self._hour(t0 + timedelta(hours=3), firing),
            self._hour(t0 + timedelta(hours=4)),   # trailing
        ]
        filled = _fill_diag_hourly(hours)
        assert filled == 3
        # Gap hours sit in the (12, 15] window → the NEXT anchor's rate,
        # not the previous window's 0.0 (which read "dry" during a firing
        # window before this fix).
        assert hours[1].nwp_cloud_diagnostics.convective_precip_mm_h == 2.5
        assert hours[2].nwp_cloud_diagnostics.convective_precip_mm_h == 2.5
        # Geometry stays persistence from the previous anchor.
        assert hours[1].nwp_cloud_diagnostics.ceiling_ft == 3000.0
        # Trailing hour keeps the last completed window's values.
        assert hours[3].nwp_cloud_diagnostics.convective_precip_mm_h == 2.5
        assert hours[4].nwp_cloud_diagnostics.convective_precip_mm_h == 2.5
        # Anchors untouched; filled hours are independent copies.
        assert hours[0].nwp_cloud_diagnostics is dry
        assert hours[1].nwp_cloud_diagnostics is not dry

    def test_missing_next_rate_means_unknown_not_stale(self):
        t0 = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        wet = NWPCloudDiagnostics(convective_precip_mm_h=1.0)
        unknown = NWPCloudDiagnostics(convective_precip_mm_h=None)
        hours = [
            self._hour(t0, wet),
            self._hour(t0 + timedelta(hours=1)),
            self._hour(t0 + timedelta(hours=3), unknown),
        ]
        _fill_diag_hourly(hours)
        # None ≠ 0: an unknown next-window rate must not be replaced by the
        # previous window's stale 1.0.
        assert hours[1].nwp_cloud_diagnostics.convective_precip_mm_h is None

    def test_no_anchors_is_noop(self):
        t0 = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        hours = [self._hour(t0), self._hour(t0 + timedelta(hours=1))]
        assert _fill_diag_hourly(hours) == 0


# ---------------------------------------------------------------------------
# Fix 4a — GFS cyclic longitude
# ---------------------------------------------------------------------------


class TestCyclicLongitude:
    def test_wrap_cyclic_lon_pads_global_grid(self):
        import xarray as xr

        da = xr.DataArray(
            np.arange(4.0),
            dims=("longitude",),
            coords={"longitude": [0.0, 90.0, 180.0, 270.0]},
        )
        padded = _wrap_cyclic_lon(da, "longitude")
        assert padded.longitude.values[-1] == 360.0
        assert padded.values[-1] == 0.0  # first column duplicated at the seam
        # Regional grids pass through untouched.
        regional = xr.DataArray(
            np.arange(3.0), dims=("longitude",),
            coords={"longitude": [8.0, 9.0, 10.0]},
        )
        assert _wrap_cyclic_lon(regional, "longitude").longitude.size == 3

    def test_bilinear_weights_wrap_last_half_cell(self):
        lat_arr = np.array([40.0, 41.0])
        lon_arr = np.array([0.0, 90.0, 180.0, 270.0])
        # 300°E: between the last column (270°) and the wrapped first (0°).
        bw = _bilinear_grid_weights(
            lat_arr, lon_arr, [40.5], [300.0], cyclic_lon=True,
        )
        assert bw is not None and bw.inb_idx.size == 1
        assert bw.j1[0] == 0  # wrapped column
        assert bw.j0[0] == 3
        # lon 300° is 1/3 of the 90° cell from the last column (270°) to the
        # wrapped first (0°): the two wrapped-corner weights sum to aj = 1/3.
        assert (bw.w01[0] + bw.w11[0]) == pytest.approx(1 / 3, rel=1e-6)

    def test_bilinear_weights_without_cyclic_drops_seam_target(self):
        lat_arr = np.array([40.0, 41.0])
        lon_arr = np.array([0.0, 90.0, 180.0, 270.0])
        bw = _bilinear_grid_weights(
            lat_arr, lon_arr, [40.5], [300.0], cyclic_lon=False,
        )
        assert bw is not None and bw.inb_idx.size == 0  # old behaviour: no data

    def test_interior_targets_unaffected_by_cyclic(self):
        lat_arr = np.array([40.0, 41.0])
        lon_arr = np.array([0.0, 90.0, 180.0, 270.0])
        bw = _bilinear_grid_weights(
            lat_arr, lon_arr, [40.5], [45.0], cyclic_lon=True,
        )
        assert bw.j0[0] == 0 and bw.j1[0] == 1


# ---------------------------------------------------------------------------
# Fix 4b — ICON CAPE_ML defensive sentinel guard
# ---------------------------------------------------------------------------


class TestIconCapeSentinel:
    def test_cape_sentinel_dropped(self):
        diag = build_icon_cloud_diagnostics(
            {"total_cover_pct": 40.0, "ml_cape_jkg": -999.9},
        )
        assert diag is not None
        assert diag.ml_cape_jkg is None

    def test_cape_real_value_kept(self):
        diag = build_icon_cloud_diagnostics(
            {"total_cover_pct": 40.0, "ml_cape_jkg": 1200.0},
        )
        assert diag.ml_cape_jkg == 1200.0
