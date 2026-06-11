"""ICON-EU prefetch: parallel (fhour, variable) download units.

The prefetch loop used to download one (fhour, variable) unit at a time —
the dominant cause of the 130-226s cold-cache fetch tail in production.
These tests cover the parallelized loop: every uncached unit is fetched and
cached, cache hits (per-variable and legacy combined) are skipped, units
actually overlap, one failing unit doesn't sink the rest, and
GRIB_ICON_PREFETCH_WORKERS=1 restores the serial path.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

import weatherbrief.fetch.grib as grib_mod
import weatherbrief.fetch.grib.icon_eu_fetch as icon_fetch_mod
from weatherbrief.fetch.grib import _IconEuContext, _prefetch_icon_eu_data_inner
from weatherbrief.fetch.grib.cache import cache_key, get_cached, put_cached
from weatherbrief.fetch.grib.icon_eu_fetch import ICON_EU_VARIABLES


def _make_ctx(tmp_path: Path, forecast_hours: list[int]) -> _IconEuContext:
    return _IconEuContext(
        init_date="20260611",
        init_hour=6,
        forecast_hours=forecast_hours,
        run_dir=tmp_path,
        levels=[60, 61, 62],
        point_lats=[48.0],
        point_lons=[2.0],
        session=None,
    )


@pytest.fixture
def tracker(monkeypatch):
    """Mock both fetch functions; track call args and peak concurrency."""
    state = {
        "var_calls": [],
        "diag_calls": [],
        "active": 0,
        "peak": 0,
        "lock": threading.Lock(),
        "fail_vars": set(),
        "delay": 0.0,
    }

    def _enter():
        with state["lock"]:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        if state["delay"]:
            time.sleep(state["delay"])

    def _exit():
        with state["lock"]:
            state["active"] -= 1

    def fake_per_variable(init_date, init_hour, fhour, levels, variables,
                          session=None, max_workers=8):
        _enter()
        try:
            (var,) = variables
            with state["lock"]:
                state["var_calls"].append((fhour, var, max_workers))
            if (fhour, var) in state["fail_vars"]:
                raise RuntimeError("boom")
            return {var: f"{fhour}-{var}".encode()}
        finally:
            _exit()

    def fake_single_level(init_date, init_hour, fhours, session=None, max_workers=8):
        _enter()
        try:
            (fhour,) = fhours
            with state["lock"]:
                state["diag_calls"].append((fhour, max_workers))
            return {fhour: f"diag-{fhour}".encode()}
        finally:
            _exit()

    monkeypatch.setattr(icon_fetch_mod, "fetch_icon_eu_per_variable", fake_per_variable)
    monkeypatch.setattr(icon_fetch_mod, "fetch_icon_eu_single_level", fake_single_level)
    return state


def test_all_uncached_units_fetched_and_cached(tmp_path, tracker):
    ctx = _make_ctx(tmp_path, forecast_hours=[6, 9])
    _prefetch_icon_eu_data_inner(ctx)

    assert sorted(tracker["var_calls"]) == sorted(
        (fh, var, tracker["var_calls"][0][2])
        for fh in (6, 9) for var in ICON_EU_VARIABLES
    )
    assert sorted(fh for fh, _ in tracker["diag_calls"]) == [6, 9]
    for fh in (6, 9):
        for var in ICON_EU_VARIABLES:
            ck = cache_key(fh, f"ICON_EU_{var.upper()}")
            assert get_cached(tmp_path, ck) == f"{fh}-{var}".encode()
        assert get_cached(tmp_path, cache_key(fh, "ICON_EU_CLOUD_DIAG")) == f"diag-{fh}".encode()


def test_cached_units_skipped(tmp_path, tracker):
    # fhour 6 fully covered by the legacy combined key; one var of fhour 9 cached.
    put_cached(tmp_path, cache_key(6, "ICON_EU_QC_QI_P"), b"legacy")
    put_cached(tmp_path, cache_key(6, "ICON_EU_CLOUD_DIAG"), b"diag")
    put_cached(tmp_path, cache_key(9, "ICON_EU_QC"), b"have-qc")

    ctx = _make_ctx(tmp_path, forecast_hours=[6, 9])
    _prefetch_icon_eu_data_inner(ctx)

    fetched = {(fh, var) for fh, var, _ in tracker["var_calls"]}
    assert all(fh == 9 for fh, _ in fetched)
    assert (9, "qc") not in fetched
    assert {var for _, var in fetched} == set(ICON_EU_VARIABLES) - {"qc"}
    assert [fh for fh, _ in tracker["diag_calls"]] == [9]
    # The pre-existing cache entry was not overwritten
    assert get_cached(tmp_path, cache_key(9, "ICON_EU_QC")) == b"have-qc"


def test_units_run_concurrently(tmp_path, tracker, monkeypatch):
    monkeypatch.delenv("GRIB_ICON_PREFETCH_WORKERS", raising=False)
    tracker["delay"] = 0.05
    ctx = _make_ctx(tmp_path, forecast_hours=[6])
    _prefetch_icon_eu_data_inner(ctx)
    assert tracker["peak"] > 1  # outer pool overlapped units


def test_serial_env_knob(tmp_path, tracker, monkeypatch):
    monkeypatch.setenv("GRIB_ICON_PREFETCH_WORKERS", "1")
    tracker["delay"] = 0.005
    ctx = _make_ctx(tmp_path, forecast_hours=[6])
    _prefetch_icon_eu_data_inner(ctx)
    assert tracker["peak"] == 1
    assert len(tracker["var_calls"]) == len(ICON_EU_VARIABLES)


def test_failing_unit_does_not_block_others(tmp_path, tracker):
    tracker["fail_vars"].add((6, "qc"))
    ctx = _make_ctx(tmp_path, forecast_hours=[6])
    _prefetch_icon_eu_data_inner(ctx)  # must not raise

    assert get_cached(tmp_path, cache_key(6, "ICON_EU_QC")) is None
    for var in set(ICON_EU_VARIABLES) - {"qc"}:
        assert get_cached(tmp_path, cache_key(6, f"ICON_EU_{var.upper()}")) is not None


def test_worker_env_parsing(monkeypatch):
    monkeypatch.setenv("GRIB_ICON_PREFETCH_WORKERS", "6")
    assert grib_mod._icon_prefetch_workers() == 6
    monkeypatch.setenv("GRIB_ICON_PREFETCH_WORKERS", "garbage")
    assert grib_mod._icon_prefetch_workers() == 4
    monkeypatch.delenv("GRIB_ICON_PREFETCH_WORKERS")
    assert grib_mod._icon_prefetch_workers() == 4
