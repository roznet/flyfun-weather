# Standalone Airport Verification Pipeline

> **STATUS: BUILT & SUPERSEDED (historical plan).** This was the original design proposal; the feature has shipped and is in production. The authoritative, as-built design now lives in [`../metar-taf-accuracy.md`](../metar-taf-accuracy.md) (INDEX: `metar-taf-accuracy`). Read THAT for current truth — this file is kept only as a record of the original intent and rejected options. Notable divergences from the as-built system: the single combined "5 sample hours" cycle became **three decoupled loops** (METAR ingest every 30 min, forecast fetch at 07/19Z, scoring at 06/09/12/15/18Z); cycles run in an isolated **subprocess** (#236); a **daily** rollup (`verification_daily_stats`) was added alongside the monthly one; the watchlist grew to **~620** airports (`configs/airport_watchlist.json`). Recommend moving to `archive/`.

> Flight-independent NWP accuracy dataset: predict weather at ~276 METAR-reporting airports across Western/Central Europe, then score against actual METARs at multiple lead times (D-0 through D-7).

## Context

The existing verification system scores NWP models against METARs, but only for airports along user flight routes — limited data volume. A standalone pipeline verifying **every METAR-reporting airport** in FR/DE/UK/NL/BE/CH/AT builds a much richer accuracy dataset, independent of user activity. This answers questions like "how accurate is GFS vs ECMWF at D-3 for ceiling in Alpine regions?"

## Key Decisions

- **5 sample points per day**: every 3h from 06Z to 18Z (covers the flying day, skips night)
- **Configurable**: via `standalone_verification_config` DB table (sample hours, model list, enabled flag) — adjustable from admin hub without redeployment
- **Multi-day lead times from the start** — store forecast snapshots in DB, score against METARs as they arrive
- **3 models only**: GFS, ICON, ECMWF — the models with full data coverage (visibility + ceiling via GRIB). Drop UKMO and MétéoFrance (missing visibility, no GRIB source). Each model's forecast horizon is capped to its GRIB coverage (see Model Forecast Horizons)
- **GRIB enrichment is a core dependency** — provides ceiling and cloud base data that Open-Meteo API lacks. GRIB downloads are per-grid (not per-point), so 276 airports cost the same as route points. Cache is shared with flight pipeline. **Note**: `decode_cloud_diag_per_point()` does bilinear interpolation per point via xarray/cfgrib — test at 276-point scale to verify memory profile before production.
- **3 ceiling estimates** stored per snapshot for accuracy comparison: NWP ceiling (GRIB), cloud base (GRIB), LCL (computed from T-Td)
- **DB storage** for forecast snapshots (`airport_forecast_snapshots` table) — structured lookup/matching, not file archival
- **Reuse existing tables** — `verification_observations` (already flight-independent) + `verification_scores` with new `source` column (included in unique constraint)
- **ECMWF phasing**: Pipeline built for all 3 models from day one. GFS/ICON score immediately (existing GRIB pipeline). ECMWF scores flow once SFTP GRIB delivery is active (ordered 2026-03-27, delivers to `/mnt/flyfun_data/ecmwf/incoming/`)
- **Unified `days_out` semantics** — both standalone and flight pipelines compute `days_out` as `(forecast_hour.date() - model_init_time.date()).days`. Flight pipeline updated to use this instead of `BriefingPackRow.days_out` (existing flight verification data is minimal, OK to reset).

---

## Scheduling & Model Init Times

### Model Update Cadence

| Model | Init times (UTC) | Typical Open-Meteo availability delay | Runs captured per day |
|-------|------------------|----------------------------------------|----------------------|
| GFS   | 00, 06, 12, 18Z  | ~4-6h                                  | 2-3 distinct runs    |
| ICON  | 00, 06, 12, 18Z  | ~4-6h                                  | 2-3 distinct runs    |
| ECMWF | 00, 12Z          | ~6-8h                                  | 1-2 distinct runs    |

### How scheduling works

The pipeline runs **at each of the 5 sample hours** (06, 09, 12, 15, 18Z), not every 10 minutes. Most cycles have no new model data and would be wasted work with a frequent poll.

At each sample hour:
1. Call `fetch_model_metadata()` → get current `model_init_time` per model (3 small HTTP GETs)
2. **Skip forecast fetching** if we already have snapshots for this `(model, model_init_time)` — UPSERT handles idempotency, but skipping avoids wasted API calls
3. **Always fetch METAR** and score against all stored forecast snapshots for this hour

This naturally captures different model runs across the day:

| Sample hour | Likely model init times available |
|-------------|-----------------------------------|
| 06Z | GFS 00Z, ICON 00Z, ECMWF 00Z |
| 09Z | Same (or GFS/ICON 06Z if fast) |
| 12Z | GFS 06Z, ICON 06Z, ECMWF 00Z |
| 15Z | GFS 12Z, ICON 12Z, ECMWF 12Z |
| 18Z | GFS 12Z, ICON 12Z, ECMWF 12Z |

Each model's `model_init_time` is resolved **independently** — GFS may have init 06Z while ECMWF still shows 00Z at the same cycle. The `model_init_time` is stored per snapshot row, so different init times per model are handled naturally.

**Failure handling**: If `fetch_model_metadata()` fails for a model (timeout, 5xx), skip that model for this cycle. Next sample hour retries. If Open-Meteo is down entirely, skip forecast fetching but still fetch METARs and score against existing snapshots.

**Missed model inits (by design)**: With 5 sample hours and 4 daily model runs (GFS/ICON), we capture 2-3 distinct init times per model, not all 4. E.g., GFS 06Z init may become available at ~10Z — after the 09Z cycle but before 12Z, when the 12Z init has already superseded it. This is acceptable: we're building a statistical dataset, not capturing every run. Over weeks the coverage is well-balanced across init times.

### Scheduler integration

```python
async def run_standalone_verification_loop(app_state) -> None:
    """Run standalone verification at configured sample hours."""
    while True:
        now_utc = datetime.now(timezone.utc)
        current_hour = now_utc.hour
        if current_hour in sample_hours and now_utc.minute < 15:
            # Within 15-min window of a sample hour
            await asyncio.to_thread(_run_standalone_cycle, app_state)
            await asyncio.sleep(3600)  # sleep ~1h to avoid re-triggering
        else:
            await asyncio.sleep(300)  # check every 5 min
```

Disableable via `DISABLE_STANDALONE_VERIFICATION=1` env var or config table toggle.

---

## Single Airport Walkthrough: LFPG (Paris CDG)

### April 2, 06Z cycle

**Phase A — Fetch forecasts from Open-Meteo + GRIB ceiling**

```
1. fetch_model_metadata(["gfs", "icon", "ecmwf"]) → per-model init times
   GFS: init=Apr 2 00Z, ICON: init=Apr 2 00Z, ECMWF: init=Apr 1 12Z

2. For each model with a NEW init time (not yet in airport_forecast_snapshots):
   fetch_multi_point(airports_chunk, start=Apr 2, end=end_date_for_model)

   End date per model: GFS/ECMWF → Apr 9 (7 days), ICON → Apr 7 (5 days)

   Open-Meteo batching: chunk 276 airports into groups of ~100 per API call
   → ~3 calls per model, ~9 total.

   Discard all except sample hours (06, 09, 12, 15, 18Z)
   → GFS/ECMWF: 35 values per airport, ICON: 25 values per airport.

3. In parallel: fetch GRIB cloud diagnostics for GFS + ICON at sample forecast hours.
   → Extract nwp_ceiling_ft and cloud_base_ft per airport location.
   → Compute lcl_ft = 400 * (T_2m - Td_2m) from Open-Meteo surface data.
```

**Phase B — Store forecast snapshots in DB**

UPSERT rows into `airport_forecast_snapshots` per airport×model (35 for GFS/ECMWF, 25 for ICON):

```
icao=LFPG, model=gfs, model_init_time=Apr 2 00Z, fetched_at=Apr 2 06:02Z

forecast_hour=Apr 2 06Z  → temp=8.2, dewpoint=5.1, vis=12000, wind=240/12,
                            nwp_ceiling=1200, cloud_base=1100, lcl=1240, ...
forecast_hour=Apr 2 09Z  → temp=10.1, dewpoint=6.3, vis=15000, wind=250/14, ...
...
forecast_hour=Apr 9 18Z  → temp=14.5, dewpoint=8.2, vis=20000, wind=220/8, ...
```

UNIQUE on `(icao, model, model_init_time, forecast_hour)` — re-fetching same model run is a no-op.

GFS: 35 rows + ICON: 25 rows + ECMWF: 35 rows = **95 rows for LFPG** per cycle (when all 3 models have new init times).

**Phase C — Fetch METAR + TAF**

```
fetch_observations_batch(all 276 ICAOs)  ← 1 API call (276 < 400 batch limit)
→ LFPG METAR at 05:50Z: ceiling 800ft, vis 3000m, wind 240/12kt, cat=IFR
→ TAF issued 05:30Z with applicable group at 06Z
→ UPSERT into verification_observations (existing table, existing hourly dedup)
```

`fetch_observations_batch()` already fetches both METAR and TAF in a single call via `RouteWeatherService`. Pass `icao_to_flights={}` to `store_observations()` (no flight linkage — handles empty mapping gracefully).

**Phase D — Score: match this METAR against ALL stored forecasts for this hour**

Build a minimal `HourlyForecast` from the snapshot row via `snapshot_to_hourly()` adapter (~20 lines). Populates `nwp_cloud_diagnostics.ceiling_ft` from stored `nwp_ceiling_ft`. Pass to existing `_score_model_vs_metar()` with `sounding=None`.

**Time matching rule**: Match each observation to the nearest `forecast_hour` in snapshots. Since sample hours are 3h apart, the max mismatch is ±90 min. In practice METARs are issued at ~X:50 or X:20, so the typical mismatch is <15 min. Rule: pick the `forecast_hour` with minimum `|forecast_hour - observation_time|`.

**Batch scoring query**: Fetch all matching snapshots for all airports in one query, then match in Python — not one query per airport:

```sql
SELECT * FROM airport_forecast_snapshots
WHERE forecast_hour BETWEEN :cycle_time - INTERVAL 90 MINUTE
                        AND :cycle_time + INTERVAL 90 MINUTE
ORDER BY icao, model, model_init_time
```

This returns all snapshots (across all airports, models, and init times) that predicted this sample hour. Group by `(icao, model, model_init_time)` in Python, match each against the airport's observation, and **batch-insert scores** via `session.bulk_save_objects()` — avoid individual INSERTs (could be hundreds per cycle).

Score D-0 (this cycle's forecast):
```
GFS (init Apr 2 00Z, lead=6h, days_out=0):
  predicted ceiling 1200ft → actual 800ft → delta=+400ft
  predicted cat MVFR → actual IFR → category_match=False
  → INSERT verification_scores(source='standalone', days_out=0, lead_hours=6)

ECMWF (init Apr 1 12Z, lead=18h, days_out=0):
  predicted ceiling 900ft → actual 800ft → delta=+100ft
  predicted cat IFR → actual IFR → category_match=True
  → INSERT verification_scores(source='standalone', days_out=0, lead_hours=18)

(same for ICON)
```

Score older forecasts (D-1 through D-7) — forecasts stored in previous cycles that predicted this hour:
```
GFS (init Apr 1 00Z, lead=30h, days_out=1):
  predicted LFPG at Apr 2 06Z: ceiling 1500ft → delta=+700ft, cat_match=False

GFS (init Mar 30 00Z, lead=78h, days_out=3):
  predicted LFPG at Apr 2 06Z: ceiling 2500ft → VFR vs actual IFR → cat_match=False

...up to 7 days back (whatever forecast snapshots exist for this hour)
```

`days_out` = `(forecast_hour.date() - model_init_time.date()).days` — same formula used by the flight pipeline.

**TAF scoring** — runs independently in the same phase:
```
_score_taf_vs_metar(obs_row, obs_weather, runway_ends)
→ Compares TAF prediction against METAR. Needs only the observation row + runway data.
→ INSERT taf_verification_scores(source='standalone')
```

**Phase E — Prune old forecast snapshots**

```sql
DELETE FROM airport_forecast_snapshots WHERE fetched_at < now - INTERVAL 10 DAY
```

10 days ensures D-7 forecasts survive long enough to be scored (7 days + buffer). Run as part of existing retention loop.

### How scoring accumulates over the first week

| Day | What happens | Scores for LFPG (cumulative) |
|-----|-------------|------------------------------|
| Apr 2 | First cycle. D-0 scores only (no older forecasts exist) | 3 models × 5 hours = 15 |
| Apr 3 | D-0 + D-1 scores (yesterday's forecasts scored against today's METARs) | 15 + 30 = 45 |
| Apr 4 | D-0 + D-1 + D-2 | 90 |
| Apr 5 | D-0 through D-3 | 150 |
| Apr 7 | ICON reaches full D-4 coverage; GFS/ECMWF at D-5 | 210 |
| Apr 9 | GFS/ECMWF reach full D-7 coverage (ICON stays at D-4 max) | ~280 |
| Steady state | ~28 scores/day for LFPG (GFS/ECMWF: D-0..D-7, ICON: D-0..D-4) | growing |

---

## What's Stored Where

| Data | Where | When written | When read | Retention |
|------|-------|-------------|-----------|-----------|
| Raw Open-Meteo response | Memory only | Each cycle | Extract sample hours, discard | Never stored |
| GRIB cloud diagnostics | Disk cache (shared with flight pipeline) | Each cycle | Extract ceiling/cloud_base per airport | **48h cache TTL** |
| Forecast snapshots (sample hours) | DB: `airport_forecast_snapshots` | Each cycle, UPSERT | During scoring, for up to 10 days | **Deleted after 10 days** |
| METAR observations | DB: `verification_observations` | Each cycle, UPSERT with dedup | During scoring + digest/dashboard | **Forever** |
| Model accuracy scores | DB: `verification_scores` (source='standalone') | Each cycle, after scoring | Digest email + dashboard queries | **12 months raw, then rollup** |
| TAF accuracy scores | DB: `taf_verification_scores` (source='standalone') | Each cycle, if TAF present | Digest email + dashboard queries | **12 months raw, then rollup** |

### What's NOT stored

- No soundings or pressure level data
- No route analyses, cross-sections, Skew-T
- No briefing packs, digest, advisories
- No flight or user linkage
- No disk files beyond shared GRIB cache — everything else is in DB

---

## Key Numbers

| Metric | Value |
|--------|-------|
| Airports | ~276 (FR 103, DE 37, UK 78, NL 26, CH 16, AT 9, BE 5) |
| Models | 3 (GFS, ICON, ECMWF) |
| Model×airport pairs per cycle | 828 (3 × 276) |
| Cycles per day | 5 (06, 09, 12, 15, 18Z) |
| Open-Meteo calls per cycle | ~9 (276 airports chunked at ~100/call, × 3 models) |
| Open-Meteo calls per day | ~45 (when all models have new init times; fewer on no-change cycles) |
| GRIB downloads per new init | ~150-200 MB (GFS: 35 files + ICON: 25 files, ~2-5MB each, cached) |
| GRIB downloads on no-change cycle | 0 (cache hit) |
| METAR API calls per cycle | 1 (276 < 400 batch limit) |
| Forecast snapshot rows per cycle | GFS: 276×35 + ICON: 276×25 + ECMWF: 276×35 ≈ 26K (when all models updated) |
| Forecast snapshot steady state (10d) | ~600K-1.2M rows (~180 MB) |
| Scores per day | ~25K |
| Scores per month | ~750K |
| Observations per month | ~41K |
| DB growth per month (permanent) | ~200 MB (scores + observations) |
| DB growth per year | ~2.4 GB |

---

## DB Schema

### New table: `airport_forecast_snapshots`

```sql
CREATE TABLE airport_forecast_snapshots (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    icao                   VARCHAR(4)  NOT NULL,
    model                  VARCHAR(20) NOT NULL,
    model_init_time        DATETIME    NOT NULL,
    forecast_hour          DATETIME    NOT NULL,
    fetched_at             DATETIME    NOT NULL,

    -- Surface fields (from Open-Meteo API)
    temperature_2m_c       FLOAT,
    dewpoint_2m_c          FLOAT,
    visibility_m           FLOAT,
    wind_speed_10m_kt      FLOAT,
    wind_direction_10m_deg FLOAT,
    wind_gusts_10m_kt      FLOAT,
    precipitation_mm       FLOAT,
    snowfall_cm            FLOAT,
    cape_jkg               FLOAT,
    weather_code           INTEGER,
    cloud_cover_pct        FLOAT,
    cloud_cover_low_pct    FLOAT,

    -- Ceiling estimates (from GRIB + computed)
    nwp_ceiling_ft         FLOAT,      -- GRIB model ceiling (GFS HGT, ICON CEILING, ECMWF ceil)
    cloud_base_ft          FLOAT,      -- GRIB lowest cloud base (GFS PRES@cloud_bottom, ICON HBAS_CON, ECMWF cbh)
    lcl_ft                 FLOAT,      -- 400*(T-Td) thermodynamic estimate

    UNIQUE(icao, model, model_init_time, forecast_hour)
);

CREATE INDEX ix_afs_icao_hour ON airport_forecast_snapshots(icao, forecast_hour);
CREATE INDEX ix_afs_fetched   ON airport_forecast_snapshots(fetched_at);
```

### Modified: `verification_scores` + `taf_verification_scores`

Add column: `source VARCHAR(16) DEFAULT 'flight'` — values `'flight'` or `'standalone'`.

**Migration note**: This is a **unique constraint change** on both tables, not just a column add:
- `verification_scores`: `(icao, observation_time, model, model_init_time)` → `(icao, observation_time, model, model_init_time, source)`
- `taf_verification_scores`: `(icao, observation_time, taf_issue_time)` → `(icao, observation_time, taf_issue_time, source)`

Use `batch_alter_table` (SQLite needs copy-and-move for constraint changes). Existing rows get `source='flight'` via DEFAULT. Since flight verification data is minimal (just started), we can also drop and recreate both tables cleanly if simpler.

**Include `source` in the unique constraint** — without this, whichever pipeline scores first wins and the flight pipeline's richer score (with full sounding/GRIB data) can be silently lost. Both pipelines can score the same (icao, time, model) independently, enabling direct quality comparison.

Add composite index for dashboard queries: `ix_verif_scores_source_model_days(source, model, days_out)`.

**Dashboard rule**: All queries MUST filter by `source` — never aggregate across `source='flight'` and `source='standalone'` in the same metric. They use the same `days_out` formula but have different data richness (standalone has no sounding).

### Additional columns on `verification_scores`

Add `cloud_base_delta_ft` and `lcl_delta_ft` alongside existing `ceiling_delta_ft` — one score row captures all 3 ceiling estimate comparisons without tripling rows:

```sql
-- Existing: ceiling_delta_ft uses reconcile_ceiling() (NWP ceiling for standalone, min(sounding, NWP) for flight)
cloud_base_delta_ft     INTEGER,    -- GRIB cloud_base vs METAR ceiling
lcl_delta_ft            INTEGER,    -- LCL estimate vs METAR ceiling
```

This enables offline analysis of which ceiling method is most accurate per model/region/season without separate scoring runs.

---

## Unified `days_out` Semantics

Both pipelines compute `days_out` the same way:

```python
days_out = (forecast_hour.date() - model_init_time.date()).days
```

**Flight pipeline change**: Update `_score_model_vs_metar()` to compute `days_out` from `model_init_time` instead of passing `pack_row.days_out`. This is more precise — a D-3 pack might contain forecasts spanning 2 calendar days relative to the model init, and this formula captures that per-hour.

**Migration**: Drop existing flight verification scores (minimal data, just started). The `days_out` values will be recomputed correctly on the next scoring run.

---

## `snapshot_to_hourly()` Adapter Contract

Maps `airport_forecast_snapshots` DB columns → `HourlyForecast` fields:

| Snapshot column | HourlyForecast field | Notes |
|----------------|---------------------|-------|
| `forecast_hour` | `time` | |
| `temperature_2m_c` | `temperature_2m_c` | |
| `dewpoint_2m_c` | `dewpoint_2m_c` | |
| `visibility_m` | `visibility_m` | |
| `wind_speed_10m_kt` | `wind_speed_10m_kt` | |
| `wind_direction_10m_deg` | `wind_direction_10m_deg` | |
| `wind_gusts_10m_kt` | `wind_gusts_10m_kt` | |
| `precipitation_mm` | `precipitation_mm` | |
| `snowfall_cm` | `snowfall_cm` | |
| `cape_jkg` | `cape_jkg` | |
| `weather_code` | `weather_code` | |
| `cloud_cover_pct` | `cloud_cover_pct` | |
| `cloud_cover_low_pct` | `cloud_cover_low_pct` | |
| `nwp_ceiling_ft` | `nwp_cloud_diagnostics.ceiling_ft` | Primary ceiling for `reconcile_ceiling(sounding=None, hourly)` |

Fields NOT populated (left as `None`): `relative_humidity_2m_pct`, `surface_pressure_hpa`, `pressure_msl_hpa`, `rain_mm`, `showers_mm`, `precipitation_probability_pct`, `cloud_cover_mid_pct`, `cloud_cover_high_pct`, `freezing_level_m`, `convective_inhibition_jkg`, `lifted_index_raw`, `pressure_levels`.

**Scoring implications**: `reconcile_ceiling(sounding=None, hourly)` returns `nwp_cloud_diagnostics.ceiling_ft` (no sounding to reconcile against). Convection scoring always returns `False` (no sounding → no `ConvectiveRisk`). All other scored fields (visibility, wind, temperature, precipitation, flight category) work normally.

---

## Model Configuration

```python
STANDALONE_MODELS = ["gfs", "icon", "ecmwf"]
```

Model keys use `MODEL_ENDPOINTS` dict keys (`"gfs"`, `"icon"`, `"ecmwf"`) — not API params like `"icon_seamless"`. Scores must match existing flight verification and dashboard queries.

### Model Forecast Horizons

Each model's forecast horizon is capped to its **GRIB cloud diagnostic coverage** — we only store forecast hours where we can get ceiling data from GRIB. Beyond the GRIB horizon, Open-Meteo surface data exists but ceiling comes from a different/fallback source (e.g., ICON global instead of ICON-EU), breaking consistency.

| Model | GRIB source | GRIB horizon | Forecast days stored | Sample hours per cycle |
|-------|------------|-------------|---------------------|----------------------|
| GFS | NOAA S3 | 384h (16d) | **D-0 to D-7** (7 days) | 35 |
| ICON | DWD (ICON-EU) | 120h (5d) | **D-0 to D-4** (5 days) | 25 |
| ECMWF | SFTP delivery | 240h (10d) | **D-0 to D-7** (7 days) | 35 |

ICON is limited to 5 days because the GRIB pipeline uses ICON-EU (higher resolution over Europe). Open-Meteo silently falls back to ICON global beyond 120h, but our GRIB ceiling data wouldn't match. Rather than scoring with inconsistent data or `NULL` ceilings, we simply don't store ICON forecasts beyond D-4.

**GRIB download volume per new model init**: GFS 35 files + ICON 25 files = ~60 cloud diagnostic files (~2-5MB each, ~150-200MB total). Cached — only downloaded when a new `model_init_time` appears (2-3x/day). No-change cycles download 0 GRIB files.

### GRIB Data Sources

| Model | Ceiling source | Cloud base source | Visibility source |
|-------|---------------|-------------------|-------------------|
| GFS | GRIB `HGT@cloud_ceiling` | GRIB `PRES@cloud_bottom` → altitude | Open-Meteo API |
| ICON | GRIB `CEILING` | GRIB `HBAS_CON` | Open-Meteo API |
| ECMWF | ECMWF GRIB `ceil` | ECMWF GRIB `cbh` | ECMWF GRIB `vis` |

GFS and ICON use the existing GRIB enrichment pipeline (shared cache at `{data_dir}/.cache/grib/`).
ECMWF uses the commercial GRIB delivery via SFTP (files at `/mnt/flyfun_data/ecmwf/incoming/`).

### GRIB / Open-Meteo Consistency

The data flow is: weather center publishes GRIB → S3/DWD hosts raw files → Open-Meteo downloads, processes, serves via API → `meta.json` updates with new `model_init_time`.

GRIB files are available **before** Open-Meteo finishes processing them. So when `meta.json` reports GFS init=00Z, the 06Z GRIB may already be on S3 — but we **must not use it**, or surface data (Open-Meteo, from 00Z run) and ceiling data (GRIB, from 06Z run) would be from different model runs.

**Rule: `meta.json` is the source of truth for `model_init_time`. Fetch GRIB for exactly that init time.** Since Open-Meteo can only serve a run after the GRIB exists upstream, the matching GRIB is guaranteed to be available — no 404 risk, no mismatch.

```python
# Phase A: resolve init times FIRST, then use them for both API and GRIB
model_metadata = fetch_model_metadata(["gfs", "icon", "ecmwf"])

for model, meta in model_metadata.items():
    init_time = meta.last_init_time  # from meta.json
    if already_have_snapshots(model, init_time):
        continue
    surface_data = client.fetch_multi_point(airports, model, ...)  # uses this init
    grib_ceiling = fetch_grib_cloud_diag(model, init_time, ...)   # same init — consistent
```

**Note — same applies to flight briefing pipeline**: The existing `enrich_forecasts()` GRIB enrichment should also pin GRIB downloads to the `model_init_time` that Open-Meteo reported for the pack, not just grab whatever's latest on S3. Today this is a latent bug — if a GRIB run is newer than what Open-Meteo served, surface and ceiling data silently come from different runs. Low priority (single briefing, small window for mismatch) but should be fixed when touching that code.

---

## Open-Meteo Batching

`fetch_multi_point()` sends all coordinates in a single HTTP request as comma-separated lat/lon values. At 276 airports, the URL length and response size could hit undocumented limits.

**Strategy**: Chunk airports into groups of **~100 per API call** (conservative). 276 airports ÷ 100 = 3 calls per model, 9 calls per cycle.

```python
_OPEN_METEO_BATCH_SIZE = 100  # airports per API call

for chunk in batched(airports, _OPEN_METEO_BATCH_SIZE):
    result = client.fetch_multi_point(chunk, model, start_date=..., end_date=...)
    # merge results
```

If a chunk call fails (429 rate limit), the existing retry logic (backoff [10s, 30s, 60s, 90s]) handles it. If all retries fail, skip that model for this cycle.

---

## Airport Watchlist

### Discovery

CLI command queries aviationweather.gov for airports that currently report METARs:

```bash
python -m weatherbrief.verify discover --prefixes LF,ED,EG,EH,EB,LS,LO
```

Outputs `configs/airport_watchlist.json`:
```json
{
  "generated_at": "2026-04-02T10:00:00Z",
  "airports": {
    "LF": ["LFPG", "LFPO", "LFBO", ...],
    "ED": ["EDDF", "EDDM", "EDDB", ...],
    ...
  }
}
```

### Maintenance

- **Manual refresh**: Re-run `discover` periodically (monthly or quarterly) to pick up new airports or drop closed ones
- **Runtime skip**: During METAR fetch, airports that return no data are silently skipped — no wasted scoring. Over time, consistently-empty airports could be flagged for removal from the watchlist
- **No automatic removal**: Watchlist changes require explicit CLI run + commit — no silent drift

---

## Reusable Code

| Function | Location | How reused |
|----------|----------|------------|
| `fetch_observations_batch()` | `tasks/verification.py` | Direct — pass watchlist ICAOs (takes flat ICAO list, no route context needed) |
| `store_observations()` | `tasks/verification.py` | Direct — pass `icao_to_flights={}` (handles empty mapping) |
| `OpenMeteoClient.fetch_multi_point()` | `fetch/open_meteo.py` | Airports as RoutePoints (set `distance_from_origin_nm=0.0`), chunked at ~100/call |
| `fetch_model_metadata()` | `fetch/model_status.py` | Call at cycle start for per-model `model_init_time` resolution |
| GRIB decode per point | `fetch/grib/decode.py` | `decode_cloud_diag_per_point()` at airport lat/lons from cached GRIB |
| `classify_flight_category()` | `analysis/airport_conditions.py` | Pure function |
| `compute_wind_advisory()` | `tasks/route_weather.py` | With runway data per airport |
| `get_runway_ends()` | `tasks/scoring.py` | Batch-load from airports DB |
| `_score_model_vs_metar()` | `tasks/scoring.py` | Via `snapshot_to_hourly()` adapter |
| `_score_taf_vs_metar()` | `tasks/scoring.py` | Direct — needs only obs_row + obs_weather + runway_ends (no route context) |

### New: `snapshot_to_hourly()` adapter

Constructs a minimal `HourlyForecast` from snapshot DB columns (~20 lines). See **Adapter Contract** section above for the exact field mapping. All `HourlyForecast` fields are `Optional`, so partial construction works.

This preserves the "same scoring functions as advisory pipeline" principle.

---

## Known Limitations

- **Convection scoring**: model-side always `False` (requires full sounding analysis with `ConvectiveRisk`). METAR-side ("TS" in weather phenomena) still works, so thunderstorm miss rate is trackable one-directionally.
- **ECMWF scores**: no data until GRIB SFTP delivery is active and stable.
- **No sounding-derived ceiling**: standalone uses NWP ceiling (GRIB) only. `reconcile_ceiling(sounding=None, hourly)` returns `nwp_ceiling_ft`. Flight verification still uses `min(sounding, NWP)` when sounding is available.
- **Ceiling comparison**: all 3 estimates are stored in snapshots. `ceiling_delta_ft` uses `nwp_ceiling_ft` (via `reconcile_ceiling`). `cloud_base_delta_ft` and `lcl_delta_ft` are scored as additional columns on the same score row — no tripled rows. Over time, this reveals which method is most accurate per model/region/season.

---

## Implementation Steps

### Step 1: Migration

- New `airport_forecast_snapshots` table
- Add `source` column to `verification_scores` and `taf_verification_scores`
- **Drop + recreate unique constraint** to include `source` (existing flight data is minimal — clean rebuild is simpler than constraint migration)
- Add `cloud_base_delta_ft` and `lcl_delta_ft` columns to `verification_scores`
- Add composite index `ix_verif_scores_source_model_days(source, model, days_out)`
- `batch_alter_table` for SQLite compatibility
- Update flight pipeline `_score_model_vs_metar()` to compute `days_out` from `model_init_time` instead of `pack_row.days_out`

### Step 2: Airport watchlist

- `configs/airport_watchlist.json` — ICAO list by region
- CLI `discover` command: query aviationweather.gov per prefix, save reporting airports
- Skip airports that return no METAR data at runtime

### Step 3: GRIB adapter

- Thin wrapper around existing GRIB decode to extract ceiling/cloud_base at airport coordinates (~50-100 lines)
- Reuses shared GRIB cache (`{data_dir}/.cache/grib/`)
- **Scale test**: Verify `decode_cloud_diag_per_point()` memory with 276 points before production

### Step 4: Core pipeline

- `src/weatherbrief/tasks/standalone_verification.py`
- Phases A-E as described in the walkthrough
- Phase A: check `model_init_time` via `fetch_model_metadata()`, skip fetching for models with no new init time
- Phase A: chunk Open-Meteo calls at ~100 airports per request
- Phase A: GRIB enrichment for ceiling/cloud_base
- Phase D: `snapshot_to_hourly()` adapter for NWP scoring + `_score_taf_vs_metar()` for TAF scoring
- Phase D: populate `cloud_base_delta_ft` and `lcl_delta_ft` alongside `ceiling_delta_ft`

### Step 5: Scheduler integration

- Run at the 5 sample hours (not every 10 min) — check current UTC hour, run when within 15-min window of a sample hour
- Separate asyncio task from flight verification loop
- Disableable via `DISABLE_STANDALONE_VERIFICATION=1` or config table toggle

### Step 6: ECMWF GRIB adapter

- Read from SFTP delivery path (`/mnt/flyfun_data/ecmwf/incoming/`)
- Extract vis, ceil, cbh for airport locations
- Built from day one but no-op until files appear

### Step 7: Retention integration

- Add forecast snapshot pruning to existing retention loop: `DELETE WHERE fetched_at < now - 10 days`
- Add monthly rollup generation (see DB Growth Plan)
- After 12 months: consider purging raw scores older than retention window (keep rollups forever)

### Step 8: CLI commands

```bash
python -m weatherbrief.verify discover --prefixes LF,ED,EG,EH,EB,LS,LO
python -m weatherbrief.verify standalone --once
python -m weatherbrief.verify stats --source standalone
```

### Step 9: Update digest + dashboard

- Add `source` filter to verification_stats.py queries — **all queries must include `WHERE source = ...`**
- Add source toggle to dashboard UI (Flight / Standalone — no "All" to prevent mixing semantics)
- Add standalone pipeline health check: last run time + score count in verification digest. Alert if 0 scores for a day.

### Step 10: Config table

- `standalone_verification_config` table: `sample_hours` (JSON list), `models` (JSON list), `enabled` (bool), `batch_size` (int)
- Editable from admin hub — no redeployment needed for tuning

---

## DB Growth Plan

- Snapshot table (10-day retention): ~600K-1.2M rows steady state — manageable
- Score tables: ~750K rows/month, ~9M/year — managed via rollups:

### Monthly rollup: `verification_monthly_stats`

Pre-aggregated accuracy metrics, generated monthly by the retention loop:

```sql
CREATE TABLE verification_monthly_stats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    month       DATE NOT NULL,           -- first day of month
    source      VARCHAR(16) NOT NULL,    -- 'flight' or 'standalone'
    model       VARCHAR(20) NOT NULL,
    days_out    INTEGER NOT NULL,
    icao_prefix VARCHAR(2) NOT NULL,     -- region (LF, ED, EG, ...)

    -- Aggregates
    n_scores                INTEGER,
    category_accuracy_pct   FLOAT,      -- AVG(category_match) * 100
    ceiling_mae_ft          FLOAT,      -- AVG(ABS(ceiling_delta_ft))
    ceiling_bias_ft         FLOAT,      -- AVG(ceiling_delta_ft)
    cloud_base_mae_ft       FLOAT,      -- AVG(ABS(cloud_base_delta_ft))
    lcl_mae_ft              FLOAT,      -- AVG(ABS(lcl_delta_ft))
    visibility_mae_m        FLOAT,
    wind_speed_mae_kt       FLOAT,
    temperature_mae_c       FLOAT,
    advisory_accuracy_pct   FLOAT,
    precip_hit_rate_pct     FLOAT,

    UNIQUE(month, source, model, days_out, icao_prefix)
);
```

**Retention policy**:
- Raw scores: keep 12 months, then purge (rollups retain the aggregate signal)
- Rollup rows: keep forever (~100 rows/month per source — negligible)
- Observations: keep forever (ground truth, small volume)

---

## Monitoring (First Week)

During initial deployment, verify:

1. **Pipeline runs at all 5 sample hours** — reuse `VerificationCycleRow` with a `source` column to track standalone cycles alongside flight cycles
2. **Model init times vary across the day** — confirm at least 2 distinct `model_init_time` values per model per day in `airport_forecast_snapshots`
3. **D-0 sanity check**: GFS ceiling MAE < 500ft at D-0 — if wildly off, adapter has a bug
4. **METAR coverage**: Confirm ~200+ airports return data per cycle (some may not have METARs at every sample hour)
5. **Score counts**: Expect ~15-30 scores per airport per day initially (D-0 only), growing to full D-0..D-7 by day 8
6. **Digest includes standalone stats**: Verification digest reports last standalone run time and score count

---

## Future Extensions

- **Sounding analysis**: Add pressure-level fetching for selected airports (major hubs) to enable convection scoring
- **Regional heatmaps**: Per-airport accuracy rendered on a map
- **Model recommendation**: Auto-suggest best model per region/season based on accumulated data
- **Expand coverage**: Add more countries (Scandinavia, Iberia, Italy, Czech Republic)
- **Full GRIB soundings**: When ICON full GRIB plan is complete, use 40-level sounding data for selected airports to enable convection and icing verification
