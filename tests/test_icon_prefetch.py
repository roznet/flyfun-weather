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
from weatherbrief.fetch.grib.icon_eu_fetch import (
    ICON_EU,
    ICON_EU_CLOUD_DIAG_CACHE_KEY,
    ICON_EU_VARIABLES,
)


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
        variant=ICON_EU,
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
                          session=None, max_workers=8, variant=ICON_EU):
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

    def fake_single_level(init_date, init_hour, fhours, variables=None,
                          session=None, max_workers=8, variant=ICON_EU):
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
    # Cloud-diag fetch also pulls the leading step (fhour 5 = min(6,9) − 1) so
    # the first window hour can de-accumulate rain_con against it (#421).
    assert sorted(fh for fh, _ in tracker["diag_calls"]) == [5, 6, 9]
    for fh in (6, 9):
        for var in ICON_EU_VARIABLES:
            ck = cache_key(fh, f"ICON_EU_{var.upper()}")
            assert get_cached(tmp_path, ck) == f"{fh}-{var}".encode()
    for fh in (5, 6, 9):
        assert (
            get_cached(tmp_path, cache_key(fh, ICON_EU_CLOUD_DIAG_CACHE_KEY))
            == f"diag-{fh}".encode()
        )


def test_cached_units_skipped(tmp_path, tracker):
    # fhour 6 fully covered by the legacy combined key; one var of fhour 9 cached.
    put_cached(tmp_path, cache_key(6, "ICON_EU_QC_QI_P"), b"legacy")
    put_cached(tmp_path, cache_key(6, ICON_EU_CLOUD_DIAG_CACHE_KEY), b"diag")
    put_cached(tmp_path, cache_key(9, "ICON_EU_QC"), b"have-qc")

    ctx = _make_ctx(tmp_path, forecast_hours=[6, 9])
    _prefetch_icon_eu_data_inner(ctx)

    fetched = {(fh, var) for fh, var, _ in tracker["var_calls"]}
    assert all(fh == 9 for fh, _ in fetched)
    assert (9, "qc") not in fetched
    assert {var for _, var in fetched} == set(ICON_EU_VARIABLES) - {"qc"}
    # fhour 6 diag already cached; the uncached diag steps are the leading
    # step (5) and fhour 9 (#421).
    assert sorted(fh for fh, _ in tracker["diag_calls"]) == [5, 9]
    # The pre-existing cache entry was not overwritten
    assert get_cached(tmp_path, cache_key(9, "ICON_EU_QC")) == b"have-qc"


def test_units_run_concurrently(tmp_path, tracker, monkeypatch):
    monkeypatch.delenv("GRIB_ICON_PREFETCH_WORKERS", raising=False)
    # 11 units (9 vars + diag f6 + leading diag f5) on a 4-worker outer pool,
    # each sleeping 100ms: expected peak is 4. Assert only >= 2 (any overlap at
    # all) so a starved CI host that staggers thread starts can't flake it.
    tracker["delay"] = 0.1
    ctx = _make_ctx(tmp_path, forecast_hours=[6])
    _prefetch_icon_eu_data_inner(ctx)
    assert tracker["peak"] >= 2  # outer pool overlapped units


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


# ---------------------------------------------------------------------------
# abort_if — the warm loop's yield gate (#490, PR #498 review)
# ---------------------------------------------------------------------------


def test_abort_if_yields_before_any_download(tmp_path, tracker):
    """Pending jobs + active gate → abort with False, zero bytes moved."""
    ctx = _make_ctx(tmp_path, forecast_hours=[6])
    completed = _prefetch_icon_eu_data_inner(ctx, abort_if=lambda: True)
    assert completed is False
    assert tracker["var_calls"] == []
    assert tracker["diag_calls"] == []


def test_abort_if_ignored_when_nothing_to_fetch(tmp_path, tracker):
    """A fully-warmed context has no jobs and completes even mid-refresh.

    This is the fast-forward half of the contract: the gate must never block
    free cache hits, or a resumption pass stalls at unit 0 for a whole burst.
    """
    put_cached(tmp_path, cache_key(6, "ICON_EU_QC_QI_P"), b"legacy")
    put_cached(tmp_path, cache_key(6, ICON_EU_CLOUD_DIAG_CACHE_KEY), b"diag")
    # Leading rain_con de-accumulation step (#421) is a job too — cache it.
    put_cached(tmp_path, cache_key(5, ICON_EU_CLOUD_DIAG_CACHE_KEY), b"diag")

    ctx = _make_ctx(tmp_path, forecast_hours=[6])
    completed = _prefetch_icon_eu_data_inner(ctx, abort_if=lambda: True)
    assert completed is True
    assert tracker["var_calls"] == []
    assert tracker["diag_calls"] == []


def test_completed_pass_returns_true(tmp_path, tracker):
    ctx = _make_ctx(tmp_path, forecast_hours=[6])
    assert _prefetch_icon_eu_data_inner(ctx, abort_if=lambda: False) is True
    assert len(tracker["var_calls"]) == len(ICON_EU_VARIABLES)


# ---------------------------------------------------------------------------
# Per-unit gating (#501)
#
# The gate used to be consulted once, before the job list ran. A warm pass one
# job into a flight was then committed to the rest of that flight — ~80 s in
# which its buffers and half the connection pool sit on top of any briefing
# that starts. It is now consulted between every unit.
# ---------------------------------------------------------------------------


#: Units a single-fhour ICON-EU context downloads: one per model-level
#: variable, plus the hour's cloud-diag blob and the leading de-accumulation
#: step (#421).
_UNITS_PER_FHOUR = len(ICON_EU_VARIABLES) + 2


def _gate_open_for(units: int):
    """A gate that stays open for ``units`` downloads, then yields forever.

    Check 0 is the pre-download one; check *k* precedes unit *k*.
    """
    state = {"checks": 0}

    def gate() -> bool:
        fired = state["checks"] >= units
        state["checks"] += 1
        return fired

    return gate


def _dispatched(tracker) -> int:
    return len(tracker["var_calls"]) + len(tracker["diag_calls"])


def test_abort_if_yields_between_units(tmp_path, tracker):
    """A gate that goes active mid-flight stops the pass at the next unit."""
    ctx = _make_ctx(tmp_path, forecast_hours=[6])
    completed = _prefetch_icon_eu_data_inner(
        ctx, outer_workers=1, abort_if=_gate_open_for(1),
    )
    assert completed is False
    assert _dispatched(tracker) == 1


def test_units_finished_before_the_gate_fires_stay_cached(tmp_path, tracker):
    """Partial progress is the whole reason mid-flight yielding is safe.

    Every finished unit is its own atomic ``put_cached``, so the resumed pass
    rebuilds a shorter job list and fast-forwards instead of refetching.
    """
    ctx = _make_ctx(tmp_path, forecast_hours=[6])
    assert _prefetch_icon_eu_data_inner(
        ctx, outer_workers=1, abort_if=_gate_open_for(2),
    ) is False
    assert _dispatched(tracker) == 2

    # Resume with the gate down: the finished units are is_cached skips.
    assert _prefetch_icon_eu_data_inner(
        ctx, outer_workers=1, abort_if=lambda: False,
    ) is True

    # Every unit fetched exactly once across the two passes — nothing dropped
    # by the interruption, nothing paid for twice.
    assert _dispatched(tracker) == _UNITS_PER_FHOUR
    assert len(set(tracker["var_calls"])) == len(tracker["var_calls"])
    assert len(set(tracker["diag_calls"])) == len(tracker["diag_calls"])


def test_serial_path_runs_every_unit_when_the_gate_stays_open(tmp_path, tracker):
    """Guard the off-by-one: an open gate must not cost a unit."""
    ctx = _make_ctx(tmp_path, forecast_hours=[6])
    assert _prefetch_icon_eu_data_inner(
        ctx, outer_workers=1, abort_if=lambda: False,
    ) is True
    assert _dispatched(tracker) == _UNITS_PER_FHOUR


def test_abort_if_gates_the_parallel_path_too(tmp_path, tracker):
    """``outer > 1`` submits incrementally, so the gate governs it as well.

    Warm callers pass ``outer_workers=1`` today and take the serial branch, but
    a gate that silently stopped working if that budget were raised back to 2
    is exactly the regression #501 exists to prevent.
    """
    ctx = _make_ctx(tmp_path, forecast_hours=[6])
    completed = _prefetch_icon_eu_data_inner(
        ctx, outer_workers=4, abort_if=_gate_open_for(3),
    )
    assert completed is False
    assert _dispatched(tracker) == 3
