# MySQL usage optimization — design (mysql-review)

**Date:** 2026-07-31
**Status:** Approved design (superpowers brainstorming), full-scope Tier 1+2+repo-3.
**Inputs:** three-track audit of the 84-migration schema, all `storage/` modules, and the
`flyfun_common` engine + every `SessionLocal()` call site (full findings in the review
presented to the user; top items independently re-verified against the code).
**Delivery:** branch `mysql-review` (based on upstream/main `4abd5b49`), PR to
`roznet/flyfun-weather:main` from the `downle` fork. Cleanly mergeable (based on latest main).

## Scope

Twelve items from the audit, grouped by tier. One new Alembic migration
(`085_mysql_review_fixes.py` — revision numbering skips 030 historically; 084 is current head)
plus focused code changes. FK additions are deliberately excluded (documented instead).

### Tier 1 — correctness & reachable outages

1. **Airport-profile SSE pool fix** (`api/airport_profile.py`): gather all DB reads, close
   the session BEFORE constructing the `StreamingResponse` (the proven
   `packs.py:2151-2153,2330` pattern), removing `Depends(get_db)` from this endpoint.
   Today: 20 allowed concurrent streams vs a 15-connection pool → reachable 500s.
2. **Session scope-downs** (web-pool connections pinned across slow work):
   `_auto_refresh_one` (DB read → close → pipeline without session → DB finalize+commit);
   `process_auto_refreshes` (close the due-flight read session before the loop);
   `collect_and_store` (read → release → aviationweather.gov fetch → reopen to store);
   the time-scan runner (same shape). `run_standalone_cycle` unchanged (subprocess-isolated
   by default) + a code comment noting why.
3. **`briefing_packs` UNIQUE `(flight_id, fetch_timestamp)`**: migration dedupes existing
   pairs (keep newest `id` per pair, log counts), adds `uq_briefing_packs_flight_ts`;
   `save_pack_meta` gains the savepoint + IntegrityError→update race guard (the
   `subscribe_flight` precedent). Removes the silent-duplicate → `MultipleResultsFound`
   → 500 trap; the composite also covers latest-pack lookups.
4. **Missing indexes**: `ix_briefing_usage_timestamp`,
   `ix_cost_ledger (service, created_at)`, `ix_verif_scores_obs_time`,
   `ix_taf_verif_obs_time` (all serve audited hot/range paths; retention DELETEs for the
   last two).

### Tier 2 — safe hygiene

5. **Drop 15 redundant indexes**: 13 leftmost-prefix duplicates (`ix_verif_obs_icao`,
   `ix_verif_scores_icao`, `ix_taf_verif_icao`, `ix_fvm_flight`, `ix_flight_subs_flight`,
   `ix_flight_seen_user`, `ix_vms_month`, `ix_vds_date_model`, `ix_ams_month`,
   `ix_ads_date`, `ix_aed_day`, `ix_abfd_day`, `ix_axcd_day`), the
   `oauth_refresh_tokens.token_hash` unique+plain duplicate, and `ix_afs_hour_model`
   (081's documented follow-up). Kept: `ix_verif_scores_lead`, `ix_verif_scores_model`
   (need `sys.schema_unused_indexes` evidence), FORCE-INDEXed `ix_verif_scores_source_time`.
   DROP INDEX is metadata-only on MySQL 8.
6. **`users (provider, provider_sub)` UNIQUE** — dedupe check + unique index; the OAuth
   login lookup becomes indexed and duplicate-identity races impossible.
7. **Money → DECIMAL**: `cost_ledger.cost`, `donation_ledger.amount/amount_usd/
   fx_rate/net_usd` → `DECIMAL(12,4)` (fx `DECIMAL(12,6)`). ORM `Float` mappings read
   DECIMAL without change; the proper `Numeric` mapping belongs upstream in
   `flyfun_common` (noted in the PR, not done here).
8. **Charset**: no table rebuilds in this PR (needs prod verification + maintenance
   window). Deliver `scripts/check_mysql_charset.py` (`SHOW TABLE STATUS` report) and a
   migration-convention note (new tables declare `mysql_charset="utf8mb4"`).

### Tier 3 — repo-level improvements

9. **`latest_pack(session, flight_id)`** in `storage/flights.py`: `LIMIT 1` + column
   subset (no 5×`json.loads`, no per-row HMAC, no filesystem stats) replacing
   `list_packs()[0]` in the scheduler gate, `tasks/refresh_resume.py`, and
   `_auto_refresh_one`. `list_packs` unchanged for genuine list consumers.
10. **Flights-list batch prefetch**: the per-flight `db.get(UserAircraftRow)` becomes one
    `IN` query over distinct aircraft ids. Python-side pagination stays (bounded).
11. **Deadlock retry helper**: `db_retry` context/decorator (OperationalError 1213/1205 →
    rollback + jittered retry ×3) applied to the hottest write paths (pack meta
    save/update, refresh-job write-through, `subscribe_flight`) with tests.
12. **FKs NOT added** — documented in `designs/data-models.md`: the intentional no-FK set
    (`cost_ledger.user_id`, `donation_ledger.user_id`, `briefing_refresh_jobs.flight_id`)
    vs rows that arguably should survive deletion (feedback, usage logs) vs the oauth
    oversight set — cascades here are product decisions, listed with per-table
    recommendations for a later issue.

## Error handling / house rules

- Dedupe migration keeps the newest row per `(flight_id, fetch_timestamp)` and logs
  counts — never silent data loss; non-destructive otherwise.
- Every session-scope change preserves the notification-after-commit ordering.
- None≠0 and the None-vs-quiet conventions are unaffected (no data-model semantic changes).

## Verification

- New/extended tests: migration chain (upgrade head on SQLite), dedupe migration unit
  test, unique-race test (SQLite enforces the same constraint), session-scope tests
  (mock-assert connection released before pipeline/stream), `latest_pack` tests,
  deadlock-retry tests, DECIMAL read-back, batch-prefetch test.
- MySQL-only paths (partitions, `ON DUPLICATE KEY UPDATE`, charset) remain
  SQLite-untestable — documented; `scripts/check_mysql_charset.py` covers prod-side
  verification.
- Full suite green except the known pre-existing `test_google_login_redirects`
  (environment-dependent, fails on the base too).

## Out of scope

FK additions (documented), `flyfun_common` engine tuning (pool size/timeouts/charset —
upstream issue), `sys.schema_unused_indexes` audit in prod, `ix_verif_scores_lead`/
`ix_verif_scores_model` drops (need evidence), CONVERT-to-utf8mb4 table rebuilds,
`run_standalone_cycle` restructure (subprocess-isolated).
