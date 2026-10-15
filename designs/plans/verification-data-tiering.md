# Verification data tiering — phased rollout plan (#522)

> **Status (2026-10-16): Phases 0–3 are done and live in production. Phase 4
> has not started.** Raw retention has run at 90 days since 2026-10-15; the
> first prune (April–June) validated exactly, and the scheduler now takes one
> month at a time as each falls past the window. The Phase 1 read gate has
> been removed from the code — the global rollups are simply how unfiltered
> requests are answered. Phase 4 is still enabled by an environment variable
> plus a container recreate, and needs no code change.

Read `designs/metar-taf-accuracy.md` for the architecture; this file is the
runbook and the record of how the rollout actually went — including the places
where the original plan was wrong. Day-by-day measurements and operational
traps live in `scratch/verification-tiering/{HANDOFF,OUTSTANDING}.md`
(gitignored).

## Why

Production at issue time: `verification_scores` 8.8M rows / ~3 GB (2 GB of it
indexes), `verification_observations` 2.3M rows / ~890 MB and growing ~800K
rows/month, `verification_daily_stats` 1.1M rows, `taf_verification_scores`
330K rows — against a shared MySQL server with a 1 GB InnoDB buffer pool.
Every dashboard and digest read window is ≤90 days, so the old rows serve no
live query: they are cold data sitting in the most expensive format available.
By the time pruning went on (2026-10-15) `verification_scores` had reached
~5 GB / ~16.5M rows — the growth this design exists to stop.

Target — three explicit tiers:

| Tier | Store | Retention | Serves |
|---|---|---|---|
| Raw operational | MySQL (`verification_observations`, `verification_scores`, `taf_verification_scores`, `airport_forecast_snapshots`) | 90 days (snapshots 10, unchanged) | scoring, rollup jobs, notable-misses/missed-warnings, ad-hoc debugging |
| Aggregates | MySQL rollup tables (per-airport daily, **global daily**, **TAF daily**, activity, monthly) | forever | all dashboard/digest/leaderboard reads |
| Row-level archive | Parquet in `DATA_DIR/archive/verification/` | forever | re-scoring, calibration, data science (DuckDB), re-import, Historical tab beyond the raw window |

## Configuration reference

Every remaining gate, its shipping default, and its state in production. All
are read through `tasks/verification_tiering.py` — nothing reads these env vars
directly.

| Env var | Ships as | Phase | State in prod | Meaning |
|---|---|---|---|---|
| `VERIFICATION_ARCHIVE_ENABLED` | `0` | 2 | **`1` since 2026-08-19** | Daily retention loop runs the Parquet archive writer — and makes the 10-day snapshot prune archive-gated (see Phase 2) |
| `VERIFICATION_RAW_RETENTION_DAYS` | `9999` (disabled) | 3 | **`90` since 2026-10-15** | Online window for raw obs/scores/TAF scores. Also switches the Historical tab's observation reads to the archive past the window |
| `VERIFICATION_PRUNE_REQUIRE_ARCHIVE` | `1` | — | unset (=1) | No verified archive manifest → no delete. Safety belt; only turn off if abandoning the archive |
| `VERIFICATION_MONTHLY_ROLLUP_ENABLED` | `0` | 4 | off | Retention loop rolls completed months into monthly stats |
| `VERIFICATION_DAILY_STATS_RETENTION_MONTHS` | `0` (keep) | 4 follow-up | unset | Prunes `verification_daily_stats` older than N months, and only for months that already have a monthly rollup. Runs from the retention loop alongside the monthly rollup (so it needs `VERIFICATION_MONTHLY_ROLLUP_ENABLED=1` too) |
| (path) `DATA_DIR/archive/verification/` | — | 2 | — | Archive root |

**Removed (2026-10-16):** `VERIFICATION_GLOBAL_ROLLUP_READS` (the Phase 1 read
gate — the rollup path is now unconditional, see Phase 1) and
`SNAPSHOT_INBOX_RETENTION_DAYS` (in-app inbox rotation never worked as
deployed; the Mac mini prunes the inbox — see Phase 3). Prod `.env` may still
carry `VERIFICATION_GLOBAL_ROLLUP_READS=1`; it is a no-op and can be deleted.

Deployment prerequisite for Phase 2 onward: `pyarrow>=15` (declared in
`pyproject.toml`; imported lazily so a missing wheel doesn't break startup
while the archive is off). Present in the prod container (25.0.1 — re-check
after any `--build`).

**Changing a gate means `docker compose up -d weatherbrief`, not
`docker compose restart`** — a plain restart does not re-read `.env`, so the
change appears not to work. The recreate also restarts every scheduled loop,
and the retention pass runs 120 s after the container starts, then every 24 h
from there — so the pass has no fixed clock time and every deploy moves it.

---

## Phase 0 — deploy the code — DONE 2026-08-04

Migration `087_verification_tiering` (four new tables), the new rollup writers,
the read switch, `tasks/archive.py`, the reworked pruner, the rewritten
monthly rollup and four new CLI verbs (`rollup-daily-stats`,
`rollup-monthly-stats`, `archive`, `prune-raw`) all shipped behind their gates.
Migration 086 dropped `ix_verif_scores_lead`/`_icao`/`_model` (~620 MB) for
real.

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

## Phase 1 — global rollups + read switch — DONE 2026-08-04, gate removed 2026-10-16

The read switch went on in prod on 2026-08-04 as
`VERIFICATION_GLOBAL_ROLLUP_READS=1`, after a 125-day backfill (1.16M groups,
670 s). The agreement check was exact: **zero** rows where the global row ≠ the
sum of its per-airport rows. After two months live the gate was removed from
the code (`c7ae3664`): `_use_global()` is now simply "the request is
unfiltered".

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

**What the read switch changed, and what it did not** (pinned by
`test_verification_global_rollup.py`, which forces each path through
`_use_global`):

- **NWP numbers did not change.** Accuracy, bias, wind-advisory and gust are
  identical on both paths — the global table is the per-airport table with
  `icao` summed away, so it cannot disagree.
  `TestReadSwitchAgreement::test_aggregates_match_across_the_gate` asserts it;
  the prod A/B found **0 NWP value mismatches**. Compare in one process against
  one database state — a before/after cache diff is confounded by METAR ingest
  in between and shows phantom mismatches.
- **Activity and TAF numbers did change, on every period.** The raw path
  filters by exact `observation_time`; the rollups are keyed by UTC `date`, so
  window edges snap to whole UTC days. A trailing-24h request becomes
  "yesterday all day + today so far" (up to ~38h), and TAF counts and accuracy
  shift on **7d and 30d too**, not just 24h (prod: `flight:24h` TAF
  `sample_count` 409 → 751; `category_accuracy_7d` TAF 88.9 → 89.9). Expected
  and accepted; `test_activity_widens_to_utc_days_off_boundary` pins it against
  accidental "fixes".
- **Blast radius: one admin web page, no email.** `/admin/verification`
  (`api/admin.py::get_verification_stats` → `verification_stats.get_digest_data`)
  is the only live reader; `tasks/cache_builder.py` is the writer.
  `notify/verification_email.py::send_verification_digest` exists but nothing
  calls it, and the daily admin digest uses
  `tasks/admin_digest_stats.py::get_admin_digest_data`, which never touches
  these tables.
- Row **order** differs between the two paths with identical values (model
  blocks swap position). Cosmetic; any equality comparison must sort first.
- A country/airport-filtered request reads `verification_daily_stats`, and its
  activity counts still come from raw scores — by design, see below.

**Row-count expectations that were wrong in the first draft** — do not read
these as gaps:

- `taf_verification_daily` holds `days_out = 0` **only**, and always will.
  Scoring pairs each observation with the TAF current at collection time, so
  `MAX(lead_hours) = 20`; a TAF is never evaluated across its full 24–30 h
  validity, so `days_out = 1` never occurs.
- `verification_global_daily_stats` is ~40 rows/day, not ~90 — the model ×
  days_out × source combinations are sparse. Trust the agreement check, not
  the row count.
- TAF history starts 2026-04-04, two days after score history.

**The raw path was kept, not deleted.** The plan's "final step" was to delete
`_SCORES_SOURCE_TIME_HINT` and `_activity_counts_from_raw` once the gate had
been on for a release cycle. That was wrong: the rollups carry no per-airport
distinct-observation count, so a country/airport-filtered activity request can
only be answered from raw scores. Both stay, reached only by filtered requests.
The hint is harmless as long as `ix_verif_scores_source_time` exists — and it
must:

> **Do not drop `ix_verif_scores_source_time`.** `EXPLAIN` on production shows
> it chosen by six live consumers: the daily rollup's per-day scan and its
> activity counts (`source IN (...) AND observation_time >= ... < ...` —
> exactly `(source, observation_time)`), the gust rollup, `get_notable_misses`,
> `get_missed_warnings`, the Phase 3 pruner's day walk, and the Phase 2 archive
> writer's pagination. `ix_verif_scores_source_days_time` cannot substitute: it
> cannot range-seek `observation_time` without an equality on `days_out`.
>
> `ix_verif_scores_source_model_days` (~325 MB) is the drop candidate — its
> only app consumer was the unhinted activity `COUNT(DISTINCT)` the read switch
> replaced. **Not dropped yet, on purpose:** `performance_schema` showed 128M
> reads on it since the 2026-10-01 restart, but that matches the 2026-10-15
> prune's observation-delete anti-join (128.65M rows examined), not app
> traffic. Snapshot `table_io_waits_summary_by_index_usage`, re-read after a
> week with no prune in between, and drop it (by migration) only if
> `count_read` stays flat. `ix_verif_scores_source_days_time` is never chosen
> either, but it is the right shape for `get_notable_misses` /
> `get_missed_warnings` — test it with `FORCE INDEX` before deciding
> drop-versus-adopt.

**The bottleneck moved, and it is not in Phases 2–4.** Post-Phase-1 the
biggest item in the cache rebuild is `count(distinct
flight_verification_map.flight_id)` at ~15 s — a different table, untouched by
this design. Second is the rollup path's own `COUNT(DISTINCT icao)` over
`verification_daily_stats` (~290K rows for a 30-day window; would suit a
`(source, date, icao)` index).

---

## Phase 2 — Parquet archive — DONE 2026-08-19

**Gate:** `VERIFICATION_ARCHIVE_ENABLED=1`, on since 2026-08-19.

**Not purely a copy.** `tasks/archive.py` never deletes a row, but the gate
also changes an existing delete: `snapshot_prune_requires_archive()` turns the
10-day snapshot prune from one unconditional `DELETE` into a per-day loop that
refuses to delete a `fetched_at` day with no verified snapshot manifest. Safe
direction, but it adds a failure mode: **if archiving silently stalls,
`airport_forecast_snapshots` stops being pruned and grows without limit.** It
warns (`Snapshot prune: N day(s) past retention are not safely archived`)
rather than failing — that line means the archive is behind.

**What was run** — staged, smallest table first, on an idle box after 20:00Z
(cycles fire at 06/09/12/15/18 +15 min). Each stage is idempotent, so the run
can be abandoned between stages with nothing lost:

```bash
python -m weatherbrief.verify archive backfill --dry-run
for t in taf_scores snapshots observations scores; do
  python -m weatherbrief.verify archive backfill --table "$t"
  python -m weatherbrief.verify archive verify --table "$t"
done
```

| Stage | Rows | Parquet | InnoDB data | Wall clock |
|---|---|---|---|---|
| `taf_scores` (4 mo) | 327,304 | 3.5 MB | 33 MB | 24 s |
| `snapshots` (9 d) | 940,880 | 44 MB | 153 MB | 83 s |
| `observations` (4 mo) | 2,319,939 | 76 MB | 847 MB | 2 m 51 s |
| `scores` (4 mo) | 8,753,646 | 137 MB | 1,150 MB | 10 m 06 s |
| **total** | **12.34M** | **260 MB** | **2,183 MB** | **~15 min** |

`archive verify` → 21/21 ok. Then `VERIFICATION_ARCHIVE_ENABLED=1` and a
recreate for the daily incremental run.

**The snapshot archive starts with a partial day.** `snapshots/2026-08-09`
holds about half a day: the 10-day prune had already deleted its earlier hours
before the archive ran. Any analysis over the snapshot archive must treat its
first day as incomplete. This decays while Phase 2 waits — every day of delay
loses a day of forecast history.

**Expect one slow morning.** The backfill pulled ~1.8 GB of cold pages through
the 1 GiB buffer pool; the next 06:15Z cache rebuild took 35.9 s against a
30.3 s baseline, recovering through the day. Every app on the shared MySQL
feels it.

**Finality rules (why a period isn't archived yet)**

- Monthly tables: month M is written once ≥10 days into M+1. Later scores
  can't arrive — scoring reads snapshots, and snapshots are pruned at 10 days.
  The current month is never archived.
- Snapshots: day D is written at D+2, partitioned by **`fetched_at`** UTC date
  so partitions line up exactly with the existing 10-day prune predicate.
- The pass logs `Verification archive: {...}` only when it writes something;
  `Retention applied:` shows that a pass ran.

**Validation — all passed**

- [x] `verify archive verify` reports every manifest OK (file present, sha256
      matches, nothing live newer than the archive). Since Phase 3 it also
      notes `live count N < archived M (expected after pruning)` on pruned
      months — informational, not a failure.
- [x] DuckDB round-trip join — `scores.observation_id → observations.id`:
      8,753,646 scored, 8,753,646 joined, **0 orphans**. This is the check the
      phase exists for: it proves the archive is re-scorable. Recipes are in
      the `tasks/archive.py` module docstring. DuckDB is not installed on the
      droplet; use a throwaway venv with `duckdb` **and `pytz`** (the wheel
      doesn't pull it in, and tz-aware queries fail without it).
- [x] Timestamps read back as UTC-aware (`timestamp[us, tz=UTC]`), zero naive.
- [x] Off-box backup of `DATA_DIR/archive/` — a hard gate for Phase 3, since
      the archive becomes the only copy. Runs daily on the Mac mini (pull,
      verify every file against prod's `archive_manifest` sha256, mirror to the
      NAS), with an append-only guard that refuses to run if the file count or
      total bytes ever drop. It trails prod by up to a day; archive files are
      immutable, so that is harmless. Proven end-to-end on a brand-new month
      (August, written by the scheduled pass 2026-09-11).

**Rollback:** `VERIFICATION_ARCHIVE_ENABLED=0`. Files and manifests stay; the
consequences are that Phase 3 stops finding new archived periods (and stops
deleting) and the snapshot prune reverts to unconditional.

---

## Phase 3 — enable pruning — DONE 2026-10-15

**Gate:** `VERIFICATION_RAW_RETENTION_DAYS=90`, on since 2026-10-15 22:38Z.

**Why 90 and not 180** (decided 2026-08-03, do not relitigate). Verification
history only starts 2026-04-01, so a 180-day window prunes *nothing* until late
September, and at ~2.3M scores/month the table would settle around 14M rows —
roughly 60% larger than the 8.8M that motivated this design. The whole point
of Phase 3 is to stop the table growing; 180 postpones that past the point
where it would have doubled.

Nothing reads raw beyond 90 days: `get_notable_misses` and
`get_missed_warnings` use ≤30-day windows, and every dashboard/digest
aggregate — including the 90-day bias leaderboard — reads the rollup tables,
which are kept forever. The one consumer that *does* look further back is the
Historical tab (#629), which reads observations for months past the window
from the monthly Parquet archive — but only when this gate is set (it keys off
`raw_retention_disabled()`). There is margin on top, because the pruner
deletes whole months only and a month must be *entirely* past the cutoff, so
in practice 90–120 days of raw stay online.

The cost is that re-rolling daily stats from raw only reaches back 90 days;
older days need a Parquet re-import first, and **no re-import tool exists** —
the archive CLI has only run/backfill/list/verify. Pruned rows are readable in
DuckDB; putting them back in MySQL means writing a script.

**Preconditions — all met before the first prune**

- [x] ≥2 weeks of validated Phase-2 archives (`archive verify` clean) — since
      2026-08-19.
- [x] Off-box backup of `DATA_DIR/archive/` running and restorable.
- [x] `airport_monthly_summary` has rows for every month to prune (the
      climatology gate) — `verify rollup-summary --all` if not.
- [x] `VERIFICATION_PRUNE_REQUIRE_ARCHIVE` at `1` (default).
- [x] The archive reader tolerates schema evolution (`a137fa8d`). Migration 091
      added `report_type` to observations *after* April–August were archived,
      and the Historical tab asked pyarrow for it by name — every request for
      an instant before the cutoff would have raised `ArrowInvalid`.
      `read_archived_rows` now returns `None` for a column a file predates. Any
      future column added to an archived table needs the same care.

**What was run — gate first, no manual `--apply`**

The original plan was to prune by hand with `--apply` at a quiet time and set
the env gate afterwards. That order is wrong: until the gate is set the
Historical tab does not look in the archive, so April–June METARs would have
vanished from it between the manual prune and the gate. Do it the other way
round:

```bash
# 1. Dry run. Reports which months pass BOTH gates and why the others don't.
#    Read-only; ~35 s.
python -m weatherbrief.verify prune-raw --retain-days 90

# 2. Set the gate in .env and recreate at a quiet time. The retention pass
#    runs 120 s after the container starts, so the recreate time IS the prune
#    time.
VERIFICATION_RAW_RETENTION_DAYS=90   # then docker compose up -d weatherbrief
```

The first pass took every eligible month at once — April, May and June
(cutoff 2026-07-17) — in 15m40s, no refusals:

```
Raw retention: pruned 1435899 observations, 6029305 scores, 203129 TAF scores across 3 month(s)
```

From then on the scheduler takes one month at a time, each as it falls fully
past 90 days (July becomes eligible ~2026-10-30) — far smaller passes.

**What the pruner will and won't touch**

- Deletes whole months only, and only months that are past the cutoff **and**
  summarised **and** safely archived — meaning, for all three monthly tables:
  a manifest exists, its Parquet file is still on disk, the file still hashes
  to the recorded sha256, and no live row in the month is newer than the
  archive (`max_id`). Any of those failing is logged loudly
  (`Raw retention: refusing to prune — …`) and the month is left alone.
- `max_id` catches rows *added* after archiving, not rows *updated in place*.
  Any repair to an already-archived month must re-archive it (`--force`)
  before it is pruned.
- Months with nothing left to delete are skipped *before* the gate runs, so an
  already-pruned month neither re-hashes its archive nor reports itself as
  blocked. If you see a "refusing to prune" line, it is a real problem.
- Never deletes `source='flight'` scores — pilot/debrief context, ERA5
  re-analysis value. Mirrors the debriefed-flights T2 exemption. Not "tiny
  volume" as first written: ~1M rows, ~6% of the table before the prune.
- Never deletes an observation referenced by `flight_verification_map`, or one
  that still has a flight score (NWP or TAF). Observations are shared between
  tracks and have no source column; every FK into them is `ON DELETE CASCADE`,
  so deleting one would cascade into the exempt flight rows hanging off it.
- Deletes in 5,000-row batches with a commit per batch, walking a day at a
  time; scores and TAF scores before observations. Never one multi-month
  statement on the shared server.

**Validation — every check exact**

- [x] Summary line present, no `refusing to prune`.
- [x] Flight scores (1,009,171), flight TAF scores (81,363) and
      `flight_verification_map` rows (181,576) identical to the pre-prune
      baseline.
- [x] Archive manifests minus deleted rows equals what survives, to the row;
      everything left before the cutoff is flight or flight-linked.
- [x] Orphaned `flight_verification_map` rows: 0
      ```sql
      SELECT COUNT(*) FROM flight_verification_map m
      LEFT JOIN verification_observations o ON m.observation_id = o.id
      WHERE m.observation_id IS NOT NULL AND o.id IS NULL;   -- expect 0
      ```
- [x] Oldest standalone score moved to 2026-07-01. Oldest observation stays
      2026-04-01 — correct, flight-linked observations are exempt.
- [x] Historical tab at an instant in June returns METARs from Parquet.
- [x] Dashboard unchanged (it reads rollups).
- [ ] Table sizes: `information_schema` did not move for minutes afterwards —
      InnoDB refreshes those stats lazily. `.ibd` files never shrink after a
      delete; freed pages show up as `data_free`. Judge by row counts and
      `data_length`/`data_free`, never by `du`.

Side effects to expect from a large prune: ~1 GB of binlog (ROW format, kept
30 days); a few colder cycles while the buffer pool refills; heavy reads on
whatever index the observation-delete anti-join uses, which will distort any
`performance_schema` index-usage reading taken across it.

**Snapshot inbox — handled outside the app.** The plan also had the app rotate
`eu-*/us-*.sqlite` transport artifacts out of `SNAPSHOT_INBOX_DIR`
(`SNAPSHOT_INBOX_RETENTION_DAYS=30`). That could never have worked: the
container doesn't have `SNAPSHOT_INBOX_DIR` set, and compose mounts the inbox
read-only, so every `unlink()` would fail. The inbox had grown to 3 GB. It is
now pruned by the Mac mini's forecast runner, which after each successful push
deletes artifacts older than 7 days by arrival time (`-ctime`; rsync preserves
the mini's mtime) over the same ssh. The in-app rotation and its env var were
removed (`c7ae3664`). The artifacts are redundant: the mini keeps its own copy,
and MySQL + Parquet are the source of truth.

**Rollback:** set `VERIFICATION_RAW_RETENTION_DAYS=9999` and recreate.
Already-deleted rows exist only in the Parquet archive (prod, Mac mini, NAS) —
which is exactly why the gate refuses to delete anything that isn't in it.

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
| `tests/test_verification_global_rollup.py` | Phase 1: global/activity/TAF rollup correctness, gust columns, TAF `days_out` bucketing on both dialects, `completed_days` source equality — and `TestReadSwitchAgreement`, which forces the raw and rollup paths and asserts they report identical NWP numbers (and pins the UTC-day widening of activity) |
| `tests/test_verification_archive.py` | Phase 2: finality rules, manifest write/verify, full-column round-trip export → re-import into an empty DB, mid-write insertion race, tamper/loss/drift detection, gap retry in incremental mode, the prune gates (missing file, corrupt file, rows newer than the archive, already-pruned month), snapshot prune stalling without a safe archive, reading a file that predates a column |
| `tests/test_retention.py::TestPruneRawObservations` | Phase 3: both gates, flight-score and flight-linked-observation exemptions, batched deletes |
| `tests/test_verification_rollup.py` | Phase 4: monthly-from-daily rewrite, TAF out of scope, month-defers-on-real-gap, day-re-roll invalidates its month, `rollup_month`'s no-source guard, `rebuild_all_months`, `prune_daily_stats`' monthly gate |
| `tests/test_verification_stats_rollup_gate.py` | Pre-existing #154 gate — still the reference that rollup-backed numbers equal raw-computed ones |

Run the set with:

```bash
pytest tests/test_verification_global_rollup.py tests/test_verification_archive.py \
       tests/test_retention.py tests/test_verification_rollup.py \
       tests/test_verification_gust.py tests/test_verification_daily_rollup.py \
       tests/test_verification_stats_rollup_gate.py tests/test_standalone_verification.py
```

## Out of scope (tracked separately)

The `flight_verification_map` and `verification_daily_stats` COUNT(DISTINCT)
hot spots described under Phase 1; the `ix_verif_scores_source_model_days`
drop (pending a clean read count, see Phase 1); InnoDB buffer-pool sizing
(looked at after Phase 3 and left at 1 GiB — the cache hit rate was 99.91%); a
Parquet → MySQL re-import tool; a `TZDateTime` TypeDecorator; and a TAF
monthly rollup.
