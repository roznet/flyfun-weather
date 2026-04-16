"""Tests for weatherbrief.frontal.cache — dev cache save/load/clear."""

from weatherbrief.frontal.cache import (
    save_raw_response,
    load_raw_response,
    clear_cache,
)


class TestCache:
    def test_save_load_roundtrip(self, tmp_path):
        data = {"temperature_850hPa": [[1.0, 2.0], [3.0, 4.0]]}
        save_raw_response("ecmwf", "2026041500", data, cache_dir=tmp_path)
        loaded = load_raw_response("ecmwf", "2026041500", cache_dir=tmp_path)
        assert loaded == data

    def test_load_missing_returns_none(self, tmp_path):
        result = load_raw_response("gfs", "2026041500", cache_dir=tmp_path)
        assert result is None

    def test_different_keys_independent(self, tmp_path):
        data1 = {"model": "ecmwf"}
        data2 = {"model": "gfs"}
        save_raw_response("ecmwf", "2026041500", data1, cache_dir=tmp_path)
        save_raw_response("gfs", "2026041500", data2, cache_dir=tmp_path)
        assert load_raw_response("ecmwf", "2026041500", tmp_path) == data1
        assert load_raw_response("gfs", "2026041500", tmp_path) == data2

    def test_clear_cache(self, tmp_path):
        save_raw_response("ecmwf", "2026041500", {}, cache_dir=tmp_path)
        save_raw_response("gfs", "2026041500", {}, cache_dir=tmp_path)
        count = clear_cache(tmp_path)
        assert count == 2
        assert load_raw_response("ecmwf", "2026041500", tmp_path) is None

    def test_clear_empty_cache(self, tmp_path):
        count = clear_cache(tmp_path / "nonexistent")
        assert count == 0
