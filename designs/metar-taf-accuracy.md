# METAR/TAF Accuracy System

> Dual-track verification: flight-based observation collection during active flights + standalone airport monitoring at fixed UTC hours, with daily digest email and admin dashboard.

## Intent

We already have NWP model forecasts stored in briefing packs (GFS, ECMWF, ICON, etc.) and we fetch METAR/TAF on D-0 for the current observation comparison. But we don't systematically **archive** observations or **score** model accuracy over time.

This system has two verification tracks:
1. **Flight-based**: Collects METAR/TAF observations during flight windows (1h before → flight end + 1h), scores against briefing pack forecasts
2. **Standalone**: Monitors ~830 pan-European watchlist airports via three decoupled loops — METAR ingest (every 30 min), forecast fetch (07/19 UTC), and scoring (06/09/12/15/18 UTC) — covering GFS/ICON/ECMWF out to the forecast map's horizon (gfs 6d, icon 4d, ecmwf 6d — see `tasks/forecast_grid.py`)
3. **Scores** TAF accuracy against METARs (TAF is also a forecast — was it right?)
4. **Archives** everything in a standalone, anonymized verification database
5. **Reports** daily verification digest via email + admin web dashboard

Over time this builds a dataset answering: "How accurate is GFS vs ECMWF at D-3 for ceiling in Alpine regions?"

## Core Design Principle: Observations Are Independent of Flights

The verification database is **not owned by any flight or user**. Observations are keyed by `(icao, observation_time)` — if three users fly through LFPG at 14:00, there's one observation row. A thin mapping table links flights to observations; when a user deletes their account, the mapping disappears but the verification data stays.

This makes the accuracy database a **growing, anonymized, community asset**.

## Architecture

```
scheduler.py
├── run_verification_loop()             ← flight-based, 10-min poll
│   └── _run_verification_once()        ← collect_and_store (+ scoring inside)
├── run_metar_ingest_loop()             ← standalone, every 30 min (:00/:30)
│   └── run_metar_ingest_cycle()        ← fetch METAR/TAF for watchlist → obs
├── run_forecast_fetch_loop()           ← standalone, fires at FORECAST_FETCH_HOURS_UTC=[7,19]
│   └── run_standalone_cycle(fetch=True, score=False)   ← "forecast" cycle
├── run_standalone_verification_loop()  ← standalone, fires at VERIFICATION_HOURS_UTC=[6,9,12,15,18]
│   └── run_standalone_cycle(fetch=False, score=True)   ← "light" cycle (score-only)
├── run_digest_loop()                   ← daily at DIGEST_HOUR_UTC (default 08:00)
│   └── send_verification_digest()

tasks/verification.py                   ← flight-based collection
├── collect_and_store()                 ← orchestrator (gather + fetch + store + score)
├── find_verifiable_flights()
├── gather_airports()                   ← corridor resolution + dedup
├── fetch_observations_batch()
├── store_observations()
└── finalize_completed_flights()

tasks/scoring.py                        ← flight-based scoring (model + TAF)
├── score_flight()
├── score_completed_flights()
├── backfill_scores()
├── _score_model_vs_metar()
└── _score_taf_vs_metar()

tasks/standalone_verification.py        ← standalone airport monitoring
├── run_metar_ingest_cycle()            ← fetch METAR/TAF → verification_observations (source 'metar_ingest')
├── run_standalone_cycle()              ← forecast / light / full (legacy) cycle
├── _fetch_forecasts_for_model()        ← Open-Meteo multi-point (batches of 100)
├── _enrich_with_sounding()             ← pressure-level sounding analysis
├── _enrich_with_grib()                 ← GFS/ICON cloud diagnostics for ceiling
└── _record_failed_cycle()              ← error capture on failure

tasks/verification_stats.py             ← shared queries for digest + dashboard
├── get_activity_summary()              ← raw (COUNT DISTINCT obs_id), bounded
├── get_category_accuracy()             ← from verification_daily_stats
├── get_notable_misses()                ← raw (needs individual rows)
├── get_category_bias_stats()           ← from verification_daily_stats (2 buckets)
├── get_wind_advisory_accuracy()        ← from verification_daily_stats
├── get_gust_accuracy()                 ← from verification_daily_stats; TAF raw (#491)
├── get_optimistic_bias_leaderboard()   ← from verification_daily_stats (#154)
└── get_digest_data()                   ← orchestrator → VerificationDigestData

tasks/verification_gust.py              ← gust definitions + aggregates + backfill (#491)
├── GUST_FLAG_THRESHOLD_KT              ← 10 kt — the METAR gust-group criterion
├── forecast_shows_gust()               ← used by scoring to set model_gust_flag
├── realised_peak_kt()                  ← obs gust if reported, else obs mean wind
├── gust_aggregate_columns()            ← shared SQL aggregates (daily + monthly)
├── backfill_taf_gust_deltas()          ← all history (obs row holds both gusts)
└── backfill_model_gust()               ← un-pruned snapshot window only
    └── _pick_scored_snapshot()          ← identifies the scored snapshot via
                                           the stored wind delta; NULL if ambiguous

tasks/verification_daily_rollup.py      ← daily pre-aggregation (#154, #522)
├── rollup_day()                        ← writes all FOUR daily tables, idempotent
├── completed_days()                    ← UTC dates with un-rolled scores
├── taf_days_out_expr()                 ← portable lead_hours // 24 bucket
├── rollup_all_complete_days()          ← orchestrator
├── rollup_today_and_pending()          ← + partial today, called from scheduler
└── rebuild_all_days()                  ← post-migration re-roll

tasks/verification_rollup.py            ← monthly pre-aggregation (NWP only, #522)
├── completed_months()                  ← months in daily stats, not yet rolled
├── rollup_month()                      ← SQL DELETE + INSERT-SELECT over daily
├── rebuild_all_months()                ← re-derive existing months
├── prune_daily_stats()                 ← Phase 4 follow-up, gated off
├── category_direction()                ← match/optimistic_1-2/pessimistic_1-2
├── advisory_direction()                ← match/optimistic/pessimistic
└── run_monthly_rollup()                ← entry point: find + process all

tasks/verification_tiering.py           ← #522 phase gates (env-var reads)
├── global_rollup_reads_enabled()       ← Phase 1 read switch
├── archive_enabled() / archive_root()  ← Phase 2
├── raw_retention_days() / prune_requires_archive()  ← Phase 3
└── monthly_rollup_enabled() / daily_stats_retention_months()  ← Phase 4

tasks/archive.py                        ← row-level Parquet archive (#522)
├── ARCHIVE_SPECS                       ← table → (time column, granularity)
├── archive_period()                    ← keyset SELECT → parquet → verify → manifest
├── pending_periods() / final_periods() ← finality rules
├── run_archive()                       ← scheduled entry point
├── verify_archives()                   ← recheck sha256 + live counts
└── month_archive_ok() / snapshot_day_archived()  ← the gates retention consults

tasks/cache_builder.py                  ← pre-computed API response cache
├── rebuild_stats_cache()               ← stats:{source}:{period} (24h/7d/30d)
├── rebuild_bias_leaderboard_cache()    ← bias_leaderboard:{model}:{d}:{period}
├── rebuild_forecast_map_cache()        ← forecast_map:{day}:{hour}
├── is_stale()                          ← compare source_max_time vs live MAX
├── get_cached()                        ← load JSON blob by cache_key
└── rebuild_all()                       ← entry point: rebuild all 3 cache types

tasks/airport_watchlist.py              ← standalone airport discovery
└── discover_airports()                 ← euro_aip DB + aviationweather.gov

tasks/standalone_grib.py                ← GRIB ceiling adapter for standalone
└── fetch_gfs_cloud_diag()

notify/verification_email.py            ← HTML + plaintext digest email
└── send_verification_digest()

models/verification.py                  ← Pydantic models
├── VerificationObservation, VerificationScore, TafVerificationScore
├── VerificationSummary
├── VerificationDigestData              ← complete digest payload
├── ActivitySummary, CategoryAccuracyRow, NotableMiss
├── CategoryBiasStats, WindAdvisoryStats, MissedWarning

db/models.py                            ← SQLAlchemy tables
├── VerificationObservationRow          ← (icao, observation_time)
├── VerificationScoreRow                ← (icao, time, model, init_time, source)
├── TafVerificationScoreRow             ← (icao, obs_time, taf_issue_time, source)
├── FlightVerificationMapRow            ← thin linkage, CASCADE on flight delete
├── VerificationCycleRow                ← performance metrics per cycle
├── AirportForecastSnapshotRow          ← standalone NWP forecasts
├── VerificationDailyStatsRow           ← pre-aggregated daily rollup (#154)
├── VerificationMonthlyStatsRow         ← pre-aggregated monthly rollup
├── VerificationGlobalDailyStatsRow     ← daily rollup, ICAO collapsed (#522)
├── VerificationActivityDailyRow        ← daily COUNT(DISTINCT obs) (#522)
├── TafVerificationDailyRow             ← TAF daily rollup, days_out keyed (#522)
├── ArchiveManifestRow                  ← Parquet archive row counts + sha256 (#522)
├── VerificationCacheRow                ← JSON cache for dashboard/map responses

API: GET /api/admin/verification        ← admin dashboard data (cache-aware; in api/admin.py)
CLI: python -m weatherbrief.verify      ← backfill, manual runs, export
```

## Database Schema

Full column definitions live in `db/models.py` (+ Alembic migrations). This section captures only each table's purpose, its natural key, and the non-obvious design choices — not the column list (read the model for that).

### `verification_observations` — ground-truth archive
METAR + active-TAF fields as fetched. Natural key `UNIQUE(icao, observation_time)` — the same METAR is never stored twice. Indexed on `icao` and `observation_time`.

### `verification_scores` — model vs reality
One row per `(icao, observation_time, model, model_init_time, source)`. The `source` column distinguishes flight-based from standalone scores — **all queries filter by source** to prevent mixing. Only two stored values: `'flight'` and `'standalone'` (the `standalone_full`/`standalone_light`/`standalone_forecast` distinction lives only on `verification_cycles.source`, for per-cycle metrics, never on the scores themselves). Migration 055 adds a composite `(source, days_out, observation_time)` index to keep dashboard queries off full table scans.

Gust is stored as a **raw value plus a delta plus a flag** (`model_wind_gust_kt`, `wind_gust_delta_kt`, `model_gust_flag`), unlike every other scored field which keeps only a delta (#491). Two reasons the delta alone doesn't work: the observed gust is NULL on ~90% of hours (a METAR only carries a gust group when the peak exceeds the mean by ~10 kt), so a delta-only column would silently drop exactly the "model called a gust, the airport wasn't gusting" rows; and the forecast gust is **not** recoverable after the fact the way `wind_speed` is, because the snapshot holding it (`airport_forecast_snapshots`) is pruned at >10 days.

### `taf_verification_scores` — TAF vs METAR
TAFs are forecasts too, scored per METAR (one TAF scored against multiple METARs across its validity period — shows accuracy over time). Separate table because the key shape differs: `UNIQUE(icao, observation_time, taf_issue_time, source)`.

TAF gust is only a delta (`wind_gust_delta_kt`): the TAF gust itself already lives permanently on `verification_observations.taf_wind_gust_kt`, reachable via `observation_id`. The delta exists for query symmetry and the rollups, and is fully backfillable for all history.

### `verification_cycles` — per-cycle performance metrics
Timing (`phase_*_ms`) and counts for each collection/scoring cycle, plus nullable `peak_rss_mb`/`peak_cgroup_mb` (migration 051 / #137; legacy rows predate sampling). `source` distinguishes `flight` / `metar_ingest` / `standalone_*`. The `error` column stores the last 500 chars of a failure traceback so post-mortems don't need log access.

### `airport_forecast_snapshots` — standalone NWP forecasts
Open-Meteo forecasts at watchlist airports (surface + ceiling estimates + sounding-derived fields), keyed `UNIQUE(icao, model, model_init_time, forecast_hour)`. Standalone needs its own forecast storage since there is no briefing pack to reference.

### `verification_daily_stats` / `verification_monthly_stats` — pre-aggregated rollups (#154)
One row per `(period, source, model, days_out, icao)`, NWP scores only (TAF out of scope — different key shape). The daily rollup runs after each standalone cycle (`rollup_today_and_pending` before `cache_builder.rebuild_all`); the monthly rollup processes only completed months and is idempotent (delete + reinsert). Design choices worth preserving:

- **SUMs, not averages** — SUMs are additive across periods; averages aren't. The dashboard computes `ceiling_mae = SUM(sum_abs_ceiling) / SUM(n_ceiling)`. Per-field non-NULL counts (`n_ceiling`, `n_wind`, …) preserve the raw query's "skip NULL deltas" semantics.
- **2 category-direction buckets, not 3** — matches the monthly table's shape; the rare 3-step case (VFR↔LIFR) folds into `_2`. The bias-leaderboard formula `(n_cat_opt_1 + 2 * n_cat_opt_2) / n` weights `_2` accordingly.
- **No observation_id** — counts of *distinct observations* can't be recovered from per-group counts (one obs → N scores), so `get_activity_summary` keeps its `COUNT(DISTINCT observation_id)` on the raw `verification_scores` instead.
- **`category_direction(obs, fcst)`** classifies each score as match / optimistic_1/2 (forecast better than reality — dangerous) / pessimistic_1/2 (too conservative). TAF scores use the same classification via `lead_hours // 24 → days_out` bucketing.
- **Gust columns join the observation row** (#491). The daily rollup's SELECT joins `verification_observations` on the score's FK so it can carry both gust conditionings: magnitude sums (`n_gust`, `sum_abs_gust_delta_kt`, `sum_gust_delta_kt`), the forecast-flagged over-peak sums (`n_gust_flagged_peak`, `sum_gust_flagged_over_peak_kt`), and the occurrence contingency (`n_model_gust_flag`, `n_obs_gust`, `n_gust_flag_hit` — false alarms and misses are derived from those three). The join is inner and can't drop rows: the FK is NOT NULL and retention prunes observations and scores in the same window. Monthly stores the MAE/bias equivalents; its TAF rows leave the occurrence columns NULL (the TAF group key is a `lead_hours` bucket with no SQL expression that truncates identically on SQLite and MySQL — TAF gust occurrence is served at query time by `get_gust_accuracy` instead).

### `verification_global_daily_stats` / `verification_activity_daily` / `taf_verification_daily` — #522 rollups

Three tables added so that **no dashboard or digest aggregate reads raw scores**.
All three are written by `rollup_day` alongside the per-airport table, in the
same idempotent DELETE+INSERT idiom.

- **`verification_global_daily_stats`** — key `(date, source, model, days_out)`.
  Identical columns to `verification_daily_stats` minus `icao`, ~90 rows/day
  against ~12K. Written as a `GROUP BY` over the per-airport table rather than
  a second pass over raw, so the two can never disagree about a day. The
  per-airport table stays: it is the only one that can answer a
  country/airport-filtered request, and it feeds the bias leaderboard.
- **`verification_activity_daily`** — key `(date, source)`, carrying
  `n_scores`, `n_distinct_obs`, `n_airports_day`. `n_distinct_obs` is the one
  activity number no per-group rollup can reproduce (one observation → N
  scores), and it is what kept `get_activity_summary` on raw scores until now.
  It **is** additive across days — an observation has exactly one
  `observation_time` and so belongs to exactly one UTC date — so summing it
  over a window is exact. `n_airports_day` is deliberately *not* additive; a
  windowed distinct-airport count is computed at read time as
  `COUNT(DISTINCT icao)` over `verification_daily_stats`.
- **`taf_verification_daily`** — key `(date, source, days_out)` where
  `days_out = lead_hours // 24`. The bucketing expression has to truncate
  identically on SQLite and MySQL: `(x - x % 24) / 24`, because MySQL's
  `CAST(2.5 AS SIGNED)` rounds *away from zero*, so a naive
  `CAST(lead_hours/24 AS INTEGER)` would bucket 60 h as 2 on SQLite and 3 on
  MySQL. Gust columns join `verification_observations` via `observation_id`
  exactly as the NWP daily rollup does; "the TAF shows a gust" means the
  applicable trend carried a gust group (`taf_wind_gust_kt` non-NULL), not the
  ~10 kt forecast criterion used for NWP.

### `archive_manifest` — what has been safely archived

`UNIQUE(table_name, period)`; `period` is `YYYY-MM` for the monthly tables and
`YYYY-MM-DD` for snapshots. `file_path` is relative to the archive root so the
tree stays relocatable. This is the gate the pruner consults — see "Retention
and its gates" under Data Volume.

### `verification_cache` — pre-computed API responses
Serialized JSON for expensive dashboard/map queries, keyed by `cache_key` (`stats:{source}:{period}`, `bias_leaderboard:{model}:{days_out}:{period}`, `forecast_map:{day}:{hour}`; the `verif_map:*` family was removed in #154). `is_stale()` compares the cached `source_max_time` against live `MAX(observation_time)`/`MAX(fetched_at)` — live > cached means stale, and missing entries are always stale. `rebuild_all()` runs after each standalone cycle, after the daily-rollup refresh.

### `flight_verification_map` — thin flight↔airport linkage
Maps flights to corridor airports; `flight_id` FK with `ON DELETE CASCADE` (flight deletion removes the linkage, not the data). `observation_id` is nullable: the first cycle caches corridor resolution (`flight_id + icao + distance`) from the spatial query, and later cycles read ICAOs from here instead of re-querying. Airports that never produce a METAR get no observation-linked rows.

### `flights.verification_status` (added column)
Lifecycle `NULL → "collecting" → "complete" → "scored"`:
- `NULL` — not yet in the verification window (or no packs); checked for eligibility and promoted on first match.
- `"collecting"` — set on the first cycle that picks up the flight; subsequent cycles skip the spatial query and read cached ICAOs from the map.
- `"complete"` — set when `now > flight_end + 1h`; collection done, ready for scoring.
- `"scored"` — scoring done; no further collection or scoring.

The collection query `WHERE verification_status IS NULL OR verification_status NOT IN ('complete','scored')` picks up both NULL and collecting flights; scoring runs automatically after each collection cycle for flights in `"complete"` status.

## Standalone Verification Pipeline

### Intent

Flight-based verification is limited to airports along active flight routes. Standalone verification monitors ~830 pan-European watchlist airports, building a broader accuracy dataset independent of user activity.

### How It Differs from Flight-Based

| Aspect | Flight-Based | Standalone |
|--------|-------------|-----------|
| **Trigger** | Active flights in observation window | METAR ingest every 30 min; scoring at fixed hours (6, 9, 12, 15, 18 UTC); forecast fetch at 7/19 UTC |
| **Polling** | Every 10 minutes | Sleep until next fire time (no polling) |
| **Airports** | Flight corridor (15nm) | Watchlist (~830 pan-European airports) |
| **Forecast source** | Briefing pack forecasts.json | Independent Open-Meteo fetch + GRIB + sounding (ECMWF prefers direct GRIB) |
| **Models** | All models in briefing pack | GFS, ICON, ECMWF (3 models) |
| **Horizon** | D-0 through D-7 (per pack) | Per-model, from `forecast_grid.MAP_FORECAST_DAYS`: gfs 6d, ecmwf 6d, icon 4d (ICON-EU's ceiling GRIB stops at 120h) |
| **Score source tag** | `source='flight'` | `source='standalone'` |
| **Database** | `verification_observations` + scores | Same + `airport_forecast_snapshots` |

### Decoupled cycle types

`run_standalone_cycle(fetch_forecasts, score_observations)` now drives only forecast/scoring work — **METAR fetch was split out** into its own `run_metar_ingest_cycle` (every 30 min, `source='metar_ingest'`) so observations accumulate independently of model fetches. The flag combination maps to a `cycle_type` that names the `verification_cycles.source` tag:

| `(fetch, score)` | cycle_type | cycle source tag | Who runs it |
|---|---|---|---|
| `(True, False)` | `forecast` | `standalone_forecast` | `run_forecast_fetch_loop` at 07/19 UTC |
| `(False, True)` | `light` | `standalone_light` | `run_standalone_verification_loop` at 06/09/12/15/18 UTC |
| `(True, True)` | `full` | `standalone_full` | legacy combined; CLI/manual only, no longer scheduled |

Forecast cycles do Open-Meteo (+ECMWF GRIB) fetch, sounding enrichment, and GRIB cloud diagnostics, then store snapshots. Light cycles skip all fetching and score the latest stored snapshots against whatever observations the ingest loop has already persisted. **All scores land with `source='standalone'`** regardless of which cycle wrote them.

**Why**: separating METAR ingest (cheap, frequent) from forecast fetch (expensive, twice-daily, timed to fresh model runs) means scoring can run on a clean synoptic cadence without redundant API cost.

### Airport Watchlist

`tasks/airport_watchlist.py` discovers METAR-reporting airports. Default ICAO prefixes: LF, ED, EG, EH, EB, LS, LO (Western/Central Europe). Sources: euro_aip DB + aviationweather.gov METAR endpoint (batch queries with `@prefix`, batch size 400). Config file: `configs/airport_watchlist.json`.

### Standalone Cycle Flow

```
METAR ingest cycle (run_metar_ingest_cycle, every 30 min):
  - Fetch METAR/TAF for watchlist airports → UPSERT verification_observations
    (source='metar_ingest' on the cycle row; observations are source-agnostic)

Forecast cycle (fetch_forecasts=True, score=False):
  A. Fetch Open-Meteo forecasts per model (batches of 100, with pressure levels);
     ECMWF prefers direct GRIB delivery when its init is fresher than Open-Meteo
  B. Enrich with sounding analysis + GRIB cloud diagnostics, store snapshots
  E. Prune old snapshots (>10 days)

Light cycle (fetch_forecasts=False, score=True):
  D. Score latest stored snapshots vs observations already in DB
     (no aviationweather.gov call — reads obs the ingest loop persisted)
  E. Prune old snapshots (>10 days)
```

### Sounding Enrichment

Full cycles fetch pressure-level data alongside surface fields from Open-Meteo. Each snapshot is enriched via `analyze_sounding_lite()` (the lite path — it does NOT compute Richardson/stability indicators, which is why the inline EDR calibration accumulator collects zero data; see [EDR calibration inert]):

- `sounding_ceiling_ft` — thermodynamic ceiling from pressure levels
- `sounding_cloud_base_ft` — lowest BKN/OVC cloud layer base
- `sounding_convective_risk` — convective risk level (enum)
- `freezing_level_ft`, `sounding_cape_jkg`, `sounding_cin_jkg`, `sounding_lifted_index`

During scoring (Phase D), `_build_sounding_proxy()` reconstructs a minimal `SoundingAnalysis`-like object from stored fields — avoids re-running analysis. If snapshot has no sounding data (pre-enrichment rows), proxy returns `None` and scoring skips sounding-based comparisons.

### Resilience

- **Chunk-level retry**: Open-Meteo fetch retries up to 3 times per batch of 100 airports with exponential backoff (5s, 10s)
- **Graceful degradation**: Failed chunks are skipped with a warning — partial cycles succeed
- **Error recording**: `_record_failed_cycle()` captures last 500 chars of traceback in the `error` column
- **Sounding fault tolerance**: `_enrich_with_sounding()` fails silently — surface data preserved
- **GRIB fallback**: GRIB failures logged but don't block the cycle

### Open-Meteo Batching

Standalone fetches surface + pressure-level variables for ~830 airports per full cycle. Requests are chunked into batches of 100 airports per Open-Meteo call (`_OPEN_METEO_BATCH_SIZE`). Chunk-level retry: up to 3 attempts with exponential backoff.

**Parse-time hour filtering (#236)**: the fetch passes `hour_filter` (the superset of sample hours, `forecast_grid.all_sample_hours()`) to `OpenMeteoClient.fetch_multi_point`, so only sample-hour slots are materialised as `HourlyForecast`/`PressureLevelData` objects. Without it, ~80% of the 6-day hourly response (28 pressure levels for GFS) was parsed into Pydantic objects only to be discarded — and stayed alive across the chunk's sounding analysis in up to 4 concurrent threads, dominating the fetch phase's transient memory. The wire payload still contains every hour; the filter bounds parse memory, not bandwidth.

### Scheduler Integration

The standalone subsystem now runs as **three independent loops** (disable individually with the matching env flag):

- **METAR ingest** (`run_metar_ingest_loop`, `DISABLE_METAR_INGEST=1`) — fires every 30 min on the half hour (`_METAR_INGEST_INTERVAL_SECONDS = 1800`), 200 s startup delay (lands before the standalone loop's 240 s). Runs `run_metar_ingest_cycle`, fetching METAR/TAF for the watchlist into `verification_observations`.
- **Forecast fetch** (`run_forecast_fetch_loop`, `DISABLE_FORECAST_FETCH=1`) — fires at `FORECAST_FETCH_HOURS_UTC = [7, 19]`. Runs `run_standalone_cycle(fetch_forecasts=True, score_observations=False)`. Timed ~30 min after ECMWF 00Z/12Z deliveries land so each fetch picks up the freshest GFS/ICON/ECMWF inits.
- **Verification** (`run_standalone_verification_loop`, `DISABLE_STANDALONE_VERIFICATION=1`) — fires at `VERIFICATION_HOURS_UTC = [6, 9, 12, 15, 18]`. Runs `run_standalone_cycle(fetch_forecasts=False, score_observations=True)`, scoring stored snapshots against whatever observations the ingest loop has persisted (most recent fetch wins).

The hour-keyed loops compute the next fire hour and sleep until then (no polling), retry after 15 min on failure, use a 240 s startup delay, and trigger `cache_builder.rebuild_all` (after `rollup_today_and_pending`) on each successful verification cycle. The legacy combined cycle (`fetch_forecasts=True, score_observations=True`, cycle source `standalone_full`) is still callable from CLI/tests but is no longer scheduled.

### Subprocess isolation (issue #236)

Forecast and verification cycles run in a **short-lived child process**, not in the uvicorn process: `scheduler._run_standalone_cycle_supervised` spawns `python -m weatherbrief.verify standalone --forecast-only|--light --with-rollup --background` and waits on it. Why: the forecast cycle's transient working set (concurrent Open-Meteo chunk parsing + ~46K sounding analyses) ratcheted the uvicorn heap high-water mark; CPython/glibc never return that peak to the OS, so it became ~3 GB of permanent anon that the host swapped. The child returns the entire peak on exit.

Mechanics worth knowing:

- **Same code path as manual debugging** — the child *is* the CLI, so worktree reproduction is literally the production command.
- `--with-rollup` runs `run_post_cycle_tasks` (daily-stats rollup + cache rebuild, shared with the in-process fallback) inside the child, keeping that memory out of the parent too.
- `--background` renices the child and raises its `oom_score_adj`, so a cgroup OOM mid-cycle kills the disposable child, not the web process.
- The child gets `GRIB_DECODE_WORKERS` = `STANDALONE_ANALYSIS_WORKERS` (default 2; `0` restores the old inline behaviour) — a deliberate, bounded second pool in the cgroup (#448 PR B): the cycle's dominant cost is ~56K GIL-bound sounding analyses, now shipped to pool workers in ~100-profile batches (`analyze_sounding_batch` via `analysis/sounding/snapshot_fields.py`; the interactive alternates path shares the fetchers but stays inline). The child and its workers are reniced, so interactive work preempts them; a worker OOM kills only the disposable child's pool.
- Supervisor enforces a hard timeout (`STANDALONE_SUBPROCESS_TIMEOUT_S`, default 3 h) and records a failed `verification_cycles` row for children that die without writing one (SIGKILL/timeout never reach the in-cycle exception path); rows the child already wrote are not duplicated.
- **Rollback switch**: `STANDALONE_SUBPROCESS=0` reverts to the old in-process `asyncio.to_thread` path.
- `peak_rss_mb` on cycle rows now measures the child process (clean per-cycle attribution); `peak_cgroup_mb` semantics are unchanged. The 30-min METAR ingest stays in-process — too cheap to justify 48 spawns/day.

## Verification Stats & Digest

### Daily Digest Email

`notify/verification_email.py` sends a daily HTML + plaintext email at 08:00 UTC (configurable via `DIGEST_HOUR_UTC` env var; default chosen to land after the 07Z full-cycle results). Admin-only while testing. Accuracy color thresholds: green ≥80%, amber 60-80%, red <60%.

### Digest Data Model

`VerificationDigestData` contains: `period_label`, `activity` (ActivitySummary), `category_accuracy_today` / `category_accuracy_7d` (CategoryAccuracyRow lists), `notable_misses` (with directional severity — optimistic vs pessimistic), `category_bias` (CategoryBiasStats per model), `wind_advisory` (WindAdvisoryStats), `gust_accuracy` (GustAccuracyStats per model/days_out — the email renders D-0 only, the dashboard every lead time), `missed_warnings` (MissedWarning list).

### Admin Dashboard

`GET /api/admin/verification` (in `api/admin.py`) serves JSON for the web dashboard (`web/verification.html`). Source-filtered: flight and standalone stats are queried independently, never mixed. Dashboard shows category accuracy by model/days_out, notable misses, MAE trends, and the gust tile (both conditionings side by side — `gust_accuracy` is absent from cache entries written before #491, so the client treats it as empty until the next rebuild).

**Cache layer**: Unfiltered requests (no country/airport filter) use `verification_cache` for fast responses. If the cache is stale or missing, falls back to live query. Filtered requests always run live (small result sets). Cache is rebuilt after each standalone verification cycle via `rebuild_all()` in `cache_builder.py`.

### Daily Rollup (issue #154)

`tasks/verification_daily_rollup.py` aggregates raw `verification_scores` into `verification_daily_stats` after each standalone verification cycle. Grouped by (date, source, model, days_out, icao) — ~12K rows/day, ~4.5M/year. Pure SQL `INSERT … SELECT … GROUP BY` with `case()` expressions encoding the category/advisory direction logic; idempotent DELETE+INSERT.

`rollup_today_and_pending()` is what the scheduler calls: it rolls up every completed UTC day not yet summarised, then re-rolls today (partial). Today's rollup is re-done each cycle until the day completes, keeping the dashboard's "last 24h" window live without falling back to raw scans.

**Why this replaces the old query-time aggregation**: the dashboard rebuild that used to take ~32 min (most of it `rebuild_verification_map_cache` aggregating 2.7M raw rows 16 times) now reads ~4.5M small pre-aggregated rows and completes in seconds. Raw `verification_scores` becomes append-only storage for `get_notable_misses` / `get_missed_warnings` (which still need individual rows) and for any future re-derivation.

TAF rollup is out of scope: `taf_verification_scores` has no `model`/`days_out` columns (lead_hours instead), so it doesn't fit the daily key shape. The TAF pseudo-model continues to be aggregated at query time from raw — small volume, no perf concern.

### Monthly Rollup

`tasks/verification_rollup.py` aggregates raw `verification_scores` and `taf_verification_scores` into `verification_monthly_stats` once a month completes. Groups by (source, model, days_out, icao) and computes:
- Category direction counts (match/optimistic_1-2/pessimistic_1-2) — classifies whether forecast was too good (dangerous) or too conservative
- Advisory direction counts (match/optimistic/pessimistic)
- Continuous metrics: ceiling MAE + bias, visibility MAE, wind speed MAE, temperature MAE
- Hit/miss/false-alarm for precipitation and convection (model scores only)

Rollup is idempotent (delete + re-insert per month). Currently triggered manually; designed for offline/retention pipeline integration. The shape mirrors `verification_daily_stats` so a future monthly-roll-of-rollups can be a GROUP BY over the daily table.

### Query Functions

All in `tasks/verification_stats.py`. Every query accepts a `source` parameter ('flight' or 'standalone') to ensure isolation.

Which table a query reads depends on the request shape and the #522 gate:

| Request | NWP table | TAF table |
|---|---|---|
| Unfiltered, `VERIFICATION_GLOBAL_ROLLUP_READS=1` | `verification_global_daily_stats` | `taf_verification_daily` |
| Country/airport-filtered (any gate) | `verification_daily_stats` | raw `taf_verification_scores` |
| Gate off (pre-#522 behaviour) | `verification_daily_stats` | raw `taf_verification_scores` |

The two NWP tables declare identical column names, so the query bodies differ
only in which class `_nwp_daily_table()` hands them. Filtered requests keep the
per-airport table because the global one has no `icao` to filter on — and those
are the requests the dashboard never caches anyway.

NWP queries (post-#154) read from the daily rollup:

- `get_category_accuracy()` — category match rate per model/days_out; TAF added at query time from raw
- `get_category_bias_stats()` — match + 4 directional buckets (`_2` = "2 or more levels off")
- `get_wind_advisory_accuracy()` — advisory match rate; TAF added at query time
- `get_gust_accuracy()` — per model/days_out, both gust conditionings plus the over-warn ratio and hit count (#491). NWP from the rollup's additive sums; TAF joined to the observation at query time
- `get_optimistic_bias_leaderboard()` — `(n_cat_opt_1 + 2 * n_cat_opt_2) / n` per airport, descending. Drives the leaderboard view that replaced the bias map.

Still on raw `verification_scores` (need individual rows):

- `get_notable_misses()` — category busts with severity and direction
- `get_missed_warnings()` — WARNINGs that models failed to predict

Both are bounded by `limit` and scoped to D-0/D-1 over windows of ≤30 days,
comfortably inside the 180-day raw retention #522 introduces.

`get_activity_summary()` reads `verification_activity_daily` +
`COUNT(DISTINCT icao)` over `verification_daily_stats` when the gate is on and
the request is unfiltered; otherwise it falls back to the raw
`COUNT(DISTINCT observation_id)` path (which is where the `FORCE INDEX` hint
still lives — see the Phase 1 final step in the rollout plan).

### Removed in #154

- **`get_verification_map_data` / `rebuild_verification_map_cache`** — the per-airport accuracy map view was dropped (decided not useful enough to justify ~30 min cache rebuild cost).
- **`/api/maps/verification`** endpoint and `panel-verification` tab on `web/maps.html`.
- **`verif_map:*` cache keys** (16 entries per cycle).
- **`CategoryBiasStats.optimistic_3` / `pessimistic_3`** — collapsed into `_2` to match the daily/monthly rollup shape (the 3-step VFR↔LIFR case is rare and the leaderboard formula already weights `_2` for severity).

## Collection Loop (Flight-Based)

### Scheduler Integration

Loop in `scheduler.py`, alongside other async loops:

```python
async def run_verification_loop(app_state) -> None:
    """Collect METAR/TAF observations for active flights."""
    logger.info("Verification loop started (poll every %ds)", _VERIF_POLL_SECONDS)
    await asyncio.sleep(_VERIF_STARTUP_DELAY_SECONDS)

    while True:
        try:
            await asyncio.to_thread(_run_verification_once, app_state)  # → collect_and_store
        except Exception:
            logger.error("Verification cycle failed", exc_info=True)
        await asyncio.sleep(_VERIF_POLL_SECONDS)
```

**Poll interval**: 10 minutes. METARs update every 30-60 minutes, SPECIs can be more frequent. 10 minutes gives good coverage without hammering aviationweather.gov.

### Finding Active Flights

```python
def find_verifiable_flights(db: Session) -> list[FlightRow]:  # tasks/verification.py
    """Flights in the observation window: departure-1h to departure+duration+1h."""
    now = datetime.now(timezone.utc)
    
    rows = db.execute(
        select(FlightRow)
        .join(BriefingPackRow)  # must have at least one briefing
        .where(FlightRow.verification_status != "complete")
        .where(FlightRow.departure_time <= now + timedelta(hours=1))  # started or about to
        .distinct()
    ).scalars().all()

    active = []
    for row in rows:
        flight_end = row.departure_time + timedelta(hours=row.flight_duration_hours)
        window_end = flight_end + timedelta(hours=1)
        if now <= window_end:
            active.append(row)
    return active
```

### Collection Flow

The key optimization: **gather all airports across all active flights first, deduplicate, make one batch fetch, then fan out results to flights.**

```
For each verification cycle:

Phase A — Gather & Deduplicate
  1. Find active flights (departure-1h ≤ now ≤ flight_end+1h, has ≥1 pack)
  2. For each flight, resolve corridor airports (15nm) from route waypoints
     - First cycle (verification_status=NULL → set to "collecting"):
       Spatial query via RouteWeatherService → cache (flight_id, icao, distance)
       in flight_verification_map (observation_id=NULL)
     - Subsequent cycles (verification_status="collecting"):
       Read ICAOs from existing map rows (no spatial query)
  3. Build a global dict:  icao → set[flight_id]
     Example: 5 flights through LFPG, 3 through EDDF, 2 through LSZH
     → unique ICAOs = {LFPG, EDDF, LSZH, ...}  (not 10 duplicate fetches)

Phase B — Batch Fetch (chunked if needed)
  4. Batch-fetch METAR/TAF for all unique ICAOs
     aviationweather.gov supports up to 400 ICAOs per request —
     if more, chunk into batches of 400 with a small delay (~1s) between calls
  5. Minimal network calls: ceil(N/400) per cycle, typically just 1

Phase C — Store & Link
  6. For each fetched result:
     a. Skip airports with no METAR data — don't store empty rows
     b. One-per-hour filter: if we already have an observation for this
        (icao, clock_hour), skip unless this one is closer to the top of hour
     c. UPSERT into verification_observations (dedup on icao + observation_time)
        → if METAR already stored from a previous cycle, skip
     d. Parse TAF: find applicable trend at observation_time, extract fields
     e. For each flight_id in icao_to_flights[icao]:
        → INSERT flight_verification_map row with observation_id
        (map rows without observation_id were created in Phase A for caching)

Phase D — Score (can be deferred to Phase 2 or batched separately)
  7. For each NEW observation × each linked flight's briefing packs:
     - Pick ONE pack per days_out: the latest pack for each calendar day
       (if user refreshed D-1 twice, only score against the last refresh)
     - Skip packs whose forecasts.json is missing (log warning)
     For each selected pack:
     a. Load forecasts.json for the pack
     b. Find route point closest to airport
     c. Find HourlyForecast closest to observation_time
     d. Compute deltas → INSERT into verification_scores
     e. Compute TAF vs METAR deltas → INSERT into taf_verification_scores

Phase E — Finalize
  8. For flights where now > flight_end + 1h:
     Mark flight verification_status = "complete"
```

**Why this matters**: On a busy day with 20 concurrent flights across similar European routes, many share LFPG, EDDF, EHAM, etc. Without dedup, we'd fetch the same METAR 10+ times per cycle. With dedup, it's one batch call for ~50-100 unique ICAOs regardless of flight count.

### Corridor Width

**15nm** default (tighter than the 30nm briefing corridor). This balances:
- Enough airports for meaningful coverage
- Not so many that we store noise from distant fields

Set by `_DEFAULT_CORRIDOR_NM = 15.0` in `tasks/verification.py`, threaded through `gather_airports`/`collect_and_store` as a `corridor_nm` parameter (NOT an env var — change the constant or pass the arg).

## Scoring Logic

### Core Principle: Verify What Users See

Verification must score **the same derivations used in advisories** — not independent calculations. If advisories use `reconcile_ceiling()` for ceiling, verification uses `reconcile_ceiling()`. If advisories use `compute_wind_advisory()` with preloaded runway data, verification uses the same function.

This means: if we improve how we derive ceiling or classify flight categories in the advisory pipeline, the verification stats for new observations will naturally reflect that improvement. The verification database becomes a measure of **advisory quality**, not just raw model quality. Over time, we can track whether advisory logic changes actually improved accuracy.

### Model → METAR Comparison

Every derived value must use **the same function the advisory pipeline uses**. The table below maps each scored field to its advisory-pipeline source:

| Scored field | Advisory function | Location | Inputs |
|-------------|-------------------|----------|--------|
| Model ceiling | `reconcile_ceiling(sounding, hourly)` | `analysis/airport_conditions.py` | `SoundingAnalysis` + `HourlyForecast` → min of sounding & NWP ceiling |
| Model visibility | `hourly.visibility_m / 1609.34` (→ statute miles) | `analysis/airport_conditions.py` | Direct from model, converted to SM like advisories do |
| Model flight category | `classify_flight_category(ceiling_ft, visibility_sm)` | `analysis/airport_conditions.py` | Standard VFR/MVFR/IFR/LIFR thresholds |
| Model wind advisory | `compute_wind_advisory(dir, speed, gust, runway_ends)` | `tasks/route_weather.py` | Same thresholds: xw ≥15kt amber, ≥25kt red; gust ≥25/35kt |
| Model gust | `hourly.wind_gusts_10m_kt` (stored raw + delta + flag) | `tasks/verification_gust.py` | `forecast_shows_gust()` applies the METAR ~10 kt criterion to the forecast |
| Model precipitation | `hourly.precipitation_mm > 0 or hourly.snowfall_cm > 0` | `analysis/sounding/precipitation.py` (`assess_precipitation`) | Same check as `assess_precipitation()` |
| Model convection | `sounding.convective.risk_level` from `assess_convective_thermo()` | `analysis/sounding/convective.py` | CAPE thresholds: 50/300/1000/2000 J/kg, CIN suppression |

```python
def compute_verification_score(
    obs: VerificationObservation,
    sounding: SoundingAnalysis | None,
    hourly: HourlyForecast,
    runway_ends: list[RunwayEnd],
    model: str,
    model_init_time: datetime,
    days_out: int,
) -> VerificationScore:
    """Compare one model forecast against one METAR observation.
    
    Uses the SAME derivation functions as the advisory pipeline.
    """
    lead_hours = int((obs.observation_time - model_init_time).total_seconds() / 3600)
    
    # Ceiling — same as advisory pipeline: reconcile sounding + NWP ceiling
    model_ceiling = reconcile_ceiling(sounding, hourly)
    
    # Visibility — same conversion as advisory pipeline: meters → statute miles
    model_visibility_sm = (
        round(hourly.visibility_m / 1609.34, 1)
        if hourly.visibility_m is not None else None
    )
    obs_visibility_sm = (
        round(obs.visibility_m / 1609.34, 1)
        if obs.visibility_m is not None else None
    )
    
    # Flight category — same function as advisory pipeline
    model_cat = classify_flight_category(model_ceiling, model_visibility_sm)
    
    # Wind advisory — same function + thresholds as advisory pipeline
    model_adv, model_rwy, model_xw, model_hw = compute_wind_advisory(
        hourly.wind_direction_10m_deg, hourly.wind_speed_10m_kt,
        hourly.wind_gusts_10m_kt, runway_ends,
    )
    obs_adv, obs_rwy, obs_xw, obs_hw = compute_wind_advisory(
        obs.wind_dir, obs.wind_speed_kt, obs.wind_gust_kt, runway_ends,
    )
    
    # Precipitation — same check as assess_precipitation()
    model_has_precip = (
        (hourly.precipitation_mm or 0) > 0 or (hourly.snowfall_cm or 0) > 0
    )
    obs_has_precip = any(w in _PRECIP_PHENOMENA for w in obs.weather)
    
    # Convection — same CAPE-based risk from sounding analysis
    model_has_convection = (
        sounding is not None
        and sounding.convective is not None
        and sounding.convective.risk_level >= ConvectiveRisk.LOW  # same threshold as advisory
    )
    obs_has_convection = "TS" in obs.weather
    
    return VerificationScore(
        icao=obs.icao,
        observation_time=obs.observation_time,
        model=model,
        model_init_time=model_init_time,
        lead_hours=lead_hours,
        days_out=days_out,
        obs_flight_category=obs.flight_category,
        model_flight_category=str(model_cat),
        category_match=(obs.flight_category == str(model_cat)),
        ceiling_delta_ft=_delta(model_ceiling, obs.ceiling_ft),
        visibility_delta_m=_delta(hourly.visibility_m, obs.visibility_m),
        wind_speed_delta_kt=_delta(hourly.wind_speed_10m_kt, obs.wind_speed_kt),
        wind_dir_delta_deg=_circular_delta(hourly.wind_direction_10m_deg, obs.wind_dir),
        temperature_delta_c=_delta(hourly.temperature_2m_c, obs.temperature_c),
        obs_wind_advisory=obs_adv,
        model_wind_advisory=model_adv,
        advisory_match=(obs_adv == model_adv),
        obs_has_precipitation=obs_has_precip,
        model_has_precipitation=model_has_precip,
        obs_has_convection=obs_has_convection,
        model_has_convection=model_has_convection,
    )
```

**Ceiling datum.** Every ceiling that meets a METAR ceiling in a delta is first
converted to **AGL**, because the METAR side always is. `model_ceiling` gets
this from `reconcile_ceiling(..., field_elevation_ft=…, model=…)`; the extra
`cloud_base_delta_ft` on the standalone path converts `cloud_base_ft` with the
same `to_agl_ceiling` + `_nwp_ceiling_is_agl` helpers before differencing.
`lcl_delta_ft` is the exception that looks like a bug: `lcl_ft` is the Espy
surface approximation, a height above the *station*, so it is already AGL and
must not have field elevation subtracted. Field elevations come from
`get_airport_elevations`; when the lookup fails, scoring degrades to the legacy
datum-naive deltas rather than failing the cycle. (#441 finding #3)

### TAF → METAR Comparison

TAF fields are already parsed and stored in `verification_observations`. The TAF-derived flight category uses the same `classify_flight_category()` as advisories.

```python
def compute_taf_verification(
    obs: VerificationObservation,
    runway_ends: list[RunwayEnd],
) -> TafVerificationScore | None:
    """Compare TAF prediction against actual METAR for same airport/time."""
    if not obs.taf_flight_category:
        return None
    
    lead_hours = int((obs.observation_time - obs.taf_issue_time).total_seconds() / 3600)
    
    # Wind advisory for TAF — same function as advisory pipeline
    taf_adv, _, _, _ = compute_wind_advisory(
        obs.taf_wind_dir, obs.taf_wind_speed_kt, obs.taf_wind_gust_kt, runway_ends,
    )
    obs_adv, _, _, _ = compute_wind_advisory(
        obs.wind_dir, obs.wind_speed_kt, obs.wind_gust_kt, runway_ends,
    )
    
    return TafVerificationScore(
        icao=obs.icao,
        observation_time=obs.observation_time,
        taf_issue_time=obs.taf_issue_time,
        lead_hours=lead_hours,
        obs_flight_category=obs.flight_category,
        taf_flight_category=obs.taf_flight_category,
        category_match=(obs.flight_category == obs.taf_flight_category),
        ceiling_delta_ft=_delta(obs.taf_ceiling_ft, obs.ceiling_ft),
        visibility_delta_m=_delta(obs.taf_visibility_m, obs.visibility_m),
        wind_speed_delta_kt=_delta(obs.taf_wind_speed_kt, obs.wind_speed_kt),
        wind_dir_delta_deg=_circular_delta(obs.taf_wind_dir, obs.wind_dir),
    )
```

### Gust: Two Conditionings, Never Blended (#491)

Gust accuracy is the one metric where a single averaged number is actively misleading, so `tasks/verification_gust.py` owns the definitions and everything downstream (scoring, both rollups, `get_gust_accuracy`, the dashboard tile) derives from them:

- **Sign** — `delta = forecast − observed`, like every other delta.
- **Realised peak** — `obs_gust` if the METAR reports a gust group, else `obs_wind`. No gust group means the peak *is* the mean, not that the peak is unknown.
- **"The forecast shows a gust"** — `forecast_gust − forecast_wind ≥ 10 kt`, the same criterion that puts a gust group on a METAR. Persisted per model score as `model_gust_flag`; the TAF equivalent is "the applicable trend carries a gust group".
- **Report both conditionings side by side:**
  - *forecast-flagged* hours — on the hours a model calls a gust, it sits **~+7 kt above** the realised peak, and ~80% of those hours the airport wasn't gusting at all. (This is the "why does the gust layer sit above the TAFs?" view.)
  - *obs-flagged* hours — on the hours a METAR does report a gust, the model gust lands **4–13 kt below** the true peak. (The extreme-day view.)
  - TAF over-flags gust *frequency* ~4× but gets the *size* right (±1 kt).

The two select different, mostly non-overlapping samples. Collapsing them into one average is what made the first ad-hoc pass (EGLL/LFPG/EDDF, Apr–Jul 2026) look self-contradictory.

### Observation Frequency: One Per Airport Per Hour

METARs are issued every 30-60 minutes, and SPECIs can be more frequent. To avoid inflating the dataset with near-duplicate observations, **keep at most one observation per airport per clock hour**. If multiple METARs exist for the same (icao, hour), keep the one closest to the top of the hour (e.g., for 14:00-14:59, prefer the METAR at 14:20 over 14:50). The UNIQUE(icao, observation_time) constraint handles exact dedup; the hourly filtering is applied during collection before insertion.

### Scoring Window Limitations

`route_analyses.json` stores soundings for the **flight window hours** only (departure through arrival). Observations collected in the 1h buffer before departure or after arrival may not have a matching sounding hour. In this case:
- **Ceiling/convection**: Fall back to the nearest available sounding hour (first or last hour of the flight window). The error from this approximation is small — ceiling patterns don't change dramatically in 1 hour.
- **Surface fields** (visibility, wind, temperature): `forecasts.json` typically has a wider hourly range and should cover the buffer. If not, skip scoring for that observation.

### Accessing Model Data for Scoring

For each observation, we need the `SoundingAnalysis` and `HourlyForecast` at the nearest route point and nearest hour. These come from the briefing pack's stored artifacts:

```python
# 0. Map airport to nearest route point — same logic as run_observation_comparison()
#    Uses cumulative great-circle distance along route to find nearest waypoint,
#    then nearest RoutePointAnalysis (20nm spacing) around that waypoint.
#    Corridor is 15nm, so max off-route distance is small.
nearest_point_index = find_nearest_route_point(airport_lat, airport_lon, route_points)

# 1. Ceiling + convection: from route_analyses.json
rpa = route_analyses[nearest_point_index]  # RoutePointAnalysis
sounding = rpa.sounding.get(model_name)    # SoundingAnalysis
#    → reconcile_ceiling(sounding, hourly) for ceiling
#    → sounding.convective.risk_level for convection

# 2. Surface fields: from forecasts.json
wp_forecast = forecasts[nearest_waypoint][model]  # WaypointForecast
hourly = wp_forecast.at_time(obs.observation_time) # nearest hour
#    → hourly.visibility_m, wind_speed_10m_kt, temperature_2m_c, etc.

# 3. Runways: preloaded once per cycle
runway_data = get_runway_ends(unique_icaos, airports_db_path)
rwy_ends = runway_data.get(obs.icao, [])
```

## CLI Interface

```bash
# Manual collection (all active flights, or one via --flight-id; --corridor NM)
python -m weatherbrief.verify collect --flight-id "LFPG-EDDF-2026-04-01-abc123"

# Score completed flights (no flag scores all; --flight-id for one)
python -m weatherbrief.verify score

# Backfill: re-run scoring after code changes (--flight-id optional)
python -m weatherbrief.verify backfill --flight-id "LFPG-EDDF-2026-04-01-abc123"

# Backfill the gust columns from migration 083 (#491). TAF reaches all history;
# model gust only the un-pruned snapshot window (--days, default 10). Model
# rows whose scored snapshot can't be pinned down (several forecast hours in
# the ±90 min window, no stored wind delta to separate them) stay NULL by
# design — see _pick_scored_snapshot.
python -m weatherbrief.verify backfill-gust
python -m weatherbrief.verify backfill-gust --taf-only

# Export accuracy data (--table observations | scores | taf-scores; the score
# tables are exported joined to their observation, so forecast gust and
# observed gust land in the same record)
python -m weatherbrief.verify export --format csv --output accuracy.csv
python -m weatherbrief.verify export --table scores --source standalone \
    --model ecmwf --format csv --output scores.csv

# Summary statistics (--source flight|standalone, --model, --icao)
python -m weatherbrief.verify stats --source standalone

# Standalone cycle (subprocess entry point): --light | --forecast-only,
# plus --with-rollup / --background as the scheduler invokes it
python -m weatherbrief.verify standalone --light --with-rollup --background

# Discover watchlist airports; rebuild dashboard cache; monthly rollup
python -m weatherbrief.verify discover --prefixes LF,ED,EG
python -m weatherbrief.verify rebuild-cache
python -m weatherbrief.verify rollup-summary

# Daily rollups (all four tables). --rebuild re-rolls every existing day —
# this is the #522 Phase 1 backfill.
python -m weatherbrief.verify rollup-daily-stats [--day YYYY-MM-DD] [--rebuild]

# Monthly stats, rolled from the daily table (#522 Phase 4)
python -m weatherbrief.verify rollup-monthly-stats [--month YYYY-MM] [--rebuild]

# Parquet archive (#522 Phase 2). `run` archives newly-final periods,
# `backfill` walks all completed history filling gaps, `verify` rechecks
# sha256 + live row counts.
python -m weatherbrief.verify archive backfill --dry-run
python -m weatherbrief.verify archive run|backfill|list|verify [--table scores]

# Raw prune (#522 Phase 3). Without --apply this only reports which months
# pass both gates and why the others don't — the safe way to validate a
# retention setting before a single row is deleted.
python -m weatherbrief.verify prune-raw --retain-days 180 [--apply]
```

## Data Volume and Storage Tiers (#522)

The original "negligible at any realistic scale" estimate was written for the
flight-based track alone and was off by ~250×. Standalone monitoring of ~830
airports across three models changed the arithmetic completely. Measured in
production:

| Table | Rows | Size | Growth |
|---|---|---|---|
| `verification_scores` | 8.8M | ~3 GB (2 GB of it indexes) | ~1.5M rows/month |
| `verification_observations` | 2.3M | ~890 MB | ~800K rows/month |
| `verification_daily_stats` | 1.1M | — | ~12K rows/day |
| `taf_verification_scores` | 330K | — | — |

Against a shared MySQL server with a 1 GB InnoDB buffer pool. Meanwhile every
dashboard/digest read window is ≤90 days, so old raw rows serve no live query:
they are cold data stored in the most expensive format available.

### Three tiers

| Tier | Store | Retention | Serves |
|---|---|---|---|
| **Raw operational** | MySQL (`verification_observations`, `verification_scores`, `taf_verification_scores`, `airport_forecast_snapshots`) | 180 days (snapshots 10, unchanged) | scoring, rollup jobs, notable-misses/missed-warnings, ad-hoc debugging |
| **Aggregates** | MySQL rollup tables (per-airport daily, global daily, TAF daily, activity, monthly) | forever | all dashboard/digest/leaderboard reads |
| **Row-level archive** | Parquet in `DATA_DIR/archive/verification/` | forever | re-scoring, calibration, data science (DuckDB), re-import |

"Keep data forever" survives — it just moves down a tier. The accuracy dataset
still grows without bound; it grows in Parquet rather than in InnoDB.

### Archive layout

```
DATA_DIR/archive/verification/
  observations/YYYY-MM.parquet
  scores/YYYY-MM.parquet
  taf_scores/YYYY-MM.parquet
  snapshots/YYYY-MM-DD.parquet
```

Monthly for the score/observation tables, daily for snapshots — partitioned by
**`fetched_at`** UTC date, not `forecast_hour`, so archive partitions line up
exactly with the existing 10-day prune predicate (DuckDB filters on
`forecast_hour` inside the files fine). zstd compression. Columns are 1:1 with
the ORM tables **including `id`**, so `scores.observation_id → observations.id`
joins work in DuckDB. Datetimes are written as UTC-aware microsecond
timestamps — MySQL hands back naive values, and UTC is stamped explicitly
rather than left as a convention a future reader has to rediscover.

**Why snapshots are archived at all:** they are the only record of what the
forecast actually *said* — scores keep deltas. The gust work (#491) had to
denormalise `model_wind_gust_kt` onto the score row precisely because
snapshots are pruned at 10 days; the archive generalises that lesson and makes
re-scoring history with improved scoring logic possible.

**Source of truth is the database, not the inbox artifacts:** the
droplet-fallback compute path writes snapshots straight to MySQL and never
emits an artifact, so the SQLite inbox has holes on node-failure days.

### Archive finality and the manifest

`archive_manifest(table_name, period, row_count, max_id, file_path, sha256,
created_at)` with `UNIQUE(table_name, period)` records every written file.
Each write is: keyset-paginated SELECT → Parquet temp file → recount live rows
**after** the write and compare → fsync (file and directory) → atomic rename →
upsert the manifest. The recount comes after the write on purpose: a row
landing mid-write is either picked up by the keyset (ids ascend) or caught by
the mismatch, whereas a count taken beforehand would match in both cases and
record a manifest for a file missing a row. A failed verification discards the
temp file and writes no manifest row, so a broken archive can never authorise
a delete.

`max_id` — the highest source primary key in the file — is what makes the
Phase 3 delete gate exact. Keys are monotonic, so any row inserted after the
archive was written has a higher `id` whatever its timestamp (a backfilled old
METAR included), and the gate asks "is any live row newer than the archive?"
rather than "do the counts still match". Counts cannot answer that once
pruning starts: pruning deliberately leaves `source='flight'` rows behind, so
a pruned month's live count sits permanently below its archived count, and a
count-equality gate would report every pruned month as broken forever.

A period is archived only once it can no longer change: monthly tables at ≥10
days into the following month (late scores can't arrive past the 10-day
snapshot window, and the current month is never archived); snapshots at D+2
(rows are immutable after insert, and the compute-node artifact ingest
restamps `fetched_at` at ingest time).

### Retention and its gates

`tasks/retention.prune_raw_observations` deletes whole months only, and only
months that pass **both** gates: the `airport_monthly_summary` climatology gate
*and*, for all three monthly tables, an `archive_manifest` row whose Parquet
file still exists, still hashes to the recorded sha256, and whose `max_id`
covers every live row in the month. A manifest row alone is not enough — it is
a database fact about a file on a filesystem that can lose or corrupt it, and
the gate authorises deleting the only other copy. No manifest, missing file,
bad checksum, or newer live rows → no delete, logged loudly.

Months with nothing left to delete are skipped *before* the gate runs, which
is what keeps "this month is done" distinguishable from "the archive is
broken" — and keeps the file re-hash off the daily path for every month
already pruned.

Deletes run in 5,000-row batches with a commit per batch, walking a day at a
time — never one multi-month statement on the shared server.

Exemptions, none optional:

- **`source='flight'` scores are never pruned** — tiny volume, pilot/debrief
  context, ERA5 re-analysis value. Mirrors the debriefed-flights T2 exemption.
- **Observations referenced by `flight_verification_map` survive**, as do
  observations that still carry a flight score. Observations are shared between
  tracks and have no source column of their own, so the mapping is what
  identifies flight-linked ground truth; deleting one would cascade into the
  exempt flight scores hanging off it.

The snapshot prune (`_prune_old_snapshots`, 10 days) applies the same gate per
`fetched_at` day — but only once archiving is actually enabled,
because with the archive off there would never be a manifest and a bounded
table would become unbounded.

Inbox rotation removes `eu-*/us-*.sqlite` from `SNAPSHOT_INBOX_DIR` older than
`SNAPSHOT_INBOX_RETENTION_DAYS`. Nothing re-reads an old artifact; once a day
is in both MySQL and Parquet the transport file is redundant, and this caps an
otherwise unbounded disk leak.

### Phase gates

All of the above ships dark. `tasks/verification_tiering.py` owns every gate;
`designs/plans/verification-data-tiering.md` is the rollout runbook (what to
check before each phase, what to run, how to validate, how to roll back).

| Env var | Default | Meaning |
|---|---|---|
| `VERIFICATION_GLOBAL_ROLLUP_READS` | `0` | Unfiltered aggregates read the global rollups |
| `VERIFICATION_ARCHIVE_ENABLED` | `0` | Retention loop runs the Parquet archive writer |
| `VERIFICATION_RAW_RETENTION_DAYS` | `9999` (disabled) | Online window for raw obs/scores/TAF scores; target `180` |
| `VERIFICATION_PRUNE_REQUIRE_ARCHIVE` | `1` | No verified manifest → no delete |
| `SNAPSHOT_INBOX_RETENTION_DAYS` | `0` (keep) | Inbox artifact rotation; target `30` |
| `VERIFICATION_MONTHLY_ROLLUP_ENABLED` | `0` | Retention loop rolls completed months |
| `VERIFICATION_DAILY_STATS_RETENTION_MONTHS` | `0` (keep) | Prune daily stats older than N months |

CLI: `python -m weatherbrief.verify archive run|backfill|list|verify`,
`prune-raw [--retain-days N] [--apply]`, `rollup-monthly-stats`. DuckDB query
recipes live in the `tasks/archive.py` module docstring.

## Accuracy Metrics & Queries

Dashboard and digest breakdowns — per-model accuracy at lead time, TAF-vs-model, regional (ICAO prefix), seasonal, and IFR-only — are implemented in the query functions noted under "Query Functions" above (and `db/models.py`). Prefer querying the additive rollup tables (`verification_daily_stats` / `verification_monthly_stats`) over raw `verification_scores` for aggregate metrics; the rollups exist precisely so the dashboard avoids full scans.

## Key Design Choices

| Decision | Rationale |
|----------|-----------|
| Observations independent of flights | Dedup across flights, survives account deletion, anonymized community data |
| Thin mapping table with CASCADE | Flight deletion removes linkage, not data |
| UNIQUE(icao, observation_time) | Natural dedup key — same METAR never stored twice |
| TAF verification as separate table | Different key structure (taf_issue_time), different semantics |
| TAF scored per METAR | Same TAF scored against multiple METARs — shows accuracy across its validity period |
| 15nm corridor (not 30nm) | Tighter = fewer noisy small airfields, still catches alternates |
| 10-min poll interval | METARs update ~30-60min, SPECIs more frequent; good coverage vs API load |
| Pre-computed sounding ceiling | Reuse `sounding_ceiling_ft` from `route_analyses.json` — no recomputation, nearest-hour is close enough |
| Wind advisory with preloaded runways | `get_runway_ends()` batch-loads from cached euro_aip model, no extra DB queries |
| One pack per days_out | Score against the latest briefing pack per calendar day — avoids near-duplicate scores from same-day refreshes |
| Score all packs including stale ones | D-7/D-3 packs scored even without D-0 — builds accuracy stats per lead time independently |
| Score after collection cycle | Scoring is an independent process that runs after each collection cycle; can be re-run via CLI |
| observation_id FK on scores | `verification_scores` has FK to `verification_observations.id` for clean joins and referential integrity |
| UPSERT idempotency for concurrency | INSERT OR IGNORE / ON CONFLICT DO NOTHING — safe if cycles overlap; no explicit locking needed |
| Skip airports without METAR | Don't store empty rows — only airports with actual METAR data enter the database |
| One observation per airport per hour | Avoids inflating dataset with near-duplicate METARs; keep the one closest to top of hour |
| Cache airport resolution | Spatial query runs once per flight (status NULL→"collecting"); subsequent cycles read ICAOs from `flight_verification_map` |
| Batch fetch with chunking | Chunk ICAOs into batches of 400 (aviationweather.gov limit) with ~1s delay between calls |
| Skip packs without forecasts.json | Log warning, don't error — shouldn't happen for active flights but safe for backfill |
| CLI for backfill/export | Can run independently of web process, enables data science workflows |
| Store all fields, not just deltas | Raw observations enable future metrics we haven't thought of yet |
| Keep data forever — but tiered (#522) | The growing accuracy dataset is still the point; it grows in Parquet rather than InnoDB. Aggregates and row-level archive are both permanent, only the *online* raw window is bounded |
| Global rollup derived from the per-airport one | A second pass over raw could drift from the per-airport numbers; a `GROUP BY` over them structurally cannot |
| Activity counts get their own table | `COUNT(DISTINCT observation_id)` can't survive a per-group rollup (one obs → N scores) and is exactly the query that went pathological at 7d/30d (#448) |
| Archive by DB, not by inbox artifact | The droplet-fallback compute path writes snapshots straight to MySQL and emits no artifact, so the inbox has holes on node-failure days |
| Archive snapshots at all | They are the only record of what the forecast *said*; #491 had to denormalise the model gust onto scores precisely because they get pruned at 10 days |
| Manifest gate on every delete | After pruning, Parquet is the only copy — "the archive job was silently broken" must never be discoverable only after the rows are gone |
| Flight scores and flight-linked observations exempt from pruning | Tiny volume, pilot/debrief context, ERA5 re-analysis value; and observations are shared between tracks, so deleting one would cascade into an exempt flight score |
| Batched deletes with a commit per batch | Shared MySQL server with a 1 GB buffer pool — one multi-month DELETE would hold a huge transaction and stall every other client |
| Monthly rollup reads the daily table | The daily SUM columns were designed to compose this way; the old load-a-month-of-ORM-rows path derived the month independently and could drift from the days |
| A month waits for its days | A month is the sum of its days, so rolling one with a real gap writes a permanently undercounted aggregate that the "already rolled" set would never revisit. A day with *no* scores isn't a gap — gating on that instead would defer a month forever |
| `rollup_day` invalidates its month | Otherwise "already rolled" is a one-way ratchet and a late backfill could never reach the monthly aggregate |
| `max_id`, not row counts, in the delete gate | Counts stop being comparable the moment pruning starts leaving exempt rows behind; monotonic keys answer "is anything live newer than the archive?" exactly |
| Dual-track (flight + standalone) | Flight-based provides real route context; standalone provides broader coverage independent of user activity |
| Source column in unique constraints | Prevents flight and standalone scores from conflicting; all queries filter by source |
| Decoupled ingest/forecast/score loops | METAR ingest (30 min) separate from forecast fetch (07/19 UTC) and scoring (06/09/12/15/18 UTC) — cheap obs accumulate independently of twice-daily model fetches |
| Sounding enrichment in snapshots | Store derived sounding fields once, reconstruct proxy during scoring — avoids re-running MetPy analysis per observation |
| Error column on cycles | Captures traceback on failure for post-mortem without needing log access |
| Chunk-level retry with backoff | Partial success beats total failure — 800/830 airports scored is better than 0 |
| Verification cycles table | Performance monitoring — track phase timings and counts per cycle for both tracks |
| airport_forecast_snapshots | Standalone needs its own forecast storage since there's no briefing pack to reference |
| Daily digest at 08:00 UTC (DIGEST_HOUR_UTC) | Fixed time avoids multiple sends; lands after the morning forecast/scoring cycles |
| Category bias (directional severity) | Distinguishes optimistic misses (model said VFR, was IFR) from pessimistic — optimistic misses are more dangerous |
| Savepoint for observation inserts | Race condition protection — concurrent cycles may try to insert the same observation |
| Monthly rollup after month ends | Only roll up completed months — avoids partial aggregates; idempotent delete+reinsert allows safe re-runs |
| Category direction classification | Distinguishes optimistic (forecast too good — dangerous) from pessimistic (too conservative) with 1-step vs 2-step severity |
| Dashboard cache with staleness | Expensive queries cached as JSON; staleness checked via MAX(observation_time) comparison; falls back to live on stale |
| Composite index on (source, days_out, observation_time) | Migration 055 — prevents full table scans on dashboard queries that filter by source + days_out + time range |
| Store the raw model gust, not just its delta | The observed gust is NULL ~90% of hours and the snapshot holding the forecast gust is pruned at 10 days — a delta-only column would lose the over-warn signal permanently (#491) |
| Gust reported under two conditionings | Forecast-flagged and obs-flagged hours are nearly disjoint samples pointing opposite ways (over-warns on average, runs low on the gusty days); one blended number reads as self-contradictory |

## Implementation Status

**Phase 1 (Collection)**: Complete. Flight-based collection loop, CLI, observation archiving.
**Phase 2 (Scoring)**: Complete. Model scoring, TAF scoring, wind advisory comparison.
**Phase 3 (Surfacing)**: Partially complete. Admin dashboard and daily digest email implemented. Per-flight verification report and confidence annotations are future work.
**Standalone pipeline**: Complete. Airport watchlist, Open-Meteo forecasting, GRIB ceiling enrichment, scoring.

## Open Considerations

- **Historical METAR sources**: For backfill, aviationweather.gov has limited history. Iowa State Mesonet or OGIMET could provide deeper archives if needed.
- **SPECI handling**: Special METARs are more interesting for verification but occur irregularly. The 10-min poll should catch most of them.
- **Confidence annotations**: Future — annotate forecasts with "historically X% accurate at D-3" based on verification data.
- **Per-flight verification report**: Future — show per-flight model accuracy in the briefing viewer.

## References

- Existing METAR/TAF: `src/weatherbrief/tasks/route_weather.py`, `src/weatherbrief/models/observations.py`
- Flight category logic: `src/weatherbrief/analysis/airport_conditions.py`
- Sounding analysis: `src/weatherbrief/analysis/sounding/`
- Scheduler: `src/weatherbrief/scheduler.py`
- DB models: `src/weatherbrief/db/models.py`
- Design: `designs/metar-taf-route-weather.md`
