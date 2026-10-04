"""Per-dataset longitude convention in the shared interpolation path (#673).

An ECMWF ``a1`` file for the US area mixes two longitude conventions: its GRIB1
messages sit on −128 … −71 and its GRIB2 messages (ceil, CAPE/CIN, KX, TOTALX,
ptype) on 232 … 289. Route targets are −180/+180, so before the fix every GRIB2
field silently came back None at US points. The grids here encode the signed
longitude as the field value, so a wrong mapping shows up as a wrong number,
not just a non-None.
"""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from weatherbrief.fetch.grib.decode import (
    _align_lons_to_axis,
    _bilinear_grid_weights,
    _decode_pressure_vars_from_datasets,
    _interpolate_per_point,
)

KJFK = (40.64, -73.78)
KLAX = (33.94, -118.41)


def _signed(lons: np.ndarray) -> np.ndarray:
    return ((lons + 180.0) % 360.0) - 180.0


def _surface(lons: np.ndarray, lats: np.ndarray) -> xr.DataArray:
    return xr.DataArray(
        np.tile(_signed(lons), (len(lats), 1)),
        dims=("latitude", "longitude"),
        coords={"latitude": lats, "longitude": lons},
    )


def _us_grid_0_360() -> xr.DataArray:
    """The ECMWF a1 US GRIB2 grid as cfgrib presents it: 232 … 289."""
    return _surface(np.arange(232.0, 289.25, 0.25), np.arange(55.0, 24.75, -0.25))


def _us_grid_180() -> xr.DataArray:
    """The ECMWF a1 US GRIB1 grid: −128 … −71."""
    return _surface(np.arange(-128.0, -70.75, 0.25), np.arange(55.0, 24.75, -0.25))


class TestAlignLonsToAxis:
    def test_signed_target_maps_onto_a_0_360_axis(self):
        got = _align_lons_to_axis(np.array([232.0, 289.0]), [-73.78, -118.41])
        assert got == pytest.approx([286.22, 241.59])

    def test_0_360_target_maps_onto_a_signed_axis(self):
        got = _align_lons_to_axis(np.array([-17.5, 39.5]), [359.0, 2.0])
        assert got == pytest.approx([-1.0, 2.0])

    def test_targets_already_in_the_axis_convention_are_unchanged(self):
        assert _align_lons_to_axis(np.array([-128.0, -71.0]), [-73.78]) == (
            pytest.approx([-73.78])
        )
        assert _align_lons_to_axis(np.array([0.0, 359.75]), [359.9, 5.0]) == (
            pytest.approx([359.9, 5.0])
        )

    def test_descending_axis_uses_its_minimum(self):
        got = _align_lons_to_axis(np.array([289.0, 232.0]), [-73.78])
        assert got == pytest.approx([286.22])


class TestXarrayPath:
    """``_interpolate_per_point`` — what ``decode_ecmwf_surface_per_point``
    calls for every a1 variable."""

    @pytest.mark.parametrize("lat,lon", [KJFK, KLAX])
    def test_us_point_decodes_on_the_0_360_grid(self, lat, lon):
        (value,) = _interpolate_per_point(_us_grid_0_360(), [lat], [lon])
        assert value == pytest.approx(lon, abs=1e-6)

    @pytest.mark.parametrize("lat,lon", [KJFK, KLAX])
    def test_both_a1_conventions_agree(self, lat, lon):
        (grib2,) = _interpolate_per_point(_us_grid_0_360(), [lat], [lon])
        (grib1,) = _interpolate_per_point(_us_grid_180(), [lat], [lon])
        assert grib2 == pytest.approx(grib1, abs=1e-9)

    def test_point_outside_the_regional_grid_stays_none(self):
        """A European point must not wrap onto the US grid."""
        values = _interpolate_per_point(
            _us_grid_0_360(), [51.0, 40.0], [2.0, -70.0],
        )
        assert values == [None, None]


class TestVectorisedPath:
    """``_bilinear_grid_weights`` / ``_decode_pressure_vars_from_datasets`` —
    the numpy gather used by the pressure-level decoders."""

    def test_weights_land_on_the_0_360_grid(self):
        da = _us_grid_0_360()
        lats = da.latitude.values
        lons = da.longitude.values
        gw = _bilinear_grid_weights(
            lats, lons, np.array([KJFK[0]]), np.array([KJFK[1]]),
        )
        assert gw is not None and gw.inb_idx.size == 1
        values = da.values
        got = (
            gw.w00 * values[gw.i0, gw.j0] + gw.w01 * values[gw.i0, gw.j1]
            + gw.w10 * values[gw.i1, gw.j0] + gw.w11 * values[gw.i1, gw.j1]
        )
        assert float(got[0]) == pytest.approx(KJFK[1], abs=1e-6)

    def test_pressure_decode_covers_a_us_point_on_a_0_360_grid(self):
        lons = np.arange(232.0, 289.25, 0.25)
        lats = np.arange(55.0, 24.75, -0.25)
        levels = np.array([850, 700])
        block = np.tile(_signed(lons), (len(levels), len(lats), 1))
        ds = xr.Dataset(
            {"clwmr": (("isobaricInhPa", "latitude", "longitude"), block)},
            coords={"isobaricInhPa": levels, "latitude": lats, "longitude": lons},
        )
        results, covered = _decode_pressure_vars_from_datasets(
            [ds], [KJFK[0], 51.0], [KJFK[1], 2.0],
        )
        assert covered == [True, False]
        assert results[0][850]["cloud_liquid_water_kg_kg"] == pytest.approx(
            KJFK[1], abs=1e-6,
        )
        assert results[1] == {}
