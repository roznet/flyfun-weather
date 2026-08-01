# MySQL usage optimization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the 12 approved MySQL fixes (correctness/outages, index hygiene, repo-level query/session improvements) from the audit-backed spec.

**Architecture:** One Alembic migration (`085_mysql_review_fixes.py`) carrying all schema changes (unique constraints after dedupe, 4 missing indexes, 15 redundant drops, DECIMAL money), plus focused code changes: SSE/session scope fixes, a `latest_pack()` fast path, batch prefetch, and a deadlock-retry helper. Spec: `docs/superpowers/specs/2026-07-31-mysql-optimization-design.md`.

**Tech Stack:** Python 3.12, SQLAlchemy 2 + PyMySQL, Alembic, FastAPI, pytest.

## Global Constraints

- Branch `mysql-review` at upstream/main `4abd5b49`; worktree `/home/qian/flyfun_weather/flyfun-weather-mysql`; tests via `./.venv/bin/python -m pytest <args> -q` from that root.
- Migration head is `084`; new revision `085_mysql_review_fixes.py`, `down_revision = "084"` (030 is historically skipped — do not renumber anything).
- Dedupe keeps the NEWEST `id` per `(flight_id, fetch_timestamp)` and logs counts; no silent data loss beyond exact-duplicate pack metadata rows.
- Kept indexes (do NOT drop): `ix_verif_scores_lead`, `ix_verif_scores_model`, FORCE-INDEXed `ix_verif_scores_source_time`.
- `CostLedgerRow`/`DonationRow` ORM models live in `flyfun_common` (external) — do NOT edit them; SQLAlchemy `Float` mappings read DECIMAL columns without change.
- House rules: None≠0; no FK additions (documentation only); no reading other branches/worktrees; no network in unit tests; every session-scope change preserves notify-after-commit ordering.
- The only allowed pre-existing test failure: `tests/test_auth.py::TestLoginRedirect::test_google_login_redirects`.

---

### Task 1: Migration 085 — briefing_packs unique + save race guard

**Files:**
- Create: `alembic/versions/085_mysql_review_fixes.py`
- Modify: `src/weatherbrief/storage/flights.py` (`save_pack_meta`/`update_pack_meta`, ~lines 650-675)
- Test: `tests/test_migration_085.py`, `tests/test_pack_meta_race.py`

**Interfaces:**
- Produces: revision `085` with `uq_briefing_packs_flight_ts (flight_id, fetch_timestamp)` UNIQUE; `save_pack_meta` becomes race-safe (IntegrityError → update path).
- Consumes: `_meta_to_row`, `_apply_meta_to_row`, `_compute_pack_hmac` (existing helpers in storage/flights.py).

Migration part A (upgrade): (1) dedupe — `DELETE bp FROM briefing_packs bp JOIN (SELECT flight_id, fetch_timestamp, MAX(id) keep_id FROM briefing_packs GROUP BY flight_id, fetch_timestamp HAVING COUNT(*) > 1) d ON bp.flight_id=d.flight_id AND bp.fetch_timestamp=d.fetch_timestamp AND bp.id != d.keep_id` executed via `op.execute`, with the duplicate count logged first; (2) `op.create_index("uq_briefing_packs_flight_ts", "briefing_packs", ["flight_id", "fetch_timestamp"], unique=True)`. Downgrade drops the index. The same file will host later tasks' ops — Task 1 creates the file with ONLY this op; Task 2 appends.

`save_pack_meta` race guard (subscribe_flight precedent at flights.py:528-560):
```python
def save_pack_meta(session: Session, meta: BriefingPackMeta) -> None:
    """Insert briefing pack metadata with integrity HMAC.

    Race-safe against the (flight_id, fetch_timestamp) unique constraint:
    a concurrent refresh inserting the same pack first flips this save to an
    update of the existing row instead of poisoning the request transaction.
    """
    row = _meta_to_row(meta)
    row.integrity_hmac = _compute_pack_hmac(row)
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        if not update_pack_meta(session, meta):
            raise
```
`begin_nested` (SAVEPOINT) contains the failure so the outer transaction survives; the fallback reuses `update_pack_meta`'s existing select+apply. Test: two `save_pack_meta` calls with the same `(flight_id, ts)` → second one updates, no raise, and `load_pack_meta` returns one row (SQLite enforces the unique constraint too — add the constraint to the ORM table args? The ORM `BriefingPackRow` model lives in `src/weatherbrief/db/models.py` — check and add the `UniqueConstraint` so dev `create_all` matches the migration; same for the Task-2 missing indexes' `__table_args__` where models live in-repo).

- [ ] **Step 1:** failing tests (dedupe on a seeded duplicate pair via raw SQL against a temp SQLite DB upgraded to head; race test).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement migration part A + race guard + ORM table args.
- [ ] **Step 4:** run → PASS; `pytest tests/test_migration_085.py tests/test_pack_meta_race.py tests/test_storage_flights.py -q`.
- [ ] **Step 5:** commit `feat(db): unique (flight_id, fetch_timestamp) on briefing_packs + race-safe save (#mysql-review)`.

---

### Task 2: Migration 085 — missing indexes, redundant drops, users unique, DECIMAL money

**Files:**
- Modify: `alembic/versions/085_mysql_review_fixes.py` (append to Task 1's file)
- Modify: `src/weatherbrief/db/models.py` (in-repo ORM table args for matching indexes where models live here)
- Test: `tests/test_migration_085.py` (extend)

**Interfaces:**
- Produces: `ix_briefing_usage_timestamp`, `ix_cost_ledger_service_created (service, created_at)`, `ix_verif_scores_obs_time (observation_time)`, `ix_taf_verif_obs_time (observation_time)`, `uq_users_provider_sub (provider, provider_sub)` UNIQUE; drops the 15 redundant indexes (spec item 5 list verbatim); DECIMAL(12,4)/DECIMAL(12,6) money columns.
- Consumes: the audited index list (spec §Tier-2 item 5 — copy it verbatim from the spec into the migration).

Ops (upgrade): (1) `op.execute` duplicate-identity check on users `(provider, provider_sub)` — raise with a clear message if any (prod would need manual resolution; expected zero); create the unique index. (2) Create the 4 missing indexes. (3) Drop the 15 redundant ones by name (guard each with an existence check helper `_index_exists(table, name)` using `information_schema.STATISTICS` on MySQL and `PRAGMA index_list` on SQLite — the migration must run on both; ORM `__table_args__` must match so dev `create_all` agrees). (4) `op.alter_column` the five money columns to DECIMAL — batch mode for SQLite; MySQL converts FLOAT→DECIMAL (document the one-way rounding in a comment). Downgrade: best-effort inverse (recreate dropped indexes, drop created ones, Float back).

- [ ] **Step 1:** failing tests (index existence after upgrade; users-unique present; DECIMAL read-back of a 1234.56 value).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement ops + ORM table args alignment.
- [ ] **Step 4:** run → PASS; full `pytest tests/test_migration_085.py -q`.
- [ ] **Step 5:** commit `feat(db): missing indexes, redundant-index drops, users identity unique, DECIMAL money (#mysql-review)`.

---

### Task 3: Airport-profile SSE session fix

**Files:**
- Modify: `src/weatherbrief/api/airport_profile.py` (`get_airport_profile`, ~lines 547-732)
- Test: `tests/test_airport_profile.py` (extend or new session-scope test)

**Interfaces:**
- Consumes: the proven streaming-session pattern at `api/packs.py:2151-2153,2330` (own SessionLocal, closed before streaming).

Change: remove `db: Session = Depends(get_db)`; open `SessionLocal()`; perform ALL DB reads (`_surface_from_cache` and anything else the stream's generator doesn't need); `db.close()` in a `finally` BEFORE returning the `StreamingResponse`. The stream generator must capture plain data, never the session. Verify no other statement in the function path needs the session afterward.

- [ ] **Step 1:** failing test — mock `SessionLocal` + the stream; assert the session is closed before the response object is created (e.g. spy on `Session.close`).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run → PASS; `pytest tests/test_airport_profile.py -q`.
- [ ] **Step 5:** commit `fix(db): close session before airport-profile SSE stream (#mysql-review)`.

---

### Task 4: Session scope-downs (refresh loop, verification, time-scan)

**Files:**
- Modify: `src/weatherbrief/scheduler.py` (`process_auto_refreshes` ~148-198, `_auto_refresh_one` ~440-517)
- Modify: `src/weatherbrief/tasks/verification.py` (`collect_and_store` ~449-525)
- Modify: `src/weatherbrief/tasks/time_scan_runner.py` (`_run_scan_job` ~98-177)
- Modify: `src/weatherbrief/tasks/refresh_resume.py` (~252-261)
- Test: `tests/test_session_scope.py` (new)

Pattern per site (adjust to local code): (1) open session, do ALL DB reads needed before the slow work, extract plain values, `db.close()` (or commit/rollback + close) in `finally`; (2) run the slow pipeline/fetch/scan WITHOUT any session; (3) open a fresh session for finalize + commit. The outer `process_auto_refreshes` must not hold a session across `asyncio.to_thread(_auto_refresh_one, ...)`; `_auto_refresh_one` must not hold one across `execute_briefing`; `collect_and_store` must not hold one across `fetch_observations_batch`; `_run_scan_job` not across `run_time_scan`. Preserve notify-after-commit ordering and the `_finalize_refresh` semantics exactly. Note: `execute_briefing`/`_prepare_refresh` currently take `db=` kwargs — untangle what genuinely needs DB during the pipeline (target: nothing; if something does, give it a short-lived dedicated session inside).

- [ ] **Step 1:** failing tests — spy session factory per site: assert no session object remains unclosed across the slow-call boundary (mock the slow call).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement the four scope-downs.
- [ ] **Step 4:** run → PASS; `pytest tests/test_session_scope.py tests/test_scheduler.py tests/test_verification.py -q` (whichever exist).
- [ ] **Step 5:** commit `fix(db): release pooled connections before pipelines and network fetches (#mysql-review)`.

---

### Task 5: `latest_pack()` fast path

**Files:**
- Modify: `src/weatherbrief/storage/flights.py` (add `latest_pack`; ~after `list_packs`)
- Modify: `src/weatherbrief/scheduler.py` (:447), `src/weatherbrief/tasks/refresh_resume.py` (:142), `src/weatherbrief/scheduler.py` `_auto_refresh_one` (Task 4's version)
- Test: `tests/test_storage_flights.py` (extend)

**Interfaces:**
- Produces: `latest_pack(session: Session, flight_id: str) -> BriefingPackMeta | None` — single query `WHERE flight_id … ORDER BY fetch_timestamp DESC, id DESC LIMIT 1` selecting ONLY the columns `_row_to_meta` needs (skip the HMAC verify and the artifact-path stat on this path — document why: the hot gate only needs assessment/status fields; keep the full check in `list_packs`).

- [ ] **Step 1:** failing tests (returns newest; skips HMAC/stat via monkeypatch spies; None when no packs).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement + swap the three hot callers.
- [ ] **Step 4:** run → PASS; `pytest tests/test_storage_flights.py tests/test_scheduler.py -q`.
- [ ] **Step 5:** commit `perf(db): latest_pack fast path for hot refresh gates (#mysql-review)`.

---

### Task 6: Flights-list aircraft batch prefetch

**Files:**
- Modify: `src/weatherbrief/api/flights.py` (`_flight_to_response` area, ~lines 358-461 and its list caller)
- Test: `tests/test_api_flights_list.py` (extend or new)

Change: in the flights-list endpoint, collect distinct `aircraft_id`s from the listed flights, fetch all `UserAircraftRow`s in ONE `select(...).where(UserAircraftRow.id.in_(ids))`, build a lookup dict, and pass it into `_flight_to_response` instead of letting it `db.get` per flight. Behaviour identical otherwise.

- [ ] **Step 1:** failing test — two flights sharing one aircraft → exactly one aircraft query issued (count via a session execute spy).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run → PASS; `pytest tests/test_api_flights_list.py -q` (or the file the endpoint's tests live in).
- [ ] **Step 5:** commit `perf(db): batch aircraft lookup in flights list (#mysql-review)`.

---

### Task 7: Deadlock retry helper

**Files:**
- Create: `src/weatherbrief/db/retry.py` (check whether `src/weatherbrief/db/` is the right home; else `storage/retry.py`)
- Modify: `src/weatherbrief/storage/flights.py` (`save_pack_meta`/`update_pack_meta`, `subscribe_flight`), `src/weatherbrief/storage/refresh_jobs.py` (write-through `_best_effort` paths)
- Test: `tests/test_db_retry.py` (new)

**Interfaces:**
- Produces: `db_retry(max_attempts: int = 3, base_delay_s: float = 0.05)` — context manager/decorator that runs the wrapped block, catches `sqlalchemy.exc.OperationalError` whose `orig` is PyMySQL 1213/1205, rolls the session back, and retries with jittered exponential backoff; re-raises after the last attempt. Works on SQLite too (no-op unless such an error occurs — tests inject a fake OperationalError).

Apply to the three hot write paths named above (wrap the retry around the smallest critical section; preserve the `begin_nested` race guard from Task 1 inside the retry).

- [ ] **Step 1:** failing tests (succeeds on 2nd attempt; gives up after N; non-deadlock OperationalError NOT retried; session rolled back between attempts).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement helper + apply.
- [ ] **Step 4:** run → PASS; `pytest tests/test_db_retry.py tests/test_pack_meta_race.py -q`.
- [ ] **Step 5:** commit `feat(db): deadlock/lock-wait retry helper for hot write paths (#mysql-review)`.

---

### Task 8: Charset check script + FK documentation + final suite

**Files:**
- Create: `scripts/check_mysql_charset.py`
- Modify: `designs/data-models.md` (FK section)
- Test: none (script is prod-tooling; doc is prose)

Script: connects via `DATABASE_URL` (argparse `--url` optional), prints per-table ENGINE/COLLATION from `information_schema.TABLES` where table_schema=current db, exits non-zero if any user table is not InnoDB/utf8mb4*, plus a one-line `sys.schema_unused_indexes` dump when available. `designs/data-models.md`: new "FK policy" section — intentional no-FK set (cost_ledger.user_id, donation_ledger.user_id, briefing_refresh_jobs.flight_id, analytics_*), survive-deletion candidates (feedback.flight_id, briefing_usage.flight_id, api_usage_log), oauth oversight set (authorization_codes, refresh_tokens, api_tokens.oauth_client_id) with a per-table recommendation and rationale.

- [ ] **Step 1:** write script + doc section.
- [ ] **Step 2:** smoke-run the script against a temp SQLite (must exit cleanly with a clear "not MySQL" message) and against nothing (usage print).
- [ ] **Step 3:** commit `docs(db): charset check script + FK policy documentation (#mysql-review)`.
- [ ] **Step 4:** FULL suite `./.venv/bin/python -m pytest -q` — green except the allowed auth failure.
- [ ] **Step 5:** push `mysql-review` to fork; PR to `roznet/flyfun-weather:main` (base main, head `downle:mysql-review`).

---

## Self-review log

- Spec coverage: items 1-12 → tasks 3, 4, 1-2, 2 (charset script 8), 5, 6, 7, 8. Item 8's migration-convention note is folded into 085's comments + the data-models doc (Task 8).
- Type consistency: `latest_pack` returns the same `BriefingPackMeta` type callers use today; `db_retry` wraps `begin_nested` from Task 1, not replaces it; migration revision chain 084→085 single head.
- Placeholder scan: Task 3/4 line numbers are approximate by design ("~") with the pattern named explicitly; implementers verify exact spots first.
