"""Tests for dynamic source dispatch + the new ECMWF watcher helper."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from weatherbrief.fetch.freshness import sources
from weatherbrief.fetch.freshness.registry import SOURCE_REGISTRY
from weatherbrief.fetch.grib import ecmwf_watcher


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# ecmwf_watcher.get_latest_ready
# ---------------------------------------------------------------------------


class TestGetLatestReady:
    def test_returns_none_for_empty_dir(self, tmp_path: Path):
        assert ecmwf_watcher.get_latest_ready(tmp_path) is None

    def test_returns_none_when_dir_missing(self, tmp_path: Path):
        missing = tmp_path / "nope"
        assert ecmwf_watcher.get_latest_ready(missing) is None

    def test_finds_max_complete_sentinel(self, tmp_path: Path):
        (tmp_path / ".ready_20260503_00z").write_text("complete")
        (tmp_path / ".ready_20260502_18z").write_text("complete")
        latest = ecmwf_watcher.get_latest_ready(tmp_path)
        assert latest == _utc(2026, 5, 3, 0)

    def test_includes_partial_sentinels(self, tmp_path: Path):
        # Partial sentinels should also count — a timed-out partial run is
        # still useful data.
        (tmp_path / ".ready_20260502_18z").write_text("complete")
        (tmp_path / ".ready_20260503_00z.partial").write_text("partial")
        latest = ecmwf_watcher.get_latest_ready(tmp_path)
        assert latest == _utc(2026, 5, 3, 0)

    def test_ignores_non_sentinel_files(self, tmp_path: Path):
        (tmp_path / ".ready_20260503_00z").write_text("complete")
        (tmp_path / "delivery_config.json").write_text("{}")
        (tmp_path / "A1S05030000050300011").write_text("grib bytes")
        (tmp_path / ".ready_garbage").write_text("noise")
        latest = ecmwf_watcher.get_latest_ready(tmp_path)
        assert latest == _utc(2026, 5, 3, 0)

    def test_ignores_malformed_sentinel_names(self, tmp_path: Path):
        (tmp_path / ".ready_2026_xx_zz").write_text("noise")
        (tmp_path / ".ready_20260503_24z").write_text("noise")  # invalid hour
        (tmp_path / ".ready_20260503_06z").write_text("complete")
        latest = ecmwf_watcher.get_latest_ready(tmp_path)
        assert latest == _utc(2026, 5, 3, 6)

    def test_with_mtime_returns_sentinel_mtime(self, tmp_path: Path):
        sentinel = tmp_path / ".ready_20260503_12z"
        sentinel.write_text("complete")
        mtime_ts = _utc(2026, 5, 3, 18, 47).timestamp()
        os.utime(sentinel, (mtime_ts, mtime_ts))
        result = ecmwf_watcher.get_latest_ready_with_mtime(tmp_path)
        assert result is not None
        init, mtime = result
        assert init == _utc(2026, 5, 3, 12)
        assert mtime is not None
        assert int(mtime.timestamp()) == int(mtime_ts)

    def test_with_mtime_returns_none_for_empty_dir(self, tmp_path: Path):
        assert ecmwf_watcher.get_latest_ready_with_mtime(tmp_path) is None

    def test_with_mtime_propagates_none_when_stat_races(self, tmp_path: Path, monkeypatch):
        """If stat() raises (sentinel deleted between iterdir and stat),
        the helper must return mtime=None — NOT substitute the cycle init,
        which would mislead the popover with a wildly wrong publish time.
        """
        (tmp_path / ".ready_20260503_12z").write_text("complete")
        original_stat = Path.stat

        def _racing_stat(self, *args, **kwargs):
            if self.name.startswith(".ready_"):
                raise OSError("simulated race: sentinel removed")
            return original_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", _racing_stat)
        result = ecmwf_watcher.get_latest_ready_with_mtime(tmp_path)
        assert result is not None
        init, mtime = result
        assert init == _utc(2026, 5, 3, 12)
        assert mtime is None  # crucial: NOT init


# ---------------------------------------------------------------------------
# sources.check_source dispatch
# ---------------------------------------------------------------------------


class TestCheckSourceDispatch:
    def test_unknown_source_returns_none(self, caplog):
        assert sources.check_source("bogus:source", "ecmwf") is None

    def test_ecmwf_direct_uses_watcher(self, monkeypatch, tmp_path: Path):
        # Drop a sentinel and point the watcher at the temp dir.
        sentinel = tmp_path / ".ready_20260503_12z"
        sentinel.write_text("complete")
        # Pin the sentinel mtime so we can assert on the publish_at value.
        mtime_ts = _utc(2026, 5, 3, 18, 32).timestamp()
        os.utime(sentinel, (mtime_ts, mtime_ts))

        def _fake_dir():
            return tmp_path
        monkeypatch.setattr(
            "weatherbrief.fetch.grib.ecmwf_watcher.ecmwf_grib_dir",
            _fake_dir,
        )
        result = sources.check_source("ecmwf:direct", "ecmwf")
        assert result is not None
        assert result.init == _utc(2026, 5, 3, 12)
        # Sentinel mtime is the closest analogue to publish time for direct.
        assert result.published_at is not None
        assert int(result.published_at.timestamp()) == int(mtime_ts)

    def test_gfs_noaa_returns_aware_utc(self, monkeypatch):
        from weatherbrief.fetch.grib import grib_fetch

        class _Resp:
            headers = {"Last-Modified": "Sun, 03 May 2026 11:15:42 GMT"}

        def _fake(target_time, **kw):
            return ("20260503", 6, _Resp())
        monkeypatch.setattr(grib_fetch, "find_latest_run_with_response", _fake)
        result = sources.check_source("gfs:noaa", "gfs")
        assert result is not None
        assert result.init == _utc(2026, 5, 3, 6)
        assert result.init.tzinfo == timezone.utc
        # Publish time comes from the HEAD response we already had —
        # no second round-trip.
        assert result.published_at == _utc(2026, 5, 3, 11, 15).replace(second=42)

    def test_icon_eu_dwd_returns_aware_utc(self, monkeypatch):
        from weatherbrief.fetch.grib import icon_eu_fetch

        class _Resp:
            headers = {"Last-Modified": "Sun, 03 May 2026 12:08:11 GMT"}

        def _fake(target_time, **kw):
            return ("20260503", 9, _Resp())
        monkeypatch.setattr(
            icon_eu_fetch, "find_latest_icon_eu_run_with_response", _fake,
        )
        result = sources.check_source("icon_eu:dwd", "icon_eu")
        assert result is not None
        assert result.init == _utc(2026, 5, 3, 9)
        assert result.published_at == _utc(2026, 5, 3, 12, 8).replace(second=11)

    def test_om_meta_skips_zero_availability(self, monkeypatch):
        """Guard against OM's `last_availability_time=0` (unpublished model)
        rendering as Jan 1 1970 in the popover."""
        from weatherbrief.fetch import model_status

        init_ts = int(_utc(2026, 5, 3, 12).timestamp())

        def _fake_meta(models, **kw):
            return {
                models[0]: model_status.ModelMetadata(
                    model=models[0],
                    last_init_time=init_ts,
                    last_availability_time=0,  # OM sentinel for "not yet"
                    update_interval_seconds=21600,
                )
            }
        monkeypatch.setattr(model_status, "fetch_model_metadata", _fake_meta)
        result = sources.check_source("gfs:openmeteo", "gfs")
        assert result is not None
        assert result.init == _utc(2026, 5, 3, 12)
        assert result.published_at is None

    def test_om_meta_returns_init_and_publish_time(self, monkeypatch):
        from weatherbrief.fetch import model_status

        init_ts = int(_utc(2026, 5, 3, 12).timestamp())
        avail_ts = init_ts + 60 * 45  # OM published 45 min after run init

        def _fake_meta(models, **kw):
            return {
                models[0]: model_status.ModelMetadata(
                    model=models[0],
                    last_init_time=init_ts,
                    last_availability_time=avail_ts,
                    update_interval_seconds=21600,
                )
            }
        monkeypatch.setattr(model_status, "fetch_model_metadata", _fake_meta)
        result = sources.check_source("gfs:openmeteo", "gfs")
        assert result is not None
        assert result.init == _utc(2026, 5, 3, 12)
        assert result.published_at is not None
        assert int(result.published_at.timestamp()) == avail_ts

    def test_om_meta_returns_data_end(self, monkeypatch):
        """OM's meta.json carries data_end_time — must surface so the
        catalog can override the static config horizon when the live
        run delivers fewer hours (the meteofrance empty-data case)."""
        from weatherbrief.fetch import model_status

        init_ts = int(_utc(2026, 5, 3, 12).timestamp())
        avail_ts = init_ts + 60 * 45
        # Simulate a ~103h run (shorter than the 144h config horizon).
        data_end_ts = init_ts + 103 * 3600

        def _fake_meta(models, **kw):
            return {
                models[0]: model_status.ModelMetadata(
                    model=models[0],
                    last_init_time=init_ts,
                    last_availability_time=avail_ts,
                    update_interval_seconds=21600,
                    data_end_time=data_end_ts,
                )
            }
        monkeypatch.setattr(model_status, "fetch_model_metadata", _fake_meta)
        result = sources.check_source("meteofrance:openmeteo", "meteofrance")
        assert result is not None
        assert result.data_end is not None
        assert int(result.data_end.timestamp()) == data_end_ts

    def test_om_meta_no_data_end_field(self, monkeypatch):
        """data_end_time=0 (legacy or missing) yields data_end=None — we
        fall back to the static config horizon downstream."""
        from weatherbrief.fetch import model_status

        init_ts = int(_utc(2026, 5, 3, 12).timestamp())

        def _fake_meta(models, **kw):
            return {
                models[0]: model_status.ModelMetadata(
                    model=models[0],
                    last_init_time=init_ts,
                    last_availability_time=init_ts + 3600,
                    update_interval_seconds=21600,
                    data_end_time=0,
                )
            }
        monkeypatch.setattr(model_status, "fetch_model_metadata", _fake_meta)
        result = sources.check_source("gfs:openmeteo", "gfs")
        assert result is not None
        assert result.data_end is None

    def test_dispatch_swallows_exceptions(self, monkeypatch):
        from weatherbrief.fetch.grib import grib_fetch

        def _boom(*a, **k):
            raise RuntimeError("simulated S3 outage")
        monkeypatch.setattr(grib_fetch, "find_latest_run_with_response", _boom)
        result = sources.check_source("gfs:noaa", "gfs")
        assert result is None


# ---------------------------------------------------------------------------
# all_tracked_sources
# ---------------------------------------------------------------------------


class TestParseLastModified:
    """Cover the small header-parse helper used by the GFS / ICON dispatches."""

    def test_returns_aware_utc_for_valid_header(self):
        from weatherbrief.fetch.freshness import sources as srcs

        result = srcs._parse_last_modified("Sun, 03 May 2026 11:15:42 GMT")
        assert result is not None
        assert result.tzinfo is not None
        assert result == _utc(2026, 5, 3, 11, 15).replace(second=42)

    def test_returns_none_for_none_header(self):
        from weatherbrief.fetch.freshness import sources as srcs
        assert srcs._parse_last_modified(None) is None

    def test_returns_none_for_empty_header(self):
        from weatherbrief.fetch.freshness import sources as srcs
        assert srcs._parse_last_modified("") is None

    def test_returns_none_for_garbage_header(self):
        from weatherbrief.fetch.freshness import sources as srcs
        assert srcs._parse_last_modified("not-a-real-date") is None


def test_all_tracked_sources_matches_registry():
    pairs = sources.all_tracked_sources()
    active = {k for k, c in SOURCE_REGISTRY.items() if c.is_active}
    assert len(pairs) == len(active)
    for src, model in pairs:
        assert src in active
        assert src.startswith(model + ":")


def test_a_partial_deployment_does_not_track_the_sources_it_left_off(monkeypatch):
    """The subset axis has to gate too, not just the all-off axis.

    Radar without EUMETSAT credentials is the documented half-a-feature case.
    Without this, the two satellite rows stay active, the loop probes a frame
    store that will never hold them, and the help page shows a permanently red
    row for a source nobody enabled — exactly what env_gate exists to prevent.
    """
    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_SOURCES", "opera_dbzh,opera_rate")
    tracked = {s for s, _ in sources.all_tracked_sources()}
    assert "opera_dbzh:eumetnet" in tracked
    assert "opera_rate:eumetnet" in tracked
    assert "eumetsat_li:eumetsat" not in tracked
    assert "eumetsat_ctth:eumetsat" not in tracked

    # Unset means "all of them", matching enabled_sources().
    monkeypatch.delenv("WB_OBSERVED_SOURCES", raising=False)
    assert "eumetsat_ctth:eumetsat" in {s for s, _ in sources.all_tracked_sources()}


def test_env_gated_sources_are_not_tracked_until_enabled(monkeypatch):
    """The loop must not probe a collector the deployment never started."""
    gated = {k for k, c in SOURCE_REGISTRY.items() if c.env_gate}
    assert gated
    # Control BOTH axes: a subset left in the ambient environment would keep
    # some gated sources off and make this assert for the wrong reason.
    monkeypatch.delenv("WB_OBSERVED_SOURCES", raising=False)
    monkeypatch.delenv("WB_OBSERVED_ENABLED", raising=False)
    assert not ({s for s, _ in sources.all_tracked_sources()} & gated)
    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    assert gated <= {s for s, _ in sources.all_tracked_sources()}
