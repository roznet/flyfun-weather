"""Tests for the GRIB decode process pool (Phase B-3).

Exercises the pool plumbing — startup, dispatch, concurrency, error
propagation, recycling — using the synthetic helpers in
``decode_worker._test_*``. Real GRIB decode is exercised by the existing
test suite via the same dispatch path.

Test isolation note
-------------------
Other tests (notably the API-refresh integration tests) submit pipeline
runs to ``api.packs._refresh_executor`` — a module-level
``ThreadPoolExecutor`` whose worker threads outlive the test that
queued them. Those background threads call ``_dispatch_decode``, which
in turn re-creates ``_DECODE_POOL`` via the lazy-singleton path. The
fixture below drains both executors before yielding so each test starts
from a known-clean state. (Underlying fix would be to drain
``_refresh_executor`` in the API test fixtures themselves; tracked
separately.)
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool

import pytest

import weatherbrief.fetch.grib as grib_pkg
from weatherbrief.fetch.grib import (
    _decode_pool_max_tasks_per_child,
    _decode_pool_workers,
    _dispatch_decode,
    _dispatch_decode_parallel,
    _get_decode_pool,
    decode_worker,
    shutdown_decode_pool,
)


def _drain_background_dispatches(timeout_s: float = 3.0) -> None:
    """Wait for any background pipeline runs to finish their decode work.

    The API ``_refresh_executor`` is the main offender: tests that POST
    ``/refresh`` queue pipeline jobs that keep running after the test
    function returns. Submitting one no-op per worker slot and waiting
    for them ensures every earlier-queued job has at least started;
    combined with the poll-and-shutdown loop below, this gives the pool
    time to settle.

    Reads ``_refresh_executor._max_workers`` rather than hardcoding so
    we don't silently regress if the executor's worker count changes.
    """
    try:
        from weatherbrief.api.packs import _refresh_executor
    except Exception:
        return
    # Private attribute, but stable across all CPython 3.x stdlib versions.
    # Fallback to 2 (the documented current setting) if it ever moves.
    n_workers = getattr(_refresh_executor, "_max_workers", 2)
    fillers = [_refresh_executor.submit(lambda: None) for _ in range(n_workers)]
    wait(fillers, timeout=timeout_s)


def _settle_pool_to_none(timeout_s: float = 2.0) -> bool:
    """Repeatedly shut down the pool until ``_DECODE_POOL`` stays None.

    Caller is responsible for preventing concurrent re-creation — set
    ``GRIB_DECODE_WORKERS=0`` (or otherwise gate ``_get_decode_pool``)
    before calling. Without that gate, a background thread that calls
    ``_dispatch_decode`` between our shutdown and the next check will
    re-create the pool and the loop will spin until the deadline.
    """
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        shutdown_decode_pool()
        if grib_pkg._DECODE_POOL is None:
            return True
        time.sleep(0.05)
    return grib_pkg._DECODE_POOL is None


@pytest.fixture(autouse=True)
def _settle_pool(monkeypatch):
    """Settle the decode pool to a known-clean state before AND after each test.

    Strategy: temporarily set ``GRIB_DECODE_WORKERS=0`` so any concurrent
    ``_get_decode_pool`` call from a leaked background thread takes the
    in-process fallback (returns None) instead of spawning a fresh pool.
    Each test then sets its own ``GRIB_DECODE_WORKERS`` value via its own
    ``monkeypatch.setenv``, which overrides this default.
    """
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "0")
    _drain_background_dispatches()
    _settle_pool_to_none()
    yield
    shutdown_decode_pool()


def test_pool_lazy_startup(monkeypatch):
    """Pool stays None until something actually dispatches a decode."""
    # Settle phase: keep workers=0 (from fixture) so any racing background
    # dispatch hits the in-process fallback. Verify pool is None.
    assert grib_pkg._DECODE_POOL is None, (
        "fixture should have settled pool to None; "
        "see _drain_background_dispatches docstring for leak source"
    )

    # Now flip on the pool and verify lazy creation.
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
    _dispatch_decode("_test_echo", "ping")
    assert grib_pkg._DECODE_POOL is not None, "dispatch should have created the pool"


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


@pytest.mark.skipif(
    (os.cpu_count() or 1) < 2,
    reason="requires at least 2 logical CPUs to actually parallelise",
)
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


def test_pool_auto_resets_after_worker_crash(monkeypatch):
    """A SIGKILL'd worker raises BrokenProcessPool; the next dispatch
    automatically gets a fresh pool — no manual shutdown required.

    This guards the recovery path added in response to PR #104 review:
    without it, one bad worker would poison every subsequent decode
    until the app restarts.
    """
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
    shutdown_decode_pool()
    # First call warms the pool.
    assert _dispatch_decode("_test_echo", "ok") == "ok"
    broken_pool = grib_pkg._DECODE_POOL
    assert broken_pool is not None

    # Crash a worker mid-call — BrokenProcessPool propagates to caller.
    with pytest.raises((BrokenProcessPool, OSError)):
        _dispatch_decode("_test_crash")

    # The except-clause inside _dispatch_decode should have torn the pool down.
    assert grib_pkg._DECODE_POOL is None, "broken pool should have been cleared"

    # Next dispatch lazily creates a fresh pool, no app restart required.
    assert _dispatch_decode("_test_echo", "recovered") == "recovered"
    assert grib_pkg._DECODE_POOL is not None
    assert grib_pkg._DECODE_POOL is not broken_pool


def test_pool_auto_resets_on_worker_hang(monkeypatch):
    """A hung worker triggers TimeoutError; the pool is torn down (wait=False
    so we don't block on the still-running worker), and the next dispatch
    lazily creates a fresh pool.

    Without the timeout, ``future.result()`` would block the calling thread
    indefinitely — eventually starving uvicorn's worker thread pool.
    """
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
    # Warm with a generous timeout so the spawn cost doesn't trip the test.
    monkeypatch.setenv("GRIB_DECODE_TIMEOUT_S", "30")
    shutdown_decode_pool()
    assert _dispatch_decode("_test_echo", "warm") == "warm"
    hung_pool = grib_pkg._DECODE_POOL
    assert hung_pool is not None

    # Now drop the timeout for the hang call.
    monkeypatch.setenv("GRIB_DECODE_TIMEOUT_S", "0.5")
    t0 = time.perf_counter()
    with pytest.raises(TimeoutError):
        _dispatch_decode("_test_hang", 30.0)  # would hang far longer than timeout
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.5, f"timeout fired in {elapsed:.2f}s, should be ~0.5s"
    assert grib_pkg._DECODE_POOL is None, "stuck pool should have been cleared"

    # Recovery: next dispatch lazily creates a fresh pool. Bump the timeout
    # back up so the new worker's spawn cost has room.
    monkeypatch.setenv("GRIB_DECODE_TIMEOUT_S", "30")
    assert _dispatch_decode("_test_echo", "post-hang") == "post-hang"
    assert grib_pkg._DECODE_POOL is not None
    assert grib_pkg._DECODE_POOL is not hung_pool


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


def test_max_tasks_per_child_default(monkeypatch):
    """Recycling is disabled by default — the 2026-05-17 incident showed the
    CPython recycle race causes the pool to hang when both workers retire
    near-simultaneously at the cap. Re-enable via env var only if cfgrib
    native-memory growth becomes a problem again."""
    monkeypatch.delenv("GRIB_DECODE_MAX_TASKS_PER_CHILD", raising=False)
    assert _decode_pool_max_tasks_per_child() is None


def test_max_tasks_per_child_override(monkeypatch):
    monkeypatch.setenv("GRIB_DECODE_MAX_TASKS_PER_CHILD", "10")
    assert _decode_pool_max_tasks_per_child() == 10


def test_max_tasks_per_child_disabled_via_zero(monkeypatch):
    """Zero (or negative) explicitly disables recycling — same behaviour as
    the default but expressed via env var."""
    monkeypatch.setenv("GRIB_DECODE_MAX_TASKS_PER_CHILD", "0")
    assert _decode_pool_max_tasks_per_child() is None
    monkeypatch.setenv("GRIB_DECODE_MAX_TASKS_PER_CHILD", "-1")
    assert _decode_pool_max_tasks_per_child() is None


def test_max_tasks_per_child_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("GRIB_DECODE_MAX_TASKS_PER_CHILD", "not_a_number")
    assert _decode_pool_max_tasks_per_child() is None


def test_pool_recycles_workers_after_max_tasks(monkeypatch):
    """Once a worker hits ``max_tasks_per_child`` it exits and is replaced.

    Verified by counting distinct worker PIDs across N+2 dispatches with
    ``max_tasks_per_child=N`` and ``max_workers=1``: at least 2 distinct
    PIDs must appear, proving the worker was recycled.
    """
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "1")
    monkeypatch.setenv("GRIB_DECODE_MAX_TASKS_PER_CHILD", "3")
    shutdown_decode_pool()

    pids = {_dispatch_decode("_test_pid") for _ in range(6)}
    assert len(pids) >= 2, (
        f"max_tasks_per_child=3 with 6 dispatches should recycle the worker; "
        f"saw only {len(pids)} distinct PIDs ({pids})"
    )


def test_parallel_dispatch_preserves_order_and_in_process_fallback(monkeypatch):
    """``_dispatch_decode_parallel`` returns results in input order — both
    via the pool and via the in-process fallback (workers=0)."""
    # Pool path
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
    shutdown_decode_pool()
    jobs = [("_test_echo", (i,)) for i in range(5)]
    assert _dispatch_decode_parallel(jobs) == [0, 1, 2, 3, 4]

    # In-process fallback path
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "0")
    shutdown_decode_pool()
    assert _dispatch_decode_parallel(jobs) == [0, 1, 2, 3, 4]
    # Empty batch is a no-op
    assert _dispatch_decode_parallel([]) == []


@pytest.mark.skipif(
    (os.cpu_count() or 1) < 2,
    reason="requires at least 2 logical CPUs to actually parallelise 2-way",
)
def test_parallel_dispatch_runs_two_jobs_in_parallel(monkeypatch):
    """Companion 2-CPU regression for issue #133.

    The 4-way test below skips on 2-CPU CI runners, leaving the core
    regression unverified there. With workers=2 and two CPU-bound jobs
    of duration ``D``, total wall time should be close to ``D`` (not
    ``2D``) — the same bug surface as the 4-way case.
    """
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
    shutdown_decode_pool()
    pool = _get_decode_pool()
    assert pool is not None

    warm = [pool.submit(decode_worker._test_echo, None, 0.05) for _ in range(2)]
    for f in warm:
        f.result()

    duration = 0.5
    jobs = [("_test_busy_loop", (duration,)) for _ in range(2)]

    t0 = time.perf_counter()
    results = _dispatch_decode_parallel(jobs)
    elapsed = time.perf_counter() - t0

    assert len(results) == 2
    # Serial would take ~1.0s; parallel should be ~0.5s.
    assert elapsed < 0.9, (
        f"Two parallel busy-loops of {duration}s took {elapsed:.2f}s — "
        "looks serialised, not parallel (issue #133 regression)"
    )


@pytest.mark.skipif(
    (os.cpu_count() or 1) < 4,
    reason="requires at least 4 logical CPUs to actually parallelise 4-way",
)
def test_parallel_dispatch_runs_four_jobs_in_parallel(monkeypatch):
    """Regression for issue #133: a 4-way parallel dispatch should finish
    in ~D wall-clock, not 4D — the bug was that sequential ``_dispatch_decode``
    calls in a ``for`` loop only ever exercised one worker even with workers>=2.

    With workers=4 and four CPU-bound jobs of duration ``D``, total wall time
    should be close to ``D``. We allow generous slack for spawn-time and
    scheduling jitter, but flag if it sneaks into "obviously serialised"
    territory (>= 2D).
    """
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "4")
    shutdown_decode_pool()
    pool = _get_decode_pool()
    assert pool is not None

    # Pre-warm all four workers — ProcessPoolExecutor spawns lazily.
    warm = [pool.submit(decode_worker._test_echo, None, 0.05) for _ in range(4)]
    for f in warm:
        f.result()

    duration = 0.5
    jobs = [("_test_busy_loop", (duration,)) for _ in range(4)]

    t0 = time.perf_counter()
    results = _dispatch_decode_parallel(jobs)
    elapsed = time.perf_counter() - t0

    assert len(results) == 4
    # Serial would take ~2.0s; parallel should be ~0.5s. Generous bound to
    # allow for jitter, but well below serial-ish territory.
    assert elapsed < 1.5, (
        f"Four parallel busy-loops of {duration}s took {elapsed:.2f}s — "
        "looks serialised, not parallel (issue #133 regression)"
    )


def test_parallel_dispatch_error_propagation(monkeypatch):
    """Worker exceptions surface to the caller and cancel pending futures.

    Three jobs with the failure in the middle exercises the cancel path —
    a 2-job batch with the failure last leaves no pending futures, so the
    cancel logic is never proven to run. The pool itself is left intact:
    a non-fatal worker exception doesn't mean the pool is broken.
    """
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
    shutdown_decode_pool()
    jobs = [
        ("_test_echo", ("ok",)),
        ("_test_echo", (None, 0.0, "boom")),
        ("_test_echo", ("also-ok",)),
    ]
    with pytest.raises(RuntimeError, match="boom"):
        _dispatch_decode_parallel(jobs)
    # Pool stays alive — exception was at job level, not pool level.
    assert grib_pkg._DECODE_POOL is not None


def test_worker_registry_captures_vanished_pid_exitcode(monkeypatch, caplog):
    """Registry holds Process refs after the pool drops dead workers, so the
    hang diag can log ``exitcode`` (negative = signal that killed the worker).

    This is the missing signal in prod hangs where ``pool._processes`` is
    empty by the time the timeout fires — without our own reference we'd
    lose the exit status entirely.
    """
    import logging
    from weatherbrief.fetch.grib import _diag_snapshot_vanished_workers

    monkeypatch.setenv("GRIB_DECODE_WORKERS", "1")
    shutdown_decode_pool()
    assert _dispatch_decode("_test_echo", "warm") == "warm"
    pool = grib_pkg._DECODE_POOL
    assert pool is not None
    # Capture the warm worker's PID before we crash it. The dispatch above
    # also triggered _diag_register_workers, so the registry holds the ref.
    warm_pid = _dispatch_decode("_test_pid")
    assert warm_pid in grib_pkg._DECODE_WORKER_REGISTRY

    # Crash the worker via SIGKILL — exitcode becomes -9.
    with pytest.raises((BrokenProcessPool, OSError)):
        _dispatch_decode("_test_crash")

    # Pool was torn down; the manager joined the dead worker and dropped it
    # from _processes. Our registry should still have the Process ref.
    proc = grib_pkg._DECODE_WORKER_REGISTRY.get(warm_pid)
    assert proc is not None, "registry must retain Process ref after pool teardown"

    # Call the snapshot helper directly with no live pool. The vanished-
    # worker branch should log the SIGKILL exit code.
    caplog.set_level(logging.ERROR, logger="weatherbrief.fetch.grib")
    _diag_snapshot_vanished_workers(pool=None, current_pids=[])
    matching = [
        rec for rec in caplog.records
        if f"vanished-worker pid={warm_pid}" in rec.getMessage()
    ]
    assert matching, f"no vanished-worker log line for pid={warm_pid}"
    msg = matching[0].getMessage()
    assert "exitcode=-9" in msg, f"expected exitcode=-9 in: {msg}"
    assert "SIGKILL" in msg, f"expected SIGKILL signal name in: {msg}"


def test_worker_registry_cleared_on_pool_reset(monkeypatch):
    """A fresh pool starts with an empty worker registry — otherwise stale
    PIDs from the previous lifetime would falsely appear as 'vanished' in
    the next hang diag."""
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "1")
    shutdown_decode_pool()
    assert _dispatch_decode("_test_echo", "warm") == "warm"
    assert grib_pkg._DECODE_WORKER_REGISTRY, "first dispatch should register a worker"

    shutdown_decode_pool()
    # Force a fresh pool creation; _diag_reset_pool_counters clears the registry.
    _get_decode_pool()
    assert grib_pkg._DECODE_WORKER_REGISTRY == {}, (
        "registry must be empty after pool recreation; "
        f"saw {grib_pkg._DECODE_WORKER_REGISTRY}"
    )


def test_parallel_dispatch_timeout_resets_pool(monkeypatch):
    """A hung worker in a parallel batch trips ``GRIB_DECODE_TIMEOUT_S``,
    tears down the pool with ``wait=False``, and re-raises ``TimeoutError``.

    Locks in the teardown semantics for the TimeoutError branch: unlike
    a non-fatal worker exception, a stuck worker means the pool is
    poisoned (the parent can't safely reuse a worker that may still be
    holding ECCODES state). Mirrors ``test_pool_auto_resets_on_worker_hang``
    for the parallel dispatch path.
    """
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
    # Warm the pool with a generous timeout first so worker spawn cost
    # doesn't burn the per-batch deadline on the hang call.
    monkeypatch.setenv("GRIB_DECODE_TIMEOUT_S", "30")
    shutdown_decode_pool()
    assert _dispatch_decode_parallel([("_test_echo", ("warm",))]) == ["warm"]

    # Drop the timeout; one of the jobs hangs far longer.
    monkeypatch.setenv("GRIB_DECODE_TIMEOUT_S", "0.5")
    jobs = [
        ("_test_hang", (30.0,)),
        ("_test_echo", ("never-reached",)),
    ]
    t0 = time.perf_counter()
    with pytest.raises(TimeoutError):
        _dispatch_decode_parallel(jobs)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.5, f"timeout fired in {elapsed:.2f}s, should be ~0.5s"
    # Pool was torn down — the TimeoutError branch calls shutdown_decode_pool(wait=False).
    assert grib_pkg._DECODE_POOL is None
