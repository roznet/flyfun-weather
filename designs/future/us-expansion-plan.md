# US Watchlist Expansion

> Add CONUS airports to the standalone verification pipeline as a second region alongside the existing European watchlist. Goal: attract US users without compromising the EU pipeline.

## Context

The standalone verification pipeline currently monitors ~619 European airports across `LF/ED/EG/EH/EB/LS/...` ICAO prefixes (see `configs/airport_watchlist.json`). With the EU heavy-cycle parallelisation landed (issue #110), spare droplet capacity is available — duty cycle drops to ~3%. Expanding to US adds users without infrastructure cost, and the ECMWF GRIB order already includes CONUS (`project_ecmwf_order.md`: "Coverage: Europe AND US ... grid coverage is global for the order"), so the ECMWF leg is purely compute.

## Key Decisions

- **2 models for US**: GFS (Open-Meteo) + ECMWF (GRIB-direct). No ICON (DWD model is European-domain only). No NBM/HRRR initially — see "Models considered and rejected" below.
- **Airport set**: TAF-issuing US airports only (~720 CONUS + ~60 AK/HI/PR ≈ ~780). This keeps cycle volume comparable to EU (619 × 3 models = 1857 model-airport calls; US 780 × 2 = 1560 — actually slightly less). METAR-only stations not included in v1.
- **Schedule offset by 6h from EU**: heavy fetch at 13Z and 01Z. Aligns with US pilot use windows (8–9 AM ET / 8–9 PM ET) and zero overlap with EU 07Z/19Z heavy cycles or EU 06/09/12/15/18Z light cycles.
- **Region tagging**: add a `region` column to `airport_forecast_snapshots` (`eu` / `us`) so cache rebuilds can split by region. Alternative — derive region from ICAO prefix at query time — works but loses an index opportunity.
- **Watchlist file structure**: extend `configs/airport_watchlist.json` with a region-keyed top-level: `{"airports_eu": {...}, "airports_us": {...}}`. Or split into two files. Either is fine; pick what makes the discover/refresh tooling simpler.
- **Cache split**: `forecast_map` cache entries keyed per-region (`forecast_map:eu:{day}:{hour}`, `forecast_map:us:{day}:{hour}`). Doubles entry count from 20 → 40 but keeps each blob under 1.5 MB. Verification map and stats caches similar.
- **Forecast horizon**: keep at 4 days for US (same as EU). Long-range fixed-airport verification is too noisy to be useful; per-flight briefing is the right tool for D+5+.
- **LLM digest cost**: scales linearly with airports — US adds ~125% to current digest spend. Real cost driver, watch closely. Consider gating US digest behind a feature flag at first.

## Models considered and rejected

| Model | Why rejected |
|---|---|
| **NBM CONUS** (`ncep_nbm_conus`) | Probed 2026-05-04 at KJFK. Despite 11-day horizon and 2.5 km resolution, only populates 8/17 of our standard surface variables. Critical missing: `dewpoint_2m`, `cloud_cover_low/mid/high`, `pressure_msl`, `freezing_level_height`, `lifted_index`, `convective_inhibition`. The cloud-layer breakdown gap is the blocker — ceiling derivation needs low/mid/high split, and NBM only outputs total. Visibility partial (drops out ~D-3.8). |
| **HRRR CONUS** (`ncep_hrrr_conus`) | 18-hour horizon (48h on 00/06/12/18Z runs) — only contributes to D-0 verification bucket, leaves D-1..D-4 empty. Useful as a future "+1" model for D-0 high-res accuracy boost, not a primary model. |
| **GEM (Canada)** | Open-Meteo `region=NORTH_AMERICA`, 10-day horizon. Less optimised for CONUS than US-domestic alternatives, no clear differentiation vs GFS over the US. Skip. |

## Open-Meteo model name convention

US models on Open-Meteo use the `ncep_` prefix:
- `ncep_gfs025` (0.25°), `ncep_gfs013` (0.11°)
- `ncep_hrrr_conus`, `ncep_nam_conus`, `ncep_nbm_conus`
- `ncep_gfs_graphcast025`

Bare names (`gfs`, `hrrr`, `nbm`) are rejected by the API. Add explicit `model_param` entries when registering in `MODEL_ENDPOINTS` (`fetch/variables.py`). No `ncep_` US models are registered there yet.

## Storage growth

Linear in airport count for the snapshot table (10-day retention bounds it). `verification_scores` is unbounded and grows roughly proportional to (airports × models × days_out × time). Estimated steady-state with US added:

| Layer | EU only | EU + US |
|---|---|---|
| Snapshot rows/cycle | ~37k | ~84k |
| Snapshot table | 236 MB | ~540 MB |
| Score table /year | ~10 GB | ~17 GB |

Score table growth is the storage concern. Activate the existing `verification_monthly_stats` rollup (table schema already in place, currently empty in prod) and prune raw scores after rollup at ~12 months. Defer until score table exceeds 5 GB.

## Cache rebuild scaling

`tasks/cache_builder.py` rebuilds all three cache categories (stats / bias_leaderboard / forecast_map) after each cycle. Currently ~33 entries (6 stats + 27 leaderboard + N forecast_map per available day/hour). With per-region split for forecast_map and bias_leaderboard: ~50 entries. Each entry runs an aggregation query against the rollup / snapshots. Watch the `Cache rebuild: ... (%dms)` log line — should stay well under 30s (post-#154 typical is ~2s on the current dataset).

## Implementation order

1. **Pre-req: DONE.** Standalone-verification parallelisation (issue #110 — chunk-level parallelism inside `_fetch_forecasts_for_model`) is shipped: `ThreadPoolExecutor` over airport chunks in `standalone_verification.py` (~line 438). Distinct from issue #112, which parallelises the per-model loop in the *briefing* pipeline; also shipped, unrelated to standalone capacity headroom. The remaining steps below are all unbuilt as of 2026-06-20.
2. Add `region` column to watchlist JSON and `AirportForecastSnapshotRow` (alembic migration)
3. Extend `STANDALONE_MODELS` to be region-aware: `{"eu": ["gfs", "icon", "ecmwf"], "us": ["gfs", "ecmwf"]}`
4. Add 13Z and 01Z to `FORECAST_FETCH_HOURS_UTC` for the US region (or split the loop)
5. Update cache builder to emit per-region keys; update API endpoints to accept `region` query param
6. Discover and seed US TAF watchlist via `python -m weatherbrief.verify discover --region us` (extend the discover command)
7. Validate: 2 days of clean US 13Z/01Z cycles in `verification_cycles`, no EU regression

## Out of scope

- NBM, HRRR, GEM additions
- Forecast horizon extension (4 days stays)
- Pre-rendering Skew-T or cross-section tiles for the watchlist
- US text-forecast integration (NWS AFD) — already exists for flight pipeline; revisit if standalone needs it
- Multi-language digest variants for US users
