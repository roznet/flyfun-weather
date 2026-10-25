"""Tests for the flight-scoped GRIB pre-warm (#929)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from weatherbrief.fetch.grib import flight_warm
from weatherbrief.fetch.grib.cache import (
    cache_key,
    cache_write_meter,
    put_cached,
    put_cached_from_chunks,
)
from weatherbrief.fetch.grib.flight_warm import (
    _Flight,
    _Outcome,
    warm_flights,
    warm_limits,
)

NOW = datetime(2026, 10, 25, 6, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WB_FLIGHT_WARM_MIN_FREE_GB", "0")
    for key in ("ICON_D2", "ICON_EU", "GFS", "HRRR"):
        monkeypatch.delenv(f"WB_FLIGHT_WARM_MAX_GB_{key}", raising=False)
        monkeypatch.delenv(f"WB_FLIGHT_WARM_HORIZON_HOURS_{key}", raising=False)
    monkeypatch.delenv("WB_FLIGHT_WARM_MAX_MINUTES", raising=False)
    monkeypatch.delenv("WB_FLIGHT_WARM_MAX_FLIGHTS", raising=False)


class _Row:
    def __init__(self, fid: str, hours_out: float = 1.0):
        self.id = fid
        self.departure_time = NOW + timedelta(hours=hours_out)


def _flight(row, _db_path):
    return _Flight(
        id=row.id, departure_time=row.departure_time, duration_hours=2.0,
        route_points=["pt"],
    )


def _run(source, rows, unit, **kw):
    """warm_flights with the DB, route building and the source unit faked."""
    with patch.object(flight_warm, "_upcoming_rows", return_value=rows) as q, \
            patch.object(flight_warm, "_to_flight", side_effect=_flight), \
            patch.object(flight_warm, "_warm_unit", return_value=unit):
        stats = warm_flights(
            source, NOW, "/db", now=NOW,
            should_defer=kw.pop("should_defer", lambda: False), **kw,
        )
    return stats, q


def _writing_unit(tmp_path: Path, nbytes: int, seen: list):
    """A unit that downloads ``nbytes`` (a cache write) per flight, one hour each."""
    def unit(flight, data_dir, stop):
        seen.append(flight.id)
        if stop():
            return _Outcome("stopped")
        put_cached(tmp_path / "run", f"{flight.id}.grib2", b"x" * nbytes)
        return _Outcome("warmed", hours={("r", int(flight.id[-1]))})
    return unit


# --- Cache write meter ---------------------------------------------------------


class TestCacheWriteMeter:
    def test_counts_writes_inside_the_context_only(self, tmp_path):
        put_cached(tmp_path, "before.grib2", b"abc")
        with cache_write_meter() as meter:
            put_cached(tmp_path, "a.grib2", b"x" * 10)
            put_cached_from_chunks(tmp_path, "b.grib2", iter([b"y" * 5, b"z" * 7]))
            # An empty stream writes no entry and costs nothing.
            put_cached_from_chunks(tmp_path, "c.grib2", iter([]))
        put_cached(tmp_path, "after.grib2", b"abc")
        assert meter.bytes_written == 22
        assert meter.files_written == 2

    def test_worker_threads_submitted_with_context_share_the_meter(self, tmp_path):
        from concurrent.futures import ThreadPoolExecutor

        from weatherbrief.fetch.grib import _submit_with_context

        with cache_write_meter() as meter:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futs = [
                    _submit_with_context(pool, put_cached, tmp_path, f"{i}.grib2", b"x" * 4)
                    for i in range(3)
                ]
                for f in futs:
                    f.result()
        assert meter.bytes_written == 12


# --- Limits --------------------------------------------------------------------


class TestWarmLimits:
    def test_defaults(self):
        gfs = warm_limits("gfs")
        assert gfs.max_bytes == 6_000_000_000
        assert gfs.horizon_hours == 72
        assert gfs.max_seconds == 20 * 60
        assert gfs.max_flights == 40
        assert warm_limits("icon-eu").max_bytes == 6_000_000_000
        assert warm_limits("hrrr").max_bytes == 2_000_000_000
        # HRRR is off by default (horizon 0): its warmed run is mostly stale.
        assert warm_limits("hrrr").horizon_hours == 0
        # D2 ran uncapped before #929 and stays so by default.
        assert warm_limits("icon-d2").max_bytes is None
        assert warm_limits("icon-d2").horizon_hours == 48

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("WB_FLIGHT_WARM_MAX_GB_GFS", "1.5")
        monkeypatch.setenv("WB_FLIGHT_WARM_HORIZON_HOURS_ICON_EU", "48")
        monkeypatch.setenv("WB_FLIGHT_WARM_MAX_GB_HRRR", "0")
        monkeypatch.setenv("WB_FLIGHT_WARM_MAX_FLIGHTS", "5")
        assert warm_limits("gfs").max_bytes == 1_500_000_000
        assert warm_limits("icon-eu").horizon_hours == 48
        assert warm_limits("hrrr").max_bytes is None  # 0 = uncapped
        assert warm_limits("gfs").max_flights == 5


# --- The pass ------------------------------------------------------------------


class TestWarmFlights:
    def test_empty_db_path_is_noop(self):
        stats = warm_flights("gfs", NOW, "")
        assert stats["flights_considered"] == 0

    def test_zero_horizon_turns_the_source_off(self, tmp_path):
        seen: list = []
        stats, query = _run("hrrr", [_Row("f1")], _writing_unit(tmp_path, 1, seen))
        assert stats["disabled"] == 1
        assert seen == []
        query.assert_not_called()

    def test_hrrr_can_be_turned_back_on(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WB_FLIGHT_WARM_HORIZON_HOURS_HRRR", "48")
        seen: list = []
        stats, _ = _run("hrrr", [_Row("f1")], _writing_unit(tmp_path, 1, seen))
        assert seen == ["f1"]
        assert stats["disabled"] == 0

    def test_horizon_is_the_sources(self, monkeypatch):
        monkeypatch.setenv("WB_FLIGHT_WARM_HORIZON_HOURS_GFS", "30")
        _stats, query = _run("gfs", [], lambda *a: _Outcome("warmed"))
        reference, horizon_end = query.call_args.args
        assert horizon_end - reference == timedelta(hours=30)

    def test_flight_cap_cuts_the_tail_and_names_it(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WB_FLIGHT_WARM_MAX_FLIGHTS", "2")
        seen: list = []
        rows = [_Row(f"f{i}", i) for i in range(1, 5)]
        stats, _ = _run("gfs", rows, _writing_unit(tmp_path, 1, seen))
        assert seen == ["f1", "f2"]  # soonest first, the rest cut
        assert stats["flights_considered"] == 4
        assert stats["flights_warmed"] == 2
        assert stats["flights_cut"] == 2
        assert stats["cut_by_flights"] == 2
        assert stats["cut_ids"] == ["f3", "f4"]

    def test_flight_cap_counts_only_this_sources_flights(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WB_FLIGHT_WARM_MAX_FLIGHTS", "1")
        seen: list = []
        writer = _writing_unit(tmp_path, 1, seen)

        def unit(flight, data_dir, stop):
            if flight.id in ("f1", "f2"):
                return _Outcome("skipped", reason="served_by_icon-d2")
            return writer(flight, data_dir, stop)

        rows = [_Row(f"f{i}", i) for i in range(1, 5)]
        stats, _ = _run("icon-eu", rows, unit)
        assert seen == ["f3"]
        assert stats["flights_skipped"] == 2
        assert stats["flights_warmed"] == 1
        assert stats["cut_ids"] == ["f4"]

    def test_cut_summary_is_a_warning_with_a_token(self, monkeypatch, tmp_path, caplog):
        import logging

        monkeypatch.setenv("WB_FLIGHT_WARM_MAX_FLIGHTS", "1")
        caplog.set_level(logging.INFO, logger="weatherbrief.fetch.grib.flight_warm")
        _run("gfs", [_Row("f1"), _Row("f2", 2)], _writing_unit(tmp_path, 1, []))
        rec = next(r for r in caplog.records if "considered" in r.getMessage())
        assert rec.levelno == logging.WARNING
        assert rec.getMessage().startswith("FLIGHT_WARM_CUT Flight warm gfs")

    def test_byte_budget_cuts_later_flights(self, monkeypatch, tmp_path):
        # 2.5 kB budget, 1 kB per flight: f1, f2 warm; f3 starts, warms (the
        # budget is checked ahead of its download), f4 is cut.
        monkeypatch.setenv("WB_FLIGHT_WARM_MAX_GB_GFS", str(2500 / 1e9))
        seen: list = []
        rows = [_Row(f"f{i}", i) for i in range(1, 5)]
        stats, _ = _run("gfs", rows, _writing_unit(tmp_path, 1000, seen))
        assert seen == ["f1", "f2", "f3"]
        assert stats["flights_warmed"] == 3
        assert stats["cut_by_bytes"] == 1
        assert stats["cut_ids"] == ["f4"]
        assert stats["bytes_downloaded"] == 3000
        assert stats["deferred"] == 0
        assert stats["distinct_hours"] == 3

    def test_byte_budget_stops_mid_flight(self, monkeypatch, tmp_path):
        """The budget rides in ``stop``: a unit that hits it mid-flight is cut."""
        monkeypatch.setenv("WB_FLIGHT_WARM_MAX_GB_GFS", str(1500 / 1e9))

        def unit(flight, data_dir, stop):
            for i in range(3):
                if stop():
                    return _Outcome("stopped")
                put_cached(tmp_path, f"{flight.id}-{i}.grib2", b"x" * 1000)
            return _Outcome("warmed")

        stats, _ = _run("gfs", [_Row("f1"), _Row("f2", 2)], unit)
        assert stats["flights_warmed"] == 0
        assert stats["cut_by_bytes"] == 2
        assert stats["cut_ids"] == ["f1", "f2"]
        assert stats["deferred"] == 0  # finished: the caller records the run

    def test_carried_bytes_count_against_the_budget(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WB_FLIGHT_WARM_MAX_GB_GFS", str(2000 / 1e9))
        seen: list = []
        stats, _ = _run(
            "gfs", [_Row("f1"), _Row("f2", 2)], _writing_unit(tmp_path, 1000, seen),
            carried_bytes=1500,
        )
        assert seen == ["f1"]
        assert stats["bytes_downloaded"] == 2500
        assert stats["cut_ids"] == ["f2"]

    def test_time_limit_cuts(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WB_FLIGHT_WARM_MAX_MINUTES", "0")
        seen: list = []
        stats, _ = _run("gfs", [_Row("f1"), _Row("f2", 2)], _writing_unit(tmp_path, 1, seen))
        assert seen == []
        assert stats["cut_by_time"] == 2

    def test_deferral_is_not_a_cut(self, tmp_path):
        seen: list = []
        calls = {"n": 0}

        def gate():
            calls["n"] += 1
            return calls["n"] > 1  # the second flight meets a refresh

        stats, _ = _run(
            "gfs", [_Row("f1"), _Row("f2", 2), _Row("f3", 3)],
            _writing_unit(tmp_path, 1, seen), should_defer=gate,
        )
        assert stats["deferred"] == 1
        assert stats["flights_warmed"] == 1
        assert stats["flights_cut"] == 0
        assert seen == ["f1", "f2"]

    def test_low_disk_skips_the_pass(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WB_FLIGHT_WARM_MIN_FREE_GB", "20")
        seen: list = []
        with patch.object(flight_warm, "free_disk_bytes", return_value=5 * 10**9):
            stats, query = _run("gfs", [_Row("f1")], _writing_unit(tmp_path, 1, seen))
        assert stats["low_disk"] == 1
        assert seen == []
        query.assert_not_called()

    def test_low_disk_warns_once_per_run(self, monkeypatch, tmp_path, caplog):
        import logging

        monkeypatch.setenv("WB_FLIGHT_WARM_MIN_FREE_GB", "20")
        monkeypatch.setattr(flight_warm, "_low_disk_warned", {})
        caplog.set_level(logging.DEBUG, logger="weatherbrief.fetch.grib.flight_warm")
        with patch.object(flight_warm, "free_disk_bytes", return_value=5 * 10**9):
            for _ in range(3):  # retried every tick
                _run("gfs", [_Row("f1")], _writing_unit(tmp_path, 1, []))
        levels = [r.levelno for r in caplog.records if "GB free" in r.getMessage()]
        assert levels == [logging.WARNING, logging.DEBUG, logging.DEBUG]

    def test_skipped_and_failed_flights_do_not_stop_the_pass(self, tmp_path):
        def unit(flight, data_dir, stop):
            if flight.id == "f1":
                return _Outcome("skipped", reason="served_by_icon-d2")
            if flight.id == "f2":
                raise RuntimeError("boom")
            if flight.id == "f3":
                return _Outcome("failed", hours={("r", 1)}, reason="1/2 steps cold")
            return _Outcome("warmed", hours={("r", 1)})

        rows = [_Row("f1"), _Row("f2", 2), _Row("f3", 3), _Row("f4", 4)]
        stats, _ = _run("icon-eu", rows, unit)
        assert stats["flights_skipped"] == 1
        assert stats["flights_failed"] == 2  # raised, and downloads left cold
        assert stats["flights_warmed"] == 1

    def test_stats_log_line(self, tmp_path, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="weatherbrief.fetch.grib.flight_warm")
        _run("gfs", [_Row("f1")], _writing_unit(tmp_path, 10, []))
        line = next(r.getMessage() for r in caplog.records if "considered" in r.getMessage())
        assert line.startswith("Flight warm gfs (run 20261025_06z): considered 1, warmed 1")
        assert "1 distinct hours" in line
        assert "budget 6 GB" in line


# --- ICON unit -------------------------------------------------------------------


class _Ctx:
    def __init__(self, slug):
        self.variant = type("V", (), {"slug": slug})()
        self.init_date, self.init_hour = "20261025", 3
        self.forecast_hours = [5, 6, 7]


class TestWarmIcon:
    def _flight(self):
        return _Flight("f1", NOW, 2.0, ["pt"])

    def test_eu_pass_warms_eu_and_leaves_d2_to_its_pass(self, tmp_path):
        from weatherbrief.fetch.grib.flight_warm import _warm_icon

        fetched: list = []
        with patch("weatherbrief.fetch.grib._prepare_icon_eu",
                   return_value=(_Ctx("icon-d2"), None)), \
                patch("weatherbrief.fetch.grib._prefetch_icon_eu_data",
                      side_effect=lambda ctx, **kw: fetched.append(ctx) or True):
            out = _warm_icon("icon-eu", self._flight(), tmp_path, lambda: False)
        assert out.status == "skipped" and out.reason == "served_by_icon-d2"
        assert fetched == []

        with patch("weatherbrief.fetch.grib._prepare_icon_eu",
                   return_value=(_Ctx("icon-eu"), None)), \
                patch("weatherbrief.fetch.grib._prefetch_icon_eu_data",
                      side_effect=lambda ctx, **kw: fetched.append(kw) or True):
            out = _warm_icon("icon-eu", self._flight(), tmp_path, lambda: False)
        assert out.status == "warmed"
        assert out.hours == {("20261025_03z", h) for h in (5, 6, 7)}
        assert fetched[0]["outer_workers"] == 1
        assert callable(fetched[0]["abort_if"])

    def test_prefetch_abort_is_a_stop(self, tmp_path):
        from weatherbrief.fetch.grib.flight_warm import _warm_icon

        with patch("weatherbrief.fetch.grib._prepare_icon_eu",
                   return_value=(_Ctx("icon-d2"), None)), \
                patch("weatherbrief.fetch.grib._prefetch_icon_eu_data",
                      return_value=False):
            out = _warm_icon("icon-d2", self._flight(), tmp_path, lambda: True)
        assert out.status == "stopped"


# --- GFS unit ----------------------------------------------------------------------


class TestWarmGfs:
    def _patches(self, *, skip=None, cached=(), hours=(12, 13), failing=()):
        """Fake the GFS helpers; a helper "downloads" by marking its key cached,
        except for the kinds in ``failing``, which log-and-return like a NOAA
        outage."""
        import contextlib

        from weatherbrief.fetch.grib import (
            GFS_SURFACE_CACHE_KEY,
            _gfs_sounding_cache_key,
            gfs_sounding_levels,
        )

        calls: dict[str, list] = {"surface": [], "sounding": [], "diag": [], "idx": []}
        cached = set(cached)
        key_of = {
            "surface": lambda f: cache_key(f, GFS_SURFACE_CACHE_KEY),
            "sounding": lambda f: _gfs_sounding_cache_key(f, gfs_sounding_levels()),
            "diag": lambda f: cache_key(f, "CLOUD_DIAG"),
        }

        def _ensure(kind):
            def fn(init_date, init_hour, fhour, *a, **kw):
                calls[kind].append(fhour)
                if kind in failing:
                    return False
                cached.add(key_of[kind](fhour))
                return True
            return fn

        @contextlib.contextmanager
        def cm():
            with patch("weatherbrief.fetch.grib.primary.grib_primary_skip_reason",
                       return_value=skip), \
                    patch("weatherbrief.fetch.grib.grib_fetch.find_latest_run",
                          return_value=("20261025", 0)), \
                    patch("weatherbrief.fetch.grib.grib_fetch.compute_flight_window_hours",
                          return_value=list(hours)), \
                    patch("weatherbrief.fetch.grib.grib_fetch.fetch_idx",
                          side_effect=lambda d, h, f, session=None: calls["idx"].append(f) or "idx"), \
                    patch("weatherbrief.fetch.grib._ensure_gfs_surface", side_effect=_ensure("surface")), \
                    patch("weatherbrief.fetch.grib._ensure_gfs_sounding", side_effect=_ensure("sounding")), \
                    patch("weatherbrief.fetch.grib._ensure_gfs_cloud_diag", side_effect=_ensure("diag")), \
                    patch("weatherbrief.fetch.grib.cache.is_cached",
                          side_effect=lambda run_dir, ck: ck in cached):
                yield calls
        return cm()

    def test_window_hours_plus_lead_surface(self, tmp_path):
        from weatherbrief.fetch.grib.flight_warm import _warm_gfs

        with self._patches() as calls:
            out = _warm_gfs(_Flight("f1", NOW, 1.0, ["pt"]), tmp_path, lambda: False)
        assert out.status == "warmed"
        assert calls["surface"] == [11, 12, 13]  # the step before the window too
        assert calls["sounding"] == [12, 13]
        assert calls["diag"] == [12, 13]
        assert out.hours == {("20261025_00z", 12), ("20261025_00z", 13)}

    def test_cached_hours_fast_forward_past_an_active_refresh(self, tmp_path):
        from weatherbrief.fetch.grib import GFS_SURFACE_CACHE_KEY, _gfs_sounding_cache_key, gfs_sounding_levels
        from weatherbrief.fetch.grib.flight_warm import _warm_gfs

        levels = gfs_sounding_levels()
        cached = {cache_key(f, GFS_SURFACE_CACHE_KEY) for f in (11, 12, 13)}
        cached |= {_gfs_sounding_cache_key(f, levels) for f in (12, 13)}
        cached |= {cache_key(f, "CLOUD_DIAG") for f in (12, 13)}
        with self._patches(cached=cached) as calls:
            out = _warm_gfs(_Flight("f1", NOW, 1.0, ["pt"]), tmp_path, lambda: True)
        assert out.status == "warmed"
        assert calls["idx"] == []

    def test_failed_downloads_report_failed_not_warmed(self, tmp_path):
        from weatherbrief.fetch.grib.flight_warm import _warm_gfs

        with self._patches(failing=("sounding",)):
            out = _warm_gfs(_Flight("f1", NOW, 1.0, ["pt"]), tmp_path, lambda: False)
        assert out.status == "failed"
        assert out.reason == "2/3 steps cold"

    def test_stop_before_a_download(self, tmp_path):
        from weatherbrief.fetch.grib.flight_warm import _warm_gfs

        with self._patches() as calls:
            out = _warm_gfs(_Flight("f1", NOW, 1.0, ["pt"]), tmp_path, lambda: True)
        assert out.status == "stopped"
        assert calls["surface"] == []
        assert out.hours == set()  # nothing landed, nothing counted

    @pytest.mark.parametrize("reason", ["hrrr_route", "disabled", "out_of_range"])
    def test_not_grib_primary_is_skipped(self, tmp_path, reason):
        from weatherbrief.fetch.grib.flight_warm import _warm_gfs

        with self._patches(skip=reason) as calls:
            out = _warm_gfs(_Flight("f1", NOW, 1.0, ["pt"]), tmp_path, lambda: False)
        assert out.status == "skipped"
        assert out.reason == f"gfs_{reason}"
        assert calls["idx"] == []


# --- HRRR unit -----------------------------------------------------------------------


class TestWarmHrrr:
    def test_route_hrrr_does_not_take_is_skipped(self, tmp_path):
        from weatherbrief.fetch.grib.flight_warm import _warm_hrrr

        with patch("weatherbrief.fetch.grib.primary._hrrr_takes_route", return_value=False):
            out = _warm_hrrr(_Flight("f1", NOW, 1.0, ["pt"]), tmp_path, lambda: False)
        assert out.status == "skipped"

    def test_downloads_sounding_and_diag_per_hour(self, tmp_path):
        from weatherbrief.fetch.grib.flight_warm import _warm_hrrr

        got: list = []

        def _write(kind, fhour, run_dir):
            got.append((kind, fhour))
            label = "HRRR_SOUNDING" if kind == "snd" else "HRRR_CLOUD_DIAG"
            put_cached(run_dir, cache_key(fhour, label), b"x")

        with patch("weatherbrief.fetch.grib.primary._hrrr_takes_route", return_value=True), \
                patch("weatherbrief.fetch.grib.hrrr_fetch.find_latest_hrrr_run",
                      return_value=("20261025", 6)), \
                patch("weatherbrief.fetch.grib.hrrr_fetch.compute_hrrr_flight_window_hours",
                      return_value=[2, 3]), \
                patch("weatherbrief.fetch.grib.hrrr_fetch.fetch_hrrr_idx", return_value="idx"), \
                patch("weatherbrief.fetch.grib._download_hrrr_sounding",
                      side_effect=lambda d, h, f, idx, run_dir, **kw: _write("snd", f, run_dir)), \
                patch("weatherbrief.fetch.grib._download_hrrr_cloud_diag",
                      side_effect=lambda d, h, f, idx, run_dir, **kw: _write("diag", f, run_dir)):
            out = _warm_hrrr(_Flight("f1", NOW, 1.0, ["pt"]), tmp_path, lambda: False)
        assert out.status == "warmed"
        assert got == [("snd", 2), ("diag", 2), ("snd", 3), ("diag", 3)]

    def test_failed_sounding_reports_failed(self, tmp_path):
        from weatherbrief.fetch.grib.flight_warm import _warm_hrrr

        def boom(*a, **kw):
            raise RuntimeError("S3 down")

        with patch("weatherbrief.fetch.grib.primary._hrrr_takes_route", return_value=True), \
                patch("weatherbrief.fetch.grib.hrrr_fetch.find_latest_hrrr_run",
                      return_value=("20261025", 6)), \
                patch("weatherbrief.fetch.grib.hrrr_fetch.compute_hrrr_flight_window_hours",
                      return_value=[2]), \
                patch("weatherbrief.fetch.grib.hrrr_fetch.fetch_hrrr_idx", return_value="idx"), \
                patch("weatherbrief.fetch.grib._download_hrrr_sounding", side_effect=boom):
            out = _warm_hrrr(_Flight("f1", NOW, 1.0, ["pt"]), tmp_path, lambda: False)
        assert out.status == "failed"
