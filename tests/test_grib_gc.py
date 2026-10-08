"""GRIB-stage explicit gc: generation choice and per-site accounting (#704)."""

from __future__ import annotations

import gc
import logging

import pytest

from weatherbrief.fetch import grib


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("WB_GRIB_GC_GENERATION", raising=False)
    monkeypatch.delenv("GRIB_DECODE_WORKERS", raising=False)


def test_young_generations_when_decode_runs_in_the_pool(monkeypatch):
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
    assert grib._grib_gc_generation() == 1


def test_full_collection_when_decode_is_in_process(monkeypatch):
    # GRIB_DECODE_WORKERS=0 puts decode back in this interpreter, where the
    # OOM-era full collection is still what reclaims the per-hour garbage.
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "0")
    assert grib._grib_gc_generation() == 2


@pytest.mark.parametrize("raw, expected", [("0", 0), ("2", 2), (" 1 ", 1)])
def test_env_override(monkeypatch, raw, expected):
    monkeypatch.setenv("WB_GRIB_GC_GENERATION", raw)
    assert grib._grib_gc_generation() == expected


@pytest.mark.parametrize("raw", ["3", "-1", "full"])
def test_invalid_override_falls_back_to_default(monkeypatch, raw):
    monkeypatch.setenv("WB_GRIB_GC_GENERATION", raw)
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
    assert grib._grib_gc_generation() == 1


def test_invalid_override_warns_once(monkeypatch, caplog):
    monkeypatch.setenv("WB_GRIB_GC_GENERATION", "bogus")
    with caplog.at_level(logging.WARNING, logger=grib.logger.name):
        for _ in range(5):
            grib._grib_gc_generation()
    assert sum("WB_GRIB_GC_GENERATION" in r.getMessage() for r in caplog.records) == 1


def test_timer_resolves_generation_once(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(gc, "collect", lambda generation=2: calls.append(generation) or 0)
    monkeypatch.setenv("WB_GRIB_GC_GENERATION", "0")
    timer = grib._GribTimer()
    monkeypatch.setenv("WB_GRIB_GC_GENERATION", "2")
    token = grib._GRIB_TIMER.set(timer)
    try:
        grib._grib_gc("site")
    finally:
        grib._GRIB_TIMER.reset(token)
    assert calls == [0]
    assert timer.gc_generation == 0


def test_grib_gc_collects_at_the_chosen_generation(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(gc, "collect", lambda generation=2: calls.append(generation) or 0)
    monkeypatch.setenv("WB_GRIB_GC_GENERATION", "1")
    grib._grib_gc("site")  # no timer active
    timer = grib._GribTimer()
    token = grib._GRIB_TIMER.set(timer)
    try:
        grib._grib_gc("site")
    finally:
        grib._GRIB_TIMER.reset(token)
    assert calls == [1, 1]


def test_timer_accounts_gc_per_site(caplog):
    timer = grib._GribTimer()
    timer.gc("a", 0)
    timer.gc("a", 0)
    timer.gc("b", 1)
    assert timer.gc_count == 3
    assert set(timer.gc_by_label) == {"a", "b"}
    assert timer.gc_by_label["a"][1] == 2
    with caplog.at_level(logging.INFO, logger=grib.logger.name):
        timer._record_time("x", 0.1)
        timer.log_summary()
    gc_lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("GRIB gc")]
    assert len(gc_lines) == 1
    assert "a=" in gc_lines[0] and "/2 objs=" in gc_lines[0] and "b=" in gc_lines[0]
