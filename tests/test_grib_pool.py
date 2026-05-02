"""Tests for the GRIB decode process pool (Phase B-3).

Exercises the pool plumbing — startup, dispatch, concurrency, error
propagation, recycling — using the synthetic helpers in
``decode_worker._test_*``. Real GRIB decode is exercised by the existing
test suite via the same dispatch path.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import pytest

from weatherbrief.fetch.grib import (
    _decode_pool_workers,
    _dispatch_decode,
    _get_decode_pool,
    shutdown_decode_pool,
)
from weatherbrief.fetch.grib import decode_worker


@pytest.fixture(autouse=True)
def _shutdown_after():
    """Always tear the pool down between tests so each test starts fresh."""
    yield
    shutdown_decode_pool()


def test_pool_lazy_startup(monkeypatch):
    """Pool isn't created until the first dispatch."""
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
    shutdown_decode_pool()  # ensure clean
    pool = _get_decode_pool()
    assert pool is not None


def test_pool_disabled_when_workers_zero(monkeypatch):
    """Setting GRIB_DECODE_WORKERS=0 falls back to in-process decode."""
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "0")
    shutdown_decode_pool()
    assert _get_decode_pool() is None
    assert _decode_pool_workers() == 0
    # In-process path still returns the result.
    result = _dispatch_decode("_test_echo", {"hello": "world"})
    assert result == {"hello": "world"}


def test_dispatch_returns_result(monkeypatch):
    """Cold start: a single dispatch starts the pool and returns the result."""
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
    shutdown_decode_pool()
    result = _dispatch_decode("_test_echo", {"a": 1, "b": [2, 3]})
    assert result == {"a": 1, "b": [2, 3]}


def test_two_concurrent_submits_run_in_parallel(monkeypatch):
    """Two concurrent decode jobs should run in parallel, not serialise.

    With workers=2, two CPU-bound jobs of duration ``D`` should complete in
    wall-clock close to ``D`` (parallel) rather than ``2D`` (serial). We
    leave generous slack for spawn-time and scheduling jitter.
    """
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
    shutdown_decode_pool()
    pool = _get_decode_pool()
    assert pool is not None

    # Pre-warm BOTH workers — ProcessPoolExecutor spawns lazily, and a single
    # warm submit only boots one worker. Submit two long-enough jobs in
    # parallel so both workers spawn and finish init before the timed run.
    warm = [pool.submit(decode_worker._test_echo, None, 0.05) for _ in range(2)]
    for f in warm:
        f.result()

    duration = 0.5
    t0 = time.perf_counter()
    f1 = pool.submit(decode_worker._test_busy_loop, duration)
    f2 = pool.submit(decode_worker._test_busy_loop, duration)
    f1.result()
    f2.result()
    elapsed = time.perf_counter() - t0

    # Serial would take ~1.0s; parallel should be ~0.5s. Allow up to 0.85s
    # for jitter — but flag if it sneaks past serial-ish territory.
    assert elapsed < 0.85, (
        f"Two parallel busy-loops of {duration}s took {elapsed:.2f}s — "
        "looks serialised, not parallel"
    )


def test_error_propagation(monkeypatch):
    """Exceptions raised in the worker propagate via Future.result()."""
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
    shutdown_decode_pool()
    with pytest.raises(RuntimeError, match="bang"):
        _dispatch_decode("_test_echo", None, 0.0, "bang")


def test_pool_recovers_after_worker_crash(monkeypatch):
    """A SIGKILL'd worker breaks the pool; shutdown + redispatch recovers."""
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
    shutdown_decode_pool()
    # First call warms the pool.
    assert _dispatch_decode("_test_echo", "ok") == "ok"

    # Crash a worker mid-call — the pool will surface BrokenProcessPool.
    with pytest.raises((BrokenProcessPool, OSError)):
        _dispatch_decode("_test_crash")

    # After teardown + a fresh dispatch, the new pool services jobs again.
    shutdown_decode_pool()
    assert _dispatch_decode("_test_echo", "recovered") == "recovered"


def test_concurrent_thread_dispatch(monkeypatch):
    """Multiple parent threads can submit concurrently without races."""
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
    shutdown_decode_pool()
    _dispatch_decode("_test_echo", None)  # warm

    n = 8
    with ThreadPoolExecutor(max_workers=n) as tpool:
        futures = [
            tpool.submit(_dispatch_decode, "_test_echo", i, 0.05)
            for i in range(n)
        ]
        results = sorted(f.result() for f in futures)
    assert results == list(range(n))
