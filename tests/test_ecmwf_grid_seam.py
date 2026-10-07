"""ECMWF Europe/Nordic grid seam (#672).

ECMWF delivers Europe (35.0–59.5°N, 17.5°W–39.5°E) and Nordic
(60.0–71.5°N, 2.5–40.0°E) as two 0.25° sub-grids with no shared row, so no
single grid brackets 59.5–60.0°N. These tests build the same geometry
synthetically (shrunk in extent, same edges at the seam) and check both ECMWF
decode paths fill the strip from the facing edge rows.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest
import xarray as xr

from weatherbrief.fetch.grib import decode as dec
from weatherbrief.fetch.grib.decode import (
    _MAX_SEAM_GAP_DEG,
    _decode_pressure_vars_from_datasets,
    _ECMWF_FRAC_TO_PCT,
    _ECMWF_FULL_VAR_MAP,
    _plan_grid_seams,
)

from grib_synth import (
    SEAM_EUROPE,
    SEAM_NORDIC,
    linear_field,
    write as write_message,
)

# Shrunk extents with the real seam edges: Europe tops out at 59.5, Nordic
# starts at 60.0 and only from 2.5°E (so 1.3°W is Europe-only, like EGPB).
# Same geometry as grib_synth.SEAM_EUROPE / SEAM_NORDIC, which the surface
# tests below write as real GRIB — keep the two in step.
_EU_LATS = np.arange(59.5, 57.75, -0.25)        # descending, as ECMWF delivers
_EU_LONS = np.arange(-5.0, 20.0 + 0.125, 0.25)
_NO_LATS = np.arange(62.0, 59.75, -0.25)
_NO_LONS = np.arange(2.5, 20.0 + 0.125, 0.25)
_LEVELS = np.array([850, 700])


def _field(lats, lons, *, offset=0.0):
    """Value = lat + lon/100 + offset: linear, so bilinear is exact."""
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    return la + lo / 100.0 + offset


def _pressure_ds(lats, lons, *, with_cc=True):
    t = np.stack([_field(lats, lons, offset=200.0 + i) for i in range(len(_LEVELS))])
    data = {"t": (("isobaricInhPa", "latitude", "longitude"), t)}
    if with_cc:
        cc = np.full_like(t, 0.5)
        data["cc"] = (("isobaricInhPa", "latitude", "longitude"), cc)
    return xr.Dataset(
        data,
        coords={"isobaricInhPa": _LEVELS, "latitude": lats, "longitude": lons},
    )


def _decode_pressure(lats, lons, *, bridge=True, nordic_cc=True):
    datasets = [
        _pressure_ds(_EU_LATS, _EU_LONS),
        _pressure_ds(_NO_LATS, _NO_LONS, with_cc=nordic_cc),
    ]
    return _decode_pressure_vars_from_datasets(
        datasets, lats, lons,
        var_map=_ECMWF_FULL_VAR_MAP,
        frac_vars=_ECMWF_FRAC_TO_PCT,
        first_wins=True,
        bridge_seams=bridge,
    )


class TestPlan:
    def _grids(self):
        return [(_EU_LATS, _EU_LONS), (_NO_LATS, _NO_LONS)]

    def test_point_inside_a_grid_is_not_planned(self):
        assert _plan_grid_seams(self._grids(), [59.0, 61.0], [10.0, 10.0]) == []

    def test_both_grids_cover_longitude_is_two_sided(self):
        (sp,) = _plan_grid_seams(self._grids(), [59.65], [17.92])
        assert not sp.held
        assert len(sp.terms) == 2
        assert sum(t.w_lat for t in sp.terms) == pytest.approx(1.0)

    def test_only_lower_grid_covers_longitude_is_held(self):
        (sp,) = _plan_grid_seams(self._grids(), [59.88], [-1.3])
        assert sp.held
        (term,) = sp.terms
        assert term.grid_idx == 0
        assert term.w_lat == 1.0

    def test_outside_every_longitude_is_not_planned(self):
        assert _plan_grid_seams(self._grids(), [59.7], [30.0]) == []

    def test_outer_domain_edge_is_never_extended(self):
        # North of Nordic, south of Europe, west of Europe: no seam there.
        assert _plan_grid_seams(self._grids(), [62.2, 57.5, 59.0], [10.0, 10.0, -6.0]) == []

    def test_gap_wider_than_limit_is_not_a_seam(self):
        far = (_NO_LATS + _MAX_SEAM_GAP_DEG, _NO_LONS)
        assert _plan_grid_seams([(_EU_LATS, _EU_LONS), far], [59.9], [10.0]) == []

    def test_grids_without_shared_longitudes_are_not_a_seam(self):
        east = (_NO_LATS, _NO_LONS + 30.0)
        assert _plan_grid_seams([(_EU_LATS, _EU_LONS), east], [59.7], [10.0]) == []


class TestPressureDecode:
    def test_seam_point_interpolates_across_the_gap(self):
        # ESSA-like point: both grids cover 17.92°E; field is linear so the
        # 0.5° cell across the gap reproduces it exactly.
        results, covered = _decode_pressure([59.65], [17.92])
        assert covered == [True]
        assert results[0][850]["raw_temperature_k"] == pytest.approx(200.0 + 59.65 + 0.1792)
        assert results[0][700]["raw_temperature_k"] == pytest.approx(201.0 + 59.65 + 0.1792)
        assert results[0][850]["cloud_area_fraction_pct"] == pytest.approx(50.0)

    def test_europe_only_point_holds_the_top_row(self):
        # EGPB-like point: Nordic does not reach 1.3°W, so Europe's 59.5 row
        # is held — latitude reads as 59.5, not 59.88.
        results, covered = _decode_pressure([59.88], [-1.3])
        assert covered == [True]
        assert results[0][850]["raw_temperature_k"] == pytest.approx(200.0 + 59.5 - 0.013)

    def test_field_missing_on_one_grid_is_dropped_not_one_sided(self):
        results, covered = _decode_pressure([59.65], [17.92], nordic_cc=False)
        assert covered == [True]
        assert "raw_temperature_k" in results[0][850]
        assert "cloud_area_fraction_pct" not in results[0][850]

    def test_without_bridge_the_seam_stays_uncovered(self):
        results, covered = _decode_pressure([59.65], [17.92], bridge=False)
        assert covered == [False]
        assert results[0] == {}

    def test_points_inside_grids_are_unchanged(self):
        lats, lons = [59.0, 61.0, 59.65], [10.0, 10.0, 17.92]
        bridged, _ = _decode_pressure(lats, lons, bridge=True)
        plain, plain_cov = _decode_pressure(lats, lons, bridge=False)
        assert plain_cov[:2] == [True, True]
        assert bridged[0] == plain[0]
        assert bridged[1] == plain[1]

    def test_outside_both_grids_stays_uncovered(self):
        _, covered = _decode_pressure([59.7, 40.0], [30.0, -30.0])
        assert covered == [False, False]

    def test_held_points_are_logged(self, caplog):
        with caplog.at_level(logging.INFO, logger="weatherbrief.fetch.grib.decode"):
            _decode_pressure([59.65, 59.88], [17.92, -1.3])
        msg = " ".join(r.getMessage() for r in caplog.records)
        assert "2 point(s) filled" in msg
        assert "1 held at the nearest edge row" in msg


# ---------------------------------------------------------------------------
# Surface (a1): driven through the real decoder on synthetic GRIB.
#
# The pressure tests above still reach the xarray helper that HRRR and the
# chunked ICON decoder share, but the ECMWF surface path has no such entry
# point since #678 deleted the cfgrib rollback — so these go through
# ``decode_ecmwf_surface_per_point``, which is what runs in production.
# Fields are linear in lat/lon, so an interpolated value is exact in closed
# form and a held edge row is visible as a latitude that reads 59.5.
# ---------------------------------------------------------------------------

_SFC_FIELDS = (("2t", "temperature_2m_k", 270.0), ("blh", "boundary_layer_height_m", 1000.0))


def _write_surface(path, *, areas=(SEAM_EUROPE, SEAM_NORDIC)):
    with open(path, "wb") as f:
        for area in areas:
            for short, _, offset in _SFC_FIELDS:
                write_message(
                    f, sample="regular_ll_sfc_grib1", short=short,
                    type_of_level="surface", level=0, area=area,
                    values=lambda la, lo, o=offset: linear_field(la, lo, offset=o),
                )
            write_message(
                f, sample="regular_ll_sfc_grib1", short="tcc",
                type_of_level="surface", level=0, area=area,
                values=lambda la, lo: np.full((len(la), len(lo)), 0.4),
            )


def _write_ceil(path, eu_value, nordic_value):
    """Constant ``ceil`` on each side, GRIB2 as ECMWF delivers it."""
    with open(path, "wb") as f:
        for area, value in ((SEAM_EUROPE, eu_value), (SEAM_NORDIC, nordic_value)):
            write_message(
                f, sample="regular_ll_sfc_grib2", short="ceil",
                type_of_level="surface", level=0, area=area,
                values=lambda la, lo, v=value: np.full((len(la), len(lo)), v),
            )


class TestSurfaceDecode:
    def test_seam_point_gets_every_field(self, tmp_path):
        path = tmp_path / "sfc.grib"
        _write_surface(path)
        results, covered = dec.decode_ecmwf_surface_per_point(path, [59.65], [17.92])
        assert covered == [True]
        raw = results[0]
        assert raw["temperature_2m_k"] == pytest.approx(270.0 + 59.65 + 0.1792)
        assert raw["boundary_layer_height_m"] == pytest.approx(1000.0 + 59.65 + 0.1792)
        assert raw["total_cover_frac"] == pytest.approx(0.4)

    def test_europe_only_point_holds_the_top_row(self, tmp_path):
        # EGPB-like point: Nordic does not reach 1.3°W, so Europe's 59.5 row
        # is held — latitude reads as 59.5, not 59.88.
        path = tmp_path / "sfc.grib"
        _write_surface(path)
        results, covered = dec.decode_ecmwf_surface_per_point(path, [59.88], [-1.3])
        assert covered == [True]
        assert results[0]["temperature_2m_k"] == pytest.approx(270.0 + 59.5 - 0.013)

    def test_inside_point_unchanged_and_outside_uncovered(self, tmp_path):
        path = tmp_path / "sfc.grib"
        _write_surface(path)
        results, covered = dec.decode_ecmwf_surface_per_point(
            path, [59.0, 40.0], [10.0, -30.0],
        )
        assert covered == [True, False]
        assert results[0]["temperature_2m_k"] == pytest.approx(270.0 + 59.0 + 0.10)


class TestCloudSentinelAtSeam:
    """A 9999 m "no cloud" corner must never blend into a fake ceiling."""

    def test_mixed_takes_the_nearest_row_not_a_blend(self, tmp_path):
        path = tmp_path / "ceil.grib"
        # 59.6 is nearer Europe's 59.5 row (cloud at 500 m) …
        _write_ceil(path, 500.0, 9999.0)
        results, _ = dec.decode_ecmwf_surface_per_point(path, [59.6], [17.92])
        assert results[0]["ceiling_m"] == pytest.approx(500.0)
        # … 59.9 nearer Nordic's 60.0 row (no cloud): the sentinel survives
        # and downstream reads it as "no ceiling", not ~5000 m.
        results, _ = dec.decode_ecmwf_surface_per_point(path, [59.9], [17.92])
        assert results[0]["ceiling_m"] == pytest.approx(9999.0)

    def test_tie_takes_the_lower_cloudier_value(self, tmp_path):
        path = tmp_path / "ceil.grib"
        _write_ceil(path, 9999.0, 500.0)
        results, _ = dec.decode_ecmwf_surface_per_point(path, [59.75], [17.92])
        assert results[0]["ceiling_m"] == pytest.approx(500.0)

    def test_real_heights_on_both_sides_still_interpolate(self, tmp_path):
        path = tmp_path / "ceil.grib"
        _write_ceil(path, 500.0, 1500.0)
        results, _ = dec.decode_ecmwf_surface_per_point(path, [59.75], [17.92])
        assert results[0]["ceiling_m"] == pytest.approx(1000.0)

    def test_sentinel_on_both_sides_stays_sentinel(self, tmp_path):
        path = tmp_path / "ceil.grib"
        _write_ceil(path, 9999.0, 9999.0)
        results, _ = dec.decode_ecmwf_surface_per_point(path, [59.75], [17.92])
        assert results[0]["ceiling_m"] == pytest.approx(9999.0)
