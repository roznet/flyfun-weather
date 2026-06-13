# METAR/TAF Accuracy System

> Dual-track verification: flight-based observation collection during active flights + standalone airport monitoring at fixed UTC hours, with daily digest email and admin dashboard.

## Intent

We already have NWP model forecasts stored in briefing packs (GFS, ECMWF, ICON, etc.) and we fetch METAR/TAF on D-0 for the current observation comparison. But we don't systematically **archive** observations or **score** model accuracy over time.

This system has two verification tracks:
1. **Flight-based**: Collects METAR/TAF observations during flight windows (1h before → flight end + 1h), scores against briefing pack forecasts
2. **Standalone**: Monitors ~830 pan-European watchlist airports via three decoupled loops — METAR ingest (every 30 min), forecast fetch (07/19 UTC), and scoring (06/09/12/15/18 UTC) — covering GFS/ICON/ECMWF at up to 4 days out
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
├── get_optimistic_bias_leaderboard()   ← from verification_daily_stats (#154)
└── get_digest_data()                   ← orchestrator → VerificationDigestData

tasks/verification_daily_rollup.py      ← daily pre-aggregation (#154)
├── rollup_day()                        ← pure SQL INSERT-SELECT, idempotent
├── completed_days()                    ← UTC dates with un-rolled scores
├── rollup_all_complete_days()          ← orchestrator
├── rollup_today_and_pending()          ← + partial today, called from scheduler
└── rebuild_all_days()                  ← post-migration re-roll

tasks/verification_rollup.py            ← monthly pre-aggregation (NWP + TAF)
├── completed_months()                  ← find months ready for rollup
├── rollup_month()                      ← aggregate one month (idempotent)
├── category_direction()                ← match/optimistic_1-2/pessimistic_1-2
├── advisory_direction()                ← match/optimistic/pessimistic
└── run_monthly_rollup()                ← entry point: find + process all

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

### `taf_verification_scores` — TAF vs METAR
TAFs are forecasts too, scored per METAR (one TAF scored against multiple METARs across its validity period — shows accuracy over time). Separate table because the key shape differs: `UNIQUE(icao, observation_time, taf_issue_time, source)`.

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
| **Horizon** | D-0 through D-7 (per pack) | Up to 4 days per model |
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

Full cycles fetch pressure-level data alongside surface fields from Open-Meteo. Each snapshot is enriched via `analyze_sounding()`:

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

**Parse-time hour filtering (#236)**: the fetch passes `hour_filter=SAMPLE_HOURS_UTC` to `OpenMeteoClient.fetch_multi_point`, so only sample-hour slots are materialised as `HourlyForecast`/`PressureLevelData` objects. Without it, ~80% of the 4-day hourly response (28 pressure levels for GFS) was parsed into Pydantic objects only to be discarded — and stayed alive across the chunk's sounding analysis in up to 4 concurrent threads, dominating the fetch phase's transient memory. The wire payload still contains every hour; the filter bounds parse memory, not bandwidth.

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
- The child gets `GRIB_DECODE_WORKERS=0` (inline decode) — no second decode pool inside the cgroup; decode priority is moot since the child doesn't share a pool with interactive briefings.
- Supervisor enforces a hard timeout (`STANDALONE_SUBPROCESS_TIMEOUT_S`, default 3 h) and records a failed `verification_cycles` row for children that die without writing one (SIGKILL/timeout never reach the in-cycle exception path); rows the child already wrote are not duplicated.
- **Rollback switch**: `STANDALONE_SUBPROCESS=0` reverts to the old in-process `asyncio.to_thread` path.
- `peak_rss_mb` on cycle rows now measures the child process (clean per-cycle attribution); `peak_cgroup_mb` semantics are unchanged. The 30-min METAR ingest stays in-process — too cheap to justify 48 spawns/day.

## Verification Stats & Digest

### Daily Digest Email

`notify/verification_email.py` sends a daily HTML + plaintext email at 08:00 UTC (configurable via `DIGEST_HOUR_UTC` env var; default chosen to land after the 07Z full-cycle results). Admin-only while testing. Accuracy color thresholds: green ≥80%, amber 60-80%, red <60%.

### Digest Data Model

`VerificationDigestData` contains: `period_label`, `activity` (ActivitySummary), `category_accuracy_today` / `category_accuracy_7d` (CategoryAccuracyRow lists), `notable_misses` (with directional severity — optimistic vs pessimistic), `category_bias` (CategoryBiasStats per model), `wind_advisory` (WindAdvisoryStats), `missed_warnings` (MissedWarning list).

### Admin Dashboard

`GET /api/admin/verification` (in `api/admin.py`) serves JSON for the web dashboard (`web/verification.html`). Source-filtered: flight and standalone stats are queried independently, never mixed. Dashboard shows category accuracy by model/days_out, notable misses, MAE trends.

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

NWP queries (post-#154) read from `verification_daily_stats`:

- `get_category_accuracy()` — category match rate per model/days_out; TAF added at query time from raw
- `get_category_bias_stats()` — match + 4 directional buckets (`_2` = "2 or more levels off")
- `get_wind_advisory_accuracy()` — advisory match rate; TAF added at query time
- `get_optimistic_bias_leaderboard()` — `(n_cat_opt_1 + 2 * n_cat_opt_2) / n` per airport, descending. Drives the leaderboard view that replaced the bias map.

Still on raw `verification_scores` (need individual rows or count-distinct):

- `get_activity_summary()` — observations, airports, flights, cycles, avg cycle duration
- `get_notable_misses()` — category busts with severity and direction
- `get_missed_warnings()` — WARNINGs that models failed to predict

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
- Configurable via env var `VERIFICATION_CORRIDOR_NM`

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
# Manual collection for a specific flight
python -m weatherbrief.verify collect --flight-id "LFPG-EDDF-2026-04-01-abc123"

# Score observations against all packs for a flight  
python -m weatherbrief.verify score --flight-id "LFPG-EDDF-2026-04-01-abc123"

# Backfill: re-process past flights (fetch historical METARs if available)
python -m weatherbrief.verify backfill --since 2026-01-01

# Export accuracy data
python -m weatherbrief.verify export --format csv --output accuracy.csv
python -m weatherbrief.verify export --format json --output accuracy.json

# Summary statistics
python -m weatherbrief.verify stats
python -m weatherbrief.verify stats --model gfs --days-out 1
python -m weatherbrief.verify stats --icao LFPG
```

## Data Volume

Negligible at any realistic scale: ~3K unique observations + ~50K scores per month at the current ~350 briefings/month (deduped across flights sharing airports), reaching only ~120 MB/month even at 10x growth. This is what makes the **keep-data-forever** choice safe — a growing accuracy dataset is the whole point, and MySQL handles it trivially.

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
| Keep data forever | This is the whole point — a growing accuracy dataset. At current scale, storage is negligible |
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
