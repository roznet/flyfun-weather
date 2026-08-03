# Verification data tiering — phased rollout plan (#522)

> **All the code is merged and dark.** Every phase below is enabled by an
> environment variable plus a restart, and disabled by the reverse. Nothing in
> this plan requires a code change, and no phase deletes anything until its
> gate is explicitly turned on.

Read `designs/metar-taf-accuracy.md` for the architecture; this file is the
runbook: what to check before each phase, what to run, how to validate, and
how to roll back.

## Why

Production at issue time: `verification_scores` 8.8M rows / ~3 GB (2 GB of it
indexes), `verification_observations` 2.3M rows / ~890 MB and growing ~800K
rows/month, `verification_daily_stats` 1.1M rows, `taf_verification_scores`
330K rows — against a shared MySQL server with a 1 GB InnoDB buffer pool.
Every dashboard and digest read window is ≤90 days, so the old rows serve no
live query: they are cold data sitting in the most expensive format available.

Target — three explicit tiers:

| Tier | Store | Retention | Serves |
|---|---|---|---|
| Raw operational | MySQL (`verification_observations`, `verification_scores`, `taf_verification_scores`, `airport_forecast_snapshots`) | 180 days (snapshots 10, unchanged) | scoring, rollup jobs, notable-misses/missed-warnings, ad-hoc debugging |
| Aggregates | MySQL rollup tables (per-airport daily, **global daily**, **TAF daily**, activity, monthly) | forever | all dashboard/digest/leaderboard reads |
| Row-level archive | Parquet in `DATA_DIR/archive/verification/` | forever | re-scoring, calibration, data science (DuckDB), re-import |

## Configuration reference

Every gate, its shipping default, and the phase that changes it. All are read
through `tasks/verification_tiering.py` — nothing reads these env vars
directly.

| Env var | Ships as | Phase | Set to | Meaning |
|---|---|---|---|---|
| `VERIFICATION_GLOBAL_ROLLUP_READS` | `0` | 1 | `1` | Unfiltered dashboard/digest aggregates read the global rollups |
| `VERIFICATION_ARCHIVE_ENABLED` | `0` | 2 | `1` | Daily retention loop runs the Parquet archive writer |
| `VERIFICATION_RAW_RETENTION_DAYS` | `9999` (disabled) | 3 | `180` | Online window for raw obs/scores/TAF scores |
| `VERIFICATION_PRUNE_REQUIRE_ARCHIVE` | `1` | — | leave `1` | No verified archive manifest → no delete. Safety belt; only turn off if abandoning the archive |
| `SNAPSHOT_INBOX_RETENTION_DAYS` | `0` (keep) | 3 | `30` | Rotates `eu-*/us-*.sqlite` out of `SNAPSHOT_INBOX_DIR` |
| `VERIFICATION_MONTHLY_ROLLUP_ENABLED` | `0` | 4 | `1` | Retention loop rolls completed months into monthly stats |
| `VERIFICATION_DAILY_STATS_RETENTION_MONTHS` | `0` (keep) | 4 follow-up | `18` | Prunes `verification_daily_stats` older than N months, and only for months that already have a monthly rollup. Runs from the retention loop alongside the monthly rollup (so it needs `VERIFICATION_MONTHLY_ROLLUP_ENABLED=1` too) |
| (path) `DATA_DIR/archive/verification/` | — | 2 | — | Archive root |

Deployment prerequisite for Phase 2 onward: `pyarrow>=15` (declared in
`pyproject.toml`; imported lazily so a missing wheel doesn't break startup
while the archive is off).

---

## Phase 0 — deploy the code (no behaviour change)

**What lands:** migration `086_verification_tiering` (four new tables, all
empty), the new rollup writers, the gated read switch, `tasks/archive.py`,
the reworked pruner, the rewritten monthly rollup, and four new CLI verbs.

Two things take effect immediately and deliberately, because both are
behaviour-preserving:

- `verification_daily_rollup.rollup_day` now writes the three new daily tables
  alongside `verification_daily_stats` on every scheduled cycle. Reads still
  come from the old tables, so this only populates going forward.
- `ensure_future_partitions` and its scheduler call are **deleted**. Dormant
  machinery: no migration ever partitioned `verification_observations`, and
  MySQL forbids foreign keys on partitioned tables anyway, so it returned 0
  on every run since it was written.

**Pre-flight**

- [ ] `alembic heads` shows a single head at `086`.
- [ ] Confirm the #519 METAR month-boundary repair is deployed **and** the
      affected production rows are corrected. The Phase 2 backfill freezes
      whatever is in the database into the archive — running it against
      uncorrected data bakes the corruption in permanently.

**Run**

```bash
alembic upgrade head
# restart the app
```

**Validate**

- [ ] `SHOW TABLES LIKE 'verification_global_daily_stats'` (and
      `verification_activity_daily`, `taf_verification_daily`,
      `archive_manifest`) — four tables exist.
- [ ] After the next standalone verification cycle, all three new daily
      tables have rows for today. Check the log line
      `daily rollup <date>: N per-airport, N global, N activity, N TAF groups`.
- [ ] Dashboard numbers unchanged (the gate is off).

**Rollback:** `alembic downgrade 085`. Nothing reads the new tables yet.

---

## Phase 1 — global rollups + read switch

**Gate:** `VERIFICATION_GLOBAL_ROLLUP_READS=1`

**Precondition to check before enabling**

- [ ] Phase 0 has been live long enough that today's cycle populates the new
      tables cleanly (one successful cycle is enough).
- [ ] The historical backfill below has completed. Flipping the gate on an
      un-backfilled database shows **empty history**, not wrong numbers — the
      7d/30d dashboard panels go blank. That is the failure mode to expect if
      the order is skipped.

**Run — backfill, in this order**

```bash
# 1. Fill any missing days in the per-airport daily table first. The global
#    table is a GROUP BY over it, so a missing day there is a missing day
#    everywhere downstream.
python -m weatherbrief.verify rollup-daily-stats

# 2. Re-roll every existing day. This is what populates the three new tables
#    over full history — rollup_day writes all four. Do this while raw scores
#    are still complete, i.e. BEFORE Phase 3.
python -m weatherbrief.verify rollup-daily-stats --rebuild
```

Expect this to take a while (one INSERT-SELECT per historical day × 4 tables).
It is idempotent — safe to interrupt and re-run.

**Validate before flipping the gate**

- [ ] Row counts look right: `verification_global_daily_stats` should be
      roughly `days × models × days_out × sources` (~90/day), and
      `verification_activity_daily` exactly `days × sources`.
- [ ] Spot-check agreement for one day — the global row must equal the sum of
      the per-airport rows:
      ```sql
      SELECT g.n, s.n FROM verification_global_daily_stats g
      JOIN (SELECT date, source, model, days_out, SUM(n) n
            FROM verification_daily_stats GROUP BY 1,2,3,4) s
        ON (g.date,g.source,g.model,g.days_out)=(s.date,s.source,s.model,s.days_out)
      WHERE g.date = '2026-07-15';
      ```
- [ ] `taf_verification_daily` has rows for `days_out` 0 and 1 (a TAF's
      validity rarely reaches 48 h).

**Flip**

```bash
VERIFICATION_GLOBAL_ROLLUP_READS=1   # then restart
python -m weatherbrief.verify rebuild-cache
```

**Validate after**

- [ ] Dashboard 24h/7d/30d accuracy, bias, wind-advisory and gust numbers are
      **identical** to a screenshot taken before the flip. They must be: the
      automated test `test_verification_global_rollup.py::TestReadSwitchAgreement`
      asserts gate-on and gate-off produce byte-identical results, so any
      visible difference means a backfill gap, not a rounding difference.
- [ ] A country/airport-filtered dashboard request still returns per-airport
      numbers (it deliberately keeps reading `verification_daily_stats`).
- [ ] **Measure and record the cache-rebuild wall time**, before and after —
      that is the acceptance number for this phase. Look for the
      `get_digest_data(...) Nms total (...)` log line, and the
      `rebuild_all` duration.
- [ ] MySQL slow-query log shows no aggregate query against raw
      `verification_scores` / `taf_verification_scores`. Only
      `get_notable_misses` / `get_missed_warnings` row reads should remain.

**Rollback:** `VERIFICATION_GLOBAL_ROLLUP_READS=0`, restart. The old code path
is still there and still correct.

**Phase 1 final step — only after a full release cycle with the gate on:**

- [ ] Delete `_SCORES_SOURCE_TIME_HINT` and the `_activity_counts_from_raw`
      fallback from `tasks/verification_stats.py`. The `FORCE INDEX` hint is a
      hard SQL error on MySQL if its index is ever dropped, so the hint has to
      go before anything else can be considered. Do **not** do this while a
      rollback to gate-off is still on the table.

> **Do not drop `ix_verif_scores_source_time`.** An earlier version of this
> step said to, on the reasoning that Phase 1 retires the last raw-scan
> consumer. It does not. `EXPLAIN` on production shows that index chosen by
> six live consumers, none of which Phase 1 touches: the daily rollup's
> per-day scan and its activity counts (`source IN (...) AND observation_time
> >= ... < ...` — exactly `(source, observation_time)`), the gust rollup,
> `get_notable_misses`, `get_missed_warnings`, the Phase 3 pruner's day walk,
> and the Phase 2 archive writer's pagination. `ix_verif_scores_source_days_time`
> cannot substitute: it cannot range-seek `observation_time` without an
> equality on `days_out`. Dropping it degrades the every-cycle rollup, the
> pruner and the archive writer — the three jobs this whole design depends on.
>
> The index that *does* become droppable after Phase 1 is
> `ix_verif_scores_source_model_days` (~325 MB): its only consumer is the
> unhinted activity `COUNT(DISTINCT)` that the read switch replaces.
> `ix_verif_scores_source_days_time` is never chosen today either, but it is
> the right shape for `get_notable_misses` / `get_missed_warnings` — which
> currently scan 3.8M rows at 0.32% selectivity because the optimizer prefers
> a backward scan on `source_time` to avoid a filesort. Test it with
> `FORCE INDEX` before deciding drop-versus-adopt.
>
> (Migration 086 already dropped `ix_verif_scores_lead`, `ix_verif_scores_icao`
> and `ix_verif_scores_model` — ~620 MB with no consumer in any plan.)

---

## Phase 2 — Parquet archive (write-only; deletes nothing)

**Gate:** `VERIFICATION_ARCHIVE_ENABLED=1`

**Precondition to check**

- [ ] `pyarrow` importable in the production venv:
      `python -c "import pyarrow; print(pyarrow.__version__)"`.
- [ ] `DATA_DIR` has headroom. Budget from a dry run (below); zstd Parquet
      typically lands ~10–20× smaller than the InnoDB footprint, so the whole
      current history should be single-digit GB.
- [ ] **#519 confirmed repaired in production data** (see Phase 0). This is
      the last point at which it is cheap to fix.

**Run**

```bash
# What would be written, without writing anything
python -m weatherbrief.verify archive backfill --dry-run

# Full historical backfill. Idempotent; safe to interrupt and re-run.
python -m weatherbrief.verify archive backfill

python -m weatherbrief.verify archive list
python -m weatherbrief.verify archive verify
```

Then enable the scheduled incremental run:

```bash
VERIFICATION_ARCHIVE_ENABLED=1   # then restart
```

**Finality rules (why a period isn't archived yet)**

- Monthly tables: month M is written once ≥10 days into M+1. Later scores
  can't arrive — scoring reads snapshots, and snapshots are pruned at 10 days.
  The current month is never archived.
- Snapshots: day D is written at D+2, partitioned by **`fetched_at`** UTC date
  so partitions line up exactly with the existing 10-day prune predicate.

**Validate**

- [ ] `verify archive verify` reports every manifest OK (file present, sha256
      matches, nothing live newer than the archive). After Phase 3 it will
      also note `live count N < archived M (expected after pruning)` on pruned
      months — that line is informational, not a failure.
- [ ] Round-trip by hand with DuckDB — recipes are in the `tasks/archive.py`
      module docstring:
      ```sql
      SELECT COUNT(*) FROM 'archive/verification/scores/2026-06.parquet';
      SELECT s.model, COUNT(*) FROM 'archive/verification/scores/*.parquet' s
        JOIN 'archive/verification/observations/*.parquet' o
          ON s.observation_id = o.id GROUP BY 1;
      ```
      The join is the important one: it proves `id` round-tripped and the
      archive is usable for re-scoring, which is the whole justification.
- [ ] Timestamps read back as UTC-aware (`timestamp[us, tz=UTC]`), not naive.
- [ ] Point the existing off-box backup job at `DATA_DIR/archive/`. **Do this
      before Phase 3** — after pruning, these files are the only copy.

**Let it run for ≥2 weeks** before Phase 3, and confirm the daily incremental
archive keeps up (`Verification archive: {...}` in the log, no errors).

**Rollback:** `VERIFICATION_ARCHIVE_ENABLED=0`. Files and manifests stay; the
only consequence is that Phase 3 stops finding new archived periods and
therefore stops deleting.

---

## Phase 3 — enable pruning

**Gates:** `VERIFICATION_RAW_RETENTION_DAYS=180`, and separately
`SNAPSHOT_INBOX_RETENTION_DAYS=30`.

**Preconditions — all of them**

- [ ] ≥2 weeks of validated Phase-2 archives (`archive verify` clean).
- [ ] Off-box backup of `DATA_DIR/archive/` confirmed running and restorable.
- [ ] `airport_monthly_summary` has rows for every month you intend to prune
      (the climatology gate) — `verify rollup-summary --all` if not.
- [ ] `VERIFICATION_PRUNE_REQUIRE_ARCHIVE` is `1` (default). Leave it there.

**Run — dry run first, always**

```bash
# Reports which months pass BOTH gates and why the others don't. Deletes nothing.
python -m weatherbrief.verify prune-raw --retain-days 180

# When the report looks right:
python -m weatherbrief.verify prune-raw --retain-days 180 --apply
```

Then hand it to the scheduler:

```bash
VERIFICATION_RAW_RETENTION_DAYS=180
SNAPSHOT_INBOX_RETENTION_DAYS=30
# then restart
```

**What the pruner will and won't touch**

- Deletes whole months only, and only months that are past the cutoff **and**
  summarised **and** safely archived — meaning, for all three monthly tables:
  a manifest exists, its Parquet file is still on disk, the file still hashes
  to the recorded sha256, and no live row in the month is newer than the
  archive (`max_id`). Any of those failing is logged loudly
  (`Raw retention: refusing to prune — …`) and the month is left alone.
- Months with nothing left to delete are skipped *before* the gate runs, so an
  already-pruned month neither re-hashes its archive nor reports itself as
  blocked. If you see a "refusing to prune" line, it is a real problem.
- Never deletes `source='flight'` scores — tiny volume, pilot/debrief context,
  ERA5 re-analysis value. Mirrors the debriefed-flights T2 exemption.
- Never deletes an observation referenced by `flight_verification_map`, or one
  that still has a flight score. Observations are shared between tracks and
  have no source column; deleting one would cascade into the exempt flight
  scores hanging off it.
- Deletes in 5,000-row batches with a commit per batch, walking a day at a
  time. Never one multi-month statement on the shared server.

**Validate**

- [ ] Watch the log for the summary line: `Raw retention: pruned N
      observations, N scores, N TAF scores across M month(s)`.
- [ ] `SELECT COUNT(*) FROM verification_scores WHERE source='flight'` is
      unchanged across the prune.
- [ ] Observations linked in `flight_verification_map` still resolve:
      ```sql
      SELECT COUNT(*) FROM flight_verification_map m
      LEFT JOIN verification_observations o ON m.observation_id = o.id
      WHERE m.observation_id IS NOT NULL AND o.id IS NULL;   -- expect 0
      ```
- [ ] Dashboard 24h/7d/30d unchanged (they read rollups now).
- [ ] `get_notable_misses` / `get_missed_warnings` still populate — their
      windows are ≤30 days, well inside 180.
- [ ] Table sizes drop: re-check `information_schema.TABLES` data+index length.
- [ ] Snapshot prune: with the archive on, it only deletes `fetched_at` days
      that have a snapshot manifest. A warning
      (`N day(s) past retention have no archive manifest`) means the archive is
      behind, not that the prune is broken — fix the archive, don't bypass.
- [ ] Inbox rotation removed old `eu-*/us-*.sqlite` and left recent ones.

**Rollback:** set `VERIFICATION_RAW_RETENTION_DAYS=9999`. Already-deleted rows
are recoverable only from the Parquet archive — which is exactly why the gate
refuses to delete anything that isn't in it.

---

## Phase 4 — monthly stats

**Gate:** `VERIFICATION_MONTHLY_ROLLUP_ENABLED=1`

**Precondition**

- [ ] `verification_daily_stats` coverage is complete for the months you want
      (`verify rollup-daily-stats` first if in doubt). A month is now the sum
      of its days, so a missing day silently under-reports the month.

**Run**

```bash
# Backfill all historical months from the daily table
python -m weatherbrief.verify rollup-monthly-stats

# If a previous version's rows need re-deriving (e.g. the old Python path's
# numbers, or leftover model='taf' rows):
python -m weatherbrief.verify rollup-monthly-stats --rebuild
```

```bash
VERIFICATION_MONTHLY_ROLLUP_ENABLED=1   # then restart
```

**Validate**

- [ ] Nothing was deferred: a month with a *real* daily gap (a day that has raw
      scores but no daily rows) logs
      `has un-rolled days — deferring until the daily rollup catches up` and is
      skipped. Run `verify rollup-daily-stats` and re-run if you see it. A day
      with no scores at all is not a gap and does not defer anything.
- [ ] For one month, the monthly row equals the sum of its daily rows:
      ```sql
      SELECT m.n_scores, d.n FROM verification_monthly_stats m
      JOIN (SELECT source, model, days_out, icao, SUM(n) n
            FROM verification_daily_stats
            WHERE date >= '2026-06-01' AND date < '2026-07-01'
            GROUP BY 1,2,3,4) d
        ON (m.source,m.model,m.days_out,m.icao)=(d.source,d.model,d.days_out,d.icao)
      WHERE m.month = '2026-06-01' AND m.n_scores <> d.n;   -- expect 0 rows
      ```
- [ ] No `model='taf'` rows remain — TAF monthly is out of scope by design and
      is served from `taf_verification_daily` at query time.

**Known scope changes from the rewrite** (both intentional, neither has a
production consumer today):

- TAF monthly rows are no longer written; a re-roll drops old ones.
- Precip/convection "no data" is no longer distinguishable from "no events" —
  both read `0/0/0` rather than NULL, because the daily table only carries the
  three event counts.

**Rollback:** `VERIFICATION_MONTHLY_ROLLUP_ENABLED=0`. Nothing in the app reads
this table yet; it exists for offline analysis.

Note that re-rolling any day invalidates its month's monthly rows (they are
deleted and re-derived on the next monthly pass). That is what stops a late
backfill from being permanently invisible to the monthly aggregate — but it
does mean a large `rollup-daily-stats --rebuild` will clear the monthly table
until `rollup-monthly-stats` runs again. Run them in that order.

### Phase 4 follow-up — daily-stats retention (separate decision)

**Gate:** `VERIFICATION_DAILY_STATS_RETENTION_MONTHS=18` (requires
`VERIFICATION_MONTHLY_ROLLUP_ENABLED=1` — it runs in the same retention-loop
step, right after the monthly rollup).

Do **not** enable this at the same time as Phase 4. The validation for the
monthly rollup is "re-derive a month from its daily rows and confirm it
matches", which becomes impossible once the daily rows are gone.

- [ ] Monthly rollups validated against the daily data for several months.
- [ ] Confirm nothing reads `verification_daily_stats` beyond 18 months —
      today the longest dashboard window is 90 days and the bias leaderboard
      is 30.
- Daily stats are deliberately **not** archived: unlike raw scores they are
  fully re-derivable from the Parquet archive.

**Try it first without the gate:**

```bash
python -m weatherbrief.verify rollup-monthly-stats --prune-daily 18
```

Its own gate: a month with no rows in `verification_monthly_stats` is never
pruned, and the refusal is logged (`refusing to prune it. Run
verify rollup-monthly-stats first`). Relatedly, `rollup_month` refuses to
re-roll a month whose daily rows are gone *and* which already has monthly
rows — otherwise a `--rebuild` after this prune would delete a permanent
aggregate and insert nothing in its place.

**Rollback:** set back to `0`. Deleted rows are re-derivable by re-running
`verify rollup-daily-stats --rebuild` **only for days still inside the raw
180-day window**; older days need a re-import from Parquet first.

---

## Test coverage map

What each automated test protects, so a failure points at a phase:

| Test module | Protects |
|---|---|
| `tests/test_verification_global_rollup.py` | Phase 1: global/activity/TAF rollup correctness, gust columns, TAF `days_out` bucketing on both dialects, `completed_days` source equality — and `TestReadSwitchAgreement`, which asserts gate-on and gate-off report identical numbers |
| `tests/test_verification_archive.py` | Phase 2: finality rules, manifest write/verify, full-column round-trip export → re-import into an empty DB, mid-write insertion race, tamper/loss/drift detection, gap retry in incremental mode, the prune gates (missing file, corrupt file, rows newer than the archive, already-pruned month), snapshot prune stalling without a safe archive |
| `tests/test_retention.py::TestPruneRawObservations` | Phase 3: both gates, flight-score and flight-linked-observation exemptions, batched deletes |
| `tests/test_retention.py::TestRotateSnapshotInbox` | Phase 3: inbox rotation, off by default |
| `tests/test_verification_rollup.py` | Phase 4: monthly-from-daily rewrite, TAF out of scope, month-defers-on-real-gap, day-re-roll invalidates its month, `rollup_month`'s no-source guard, `rebuild_all_months`, `prune_daily_stats`' monthly gate |
| `tests/test_verification_stats_rollup_gate.py` | Pre-existing #154 gate — still the reference that rollup-backed numbers equal raw-computed ones |

Run the set with:

```bash
pytest tests/test_verification_global_rollup.py tests/test_verification_archive.py \
       tests/test_retention.py tests/test_verification_rollup.py \
       tests/test_verification_gust.py tests/test_verification_daily_rollup.py \
       tests/test_verification_stats_rollup_gate.py
```

## Out of scope (tracked separately)

Index removal beyond the Phase-1 final step (invisible-index evaluation needs
production observation once the last raw-scan consumers are gone), InnoDB
buffer-pool sizing, a `TZDateTime` TypeDecorator, and a TAF monthly rollup.
