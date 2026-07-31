"""HRRR full sounding replacement tests (#457, Task 5 / commit 2).

The gate in ``_enrich_gfs_inner`` routes HRRR-eligible routes to
``_enrich_hrrr_replace``: per fhour the 40-level sounding set is byte-range
fetched (cache key ``HRRR_SND``), decoded per route point on the Lambert
grid, and REPLACES the hourly's Open-Meteo pressure levels via
``_replace_pressure_levels_from_grib`` — the ECMWF shape. The commit-1 diag
pass (cloud diagnostics + gap-fill surface extras) then applies onto the
same hourlies. Total failure (no idx / zero hours replaced) falls back to
plain GFS — never a half-HRRR pack.

Covered here:
- ``_convert_raw_sounding`` direct HRRR branches: DPT-direct dewpoint,
  HGT-direct geopotential height, VVEL-direct omega — no Magnus / −ρgw
  derivations when the model ships the field itself.
- End-to-end replacement on a synthetic cfgrib-readable Lambert GRIB2 blob
  (eccodes sample-built, same builder as test_hrrr_decode.py): the replaced
  ``pressure_levels`` carry the direct fields plus rotated winds and
  CLWMR/CIMIXR, the surface-PRES sentinel key drops out, and the diag pass
  still lands.
- Zero coverage → ``_enrich_hrrr_replace`` returns None (the gate then calls
  the plain-GFS run-finder) and the diag pass never runs, so the fallback
  starts from a pack nothing HRRR-specific has touched.
- The gate's crash catch-all fires with ``_enrich_hrrr_replace`` raising.
- fill.py: two HRRR anchors (40 levels) bracketing an Open-Meteo gap hour
  (28 levels) → ``_interp_levels_hourly`` rebuilds the gap by interpolation.

No network access, no process-pool decode (``_dispatch_decode`` is faked
in-process).
"""

from __future__ import annotations

import io
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import requests

import weatherbrief.fetch.grib as grib_mod
from weatherbrief.fetch.grib.decode import (
    _convert_raw_sounding,
    _rotate_grid_wind_to_earth,
    decode_hrrr_pressure_per_point,
)
from weatherbrief.fetch.grib.fill import _interp_levels_hourly
from weatherbrief.fetch.open_meteo import magnus_dewpoint
from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    PressureLevelData,
    RouteCrossSection,
    RoutePoint,
    Waypoint,
    WaypointForecast,
)

DEP = datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc)
HRRR_RUN = ("20260720", 12)          # 12z → fhours 3,4,5 valid at 15/16/17 UTC
GFS_RUN = ("20260720", 6)
HRRR_TS = int(datetime(2026, 7, 20, 12, tzinfo=timezone.utc).timestamp())
GFS_TS = int(datetime(2026, 7, 20, 6, tzinfo=timezone.utc).timestamp())

CONUS_POINT = (39.86, -104.67)  # KDEN

# Fractional grid indices of the route point on the synthetic Lambert grid.
_FJ, _FI = 3.5, 4.25


def _rp(lat: float, lon: float) -> RoutePoint:
    return RoutePoint(lat=lat, lon=lon, distance_from_origin_nm=0.0)


def _utc(h: int) -> datetime:
    return datetime(2026, 7, 20, h, 0, tzinfo=timezone.utc)


def _gfs_cross_section(hours: list[int]) -> tuple[RouteCrossSection, WaypointForecast]:
    """One-point GFS cross-section; each hourly carries one OM 850 level.

    The OM level's geopotential height (1111 m) deliberately disagrees with
    the GRIB HGT — the replaced column must carry the GRIB value.
    """
    hourly = [
        HourlyForecast(
            time=_utc(h),
            pressure_levels=[
                PressureLevelData(pressure_hpa=850, geopotential_height_m=1111.0)
            ],
        )
        for h in hours
    ]
    wpt = Waypoint(icao="KDEN", name="Denver", lat=CONUS_POINT[0], lon=CONUS_POINT[1])
    wf = WaypointForecast(
        waypoint=wpt, model=ModelSource.GFS,
        fetched_at=datetime.now(tz=timezone.utc), hourly=hourly,
    )
    cs = RouteCrossSection(
        model=ModelSource.GFS, route_points=[], fetched_at=wf.fetched_at,
        point_forecasts=[wf],
    )
    return cs, wf


# ---------------------------------------------------------------------------
# Synthetic Lambert GRIB2 sounding blob (same builder as test_hrrr_decode.py)
# ---------------------------------------------------------------------------

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
    from weatherbrief.fetch.grib.hrrr_fetch import hrrr_projection

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


# ---------------------------------------------------------------------------
# Mock plumbing
# ---------------------------------------------------------------------------

# Sentinel range lists: the planners are mocked, so identity (not content)
# routes the fake byte-range fetch to the right blob.
_SND_RANGES = ["snd-sentinel"]
_DIAG_RANGES = ["diag-sentinel"]

_HRRR_DIAG_RAW = {
    "low_cover_pct": 40.0,
    "ceiling_m": 1500.0,
    "visibility_m": 8000.0,
    "gust_ms": 10.0,
    "sfc_cape_jkg": 500.0,
    "sfc_cin_jkg": -25.0,
}


def _patch_replace_flow(monkeypatch, sounding_bytes, *, dispatch=None):
    """Mock every network boundary of ``_enrich_hrrr_replace``.

    The sounding decode itself runs FOR REAL on the synthetic Lambert blob
    (via the cache file the flow writes under tmp_path); the diag decode is
    faked with a flat raw dict (its own decode is covered in
    test_hrrr_decode.py).
    """
    monkeypatch.setattr(grib_mod, "fetch_idx", lambda *a, **kw: "idx text")
    monkeypatch.setattr(grib_mod, "is_cached", lambda run_dir, ck: False)
    monkeypatch.setattr(
        grib_mod, "plan_hrrr_sounding_byte_ranges", lambda idx: _SND_RANGES,
    )
    monkeypatch.setattr(
        grib_mod, "plan_hrrr_diag_byte_ranges", lambda idx: _DIAG_RANGES,
    )

    def _fake_fetch(init_date, init_hour, fhour, ranges,
                    session=None, url_builder=None):
        if ranges is _SND_RANGES:
            return sounding_bytes
        if ranges is _DIAG_RANGES:
            return b"diag-bytes"  # never decoded — diag dispatch is faked
        raise AssertionError(f"unexpected ranges {ranges!r}")

    monkeypatch.setattr(grib_mod, "fetch_cloud_diag_ranges", _fake_fetch)

    if dispatch is None:
        def dispatch(worker_name, path, lats, lons, **kw):
            if worker_name == "decode_hrrr_pressure":
                return decode_hrrr_pressure_per_point(
                    Path(path).read_bytes(), lats, lons,
                )
            if worker_name == "decode_hrrr_diag":
                return [dict(_HRRR_DIAG_RAW) for _ in lats]
            raise AssertionError(f"unexpected decode worker {worker_name}")
    monkeypatch.setattr(grib_mod, "_dispatch_decode", dispatch)


def _run_replace(cs, route_points, tmp_path, all_forecasts=None):
    return grib_mod._enrich_hrrr_replace(
        [cs], all_forecasts or [], route_points,
        HRRR_RUN[0], HRRR_RUN[1], DEP,
        data_dir=tmp_path, flight_duration_hours=2.0,
        session=requests.Session(),
    )


def _patch_gate_common(monkeypatch):
    """Gate-level mocks: warm cache (no downloads), call-order tracker."""
    calls: list[str] = []
    monkeypatch.setattr(grib_mod, "is_cached", lambda run_dir, ck: True)
    monkeypatch.setattr(
        grib_mod, "find_latest_hrrr_run",
        lambda *a, **kw: calls.append("hrrr") or HRRR_RUN,
    )
    monkeypatch.setattr(
        grib_mod, "find_latest_run",
        lambda *a, **kw: calls.append("gfs") or GFS_RUN,
    )
    monkeypatch.setattr(grib_mod, "fetch_idx", lambda *a, **kw: "idx text")
    # The plain-GFS worker pair is not under test here — no-op it so the GFS
    # path's success is signalled by find_latest_run + the returned ts alone.
    monkeypatch.setattr(grib_mod, "_enrich_clwmr_icmr", lambda *a: None)
    monkeypatch.setattr(grib_mod, "_enrich_cloud_diagnostics", lambda *a: None)
    return calls


# ---------------------------------------------------------------------------
# _convert_raw_sounding direct HRRR branches
# ---------------------------------------------------------------------------


class TestConvertRawSoundingHrrrDirects:
    """HRRR ships DPT / HGT / VVEL directly — no derivations (#457)."""

    def test_direct_dewpoint_preferred_over_magnus(self):
        out = _convert_raw_sounding(
            {
                "raw_temperature_k": 281.8125,
                "raw_relative_humidity_pct": 70.625,
                "raw_dewpoint_k": 277.25,
            },
            850,
        )
        # Direct: 277.25 − 273.15 = 4.10 °C. Magnus from the same T/RH gives
        # ≈3.6 °C — the branch taken is distinguishable.
        assert out["dewpoint_c"] == pytest.approx(4.10, abs=1e-9)

    def test_magnus_still_derives_when_no_direct_dewpoint(self):
        out = _convert_raw_sounding(
            {"raw_temperature_k": 281.8125, "raw_relative_humidity_pct": 70.625},
            850,
        )
        assert out["dewpoint_c"] == pytest.approx(3.6, abs=0.1)
        assert out["dewpoint_c"] != pytest.approx(4.10, abs=0.1)

    def test_dewpoint_zero_celsius_is_data(self):
        """None ≠ 0: a delivered 0 °C dewpoint must survive."""
        out = _convert_raw_sounding({"raw_dewpoint_k": 273.15}, 850)
        assert out["dewpoint_c"] == 0.0

    def test_direct_geopotential_height_gpm(self):
        out = _convert_raw_sounding({"raw_geopotential_height_gpm": 1461.25}, 850)
        assert out["geopotential_height_m"] == 1461.25

    def test_geopotential_height_zero_is_data(self):
        """None ≠ 0: sea-level gpm 0 is a real height, not a missing one."""
        out = _convert_raw_sounding({"raw_geopotential_height_gpm": 0.0}, 1000)
        assert out["geopotential_height_m"] == 0.0

    def test_direct_omega_preferred_over_w_derivation(self):
        out = _convert_raw_sounding(
            {
                "raw_omega_pa_s": 0.5,
                "raw_w_m_s": 99.0,  # must be ignored when VVEL is delivered
                "raw_temperature_k": 280.0,
            },
            850,
        )
        assert out["vertical_velocity_pa_s"] == 0.5

    def test_omega_zero_is_data(self):
        """None ≠ 0: zero vertical velocity is a real measurement."""
        out = _convert_raw_sounding({"raw_omega_pa_s": 0.0}, 850)
        assert out["vertical_velocity_pa_s"] == 0.0


# ---------------------------------------------------------------------------
# End-to-end sounding replacement
# ---------------------------------------------------------------------------


class TestHrrrSoundingReplacement:
    def test_replaced_levels_carry_direct_hrrr_fields(
        self, monkeypatch, tmp_path, sounding_bytes,
    ):
        lat, lon = _target_latlon(_FJ, _FI)
        cs, wf = _gfs_cross_section([15, 16, 17])
        _patch_replace_flow(monkeypatch, sounding_bytes)

        ts = _run_replace(cs, [_rp(lat, lon)], tmp_path)

        assert ts == HRRR_TS
        for h in wf.hourly:
            # OM's single 850 level is GONE — replaced by the GRIB column;
            # the surface-PRES sentinel key (0) drops out of the build.
            assert [pl.pressure_hpa for pl in h.pressure_levels] == [850, 500]

            lev850 = h.pressure_levels[0]
            # DPT-direct dewpoint: 275 + 0.4·3.5 + 0.2·4.25 = 277.25 K
            # → 4.10 °C (Magnus from T/RH would give ≈4.6 °C).
            assert lev850.dewpoint_c == pytest.approx(4.10, abs=0.15)
            assert lev850.temperature_c == pytest.approx(
                282.8125 - 273.15, abs=0.15,
            )
            # HGT-direct (gpm ≈ m): 1450 + 2·3.5 + 1·4.25 — NOT the OM
            # reference height (1111 m) the hourly carried before.
            assert lev850.geopotential_height_m == pytest.approx(1461.25, rel=1e-2)
            # VVEL-direct omega, no −ρgw conversion.
            assert lev850.vertical_velocity_pa_s == pytest.approx(0.5, rel=1e-2)
            # CLMR / CIMIXR ride the rebuilt levels (no separate patch pass).
            assert lev850.cloud_liquid_water_kg_kg == pytest.approx(
                1.5625e-5, rel=2e-2,
            )
            assert lev850.ice_mixing_ratio_kg_kg == pytest.approx(
                3.125e-5, rel=2e-2,
            )
            # Winds are earth-rotated at decode; speed/direction derive from
            # the rotated vector.
            exp_u, exp_v = _rotate_grid_wind_to_earth([3.0], [-1.5], [lon])
            assert lev850.wind_speed_kt == pytest.approx(
                math.hypot(exp_u[0], exp_v[0]) * 1.94384, rel=1e-2,
            )
            assert lev850.wind_direction_deg == pytest.approx(
                math.degrees(math.atan2(-exp_u[0], -exp_v[0])) % 360.0, abs=0.5,
            )

            lev500 = h.pressure_levels[1]
            assert lev500.temperature_c == pytest.approx(
                250.0 + 0.3 * _FJ + 0.15 * _FI - 273.15, abs=0.15,
            )
            assert lev500.vertical_velocity_pa_s == pytest.approx(-0.2, rel=1e-2)

            # The commit-1 diag pass still lands on the replaced hourlies.
            assert h.nwp_cloud_diagnostics is not None
            assert h.visibility_m == 8000.0
            assert h.wind_gusts_10m_kt == pytest.approx(19.4384)

    def test_waypoint_only_forecasts_replaced_with_gfs_model_source(
        self, monkeypatch, tmp_path, sounding_bytes,
    ):
        """``model_source=ModelSource.GFS``: GFS waypoint-only forecasts get
        the replacement; other models' forecasts at the same waypoint are
        left alone."""
        lat, lon = _target_latlon(_FJ, _FI)
        cs, _wf = _gfs_cross_section([15])
        _patch_replace_flow(monkeypatch, sounding_bytes)

        def _wp_wf(model: ModelSource) -> WaypointForecast:
            return WaypointForecast(
                waypoint=Waypoint(
                    icao="KDEN", name="Denver", lat=lat, lon=lon,
                ),
                model=model,
                fetched_at=datetime.now(tz=timezone.utc),
                hourly=[
                    HourlyForecast(
                        time=_utc(15),
                        pressure_levels=[PressureLevelData(pressure_hpa=850)],
                    ),
                ],
            )

        gfs_wp = _wp_wf(ModelSource.GFS)
        ecmwf_wp = _wp_wf(ModelSource.ECMWF)
        rp = RoutePoint(
            lat=lat, lon=lon, distance_from_origin_nm=0.0, waypoint_icao="KDEN",
        )

        ts = _run_replace(cs, [rp], tmp_path, all_forecasts=[gfs_wp, ecmwf_wp])

        assert ts == HRRR_TS
        assert [pl.pressure_hpa for pl in gfs_wp.hourly[0].pressure_levels] == [
            850, 500,
        ]
        # ECMWF at the same waypoint is NOT the gfs slot — untouched.
        assert [pl.pressure_hpa for pl in ecmwf_wp.hourly[0].pressure_levels] == [
            850,
        ]


# ---------------------------------------------------------------------------
# Fallback: zero coverage / crash
# ---------------------------------------------------------------------------


class TestHrrrReplacementFallback:
    def test_zero_coverage_returns_none_and_skips_diag_pass(
        self, monkeypatch, tmp_path, sounding_bytes,
    ):
        """Every point uncovered → None (gate falls back) and NO diag pass:
        the plain-GFS fallback must start from a pack nothing HRRR-specific
        has touched."""
        lat, lon = _target_latlon(_FJ, _FI)
        cs, wf = _gfs_cross_section([15, 16, 17])
        _patch_replace_flow(
            monkeypatch, sounding_bytes,
            dispatch=lambda *a, **kw: ([{}], [False]),
        )
        diag_calls: list = []
        monkeypatch.setattr(
            grib_mod, "_enrich_hrrr_diagnostics",
            lambda *a: diag_calls.append(a) or 0,
        )

        ts = _run_replace(cs, [_rp(lat, lon)], tmp_path)

        assert ts is None
        assert diag_calls == []
        # Open-Meteo levels untouched.
        for h in wf.hourly:
            assert [pl.pressure_hpa for pl in h.pressure_levels] == [850]
            assert h.nwp_cloud_diagnostics is None

    def test_zero_coverage_gate_falls_back_to_plain_gfs(
        self, monkeypatch, tmp_path,
    ):
        """Gate level: zero replaced hours → the plain-GFS run-finder runs."""
        cs, _wf = _gfs_cross_section([15, 16, 17])
        calls = _patch_gate_common(monkeypatch)
        monkeypatch.setattr(grib_mod, "route_in_hrrr_domain", lambda rps: True)
        monkeypatch.setattr(
            grib_mod, "_dispatch_decode", lambda *a, **kw: ([{}], [False]),
        )

        ts, source_key = grib_mod._enrich_gfs_inner(
            [cs], [], [_rp(*CONUS_POINT)], DEP,
            data_dir=tmp_path, flight_duration_hours=2.0,
        )

        assert (ts, source_key) == (GFS_TS, "gfs:noaa")
        assert calls == ["hrrr", "gfs"]  # GFS run-finder called AFTER HRRR

    def test_gate_catch_all_fires_when_replace_raises(
        self, monkeypatch, tmp_path,
    ):
        """Catch-all: an unexpected exception from ``_enrich_hrrr_replace``
        (e.g. a decode dead-letter) degrades to plain GFS — never a failed
        briefing."""
        cs, _wf = _gfs_cross_section([15, 16, 17])
        calls = _patch_gate_common(monkeypatch)
        monkeypatch.setattr(grib_mod, "route_in_hrrr_domain", lambda rps: True)

        def _boom(*a, **kw):
            raise RuntimeError("decode worker dead-letter")

        monkeypatch.setattr(grib_mod, "_enrich_hrrr_replace", _boom)

        ts, source_key = grib_mod._enrich_gfs_inner(  # must not raise
            [cs], [], [_rp(*CONUS_POINT)], DEP,
            data_dir=tmp_path, flight_duration_hours=2.0,
        )

        assert (ts, source_key) == (GFS_TS, "gfs:noaa")
        assert calls == ["hrrr", "gfs"]  # GFS run-finder called after the crash


class TestDiagCrashKeepsHrrrSounding:
    """Review fix (#457): a diag-pass crash AFTER the sounding replacement
    landed must not trigger the gate's catch-all → plain-GFS fallback (that
    produced a half-HRRR pack mis-attributed ``gfs:noaa``). The replacement
    stands, the pack stays ``hrrr:noaa``, and only the diag fields stay
    missing (house None semantics — missing ≠ quiet)."""

    def test_diag_crash_after_replacement_keeps_hrrr_sounding(
        self, monkeypatch, tmp_path, sounding_bytes,
    ):
        """Unit level: ``_enrich_hrrr_diagnostics`` raising must neither
        propagate nor void the replacement — the run timestamp is still
        returned and the replaced levels stand."""
        lat, lon = _target_latlon(_FJ, _FI)
        cs, wf = _gfs_cross_section([15, 16, 17])
        _patch_replace_flow(monkeypatch, sounding_bytes)

        def _boom(*a, **kw):
            raise RuntimeError("diag decode dead-letter")

        monkeypatch.setattr(grib_mod, "_enrich_hrrr_diagnostics", _boom)

        ts = _run_replace(cs, [_rp(lat, lon)], tmp_path)  # must not raise

        assert ts == HRRR_TS
        for h in wf.hourly:
            # Sounding replacement intact…
            assert [pl.pressure_hpa for pl in h.pressure_levels] == [850, 500]
            # …only the diag extras are missing.
            assert h.nwp_cloud_diagnostics is None
            assert h.visibility_m is None

    def test_diag_crash_gate_keeps_hrrr_source_no_gfs_fallback(
        self, monkeypatch, tmp_path, sounding_bytes,
    ):
        """Gate level: the GFS run-finder is NOT invoked and the gate still
        returns the HRRR source key when the diag pass crashes."""
        lat, lon = _target_latlon(_FJ, _FI)
        cs, wf = _gfs_cross_section([15, 16, 17])
        calls = _patch_gate_common(monkeypatch)
        _patch_replace_flow(monkeypatch, sounding_bytes)
        monkeypatch.setattr(grib_mod, "route_in_hrrr_domain", lambda rps: True)

        def _boom(*a, **kw):
            raise RuntimeError("diag decode dead-letter")

        monkeypatch.setattr(grib_mod, "_enrich_hrrr_diagnostics", _boom)

        ts, source_key = grib_mod._enrich_gfs_inner(  # must not raise
            [cs], [], [_rp(lat, lon)], DEP,
            data_dir=tmp_path, flight_duration_hours=2.0,
        )

        assert (ts, source_key) == (HRRR_TS, "hrrr:noaa")
        assert calls == ["hrrr"]  # plain-GFS run-finder never called
        for h in wf.hourly:
            assert [pl.pressure_hpa for pl in h.pressure_levels] == [850, 500]
            assert h.nwp_cloud_diagnostics is None


# ---------------------------------------------------------------------------
# fill.py: gap-hour sounding interpolation between HRRR anchors
# ---------------------------------------------------------------------------


def _sounding_levels(temp_base: float) -> list[PressureLevelData]:
    """40 levels at 25 hPa spacing — the HRRR-replaced anchor shape."""
    return [
        PressureLevelData(
            pressure_hpa=p,
            temperature_c=temp_base - (1000 - p) * 0.05,
            relative_humidity_pct=60.0,
            dewpoint_c=temp_base - 5.0 - (1000 - p) * 0.05,
            wind_speed_kt=20.0,
            wind_direction_deg=270.0,
            geopotential_height_m=float((1000 - p) * 10),
            vertical_velocity_pa_s=-0.1,
            cloud_liquid_water_kg_kg=1.0e-5 + temp_base * 1.0e-7,
            ice_mixing_ratio_kg_kg=2.0e-5,
        )
        for p in range(1000, 0, -25)
    ]


def _om_levels() -> list[PressureLevelData]:
    """28 levels — the Open-Meteo shape a gap hour keeps before the fill."""
    return [
        PressureLevelData(pressure_hpa=p, temperature_c=-99.0)
        for p in range(1000, 325, -25)
    ]


class TestHrrrGapHourFill:
    def test_gap_hour_interpolated_between_two_hrrr_anchors(self):
        """40-level HRRR anchors vs 28-level OM gap hour: the level-count
        heuristic marks the HRRR hours as GRIB anchors and rebuilds the gap
        hour on the HRRR level set by linear interpolation."""
        hours = [
            HourlyForecast(time=_utc(15), pressure_levels=_sounding_levels(10.0)),
            HourlyForecast(time=_utc(16), pressure_levels=_om_levels()),
            HourlyForecast(time=_utc(17), pressure_levels=_sounding_levels(20.0)),
        ]

        filled = _interp_levels_hourly(hours)

        assert filled == 1
        gap = hours[1]
        assert len(gap.pressure_levels) == 40  # rebuilt on the HRRR level set
        lev850 = next(pl for pl in gap.pressure_levels if pl.pressure_hpa == 850)
        # Midpoint of 2.5 °C and 12.5 °C.
        assert lev850.temperature_c == pytest.approx(7.5)
        # Dewpoint is DERIVED from the interpolated (T, RH) via Magnus, not
        # linearly interpolated (fill.py's sounding-interp invariant).
        assert lev850.dewpoint_c == pytest.approx(magnus_dewpoint(7.5, 60.0))
        assert lev850.geopotential_height_m == pytest.approx(1500.0)
        assert lev850.cloud_liquid_water_kg_kg == pytest.approx(1.15e-5)
        assert lev850.ice_mixing_ratio_kg_kg == pytest.approx(2.0e-5)
        assert lev850.vertical_velocity_pa_s == pytest.approx(-0.1)
        # Anchors are untouched.
        assert hours[0].pressure_levels[0].temperature_c == 10.0
        assert hours[2].pressure_levels[0].temperature_c == 20.0
