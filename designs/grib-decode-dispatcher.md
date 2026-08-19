# GRIB Decode Dispatcher

> Priority-aware, fault-tolerant admission layer in front of the GRIB decode process pool.

Lives in `src/weatherbrief/fetch/grib/__init__.py` (alongside the pool lifecycle and hang-diagnostics it wraps). Workers live in `fetch/grib/decode_worker.py`.

## Why a pool at all

GRIB decode (cfgrib + xarray + numpy interp) is GIL-bound. Two concurrent `enrich_forecasts()` calls in the uvicorn process serialise on the GIL and per-step times balloon even with idle cores. Decode is dispatched to a process pool (`ProcessPoolExecutor`, spawn) so each decode gets its own interpreter.

The pool is a **lazy singleton** (`_get_decode_pool`), default **2 workers** (`GRIB_DECODE_WORKERS`; `0` = in-process kill switch). `max_tasks_per_child` recycling is **disabled by default** after a 2026-05-17 CPython recycle-race incident. Workers eagerly import cfgrib in `_worker_init` and register a SIGUSR1 faulthandler for hang dumps.

Decode jobs are **pure**: read a GRIB file path from disk, return plain dicts/lists. Bytes are never pickled across the IPC boundary — only path strings + lat/lon lists go in, serialisable results come out.

## The problem the dispatcher solves (issue #171)

Three workloads share the one pool with **no ordering guarantee** (plain `pool.submit`, FIFO with races between submitter threads):

| Workload | Priority |
|---|---|
| User refresh (`run_pipeline`), airport profile | **INTERACTIVE (10)** |
| Scheduler auto-refresh | **SCHEDULED (50)** |
| Standalone forecast/verification cycle, precache | **BACKGROUND (90)** |

`concurrent.futures` executors have no priority support, so a user refresh routinely queued behind a background batch. Priority must be an **admission layer** in front of the pool.

## Priority signal & propagation

`DecodePriority(IntEnum)`: `INTERACTIVE=10`, `SCHEDULED=50`, `BACKGROUND=90`. **Lower value = higher priority** (Unix `nice`-style) so it composes directly with a min-heap and the FIFO `seq` tiebreak — no negation. Values are spread so finer levels can be inserted later without renumbering. Helpers accept `int | DecodePriority`. **No priority arithmetic** (aging / dynamic boosting) in v1.

Propagation mirrors the existing `_GRIB_TIMER` pattern via a `ContextVar` `_DECODE_PRIORITY`:

- `enrich_forecasts(..., priority=None)` resolves `explicit arg → ContextVar → SCHEDULED` and publishes it on the ContextVar for the call. Phase-1 worker threads inherit it via `_submit_with_context` (which copies the context), so nested `_dispatch_decode` calls see it.
- `_dispatch_decode` / `_dispatch_decode_parallel` take an optional `priority` kwarg defaulting to the ContextVar value.
- **Entry points set the value past context-copying boundaries** via the public `set_decode_priority(p)` helper (keeps call sites off the private ContextVar):
  - `api/packs.py` user refresh (both `run_pipeline`s): set INTERACTIVE *inside* `run_pipeline` (`run_in_executor` does not copy the caller context).
  - `api/airport_profile.py`: `enrich_forecasts(priority=INTERACTIVE)`.
  - `scheduler.py` `_auto_refresh_one`: SCHEDULED. `_run_standalone_once`: BACKGROUND (runs in `asyncio.to_thread`, which copies the context).
  - `tasks/standalone_verification.py`: the ECMWF a1/a2 decode batch passes `priority=BACKGROUND` explicitly (its decode runs off the standalone context); since #459 it fans out via `_dispatch_decode_parallel` (with an optional `GRIB_DECODE_WORKERS_ECMWF` window) rather than one blocking `_dispatch_decode` per step.
  - `tasks/standalone_grib.py`: the GFS/ICON cloud-diag adapter dispatches by cache path with `priority=BACKGROUND` (#236 — it previously called the decode functions directly in the orchestrating process, putting cfgrib/xarray full-grid decode on that process's heap), batched via `_dispatch_decode_parallel` since #459 (light leg, no cap). Note: scheduled standalone cycles run in a subprocess whose `GRIB_DECODE_WORKERS` is set from `STANDALONE_ANALYSIS_WORKERS` (default 2, `0` restores inline; #448 PR B), so the child runs its own small pool for decode **and** pooled sounding batches (`analyze_sounding_batch` — the first non-decode worker fn, gated on the public `decode_pool_enabled()`); the parent app's pool is untouched.
  - `tasks/time_scan.py` (timing-flexibility ±day scan): BACKGROUND, both via `set_decode_priority` before a bare `_enrich_ecmwf` and via `enrich_forecasts(priority=…)` per candidate slot.
  - `scenario/measure.py`: BACKGROUND — the RSS-measurement harness mirrors the safest overnight mode.
  - Precache (`scheduler.py`): download-only, no decode → nothing to prioritise.

## Dispatcher architecture

`PriorityDecodeDispatcher` — process-wide lazy singleton (`_get_dispatcher`), one lock + condition guards all state. **Event-driven; no dedicated dispatch thread.**

- `pending`: min-heap of `(priority, seq, _JobHandle)`. `seq` is a monotonic counter → FIFO within a level (and makes heap entries totally ordered, so handles are never compared).
- `inflight`: `dict[pool_future, _JobHandle]`, capped at the worker count.
- `_JobHandle`: `worker_fn_name`, `args`, `caller_future`, `priority`, `seq`, `retries`, `deadline`, `last_exc`.

`submit_one` / `submit_batch` create caller-facing futures, push handles, call `_pump`, and return the caller futures. **Callers block on these exactly as before** (`fut.result()`); the call-site shape is unchanged. The caller future is the *logical* operation — internally several pool futures may back it across retries, transparently.

`_pump` (locked): while `len(inflight) < workers` and `pending`, pop the highest-priority handle, `pool.submit`, set `deadline = monotonic + GRIB_DECODE_TIMEOUT_S`, add to `inflight`. Done-callbacks are attached **outside** the lock (an already-done future fires the callback synchronously, and `_on_done` re-takes the non-reentrant lock). A `BrokenProcessPool` from `submit()` → `_handle_fault(CRASH)`.

`_on_done` (pool manager thread): if draining/closed, ignore (a teardown owns it); `BrokenProcessPool` → leave the handle in `inflight` and `_handle_fault(CRASH)`; else resolve the caller future and `_pump`.

**Watchdog** (one daemon thread): parks until the earliest `inflight` deadline (or `_WATCHDOG_IDLE_POLL_S` = 30 s when nothing is in flight); on wake, the in-flight job past its deadline becomes the `TIMEOUT` victim.

**Where recovery runs matters.** The watchdog owns its own thread, so it calls `_handle_fault` synchronously. Crash faults are different: they surface in `_on_done` / a `_pump` submit, which can run on the pool's *executor-manager thread* — and teardown's `shutdown(wait=True)` joins that thread, so an inline call would self-join (`RuntimeError`) and wedge the dispatcher with `draining` stuck True. Those paths therefore go through `_spawn_recovery`, which runs `_handle_fault` on a fresh daemon thread; the `draining` guard dedups concurrent spawns.

### Per-job timeout (deliberate improvement)

The legacy `_dispatch_decode_parallel` shared **one** deadline across a whole batch, which false-times-out large batches. The dispatcher gives **each job its own** `GRIB_DECODE_TIMEOUT_S`, which is also what makes timeout-victim identification possible.

### Batch failure isolation

`_dispatch_decode_parallel(..., return_exceptions=False)` mirrors `asyncio.gather`: on `True` a job that ultimately dead-letters yields its exception *in its own slot* instead of raising and discarding its siblings' results. The legacy FIFO path is shared-fate by design (one deadline, batch-wide cancel), so there the same exception lands in every slot.

### `max_inflight` — throttle a memory-heavy batch below the pool (#459)

`_dispatch_decode_parallel(jobs, ..., max_inflight=None)`:

- `None` — submit the whole batch; the pool size (`GRIB_DECODE_WORKERS`) bounds concurrency. This is the default and what every fan-out used before #459.
- an `int` — a caller-side **sliding window** (`_collect_windowed`, one `submit_one` per slot): keep at most that many caller futures outstanding, submit the next as each completes. Bounds how many full GRIB grids decode concurrently for a memory-heavy loop even when the pool is wider. Because there is a **single** pool, the pool is always the hard ceiling — a window `>=` the batch size (or `>=` the pool) is a no-op, and a per-task window can only throttle *down* from `GRIB_DECODE_WORKERS`; to give a task *more*, raise the pool. Ignored on the legacy FIFO path (`GRIB_DECODE_PRIORITY_ENABLED=0`), a rollback switch where the pool size alone bounds concurrency.

Two standalone decode loops that used to walk one blocking `_dispatch_decode` per step (only ever 1 worker busy) now collect their jobs and fan out through this primitive:

- **ECMWF a1/a2** (`tasks/standalone_verification.py::fetch_ecmwf_grib_snapshots`) — the dominant standalone wall-time cost (~6 min, ~70% idle on the M4). Passes `max_inflight=_decode_workers_ecmwf()` (`GRIB_DECODE_WORKERS_ECMWF`, unset → full pool). Concurrent ECMWF decode holds full grids in RAM, heavier per-worker than the sounding batches the pool was tuned for, so a memory-constrained fallback host can set `GRIB_DECODE_WORKERS_ECMWF=1` to keep this leg serial while leaving the shared pool wide for everything else.
- **GFS/ICON cloud-diag** (`tasks/standalone_grib.py`) — light (single field); batched with no dedicated cap (`max_inflight` left `None`). Fetch stays sequential (network I/O); only the decodes fan out.

## Recovery — `_handle_fault(reason, victim=None)`

```
under lock: if draining/closed: return               # already handled
            if victim and victim not in inflight: return   # it finished in the gap — no hang, no teardown
            draining=True; snapshot=list(inflight.values()); inflight.clear()
if TIMEOUT: _diag_snapshot_workers(pool, ...)        # reuse existing hang-diag
pool_teardown(wait = reason != TIMEOUT)              # reuse shutdown_decode_pool
for h in snapshot:
    if closed:                      _dead_letter(h, "dispatcher_shutdown")  # drain() began mid-recovery
    elif h is victim:               _dead_letter(h, "decode_hung")        # don't retry — re-running re-hangs
    elif not retry_budget_ok():     _dead_letter(h, "retry_budget_exhausted")
    elif h.retries >= RETRY_CAP:    _dead_letter(h, "retry_cap_exhausted")
    else: h.retries+=1; reenqueue(h, after = 0 if TIMEOUT else jittered_backoff(h.retries))
draining=False (in finally); _pump()                 # rebuild pool lazily, resume in priority order
```

Properties:

- **Completed work is never touched** — already-resolved caller futures keep their results.
- **The pending heap is the durable structure** — teardown never touches it, so after rebuild a waiting INTERACTIVE job jumps ahead of the rest of a BACKGROUND batch.
- **Interrupted jobs are transparently rescheduled** by creating a new pool future for the same caller future. Strictly better than before, where an interrupted briefing degraded to Open-Meteo / a standalone step went empty.
- **Timeout victim is dead-lettered, not retried** — breaks the infinite-teardown loop a corrupt GRIB would cause.
- **Crash collateral backs off with jitter; timeout collateral retries immediately** (fresh pool, those jobs were healthy).
- **Dead-letter, don't silently drop**: `_dead_letter` sets the caller-future exception (`DecodeDispatchError`) **and** emits a structured WARNING + per-reason counter (`decode_dead_letter_counts()`). Existing call sites already degrade on a decode exception, so they degrade exactly as before — just far less often.
- **`RETRY_CAP`** bounds the crash case where stdlib can't attribute the culprit. **Retry budget** is a sliding-window cap on retry *rate* process-wide (per Google SRE: per-item caps alone allow retry amplification); when tripped, interrupted jobs are dead-lettered for the window so an OOM storm can't thrash.

## Idempotency invariant (load-bearing)

Auto-rescheduling interrupted work is **only safe because dispatched jobs are pure** (at-least-once delivery with idempotent consumers). Any future side-effecting job MUST NOT use this dispatcher, or MUST carry its own dedup — otherwise a reschedule double-applies the effect. Stated in the `PriorityDecodeDispatcher` docstring so it isn't silently violated.

## Bypass paths

- `GRIB_DECODE_WORKERS=0`: jobs run **inline** (resolved futures); priority moot. Identical to today's kill switch.
- `GRIB_DECODE_PRIORITY_ENABLED=0`: `_dispatch_decode{,_parallel}` route to `_dispatch_decode{,_parallel}_legacy` — the pre-#171 FIFO path. Production rollback switch; covered by `test_grib_pool.py`.

## App shutdown

`shutdown_decode_pool(drain_dispatcher=True)` (called from the app lifespan) first drains the dispatcher — failing every pending/in-flight caller future with `DecodeDispatchError("dispatcher_shutdown")` so blocked callers are released rather than left waiting on a vanishing pool. The fault-recovery path uses `drain_dispatcher=False` (default) because it must **not** touch the durable pending heap.

`force=True` (#448 PR B) additionally TERM→KILLs every known worker — the current pool *and* orphans left by earlier `wait=False` resets — before the executor shutdown. Without it, `wait=False` merely abandons a wedged worker, and at interpreter exit `concurrent.futures`' atexit hook joins the executor manager thread, which never finishes while a worker sits in native code → the process hangs. Used by the disposable standalone child's exit path. The web app's fault-recovery path deliberately does **not** force (see the bounded-leak trade-off below).

## Config / env vars

| Var | Default | Meaning |
|---|---|---|
| `GRIB_DECODE_WORKERS` | 2 | Pool size; `0` = in-process |
| `GRIB_DECODE_WORKERS_ECMWF` | unset | `max_inflight` window for the standalone ECMWF decode batch (#459); unset/`<=0` → full pool. Only throttles *down* from `GRIB_DECODE_WORKERS` |
| `GRIB_DECODE_MAX_TASKS_PER_CHILD` | 0 (off) | Worker recycle threshold; `<=0` disables recycling (see the 2026-05-17 incident) |
| `GRIB_DECODE_TIMEOUT_S` | 300 | **Per-job** deadline |
| `GRIB_DECODE_PRIORITY_ENABLED` | on | `0` = legacy FIFO (rollback) |
| `GRIB_DECODE_RETRY_CAP` | 2 | Max reschedules of one crash-interrupted job |
| `GRIB_DECODE_RETRY_BUDGET` | 5 | Max retries within the window (rate cap) |
| `GRIB_DECODE_RETRY_WINDOW_S` | 120 | Retry-rate window |
| `GRIB_DECODE_BACKOFF_BASE_S` | 0.5 | Crash-retry backoff base (equal jitter); `0` disables delay |

## Inherent tradeoffs (v1, documented not fixed)

- **Global teardown kills collateral.** stdlib `ProcessPoolExecutor` can't replace one wedged worker (recycling disabled by default in `_decode_pool_max_tasks_per_child` after the 2026-05-17 recycle-race incident), so one bad job tears down the whole pool. Auto-reschedule is a deliberate workaround. Escalation: per-worker supervision / `pebble`.
- **Priority reduces queue latency, doesn't bound it.** Non-preemption + FIFO-within-level → head-of-line blocking (a long BACKGROUND decode holds a worker while INTERACTIVE waits, bounded by job runtime). Hard interactive SLA → reserved-worker **bulkhead**. Low-priority **starvation** → **aging**. Both deferred.

## Observability

- `_handle_fault` logs reason + victim + rescheduled/dead-lettered counts + `_diag_pool_summary`.
- `_dead_letter` emits a structured WARNING (`fn`, `reason`, `retries`, args summary incl. file name, `last_exc`) and bumps `_DEAD_LETTER_COUNTS` (read via `decode_dead_letter_counts()`).
- All existing hang-diagnostics (`_diag_snapshot_*`) are unchanged and still run before a timeout teardown.

## Tests

- `tests/test_grib_dispatcher.py` — dispatcher behaviour with an injected fake `worker_fn` + thread-backed `FakePool` (no cfgrib): priority ordering, slot bounding, crash reschedule, timeout dead-letter + collateral, crash-backoff vs timeout-immediacy, retry cap, retry budget + recovery, dead-letter observability, drain semantics (submit-after-drain, drain during crash backoff), both bypass paths, `max_inflight` (caps below the pool, no-op when `>=` batch, `return_exceptions` isolation), a ContextVar→ordering integration test through the real `_dispatch_decode`, and two **real `ProcessPoolExecutor`** tests — including the crash-recovers-off-the-manager-thread regression that pins `_spawn_recovery`.
- `tests/test_grib_pool.py` — the pool plumbing the dispatcher reuses (pinned to the legacy FIFO path).
