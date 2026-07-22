"""Regression tests from the GRIB interpolation audit (#480, #482-#485).

Each class here pins one defect found by an external review of the spatial /
temporal / vertical interpolation paths. They are grouped in one file because
they share a provenance, not a module — the fixes span decode, fill, the ECMWF
enrichment loop and spatial interpolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import xarray as xr

from weatherbrief.analysis.spatial_interpolation import _lerp_diagnostics
from weatherbrief.fetch.grib import (
    _advance_ecmwf_accumulators,
    _select_ecmwf_window_steps,
)
from weatherbrief.fetch.grib.decode import (
    _bilinear_grid_weights,
    _decode_pressure_vars_from_datasets,
    _close_cyclic_longitude,
    _interpolate_per_point,
)
from weatherbrief.fetch.grib.fill import _interp_diag_at
from weatherbrief.models import NWPCloudDiagnostics
from weatherbrief.models.analysis import (
    NWP_CLOUD_DIAG_AVERAGED_SCALARS,
    NWP_CLOUD_DIAG_INSTANT_SCALARS,
    NWP_CLOUD_DIAG_LAYER_FIELDS,
    NWP_CLOUD_DIAG_SCALARS,
)


# ---------------------------------------------------------------------------
# #484 — Greenwich seam on the cyclic GFS longitude axis
# ---------------------------------------------------------------------------


def _global_grid() -> xr.DataArray:
    """Global 0.25° grid whose value at each cell IS its longitude in ±180.

    Encoding the answer into the data means a wrong seam interpolation shows
    up as a wrong value, not just a non-NaN.
    """
    lons = np.arange(0.0, 360.0, 0.25)
    lats = np.arange(60.0, 40.0, -0.25)
    signed = ((lons + 180.0) % 360.0) - 180.0
    return xr.DataArray(
        np.tile(signed, (len(lats), 1)),
        dims=("latitude", "longitude"),
        coords={"latitude": lats, "longitude": lons},
    )


def _regional_grid() -> xr.DataArray:
    """ICON-EU-like regional grid — not cyclic, must be left alone."""
    lons = np.arange(-10.0, 20.0, 0.0625)
    lats = np.arange(60.0, 40.0, -0.0625)
    return xr.DataArray(
        np.tile(lons, (len(lats), 1)),
        dims=("latitude", "longitude"),
        coords={"latitude": lats, "longitude": lons},
    )


class TestGreenwichSeam:
    """GFS targets are normalised with ``lon % 360`` but the grid stops at
    359.75, so longitudes in (-0.25, 0) landed past the end of the axis and
    returned NaN — a band every UK↔continent route crosses (#484)."""

    @pytest.mark.parametrize("lon", [-0.01, -0.05, -0.10, -0.20, -0.24])
    def test_just_west_of_greenwich_interpolates(self, lon):
        da = _global_grid()
        (value,) = _interpolate_per_point(da, [51.0], [lon % 360])
        assert value is not None, f"{lon} fell into the seam gap"
        assert value == pytest.approx(lon, abs=1e-6)

    @pytest.mark.parametrize("lon", [-0.25, -0.5, -1.0, 0.0, 0.1, 2.0])
    def test_longitudes_that_already_worked_are_unchanged(self, lon):
        da = _global_grid()
        (value,) = _interpolate_per_point(da, [51.0], [lon % 360])
        assert value == pytest.approx(lon, abs=1e-6)

    def test_seam_column_is_appended_once_for_a_cyclic_grid(self):
        da = _global_grid()
        closed = _close_cyclic_longitude(da, "longitude")
        assert closed.longitude.size == da.longitude.size + 1
        assert float(closed.longitude[-1]) == pytest.approx(360.0)
        # The wrapped column must equal the 0° column it duplicates.
        assert closed.isel(longitude=-1).values == pytest.approx(
            da.isel(longitude=0).values
        )

    def test_regional_grid_is_not_wrapped(self):
        da = _regional_grid()
        closed = _close_cyclic_longitude(da, "longitude")
        assert closed.longitude.size == da.longitude.size

    def test_regional_grid_still_interpolates_normally(self):
        da = _regional_grid()
        (value,) = _interpolate_per_point(da, [51.0], [5.0])
        assert value == pytest.approx(5.0, abs=1e-6)

    def test_regional_grid_still_returns_none_outside_its_domain(self):
        """Wrapping must not accidentally make out-of-domain points resolve."""
        da = _regional_grid()
        (value,) = _interpolate_per_point(da, [51.0], [40.0])
        assert value is None


def _gather(lat_arr, lon_arr, values, lat, lon):
    """Run the vectorised numpy path end to end for one target point.

    Mirrors the gather in ``_decode_pressure_vars_from_datasets``. Returns None
    when the point is reported out of bounds.
    """
    gw = _bilinear_grid_weights(
        lat_arr, lon_arr, np.array([lat]), np.array([lon]),
    )
    if gw is None or gw.inb_idx.size == 0:
        return None
    return float(
        (
            gw.w00 * values[gw.i0, gw.j0]
            + gw.w01 * values[gw.i0, gw.j1]
            + gw.w10 * values[gw.i1, gw.j0]
            + gw.w11 * values[gw.i1, gw.j1]
        )[0]
    )


class TestGreenwichSeamVectorisedPath:
    """`decode_grib_per_point` (the live GFS CLWMR/ICMR decode, dispatched as
    the ``decode_gfs_pressure`` job) does NOT go through
    ``_interpolate_per_point`` — it uses the vectorised numpy gather, which had
    the identical seam bug via ``np.interp(..., left=nan, right=nan)``. Closing
    the seam only in the xarray path left this one broken (#484)."""

    def _grid(self):
        lons = np.arange(0.0, 360.0, 0.25)
        lats = np.arange(60.0, 40.0, -0.25)
        signed = ((lons + 180.0) % 360.0) - 180.0
        return lats, lons, np.tile(signed, (len(lats), 1))

    @pytest.mark.parametrize("lon", [-0.01, -0.05, -0.10, -0.20, -0.24])
    def test_just_west_of_greenwich_interpolates(self, lon):
        lats, lons, values = self._grid()
        got = _gather(lats, lons, values, 51.0, lon % 360)
        assert got is not None, f"{lon} dropped as out-of-bounds at the seam"
        assert got == pytest.approx(lon, abs=1e-6)

    @pytest.mark.parametrize("lon", [-0.25, -0.5, -2.0, 0.0, 0.1, 3.0])
    def test_longitudes_that_already_worked_are_unchanged(self, lon):
        lats, lons, values = self._grid()
        assert _gather(lats, lons, values, 51.0, lon % 360) == pytest.approx(
            lon, abs=1e-6
        )

    def test_wrapped_column_index_folds_back_into_the_array(self):
        """The gather indexes the real data array, so the extended axis's
        column W must wrap to 0 rather than run off the end."""
        lats, lons, _ = self._grid()
        gw = _bilinear_grid_weights(
            lats, lons, np.array([51.0]), np.array([359.9]),
        )
        assert gw is not None and gw.inb_idx.size == 1
        assert int(gw.j0[0]) == lons.size - 1  # last real column
        assert int(gw.j1[0]) == 0              # wrapped to the first

    def test_regional_grid_is_untouched(self):
        lons = np.arange(-10.0, 20.0, 0.0625)
        lats = np.arange(60.0, 40.0, -0.0625)
        values = np.tile(lons, (len(lats), 1))
        assert _gather(lats, lons, values, 51.0, 5.0) == pytest.approx(5.0)

    def test_regional_grid_still_reports_out_of_bounds(self):
        lons = np.arange(-10.0, 20.0, 0.0625)
        lats = np.arange(60.0, 40.0, -0.0625)
        values = np.tile(lons, (len(lats), 1))
        assert _gather(lats, lons, values, 51.0, 40.0) is None

    def test_both_paths_agree_at_the_seam(self):
        """The xarray and numpy paths must not disagree about the same point."""
        lats, lons, values = self._grid()
        da = xr.DataArray(
            values,
            dims=("latitude", "longitude"),
            coords={"latitude": lats, "longitude": lons},
        )
        for lon in (-0.10, -0.05, -0.20):
            (via_xarray,) = _interpolate_per_point(da, [51.0], [lon % 360])
            via_numpy = _gather(lats, lons, values, 51.0, lon % 360)
            assert via_xarray == pytest.approx(via_numpy, abs=1e-9)


class TestSeamThroughPressureLevelDecode:
    """End-to-end through `_decode_pressure_vars_from_datasets` — the function
    `decode_grib_per_point` actually calls for GFS CLWMR/ICMR. Before the fix
    a target at -0.10 came back as an empty dict with covered=False."""

    def _dataset(self):
        lons = np.arange(0.0, 360.0, 0.25)
        lats = np.arange(60.0, 40.0, -0.25)
        levels = np.array([850, 700])
        # clwmr value encodes the signed longitude so a wrong wrap is visible.
        signed = ((lons + 180.0) % 360.0) - 180.0
        block = np.tile(signed, (len(levels), len(lats), 1))
        return xr.Dataset(
            {"clwmr": (("isobaricInhPa", "latitude", "longitude"), block)},
            coords={
                "isobaricInhPa": levels,
                "latitude": lats,
                "longitude": lons,
            },
        )

    @pytest.mark.parametrize("lon", [-0.10, -0.05, -0.20])
    def test_pressure_levels_resolve_at_the_seam(self, lon):
        ds = self._dataset()
        results, covered = _decode_pressure_vars_from_datasets(
            [ds], [51.0], [lon % 360],
        )
        assert covered == [True], f"{lon} reported as uncovered"
        assert results[0], f"{lon} returned an empty pressure-level dict"
        assert results[0][850]["cloud_liquid_water_kg_kg"] == pytest.approx(lon, abs=1e-6)

    def test_longitude_well_inside_the_grid_still_works(self):
        ds = self._dataset()
        results, covered = _decode_pressure_vars_from_datasets(
            [ds], [51.0], [5.0 % 360],
        )
        assert covered == [True]
        assert results[0][700]["cloud_liquid_water_kg_kg"] == pytest.approx(5.0, abs=1e-6)


# ---------------------------------------------------------------------------
# #483 — ECMWF step selection must bracket the window at any cadence
# ---------------------------------------------------------------------------


_INIT = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)

# The real 00z manifest shape: hourly to 90, 3-hourly to 144, 6-hourly to 168.
_ECMWF_STEPS = (
    list(range(0, 91))
    + list(range(93, 145, 3))
    + [150, 156, 162, 168]
)


def _steps():
    return [(h, {}, _INIT + timedelta(hours=h)) for h in _ECMWF_STEPS]


class TestEcmwfWindowStepSelection:
    """A fixed ±3 h margin cannot bracket 6-hourly output past +144h (#483)."""

    def _select(self, dep_hour, duration_h=1.0):
        start = _INIT + timedelta(hours=dep_hour)
        end = start + timedelta(hours=duration_h)
        chosen = _select_ecmwf_window_steps(
            _steps(), start, end, timedelta(hours=3),
        )
        return [s[0] for s in chosen]

    def test_six_hourly_range_keeps_both_brackets(self):
        """The reported case: +154h kept only step 156 before the fix."""
        selected = self._select(154)
        assert 150 in selected, "lower bracket missing — nothing to interpolate from"
        assert 156 in selected

    @pytest.mark.parametrize("dep_hour", [146, 148, 151, 154, 158, 160, 165])
    def test_every_departure_in_the_six_hourly_range_is_bracketed(self, dep_hour):
        selected = self._select(dep_hour)
        start_h = dep_hour
        end_h = dep_hour + 1
        assert any(s <= start_h for s in selected), f"no lower anchor at +{dep_hour}h"
        assert any(s >= end_h for s in selected), f"no upper anchor at +{dep_hour}h"

    @pytest.mark.parametrize("dep_hour", [10, 45, 89, 95, 110, 140])
    def test_hourly_and_three_hourly_ranges_are_bracketed_too(self, dep_hour):
        selected = self._select(dep_hour)
        assert any(s <= dep_hour for s in selected)
        assert any(s >= dep_hour + 1 for s in selected)

    @pytest.mark.parametrize("dep_hour", [150, 156, 162])
    def test_departure_exactly_on_a_six_hourly_step_keeps_a_predecessor(
        self, dep_hour,
    ):
        """A departure landing exactly on a step must not select that step as
        its own predecessor — otherwise accumulated tp/sf/cp have nothing to
        difference against for the departure hour."""
        selected = self._select(dep_hour)
        assert any(s < dep_hour for s in selected), (
            f"+{dep_hour}h selected itself as the lower bracket: {selected}"
        )
        assert dep_hour - 6 in selected

    @pytest.mark.parametrize("dep_hour", [24, 60, 96, 120, 144])
    def test_departure_exactly_on_a_step_elsewhere_also_keeps_a_predecessor(
        self, dep_hour,
    ):
        selected = self._select(dep_hour)
        assert any(s < dep_hour for s in selected)

    def test_exact_step_itself_is_still_retained(self):
        """Making the lower bracket strict must not drop the departure step —
        the margin window still covers it."""
        assert 156 in self._select(156)

    def test_hourly_range_selection_is_unchanged_by_the_bracket_union(self):
        """Inside hourly cadence the brackets already sit within the margin,
        so the fix must be a no-op there."""
        start = _INIT + timedelta(hours=40)
        end = start + timedelta(hours=2)
        margin = timedelta(hours=3)
        margin_only = sorted(
            s[0] for s in _steps() if start - margin <= s[2] <= end + margin
        )
        assert self._select(40, duration_h=2.0) == margin_only

    def test_selection_is_sorted_and_deduplicated(self):
        selected = self._select(154)
        assert selected == sorted(set(selected))

    def test_departure_before_the_first_step_has_no_lower_bracket(self):
        """Nothing to invent — must not crash, must still take the upper side."""
        start = _INIT - timedelta(hours=5)
        selected = [
            s[0] for s in _select_ecmwf_window_steps(
                _steps(), start, start + timedelta(hours=1), timedelta(hours=3),
            )
        ]
        assert selected, "should still pick the earliest available step"
        assert min(selected) == 0

    def test_departure_past_the_horizon_takes_the_last_step(self):
        start = _INIT + timedelta(hours=200)
        selected = [
            s[0] for s in _select_ecmwf_window_steps(
                _steps(), start, start + timedelta(hours=1), timedelta(hours=3),
            )
        ]
        assert selected == [168]

    def test_no_steps_available(self):
        start = _INIT + timedelta(hours=10)
        assert _select_ecmwf_window_steps(
            [], start, start + timedelta(hours=1), timedelta(hours=3),
        ) == []


# ---------------------------------------------------------------------------
# #485 — the two interpolation axes must cover the same fields
# ---------------------------------------------------------------------------


def _filled_diag(value: float) -> NWPCloudDiagnostics:
    """A diagnostics object with every scalar set, so a dropped field shows up
    as a None rather than being masked by a default."""
    return NWPCloudDiagnostics(**{name: value for name in NWP_CLOUD_DIAG_SCALARS})


class TestDiagnosticsFieldInventory:
    """`_lerp_diagnostics` (spatial) and `_interp_diag_at` (time) had drifted
    apart, each silently dropping fields the other carried (#485)."""

    def test_inventory_partitions_every_model_field(self):
        covered = set(NWP_CLOUD_DIAG_LAYER_FIELDS) | set(NWP_CLOUD_DIAG_SCALARS)
        assert covered == set(NWPCloudDiagnostics.model_fields), (
            "a field was added to NWPCloudDiagnostics without classifying it "
            "for interpolation"
        )

    def test_averaged_and_instant_scalars_do_not_overlap(self):
        assert not (
            set(NWP_CLOUD_DIAG_AVERAGED_SCALARS) & set(NWP_CLOUD_DIAG_INSTANT_SCALARS)
        )

    def test_spatial_interpolation_carries_every_scalar(self):
        a = _filled_diag(10.0)
        b = _filled_diag(20.0)
        out = _lerp_diagnostics(a, b, 0.5)
        for name in NWP_CLOUD_DIAG_SCALARS:
            assert getattr(out, name) == pytest.approx(15.0), f"{name} dropped"

    def test_time_interpolation_carries_every_scalar(self):
        a = _filled_diag(10.0)
        b = _filled_diag(20.0)
        out = _interp_diag_at(a, b, mid_frac=0.5, step_frac=0.5)
        for name in NWP_CLOUD_DIAG_SCALARS:
            assert getattr(out, name) == pytest.approx(15.0), f"{name} dropped"

    def test_freezing_level_survives_spatial_fill(self):
        """The concrete regression: ECMWF `deg0l` was lost on spatially filled
        interior route points."""
        a = NWPCloudDiagnostics(freezing_level_ft=8000.0)
        b = NWPCloudDiagnostics(freezing_level_ft=10000.0)
        assert _lerp_diagnostics(a, b, 0.5).freezing_level_ft == pytest.approx(9000.0)


class TestFreezingLevelMirror:
    """Interpolating `freezing_level_ft` into the diagnostics is only half the
    job — sounding analysis reads `HourlyForecast.freezing_level_m` to populate
    `indices.nwp_freezing_level_ft`, so the mirror has to move with it."""

    def _hour(self):
        from weatherbrief.models import HourlyForecast
        return HourlyForecast(time=_INIT)

    def test_attach_mirrors_the_freezing_level(self):
        h = self._hour()
        h.attach_nwp_diagnostics(NWPCloudDiagnostics(freezing_level_ft=9000.0))
        assert h.nwp_cloud_diagnostics.freezing_level_ft == pytest.approx(9000.0)
        assert h.freezing_level_m == pytest.approx(9000.0 / 3.28084)

    def test_attach_leaves_the_mirror_alone_when_there_is_no_value(self):
        """No freezing level in the diagnostics must not blank an existing
        Open-Meteo value."""
        h = self._hour()
        h.freezing_level_m = 2500.0
        h.attach_nwp_diagnostics(NWPCloudDiagnostics(total_cover_pct=50.0))
        assert h.freezing_level_m == pytest.approx(2500.0)

    def test_model_native_value_overrides_open_meteo(self):
        h = self._hour()
        h.freezing_level_m = 2500.0
        h.attach_nwp_diagnostics(NWPCloudDiagnostics(freezing_level_ft=9000.0))
        assert h.freezing_level_m == pytest.approx(9000.0 / 3.28084)

    def test_no_direct_diagnostics_assignment(self):
        """Structural guard: nothing outside the model may assign
        ``nwp_cloud_diagnostics`` directly, because that bypasses the
        freezing-level mirror.

        This exact omission shipped three times across this audit — the
        spatial fill, then all three time-axis fill sites, then the RH gate.
        A source-level rule is the only thing that makes it stop recurring;
        reviewing for it by eye demonstrably does not.
        """
        import re
        from pathlib import Path

        import weatherbrief

        src_root = Path(weatherbrief.__file__).parent
        pattern = re.compile(r"\.nwp_cloud_diagnostics\s*=(?!=)")
        offenders = []
        for path in src_root.rglob("*.py"):
            if path.name == "analysis.py" and path.parent.name == "models":
                continue  # the model itself legitimately assigns the field
            for lineno, line in enumerate(
                path.read_text().splitlines(), start=1,
            ):
                if pattern.search(line):
                    rel = path.relative_to(src_root)
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")

        assert not offenders, (
            "assign via HourlyForecast.attach_nwp_diagnostics() instead — "
            "a direct assignment skips the freezing_level_m mirror:\n  "
            + "\n  ".join(offenders)
        )

    def test_gfs_rh_gate_preserves_non_layer_fields(self):
        """The gate replaces only the layer bands; every other diagnostic must
        ride through. It used to rebuild the object from a hand-written field
        list, silently dropping the #283 convective/stability fields."""
        from weatherbrief.fetch.grib.fill import _gate_gfs_hourly
        from weatherbrief.models import NWPCloudLayerDiag, PressureLevelData

        h = self._hour()
        # A low layer with no supporting RH/condensate → the gate drops it.
        h.pressure_levels = [
            PressureLevelData(
                pressure_hpa=850, relative_humidity_pct=5.0,
                cloud_liquid_water_kg_kg=0.0, ice_mixing_ratio_kg_kg=0.0,
                geopotential_height_m=1500.0,
            ),
        ]
        h.attach_nwp_diagnostics(
            NWPCloudDiagnostics(
                low=NWPCloudLayerDiag(cover_pct=90.0, base_ft=3000, top_ft=6000),
                ml_cape_jkg=1200.0,
                ml_cin_jkg=-50.0,
                k_index=28.0,
                total_totals=48.0,
                convective_precip_mm_h=2.5,
                freezing_level_ft=9000.0,
            )
        )

        dropped = _gate_gfs_hourly(h)
        assert dropped >= 1, "expected the unsupported low layer to be gated"

        diag = h.nwp_cloud_diagnostics
        assert diag.ml_cape_jkg == pytest.approx(1200.0)
        assert diag.ml_cin_jkg == pytest.approx(-50.0)
        assert diag.k_index == pytest.approx(28.0)
        assert diag.total_totals == pytest.approx(48.0)
        assert diag.convective_precip_mm_h == pytest.approx(2.5)
        # And the mirror still holds after the gate rewrites the object.
        assert h.freezing_level_m == pytest.approx(9000.0 / 3.28084)

    def test_time_axis_forward_fill_mirrors_the_freezing_level(self):
        """The bot's reported case: ECMWF gap hours between a1 anchors got the
        diagnostics forward-filled but kept Open-Meteo's freezing_level_m."""
        from weatherbrief.fetch.grib.fill import _fill_diag_hourly

        anchor = self._hour()
        anchor.attach_nwp_diagnostics(
            NWPCloudDiagnostics(freezing_level_ft=9000.0)
        )
        gap = self._hour()
        gap.time = _INIT + timedelta(hours=1)
        gap.freezing_level_m = 2500.0  # stale Open-Meteo value

        assert _fill_diag_hourly([anchor, gap]) == 1
        assert gap.nwp_cloud_diagnostics.freezing_level_ft == pytest.approx(9000.0)
        assert gap.freezing_level_m == pytest.approx(9000.0 / 3.28084), (
            "forward-fill set the diagnostics but left the stale mirror"
        )

    def test_spatial_fill_mirrors_the_interpolated_freezing_level(self):
        """End to end: an interior point filled from two neighbours must end up
        with the mirror set, not just the diagnostics."""
        from weatherbrief.analysis.spatial_interpolation import (
            interpolate_diagnostics_spatially,
        )
        from weatherbrief.models import (
            ModelSource,
            RouteCrossSection,
            RoutePoint,
            Waypoint,
            WaypointForecast,
        )

        def _wf(idx, diag):
            h = self._hour()
            if diag is not None:
                h.attach_nwp_diagnostics(diag)
            return WaypointForecast(
                waypoint=Waypoint(icao=f"PT{idx}", name=f"pt{idx}",
                                  lat=51.0, lon=float(idx)),
                model=ModelSource.ECMWF,
                fetched_at=_INIT,
                hourly=[h],
            )

        left = NWPCloudDiagnostics(freezing_level_ft=8000.0)
        right = NWPCloudDiagnostics(freezing_level_ft=10000.0)
        cs = RouteCrossSection(
            model=ModelSource.ECMWF,
            route_points=[],  # not used by interpolation
            fetched_at=_INIT,
            point_forecasts=[_wf(0, left), _wf(1, None), _wf(2, right)],
        )
        route_points = [
            RoutePoint(
                lat=51.0, lon=float(i), distance_from_origin_nm=float(i * 10),
            )
            for i in range(3)
        ]

        filled = interpolate_diagnostics_spatially([cs], route_points, 100.0)
        assert filled == 1

        mid = cs.point_forecasts[1].hourly[0]
        assert mid.nwp_cloud_diagnostics.freezing_level_ft == pytest.approx(9000.0)
        assert mid.freezing_level_m == pytest.approx(9000.0 / 3.28084), (
            "spatial fill set the diagnostics but not the field sounding reads"
        )

    def test_convective_fields_survive_time_fill(self):
        """The other direction of the same drift: the #283 convective and
        stability fields were absent from the GFS time axis."""
        a = NWPCloudDiagnostics(
            convective_precip_mm_h=1.0, k_index=20.0, total_totals=40.0,
            ml_cape_jkg=500.0, ml_cin_jkg=-100.0,
        )
        b = NWPCloudDiagnostics(
            convective_precip_mm_h=3.0, k_index=30.0, total_totals=50.0,
            ml_cape_jkg=1500.0, ml_cin_jkg=-200.0,
        )
        out = _interp_diag_at(a, b, mid_frac=0.5, step_frac=0.5)
        assert out.convective_precip_mm_h == pytest.approx(2.0)
        assert out.k_index == pytest.approx(25.0)
        assert out.total_totals == pytest.approx(45.0)
        assert out.ml_cape_jkg == pytest.approx(1000.0)
        assert out.ml_cin_jkg == pytest.approx(-150.0)

    def test_averaged_scalars_use_the_midpoint_fraction(self):
        """Boundary-layer cover is published averaged-only, so it must follow
        mid_frac while instantaneous scalars follow step_frac."""
        a = _filled_diag(0.0)
        b = _filled_diag(100.0)
        out = _interp_diag_at(a, b, mid_frac=0.25, step_frac=0.75)
        for name in NWP_CLOUD_DIAG_AVERAGED_SCALARS:
            assert getattr(out, name) == pytest.approx(25.0), f"{name} used step_frac"
        for name in NWP_CLOUD_DIAG_INSTANT_SCALARS:
            assert getattr(out, name) == pytest.approx(75.0), f"{name} used mid_frac"


# ---------------------------------------------------------------------------
# #482 — a missing accumulated step must invalidate its predecessor
# ---------------------------------------------------------------------------


class _AccumState:
    """Holds the three per-point predecessor lists for a fixed point count."""

    def __init__(self, n_points: int):
        self.n = n_points
        self.tp: list[float | None] = [None] * n_points
        self.sf: list[float | None] = [None] * n_points
        self.cp: list[float | None] = [None] * n_points

    def advance(self, sfc_data, sfc_covered):
        _advance_ecmwf_accumulators(
            sfc_data, sfc_covered, self.n,
            prev_tp_per_point=self.tp,
            prev_sf_per_point=self.sf,
            prev_cp_per_point=self.cp,
        )


def _raw(tp=None, sf=None, cp=None):
    out = {}
    if tp is not None:
        out["total_precip_m"] = tp
    if sf is not None:
        out["snowfall_m_we"] = sf
    if cp is not None:
        out["conv_precip_m"] = cp
    return out


class TestEcmwfAccumulatorPredecessor:
    """``prev_a1_valid_utc`` advances every processed step, so a predecessor
    retained across a missing step gets divided by the wrong window (#482)."""

    def test_present_value_is_carried_forward(self):
        st = _AccumState(1)
        st.advance([_raw(tp=0.005, sf=0.001, cp=0.002)], [True])
        assert st.tp == [0.005]
        assert st.sf == [0.001]
        assert st.cp == [0.002]

    def test_missing_field_resets_only_that_field(self):
        st = _AccumState(1)
        st.advance([_raw(tp=0.005, sf=0.001, cp=0.002)], [True])
        st.advance([_raw(tp=0.007)], [True])  # sf/cp absent this step
        assert st.tp == [0.007]
        assert st.sf == [None], "stale snowfall accumulator survived a gap"
        assert st.cp == [None], "stale convective accumulator survived a gap"

    def test_uncovered_point_resets_every_field(self):
        st = _AccumState(1)
        st.advance([_raw(tp=0.005, sf=0.001, cp=0.002)], [True])
        st.advance([_raw(tp=0.009, sf=0.003, cp=0.004)], [False])
        assert st.tp == [None]
        assert st.sf == [None]
        assert st.cp == [None]

    def test_empty_raw_dict_resets(self):
        st = _AccumState(1)
        st.advance([_raw(tp=0.005)], [True])
        st.advance([{}], [True])
        assert st.tp == [None]

    def test_one_point_gap_does_not_disturb_its_neighbours(self):
        st = _AccumState(3)
        st.advance([_raw(tp=0.001), _raw(tp=0.002), _raw(tp=0.003)], [True] * 3)
        st.advance([_raw(tp=0.004), _raw(), _raw(tp=0.006)], [True] * 3)
        assert st.tp == [0.004, None, 0.006]

    def test_short_sfc_data_resets_the_tail(self):
        """A truncated decode must not leave trailing points holding stale
        accumulators from an older step."""
        st = _AccumState(3)
        st.advance([_raw(tp=0.001), _raw(tp=0.002), _raw(tp=0.003)], [True] * 3)
        st.advance([_raw(tp=0.004)], [True])
        assert st.tp == [0.004, None, None]

    def test_recovery_after_a_gap_needs_two_good_steps(self):
        """After a gap the next step re-seeds the predecessor; the step after
        that can difference again."""
        st = _AccumState(1)
        st.advance([_raw(tp=0.001)], [True])
        st.advance([{}], [True])           # gap → reset
        assert st.tp == [None]             # next step can compute no rate
        st.advance([_raw(tp=0.005)], [True])
        assert st.tp == [0.005]            # re-seeded, differencing resumes

    def test_zero_is_preserved_not_treated_as_missing(self):
        """A genuine 0.0 accumulation must be carried, not reset — otherwise
        dry steps would keep resetting the chain."""
        st = _AccumState(1)
        st.advance([_raw(tp=0.0, sf=0.0, cp=0.0)], [True])
        assert st.tp == [0.0]
        assert st.sf == [0.0]
        assert st.cp == [0.0]
