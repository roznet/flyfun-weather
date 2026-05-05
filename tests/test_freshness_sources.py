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
        assert int(mtime.timestamp()) == int(mtime_ts)

    def test_with_mtime_returns_none_for_empty_dir(self, tmp_path: Path):
        assert ecmwf_watcher.get_latest_ready_with_mtime(tmp_path) is None


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
        from weatherbrief.fetch.freshness import sources as srcs
        from weatherbrief.fetch.grib import grib_fetch

        def _fake(target_time, **kw):
            return ("20260503", 6)
        monkeypatch.setattr(grib_fetch, "find_latest_run", _fake)
        # Stub the Last-Modified probe so the dispatch doesn't touch S3.
        publish_ts = _utc(2026, 5, 3, 11, 15)
        monkeypatch.setattr(srcs, "_http_last_modified", lambda url: publish_ts)
        result = sources.check_source("gfs:noaa", "gfs")
        assert result is not None
        assert result.init == _utc(2026, 5, 3, 6)
        assert result.init.tzinfo == timezone.utc
        assert result.published_at == publish_ts

    def test_icon_eu_dwd_returns_aware_utc(self, monkeypatch):
        from weatherbrief.fetch.freshness import sources as srcs
        from weatherbrief.fetch.grib import icon_eu_fetch

        def _fake(target_time, **kw):
            return ("20260503", 9)
        monkeypatch.setattr(icon_eu_fetch, "find_latest_icon_eu_run", _fake)
        publish_ts = _utc(2026, 5, 3, 12, 8)
        monkeypatch.setattr(srcs, "_http_last_modified", lambda url: publish_ts)
        result = sources.check_source("icon_eu:dwd", "icon_eu")
        assert result is not None
        assert result.init == _utc(2026, 5, 3, 9)
        assert result.published_at == publish_ts

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

    def test_dispatch_swallows_exceptions(self, monkeypatch):
        from weatherbrief.fetch.grib import grib_fetch

        def _boom(*a, **k):
            raise RuntimeError("simulated S3 outage")
        monkeypatch.setattr(grib_fetch, "find_latest_run", _boom)
        result = sources.check_source("gfs:noaa", "gfs")
        assert result is None


# ---------------------------------------------------------------------------
# all_tracked_sources
# ---------------------------------------------------------------------------


class TestHttpLastModified:
    """Cover the small HEAD helper used by the GFS / ICON dispatches."""

    def test_returns_aware_utc_for_valid_header(self, monkeypatch):
        from weatherbrief.fetch.freshness import sources as srcs

        class _Resp:
            status_code = 200
            headers = {"Last-Modified": "Sun, 03 May 2026 11:15:42 GMT"}

        monkeypatch.setattr(
            "requests.head", lambda url, timeout=10: _Resp(),
        )
        result = srcs._http_last_modified("https://example.test/x")
        assert result is not None
        assert result.tzinfo is not None
        assert result == _utc(2026, 5, 3, 11, 15).replace(second=42)

    def test_returns_none_on_non_200(self, monkeypatch):
        from weatherbrief.fetch.freshness import sources as srcs

        class _Resp:
            status_code = 404
            headers = {}

        monkeypatch.setattr("requests.head", lambda url, timeout=10: _Resp())
        assert srcs._http_last_modified("https://example.test/x") is None

    def test_returns_none_when_header_missing(self, monkeypatch):
        from weatherbrief.fetch.freshness import sources as srcs

        class _Resp:
            status_code = 200
            headers = {}

        monkeypatch.setattr("requests.head", lambda url, timeout=10: _Resp())
        assert srcs._http_last_modified("https://example.test/x") is None

    def test_swallows_request_exceptions(self, monkeypatch):
        from weatherbrief.fetch.freshness import sources as srcs

        def _boom(url, timeout=10):
            raise RuntimeError("network down")
        monkeypatch.setattr("requests.head", _boom)
        assert srcs._http_last_modified("https://example.test/x") is None


def test_all_tracked_sources_matches_registry():
    pairs = sources.all_tracked_sources()
    assert len(pairs) == len(SOURCE_REGISTRY)
    for src, model in pairs:
        assert src in SOURCE_REGISTRY
        assert src.startswith(model + ":")
