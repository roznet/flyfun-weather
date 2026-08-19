# Forecast Page

> Pan-European weather overview map with per-airport forecast visualization, powered by standalone verification snapshots.

## Intent

Provide a spatial overview of current weather conditions across ~620 European airports (watchlist in `configs/airport_watchlist.json`). The page reuses data already collected by the standalone verification pipeline — no additional data fetching required.

**Removed in #154**: the Model Accuracy Map tab and its underlying `get_verification_map_data` query / `verif_map:*` cache keys / `GET /maps/verification` endpoint are gone. Per-airport accuracy is now surfaced via the optimistic-bias leaderboard (see ``metar-taf-accuracy.md``).

## Architecture

```
Backend                                    Frontend
────────                                   ────────
api/maps.py                                maps.html (template)
├── GET /maps/forecast (day,hour only)     maps-main.ts (controller)
│   → cache or map_queries.get_forecast_   ├── state mgmt (day/hour/model/metric)
│     map_data() — per-model + BOTH        ├── 4 tabs: forecast/synoptic/
│     consensus blocks baked in            │           climatology/stats
├── GET /maps/forecast/days                └── data loading + client rerender
│   → per day: which hours + which         adapters/maps-adapter.ts (API client)
│     models have data (D+0..D+6)          ├── fetchForecastMap(day,hour)
├── GET /maps/airport-weather             └── fetchAvailableDays()
│   → forecast+obs for specific ICAOs      visualization/weather-map.ts (Leaflet map)
├── GET /flights/frequent-airports        ├── setForecastData()
│   → top-5 dep/dest from history (#419;   └── weather-map-format.ts
│     consumed by the iOS map only)
api/help.py  GET /help/catalog                  (reads baked consensus + served
└── serves map-metrics-catalog.json              map-metrics-catalog.json colours)
    (colours/thresholds) for iOS          visualization/airport-profile-panel.ts
                                           visualization/synoptic-map.ts (Hewson)
Cache layer:                               visualization/climatology-tab.ts
  verification_cache table (JSON blobs)    (airport-profile SSE panel:
  ├── forecast_map:{ver}:{day}:{hour}       api/airport_profile.py, see below)
  │     (versioned; one entry per day/hour)
  └── staleness: source_max_time vs live MAX

Data source:
  airport_forecast_snapshots table
  (from standalone verification pipeline)
```

## Tabs

The page hosts four tabs (`forecast`, `synoptic`, `climatology`, `stats`). Only the forecast tab is owned by this doc; the others are surfaced here but the analysis behind them lives in their own docs.

### Forecast Overview
- ~620 airport markers (watchlist) on a Leaflet map, color-coded by selectable metric
- **Metrics** (10): Flight Category, Alternate Required (FAA/EASA, #249), Wind Speed, Crosswind, Headwind, Ceiling, Visibility, CAPE, Convective Risk, Cloud Cover
- **Controls**: Day (D-0 to D-6), hour (sample hours — five on the near days, three on D-6), model selector. Day and hour buttons are rendered from `/maps/forecast/days`, not hardcoded.
- **Model modes**: Worst consensus, Majority consensus, or individual model (GFS/ICON/ECMWF)
- **Agreement indicator**: Border color shows model divergence (good/moderate/poor) in consensus modes
- **All mode/metric switches are client-side rerenders.** `/maps/forecast` returns per-model data for every airport plus **both** server pre-computed consensus blocks (`consensus` = worst, `consensus_majority`); the frontend just re-reads the payload and recolours (`weather-map-format.getConsensus`), so changing model OR metric is a pure rerender with no consensus math. Only day/hour changes trigger an API call (different snapshot set)

### Airport Profile Panel
- **Left-click / tap** a forecast marker opens a side panel (`airport-profile-panel.ts`); right-click is a desktop alias for the same handler (touch devices have neither hover nor right-click, so click is the primary entry point).
- **Three view modes** behind a segmented toggle, persisted to `localStorage` under `wb_apProfileView2`: `card` (default), `cross`, `skewt`. The **card is free** — `airport-summary-card.ts` renders it synchronously from the forecast-map payload the page already holds, with **no network round-trip**. Cross-section and Skew-T are the expensive views.
- Cross-section / Skew-T data comes from `GET /maps/airport-profile` (SSE, in `api/airport_profile.py`) — an on-demand briefing-style profile generated per request, not from the snapshot cache. The stream is warmed **on intent**: pointer-enter, an explicit view switch, or a dwell timer while the user lingers on the card. The panel has its own model selector (`panelDefaultModel()`: the map's model when an individual one is selected, otherwise ECMWF — a consensus mode is not a real model) and requests `_DEFAULT_WINDOW_H` (3) forward hours.
- Clicking a metric row in the card calls back into `setMetric()`, i.e. it **recolours the whole map** to that metric. The card's consensus column mirrors the map's mode (`panelConsensusMode()`), falling back to `worst`.
- Open ICAO (`fc.apt`), panel model (`fc.apModel`) and view mode (`fc.apView`) are all deep-linked, so a shared URL re-opens the panel on the right view. A deep-linked model the selected day has no data for is constrained via `setAvailableModels()` **before** the load, not after.

### Synoptic
- Hewson frontal-analysis overlay (`synoptic-map.ts` + hewson adapters/colormaps). Inner controls (model/init/level/metric) are NOT yet deep-linked — only the `tab=synoptic` switch is preserved. See [frontal-detection.md](./frontal-detection.md).

### Climatology
- `climatology-tab.ts`, with its own `Map` / `Top airports` sub-tabs (`#clim-subtabs`, not deep-linked). Opening the tab fires the `CLIMATOLOGY_OPENED` analytics event.

### Accuracy Stats (`stats`)
- Embedded iframe to `/verification.html?embed`; loaded lazily on first switch. No inner filters are deep-linked from this page (the iframe carries its own state).

## URL State & Share Links

The forecast tab deep-links via the URL query string so any view can be shared as a stable link (PR #117).

- **Encoder/decoder**: `web/ts/utils/url-state.ts` (`createUrlState`) — a schema of `{key: {default, values?}}` parses + serialises the current view to/from `URLSearchParams`. `maps-main.ts` defines `mapsUrlState` with keys: `tab`, `fc.day`, `fc.hour`, `fc.model`, `fc.metric`, `fc.apt` (open airport-profile ICAO), `fc.apModel`, `fc.apView` (`card|cross|skewt`). State changes write back via `history.replaceState` (no history pollution). Keys whose value equals the default are omitted, so an untouched view yields a bare `/maps.html`.
- **Share button**: `web/ts/utils/share-link.ts` — copies the canonical URL for the current view to the clipboard with a transient toast.
- **`tab=` is the dispatch key** with values `forecast | synoptic | climatology | stats`. Only the forecast tab's inner controls are deep-linked; synoptic/climatology/stats preserve the tab switch but not their inner state.
- **Backwards-compatible**: bookmarks without `tab=` default to forecast tab with the page-level defaults.

Pattern: any new control on the forecast tab that affects the rendered view should be added to the `mapsUrlState` schema so the share-link round-trip stays lossless.

## Consensus Algorithm

Consensus is computed **only on the server** (`analysis/airport_consensus.py::consensus()`, re-exported into `map_queries.py` as `_consensus`/`_shared_consensus`). Both modes are baked into every airport in the API response — `consensus` (worst) **and** `consensus_majority` (majority) — so the clients carry zero consensus math and cannot drift (#419). The web reads whichever block matches the selected mode (`weather-map-format.getConsensus`); the retired `computeConsensus` client recompute is gone. iOS reads the same baked blocks.

The server output covers every field the markers/card read: `flight_category`, `convective_risk` (ordinal over all models), the numeric fields (`wind_speed_kt`, `ceiling_ft`, `cape_jkg`, `visibility_m`, `crosswind_kt`, `headwind_kt`, `cloud_cover_pct`), `wind_dir_deg` (circular mean), and per-field `agreement`. `tests/test_consensus_parity.py` (+ `tests/fixtures/consensus_vectors.json`) pins the server to the reference values the client used to compute, field by field.

Rules:
- **Worst mode**: Pick most restrictive flight category across models (VFR=0 < MVFR < IFR < LIFR=3)
- **Majority mode**: Most common category; ties broken by worst severity
- **Agreement scoring**: Uses `compare_models()` divergence on wind/ceiling/CAPE/visibility/cloud → good/moderate/poor
- **Numeric consensus**: Wind direction uses circular mean; every other numeric field takes the least-favourable value in worst mode (`min` for the fields in `_WORST_IS_MIN`, `max` otherwise) and the median of the winning-category pool in majority mode (`_reduce_numeric`)
- **Crosswind consensus**: max (largest crosswind) across models — the conservative pick
- **Headwind consensus**: `_WORST_IS_MIN` includes `headwind_kt`, so worst mode takes `min` — a positive headwind helps, so the weakest headwind / strongest tailwind is the least-favourable pick (worst ≠ max here). Don't "fix" this to `max`

## Cache Layer

Both map endpoints use a `verification_cache` table (see [metar-taf-accuracy.md](./metar-taf-accuracy.md)) to serve pre-computed JSON responses. Cache is rebuilt after each standalone verification cycle via `cache_builder.rebuild_all()`.

**Forecast map**: One cache entry per `forecast_map:{version}:{day}:{hour}` (no mode dimension — the cached payload holds per-model data + both baked consensus blocks). The key is **version-segmented** (`FORECAST_MAP_CACHE_VERSION` / `forecast_map_cache_key`, currently `v3`): bump it whenever the baked payload shape changes so entries written by the old code never match (`is_stale()` only checks `fetched_at` + the UTC-date rule, not the shape) and the endpoint falls through to the live path instead of serving a stale shape. Staleness checked against `MAX(AirportForecastSnapshotRow.fetched_at)`.

**Fallback**: If cache is stale or missing, the endpoint falls back to a live `get_forecast_map_data()` query transparently.

## Key Queries

### Forecast Map (`get_forecast_map_data`)
1. Find latest `model_init_time` per model that has data for the target forecast_hour
2. Fetch all `AirportForecastSnapshotRow` for those (model, init_time, hour) combos
3. Derive `flight_category` from ceiling + visibility via `classify_flight_category()`
4. Enrich each snapshot with runway crosswind/headwind: load runway headings from airports DB, compute `compute_runway_winds()` per model, select best runway (min crosswind, max headwind), attach `crosswind_kt`, `headwind_kt`, `best_runway_id`, and gust equivalents
5. Group by airport → per-model data + computed consensus

## Color Scales

The colour ramps, thresholds, metric labels and legends are **served data**, not hardcoded in the client: `web/ts/data/map-metrics-catalog.json` is the single source of truth, imported directly by `weather-map-format.ts` (the web bundle keeps one copy) and served ETag-cacheable to iOS via `/api/help` (`maps` section, `api/help.py`). A threshold or colour change is a one-line JSON edit both clients pick up — the same weather can't show in two colours on two devices (#419, B2). The table below is the current content of that catalog.

| Metric | Scale |
|--------|-------|
| Flight Category | VFR=green, MVFR=blue, IFR=red, LIFR=purple |
| Alternate Required | No=green, Yes=red (FAA/EASA alternate-required flag; ordinal no<yes for consensus) |
| Wind Speed | Green (<10kt) → dark red (35+kt) |
| Crosswind | Green (<5kt) → dark red (25+kt), best-runway selection |
| Headwind | Green (<10kt) → dark red (30+kt), negative = tailwind, best-runway selection |
| Ceiling | Green (>3000ft) → purple (<500ft) |
| Visibility | Flight-category bands (VFR/MVFR/IFR/LIFR); region-aware breakpoints (SM in US, km in EU) |
| CAPE | Green (<100 J/kg) → dark red (2000+) |
| Convective Risk | 5-level: none → extreme (green → dark red) |
| Cloud Cover | Grayscale 0-100% |
| Accuracy % | Green (>=80%) → red (<40%) |
| MAE | Per-metric thresholds (ceiling 1500ft, wind 10kt, temp 5C) |

## Gotchas

- **The grid is not rectangular, and the UI is drawn from the data**: the horizon runs D+0..D+6 and is set by ECMWF (168h wall), not by the model that reaches furthest — a day only GFS can reach has nothing to cross-check it. ICON-EU's cloud-diag GRIB stops at 120h, which falls exactly on the D+4/D+5 boundary, so **D+5 and D+6 carry two models**; and ECMWF delivers only 6-hourly steps past 144h, where 09Z/15Z never land, so **D+6 carries three sample hours, not five**. `tasks/forecast_grid.py` is the single source of truth (`MAX_FORECAST_DAY`, `MAP_FORECAST_DAYS`, `sample_hours_for_day`) — the cycle, cache builder and API all read it. `fetchAvailableDays()` reports what each day actually holds, and the day/hour pickers are **rendered from that response**, not from markup; a model that can't reach the selected day is disabled with an explanation, so its absence never reads as agreement. Don't restate the horizon rules in the client. (#415)
- **Consensus is baked, switching is a pure client rerender**: both `consensus` (worst) and `consensus_majority` are baked into the one payload; switching worst/majority/individual-model just re-reads the payload and recolours — only day/hour hits the API. Don't reintroduce a client-side recompute, a per-mode server query, or a per-mode cache key
- **Per-metric worst rules** (all server-side in `airport_consensus.py`; the client only picks a baked block): `convective_risk` worst = ordinal max across models; `cloud_cover_pct` worst = max; `crosswind_kt` worst = max; `headwind_kt` worst = **min** (weakest headwind is least favourable — see Consensus Algorithm). The client's old `computeConsensus` / `NUMERIC_CONSENSUS` tables no longer exist; `weather-map-consensus.ts` keeps only shared helpers (`isConsensusMode`, `CAT_ORDER`, `RISK_ORDER`, `median`, `circularMean`)
- **Runway wind data**: Crosswind/headwind require runway headings from the airports database; airports without runway data show no crosswind/headwind values. Best runway is selected by minimizing crosswind then maximizing headwind
- **Marker sizing**: Radius scales with zoom level (5px at z<=4 up to 11px at z>7) for readability at all zoom levels
- **All map endpoints require authentication**: forecast data is available to any authenticated user

## References

- Data source pipeline: [metar-taf-accuracy.md](./metar-taf-accuracy.md)
- Flight category logic: `src/weatherbrief/analysis/airport_conditions.py`
- Model comparison: `src/weatherbrief/analysis/comparison.py`
- Consensus (server, single source): `src/weatherbrief/analysis/airport_consensus.py` (`consensus`, `enrich_wind`; re-exported into `map_queries.py` as `_consensus`/`_enrich_wind`). Both modes baked in `get_forecast_map_data`. Client reads the baked blocks via `weather-map-format.getConsensus` — the old `computeConsensus` recompute was retired in #419.
- Consensus parity guardrail: `tests/test_consensus_parity.py` + `tests/fixtures/consensus_vectors.json`.
- Map-metrics catalog (colours/thresholds/labels/legends): `web/ts/data/map-metrics-catalog.json`, served via `src/weatherbrief/api/help.py` (`maps` section).
- Frequent airports (#419): `src/weatherbrief/api/flights.py` (`compute_frequent_airports`, `GET /flights/frequent-airports`). Only the iOS forecast map consumes it (`ForecastMapViewModel`) — the web page has no caller.
- API: `src/weatherbrief/api/maps.py`, `src/weatherbrief/api/airport_profile.py` (airport-profile SSE)
- Queries: `src/weatherbrief/tasks/map_queries.py`; cache key: `src/weatherbrief/tasks/cache_builder.py` (`forecast_map_cache_key`, `FORECAST_MAP_CACHE_VERSION`)
- Frontend: `web/ts/maps-main.ts`, `web/ts/visualization/weather-map.ts`, `web/ts/visualization/weather-map-format.ts` (catalog interpreter + `getConsensus`), `web/ts/visualization/weather-map-consensus.ts` (shared helpers/orderings), `web/ts/visualization/airport-profile-panel.ts` + `web/ts/visualization/airport-summary-card.ts`, `web/ts/visualization/synoptic-map.ts`, `web/ts/visualization/climatology-tab.ts`
- Panel renderers: `web/ts/visualization/cross-section/renderer.ts`, `web/ts/visualization/skewt/renderer.ts` (shared with `/briefing.html`; the panel keeps its own layer-toggle state under `wb_apProfileLayers` so it doesn't bleed into the briefing view)
- Synoptic / frontal overlay: [frontal-detection.md](./frontal-detection.md)
