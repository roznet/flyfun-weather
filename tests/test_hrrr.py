"""Tests for the HRRR in-place upgrade of the gfs slot (issue #457).

HRRR (3 km, convection-allowing) serves the ``gfs`` model slot in place of
GFS when the whole route fits the HRRR CONUS domain AND the flight window is
within the selected run's horizon (48h on 00/06/12/18z cycles, 18h on the
other hourly cycles) — as a FULL sounding replacement, not the GFS patch.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from weatherbrief.fetch.grib.hrrr_fetch import (
    HRRR_EXTENDED_CYCLES,
    HRRR_SOUNDING_MIN_LEVEL_HPA,
    compute_hrrr_flight_window_hours,
    find_latest_hrrr_run,
    hrrr_grib2_url,
    hrrr_idx_url,
    hrrr_run_horizon_hours,
    hrrr_window_out_of_range,
    plan_hrrr_cloud_diag_byte_ranges,
    plan_hrrr_sounding_byte_ranges,
    route_in_hrrr_domain,
)
from weatherbrief.models import RoutePoint


def _rp(lat: float, lon: float, nm: float = 0.0) -> RoutePoint:
    return RoutePoint(lat=lat, lon=lon, distance_from_origin_nm=nm)


def _utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# URL builders — 2-digit forecast hour, conus path
# ---------------------------------------------------------------------------


class TestHrrrUrls:
    def test_grib2_url_two_digit_fhour(self):
        assert hrrr_grib2_url("20260726", 12, 6) == (
            "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.20260726/conus/"
            "hrrr.t12z.wrfprsf06.grib2"
        )

    def test_idx_url(self):
        assert hrrr_idx_url("20260726", 0, 48) == (
            "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.20260726/conus/"
            "hrrr.t00z.wrfprsf48.grib2.idx"
        )


# ---------------------------------------------------------------------------
# CONUS domain gate — Lambert grid bounds, all-or-nothing
# ---------------------------------------------------------------------------


class TestHrrrDomain:
    def test_kbos_kbwi_inside(self):
        # KBOS (Boston) → KBWI (Baltimore), the issue's integration route.
        points = [_rp(42.3656, -71.0096), _rp(39.1754, -76.6683, 320)]
        assert route_in_hrrr_domain(points) is True

    def test_one_point_outside_fails_all_or_nothing(self):
        # Boston → Bermuda: the oceanic endpoint is off-grid.
        points = [_rp(42.3656, -71.0096), _rp(32.36, -64.68, 660)]
        assert route_in_hrrr_domain(points) is False

    def test_alaska_outside(self):
        # PANC — HRRR-Alaska is a separate domain, out of scope.
        assert route_in_hrrr_domain([_rp(61.17, -149.99)]) is False

    def test_hawaii_outside(self):
        assert route_in_hrrr_domain([_rp(21.32, -157.92)]) is False

    def test_europe_outside(self):
        assert route_in_hrrr_domain([_rp(51.47, -0.46)]) is False

    def test_empty_route_is_outside(self):
        assert route_in_hrrr_domain([]) is False

    def test_southern_canada_edge_inside(self):
        # The Lambert grid reaches into southern Canada (e.g. Toronto) even
        # though it is not a lat/lon rectangle — the projection is the truth.
        assert route_in_hrrr_domain([_rp(43.68, -79.63)]) is True


# ---------------------------------------------------------------------------
# Horizons + run selection across 18h/48h cycles
# ---------------------------------------------------------------------------


class TestHrrrHorizons:
    @pytest.mark.parametrize("cycle", sorted(HRRR_EXTENDED_CYCLES))
    def test_extended_cycles_reach_48h(self, cycle):
        assert hrrr_run_horizon_hours(cycle) == 48

    @pytest.mark.parametrize("cycle", [1, 5, 9, 15, 23])
    def test_intermediate_cycles_stop_at_18h(self, cycle):
        assert hrrr_run_horizon_hours(cycle) == 18


class _FakeHead:
    """Session stub recording HEAD-probed URLs and answering 200 for a set."""

    def __init__(self, ok_urls):
        self.ok_urls = ok_urls
        self.probed: list[str] = []

    def head(self, url, timeout=10):
        self.probed.append(url)

        class _Resp:
            status_code = 200 if url in self.ok_urls else 404

        return _Resp()


class TestHrrrRunSelection:
    def test_short_lead_uses_freshest_hourly_cycle(self):
        # Now 15:30z, flight 17–19z: the 14z intermediate cycle (18h horizon)
        # covers it and is the freshest published (15z is within the delay).
        now = _utc(2026, 7, 26, 15, 30)
        dep = _utc(2026, 7, 26, 17)
        sess = _FakeHead({hrrr_idx_url("20260726", 14, 5)})
        run = find_latest_hrrr_run(
            dep, session=sess, as_of_time=now,
            cover_until=dep + timedelta(hours=2),
        )
        assert run == ("20260726", 14)
        # The probe targets the LAST needed forecast hour, not f00.
        assert sess.probed[0].endswith("hrrr.t14z.wrfprsf05.grib2.idx")

    def test_next_day_window_skips_intermediate_cycles(self):
        # Flight ~22h out: intermediate cycles (18h) can't cover it, so the
        # finder must fall back to the freshest EXTENDED cycle (12z).
        now = _utc(2026, 7, 26, 15, 30)
        dep = _utc(2026, 7, 27, 12)
        cover = dep + timedelta(hours=2)
        sess = _FakeHead({hrrr_idx_url("20260726", 12, 26)})
        run = find_latest_hrrr_run(dep, session=sess, as_of_time=now, cover_until=cover)
        assert run == ("20260726", 12)
        # No intermediate cycle was even probed.
        assert all("t14z" not in u and "t15z" not in u and "t13z" not in u
                   for u in sess.probed)

    def test_window_beyond_48h_returns_none(self):
        now = _utc(2026, 7, 26, 15, 30)
        dep = _utc(2026, 7, 29, 12)  # ~69h out
        sess = _FakeHead(set())
        assert find_latest_hrrr_run(
            dep, session=sess, as_of_time=now,
            cover_until=dep + timedelta(hours=2),
        ) is None
        assert sess.probed == []  # nothing can cover — no network at all

    def test_publish_delay_excludes_brand_new_cycle(self):
        # At 15:30z the 15z cycle is only 0.5h old (< 1.25h delay) — the
        # finder must not probe it even though its horizon covers.
        now = _utc(2026, 7, 26, 15, 30)
        dep = _utc(2026, 7, 26, 16)
        sess = _FakeHead({hrrr_idx_url("20260726", 14, 2)})
        run = find_latest_hrrr_run(
            dep, session=sess, as_of_time=now, cover_until=dep,
        )
        assert run == ("20260726", 14)
        assert all("t15z" not in u for u in sess.probed)

    def test_extended_cycles_only_filter(self):
        # Used by the freshness readiness check: 14z covers but is filtered.
        # The 12z probe targets f04 (covering need_until = 15:30z).
        now = _utc(2026, 7, 26, 15, 30)
        sess = _FakeHead({hrrr_idx_url("20260726", 12, 4)})
        run = find_latest_hrrr_run(
            now, session=sess, as_of_time=now, cover_until=now,
            extended_cycles_only=True,
        )
        assert run == ("20260726", 12)
        assert all("t14z" not in u and "t13z" not in u for u in sess.probed)

    def test_walks_back_past_unpublished_cycles(self):
        # 14z missing on S3 → falls back to 13z.
        now = _utc(2026, 7, 26, 15, 30)
        dep = _utc(2026, 7, 26, 16)
        sess = _FakeHead({hrrr_idx_url("20260726", 13, 3)})
        assert find_latest_hrrr_run(
            dep, session=sess, as_of_time=now, cover_until=dep,
        ) == ("20260726", 13)


class TestHrrrWindowOutOfRange:
    def test_within_48h_in_range(self):
        now = _utc(2026, 7, 26, 15, 30)
        assert hrrr_window_out_of_range(
            _utc(2026, 7, 27, 12), 2.0, as_of_time=now,
        ) is False

    def test_beyond_48h_out_of_range(self):
        now = _utc(2026, 7, 26, 15, 30)
        assert hrrr_window_out_of_range(
            _utc(2026, 7, 29, 12), 2.0, as_of_time=now,
        ) is True


class TestHrrrWindowHours:
    def test_hourly_coverage(self):
        fh = compute_hrrr_flight_window_hours(
            "20260726", 12, _utc(2026, 7, 26, 15), 2.0,
        )
        assert fh == [3, 4, 5]

    def test_non_round_departure_brackets_window_end(self):
        fh = compute_hrrr_flight_window_hours(
            "20260726", 12, _utc(2026, 7, 26, 15, 15), 2.0,
        )
        # 15:15 + 2h → needs 15..18z = f03..f06.
        assert fh == [3, 4, 5, 6]

    def test_clamped_to_horizon(self):
        # 06z extended run, flight at +47..49h clamps at f48.
        fh = compute_hrrr_flight_window_hours(
            "20260726", 6, _utc(2026, 7, 28, 5), 2.0,
        )
        assert fh[-1] == 48


# ---------------------------------------------------------------------------
# Idx planning — sounding set (incl. CIMIXR) + instantaneous cloud diags
# ---------------------------------------------------------------------------

_FAKE_IDX = "\n".join([
    "1:0:d=2026072612:REFC:entire atmosphere:6 hour fcst:",
    "2:1000:d=2026072612:TMP:125 mb:6 hour fcst:",
    "3:2000:d=2026072612:TMP:500 mb:6 hour fcst:",
    "4:3000:d=2026072612:DPT:500 mb:6 hour fcst:",
    "5:4000:d=2026072612:RH:500 mb:6 hour fcst:",
    "6:5000:d=2026072612:UGRD:500 mb:6 hour fcst:",
    "7:6000:d=2026072612:VGRD:500 mb:6 hour fcst:",
    "8:7000:d=2026072612:VVEL:500 mb:6 hour fcst:",
    "9:8000:d=2026072612:HGT:500 mb:6 hour fcst:",
    "10:9000:d=2026072612:CLMR:500 mb:6 hour fcst:",
    "11:10000:d=2026072612:CIMIXR:500 mb:6 hour fcst:",
    "12:11000:d=2026072612:TMP:1013.2 mb:6 hour fcst:",
    "13:12000:d=2026072612:LCDC:low cloud layer:6 hour fcst:",
    "14:13000:d=2026072612:MCDC:middle cloud layer:6 hour fcst:",
    "15:14000:d=2026072612:HCDC:high cloud layer:6 hour fcst:",
    "16:15000:d=2026072612:TCDC:entire atmosphere:6 hour fcst:",
    "17:16000:d=2026072612:HGT:cloud ceiling:6 hour fcst:",
    "18:17000:d=2026072612:HGT:cloud base:6 hour fcst:",
    "19:18000:d=2026072612:HGT:cloud top:6 hour fcst:",
    "20:19000:d=2026072612:PRES:cloud base:6 hour fcst:",
    "21:20000:d=2026072612:CAPE:surface:6 hour fcst:",
    "22:21000:d=2026072612:CAPE:90-0 mb above ground:6 hour fcst:",
    "23:22000:d=2026072612:CIN:90-0 mb above ground:6 hour fcst:",
    "24:23000:d=2026072612:CAPE:255-0 mb above ground:6 hour fcst:",
])


class TestHrrrIdxPlanning:
    def test_sounding_plan_includes_cimixr_and_direct_fields(self):
        ranges = plan_hrrr_sounding_byte_ranges(_FAKE_IDX)
        got = {(r.variable, r.level_hpa) for r in ranges}
        for var in ("TMP", "DPT", "RH", "UGRD", "VGRD", "VVEL", "HGT",
                    "CLMR", "CIMIXR"):
            assert (var, 500) in got

    def test_sounding_plan_skips_stratospheric_and_fractional_levels(self):
        ranges = plan_hrrr_sounding_byte_ranges(_FAKE_IDX)
        levels = {r.level_hpa for r in ranges}
        assert 125 not in levels  # above HRRR_SOUNDING_MIN_LEVEL_HPA
        assert all(lv >= HRRR_SOUNDING_MIN_LEVEL_HPA for lv in levels)
        # The non-integer 1013.2 mb near-surface extra is dropped.
        offsets = {r.start for r in ranges}
        assert 11000 not in offsets

    def test_sounding_plan_byte_ranges_end_at_next_message(self):
        ranges = plan_hrrr_sounding_byte_ranges(_FAKE_IDX)
        by_offset = {r.start: r for r in ranges}
        assert by_offset[2000].end == 2999  # TMP 500mb runs to DPT's offset - 1

    def test_cloud_diag_plan_selects_instantaneous_set(self):
        ranges = plan_hrrr_cloud_diag_byte_ranges(_FAKE_IDX)
        got = {(r.variable, r.level_str) for r in ranges}
        assert got == {
            ("LCDC", "low cloud layer"),
            ("MCDC", "middle cloud layer"),
            ("HCDC", "high cloud layer"),
            ("TCDC", "entire atmosphere"),
            ("HGT", "cloud ceiling"),
            ("HGT", "cloud base"),
            ("CAPE", "90-0 mb above ground"),
            ("CIN", "90-0 mb above ground"),
        }
        # Surface/255-0mb CAPE and PRES/HGT cloud top are deliberately absent
        # (ambiguous decode / no NWPCloudDiagnostics home).
        assert ("CAPE", "surface") not in got
        assert ("CAPE", "255-0 mb above ground") not in got
        assert ("PRES", "cloud base") not in got
        assert ("HGT", "cloud top") not in got


# ---------------------------------------------------------------------------
# Lambert-grid interpolation (decode.py shared branch)
# ---------------------------------------------------------------------------


def _lambert_test_attrs() -> dict:
    return {
        "GRIB_gridType": "lambert",
        "GRIB_LaDInDegrees": 38.5,
        "GRIB_LoVInDegrees": 262.5,
        "GRIB_Latin1InDegrees": 38.5,
        "GRIB_Latin2InDegrees": 38.5,
        "GRIB_latitudeOfFirstGridPointInDegrees": 21.138123,
        "GRIB_longitudeOfFirstGridPointInDegrees": 237.280472,
        "GRIB_DxInMetres": 3000.0,
        "GRIB_DyInMetres": 3000.0,
        "GRIB_iScansNegatively": 0,
        "GRIB_jScansPositively": 1,
    }


def _latlon_at_frac(fx: float, fy: float) -> tuple[float, float]:
    """Inverse-project a fractional (x, y) grid index to (lat, lon)."""
    import pyproj

    a = _lambert_test_attrs()
    proj = pyproj.Proj(
        proj="lcc", lat_1=a["GRIB_Latin1InDegrees"], lat_2=a["GRIB_Latin2InDegrees"],
        lat_0=a["GRIB_LaDInDegrees"], lon_0=a["GRIB_LoVInDegrees"], R=6371229.0,
    )
    x0, y0 = proj(
        a["GRIB_longitudeOfFirstGridPointInDegrees"],
        a["GRIB_latitudeOfFirstGridPointInDegrees"],
    )
    lon, lat = proj(x0 + fx * 3000.0, y0 + fy * 3000.0, inverse=True)
    return lat, lon


def _lambert_dataset(ny: int = 60, nx: int = 80):
    """Synthetic cfgrib-shaped Lambert dataset: value = 2*ix + 3*iy."""
    import numpy as np
    import xarray as xr

    vals = (2.0 * np.arange(nx)[None, :] + 3.0 * np.arange(ny)[:, None])
    da = xr.DataArray(
        vals, dims=("y", "x"),
        coords={"y": np.arange(ny), "x": np.arange(nx)},
        attrs=_lambert_test_attrs(),
    )
    return da


class TestLambertInterpolation:
    def test_interpolate_per_point_bilinear_on_projected_axes(self):
        from weatherbrief.fetch.grib.decode import _interpolate_per_point

        da = _lambert_dataset()
        lat, lon = _latlon_at_frac(10.25, 20.5)
        vals = _interpolate_per_point(da, [lat], [lon])
        assert vals[0] == pytest.approx(2 * 10.25 + 3 * 20.5, abs=1e-3)

    def test_interpolate_per_point_outside_grid_is_none(self):
        from weatherbrief.fetch.grib.decode import _interpolate_per_point

        da = _lambert_dataset()
        # Well outside the tiny 80×60 test grid.
        lat, lon = _latlon_at_frac(500.0, 500.0)
        assert _interpolate_per_point(da, [lat], [lon]) == [None]

    def test_pressure_vars_gather_on_lambert_grid(self):
        import numpy as np
        import xarray as xr

        from weatherbrief.fetch.grib.decode import (
            _HRRR_FULL_VAR_MAP,
            _decode_pressure_vars_from_datasets,
        )

        ny, nx = 60, 80
        base = 2.0 * np.arange(nx)[None, :] + 3.0 * np.arange(ny)[:, None]
        t = np.stack([base + 250.0, base + 260.0])  # two levels
        ds = xr.Dataset(
            {
                "t": xr.DataArray(
                    t, dims=("isobaricInhPa", "y", "x"),
                    attrs=_lambert_test_attrs(),
                ),
            },
            coords={
                "isobaricInhPa": [500, 700],
                "y": np.arange(ny),
                "x": np.arange(nx),
            },
        )
        lat, lon = _latlon_at_frac(10.25, 20.5)
        results, covered = _decode_pressure_vars_from_datasets(
            [ds], [lat], [lon], var_map=_HRRR_FULL_VAR_MAP,
        )
        expected = 2 * 10.25 + 3 * 20.5
        assert covered == [True]
        assert results[0][500]["raw_temperature_k"] == pytest.approx(
            expected + 250.0, abs=1e-3,
        )
        assert results[0][700]["raw_temperature_k"] == pytest.approx(
            expected + 260.0, abs=1e-3,
        )

    def test_wind_rotation_identity_on_central_meridian(self):
        from weatherbrief.fetch.grib.decode import _lambert_wind_rotation

        # LoV = 262.5°E ≡ 97.5°W: grid north == true north there.
        (nx, ny), = _lambert_wind_rotation(_lambert_test_attrs(), [38.5], [-97.5])
        assert nx == pytest.approx(0.0, abs=1e-6)
        assert ny == pytest.approx(1.0, abs=1e-6)

    def test_wind_rotation_matches_cone_constant_off_meridian(self):
        import math

        from weatherbrief.fetch.grib.decode import _lambert_wind_rotation

        # Closed form for a tangent Lambert cone: |rotation| = sin(lat_1)·Δλ.
        # East of the central meridian the meridian converges back toward the
        # projection apex, so TRUE north tilts LEFT of grid north (nx < 0).
        dlon = 10.0
        (nx, ny), = _lambert_wind_rotation(
            _lambert_test_attrs(), [40.0], [-97.5 + dlon],
        )
        expected_rot = -math.radians(math.sin(math.radians(38.5)) * dlon)
        got_rot = math.atan2(nx, ny)
        assert got_rot == pytest.approx(expected_rot, abs=1e-4)


# ---------------------------------------------------------------------------
# Sounding conversion + diagnostics builder
# ---------------------------------------------------------------------------


class TestHrrrConversions:
    def test_convert_raw_sounding_prefers_direct_dewpoint(self):
        from weatherbrief.fetch.grib.decode import _convert_raw_sounding

        out = _convert_raw_sounding(
            {
                "raw_temperature_k": 273.15,
                "raw_dewpoint_k": 263.15,
                "raw_relative_humidity_pct": 100.0,  # would give Td == T via Magnus
                "raw_u_wind_m_s": 10.0,
                "raw_v_wind_m_s": 0.0,
                "vertical_velocity_pa_s": -1.5,
                "geopotential_height_m": 5570.0,
            },
            500,
        )
        assert out["dewpoint_c"] == pytest.approx(-10.0)
        assert out["temperature_c"] == pytest.approx(0.0)
        assert out["vertical_velocity_pa_s"] == pytest.approx(-1.5)  # no omega conv
        assert out["geopotential_height_m"] == pytest.approx(5570.0)
        assert out["wind_speed_kt"] == pytest.approx(19.4384, abs=0.01)
        assert out["wind_direction_deg"] == pytest.approx(270.0)

    def test_magnus_fallback_when_no_direct_dewpoint(self):
        from weatherbrief.fetch.grib.decode import _convert_raw_sounding

        out = _convert_raw_sounding(
            {"raw_temperature_k": 283.15, "raw_relative_humidity_pct": 100.0},
            850,
        )
        assert out["dewpoint_c"] == pytest.approx(10.0, abs=0.1)


class TestBuildHrrrCloudDiagnostics:
    def test_full_mapping(self):
        from weatherbrief.fetch.grib.decode import build_hrrr_cloud_diagnostics

        diag = build_hrrr_cloud_diagnostics({
            "low_cover_pct": 75.0,
            "mid_cover_pct": 40.0,
            "high_cover_pct": 5.0,
            "total_cover_pct": 80.0,
            "ceiling_gpm": 500.0,
            "cloud_base_gpm": 450.0,
            "ml_cape_jkg": 1200.0,
            "ml_cin_jkg": -85.0,
        })
        assert diag is not None
        assert diag.low.cover_pct == 75.0
        assert diag.low.base_ft == pytest.approx(450 * 3.28084, abs=1)
        # No per-band geometry beyond the overall base — ECMWF-like shape.
        assert diag.mid.base_ft is None and diag.high.base_ft is None
        assert diag.low.top_ft is None and diag.low.top_temp_c is None
        assert diag.ceiling_ft == pytest.approx(500 * 3.28084, abs=1)
        assert diag.ml_cape_jkg == 1200.0
        assert diag.ml_cin_jkg == -85.0  # already negative — passed through
        assert diag.convective_cover_pct is None  # no HRRR convective fields

    def test_cin_positive_noise_floored_to_zero(self):
        from weatherbrief.fetch.grib.decode import build_hrrr_cloud_diagnostics

        diag = build_hrrr_cloud_diagnostics({"ml_cin_jkg": 0.4})
        assert diag is not None
        assert diag.ml_cin_jkg == 0.0

    def test_empty_returns_none(self):
        from weatherbrief.fetch.grib.decode import build_hrrr_cloud_diagnostics

        assert build_hrrr_cloud_diagnostics({}) is None


# ---------------------------------------------------------------------------
# Enrichment gate — HRRR upgrade of the gfs slot, with GFS fallback
# ---------------------------------------------------------------------------


class TestGfsSlotHrrrGate:
    def _gfs_cross_sections(self):
        from weatherbrief.models import ModelSource, RouteCrossSection

        cs = RouteCrossSection(
            model=ModelSource.GFS,
            route_points=[],
            fetched_at=_utc(2026, 7, 26),
            point_forecasts=[],
        )
        return [cs]

    def test_gate_passes_and_reports_hrrr_source(self, tmp_path):
        import weatherbrief.fetch.grib as grib_mod

        route = [_rp(42.3656, -71.0096), _rp(39.1754, -76.6683, 320)]
        dep = _utc(2026, 7, 26, 17)
        with patch(
            "weatherbrief.fetch.grib.hrrr_fetch.find_latest_hrrr_run",
            return_value=("20260726", 12),
        ), patch.object(
            grib_mod, "_enrich_gfs_from_hrrr", return_value=1753531200,
        ) as enrich_hrrr:
            ts, source = grib_mod._enrich_gfs_inner(
                self._gfs_cross_sections(), [], route, dep,
                data_dir=tmp_path, flight_duration_hours=2.0,
            )
        assert ts == 1753531200
        assert source == "hrrr:noaa"
        assert enrich_hrrr.call_count == 1

    def test_route_outside_conus_uses_plain_gfs(self, tmp_path):
        import weatherbrief.fetch.grib as grib_mod

        route = [_rp(51.47, -0.46), _rp(48.35, 11.79, 500)]  # Europe
        dep = _utc(2026, 7, 26, 17)
        with patch.object(
            grib_mod, "find_latest_run", return_value=None,
        ) as gfs_finder, patch(
            "weatherbrief.fetch.grib.hrrr_fetch.find_latest_hrrr_run",
        ) as hrrr_finder:
            ts, source = grib_mod._enrich_gfs_inner(
                self._gfs_cross_sections(), [], route, dep,
                data_dir=tmp_path, flight_duration_hours=2.0,
            )
        assert (ts, source) == (None, None)
        hrrr_finder.assert_not_called()  # domain gate short-circuits
        gfs_finder.assert_called_once()

    def test_no_covering_run_falls_back_to_gfs(self, tmp_path):
        import weatherbrief.fetch.grib as grib_mod

        route = [_rp(42.3656, -71.0096), _rp(39.1754, -76.6683, 320)]
        dep = _utc(2026, 7, 30, 17)  # beyond any HRRR horizon
        with patch(
            "weatherbrief.fetch.grib.hrrr_fetch.find_latest_hrrr_run",
            return_value=None,
        ), patch.object(
            grib_mod, "find_latest_run", return_value=None,
        ) as gfs_finder:
            ts, source = grib_mod._enrich_gfs_inner(
                self._gfs_cross_sections(), [], route, dep,
                data_dir=tmp_path, flight_duration_hours=2.0,
            )
        assert (ts, source) == (None, None)
        gfs_finder.assert_called_once()

    def test_hrrr_total_failure_falls_back_to_gfs(self, tmp_path):
        import weatherbrief.fetch.grib as grib_mod

        route = [_rp(42.3656, -71.0096), _rp(39.1754, -76.6683, 320)]
        dep = _utc(2026, 7, 26, 17)
        with patch(
            "weatherbrief.fetch.grib.hrrr_fetch.find_latest_hrrr_run",
            return_value=("20260726", 12),
        ), patch.object(
            grib_mod, "_enrich_gfs_from_hrrr", return_value=None,
        ), patch.object(
            grib_mod, "find_latest_run", return_value=None,
        ) as gfs_finder:
            ts, source = grib_mod._enrich_gfs_inner(
                self._gfs_cross_sections(), [], route, dep,
                data_dir=tmp_path, flight_duration_hours=2.0,
            )
        assert (ts, source) == (None, None)
        gfs_finder.assert_called_once()


class TestHrrrFillSemantics:
    """When HRRR sourced the gfs slot, the GFS averaged-window machinery is off."""

    def _run_enrich(self, tmp_path, gfs_result):
        import weatherbrief.fetch.grib as grib_mod

        route = [_rp(42.3656, -71.0096)]
        dep = _utc(2026, 7, 26, 17)
        with patch.object(
            grib_mod, "_enrich_gfs", return_value=gfs_result,
        ), patch.object(
            grib_mod, "_enrich_ecmwf", return_value=None,
        ), patch.object(
            grib_mod, "_prepare_icon_eu", return_value=(None, None),
        ), patch(
            "weatherbrief.fetch.grib.fill.propagate_all",
        ) as propagate, patch(
            "weatherbrief.fetch.grib.fill.apply_gfs_rh_condensate_gate",
        ) as gate:
            init_times, _skips, sources = grib_mod.enrich_forecasts(
                [], [], route, dep,
                data_dir=tmp_path, flight_duration_hours=1.0,
            )
        return init_times, sources, propagate, gate

    def test_hrrr_source_disables_gfs_midpoint_and_gate(self, tmp_path):
        init_times, sources, propagate, gate = self._run_enrich(
            tmp_path, (1753531200, "hrrr:noaa"),
        )
        assert sources["gfs"] == "hrrr:noaa"
        assert init_times["gfs"] == 1753531200
        assert propagate.call_args.kwargs["gfs_init"] is None
        gate.assert_not_called()

    def test_plain_gfs_keeps_midpoint_and_gate(self, tmp_path):
        init_times, sources, propagate, gate = self._run_enrich(
            tmp_path, (1753531200, "gfs:noaa"),
        )
        assert sources["gfs"] == "gfs:noaa"
        assert propagate.call_args.kwargs["gfs_init"] == datetime.fromtimestamp(
            1753531200, tz=timezone.utc,
        )
        gate.assert_called_once()


# ---------------------------------------------------------------------------
# Cache TTL + freshness registry wiring
# ---------------------------------------------------------------------------


def test_hrrr_cache_ttl_registered():
    from weatherbrief.fetch.grib.cache import MODEL_TTL_SECONDS

    assert MODEL_TTL_SECONDS["hrrr"] == 6 * 3600


def test_hrrr_source_registry_entry():
    from weatherbrief.fetch.freshness import registry

    cfg = registry.SOURCE_REGISTRY["hrrr:noaa"]
    assert cfg.readiness_check == "hrrr_noaa"
    assert cfg.model_label == "HRRR"
    assert cfg.provider_label == "NOAA"
    assert cfg.role == "primary-sounding"
    # Only the extended cycles are tracked (hourly tracking would flag
    # short-lead US packs stale every hour).
    assert cfg.cycles == (0, 6, 12, 18)
    for h in cfg.cycles:
        assert registry.run_horizon("hrrr:noaa", _utc(2026, 7, 26, h)) == timedelta(hours=48)


def test_hrrr_readiness_dispatch_registered():
    from weatherbrief.fetch.freshness.sources import _DISPATCH, _check_hrrr_noaa

    assert _DISPATCH["hrrr_noaa"] is _check_hrrr_noaa


def test_hrrr_tracked_by_marker_store():
    from weatherbrief.fetch.freshness.sources import all_tracked_sources

    assert ("hrrr:noaa", "hrrr") in all_tracked_sources()
