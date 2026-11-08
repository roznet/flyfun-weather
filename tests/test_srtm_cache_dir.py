"""SRTM tile cache follows DATA_DIR unless SRTM_CACHE_DIR is set."""

from pathlib import Path

from weatherbrief.fetch.elevation import srtm_cache_dir


def test_defaults_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("SRTM_CACHE_DIR", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert srtm_cache_dir() == tmp_path / ".cache" / "srtm"


def test_explicit_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SRTM_CACHE_DIR", str(tmp_path / "tiles"))
    assert srtm_cache_dir() == tmp_path / "tiles"


def test_falls_back_to_relative_data(monkeypatch):
    monkeypatch.delenv("SRTM_CACHE_DIR", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    assert srtm_cache_dir() == Path("data") / ".cache" / "srtm"
