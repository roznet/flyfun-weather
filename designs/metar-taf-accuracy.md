# METAR/TAF Accuracy System

> Dual-track verification: flight-based observation collection during active flights + standalone airport monitoring at fixed UTC hours, with daily digest email and admin dashboard.

## Intent

We already have NWP model forecasts stored in briefing packs (GFS, ECMWF, ICON, etc.) and we fetch METAR/TAF on D-0 for the current observation comparison. But we don't systematically **archive** observations or **score** model accuracy over time.

This system has two verification tracks:
1. **Flight-based**: Collects METAR/TAF observations during flight windows (1h before → flight end + 1h), scores against briefing pack forecasts
2. **Standalone**: Monitors ~830 pan-European watchlist airports via full cycles (06/18 UTC — fetch forecasts + score) and light cycles (09/12/15 UTC — score existing forecasts against fresh METARs), covering GFS/ICON/ECMWF at up to 4 days out
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
│   ├── _find_verifiable_flights()
│   ├── _collect_observations()
│   └── _score_observations()
├── run_standalone_verification_loop()  ← standalone, sleeps until next sample hour
│   └── run_standalone_cycle()          ← full (fetch+score) or light (score only)
├── run_digest_loop()                   ← daily at 06:00 UTC
│   └── send_verification_digest()

tasks/verification.py                   ← flight-based collection & scoring
├── collect_and_store()
├── score_against_models()
├── score_taf_against_metar()
└── finalize_completed_flights()

tasks/standalone_verification.py        ← standalone airport monitoring
├── run_standalone_cycle()              ← orchestrator (full or light cycle)
├── _fetch_forecasts_for_model()        ← Open-Meteo multi-point (batches of 100)
├── _enrich_with_sounding()             ← pressure-level sounding analysis
├── _enrich_with_grib()                 ← GFS cloud diagnostics for ceiling
└── _record_failed_cycle()              ← error capture on failure

tasks/verification_stats.py             ← shared queries for digest + dashboard
├── get_activity_summary()
├── get_category_accuracy()
├── get_notable_misses()
├── get_category_bias()
├── get_wind_advisory_stats()
└── get_digest_data()                   ← orchestrator → VerificationDigestData

tasks/verification_rollup.py            ← monthly pre-aggregation
├── completed_months()                  ← find months ready for rollup
├── rollup_month()                      ← aggregate one month (idempotent)
├── category_direction()                ← match/optimistic_1-2/pessimistic_1-2
├── advisory_direction()                ← match/optimistic/pessimistic
└── run_monthly_rollup()                ← entry point: find + process all

tasks/cache_builder.py                  ← pre-computed API response cache
├── rebuild_stats_cache()               ← stats:{source}:{period} (24h/7d/30d)
├── rebuild_verification_map_cache()    ← verif_map:{model}:{days_out}:{period}
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
├── VerificationMonthlyStatsRow         ← pre-aggregated monthly rollup
├── VerificationCacheRow                ← JSON cache for dashboard/map responses

API: /api/verification/stats            ← admin dashboard data (cache-aware)
CLI: python -m weatherbrief.verify      ← backfill, manual runs, export
```

## Database Schema

### `verification_observations` — Ground Truth Archive

```sql
CREATE TABLE verification_observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    icao            VARCHAR(4)  NOT NULL,
    observation_time DATETIME   NOT NULL,     -- METAR's own timestamp (UTC)
    collected_at    DATETIME    NOT NULL,      -- when we fetched it

    -- METAR fields
    metar_raw           TEXT,
    flight_category     VARCHAR(4),           -- VFR/MVFR/IFR/LIFR
    ceiling_ft          INTEGER,
    visibility_m        INTEGER,
    wind_dir            INTEGER,
    wind_speed_kt       INTEGER,
    wind_gust_kt        INTEGER,
    temperature_c       INTEGER,
    dewpoint_c          INTEGER,
    qnh                 FLOAT,
    weather             TEXT,                  -- JSON list: ["RA","BR",...]

    -- TAF fields (active at observation_time)
    taf_raw             TEXT,
    taf_applicable      TEXT,                  -- applicable trend text at obs time
    taf_issue_time      DATETIME,              -- when the TAF was issued
    taf_flight_category VARCHAR(4),            -- TAF-predicted category at obs time
    taf_ceiling_ft      INTEGER,
    taf_visibility_m    INTEGER,
    taf_wind_dir        INTEGER,
    taf_wind_speed_kt   INTEGER,
    taf_wind_gust_kt    INTEGER,

    UNIQUE(icao, observation_time)
);

CREATE INDEX ix_verif_obs_icao ON verification_observations(icao);
CREATE INDEX ix_verif_obs_time ON verification_observations(observation_time);
```

### `verification_scores` — Model vs Reality

One row per (airport, observation, model, model_init_time, source). The `source` column distinguishes flight-based vs standalone scores — all queries filter by source to prevent mixing.

```sql
CREATE TABLE verification_scores (
    -- ... standard columns ...
    source              VARCHAR(24) NOT NULL DEFAULT 'flight',
    -- Values: 'flight', 'standalone_full', 'standalone_light'
    cloud_base_delta_ft INTEGER,    -- added for standalone ceiling scoring
    lcl_delta_ft        INTEGER,    -- LCL-based ceiling delta

    UNIQUE(icao, observation_time, model, model_init_time, source),
    -- ...
);

CREATE INDEX ix_verif_scores_source ON verification_scores(source, model, days_out);
```

### `taf_verification_scores` — TAF vs METAR

TAFs are forecasts too. The `source` column is included in the unique constraint.

```sql
CREATE TABLE taf_verification_scores (
    -- ... standard columns ...
    source              VARCHAR(24) NOT NULL DEFAULT 'flight',

    UNIQUE(icao, observation_time, taf_issue_time, source),
    -- ...
);
```

### `verification_cycles` — Performance Metrics

Tracks timing and counts for each collection cycle (flight or standalone).

```sql
CREATE TABLE verification_cycles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          VARCHAR(24) NOT NULL DEFAULT 'flight',
    -- Values: 'flight', 'standalone_full', 'standalone_light'
    started_at      DATETIME NOT NULL,
    duration_ms     INTEGER,
    error           TEXT,              -- last 500 chars of traceback on failure
    -- Phase timings
    find_ms         INTEGER,
    gather_ms       INTEGER,
    fetch_ms        INTEGER,           -- 0 for light cycles (no forecast fetch)
    store_ms        INTEGER,
    score_ms        INTEGER,
    finalize_ms     INTEGER,
    -- Counts
    flights_found   INTEGER,
    airports_found  INTEGER,
    observations_stored INTEGER
);
```

### `airport_forecast_snapshots` — Standalone NWP Forecasts

Stores Open-Meteo forecasts at watchlist airports for standalone verification scoring.

```sql
CREATE TABLE airport_forecast_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    icao            VARCHAR(4) NOT NULL,
    model           VARCHAR(20) NOT NULL,
    model_init_time DATETIME NOT NULL,
    forecast_hour   DATETIME NOT NULL,
    fetched_at      DATETIME NOT NULL,
    -- Surface fields
    temperature_c   FLOAT,
    dewpoint_c      FLOAT,
    visibility_m    FLOAT,
    wind_speed_kt   FLOAT,
    wind_dir_deg    FLOAT,
    wind_gust_kt    FLOAT,
    precipitation_mm FLOAT,
    cape            FLOAT,
    -- Ceiling estimates
    nwp_ceiling_ft  INTEGER,
    cloud_base_ft   INTEGER,
    lcl_ft          INTEGER,
    -- Sounding-derived fields (from pressure-level analysis)
    sounding_ceiling_ft    INTEGER,
    sounding_cloud_base_ft INTEGER,
    sounding_convective_risk INTEGER,
    freezing_level_ft      INTEGER,
    sounding_cape_jkg      FLOAT,
    sounding_cin_jkg       FLOAT,
    sounding_lifted_index  FLOAT,

    UNIQUE(icao, model, model_init_time, forecast_hour)
);
```

### `verification_monthly_stats` — Pre-Aggregated Monthly Rollup

One row per (month, source, model, days_out, icao). Aggregated from raw scores once a month is complete (current UTC past month end). Idempotent — re-running deletes and recreates rows for a given month.

```sql
CREATE TABLE verification_monthly_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    month           DATETIME NOT NULL,       -- first day of month (UTC)
    source          VARCHAR(24) NOT NULL,    -- 'flight' or 'standalone'
    model           VARCHAR(20) NOT NULL,    -- 'gfs', 'icon', 'ecmwf', 'taf'
    days_out        INTEGER NOT NULL,        -- 0, 1, 2, ...
    icao            VARCHAR(4) NOT NULL,
    n_scores        INTEGER NOT NULL,
    -- Category direction counts (match/optimistic_1/2/pessimistic_1/2)
    n_cat_match     INTEGER, n_cat_optimistic_1 INTEGER, n_cat_optimistic_2 INTEGER,
    n_cat_pessimistic_1 INTEGER, n_cat_pessimistic_2 INTEGER,
    -- Advisory direction counts
    n_advisory_match INTEGER, n_advisory_optimistic INTEGER, n_advisory_pessimistic INTEGER,
    -- Continuous error metrics
    ceiling_mae_ft FLOAT, ceiling_bias_ft FLOAT, visibility_mae_m FLOAT,
    wind_speed_mae_kt FLOAT, temperature_mae_c FLOAT,
    -- Hit/miss/false-alarm (model scores only, NULL for TAF)
    n_precip_hit INTEGER, n_precip_miss INTEGER, n_precip_false_alarm INTEGER,
    n_convection_hit INTEGER, n_convection_miss INTEGER, n_convection_false_alarm INTEGER,
    UNIQUE(month, source, model, days_out, icao)
);
```

**Category direction**: `category_direction(obs, fcst)` classifies each score as match, optimistic_1/2 (forecast better than reality — dangerous), or pessimistic_1/2 (forecast worse — conservative). TAF scores use the same classification via `lead_hours // 24` → `days_out` bucketing.

### `verification_cache` — Pre-Computed API Response Cache

Stores serialized JSON responses for expensive dashboard/map queries, with staleness tracking.

```sql
CREATE TABLE verification_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key       VARCHAR(128) NOT NULL UNIQUE,  -- e.g. 'stats:standalone:7d'
    computed_at     DATETIME NOT NULL,
    source_max_time DATETIME,     -- MAX(observation_time) when cache was built
    data_json       TEXT NOT NULL  -- complete JSON response
);
```

**Cache keys**: `stats:{source}:{period}`, `verif_map:{model}:{days_out}:{period}`, `forecast_map:{day}:{hour}`.
**Staleness**: `is_stale()` compares cached `source_max_time` against live `MAX(observation_time)` or `MAX(fetched_at)`. If live > cached, cache is stale. Missing entries are always stale.
**Rebuild trigger**: `rebuild_all()` runs after each standalone verification cycle in `_run_standalone_once()`.

### `flight_verification_map` — Thin Flight Linkage

Maps flights to corridor airports. Populated on first collection cycle via spatial query; reused on subsequent cycles to avoid re-querying. Only airports that have produced at least one METAR are kept — airports with no METAR data are skipped entirely and never inserted.

```sql
CREATE TABLE flight_verification_map (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id       VARCHAR(256) NOT NULL,      -- FK → flights ON DELETE CASCADE
    icao            VARCHAR(4)   NOT NULL,       -- corridor airport
    observation_id  INTEGER      NULL,           -- FK → verification_observations (NULL until first METAR)
    distance_from_route_nm FLOAT,               -- how far airport is from route

    UNIQUE(flight_id, icao, observation_id),
    FOREIGN KEY (flight_id) REFERENCES flights(id) ON DELETE CASCADE,
    FOREIGN KEY (observation_id) REFERENCES verification_observations(id)
);

CREATE INDEX ix_fvm_flight ON flight_verification_map(flight_id);
CREATE INDEX ix_fvm_icao   ON flight_verification_map(icao);
```

**Note**: `observation_id` is nullable — on the first cycle, rows are created with just `flight_id + icao + distance` to cache the corridor resolution. As observations arrive, new rows are inserted with the `observation_id` set. Airports that never produce a METAR simply have no observation-linked rows.

### Flight-Level Tracking

Add column to `flights` table:

```sql
ALTER TABLE flights ADD COLUMN verification_status VARCHAR(16) DEFAULT NULL;
-- NULL = not yet started, "collecting" = actively gathering, "complete" = collection done, "scored" = scoring done
```

**Lifecycle**:
- `NULL`: Flight exists but hasn't entered the verification window yet (or has no packs)
- `"collecting"`: Set on first cycle that picks up this flight. Subsequent cycles see `"collecting"` and skip the spatial query (read cached ICAOs from map instead)
- `"complete"`: Set when `now > flight_end + 1h`. Collection done, ready for scoring.
- `"scored"`: Set after scoring completes. No further collection or scoring.

The collection query `WHERE verification_status IS NULL OR verification_status NOT IN ('complete', 'scored')` picks up both NULL and "collecting" flights. NULL flights are checked for eligibility (has packs, in time window) and promoted to "collecting" on first match. Scoring runs automatically after each collection cycle for any flights in "complete" status.

## Standalone Verification Pipeline

### Intent

Flight-based verification is limited to airports along active flight routes. Standalone verification monitors ~830 pan-European watchlist airports, building a broader accuracy dataset independent of user activity.

### How It Differs from Flight-Based

| Aspect | Flight-Based | Standalone |
|--------|-------------|-----------|
| **Trigger** | Active flights in observation window | Fixed sample hours (6, 9, 12, 15, 18 UTC) |
| **Polling** | Every 10 minutes | Sleep until next sample hour |
| **Airports** | Flight corridor (15nm) | Watchlist (~830 pan-European airports) |
| **Forecast source** | Briefing pack forecasts.json | Independent Open-Meteo fetch + GRIB + sounding |
| **Models** | All models in briefing pack | GFS, ICON, ECMWF (3 models) |
| **Horizon** | D-0 through D-7 (per pack) | Up to 4 days per model |
| **Source tag** | `source='flight'` | `source='standalone_full'` or `'standalone_light'` |
| **Database** | `verification_observations` + scores | Same + `airport_forecast_snapshots` |

### Full vs Light Cycles

The standalone pipeline runs two cycle types to balance API cost against scoring frequency:

| | Full Cycle (06, 18 UTC) | Light Cycle (09, 12, 15 UTC) |
|---|---|---|
| **Forecast fetch** | Yes — Open-Meteo for 3 models | No — uses stored snapshots |
| **Sounding enrichment** | Yes — pressure-level analysis | No |
| **GRIB enrichment** | Yes — cloud diagnostics | No |
| **METAR fetch** | Yes | Yes |
| **Scoring** | Yes — against fresh snapshots | Yes — against latest stored snapshots |
| **Phases executed** | A→B→C→D→E | C→D→E only |
| **Source tag** | `standalone_full` | `standalone_light` |

**Why**: Full cycles at 06/18 UTC align with fresh model runs. Light cycles in between provide continuous scoring coverage without redundant forecast fetching — the same stored snapshots get scored against fresh METARs.

### Airport Watchlist

`tasks/airport_watchlist.py` discovers METAR-reporting airports. Default ICAO prefixes: LF, ED, EG, EH, EB, LS, LO (Western/Central Europe). Sources: euro_aip DB + aviationweather.gov METAR endpoint (batch queries with `@prefix`, batch size 400). Config file: `configs/airport_watchlist.json`.

### Standalone Cycle Flow (Five Phases)

```
Full cycle (phases A–E):
  A. Fetch Open-Meteo forecasts per model (batches of 100, with pressure levels)
  B. Store snapshots + enrich with sounding analysis + GRIB cloud diagnostics
  C. Fetch METAR/TAF observations
  D. Score forecasts vs observations (using stored sounding proxy)
  E. Prune old snapshots (>10 days)

Light cycle (phases C–E only):
  C. Fetch METAR/TAF observations
  D. Score latest stored snapshots vs fresh observations
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

### Scheduler Integration

The standalone subsystem now runs as **two independent loops** (disable individually with `DISABLE_FORECAST_FETCH=1` / `DISABLE_STANDALONE_VERIFICATION=1`):

- **Forecast fetch** — fires at `FORECAST_FETCH_HOURS_UTC = [7, 19]`. Runs `run_standalone_cycle(fetch_forecasts=True, score_observations=False)`. Timed ~30 min after ECMWF 00Z/12Z deliveries land (~06:35 / 18:35 UTC) so each fetch picks up the freshest GFS/ICON/ECMWF inits.
- **Verification** — fires at `VERIFICATION_HOURS_UTC = [6, 9, 12, 15, 18]`. Runs `run_standalone_cycle(fetch_forecasts=False, score_observations=True)`. `cycle_time = wall clock`, so METARs are pulled near each synoptic bucket and scored against whatever snapshots are already in DB (most recent fetch wins).

Both loops compute next fire hour and sleep until then (no polling), retry after 15 min on failure, use 240 s startup delay, and trigger a `cache_builder.rebuild_all` after each successful cycle. The legacy combined cycle (`fetch_forecasts=True, score_observations=True`, source `standalone_full`) is still callable from CLI/tests but is no longer scheduled.

## Verification Stats & Digest

### Daily Digest Email

`notify/verification_email.py` sends a daily HTML + plaintext email at 08:00 UTC (configurable via `DIGEST_HOUR_UTC` env var; default chosen to land after the 07Z full-cycle results). Admin-only while testing. Accuracy color thresholds: green ≥80%, amber 60-80%, red <60%.

### Digest Data Model

`VerificationDigestData` contains: `period_label`, `activity` (ActivitySummary), `category_accuracy_today` / `category_accuracy_7d` (CategoryAccuracyRow lists), `notable_misses` (with directional severity — optimistic vs pessimistic), `category_bias` (CategoryBiasStats per model), `wind_advisory` (WindAdvisoryStats), `missed_warnings` (MissedWarning list).

### Admin Dashboard

`GET /api/verification/stats` serves JSON for the web dashboard (`web/verification.html`). Source-filtered: flight and standalone stats are queried independently, never mixed. Dashboard shows category accuracy by model/days_out, notable misses, MAE trends.

**Cache layer**: Unfiltered requests (no country/airport filter) use `verification_cache` for fast responses. If the cache is stale or missing, falls back to live query. Filtered requests always run live (small result sets). Cache is rebuilt after each standalone verification cycle via `rebuild_all()` in `cache_builder.py`.

### Monthly Rollup

`tasks/verification_rollup.py` aggregates raw `verification_scores` and `taf_verification_scores` into `verification_monthly_stats` once a month completes. Groups by (source, model, days_out, icao) and computes:
- Category direction counts (match/optimistic_1-2/pessimistic_1-2) — classifies whether forecast was too good (dangerous) or too conservative
- Advisory direction counts (match/optimistic/pessimistic)
- Continuous metrics: ceiling MAE + bias, visibility MAE, wind speed MAE, temperature MAE
- Hit/miss/false-alarm for precipitation and convection (model scores only)

Rollup is idempotent (delete + re-insert per month). Currently triggered manually; designed for offline/retention pipeline integration.

### Query Functions

All in `tasks/verification_stats.py`. Every query accepts a `source` parameter ('flight' or 'standalone') to ensure isolation:

- `get_activity_summary()` — observations, airports, flights, cycles, avg cycle duration
- `get_category_accuracy()` — category match rate per model/days_out (includes TAF as pseudo-model)
- `get_notable_misses()` — category busts with severity and direction (optimistic/pessimistic)
- `get_category_bias()` — optimistic vs pessimistic miss rates per model
- `get_wind_advisory_stats()` — wind advisory match rate
- `get_missed_warnings()` — WARNINGs that models failed to predict

## Collection Loop (Flight-Based)

### Scheduler Integration

Loop in `scheduler.py`, alongside other async loops:

```python
async def run_verification_loop(app_state) -> None:
    """Collect METAR/TAF observations for active flights."""
    logger.info("Verification loop started (poll every %ds)", _VERIF_POLL_SECONDS)
    await asyncio.sleep(_VERIF_STARTUP_DELAY)

    while True:
        try:
            await _process_verifications(app_state)
        except Exception:
            logger.error("Verification cycle failed", exc_info=True)
        await asyncio.sleep(_VERIF_POLL_SECONDS)
```

**Poll interval**: 10 minutes. METARs update every 30-60 minutes, SPECIs can be more frequent. 10 minutes gives good coverage without hammering aviationweather.gov.

### Finding Active Flights

```python
def _find_verifiable_flights(db: Session) -> list[FlightRow]:
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
| Model ceiling | `reconcile_ceiling(sounding, hourly)` | `analysis/airport_conditions.py:117` | `SoundingAnalysis` + `HourlyForecast` → min of sounding & NWP ceiling |
| Model visibility | `hourly.visibility_m / 1609.34` (→ statute miles) | `analysis/airport_conditions.py:200` | Direct from model, converted to SM like advisories do |
| Model flight category | `classify_flight_category(ceiling_ft, visibility_sm)` | `analysis/airport_conditions.py:48` | Standard VFR/MVFR/IFR/LIFR thresholds |
| Model wind advisory | `compute_wind_advisory(dir, speed, gust, runway_ends)` | `tasks/route_weather.py:109` | Same thresholds: xw ≥15kt amber, ≥25kt red; gust ≥25/35kt |
| Model precipitation | `hourly.precipitation_mm > 0 or hourly.snowfall_cm > 0` | `analysis/sounding/precipitation.py:44` | Same check as `assess_precipitation()` |
| Model convection | `sounding.convective.risk_level` from `assess_convective_thermo()` | `analysis/sounding/convective.py:97` | CAPE thresholds: 50/300/1000/2000 J/kg, CIN suppression |

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

Estimates at current scale (350 briefings/month, growing):

| Table | Rows/month | Row size | Monthly size |
|-------|-----------|----------|-------------|
| `verification_observations` | ~3,000 | ~500B | ~1.5 MB |
| `verification_scores` | ~50,000 | ~200B | ~10 MB |
| `taf_verification_scores` | ~3,000 | ~150B | ~450 KB |
| `flight_verification_map` | ~10,000 | ~50B | ~500 KB |

**Calculation**: ~350 flights × ~8 airports × ~6 METAR fetches/flight = ~17K observations, but deduplicated across flights sharing airports → ~3K unique. Scores: 3K obs × ~6 models × ~3 packs = ~50K.

At 10× growth (3,500 flights/month): ~120 MB/month → ~1.4 GB/year. Trivial for MySQL.

## Accuracy Metrics & Queries

### Per-Model Accuracy at Lead Time

```sql
SELECT model, days_out,
       COUNT(*) as n,
       AVG(category_match) as category_accuracy,
       AVG(ABS(ceiling_delta_ft)) as ceiling_mae_ft,
       AVG(ceiling_delta_ft) as ceiling_bias_ft,
       AVG(ABS(wind_speed_delta_kt)) as wind_mae_kt,
       AVG(ABS(temperature_delta_c)) as temp_mae_c
FROM verification_scores
GROUP BY model, days_out
ORDER BY model, days_out;
```

### TAF vs Model Accuracy

```sql
-- Which is more accurate for ceiling: TAF or GFS at D-0?
SELECT 'TAF' as source,
       AVG(category_match) as cat_accuracy,
       AVG(ABS(ceiling_delta_ft)) as ceiling_mae
FROM taf_verification_scores
UNION ALL
SELECT 'GFS',
       AVG(category_match),
       AVG(ABS(ceiling_delta_ft))
FROM verification_scores
WHERE model = 'gfs' AND days_out = 0;
```

### Regional Accuracy (by ICAO prefix)

```sql
SELECT SUBSTR(icao, 1, 2) as region,
       model,
       AVG(category_match) as accuracy
FROM verification_scores
WHERE days_out = 1
GROUP BY region, model
ORDER BY region, accuracy DESC;
```

### Seasonal Accuracy

```sql
SELECT model,
       CASE
         WHEN MONTH(observation_time) IN (12,1,2) THEN 'winter'
         WHEN MONTH(observation_time) IN (6,7,8) THEN 'summer'
         ELSE 'shoulder'
       END as season,
       AVG(category_match) as accuracy
FROM verification_scores
WHERE days_out <= 1
GROUP BY model, season;
```

### IFR Accuracy (when it matters most)

```sql
SELECT model, days_out,
       AVG(category_match) as accuracy,
       COUNT(*) as n_ifr_obs
FROM verification_scores
WHERE obs_flight_category IN ('IFR', 'LIFR')
GROUP BY model, days_out;
```

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
| Full/light cycle split | Full at 06/18 UTC (fresh model runs), light at 09/12/15 UTC — reduces API load 60% while maintaining scoring frequency |
| Sounding enrichment in snapshots | Store derived sounding fields once, reconstruct proxy during scoring — avoids re-running MetPy analysis per observation |
| Error column on cycles | Captures traceback on failure for post-mortem without needing log access |
| Chunk-level retry with backoff | Partial success beats total failure — 800/830 airports scored is better than 0 |
| Verification cycles table | Performance monitoring — track phase timings and counts per cycle for both tracks |
| airport_forecast_snapshots | Standalone needs its own forecast storage since there's no briefing pack to reference |
| Daily digest at 06:00 UTC | Fixed time avoids multiple sends; early morning for overnight review |
| Category bias (directional severity) | Distinguishes optimistic misses (model said VFR, was IFR) from pessimistic — optimistic misses are more dangerous |
| Savepoint for observation inserts | Race condition protection — concurrent cycles may try to insert the same observation |
| Monthly rollup after month ends | Only roll up completed months — avoids partial aggregates; idempotent delete+reinsert allows safe re-runs |
| Category direction classification | Distinguishes optimistic (forecast too good — dangerous) from pessimistic (too conservative) with 1-step vs 2-step severity |
| Dashboard cache with staleness | Expensive queries cached as JSON; staleness checked via MAX(observation_time) comparison; falls back to live on stale |
| Composite index on (source, observation_time) | Migration 038 — prevents full table scans on dashboard queries that filter by source + time range |

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
