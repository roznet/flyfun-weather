# US Watchlist Expansion

> Add CONUS airports to the standalone verification pipeline as a second region alongside the existing European watchlist. Goal: attract US users without compromising the EU pipeline.

## Context

The standalone verification pipeline currently monitors ~619 European airports across `LF/ED/EG/EH/EB/LS/...` ICAO prefixes (see `configs/airport_watchlist.json`). With the EU heavy-cycle parallelisation landed (issue #110), spare droplet capacity is available — duty cycle drops to ~3%. Expanding to US adds users without infrastructure cost, and the ECMWF GRIB order already includes CONUS (`project_ecmwf_order.md`: "Coverage: Europe AND US ... grid coverage is global for the order"), so the ECMWF leg is purely compute.

## Key Decisions

- **2 models for US**: GFS (Open-Meteo) + ECMWF (GRIB-direct). No ICON (DWD model is European-domain only). No NBM/HRRR initially — see "Models considered and rejected" below.
- **Airport set**: TAF-issuing US airports only (~720 CONUS + ~60 AK/HI/PR ≈ ~780). This keeps cycle volume comparable to EU (619 × 3 models = 1857 model-airport calls; US 780 × 2 = 1560 — actually slightly less). METAR-only stations not included in v1.
- **Schedule offset by 6h from EU**: heavy fetch at 13Z and 01Z. Aligns with US pilot use windows (8–9 AM ET / 8–9 PM ET) and zero overlap with EU 07Z/19Z heavy cycles or EU 06/09/12/15/18Z light cycles.
  - **Constraint — the 01Z cycle depends on GFS staying outside the warming window (#475).** The airport-profile precache is gated to a 03Z–21Z wall-clock window per model (`MODEL_WARMING_WINDOW_UTC` in `fetch/grib/precache.py`); `icon-eu` and `icon-d2` are gated, **GFS deliberately is not**. The 13Z US cycle sits inside the window either way, but **01Z sits outside it** — it works only because US is GFS + ECMWF (GFS ungated, ECMWF push-delivered with no precache at all). Gating GFS "for consistency" would silently leave the 01Z US cycle on a cold cache. The reasoning currently lives in a comment in `precache.py`; it is restated here because the two files are easy to change independently.
- **Region tagging**: add a `region` column to `airport_forecast_snapshots` (`eu` / `us`) so cache rebuilds can split by region. Alternative — derive region from ICAO prefix at query time — works but loses an index opportunity.
- **Watchlist file structure**: extend `configs/airport_watchlist.json` with a region-keyed top-level: `{"airports_eu": {...}, "airports_us": {...}}`. Or split into two files. Either is fine; pick what makes the discover/refresh tooling simpler.
- **Cache split**: `forecast_map` cache entries keyed per-region (`forecast_map:eu:{day}:{hour}`, `forecast_map:us:{day}:{hour}`). Doubles entry count from 20 → 40 but keeps each blob under 1.5 MB. Verification map and stats caches similar.
- **Forecast horizon**: keep at 4 days for US (same as EU). Long-range fixed-airport verification is too noisy to be useful; per-flight briefing is the right tool for D+5+.
- **LLM digest cost**: scales linearly with airports — US adds ~125% to current digest spend. Real cost driver, watch closely. Consider gating US digest behind a feature flag at first.

## Models considered and rejected

| Model | Why rejected |
|---|---|
| **NBM CONUS** (`ncep_nbm_conus`) | Probed 2026-05-04 at KJFK. Despite 11-day horizon and 2.5 km resolution, only populates 8/17 of our standard surface variables. Critical missing: `dewpoint_2m`, `cloud_cover_low/mid/high`, `pressure_msl`, `freezing_level_height`, `lifted_index`, `convective_inhibition`. The cloud-layer breakdown gap is the blocker — ceiling derivation needs low/mid/high split, and NBM only outputs total. Visibility partial (drops out ~D-3.8). |
| **HRRR CONUS** (`ncep_hrrr_conus`) | 18-hour horizon (48h on 00/06/12/18Z runs) — only contributes to D-0 verification bucket, leaves D-1..D-4 empty. Useful as a future "+1" model for D-0 high-res accuracy boost, not a primary model. **If it ever lands GRIB-direct** (as #457 proposes for the *briefing* pipeline, not this one) it inherits obligations added after this doc was first written — see "What a new GRIB-direct model owes the cache" below. |
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

### The GRIB disk budget does NOT constrain US expansion

Worth stating explicitly, because reading `weather-engine-specs.md` alongside this doc invites the opposite conclusion. The GRIB byte-range cache is under active pressure — ICON-D2 warming is sized at ~62 GB/day with a ~42 GB peak retained and a 45 GiB cap (#475/#478). **None of that applies here.** The two US models never touch that cache:

- **GFS** for the standalone pipeline comes from **Open-Meteo** (JSON), not GRIB.
- **ECMWF** is **push-delivered** to `ECMWF_GRIB_DIR` and read from local disk — no cache, no TTL, no cap.

So US expansion's storage cost is entirely in MySQL (the table above), not in `.cache/grib/`. The per-model TTLs, size caps and completeness gates are ICON-specific concerns.

### What a new GRIB-direct model owes the cache

Only relevant if a future model is added **GRIB-direct** rather than via Open-Meteo — HRRR (#457) being the live candidate. These obligations post-date this doc's first draft (#475/#478) and are easy to miss:

- a `MODEL_TTL_SECONDS` entry in `fetch/grib/cache.py` (no entry → the 12 h `CACHE_TTL_SECONDS` fallback)
- a `_DEFAULT_CACHE_CAP_GIB` entry, or a deliberate decision to leave it uncapped
- a `MODEL_WARMING_WINDOW_UTC` entry — **for a CONUS model the right answer is ungated**, same reasoning as GFS above: its users are awake during the EU night
- a per-level vs whole-column cache-layout decision (`IconVariant.per_level_cache` is the ICON precedent — per-level keeps a partial download *detectable*, a whole-column blob does not)
- it must satisfy the decode completeness gate: every required variable present, and for a per-level layout every requested level present, or the forecast hour is skipped. **Verify against the real feed first** — if the provider does not publish every variable at every level, a strict gate skips every hour and the model silently produces nothing. `scripts/e2e_icon_d2_cache.py` is the pattern for that check.

## Cache rebuild scaling

`tasks/cache_builder.py` rebuilds all three cache categories (stats / bias_leaderboard / forecast_map) after each cycle. Currently ~33 entries (6 stats + 27 leaderboard + N forecast_map per available day/hour). With per-region split for forecast_map and bias_leaderboard: ~50 entries. Each entry runs an aggregation query against the rollup / snapshots. Watch the `Cache rebuild: ... (%dms)` log line — should stay well under 30s (post-#154 typical is ~2s on the current dataset).

## Region serving & selection (forecast map)

How the map picks a region and serves it. Detail beyond the one-line "cache split" /
"region query param" decisions above, since this is the user-visible layer.

### Efficiency: partition at build time, don't filter at query time

The map serves from a prebuilt cache blob, not a live query, in the common case, so
"efficient based on where the user is" is achieved by **pre-splitting the cache per region**,
not by geographic filtering:

- **Serving path is O(1):** `GET /maps/forecast?region=us&day&hour` →
  single-row `verification_cache[forecast_map:us:{day}:{hour}]` lookup. No scan, no bbox
  math, no coordinate join. Each region blob is that region's airports only (~780 US),
  <1.5 MB.
- **A region index only matters off the hot path** — the live-fallback query and the
  cache-rebuild query in `get_forecast_map_data` (`tasks/map_queries.py:322-361`), which
  become `WHERE region=:r AND (model, model_init_time, forecast_hour) …`. Prepend `region`
  to the existing `ix_afs_hour_model` → **`(region, forecast_hour, model)`** to cover both
  without a table scan (this is the "index opportunity" noted under Key Decisions).
- **No lat/lon on snapshots, no spatial index needed.** True bounding-box filtering (a
  global map zoomed to an area) would require both; per-region partitioning makes it
  unnecessary. Pre-split, don't filter.

### Region selection: default + override

Resolve the default map region as a chain (first hit wins), with an explicit override that
is always available and shareable:

1. **Explicit EU ⇄ US toggle** on the map — add `fc.region` to the `mapsUrlState` schema
   (`web/ts/maps-main.ts`) so it deep-links (share-link round-trip stays lossless) and
   persist it as the user's last choice.
2. **Last / frequent-flight region** — reuse `compute_frequent_airports`
   (`api/flights.py`, #419: top-5 dep/dest from history) and take the dominant region from
   those ICAOs. Captures "usually flies EU, on a US trip this week" better than a static
   preference.
3. **`units_region` preference** (`europe|us`) — already persisted and already read by
   `maps-main.ts` for unit display; reuse it, don't invent a new region inference.
4. **EU default** for brand-new users with no signal.

Rationale: the toggle is the escape hatch, so the chain only needs to set a *smart default*
— no single detection heuristic has to be perfect.

### Serving-layer touch points & gotchas

- **US is 2-model (GFS + ECMWF, no ICON)** — the consensus/agreement UI must render a
  2-model consensus gracefully. The per-day variable-model-availability machinery in
  `tasks/forecast_grid.py` (the D+5/D+6 two-model logic) is the same shape of problem and
  can be leaned on.
- **`GET /maps/airport-weather` rejects non-European ICAO prefixes** today
  (`api/maps.py:198,214-216` via `DEFAULT_PREFIXES`) — this gate must become region-aware
  or US airport clicks break.
- **Cache-key version** — the region segment re-segments the keys, but bump
  `FORECAST_MAP_CACHE_VERSION` so old `forecast_map:v2:{day}:{hour}` entries never match the
  new `forecast_map:{region}:{day}:{hour}` shape and the endpoint falls through to the live
  path instead of serving a stale shape.
- **Visibility is already region-aware** (SM vs km) in `web/ts/data/map-metrics-catalog.json`
  — no change needed there.
- **iOS** consumes the same `/maps/forecast` payload; the open "which region does the map
  summarise" question in `designs/future/ios-forecast-map.md` is answered by the same
  `region` param + the selection chain above.

### Where the region cycle runs (compute location)

Region-tagged cycles do not have to run on the serving box. The generic, provider-agnostic
capabilities — **disable the scheduled cycle**, **emit a snapshot artifact**, **ingest a
snapshot artifact** — let a region's compute run off-box and ship results in; the `region`
column is exactly what the ingest artifact carries, and the cache rebuild then emits that
region's `forecast_map:{region}:*` blobs. This removes the "spare droplet capacity" framing
in Context above: US compute need not consume serving-box capacity at all. The specific
deployment topology (where off-box compute runs, transport, scheduling) is intentionally
kept out of this repo — see the private compute-offload working notes.

## Implementation order

Status re-checked against code 2026-07-22 — the DB seam landed with the
compute-offload work (the snapshot artifact carries `region`), so this list is no
longer "all unbuilt".

1. **Pre-req: DONE.** Standalone-verification parallelisation (issue #110 — chunk-level parallelism inside `_fetch_forecasts_for_model`) is shipped: `ThreadPoolExecutor` over airport chunks in `standalone_verification.py` (~line 438). Distinct from issue #112, which parallelises the per-model loop in the *briefing* pipeline; also shipped, unrelated to standalone capacity headroom.
2. **DONE.** ~~Add `region` column to `AirportForecastSnapshotRow`~~ — shipped:
   `String(2)`, `default="eu"`, `server_default="eu"`, and threaded through
   `standalone_verification._store_snapshots(..., region="eu")`. Still to do: the
   region key in the **watchlist JSON** (`configs/airport_watchlist.json` is still
   a flat `{prefix: [icao...]}` map of 619 EU airports).
3. Extend `STANDALONE_MODELS` to be region-aware: `{"eu": ["gfs", "icon", "ecmwf"], "us": ["gfs", "ecmwf"]}` (still the flat `["gfs", "icon", "ecmwf"]`)
4. Add 13Z and 01Z to `FORECAST_FETCH_HOURS_UTC` for the US region (or split the loop) — see the 01Z/warming-window constraint under Key Decisions
5. Update cache builder to emit per-region keys; update API endpoints to accept `region`
   query param. **The index is DONE** — `ix_afs_region_hour_model` on
   `(region, forecast_hour, model)` already exists alongside `ix_afs_hour_model`,
   which is dropped in a follow-up once serving is region-aware. `map_queries.py`
   and `cache_builder.py` still have no `region` awareness.
6. Serving/client (see "Region serving & selection"): `fc.region` in `mapsUrlState` + EU⇄US
   toggle + selection chain; make `/maps/airport-weather` region-aware; 2-model consensus
   rendering for US; bump `FORECAST_MAP_CACHE_VERSION`
7. Discover and seed US TAF watchlist via `python -m weatherbrief.verify discover --region us` (extend the discover command)
8. Validate: 2 days of clean US 13Z/01Z cycles in `verification_cycles`, no EU regression

## Out of scope

- NBM, HRRR, GEM additions
- Forecast horizon extension (4 days stays)
- Pre-rendering Skew-T or cross-section tiles for the watchlist
- US text-forecast integration (NWS AFD) — already exists for flight pipeline; revisit if standalone needs it
- Multi-language digest variants for US users
