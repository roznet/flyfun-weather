"""Tests for the GRIB pre-cache module (issue #126).

Covers ``airport_profile_forecast_hours`` (pure helper) and the precache
functions' integration with the cache + freshness markers (mocked HTTP).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from weatherbrief.fetch.grib.precache import (
    PRECACHE_DAYS_AHEAD,
    PRECACHE_FORECAST_HOURS_PER_DAY,
    PRECACHE_MAX_FORECAST_HOUR,
    airport_profile_forecast_hours,
    precache_gfs_run,
    precache_icon_eu_run,
)


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# airport_profile_forecast_hours
# ---------------------------------------------------------------------------


class TestAirportProfileForecastHours:

    def test_00z_init_covers_64_hours(self):
        """00 Z init: every 06–21 Z hour for 4 days = 64 unique offsets."""
        hours = airport_profile_forecast_hours(_utc(2026, 5, 8, 0))
        # D-0: 6..21 (16), D-1: 30..45 (16), D-2: 54..69 (16), D-3: 78..93 (16)
        expected = (
            list(range(6, 22))
            + list(range(30, 46))
            + list(range(54, 70))
            + list(range(78, 94))
        )
        assert hours == expected
        assert len(hours) == 64

    def test_12z_init_skips_past_hours_on_day_zero(self):
        """12 Z init: D-0 only covers 12-21 Z (10 hours), then full days."""
        hours = airport_profile_forecast_hours(_utc(2026, 5, 8, 12))
        # D-0: offsets 0..9 (target 12..21 Z), D-1: 18..33, D-2: 42..57, D-3: 66..81
        expected = (
            list(range(0, 10))
            + list(range(18, 34))
            + list(range(42, 58))
            + list(range(66, 82))
        )
        assert hours == expected
        assert len(hours) == 10 + 16 + 16 + 16  # 58

    def test_18z_init_only_keeps_18_21_on_day_zero(self):
        """18 Z init: D-0 only covers 18-21 Z (4 hours), then full days."""
        hours = airport_profile_forecast_hours(_utc(2026, 5, 8, 18))
        assert hours[:4] == [0, 1, 2, 3]
        assert len(hours) == 4 + 16 * 3

    def test_06z_init_full_first_day(self):
        """06 Z init: D-0 targets 06-21 Z = offsets 0..15, full 16 hours."""
        hours = airport_profile_forecast_hours(_utc(2026, 5, 8, 6))
        assert hours[:16] == list(range(0, 16))
        assert len(hours) == 64

    def test_horizon_capped_at_120h(self):
        """No offset can exceed the 120 h main-cycle horizon."""
        for init_hour in (0, 6, 12, 18):
            hours = airport_profile_forecast_hours(_utc(2026, 5, 8, init_hour))
            assert all(h <= PRECACHE_MAX_FORECAST_HOUR for h in hours)

    def test_returns_sorted_unique(self):
        hours = airport_profile_forecast_hours(_utc(2026, 5, 8, 0))
        assert hours == sorted(set(hours))

    def test_constants_consistent(self):
        """Sanity: 16 hours/day × 4 days = 64 max possible offsets."""
        assert len(PRECACHE_FORECAST_HOURS_PER_DAY) == 16
        assert PRECACHE_DAYS_AHEAD == 4


# ---------------------------------------------------------------------------
# precache_icon_eu_run / precache_gfs_run — mocked HTTP
# ---------------------------------------------------------------------------


class TestPrecacheIconEuRun:

    def test_skips_already_cached_combos(self, tmp_path: Path, monkeypatch):
        """If every (var × fhour) is already cached, no fetch is invoked."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

        from weatherbrief.fetch.grib.cache import (
            cache_dir_for_run,
            cache_key,
            put_cached,
        )
        from weatherbrief.fetch.grib.icon_eu_fetch import (
            ICON_EU_CLOUD_DIAG_CACHE_KEY,
            ICON_EU_VARIABLES,
        )

        init = _utc(2026, 5, 8, 0)
        run_dir = cache_dir_for_run(
            tmp_path, init.strftime("%Y%m%d"), init.hour, model="icon-eu",
        )

        forecast_hours = airport_profile_forecast_hours(init)
        for fhour in forecast_hours:
            for var in ICON_EU_VARIABLES:
                put_cached(
                    run_dir,
                    cache_key(fhour, f"ICON_EU_{var.upper()}"),
                    b"x",  # marker bytes; not parsed
                )
            put_cached(run_dir, cache_key(fhour, ICON_EU_CLOUD_DIAG_CACHE_KEY), b"x")

        with patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_per_variable"
        ) as mock_var, patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_single_level"
        ) as mock_single:
            stats = precache_icon_eu_run(init)

        assert mock_var.call_count == 0
        assert mock_single.call_count == 0
        assert stats["hours_fetched"] == len(forecast_hours)
        assert stats["vars_fetched"] == 0
        assert stats["bytes_downloaded"] == 0

    def test_fetches_and_caches_missing_combos(self, tmp_path: Path, monkeypatch):
        """Missing combos trigger fetch + put_cached for each (var × fhour)."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

        from weatherbrief.fetch.grib.cache import cache_dir_for_run, cache_key

        init = _utc(2026, 5, 8, 0)

        def fake_fetch_per_var(init_date, init_hour, fhour, levels, variables, session,
                               max_workers=None):
            return {variables[0]: b"GRIB" + variables[0].encode()}

        def fake_fetch_single(init_date, init_hour, fhours, session=None,
                              max_workers=None):
            return {fhours[0]: b"DIAG"}

        with patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_per_variable",
            side_effect=fake_fetch_per_var,
        ) as mock_var, patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_single_level",
            side_effect=fake_fetch_single,
        ) as mock_single:
            stats = precache_icon_eu_run(init)

        forecast_hours = airport_profile_forecast_hours(init)
        # 9 ICON-EU pressure-level vars per fhour
        assert mock_var.call_count == len(forecast_hours) * 9
        assert mock_single.call_count == len(forecast_hours)
        assert stats["hours_fetched"] == len(forecast_hours)
        assert stats["vars_fetched"] == len(forecast_hours) * 10  # 9 + 1 diag

        # Spot-check that one of the cache files exists
        run_dir = cache_dir_for_run(
            tmp_path, "20260508", 0, model="icon-eu",
        )
        sample_path = run_dir / cache_key(6, "ICON_EU_QC")
        assert sample_path.exists()


class TestPrecacheGfsRun:

    def test_skips_already_cached_combos(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

        from weatherbrief.fetch.grib.cache import (
            cache_dir_for_run,
            cache_key,
            put_cached,
        )

        init = _utc(2026, 5, 8, 12)
        run_dir = cache_dir_for_run(
            tmp_path, init.strftime("%Y%m%d"), init.hour, model="gfs",
        )

        forecast_hours = airport_profile_forecast_hours(init)
        for fhour in forecast_hours:
            put_cached(run_dir, cache_key(fhour, "CLWMR_ICMR"), b"x")
            put_cached(run_dir, cache_key(fhour, "CLOUD_DIAG"), b"x")

        with patch(
            "weatherbrief.fetch.grib.grib_fetch.fetch_idx"
        ) as mock_idx:
            stats = precache_gfs_run(init)

        assert mock_idx.call_count == 0
        assert stats["hours_fetched"] == len(forecast_hours)
        assert stats["vars_fetched"] == 0

    def test_fetches_idx_and_byte_ranges_when_missing(
        self, tmp_path: Path, monkeypatch,
    ):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

        init = _utc(2026, 5, 8, 0)
        forecast_hours = airport_profile_forecast_hours(init)

        with patch(
            "weatherbrief.fetch.grib.grib_fetch.fetch_idx",
            return_value="dummy idx",
        ) as mock_idx, patch(
            "weatherbrief.fetch.grib.gfs_idx.plan_byte_ranges",
            return_value=["fake_range"],
        ), patch(
            "weatherbrief.fetch.grib.gfs_idx.plan_cloud_diag_byte_ranges",
            return_value=["fake_range"],
        ), patch(
            "weatherbrief.fetch.grib.grib_fetch.fetch_byte_ranges",
            return_value=b"CLWMR_BYTES",
        ) as mock_clwmr, patch(
            "weatherbrief.fetch.grib.grib_fetch.fetch_cloud_diag_ranges",
            return_value=b"DIAG_BYTES",
        ) as mock_diag:
            stats = precache_gfs_run(init)

        # idx fetched once per fhour; CLWMR+diag downloaded once per fhour
        assert mock_idx.call_count == len(forecast_hours)
        assert mock_clwmr.call_count == len(forecast_hours)
        assert mock_diag.call_count == len(forecast_hours)
        assert stats["hours_fetched"] == len(forecast_hours)
        # 2 vars per fhour (CLWMR + DIAG)
        assert stats["vars_fetched"] == len(forecast_hours) * 2


# ---------------------------------------------------------------------------
# Loop logic — main-cycle filter, last_done dedup
# ---------------------------------------------------------------------------


class TestPrecacheLoopFiltering:
    """Smoke-test the cycle filter + dedup logic the loop relies on.

    The full loop is exercised in ``run_grib_precache_loop`` (scheduler.py).
    Here we just verify the building blocks behave as the loop expects.
    """

    def test_main_cycle_constant_includes_only_main_runs(self):
        from weatherbrief.fetch.grib.precache import MAIN_CYCLE_HOURS
        assert set(MAIN_CYCLE_HOURS) == {0, 6, 12, 18}

    def test_short_cycle_init_excluded_by_filter(self):
        """A 03/09/15/21 Z init must not match the main-cycle filter."""
        from weatherbrief.fetch.grib.precache import MAIN_CYCLE_HOURS
        for h in (3, 9, 15, 21):
            assert h not in MAIN_CYCLE_HOURS


# ---------------------------------------------------------------------------
# ICON-D2 flight warming (#469 phase 3)
# ---------------------------------------------------------------------------


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    def execute(self, _stmt):
        return _FakeResult(self._rows)

    def close(self):
        self.closed = True


class _FakeRow:
    def __init__(self, fid):
        self.id = fid
        self.departure_time = _utc(2026, 7, 21, 12)
        self.flight_duration_hours = 2.0
        self.flight_ceiling_ft = 18000


class _FakeCtx:
    def __init__(self, variant):
        self.variant = variant


class TestIconD2FlightWarming:
    def _patches(self, rows, prepare_side_effect, warmed, prefetch=None):
        """Patch the warm path's collaborators; return a contextmanager stack.

        ``prefetch`` overrides the fake `_prefetch_icon_eu_data`; the default
        records the ctx and reports completion (True). Return False from an
        override to simulate the abort_if gate firing (jobs pending during an
        active refresh).
        """
        import contextlib

        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2

        def _default_prefetch(ctx, **kw):
            warmed.append(ctx)
            return True

        @contextlib.contextmanager
        def _cm():
            with patch("flyfun_common.db.SessionLocal", return_value=_FakeDB(rows)), \
                    patch("weatherbrief.storage.flights._row_to_flight",
                          side_effect=lambda r: r), \
                    patch("weatherbrief.api.packs._build_route_config",
                          side_effect=lambda flight, db_path: flight), \
                    patch("weatherbrief.fetch.route_points.interpolate_route",
                          side_effect=lambda route, spacing_nm=10.0: ["pt"]), \
                    patch("weatherbrief.fetch.grib._prepare_icon_eu",
                          side_effect=prepare_side_effect) as prep, \
                    patch("weatherbrief.fetch.grib._prefetch_icon_eu_data",
                          side_effect=prefetch or _default_prefetch) as fetch:
                yield prep, fetch

        return _cm()

    def test_empty_db_path_is_noop(self):
        from weatherbrief.fetch.grib.precache import precache_icon_d2_flights
        stats = precache_icon_d2_flights(_utc(2026, 7, 21, 0), db_path="")
        assert stats["flights_warmed"] == 0
        assert stats["flights_considered"] == 0

    def test_default_now_resolves_without_nameerror(self):
        # Production always omits `now` (scheduler passes only init + db_path),
        # so the `now or datetime.now(timezone.utc)` branch must actually
        # evaluate — with a non-empty db_path so it runs past the early return.
        # Regression for the missing `timezone` import (PR #470 review).
        from weatherbrief.fetch.grib.precache import precache_icon_d2_flights

        with patch("flyfun_common.db.SessionLocal", return_value=_FakeDB([])):
            stats = precache_icon_d2_flights(_utc(2026, 7, 21, 0), db_path="/db")
        assert stats["flights_considered"] == 0  # no rows, but no NameError

    def test_only_d2_eligible_flights_warmed(self):
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2, ICON_EU
        from weatherbrief.fetch.grib.precache import precache_icon_d2_flights

        # flight 1 → D2 (warm), 2 → EU (skip), 3 → no run (skip).
        outcomes = {
            1: (_FakeCtx(ICON_D2), None),
            2: (_FakeCtx(ICON_EU), None),
            3: (None, "out_of_range"),
        }

        # Rows are returned in order, so drive the outcomes by call sequence.
        calls = {"i": 0}
        ids = [1, 2, 3]

        def prepare_seq(sections, route_points, dep, **kwargs):
            fid = ids[calls["i"]]
            calls["i"] += 1
            return outcomes[fid]

        rows = [_FakeRow(1), _FakeRow(2), _FakeRow(3)]
        warmed: list = []
        with self._patches(rows, prepare_seq, warmed) as (_prep, fetch):
            stats = precache_icon_d2_flights(
                _utc(2026, 7, 21, 0), db_path="/db",
                now=_utc(2026, 7, 21, 0),
            )
        assert stats == {
            "flights_considered": 3, "flights_warmed": 1, "flights_skipped": 2,
            "deferred": 0,
        }
        assert len(warmed) == 1
        assert warmed[0].variant is ICON_D2
        # Discretionary warm must request the reduced prefetch budget so it
        # never crowds out a concurrent interactive briefing (05:09Z OOM),
        # and must thread the yield gate through as abort_if (#490).
        assert fetch.call_args.kwargs.get("outer_workers") == 2
        assert callable(fetch.call_args.kwargs.get("abort_if"))

    def test_duration_passed_to_prepare(self):
        """The warm path threads the flight's real duration through.

        Duration picks the forecast-hour window, so it must reach _prepare_icon_eu
        for the warmed cache to be byte-for-byte what the briefing later reads.
        (The ceiling is deliberately NOT passed any more: the sounding column is
        never subset per flight — #478 removed the ceiling-limited fetch.)
        """
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2
        from weatherbrief.fetch.grib.precache import precache_icon_d2_flights

        captured = {}

        def prepare(sections, route_points, dep, **kwargs):
            captured.update(kwargs)
            return _FakeCtx(ICON_D2), None

        row = _FakeRow(1)
        row.flight_duration_hours = 2.0
        warmed: list = []
        with self._patches([row], prepare, warmed):
            precache_icon_d2_flights(
                _utc(2026, 7, 21, 0), db_path="/db", now=_utc(2026, 7, 21, 0),
            )
        assert captured["flight_duration_hours"] == 2.0
        assert "flight_ceiling_ft" not in captured

    def test_one_flight_failure_does_not_abort_others(self):
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2
        from weatherbrief.fetch.grib.precache import precache_icon_d2_flights

        calls = {"i": 0}

        def prepare_seq(sections, route_points, dep, **kwargs):
            calls["i"] += 1
            if calls["i"] == 1:
                raise RuntimeError("boom")
            return _FakeCtx(ICON_D2), None

        warmed: list = []
        with self._patches([_FakeRow(1), _FakeRow(2)], prepare_seq, warmed):
            stats = precache_icon_d2_flights(
                _utc(2026, 7, 21, 0), db_path="/db", now=_utc(2026, 7, 21, 0),
            )
        assert stats["flights_considered"] == 2
        assert stats["flights_warmed"] == 1   # second flight still warmed
        assert stats["flights_skipped"] == 1


# ---------------------------------------------------------------------------
# Wall-clock warming window (issue #475 item 3)
# ---------------------------------------------------------------------------


class TestWarmingWindow:

    def test_gated_models_default_03z_21z(self):
        from weatherbrief.fetch.grib.precache import MODEL_WARMING_WINDOW_UTC
        assert MODEL_WARMING_WINDOW_UTC["icon-d2"] == (3, 21)
        assert MODEL_WARMING_WINDOW_UTC["icon-eu"] == (3, 21)

    def test_gfs_is_ungated(self):
        from weatherbrief.fetch.grib.precache import (
            MODEL_WARMING_WINDOW_UTC,
            is_within_warming_window,
        )
        assert "gfs" not in MODEL_WARMING_WINDOW_UTC
        # Ungated → runs at any hour, including the dead of night.
        for hour in (0, 2, 3, 12, 21, 23):
            assert is_within_warming_window("gfs", _utc(2026, 7, 21, hour))

    def test_d2_daytime_passes_run(self):
        """The six daytime D2 passes (05..20) are inside the window."""
        from weatherbrief.fetch.grib.precache import is_within_warming_window
        for hour in (5, 8, 11, 14, 17, 20):
            assert is_within_warming_window("icon-d2", _utc(2026, 7, 21, hour))

    def test_d2_overnight_passes_skipped(self):
        """The 23Z and 02Z D2 passes are outside the window."""
        from weatherbrief.fetch.grib.precache import is_within_warming_window
        assert not is_within_warming_window("icon-d2", _utc(2026, 7, 21, 23))
        assert not is_within_warming_window("icon-d2", _utc(2026, 7, 21, 2))

    def test_icon_eu_boundaries(self):
        """ICON-EU 03Z pass runs; the 21Z pass is dropped."""
        from weatherbrief.fetch.grib.precache import is_within_warming_window
        assert is_within_warming_window("icon-eu", _utc(2026, 7, 21, 3))
        assert is_within_warming_window("icon-eu", _utc(2026, 7, 21, 15))
        assert not is_within_warming_window("icon-eu", _utc(2026, 7, 21, 21))


class TestInteractiveRefreshActive:
    """The gate the warm loop polls — reads the live refresh registry (#490)."""

    @pytest.fixture
    def registry(self):
        """A fresh registry standing in for the process-wide one."""
        from weatherbrief.api.packs import _RefreshRegistry

        fresh = _RefreshRegistry()
        with patch("weatherbrief.api.packs.refresh_registry", fresh):
            yield fresh

    def test_idle_process_does_not_defer(self, registry):
        from weatherbrief.fetch.grib.precache import interactive_refresh_active
        # Nothing has ever refreshed → nothing to yield to.
        assert not interactive_refresh_active()

    def test_queued_refresh_defers(self, registry):
        from weatherbrief.fetch.grib.precache import interactive_refresh_active

        registry.try_register("f1", triggered_by="user", user_id="u1")
        assert interactive_refresh_active()

    def test_scheduler_refresh_also_defers(self, registry):
        """The 05Z burst is scheduler-driven — warming must yield to it too."""
        from weatherbrief.fetch.grib.precache import interactive_refresh_active

        registry.try_register("f1", triggered_by="scheduler")
        assert interactive_refresh_active()

    def test_active_refresh_defers_whatever_the_cooldown(self, registry):
        """The cooldown extends the yield; it can never shorten it."""
        from weatherbrief.fetch.grib.precache import interactive_refresh_active

        registry.try_register("f1", triggered_by="user", user_id="u1")
        assert interactive_refresh_active(cooldown_seconds=0.0)

    def test_cooldown_after_last_refresh_finishes(self, registry):
        from weatherbrief.fetch.grib.precache import (
            WARM_YIELD_COOLDOWN_SECONDS,
            interactive_refresh_active,
        )

        registry.try_register("f1", triggered_by="user", user_id="u1")
        registry.unregister("f1")
        # Just finished → still inside the cooldown.
        assert interactive_refresh_active()
        assert registry.idle_seconds() < WARM_YIELD_COOLDOWN_SECONDS
        # Same instant, but with a cooldown short enough to have elapsed.
        assert not interactive_refresh_active(cooldown_seconds=0.0)


class TestWarmYielding:
    """Every warm pass bails out mid-flight when a briefing needs the box."""

    def test_icon_eu_defers_before_any_download(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

        with patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_per_variable"
        ) as mock_var, patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_single_level"
        ) as mock_single:
            stats = precache_icon_eu_run(
                _utc(2026, 5, 8, 0), should_defer=lambda: True,
            )

        assert stats["deferred"] == 1
        assert stats["hours_fetched"] == 0
        mock_var.assert_not_called()
        mock_single.assert_not_called()

    def test_icon_eu_defers_between_variables(self, tmp_path: Path, monkeypatch):
        """A refresh arriving mid-hour stops the pass at the next variable."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

        # Yield only once the first variable download has happened.
        calls = {"n": 0}

        def fetch_var(init_date, init_hour, fhour, **kwargs):
            calls["n"] += 1
            return {kwargs["variables"][0]: b"grib"}

        with patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_per_variable",
            side_effect=fetch_var,
        ), patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_single_level"
        ) as mock_single:
            stats = precache_icon_eu_run(
                _utc(2026, 5, 8, 0), should_defer=lambda: calls["n"] >= 1,
            )

        assert stats["deferred"] == 1
        assert calls["n"] == 1          # stopped after the first variable
        assert stats["vars_fetched"] == 1
        assert stats["hours_fetched"] == 0  # the hour never completed
        mock_single.assert_not_called()  # cloud diag skipped on the bail-out

    def test_icon_eu_fast_forwards_cached_hours_during_refresh(
        self, tmp_path: Path, monkeypatch,
    ):
        """Cache hits are free — an active refresh must not block them.

        A resumption pass over a fully-warmed run completes (so last_done gets
        recorded) even while a refresh burst is in flight (PR #498 review).
        """
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

        from weatherbrief.fetch.grib.cache import (
            cache_dir_for_run,
            cache_key,
            put_cached,
        )

        init = _utc(2026, 5, 8, 0)
        run_dir = cache_dir_for_run(tmp_path, "20260508", 0, model="icon-eu")
        for fhour in airport_profile_forecast_hours(init):
            put_cached(run_dir, cache_key(fhour, "ICON_EU_QC_QI_P"), b"x")

        with patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_per_variable"
        ) as mock_var, patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_single_level"
        ) as mock_single:
            stats = precache_icon_eu_run(init, should_defer=lambda: True)

        assert stats["deferred"] == 0
        assert stats["hours_fetched"] == stats["forecast_hours_total"]
        mock_var.assert_not_called()
        mock_single.assert_not_called()

    def test_gfs_fast_forwards_cached_hours_during_refresh(
        self, tmp_path: Path, monkeypatch,
    ):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

        from weatherbrief.fetch.grib.cache import (
            cache_dir_for_run,
            cache_key,
            put_cached,
        )

        init = _utc(2026, 5, 8, 12)
        run_dir = cache_dir_for_run(tmp_path, "20260508", 12, model="gfs")
        for fhour in airport_profile_forecast_hours(init):
            put_cached(run_dir, cache_key(fhour, "CLWMR_ICMR"), b"x")
            put_cached(run_dir, cache_key(fhour, "CLOUD_DIAG"), b"x")

        with patch(
            "weatherbrief.fetch.grib.grib_fetch.fetch_idx"
        ) as mock_idx:
            stats = precache_gfs_run(init, should_defer=lambda: True)

        assert stats["deferred"] == 0
        assert stats["hours_fetched"] == stats["forecast_hours_total"]
        mock_idx.assert_not_called()

    def test_icon_eu_reports_not_deferred_on_a_clean_pass(
        self, tmp_path: Path, monkeypatch,
    ):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

        with patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_per_variable",
            return_value={},
        ), patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_single_level",
            return_value={},
        ):
            stats = precache_icon_eu_run(
                _utc(2026, 5, 8, 0), should_defer=lambda: False,
            )

        assert stats["deferred"] == 0
        assert stats["hours_fetched"] == stats["forecast_hours_total"]

    def test_gfs_defers_between_forecast_hours(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

        hours = {"n": 0}

        def fetch_idx(init_date, init_hour, fhour, **kwargs):
            hours["n"] += 1
            return "idx"

        with patch(
            "weatherbrief.fetch.grib.grib_fetch.fetch_idx", side_effect=fetch_idx,
        ), patch(
            "weatherbrief.fetch.grib.gfs_idx.plan_byte_ranges", return_value=[],
        ), patch(
            "weatherbrief.fetch.grib.gfs_idx.plan_cloud_diag_byte_ranges",
            return_value=[],
        ):
            stats = precache_gfs_run(
                _utc(2026, 5, 8, 0), should_defer=lambda: hours["n"] >= 2,
            )

        assert stats["deferred"] == 1
        assert hours["n"] == 2  # two hours fetched, then yielded
        assert stats["hours_fetched"] == 2
        assert stats["hours_fetched"] < stats["forecast_hours_total"]

    def test_d2_defers_when_prefetch_aborts(self):
        """A flight whose prefetch hits the abort_if gate stops the pass."""
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2
        from weatherbrief.fetch.grib.precache import precache_icon_d2_flights

        warmed: list = []
        rows = [_FakeRow(1), _FakeRow(2), _FakeRow(3)]

        def prefetch(ctx, **kw):
            # First flight completes (fully cached, say); a refresh is active
            # by the second, which has pending jobs → the gate aborts it.
            if warmed:
                return False
            warmed.append(ctx)
            return True

        helper = TestIconD2FlightWarming()
        with helper._patches(
            rows, lambda *a, **kw: (_FakeCtx(ICON_D2), None), warmed,
            prefetch=prefetch,
        ):
            stats = precache_icon_d2_flights(
                _utc(2026, 7, 21, 0), db_path="/db", now=_utc(2026, 7, 21, 0),
            )

        assert stats["deferred"] == 1
        assert stats["flights_warmed"] == 1
        assert stats["flights_considered"] == 2  # flight 3 never started
        assert len(warmed) == 1

    def test_d2_fast_forwards_warmed_flights_during_refresh(self):
        """An active refresh must not block flights that are already warmed.

        The gate lives inside the prefetch (abort_if) and only fires when
        downloads are pending — so a resumption pass with every flight cached
        completes and lets the scheduler record last_done, even mid-burst
        (PR #498 review finding).
        """
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2
        from weatherbrief.fetch.grib.precache import precache_icon_d2_flights

        warmed: list = []
        rows = [_FakeRow(1), _FakeRow(2), _FakeRow(3)]

        helper = TestIconD2FlightWarming()
        with helper._patches(
            # Default prefetch fake: no pending jobs → completes (True), as a
            # fully-warmed flight would even while a refresh is active.
            rows, lambda *a, **kw: (_FakeCtx(ICON_D2), None), warmed,
        ):
            stats = precache_icon_d2_flights(
                _utc(2026, 7, 21, 0), db_path="/db", now=_utc(2026, 7, 21, 0),
                should_defer=lambda: True,  # refresh active the whole pass
            )

        assert stats["deferred"] == 0
        assert stats["flights_warmed"] == 3
        assert len(warmed) == 3

    def test_d2_clean_pass_is_not_deferred(self):
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2
        from weatherbrief.fetch.grib.precache import precache_icon_d2_flights

        warmed: list = []
        helper = TestIconD2FlightWarming()
        with helper._patches(
            [_FakeRow(1)], lambda *a, **kw: (_FakeCtx(ICON_D2), None), warmed,
        ):
            stats = precache_icon_d2_flights(
                _utc(2026, 7, 21, 0), db_path="/db", now=_utc(2026, 7, 21, 0),
                should_defer=lambda: False,
            )

        assert stats["deferred"] == 0
        assert stats["flights_warmed"] == 1


class TestShouldWarm:

    def test_inside_window_new_run_warms(self):
        from weatherbrief.fetch.grib.precache import should_warm
        assert should_warm(
            "icon-d2", "20260721_17z", last_key=None, now=_utc(2026, 7, 21, 17),
        )

    def test_already_done_skips(self):
        from weatherbrief.fetch.grib.precache import should_warm
        assert not should_warm(
            "icon-d2", "20260721_17z", last_key="20260721_17z",
            now=_utc(2026, 7, 21, 17),
        )

    def test_outside_window_skips_and_not_backfilled(self):
        """A skipped overnight pass is never replayed once the window opens.

        23Z pass → skipped (last_done stays at the prior in-window run). When
        03:00 arrives, the marker has advanced to the 03z run, so should_warm
        fires for THAT run — not the skipped 23z/00z runs.
        """
        from weatherbrief.fetch.grib.precache import should_warm

        last = "20260720_20z"  # last in-window run warmed the evening before
        # 23:00 pass for the 21z init → skipped (outside window). Caller must
        # NOT record it, so last_done is unchanged.
        assert not should_warm(
            "icon-d2", "20260720_21z", last_key=last, now=_utc(2026, 7, 20, 23),
        )
        # 02:00 pass for the 00z init → still skipped.
        assert not should_warm(
            "icon-d2", "20260721_00z", last_key=last, now=_utc(2026, 7, 21, 2),
        )
        # 03:00: window opens, freshest run is now the 03z init → warm it.
        assert should_warm(
            "icon-d2", "20260721_03z", last_key=last, now=_utc(2026, 7, 21, 3),
        )
