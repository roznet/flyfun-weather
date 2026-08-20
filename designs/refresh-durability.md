# Refresh Durability

> Durable briefing-refresh tracking, and resuming a refresh interrupted by a container restart.

Issue #499. Code: `db/models.py::BriefingRefreshJobRow`, `storage/refresh_jobs.py`,
`api/packs.py::_RefreshRegistry`, `tasks/refresh_resume.py`, migration `082`.

## The problem

Refresh state used to be **in-memory only**. `_RefreshRegistry` holds a
`dict[flight_id, RefreshEntry]` and the pipeline runs on a module-level
`ThreadPoolExecutor(max_workers=2)`. If the container died mid-refresh (OOM
kill, deploy, crash) that state evaporated and *nothing in the DB or on disk
recorded that a refresh had ever been in flight*:

- the pack row is written at the end (`_persist_pack_finalize`), or at the
  `briefing_ready` milestone on the SSE path only (`_persist_pack_provisional`);
- `_prepare_refresh` mkdirs the pack dir up front, so a killed refresh leaves an
  orphan directory of partial artifacts with no row pointing at it;
- web SSE just errored out; iOS polling `/refresh/status` got
  `{"active": false}` — indistinguishable from "never asked";
- recovery was accidental: the scheduler picked the flight up at its *next*
  auto-refresh slot, if one happened to be due. Manual / Siri / MCP refreshes
  were simply lost.

The 2026-07-23 05:09Z OOM (#490) is the motivating case: a user briefing was
killed, with no record, no retry, and nothing to post-mortem.

## What makes it cheap

1. **The registry is already the single choke point.** All three refresh paths
   (sync `POST /refresh`, SSE `/refresh/stream`, scheduler
   `process_auto_refreshes`) go through `try_register` → `set_refreshing` →
   `update_progress` → `unregister`. Durability is a write-through mirror at
   those call sites, not a rewrite.
2. **Single uvicorn worker** (`Dockerfile`, no `--workers`). So no leases, no
   instance ids, no distributed locking: *any* non-terminal row present at
   process boot is by definition an orphan.

## The job row

`briefing_refresh_jobs` (migration 082) — one row per attempt:

| Column | Notes |
|---|---|
| `flight_id` | **Not** a foreign key — see below |
| `user_id` | FK to `users`, `ON DELETE CASCADE` |
| `triggered_by` | `user` \| `scheduler` \| `resume` — the registry's queue-cap class |
| `source` | Client-declared attribution (`user` \| `siri` \| `mcp` \| `scheduler`) |
| `as_of_date` | Set when the refresh pinned a backtest date |
| `status` | `queued` \| `running` \| `succeeded` \| `skipped` \| `failed` \| `abandoned` |
| `attempt` | 1 for the original run, +1 per resume |
| `created_at` / `started_at` / `finished_at` / `heartbeat_at` | |
| `stage` | Last pipeline stage the registry saw — "where did it die" |
| `pack_path` | Recorded as soon as the pack dir is created |
| `last_error` | |

`flight_id` is deliberately **not** an FK: the row has to outlive the flight so
reconciliation can distinguish "the flight was deleted, don't resume" from "no
record at all". Because of that, `app._on_delete_user` sweeps the table
explicitly on account deletion (the `FlightRow` bulk delete emits no ORM
cascades anyway).

A separate table rather than two columns on `flights`: the registry is keyed by
`flight_id` so at most one refresh per flight is live, but when the container
OOMs, the record of what was in flight is exactly the post-mortem that wasn't
possible for #490.

## Write-through

`_RefreshRegistry(durable=True)` — the process-wide `refresh_registry`
singleton. A bare `_RefreshRegistry()` stays a pure in-memory object, so unit
tests and ad-hoc use never touch the DB.

| Registry call | Row effect |
|---|---|
| `try_register` | insert `queued` (carries `source`, `as_of_date`, `attempt`) |
| `set_refreshing` | → `running`, stamp `started_at` |
| `note_pack_path` | record `pack_path` (called from `_prepare_refresh` — the one place every path creates the dir) |
| `update_progress` | bump `heartbeat_at` + `stage`, throttled to `HEARTBEAT_MIN_INTERVAL` (15s) |
| `mark_outcome` + `unregister` | terminal status |

`succeeded` vs `skipped` is a real distinction, not bookkeeping.
`scheduler._auto_refresh_one` returns a **bool** — `False` when it
short-circuits on its own refresh gate (or a missing `AIRPORTS_DB`) rather than
running the pipeline — and both callers (the auto-refresh cycle and the resume
pass) use it to close the row honestly. A gated skip is the *routine* outcome
for a flight that already has a recent pack, so folding it into `succeeded`
would have the table claim briefings that were never produced — exactly the
record this feature exists to make trustworthy. Practically, `skipped` rows
will dominate the table (bounded at roughly one per flight per day, since
`last_auto_refresh_at` is bumped either way), which sharpens the pruning
follow-on below.

Two invariants make this work:

- **Best-effort.** Every write goes through `storage/refresh_jobs.py`'s
  `record_*` helpers, which open their own short-lived session and swallow all
  exceptions. A DB hiccup must never fail a refresh; durability here is a
  diagnostic and a resume hint, not a correctness invariant. DB I/O also happens
  strictly *outside* the registry lock — the GRIB warm loop polls that lock.
- **`mark_outcome` is separate from `unregister`.** Callers close the entry in a
  `finally`, which is exactly what does *not* run when the process is killed. An
  entry closed without a marked outcome records `failed`; an entry never closed
  at all stays non-terminal — the orphan. The crash case is the mechanism, not
  a bug.

## Boot-time reconciliation + resume

`tasks/refresh_resume.py::run_refresh_resume`, wired into `lifespan` like the
other loops (disable with `DISABLE_REFRESH_RESUME=1`). It is **one-shot, not a
loop**: orphan-hood is a property of process boot.

Orphan ids are snapshotted *immediately*, then the pass sleeps
`STARTUP_DELAY_SECONDS` (90s, past the scheduler's own 30s delay). Snapshotting
first means a refresh started by *this* process can never be mistaken for an
orphan of the previous one.

`decide_resume` then picks per row, cheapest check first:

| Condition | Outcome |
|---|---|
| `attempt >= WB_REFRESH_MAX_ATTEMPTS` | `abandoned` — retry budget spent |
| Pinned `as_of_date` | `abandoned` — a backtest re-run live answers a different question |
| Flight deleted | `abandoned` |
| Flight already departed | `abandoned` |
| Beyond the forecast horizon | `abandoned` |
| `gated_data_status(...).refresh_decision` no longer says `full` | `abandoned` — the scheduler already re-briefed it, or no model moved. The gate is a free correctness check |
| otherwise | **resume** |

Three consequences of the gate check worth knowing:

- The gate is reached through `api/packs.py:gated_data_status`, which applies
  the parameter-change override (#552) for us — the single entry point every
  caller now shares (#558; see
  [freshness-markers.md](freshness-markers.md)). A refresh queued *because* the
  pilot edited the flight has no new model run behind it, so the bare gate
  answers `none` and we would abandon the very job that exists to rebuild the
  pack — leaving a briefing computed for the previous departure time.
  `_auto_refresh_one` calls the same boundary, passing
  `params_override=(triggered_by == "resume")`, so its own re-check agrees with
  `decide_resume`. The routine scheduler cycle deliberately stays
  un-overridden: an edit there is already followed by a client-driven refresh.

- An SSE refresh killed *after* the `briefing_ready` milestone already wrote a
  provisional pack row from the current runs, so the gate says `none` and the
  resume is skipped. That is the intended outcome: the pilot has a briefing off
  those runs, and only the LLM digest is missing — which the pack's Generate
  button covers far more cheaply than a whole pipeline.
- `heartbeat_at` and `stage` are populated on **every** path: all three pass a
  `progress_callback` that forwards into `update_progress`. The SSE path also
  turns each stage into a stream event; the sync and scheduler/resume paths use
  it purely to advance the registry, which is what `/refresh/status` polling and
  the durable row both read. (Until 2026-07-26 only the SSE path wired it, so a
  resumed run showed "Starting refresh" for its whole life and recorded no stage
  — losing "where did it die" on exactly the attempt most likely to die again.)
  Reconciliation still never reads the heartbeat: non-terminal-at-boot is the
  whole signal. This only affects how much a post-mortem, or a polling client,
  can see.

A resume re-queues through the registry with `triggered_by="resume"` and
`attempt + 1`, **then** closes the interrupted row as `failed` — one row per
attempt, so the post-mortem reads as a history rather than a mutating counter.
The ordering matters: registering first means the closed row can never claim a
resume that didn't happen (another refresh may already own the flight by the
time the pass fires, in which case the row closes `abandoned` with *resume
skipped, flight already active*). It costs a narrow window where both rows are
non-terminal — if the process dies inside it, the next boot resumes the old row
and abandons the new one at the attempt cap, i.e. one extra run rather than a
lost one. Execution reuses `scheduler._auto_refresh_one`
(gate → prepare → pipeline → persist → notify), which grew a `triggered_by`
parameter for usage attribution and, since #552, to select the params-change
override on its own gate re-check.

Registering also drives `idle_seconds()` to zero, so the discretionary GRIB warm
loop yields to a resume exactly as it does for an interactive briefing — the
#490 machinery falls out for free. `"resume"` joins `"scheduler"` in
`UNCAPPED_TRIGGERS`: a resume is finishing work the user already asked for and
must not be turned away by a busy queue.

### Retry cap

`WB_REFRESH_MAX_ATTEMPTS`, **default 2** = the original run plus one resume. Set
it to 3 for two resumes, 1 to disable resume entirely. Sized so a briefing that
genuinely does take the container down can only do it twice, while a single OOM
or deploy never silently loses a user's refresh.

**No crash attribution**: we do not try to tell "this briefing caused the OOM"
from "something else did" (the GRIB precache and standalone cycle are the
likelier culprits, and no briefing has been observed crashing the container).
Any interruption burns an attempt regardless of cause. Likewise no
SIGTERM-vs-SIGKILL distinction — a deploy mid-briefing burns an attempt and
still gets its one resume.

## Client-visible terminal state

`GET /flights/{id}/packs/refresh/status` falls back to the job row when there is
no in-memory entry (`_interrupted_refresh_status`), so a reload after a restart
reports something instead of silence:

- non-terminal row → `{"active": true, "status": "interrupted", "attempt", "max_attempts", "will_retry"}` plus the last `stage`/`label`/`progress`;
- `abandoned` row → `{"active": false, "status": "abandoned", "last_error", ...}`.

Bounded by the same `_RefreshRegistry.STALE_ENTRY_SECONDS` (30 min) the warm
loop uses, so a leaked row can never pin a flight as permanently active. Rows
belonging to another user are never disclosed. Fields are additive — existing
web/iOS clients keep working; the UI copy for these states ships separately.

## Out of scope / follow-ons

- Web + iOS UI copy for the "retrying after restart" / "gave up" states.
- Sweeping orphan pack directories left by killed refreshes (retention loop) —
  `pack_path` on the abandoned row is the hook.
- Notifying the user on `abandoned` via the existing notify sink.
- Pruning old job rows (one small row per refresh; unbounded but slow-growing).
