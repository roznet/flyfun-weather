# Verification data tiering — phased rollout plan (#522)

> **Status (2026-08-15): Phases 0 and 1 are done and live in production.
> Phases 2–4 are code-complete but dark.** Every remaining phase is enabled by
> an environment variable plus a container recreate, and disabled by the
> reverse. Nothing below needs a code change, and no phase deletes anything
> until its gate is explicitly turned on.

Read `designs/metar-taf-accuracy.md` for the architecture; this file is the
runbook. Rollout state, measured numbers and traps live in
`scratch/verification-tiering/{HANDOFF,OUTSTANDING}.md` — read those before
starting a phase, and update them after.

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
| Raw operational | MySQL (`verification_observations`, `verification_scores`, `taf_verification_scores`, `airport_forecast_snapshots`) | 90 days (snapshots 10, unchanged) | scoring, rollup jobs, notable-misses/missed-warnings, ad-hoc debugging |
| Aggregates | MySQL rollup tables (per-airport daily, **global daily**, **TAF daily**, activity, monthly) | forever | all dashboard/digest/leaderboard reads |
| Row-level archive | Parquet in `DATA_DIR/archive/verification/` | forever | re-scoring, calibration, data science (DuckDB), re-import |

## Configuration reference

Every gate, its shipping default, and the phase that changes it. All are read
through `tasks/verification_tiering.py` — nothing reads these env vars
directly.

| Env var | Ships as | Phase | Set to | State in prod | Meaning |
|---|---|---|---|---|---|
| `VERIFICATION_GLOBAL_ROLLUP_READS` | `0` | 1 | `1` | **ON since 2026-08-04** | Unfiltered dashboard/digest aggregates read the global rollups |
| `VERIFICATION_ARCHIVE_ENABLED` | `0` | 2 | `1` | off | Daily retention loop runs the Parquet archive writer |
| `VERIFICATION_RAW_RETENTION_DAYS` | `9999` (disabled) | 3 | `90` | unset | Online window for raw obs/scores/TAF scores |
| `VERIFICATION_PRUNE_REQUIRE_ARCHIVE` | `1` | — | leave `1` | unset (=1) | No verified archive manifest → no delete. Safety belt; only turn off if abandoning the archive |
| `SNAPSHOT_INBOX_RETENTION_DAYS` | `0` (keep) | 3 | `30` | unset | Rotates `eu-*/us-*.sqlite` out of `SNAPSHOT_INBOX_DIR` |
| `VERIFICATION_MONTHLY_ROLLUP_ENABLED` | `0` | 4 | `1` | off | Retention loop rolls completed months into monthly stats |
| `VERIFICATION_DAILY_STATS_RETENTION_MONTHS` | `0` (keep) | 4 follow-up | `18` | unset | Prunes `verification_daily_stats` older than N months, and only for months that already have a monthly rollup. Runs from the retention loop alongside the monthly rollup (so it needs `VERIFICATION_MONTHLY_ROLLUP_ENABLED=1` too) |
| (path) `DATA_DIR/archive/verification/` | — | 2 | — | — | Archive root |

Deployment prerequisite for Phase 2 onward: `pyarrow>=15` (declared in
`pyproject.toml`; imported lazily so a missing wheel doesn't break startup
while the archive is off). Verified present in the prod container (25.0.0).

**Flipping a gate means `docker compose up -d weatherbrief`, not
`docker compose restart`** — a plain restart does not re-read `.env`, so the
gate appears not to work. This applies to every "then restart" below.

---

## Phase 0 — deploy the code — DONE 2026-08-04

Migration `087_verification_tiering` (four new tables), the new rollup writers,
the gated read switch, `tasks/archive.py`, the reworked pruner, the rewritten
monthly rollup and four new CLI verbs (`rollup-daily-stats`,
`rollup-monthly-stats`, `archive`, `prune-raw`) all shipped behind their gates.
Migration 086 dropped `ix_verif_scores_lead`/`_icao`/`_model` (~620 MB) for
real. Alembic head has since moved on (088+); Phase 0's own head was `087`.

Two things took effect immediately and deliberately, both behaviour-preserving:

- `verification_daily_rollup.rollup_day` writes the three new daily tables
  alongside `verification_daily_stats` on every scheduled cycle.
- `ensure_future_partitions` and its scheduler call were **deleted**. Dormant
  machinery: no migration ever partitioned `verification_observations`, and
  MySQL forbids foreign keys on partitioned tables anyway, so it returned 0 on
  every run since it was written. (`designs/../archive/patterns-and-spotlight.md`
  records the same thing from the partitioning side.)

Validated by the 09:15Z `standalone_light` cycle logging
`daily rollup 2026-08-03: 12042 per-airport, 45 global, 2 activity, 2 TAF
groups`, with table counts reconciling exactly.

---

## Phase 1 — global rollups + read switch — DONE, gate ON 2026-08-04

`VERIFICATION_GLOBAL_ROLLUP_READS=1` is live in prod `.env`. Backfill was 125
days / 1.16M groups / 670 s, and the agreement check was exact: **zero** rows
where the global row ≠ the sum of its per-airport rows.

**Measured, scheduled-cycle to scheduled-cycle: 39.6 s → 30.3 s (~23%)** for
the six stats cache entries; `stats:standalone:30d` activity 17.2 s → 7.2 s;
zero raw `count(distinct verification_scores…)` entries in the slow log after
the flip. (A 7.7× figure was reported first — it was a warm back-to-back
rebuild and is **not** representative. Do not quote it.)

**Backfill lesson, if this ever needs redoing:** run `rollup_day` in a per-day
loop with a commit per day, **not** `verify rollup-daily-stats --rebuild`. The
CLI path commits once after all days: ~1.5M rows deleted and reinserted in one
transaction on a shared instance, holding locks on `verification_daily_stats`
throughout — and an interrupt loses everything. The script is at
`scratch/verification-tiering/backfill_rollup.py`; a rewrite must mirror
`verify/__main__.py::_init_db` and call `get_engine()`, or every mapper raises
`UnboundExecutionError`.

**What the read switch changes, and what it must not** (pinned by
`test_verification_global_rollup.py`):

- Accuracy, bias, wind-advisory and gust numbers are **identical** across the
  gate — the global table is the per-airport table with `icao` summed away, so
  it cannot disagree. `TestReadSwitchAgreement::test_aggregates_match_across_the_gate`
  asserts it; the prod A/B found **0 NWP value mismatches**. Compare with
  `scratch/verification-tiering/ab_gate.py`, in one process against one
  database state — a before/after cache diff is confounded by METAR ingest in
  between and shows phantom mismatches.
- **Activity and TAF counts widen to UTC-day buckets, and that is expected.**
  Gate-off reads raw by exact `observation_time`; gate-on reads a rollup keyed
  by UTC `date`, so a trailing-24h request becomes "yesterday all day + today
  so far" — up to ~38h. TAF shifts on **7d and 30d too**, not just 24h: the
  window edges snap to UTC days whatever the period.
  `test_activity_widens_to_utc_days_off_boundary` pins this against accidental
  "fixes". Blast radius is **one admin web page and no email at all**:
  `/admin/verification` (`api/admin.py::get_verification_stats` →
  `verification_stats.get_digest_data`) is the only live reader;
  `notify/verification_email.py::send_verification_digest` exists but nothing
  calls it, and the daily admin digest uses
  `tasks/admin_digest_stats.py::get_admin_digest_data`, which never touches
  these tables.
- Row **order** differs between the two paths with identical values. Cosmetic;
  any equality comparison must sort first.
- A country/airport-filtered dashboard request still reads
  `verification_daily_stats` by design.

**Row-count expectations that were wrong in the first draft** — do not read
these as gaps: `taf_verification_daily` holds `days_out = 0` **only** (scoring
pairs each observation with the TAF current at collection time, so
`MAX(lead_hours) = 20`), and `verification_global_daily_stats` is ~40 rows/day,
not ~90 (model × days_out × source combinations are sparse). Trust the
agreement check, not the row count. TAF history starts two days after score
history.

**Rollback:** `VERIFICATION_GLOBAL_ROLLUP_READS=0`, then
`docker compose up -d weatherbrief`. The old code path is still there and still
correct.

**Phase 1 final step — still outstanding, only after a full release cycle:**

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

**The bottleneck has moved, and it is not in Phases 2–4.** Post-Phase-1 the
biggest item in the cache rebuild is `count(distinct
flight_verification_map.flight_id)` at ~15 s — a different table, untouched by
this design. Second is the new rollup path's own `COUNT(DISTINCT icao)` over
`verification_daily_stats` (~290K rows for a 30-day window; would suit a
`(source, date, icao)` index). Both are better value than anything below.

---

## Phase 2 — Parquet archive (write-only; deletes nothing) — NEXT

**Gate:** `VERIFICATION_ARCHIVE_ENABLED=1`

**Precondition to check**

- [ ] `pyarrow` importable in the production venv:
      `python -c "import pyarrow; print(pyarrow.__version__)"`. (25.0.0 as of
      the Phase 0 deploy — re-check after any `--build`.)
- [ ] `DATA_DIR` has headroom. Budget from a dry run (below); zstd Parquet
      typically lands ~10–20× smaller than the InnoDB footprint, so the whole
      current history should be single-digit GB.
- [ ] #519's METAR month-boundary residue is repaired in production data.
      **Done 2026-08-04** — 57 orphaned `taf_verification_scores` rows fixed.
      The backfill freezes whatever is in the database into the archive, so
      this is the last cheap moment to catch anything similar.

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
VERIFICATION_ARCHIVE_ENABLED=1   # then docker compose up -d weatherbrief
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
      before Phase 3** — after pruning, these files are the only copy. The
      user has explicitly confirmed this as a hard gate.

**Let it run for ≥2 weeks** before Phase 3, and confirm the daily incremental
archive keeps up (`Verification archive: {...}` in the log, no errors).

**Rollback:** `VERIFICATION_ARCHIVE_ENABLED=0`. Files and manifests stay; the
only consequence is that Phase 3 stops finding new archived periods and
therefore stops deleting.

---

## Phase 3 — enable pruning

**Gates:** `VERIFICATION_RAW_RETENTION_DAYS=90`, and separately
`SNAPSHOT_INBOX_RETENTION_DAYS=30`.

**Why 90 and not 180** (decided 2026-08-03, do not relitigate). Verification
history only starts 2026-04-01, so a 180-day window prunes *nothing* until late
September, and at ~2.3M scores/month the table would settle around 14M rows —
roughly 60% larger than the 8.8M that motivated this design. 90 days makes
April prunable immediately (~1.75M scores, ~20% of the table, ~780 MB across
scores and observations) and holds the steady state near 7M rows, about 20%
below today. The whole point of Phase 3 is to stop the table growing; 180
postpones that past the point where it would have doubled.

> Stale 180s still in the tree: `verification_tiering.RAW_RETENTION_TARGET_DAYS`
> (documentation-only, no functional consumer), its module docstring, the
> `verify prune-raw` error text, and `designs/multi-user-deployment.md`. This
> runbook is the decision of record.

Nothing reads raw beyond 90 days: `get_notable_misses` and
`get_missed_warnings` use ≤30-day windows, and every dashboard/digest
aggregate — including the 90-day bias leaderboard — reads the rollup tables,
which are kept forever. There is margin on top of that, because the pruner
deletes whole months only and a month must be *entirely* past the cutoff, so
in practice 90–120 days of raw stay online.

The cost is that re-rolling daily stats from raw only reaches back 90 days;
older days need a Parquet re-import first. Phase 1's full-history backfill is
already done, so that slack has been banked — but it is why Phase 2 must be
validated and backed up off-box before this gate goes on.

**Preconditions — all of them**

- [ ] ≥2 weeks of validated Phase-2 archives (`archive verify` clean).
- [ ] Off-box backup of `DATA_DIR/archive/` confirmed running and restorable.
- [ ] `airport_monthly_summary` has rows for every month you intend to prune
      (the climatology gate) — `verify rollup-summary --all` if not.
- [ ] `VERIFICATION_PRUNE_REQUIRE_ARCHIVE` is `1` (default). Leave it there.

**Run — dry run first, always**

```bash
# Reports which months pass BOTH gates and why the others don't. Deletes nothing.
python -m weatherbrief.verify prune-raw --retain-days 90

# When the report looks right:
python -m weatherbrief.verify prune-raw --retain-days 90 --apply
```

Then hand it to the scheduler:

```bash
VERIFICATION_RAW_RETENTION_DAYS=90
SNAPSHOT_INBOX_RETENTION_DAYS=30
# then docker compose up -d weatherbrief
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
      windows are ≤30 days, well inside 90.
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
VERIFICATION_MONTHLY_ROLLUP_ENABLED=1   # then docker compose up -d weatherbrief
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
90-day window**; older days need a re-import from Parquet first.

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

The `flight_verification_map` and `verification_daily_stats` COUNT(DISTINCT)
hot spots described under Phase 1, index removal beyond the Phase-1 final step
(invisible-index evaluation needs production observation once the last raw-scan
consumers are gone), InnoDB buffer-pool sizing, a `TZDateTime` TypeDecorator,
and a TAF monthly rollup.
