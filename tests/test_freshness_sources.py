"""Tests for dynamic source dispatch + the new ECMWF watcher helper."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from weatherbrief.fetch.freshness import sources
from weatherbrief.fetch.freshness.registry import SOURCE_REGISTRY
from weatherbrief.fetch.grib import ecmwf_watcher


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


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


# ---------------------------------------------------------------------------
# sources.check_source dispatch
# ---------------------------------------------------------------------------


class TestCheckSourceDispatch:
    def test_unknown_source_returns_none(self, caplog):
        assert sources.check_source("bogus:source", "ecmwf") is None

    def test_ecmwf_direct_uses_watcher(self, monkeypatch, tmp_path: Path):
        # Drop a sentinel and point the watcher at the temp dir.
        (tmp_path / ".ready_20260503_12z").write_text("complete")

        def _fake_dir():
            return tmp_path
        monkeypatch.setattr(
            "weatherbrief.fetch.grib.ecmwf_watcher.ecmwf_grib_dir",
            _fake_dir,
        )
        result = sources.check_source("ecmwf:direct", "ecmwf")
        assert result == _utc(2026, 5, 3, 12)

    def test_gfs_noaa_returns_aware_utc(self, monkeypatch):
        from weatherbrief.fetch.grib import grib_fetch

        def _fake(target_time, **kw):
            return ("20260503", 6)
        monkeypatch.setattr(grib_fetch, "find_latest_run", _fake)
        result = sources.check_source("gfs:noaa", "gfs")
        assert result == _utc(2026, 5, 3, 6)
        assert result.tzinfo == timezone.utc

    def test_icon_eu_dwd_returns_aware_utc(self, monkeypatch):
        from weatherbrief.fetch.grib import icon_eu_fetch

        def _fake(target_time, **kw):
            return ("20260503", 9)
        monkeypatch.setattr(icon_eu_fetch, "find_latest_icon_eu_run", _fake)
        result = sources.check_source("icon_eu:dwd", "icon_eu")
        assert result == _utc(2026, 5, 3, 9)

    def test_om_meta_returns_aware_utc(self, monkeypatch):
        from weatherbrief.fetch import model_status

        ts = int(_utc(2026, 5, 3, 12).timestamp())

        def _fake_meta(models, **kw):
            return {
                models[0]: model_status.ModelMetadata(
                    model=models[0],
                    last_init_time=ts,
                    last_availability_time=ts + 60,
                    update_interval_seconds=21600,
                )
            }
        monkeypatch.setattr(model_status, "fetch_model_metadata", _fake_meta)
        result = sources.check_source("gfs:openmeteo", "gfs")
        assert result == _utc(2026, 5, 3, 12)

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


def test_all_tracked_sources_matches_registry():
    pairs = sources.all_tracked_sources()
    assert len(pairs) == len(SOURCE_REGISTRY)
    for src, model in pairs:
        assert src in SOURCE_REGISTRY
        assert src.startswith(model + ":")
