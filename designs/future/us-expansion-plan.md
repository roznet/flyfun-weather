# US Watchlist Expansion

> Add CONUS airports to the standalone verification pipeline as a second region alongside the existing European watchlist. Goal: attract US users without compromising the EU pipeline.

**Revised 2026-07-29** with measured delivery timings. The schedule decision changed —
**four cycles at ~02:15 / 08:15 / 14:15 / 20:15Z**, not the 13Z/01Z pair this doc originally
specified — and three other premises turned out to be wrong: ECMWF is delivered on all four
cycles (not just 00/12Z), `ncep_gfs025` has no surface variables, and HRRR's variable coverage is
complete (it was rejected on horizon alone). See **Cycle timing** for the evidence and
**Two things that will fail silently** for what must be fixed before any US cycle hour is added.

## Context

The standalone verification pipeline currently monitors ~619 European airports across `LF/ED/EG/EH/EB/LS/...` ICAO prefixes (see `configs/airport_watchlist.json`). With the EU heavy-cycle parallelisation landed (issue #110), spare droplet capacity is available — duty cycle drops to ~3%. Expanding to US adds users without infrastructure cost, and the ECMWF GRIB order already includes CONUS (`project_ecmwf_order.md`: "Coverage: Europe AND US ... grid coverage is global for the order"), so the ECMWF leg is purely compute.

## Key Decisions

- **2 models for US**: GFS (Open-Meteo) + ECMWF (GRIB-direct). No ICON (DWD model is European-domain only). No NBM/HRRR initially — see "Models considered and rejected" below.
- **Airport set**: TAF-issuing US airports only. Measured 2026-07-29 from aviationweather.gov (tiled bbox sweep, to defeat the API's per-request result cap): **605 TAF-issuing CONUS `K` stations** (662 including bordering non-`K`), so ~665 with AK/HI/PR. Slightly smaller than the ~780 first estimated here. Cycle volume stays comparable to EU (619 × 3 models = 1857 model-airport calls; US ~665 × 2 = ~1330). METAR-only stations not included in v1.
  - **`discover` will overshoot 3× if pointed at `K` prefixes as-is.** The same sweep found **1,853 METAR-reporting** `K` stations, and `airport_watchlist.discover_metar_airports` filters on *METAR* presence, not TAF. The US path needs a TAF-presence filter (`/api/data/taf`) or it seeds ~1,850 airports.
- **Schedule: four cycles at ~02:15 / 08:15 / 14:15 / 20:15Z.** Phase in as 14:15 + 02:15 first, then add 08:15 + 20:15. This supersedes the original "offset 6h from EU → 13Z and 01Z", which was written before the delivery times below were measured and is wrong in two ways — see "Cycle timing" for the evidence.
  - **13:15/01:15 is too early.** GFS 06Z was measured publishing at **13:47Z** (+7h47m). A 13:15 cycle misses it and silently runs on the 00Z init, 13¼ h old. Same for 01:15 vs the 18Z run.
  - **Two cycles can't serve both coasts**, and worse, a 2-cycle US schedule at 14:15/02:15 puts the *entire* US region on the 06/18Z short cut-off ECMWF runs while EU sits entirely on the full 00/12Z runs — a systematic confound in any cross-region model comparison.
  - **Constraint — the 02:15 cycle depends on GFS staying outside the warming window (#475).** The airport-profile precache is gated to a 03Z–21Z wall-clock window per model (`MODEL_WARMING_WINDOW_UTC` in `fetch/grib/precache.py`); `icon-eu` and `icon-d2` are gated, **GFS deliberately is not**. The 14:15 and 20:15 US cycles sit inside the window either way, but **02:15 sits outside it** — it works only because US is GFS + ECMWF (GFS ungated, ECMWF push-delivered with no precache at all). Gating GFS "for consistency" would silently leave the 02:15 US cycle on a cold cache. The reasoning currently lives in a comment in `precache.py`; it is restated here because the two files are easy to change independently.
- **Region tagging**: add a `region` column to `airport_forecast_snapshots` (`eu` / `us`) so cache rebuilds can split by region. Alternative — derive region from ICAO prefix at query time — works but loses an index opportunity.
- **Watchlist file structure**: extend `configs/airport_watchlist.json` with a region-keyed top-level: `{"airports_eu": {...}, "airports_us": {...}}`. Or split into two files. Either is fine; pick what makes the discover/refresh tooling simpler.
- **Cache split**: `forecast_map` cache entries keyed per-region (`forecast_map:eu:{day}:{hour}`, `forecast_map:us:{day}:{hour}`). Doubles entry count from 20 → 40 but keeps each blob under 1.5 MB. Verification map and stats caches similar.
- **Forecast horizon**: keep at 4 days for US (same as EU). Long-range fixed-airport verification is too noisy to be useful; per-flight briefing is the right tool for D+5+.
- **LLM digest cost**: scales linearly with airports — US adds ~125% to current digest spend. Real cost driver, watch closely. Consider gating US digest behind a feature flag at first.

## Cycle timing

Everything here was measured on **2026-07-29**, not taken from provider docs (Open-Meteo's
model table documents update *frequency* but not delivery lag, and the lag is the thing that
picks the cycle hour).

### When the data actually lands

| Feed | Init hours | Lands at | How measured |
|---|---|---|---|
| **ECMWF** direct GRIB (ECPDS push) | **00 / 06 / 12 / 18Z — all four** | init **+6.5 h**, punctual | droplet `.ready_YYYYMMDD_HHz` sentinel mtimes, 27–29 Jul: 06:36 / 12:28 / 18:34 / 00:28Z |
| **GFS** via Open-Meteo `/v1/gfs` | 00 / 06 / 12 / 18Z | init **+6.5 h to +7.8 h — jittery** | `ncep_gfs025/static/meta.json`; 00Z at 06:27Z, but **06Z at 13:47Z (+7h47m)** on a live poll |
| HRRR CONUS | hourly (48 h on 00/06/12/18Z) | +1.8 h | `meta.json` |
| NBM CONUS | hourly | +1.0 h | `meta.json` |

**Correction to this doc's original premise:** it assumed ECMWF was usable only at 00/12Z, which
made a 6 h-offset US cycle look like it would pair a fresh GFS with a 13 h-old ECMWF. Not so —
all four cycles are delivered (06/18Z to 144 h rather than 168 h). The offset idea is better
founded than first written; the problem is elsewhere.

### The binding constraint is GFS jitter, not ECMWF

ECMWF lands within a few minutes of +6.5 h on every cycle. Open-Meteo's GFS does not, and the
variance is per-cycle rather than random: on 2026-07-29 the 00Z run was available at +6h27m while
the **06Z run took +7h47m**.

This already costs us in production. Across 30 days, the existing **EU 19:15Z cycle fetched the
06Z GFS instead of the 12Z run on 6 of 9 evenings** — i.e. the EU evening map runs on a ~13 h-old
GFS more often than not, because 19:15 sits on the wrong side of Open-Meteo's 12Z publication.
Two consequences:

- Any US cycle must sit **≥ +8 h after its target init** to be safe. Hence 14:15/02:15, not
  13:15/01:15.
- The EU evening cycle should move **19:15 → ~19:45Z** independently of US work.

A contributing detail: `/v1/gfs` is a *seamless blend* of `ncep_gfs013` (surface) and
`ncep_gfs025` (pressure levels), and it is gated by the slower half. On 2026-07-29 at 11:32Z
`gfs013` already had the 06Z run while `gfs025` was still on 00Z. Our freshness meta URL points at
`gfs025`, which is the correct conservative choice.

### Why four cycles

Two independent arguments, and the second is the stronger one.

**Coverage.** One cycle cannot serve both coasts. Age of the newest map data and of the
underlying model init, worst-case across each 3 h planning window (06–09 / 19–22 local):

| Planning window | 13:15/01:15 (original) | 14:15/02:15 | 4-cycle |
|---|---|---|---|
| US East morning 06–09 EDT | init ≤ 19.0 h | init ≤ 19.0 h | **init ≤ 13.0 h** |
| US East evening 19–22 EDT | init ≤ 19.7 h | init ≤ 20.0 h | **init ≤ 14.0 h** |
| US West morning 06–09 PDT | init ≤ 19.7 h | init ≤ 20.7 h | **init ≤ 14.7 h** |
| US West evening 19–22 PDT | init ≤ 11.0 h | init ≤ 20.7 h | **init ≤ 14.7 h** |

A 2-cycle schedule leaves East-Coast dawn (~11Z) on 17 h-old inits, and the East is where most US
GA traffic is. **Note the trap in the 2-cycle column:** shifting 13:15 → 14:15 for GFS safety
pushes the refresh *inside* the West-coast morning window, so its median map age gets worse
(1.5 h → 10.4 h), not better. The 1 h safety shift is not free at two cycles; it stops mattering
at four.

**Run-quality confound.** ECMWF's 06/18Z cycles are the short cut-off (BC/SCDA) suite — a 4 h
observation cut-off versus the longer cut-off on 00/12Z. A 2-cycle US schedule at 14:15/02:15 uses
*only* those, while EU uses *only* the full 00/12Z runs. The bias leaderboard would then show US
ECMWF looking worse for reasons that have nothing to do with the model. Four cycles mix both tiers,
so init hour becomes a filterable dimension instead of a hidden variable.

### Prefer availability-triggered over fixed-clock

Better than any fixed hour: poll the ECMWF sentinel plus each model's `meta.json` init hour, and
fire when both reach the target init, with a hard deadline as fallback. The compute node does the
EU cycle in **8:53**, so the generous fixed slot the old schedule needed is no longer necessary.
This removes three problems at once:

- GFS publication jitter stops mattering — the cycle waits for the run instead of hoping.
- The **launchd DST bug** (private compute-offload notes: schedules are Europe/London local, so
  they shift 1 h on 2026-10-25 and fall outside the droplet's ingest window) stops applying,
  because "which synoptic run" is DST-invariant where "which UTC hour" is not. This matters more
  with four schedules to keep correct, on two DST calendars that shift on **different dates**
  (EU 25 Oct 2026, US 1 Nov 2026 — there is a week where the usual Europe↔US-East offset is 5 h,
  not 6 h).
- It also removes the need to pad every cycle for the worst observed lag.

### Compute cost is not the constraint; the score table is

ECMWF GRIB decode is **per-file, not per-airport** — of the node's 8:53 EU cycle, ~6 min is
reading/decoding 226 GRIB files largely single-threaded, and that cost barely moves when more
airports are extracted from the same run. So:

- US airports on an **already-decoded** run are nearly free. The 08:15/20:15 pair shares the 00/12Z
  runs with the EU cycle, so co-scheduling those into one node run with a merged airport set makes
  four cycles cost far less than 2× two cycles.
- Four cycles/day is ~40 min of node time. Irrelevant.
- **The real cost is `verification_scores`**: four cycles roughly doubles this doc's US estimate
  (~24 GB/yr for EU+US, vs the ~17 GB/yr in "Storage growth" below, which assumed two). That pulls
  the deferred `verification_monthly_stats` rollup + prune forward.

## Two things that will fail silently

Both are worth stating plainly because neither surfaces as an error.

### 1. The artifact freshness rule rejects any offset cycle

`_expected_cycle_init()` (`scheduler.py`) floors to the **last 12-hourly synoptic boundary**, so a
07Z cycle expects 00Z and a 19Z cycle expects 12Z. A US artifact built at 14:15 carries newest init
06Z; a 14:00 cycle demands `>= 12Z`, so `find_ingestable_artifact` rejects it, the droplet waits out
its 50 min window and computes locally — **every day**.

It needs to become a per-cycle-hour expected-init map rather than a floor. And note the monitoring
gap: the compute node's own deadman pings **green** in this scenario (it computed and pushed
successfully), so the only signal is the ingest-side `/fail` ping, which the compute-offload notes
list as **not yet built**.

### 2. `FINE_SAMPLE_HOURS` is a European grid

`FINE_SAMPLE_HOURS = (6, 9, 12, 15, 18)` UTC (`tasks/forecast_grid.py`) is 02:00–14:00 EDT /
23:00–11:00 PDT. A US map on that grid has **no slot in the local afternoon or evening at all**,
and its first slot is the middle of the night. This is arguably a bigger decision than the cycle
hour.

Use `(12, 15, 18, 21, 00)` for US — 08:00–20:00 EDT / 05:00–17:00 PDT. That preserves the ECMWF
step constraint exactly: all are multiples of 3 within 144 h, and the coarse subset past 144 h is
`(12, 18, 00)`, the multiples of 6 — mirroring EU's `(6, 12, 18)`. `VERIFICATION_HOURS_UTC` should
follow (union = 0, 6, 9, 12, 15, 18, 21).

## Models considered and rejected

| Model | Why rejected |
|---|---|
| **NBM CONUS** (`ncep_nbm_conus`) | Probed 2026-05-04 at KJFK. Despite 11-day horizon and 2.5 km resolution, only populates 8/17 of our standard surface variables. Critical missing: `dewpoint_2m`, `cloud_cover_low/mid/high`, `pressure_msl`, `freezing_level_height`, `lifted_index`, `convective_inhibition`. The cloud-layer breakdown gap is the blocker — ceiling derivation needs low/mid/high split, and NBM only outputs total. Visibility partial (drops out ~D-3.8). |
| **HRRR CONUS** (`ncep_hrrr_conus`) | Rejected on **horizon only** — 18 h (48 h on 00/06/12/18Z runs), so it leaves D-1..D-4 empty. **Not** rejected on variable coverage: probed 2026-07-29 at KJFK, it populates **all 22 standard surface variables and all 7 pressure-level variables** at every level tested, unlike NBM. Worth revisiting as a D+0/D+1 model — its 48 h extended runs cover D+0 plus most of D+1, and a 14:15/02:15 schedule sits right after the 06/18Z extended runs. **One trap first:** Open-Meteo serves a *merged rolling field* per model, so hours beyond the current run's horizon are backfilled from older inits while `meta.json` reports only the newest init — we would label the tail with an init that did not produce it, crediting a new run with an old run's forecast. Needs per-hour init tracking or truncation to the run's real horizon before it is verification-safe. **If it ever lands GRIB-direct** (as #457 proposes for the *briefing* pipeline, not this one) it inherits obligations added after this doc was first written — see "What a new GRIB-direct model owes the cache" below. |
| **GEM (Canada)** | Open-Meteo `region=NORTH_AMERICA`, 10-day horizon. Less optimised for CONUS than US-domestic alternatives, no clear differentiation vs GFS over the US. Skip. |

## Open-Meteo model name convention

> ⚠️ **US GFS needs no new endpoint at all — do not register `ncep_gfs025`.** Probed
> 2026-07-29 at KJFK: `ncep_gfs025` is the **pressure-levels-only** dataset. All 15 core surface
> variables (`temperature_2m`, `dewpoint_2m`, wind, `cloud_cover*`, `precipitation`, …) come back
> **all-null**; only the pressure levels populate. Registering it as the US `model_param`, as an
> earlier revision of this section implied, would silently produce US snapshots with no surface
> weather — no temperature, no wind, no cloud, no precipitation.
>
> The existing `"gfs"` entry in `MODEL_ENDPOINTS` already points at `/v1/gfs`, the **seamless
> blend** (`gfs013` surface + `gfs025` pressure levels), and it is **global** — it already covers
> CONUS. US GFS is therefore the same fetch with a different airport list. Nothing to register.

The `ncep_` prefix convention still matters for any *genuinely new* US model (HRRR being the
candidate):
- `ncep_gfs025` (0.25°, pressure levels), `ncep_gfs013` (0.11°, surface)
- `ncep_hrrr_conus`, `ncep_nam_conus`, `ncep_nbm_conus`
- `ncep_gfs_graphcast025`

Bare names (`hrrr`, `nbm`) are rejected by the API, so such a model needs an explicit
`model_param` entry in `MODEL_ENDPOINTS` (`fetch/variables.py`). No `ncep_` models are registered
there yet, and per the note above none is needed for GFS.

## Storage growth

Linear in airport count for the snapshot table (10-day retention bounds it). `verification_scores` is unbounded and grows roughly proportional to (airports × models × days_out × time). Estimated steady-state with US added:

| Layer | EU only | EU + US |
|---|---|---|
| Snapshot rows/cycle | ~37k | ~84k |
| Snapshot table | 236 MB | ~540 MB |
| Score table /year | ~10 GB | ~17 GB |

Score table growth is the storage concern. Activate the existing `verification_monthly_stats` rollup (table schema already in place, currently empty in prod) and prune raw scores after rollup at ~12 months. Defer until score table exceeds 5 GB.

**The table above assumes two US cycles per day.** The four-cycle schedule decided in "Cycle
timing" stores four distinct inits per model per day instead of two, so it roughly doubles the US
contribution to both rows — call it **~24 GB/yr** for EU+US rather than ~17. That is more
verification signal (more init/lead pairs), not waste, but it brings the rollup + prune work
forward rather than leaving it deferred. This is the one place where four cycles genuinely costs
more than two; compute does not (see "Compute cost is not the constraint").

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
  math, no coordinate join. Each region blob is that region's airports only (~665 US),
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
3. Extend `STANDALONE_MODELS` to be region-aware: `{"eu": ["gfs", "icon", "ecmwf"], "us": ["gfs", "ecmwf"]}`. The commented-out stub in `standalone_verification.py` currently reads `["ncep_gfs025", "ecmwf"]` — **use `"gfs"`, not `ncep_gfs025`** (see the warning under "Open-Meteo model name convention"; `ncep_gfs025` has no surface variables).
4. **Generalise `_expected_cycle_init()` before adding any US hour** — otherwise every offset-cycle artifact is rejected and the droplet silently falls back to local compute daily. See "Two things that will fail silently". Then add the US cycle hours to `FORECAST_FETCH_HOURS_UTC` (or split the loop) — **14:15 / 02:15 first, then 08:15 / 20:15**; see the 02:15/warming-window constraint under Key Decisions. Prefer availability-triggered firing over fixed-clock if the scheduling seam allows it.
4b. **Make the map sample grid region-aware** — US needs `(12, 15, 18, 21, 00)`, not EU's `(6, 9, 12, 15, 18)`, or the US map has no afternoon/evening slot. `VERIFICATION_HOURS_UTC` follows.
5. Update cache builder to emit per-region keys; update API endpoints to accept `region`
   query param. **The index is DONE** — `ix_afs_region_hour_model` on
   `(region, forecast_hour, model)` already exists alongside `ix_afs_hour_model`,
   which is dropped in a follow-up once serving is region-aware. `map_queries.py`
   and `cache_builder.py` still have no `region` awareness.
6. Serving/client (see "Region serving & selection"): `fc.region` in `mapsUrlState` + EU⇄US
   toggle + selection chain; make `/maps/airport-weather` region-aware; 2-model consensus
   rendering for US; bump `FORECAST_MAP_CACHE_VERSION`
7. Discover and seed US TAF watchlist via `python -m weatherbrief.verify discover --region us` (extend the discover command) — **and add a TAF-presence filter**, or it seeds ~1,850 METAR stations instead of ~665 TAF sites (see Key Decisions)
8. Validate: 2 days of clean US cycles in `verification_cycles` (check the `source` column is the imported-artifact path, not the local-compute fallback — a rejected artifact looks like success otherwise), no EU regression, and confirm the fetched GFS `model_init_time` is the expected run rather than the previous one

## Out of scope

- NBM, HRRR, GEM additions
- Forecast horizon extension (4 days stays)
- Pre-rendering Skew-T or cross-section tiles for the watchlist
- US text-forecast integration (NWS AFD) — already exists for flight pipeline; revisit if standalone needs it
- Multi-language digest variants for US users
