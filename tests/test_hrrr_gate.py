"""Tests for the HRRR gate on the gfs slot (#457, Task 4).

The gate at the top of ``_enrich_gfs_inner`` upgrades the gfs slot to HRRR
when the kill switch is off, the whole route fits the CONUS Lambert grid, and
a covering run exists. A TOTAL HRRR failure (no idx at all / zero decoded
hours) falls through to the plain-GFS path — never a half-HRRR pack. The
``grib_sources["gfs"]`` key records which source actually served the slot, and
the GFS averaged-window machinery (``gfs_init_dt``) must never run on
HRRR-sourced hours.

All network/decode boundaries are mocked — no live S3, no cfgrib.
"""

from __future__ import annotations

import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import weatherbrief.fetch.grib as grib_mod
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

# CONUS (KDEN) vs outside (Brest, France) — the domain mock keys off nothing,
# but the fixtures keep the intent readable.
CONUS_POINT = (39.86, -104.67)


def _rp(lat: float, lon: float) -> RoutePoint:
    return RoutePoint(lat=lat, lon=lon, distance_from_origin_nm=0.0)


def _utc(h: int) -> datetime:
    return datetime(2026, 7, 20, h, 0, tzinfo=timezone.utc)


def _gfs_cross_section(hours: list[int]) -> tuple[RouteCrossSection, WaypointForecast]:
    """One-point GFS cross-section with an 850 hPa level per hourly."""
    hourly = [
        HourlyForecast(
            time=_utc(h),
            pressure_levels=[PressureLevelData(pressure_hpa=850)],
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


# Decoded HRRR payloads (what the decode workers return over the pool).

_HRRR_DIAG_RAW = {
    "low_cover_pct": 40.0,
    "ceiling_m": 1500.0,
    "visibility_m": 8000.0,
    "gust_ms": 10.0,
    "sfc_cape_jkg": 500.0,
    "sfc_cin_jkg": -25.0,
}
_HRRR_CLMR_POINTS = (
    [{850: {"cloud_liquid_water_kg_kg": 0.001, "ice_mixing_ratio_kg_kg": 0.0002}}],
    [True],
)


def _fake_hrrr_dispatch(worker_name, path, lats, lons, **kw):
    if worker_name == "decode_hrrr_diag":
        return [dict(_HRRR_DIAG_RAW)]
    if worker_name == "decode_hrrr_pressure":
        return ([dict(d) for d in _HRRR_CLMR_POINTS[0]], list(_HRRR_CLMR_POINTS[1]))
    raise AssertionError(f"unexpected decode worker {worker_name}")


def _empty_hrrr_dispatch(worker_name, path, lats, lons, **kw):
    """Every fhour decodes to nothing — the total-failure trigger."""
    if worker_name == "decode_hrrr_diag":
        return [{}]
    if worker_name == "decode_hrrr_pressure":
        return ([{}], [False])
    raise AssertionError(f"unexpected decode worker {worker_name}")


def _patch_common(monkeypatch):
    """Shared mocks: warm cache (no downloads), call-order tracker."""
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


def _run_enrich_gfs_inner(cs, route_points, tmp_path, monkeypatch):
    monkeypatch.delenv("WB_HRRR_ENABLED", raising=False)
    return grib_mod._enrich_gfs_inner(
        [cs], [], route_points, DEP,
        data_dir=tmp_path, flight_duration_hours=2.0,
    )


class TestHrrrGate:
    """Gate selection at the top of _enrich_gfs_inner."""

    def test_picks_hrrr_when_in_domain_and_run_covers(self, monkeypatch, tmp_path):
        cs, wf = _gfs_cross_section([15, 16, 17])
        calls = _patch_common(monkeypatch)
        monkeypatch.setattr(grib_mod, "route_in_hrrr_domain", lambda rps: True)
        monkeypatch.setattr(grib_mod, "_dispatch_decode", _fake_hrrr_dispatch)

        ts, source_key = _run_enrich_gfs_inner(
            cs, [_rp(*CONUS_POINT)], tmp_path, monkeypatch,
        )

        assert (ts, source_key) == (HRRR_TS, "hrrr:noaa")
        assert calls == ["hrrr"]  # GFS run-finder never consulted

        # Diagnostics + CLMR/CIMIXR + surface extras landed on the hourlies.
        for h in wf.hourly:
            assert h.nwp_cloud_diagnostics is not None
            assert h.pressure_levels[0].cloud_liquid_water_kg_kg == 0.001
            assert h.pressure_levels[0].ice_mixing_ratio_kg_kg == 0.0002
            assert h.visibility_m == 8000.0
            assert h.wind_gusts_10m_kt == pytest.approx(19.4384)
            assert h.convective_inhibition_jkg == -25.0

    def test_surface_extras_never_clobber_open_meteo_values(
        self, monkeypatch, tmp_path,
    ):
        """HRRR extras gap-fill only: the gfs slot's surface base belongs to
        Open-Meteo's gfs_seamless feed, so a populated field is left alone."""
        cs, wf = _gfs_cross_section([15])
        wf.hourly[0].cape_jkg = 123.0  # Open-Meteo already delivered CAPE
        _patch_common(monkeypatch)
        monkeypatch.setattr(grib_mod, "route_in_hrrr_domain", lambda rps: True)
        monkeypatch.setattr(grib_mod, "_dispatch_decode", _fake_hrrr_dispatch)

        ts, source_key = _run_enrich_gfs_inner(
            cs, [_rp(*CONUS_POINT)], tmp_path, monkeypatch,
        )

        assert (ts, source_key) == (HRRR_TS, "hrrr:noaa")
        assert wf.hourly[0].cape_jkg == 123.0        # not clobbered by 500.0
        assert wf.hourly[0].visibility_m == 8000.0   # None → gap-filled

    def test_picks_gfs_when_outside_domain(self, monkeypatch, tmp_path):
        cs, _wf = _gfs_cross_section([15, 16, 17])
        calls = _patch_common(monkeypatch)
        monkeypatch.setattr(grib_mod, "route_in_hrrr_domain", lambda rps: False)

        ts, source_key = _run_enrich_gfs_inner(
            cs, [_rp(48.4, -4.5)], tmp_path, monkeypatch,
        )

        assert (ts, source_key) == (GFS_TS, "gfs:noaa")
        assert calls == ["gfs"]  # HRRR run-finder never consulted

    def test_picks_gfs_when_no_covering_run(self, monkeypatch, tmp_path):
        cs, _wf = _gfs_cross_section([15, 16, 17])
        calls = _patch_common(monkeypatch)
        monkeypatch.setattr(grib_mod, "route_in_hrrr_domain", lambda rps: True)
        monkeypatch.setattr(
            grib_mod, "find_latest_hrrr_run",
            lambda *a, **kw: calls.append("hrrr") or None,
        )

        ts, source_key = _run_enrich_gfs_inner(
            cs, [_rp(*CONUS_POINT)], tmp_path, monkeypatch,
        )

        assert (ts, source_key) == (GFS_TS, "gfs:noaa")
        assert calls == ["hrrr", "gfs"]

    def test_kill_switch_disables_gate(self, monkeypatch, tmp_path):
        cs, _wf = _gfs_cross_section([15, 16, 17])
        calls = _patch_common(monkeypatch)
        monkeypatch.setattr(grib_mod, "route_in_hrrr_domain", lambda rps: True)
        monkeypatch.setenv("WB_HRRR_ENABLED", "0")

        ts, source_key = grib_mod._enrich_gfs_inner(
            [cs], [], [_rp(*CONUS_POINT)], DEP,
            data_dir=tmp_path, flight_duration_hours=2.0,
        )

        assert (ts, source_key) == (GFS_TS, "gfs:noaa")
        assert calls == ["gfs"]  # gate short-circuited before the HRRR finder


class TestHrrrTotalFailureFallback:
    """Total HRRR failure → the plain-GFS path runs afterwards, whole."""

    def test_idx_fetch_failure_falls_back_to_gfs(self, monkeypatch, tmp_path):
        cs, _wf = _gfs_cross_section([15, 16, 17])
        calls = _patch_common(monkeypatch)
        monkeypatch.setattr(grib_mod, "route_in_hrrr_domain", lambda rps: True)

        def _idx_raises_for_hrrr(init_date, init_hour, fhour, session=None,
                                 url_builder=None):
            if url_builder is not None:  # HRRR idx URL builder → boom
                raise RuntimeError("HRRR idx boom")
            return "gfs idx text"

        monkeypatch.setattr(grib_mod, "fetch_idx", _idx_raises_for_hrrr)

        ts, source_key = _run_enrich_gfs_inner(
            cs, [_rp(*CONUS_POINT)], tmp_path, monkeypatch,
        )

        assert (ts, source_key) == (GFS_TS, "gfs:noaa")
        assert calls == ["hrrr", "gfs"]  # GFS run-finder called AFTER the HRRR attempt

    def test_zero_decoded_hours_falls_back_to_gfs(self, monkeypatch, tmp_path):
        cs, _wf = _gfs_cross_section([15, 16, 17])
        calls = _patch_common(monkeypatch)
        monkeypatch.setattr(grib_mod, "route_in_hrrr_domain", lambda rps: True)
        monkeypatch.setattr(grib_mod, "_dispatch_decode", _empty_hrrr_dispatch)

        ts, source_key = _run_enrich_gfs_inner(
            cs, [_rp(*CONUS_POINT)], tmp_path, monkeypatch,
        )

        assert (ts, source_key) == (GFS_TS, "gfs:noaa")
        assert calls == ["hrrr", "gfs"]


class TestGfsSlotSourceWiring:
    """enrich_forecasts-level wiring: grib_sources + gfs_init_dt gating."""

    def _patch_outer(self, monkeypatch):
        """Icon/ECMWF out of the way; fill.py calls captured, not executed."""
        monkeypatch.setattr(
            grib_mod, "_prepare_icon_eu",
            lambda *a, **kw: (None, "out_of_domain"),
        )
        monkeypatch.setattr(grib_mod, "_enrich_ecmwf", lambda *a, **kw: None)
        captured: dict[str, object] = {}

        def _fake_propagate(cross_sections, all_forecasts, gfs_init=None):
            captured["gfs_init"] = gfs_init

        monkeypatch.setattr(
            "weatherbrief.fetch.grib.fill.propagate_all", _fake_propagate,
        )
        monkeypatch.setattr(
            "weatherbrief.fetch.grib.fill.apply_gfs_rh_condensate_gate",
            lambda *a: None,
        )
        return captured

    def test_grib_sources_records_hrrr_on_gate_hit(self, monkeypatch, tmp_path):
        cs, _wf = _gfs_cross_section([15, 16, 17])
        _patch_common(monkeypatch)
        captured = self._patch_outer(monkeypatch)
        monkeypatch.setattr(grib_mod, "route_in_hrrr_domain", lambda rps: True)
        monkeypatch.setattr(grib_mod, "_dispatch_decode", _fake_hrrr_dispatch)

        init_times, _skips, sources = grib_mod.enrich_forecasts(
            [cs], [], [_rp(*CONUS_POINT)], DEP,
            data_dir=tmp_path, flight_duration_hours=2.0,
        )

        assert init_times["gfs"] == HRRR_TS
        assert sources["gfs"] == "hrrr:noaa"
        # All HRRR fields are instantaneous — the GFS averaged-window
        # machinery (window-midpoint fill) must never see HRRR-sourced hours.
        assert captured["gfs_init"] is None

    def test_grib_sources_records_gfs_on_plain_path(self, monkeypatch, tmp_path):
        cs, _wf = _gfs_cross_section([15, 16, 17])
        _patch_common(monkeypatch)
        captured = self._patch_outer(monkeypatch)
        monkeypatch.setattr(grib_mod, "route_in_hrrr_domain", lambda rps: False)

        init_times, _skips, sources = grib_mod.enrich_forecasts(
            [cs], [], [_rp(48.4, -4.5)], DEP,
            data_dir=tmp_path, flight_duration_hours=2.0,
        )

        assert init_times["gfs"] == GFS_TS
        assert sources["gfs"] == "gfs:noaa"
        assert captured["gfs_init"] == datetime(2026, 7, 20, 6, tzinfo=timezone.utc)


class TestHrrrWiring:
    """Cache TTL, freshness registry, readiness dispatch, declared deps."""

    def test_hrrr_cache_ttl(self):
        from weatherbrief.fetch.grib.cache import MODEL_TTL_SECONDS
        assert MODEL_TTL_SECONDS["hrrr"] == 6 * 3600

    def test_registry_entry_shape(self):
        from weatherbrief.fetch.freshness.registry import SOURCE_REGISTRY
        cfg = SOURCE_REGISTRY["hrrr:noaa"]
        assert cfg.cycles == (0, 6, 12, 18)
        assert cfg.horizon == timedelta(hours=48)
        assert cfg.delivery_offset == timedelta(hours=1)
        assert cfg.model_label == "HRRR"
        assert cfg.provider_label == "NOAA"
        assert cfg.role == "primary-sounding"
        assert cfg.resolution == "3 km"
        assert "CONUS" in cfg.coverage
        assert cfg.pressure_levels == 40
        assert cfg.readiness_check == "hrrr_noaa"
        assert "gfs" in cfg.description

    def test_readiness_dispatch_registered(self):
        from weatherbrief.fetch.freshness.sources import _DISPATCH
        assert "hrrr_noaa" in _DISPATCH

    def test_pyproject_declares_pyproj(self):
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with pyproject.open("rb") as f:
            deps = tomllib.load(f)["project"]["dependencies"]
        assert any(d.startswith("pyproj>=") for d in deps)
