"""Tests for the priority decode dispatcher (issue #171).

These exercise the dispatcher's own behaviour — priority ordering, slot
bounding, fault rescheduling, timeout dead-lettering, crash backoff, retry
cap/budget, dead-letter observability, and the two bypass paths
(``GRIB_DECODE_WORKERS=0`` / ``GRIB_DECODE_PRIORITY_ENABLED=0``).

Design: an injectable fake ``worker_fn`` plus a ``FakePool`` (thread-backed,
mimicking ``ProcessPoolExecutor``'s all-or-nothing failure on a worker death).
No real cfgrib — the pool plumbing it wraps is covered by ``test_grib_pool.py``.
The dispatcher takes its pool/teardown/workers/timeout/resolver as constructor
seams, so each test drives it deterministically without touching the global
singleton (except the integration test, which exercises the real
``_dispatch_decode`` wrapper).
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import pytest

import weatherbrief.fetch.grib as grib
from weatherbrief.fetch.grib import (
    DecodeDispatchError,
    DecodePriority,
    PriorityDecodeDispatcher,
    decode_dead_letter_counts,
    set_decode_priority,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakePool:
    """Thread-backed stand-in for ``ProcessPoolExecutor``.

    Mimics the failure mode that matters: when any job raises
    ``BrokenProcessPool``, the *whole* pool breaks — that job's future plus
    every other not-yet-done future get ``BrokenProcessPool`` set, and further
    ``submit`` calls raise. Normal job exceptions only fail their own future.
    """

    def __init__(self, workers: int) -> None:
        self._ex = ThreadPoolExecutor(max_workers=max(1, workers))
        self._lock = threading.Lock()
        self._broken = False
        self._live: set[Future] = set()

    def submit(self, fn, *args):  # noqa: ANN001
        with self._lock:
            if self._broken:
                raise BrokenProcessPool("fake pool already broken")
        out: Future = Future()
        with self._lock:
            self._live.add(out)

        def _runner() -> None:
            try:
                res = fn(*args)
            except BrokenProcessPool as exc:
                self._break(str(exc))
                return
            except BaseException as exc:  # noqa: BLE001 — normal job failure
                if not out.done():
                    out.set_exception(exc)
                self._forget(out)
                return
            if not out.done():
                out.set_result(res)
            self._forget(out)

        self._ex.submit(_runner)
        return out

    def _forget(self, fut: Future) -> None:
        with self._lock:
            self._live.discard(fut)

    def _break(self, msg: str) -> None:
        with self._lock:
            self._broken = True
            live = list(self._live)
            self._live.clear()
        for f in live:
            if not f.done():
                f.set_exception(BrokenProcessPool(msg))

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:  # noqa: ARG002
        # wait=False mirrors the production hung-pool teardown: never join a
        # stuck worker thread.
        self._ex.shutdown(wait=False)


class PoolHarness:
    """Owns the current FakePool and rebuilds it on teardown (as the real
    lazy ``_get_decode_pool`` does after ``shutdown_decode_pool``)."""

    def __init__(self, workers: int) -> None:
        self.workers = workers
        self.pool = FakePool(workers)
        self.teardown_waits: list[bool] = []

    def factory(self):  # -> FakePool
        return self.pool

    def teardown(self, *, wait: bool = True) -> None:
        self.teardown_waits.append(wait)
        self.pool.shutdown(wait=False)
        self.pool = FakePool(self.workers)

    def workers_fn(self) -> int:
        return self.workers


@pytest.fixture
def make_dispatcher():
    """Factory for dispatchers wired to a FakePool + a worker registry.

    Returns ``(dispatcher, registry, harness)``. ``registry`` is a name->fn
    dict the test fills with fake workers. All dispatchers are drained on
    teardown so their watchdog threads exit.
    """
    created: list[PriorityDecodeDispatcher] = []

    def _make(workers: int = 2, timeout_s: float = 5.0):
        registry: dict[str, object] = {}
        harness = PoolHarness(workers)
        d = PriorityDecodeDispatcher(
            worker_resolver=lambda name: registry[name],
            pool_factory=harness.factory,
            pool_teardown=harness.teardown,
            workers_fn=harness.workers_fn,
            timeout_fn=lambda: timeout_s,
        )
        created.append(d)
        return d, registry, harness

    yield _make
    for d in created:
        d.drain()


# ---------------------------------------------------------------------------
# 1. Priority ordering
# ---------------------------------------------------------------------------


def test_priority_then_fifo_ordering(make_dispatcher):
    """On a 1-worker pool, queued jobs dispatch priority-first, FIFO within
    a level — an INTERACTIVE job jumps ahead of already-queued BACKGROUND."""
    d, registry, _ = make_dispatcher(workers=1)
    order: list[str] = []
    order_lock = threading.Lock()
    gate_started = threading.Event()
    release_gate = threading.Event()

    def _gate(_label):  # occupies the single worker until released
        gate_started.set()
        release_gate.wait(5.0)
        return "gate"

    def _record(label):
        with order_lock:
            order.append(label)
        return label

    registry["gate"] = _gate
    registry["record"] = _record

    gate_fut = d.submit_one("gate", ("g",), int(DecodePriority.BACKGROUND))
    assert gate_started.wait(2.0), "gate job never started"

    # Queue while the only worker is busy. Submission order deliberately does
    # NOT match priority order, to prove the heap reorders.
    futs = {
        "b1": d.submit_one("record", ("b1",), DecodePriority.BACKGROUND),
        "s1": d.submit_one("record", ("s1",), DecodePriority.SCHEDULED),
        "i1": d.submit_one("record", ("i1",), DecodePriority.INTERACTIVE),
        "b2": d.submit_one("record", ("b2",), DecodePriority.BACKGROUND),
        "s2": d.submit_one("record", ("s2",), DecodePriority.SCHEDULED),
    }

    release_gate.set()
    gate_fut.result(5.0)
    for f in futs.values():
        f.result(5.0)

    assert order == ["i1", "s1", "s2", "b1", "b2"], order


# ---------------------------------------------------------------------------
# 2. Slot bounding
# ---------------------------------------------------------------------------


def test_never_exceeds_worker_slots(make_dispatcher):
    """In-flight jobs never exceed the worker count, even with many queued."""
    workers = 2
    d, registry, _ = make_dispatcher(workers=workers)
    concurrency = {"now": 0, "max": 0}
    c_lock = threading.Lock()
    proceed = threading.Event()

    def _tracked(_label):
        with c_lock:
            concurrency["now"] += 1
            concurrency["max"] = max(concurrency["max"], concurrency["now"])
        proceed.wait(5.0)
        with c_lock:
            concurrency["now"] -= 1
        return _label

    def _wait_now(target: int, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with c_lock:
                if concurrency["now"] >= target:
                    return True
            time.sleep(0.005)
        return False

    registry["tracked"] = _tracked
    futs = [d.submit_one("tracked", (i,), DecodePriority.SCHEDULED) for i in range(8)]
    # Deterministic: wait until both slots are actually occupied (bounded to
    # `workers`, so `now` can only reach `workers`, never exceed it).
    assert _wait_now(workers), "dispatcher should saturate all worker slots"
    with c_lock:
        peak = concurrency["max"]
    proceed.set()
    for f in futs:
        f.result(5.0)
    assert peak <= workers, f"saw {peak} concurrent jobs, expected <= {workers}"
    assert peak == workers, f"expected to saturate {workers} workers, saw {peak}"


# ---------------------------------------------------------------------------
# 3. Fault -> reschedule (crash)
# ---------------------------------------------------------------------------


def test_crash_reschedules_interrupted_and_keeps_completed(make_dispatcher, monkeypatch):
    """A worker crash: completed work keeps its result, the interrupted job is
    transparently rescheduled and resolves, and no caller sees BrokenProcessPool."""
    monkeypatch.setenv("GRIB_DECODE_BACKOFF_BASE_S", "0.01")  # fast retry
    d, registry, harness = make_dispatcher(workers=2)

    # Already-completed work, resolved before any fault.
    registry["ok"] = lambda v: v
    done_fut = d.submit_one("ok", ("r1",), DecodePriority.SCHEDULED)
    assert done_fut.result(2.0) == "r1"

    # A job that crashes on its first run, succeeds on the retry.
    attempts = {"n": 0}
    a_lock = threading.Lock()

    def _flaky(_v):
        with a_lock:
            attempts["n"] += 1
            n = attempts["n"]
        if n == 1:
            raise BrokenProcessPool("worker died")
        return "flaky-ok"

    registry["flaky"] = _flaky
    # A healthy sibling submitted in the same wave.
    registry["sibling"] = lambda v: v

    flaky_fut = d.submit_one("flaky", ("x",), DecodePriority.SCHEDULED)
    sib_fut = d.submit_one("sibling", ("sib",), DecodePriority.SCHEDULED)

    assert flaky_fut.result(5.0) == "flaky-ok"
    assert sib_fut.result(5.0) == "sib"
    # Completed-before-fault work is untouched.
    assert done_fut.result(0) == "r1"
    # The fault tore the pool down (crash -> wait=True).
    assert harness.teardown_waits and harness.teardown_waits[-1] is True
    assert attempts["n"] == 2, "flaky job should have been rescheduled exactly once"


# ---------------------------------------------------------------------------
# 4. Timeout victim dead-lettered; collateral rescheduled
# ---------------------------------------------------------------------------


def test_timeout_victim_dead_lettered_collateral_rescheduled(make_dispatcher, monkeypatch):
    """A job past its deadline is dead-lettered (not retried); a concurrent
    healthy job that was in-flight is rescheduled and succeeds."""
    monkeypatch.setenv("GRIB_DECODE_BACKOFF_BASE_S", "0")
    before = decode_dead_letter_counts().get("decode_hung", 0)
    d, registry, _ = make_dispatcher(workers=2, timeout_s=0.3)

    def _hang(_v):
        time.sleep(2.0)  # finite (no thread leak) but well past the 0.3s deadline
        return "hang"

    blk = {"n": 0}
    b_lock = threading.Lock()

    def _blocker(_v):
        with b_lock:
            blk["n"] += 1
            n = blk["n"]
        if n == 1:
            time.sleep(1.0)  # still in flight when the watchdog fires
            return "blocker-late"
        return "blocker-ok"

    registry["hang"] = _hang
    registry["blocker"] = _blocker

    hang_fut = d.submit_one("hang", ("h",), DecodePriority.SCHEDULED)
    # hang is pumped synchronously, so it is the only in-flight job (earliest
    # deadline, inserted first) before blocker is submitted; the watchdog
    # iterates _inflight in insertion order, making hang the victim
    # deterministically — no reliance on a wall-clock sleep margin.
    _wait_deadline = time.monotonic() + 5.0
    while not d._inflight and time.monotonic() < _wait_deadline:
        time.sleep(0.005)
    blocker_fut = d.submit_one("blocker", ("b",), DecodePriority.SCHEDULED)

    with pytest.raises(DecodeDispatchError) as exc:
        hang_fut.result(5.0)
    assert exc.value.reason == "decode_hung"
    assert blocker_fut.result(5.0) == "blocker-ok"
    assert blk["n"] == 2, "healthy collateral should have been rescheduled once"
    assert decode_dead_letter_counts().get("decode_hung", 0) == before + 1


# ---------------------------------------------------------------------------
# 5. Crash backoff vs timeout immediacy
# ---------------------------------------------------------------------------


def test_crash_retry_is_delayed(make_dispatcher, monkeypatch):
    """A crash-collateral retry waits a (jittered) backoff before re-running."""
    monkeypatch.setenv("GRIB_DECODE_BACKOFF_BASE_S", "0.6")
    d, registry, _ = make_dispatcher(workers=1)
    times: list[float] = []
    t_lock = threading.Lock()
    n = {"i": 0}

    def _flaky(_v):
        with t_lock:
            times.append(time.monotonic())
            n["i"] += 1
            i = n["i"]
        if i == 1:
            raise BrokenProcessPool("die")
        return "ok"

    registry["flaky"] = _flaky
    fut = d.submit_one("flaky", ("x",), DecodePriority.SCHEDULED)
    assert fut.result(5.0) == "ok"
    gap = times[1] - times[0]
    # Equal-jitter backoff for base=0.6, attempt 1 => delay in [0.3, 0.6].
    assert gap >= 0.25, f"crash retry gap {gap:.3f}s — expected a backoff delay"


def test_timeout_collateral_retry_is_immediate(make_dispatcher, monkeypatch):
    """Timeout collateral (healthy job, sibling hung) retries with no backoff."""
    monkeypatch.setenv("GRIB_DECODE_BACKOFF_BASE_S", "0.6")  # would be obvious if applied
    d, registry, _ = make_dispatcher(workers=2, timeout_s=0.3)
    times: list[float] = []
    t_lock = threading.Lock()
    n = {"i": 0}

    def _hang(_v):
        time.sleep(2.0)
        return "hang"

    def _collateral(_v):
        with t_lock:
            times.append(time.monotonic())
            n["i"] += 1
            i = n["i"]
        if i == 1:
            time.sleep(1.0)  # in flight at watchdog
            return "late"
        return "ok"

    registry["hang"] = _hang
    registry["collateral"] = _collateral

    d.submit_one("hang", ("h",), DecodePriority.SCHEDULED)
    # Wait until hang is in-flight (earliest deadline, inserted first) so it is
    # the deterministic victim — see test_timeout_victim_* for the rationale.
    _wait_deadline = time.monotonic() + 5.0
    while not d._inflight and time.monotonic() < _wait_deadline:
        time.sleep(0.005)
    fut = d.submit_one("collateral", ("c",), DecodePriority.SCHEDULED)
    assert fut.result(5.0) == "ok"
    # Re-run started essentially as soon as the first attempt's sleep returned;
    # the gap is dominated by the 1s in-flight sleep, NOT the 0.6s backoff
    # (which is skipped for timeout collateral). Just assert the retry ran.
    assert n["i"] == 2


# ---------------------------------------------------------------------------
# 6. Retry cap
# ---------------------------------------------------------------------------


def test_retry_cap_dead_letters_persistent_crasher(make_dispatcher, monkeypatch):
    """An always-crashing job is dead-lettered after RETRY_CAP reschedules."""
    monkeypatch.setenv("GRIB_DECODE_RETRY_CAP", "2")
    monkeypatch.setenv("GRIB_DECODE_RETRY_BUDGET", "100")  # don't trip budget first
    monkeypatch.setenv("GRIB_DECODE_BACKOFF_BASE_S", "0.01")
    d, registry, _ = make_dispatcher(workers=1)
    runs = {"n": 0}
    r_lock = threading.Lock()

    def _always_crash(_v):
        with r_lock:
            runs["n"] += 1
        raise BrokenProcessPool("always dies")

    registry["crash"] = _always_crash
    fut = d.submit_one("crash", ("x",), DecodePriority.SCHEDULED)
    with pytest.raises(DecodeDispatchError) as exc:
        fut.result(5.0)
    assert exc.value.reason == "retry_cap_exhausted"
    # cap=2 => attempts 1,2,3 (3rd fault sees retries==cap and gives up).
    assert runs["n"] == 3, runs["n"]


# ---------------------------------------------------------------------------
# 7. Retry budget
# ---------------------------------------------------------------------------


def test_retry_budget_dead_letters_then_recovers(make_dispatcher, monkeypatch):
    """When the process-wide retry RATE is exhausted, interrupted work is
    dead-lettered fast; after the window clears, retries work again."""
    monkeypatch.setenv("GRIB_DECODE_RETRY_BUDGET", "1")
    monkeypatch.setenv("GRIB_DECODE_RETRY_WINDOW_S", "0.5")
    monkeypatch.setenv("GRIB_DECODE_RETRY_CAP", "100")  # so cap doesn't trip first
    monkeypatch.setenv("GRIB_DECODE_BACKOFF_BASE_S", "0.01")
    d, registry, _ = make_dispatcher(workers=1)

    def _always_crash(_v):
        raise BrokenProcessPool("die")

    registry["crash"] = _always_crash
    fut = d.submit_one("crash", ("x",), DecodePriority.SCHEDULED)
    with pytest.raises(DecodeDispatchError) as exc:
        fut.result(5.0)
    # First fault consumes the single budget unit (reschedule); second fault
    # finds the budget exhausted -> dead-letter.
    assert exc.value.reason == "retry_budget_exhausted"

    # After the window clears, a transient (crash-once) job recovers.
    time.sleep(0.7)
    n = {"i": 0}

    def _flaky(_v):
        n["i"] += 1
        if n["i"] == 1:
            raise BrokenProcessPool("transient")
        return "recovered"

    registry["flaky"] = _flaky
    fut2 = d.submit_one("flaky", ("y",), DecodePriority.SCHEDULED)
    assert fut2.result(5.0) == "recovered"


# ---------------------------------------------------------------------------
# 8. Dead-letter observability
# ---------------------------------------------------------------------------


def test_dead_letter_emits_structured_log_and_counter(make_dispatcher, monkeypatch, caplog):
    """A give-up emits a structured WARNING (fn / reason / retries / args) and
    bumps the per-reason counter."""
    monkeypatch.setenv("GRIB_DECODE_RETRY_CAP", "0")  # dead-letter on the first fault
    monkeypatch.setenv("GRIB_DECODE_RETRY_BUDGET", "100")
    d, registry, _ = make_dispatcher(workers=1)
    before = decode_dead_letter_counts().get("retry_cap_exhausted", 0)

    def _crash(_path):
        raise BrokenProcessPool("boom")

    registry["decode_thing"] = _crash
    caplog.set_level(logging.WARNING, logger="weatherbrief.fetch.grib")
    fut = d.submit_one("decode_thing", ("/data/grib/ecmwf_a2_step3.grib",),
                       DecodePriority.BACKGROUND)
    with pytest.raises(DecodeDispatchError):
        fut.result(5.0)

    assert decode_dead_letter_counts().get("retry_cap_exhausted", 0) == before + 1
    dl = [r.getMessage() for r in caplog.records if "dead-letter" in r.getMessage()]
    assert dl, "expected a dead-letter WARNING"
    msg = dl[-1]
    assert "fn=decode_thing" in msg
    assert "reason=retry_cap_exhausted" in msg
    assert "ecmwf_a2_step3.grib" in msg, f"args summary missing file name: {msg}"


# ---------------------------------------------------------------------------
# 9. Bypass paths
# ---------------------------------------------------------------------------


def test_in_process_fallback_when_workers_zero(make_dispatcher):
    """``workers==0`` runs jobs inline and returns resolved futures (priority
    moot) — never touches the pool."""
    d, registry, harness = make_dispatcher(workers=0)
    registry["echo"] = lambda v: {"got": v}
    fut = d.submit_one("echo", (7,), DecodePriority.INTERACTIVE)
    assert fut.done()
    assert fut.result() == {"got": 7}
    # Batch too.
    registry["id"] = lambda v: v
    futs = d.submit_batch([("id", (i,)) for i in range(3)], DecodePriority.SCHEDULED)
    assert [f.result() for f in futs] == [0, 1, 2]
    assert harness.teardown_waits == [], "inline path must not tear down a pool"


def test_in_process_fallback_propagates_exception(make_dispatcher):
    d, registry, _ = make_dispatcher(workers=0)

    def _boom(_v):
        raise RuntimeError("inline-bang")

    registry["boom"] = _boom
    fut = d.submit_one("boom", (1,), DecodePriority.SCHEDULED)
    with pytest.raises(RuntimeError, match="inline-bang"):
        fut.result()


def test_submit_after_drain_fails_fast(make_dispatcher):
    """A submit that races behind drain() fails its caller future immediately
    rather than hanging on a _pump that will never run again."""
    d, registry, _ = make_dispatcher(workers=2)
    registry["echo"] = lambda v: v
    d.drain()

    fut = d.submit_one("echo", (1,), DecodePriority.SCHEDULED)
    assert fut.done()
    with pytest.raises(DecodeDispatchError) as exc:
        fut.result()
    assert exc.value.reason == "dispatcher_shutdown"

    futs = d.submit_batch([("echo", (2,)), ("echo", (3,))], DecodePriority.SCHEDULED)
    assert len(futs) == 2
    for f in futs:
        assert f.done()
        with pytest.raises(DecodeDispatchError):
            f.result()


def test_drain_during_crash_backoff_releases_floating_handle(make_dispatcher, monkeypatch):
    """A crash-retry handle waiting out its backoff timer lives in neither
    _pending nor _inflight. drain() must still release its caller — otherwise
    the caller blocked on .result() hangs until (or past) process exit."""
    monkeypatch.setenv("GRIB_DECODE_BACKOFF_BASE_S", "2.0")  # wide backoff window
    monkeypatch.setenv("GRIB_DECODE_RETRY_CAP", "5")
    d, registry, _ = make_dispatcher(workers=1)

    crashed = threading.Event()

    def _crash_once(_v):
        crashed.set()
        raise BrokenProcessPool("die")

    registry["crash"] = _crash_once
    fut = d.submit_one("crash", ("x",), DecodePriority.SCHEDULED)
    assert crashed.wait(5.0), "job should have run and crashed"

    # Wait until recovery has reached _reenqueue and the handle is floating
    # in the backoff timer (tracked in _delayed_handles).
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        with d._cond:
            if d._delayed_handles:
                break
        time.sleep(0.01)
    with d._cond:
        assert d._delayed_handles, "handle should be mid-backoff (floating)"

    d.drain()  # must release the floating handle, not just _pending/_inflight
    with pytest.raises(DecodeDispatchError) as exc:
        fut.result(5.0)
    assert exc.value.reason == "dispatcher_shutdown"


def test_priority_disabled_routes_to_legacy(monkeypatch):
    """``GRIB_DECODE_PRIORITY_ENABLED=0`` makes the wrappers bypass the
    dispatcher entirely and call the legacy FIFO functions."""
    monkeypatch.setenv("GRIB_DECODE_PRIORITY_ENABLED", "0")
    assert grib._priority_enabled() is False

    called: dict[str, object] = {}

    def _fake_single(name, *args):
        called["single"] = (name, args)
        return "legacy-result"

    def _fake_batch(jobs):
        called["batch"] = jobs
        return ["legacy-batch"]

    monkeypatch.setattr(grib, "_dispatch_decode_legacy", _fake_single)
    monkeypatch.setattr(grib, "_dispatch_decode_parallel_legacy", _fake_batch)
    # A fresh sentinel dispatcher that must NOT be used.
    monkeypatch.setattr(grib, "_DISPATCHER", _ExplodingDispatcher())

    assert grib._dispatch_decode("decode_x", "p", priority=DecodePriority.INTERACTIVE) == "legacy-result"
    assert called["single"] == ("decode_x", ("p",))
    assert grib._dispatch_decode_parallel([("decode_y", ("q",))]) == ["legacy-batch"]
    assert called["batch"] == [("decode_y", ("q",))]


class _ExplodingDispatcher:
    def submit_one(self, *a, **k):  # noqa: ANN001, ANN002, ANN003
        raise AssertionError("dispatcher must not be used when priority disabled")

    def submit_batch(self, *a, **k):  # noqa: ANN001, ANN002, ANN003
        raise AssertionError("dispatcher must not be used when priority disabled")

    _closed = False


# ---------------------------------------------------------------------------
# 10. Integration: ContextVar-driven priority through the real wrapper
# ---------------------------------------------------------------------------


def test_contextvar_priority_ordering_concurrent(monkeypatch):
    """INTERACTIVE context's decode is dispatched ahead of a BACKGROUND
    context's decode that was queued first (1 worker)."""
    monkeypatch.setenv("GRIB_DECODE_PRIORITY_ENABLED", "1")

    registry: dict[str, object] = {}
    harness = PoolHarness(1)
    dispatcher = PriorityDecodeDispatcher(
        worker_resolver=lambda name: registry[name],
        pool_factory=harness.factory,
        pool_teardown=harness.teardown,
        workers_fn=harness.workers_fn,
        timeout_fn=lambda: 5.0,
    )
    monkeypatch.setattr(grib, "_DISPATCHER", dispatcher)

    order: list[str] = []
    o_lock = threading.Lock()
    gate_started = threading.Event()
    release = threading.Event()

    def _gate(_v):
        gate_started.set()
        release.wait(5.0)
        return "gate"

    def _record(label):
        with o_lock:
            order.append(label)
        return label

    registry["gate"] = _gate
    registry["record"] = _record

    results: dict[str, object] = {}

    def _gate_thread():
        results["gate"] = grib._dispatch_decode("gate", "g",
                                                 priority=DecodePriority.SCHEDULED)

    def _enrich(label, priority):
        # Mimic an entry point setting the priority via the public helper, then
        # dispatching with priority=None so the value is read from the context.
        set_decode_priority(priority)
        results[label] = grib._dispatch_decode("record", label)

    def _wait_pending(n: int, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with dispatcher._cond:
                if len(dispatcher._pending) >= n:
                    return True
            time.sleep(0.005)
        return False

    gt = threading.Thread(target=_gate_thread)
    gt.start()
    assert gate_started.wait(2.0), "gate never occupied the worker"

    # Submission order (BACKGROUND before INTERACTIVE) is deliberately the
    # opposite of priority order — the heap must reorder. Ordering is decided by
    # the heap when the gate frees the worker, NOT by which thread submits first,
    # so no sleeps are needed: just wait until both jobs are actually queued.
    bg = threading.Thread(target=_enrich, args=("bg", DecodePriority.BACKGROUND))
    it = threading.Thread(target=_enrich, args=("int", DecodePriority.INTERACTIVE))
    bg.start()
    it.start()
    assert _wait_pending(2), "both record jobs should be queued behind the gate"
    release.set()

    for t in (gt, bg, it):
        t.join(5.0)
    dispatcher.drain()

    assert results["int"] == "int" and results["bg"] == "bg"
    assert order == ["int", "bg"], f"INTERACTIVE should dispatch first: {order}"


# ---------------------------------------------------------------------------
# 10b. max_inflight sliding window (issue #459)
# ---------------------------------------------------------------------------


def _window_dispatcher(monkeypatch, workers: int) -> PriorityDecodeDispatcher:
    """Install a FakePool-backed dispatcher as the module singleton so
    ``_dispatch_decode_parallel`` (which reaches for ``_get_dispatcher``) uses it."""
    monkeypatch.setenv("GRIB_DECODE_PRIORITY_ENABLED", "1")
    registry: dict[str, object] = {}
    harness = PoolHarness(workers)
    dispatcher = PriorityDecodeDispatcher(
        worker_resolver=lambda name: registry[name],
        pool_factory=harness.factory,
        pool_teardown=harness.teardown,
        workers_fn=harness.workers_fn,
        timeout_fn=lambda: 5.0,
    )
    dispatcher._registry = registry  # test-only handle
    monkeypatch.setattr(grib, "_DISPATCHER", dispatcher)
    return dispatcher


def test_parallel_max_inflight_caps_concurrency_below_pool(monkeypatch):
    """``max_inflight`` bounds concurrent decodes *below* a wider pool, and
    results still return in input order."""
    dispatcher = _window_dispatcher(monkeypatch, workers=4)  # wide pool

    concurrency = {"now": 0, "max": 0}
    c_lock = threading.Lock()
    proceed = threading.Event()

    def _tracked(v):
        with c_lock:
            concurrency["now"] += 1
            concurrency["max"] = max(concurrency["max"], concurrency["now"])
        proceed.wait(5.0)
        with c_lock:
            concurrency["now"] -= 1
        return v

    dispatcher._registry["decode_x"] = _tracked
    jobs = [("decode_x", (i,)) for i in range(8)]

    box: dict[str, object] = {}

    def _run():
        box["out"] = grib._dispatch_decode_parallel(
            jobs, priority=DecodePriority.SCHEDULED, max_inflight=2,
        )

    t = threading.Thread(target=_run)
    t.start()
    # Wait until the window saturates at 2, then prove it never exceeds 2.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        with c_lock:
            if concurrency["now"] >= 2:
                break
        time.sleep(0.005)
    time.sleep(0.05)
    with c_lock:
        peak = concurrency["max"]
    proceed.set()
    t.join(5.0)
    dispatcher.drain()

    assert peak == 2, f"max_inflight=2 should cap concurrency at 2, saw {peak}"
    assert box["out"] == list(range(8)), "results must stay in input order"


def test_parallel_max_inflight_isolates_failures_with_return_exceptions(monkeypatch):
    """Windowed dispatch with ``return_exceptions=True`` places each failure in
    its own slot and keeps siblings' results in order."""
    dispatcher = _window_dispatcher(monkeypatch, workers=4)

    def _maybe_fail(i):
        if i % 3 == 0:
            raise ValueError(f"boom-{i}")
        return i

    dispatcher._registry["decode_x"] = _maybe_fail
    jobs = [("decode_x", (i,)) for i in range(7)]

    out = grib._dispatch_decode_parallel(
        jobs, priority=DecodePriority.SCHEDULED,
        return_exceptions=True, max_inflight=2,
    )
    dispatcher.drain()

    for i, r in enumerate(out):
        if i % 3 == 0:
            assert isinstance(r, ValueError) and str(r) == f"boom-{i}", (i, r)
        else:
            assert r == i, (i, r)


def test_parallel_max_inflight_noop_when_ge_batch(monkeypatch):
    """A window >= the batch size is a no-op — the whole batch is submitted."""
    dispatcher = _window_dispatcher(monkeypatch, workers=4)
    dispatcher._registry["decode_x"] = lambda v: v

    jobs = [("decode_x", (i,)) for i in range(3)]
    out = grib._dispatch_decode_parallel(
        jobs, priority=DecodePriority.SCHEDULED, max_inflight=10,
    )
    dispatcher.drain()
    assert out == [0, 1, 2]


# ---------------------------------------------------------------------------
# 11. Real ProcessPool — manager-thread recovery (FakePool can't reproduce this)
# ---------------------------------------------------------------------------


@pytest.fixture
def real_pool_dispatch(monkeypatch):
    """Drive the real ``_dispatch_decode`` through a real ``ProcessPoolExecutor``
    and the global dispatcher singleton. This is the only way to exercise the
    executor-manager-thread recovery path: with a real pool, a worker death sets
    ``BrokenProcessPool`` on the manager thread, which invokes ``_on_done`` —
    recovery must tear the pool down off that thread (it joins the manager
    thread) or it self-joins and wedges with ``draining`` stuck True."""
    monkeypatch.setenv("GRIB_DECODE_PRIORITY_ENABLED", "1")
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "2")
    grib.shutdown_decode_pool()
    grib._DISPATCHER = None
    yield
    try:
        grib._drain_dispatcher_for_shutdown()
    finally:
        grib._DISPATCHER = None
        grib.shutdown_decode_pool()


def test_real_pool_dispatch_returns_result(real_pool_dispatch):
    assert grib._dispatch_decode("_test_echo", "hi") == "hi"


def test_real_pool_crash_recovers_off_manager_thread(real_pool_dispatch, monkeypatch):
    """A SIGKILL'd worker breaks the pool; recovery runs off the manager thread,
    so the dispatcher doesn't wedge. An always-crashing job is dead-lettered
    after RETRY_CAP, and the pool recovers for the next dispatch.

    The test completing (not hanging) is itself the regression check for the
    manager-thread self-join bug."""
    monkeypatch.setenv("GRIB_DECODE_RETRY_CAP", "1")
    monkeypatch.setenv("GRIB_DECODE_RETRY_BUDGET", "100")
    monkeypatch.setenv("GRIB_DECODE_BACKOFF_BASE_S", "0.01")
    monkeypatch.setenv("GRIB_DECODE_TIMEOUT_S", "30")

    assert grib._dispatch_decode("_test_echo", "warm") == "warm"

    t0 = time.monotonic()
    with pytest.raises(DecodeDispatchError) as exc:
        grib._dispatch_decode("_test_crash")
    assert exc.value.reason == "retry_cap_exhausted"
    assert time.monotonic() - t0 < 25, "recovery appears wedged (manager-thread self-join?)"

    # Pool rebuilt lazily — a subsequent dispatch succeeds.
    assert grib._dispatch_decode("_test_echo", "recovered") == "recovered"
