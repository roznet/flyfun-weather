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
│     map_data() — per-model + worst       ├── 4 tabs: forecast/synoptic/
│     consensus baked in                   │           climatology/stats
├── GET /maps/forecast/hours               └── data loading + client rerender
│   → available hours for a day            adapters/maps-adapter.ts (API client)
├── GET /maps/forecast/days                ├── fetchForecastMap(day,hour)
│   → which D-0..D-3 have data             ├── fetchAvailableHours()
└── GET /maps/airport-weather              └── fetchAvailableDays()
    → forecast+obs for specific ICAOs      visualization/weather-map.ts (Leaflet map)
api/airport_profile.py                     ├── setForecastData()
└── GET /maps/airport-profile (SSE)        └── weather-map-consensus.ts
    → on-demand single-airport                  (client-side computeConsensus)
      cross-section (panel)                visualization/airport-profile-panel.ts
                                           visualization/synoptic-map.ts (Hewson)
Cache layer:                               visualization/climatology-tab.ts
  verification_cache table (JSON blobs)
  ├── forecast_map:{day}:{hour}  (no mode — one entry per day/hour)
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
- **Controls**: Day (D-0 to D-3), hour (sample hours), model selector
- **Model modes**: Worst consensus, Majority consensus, or individual model (GFS/ICON/ECMWF)
- **Agreement indicator**: Border color shows model divergence (good/moderate/poor) in consensus modes
- **All mode/metric switches are client-side rerenders.** `/maps/forecast` returns per-model data for every airport plus a server pre-computed worst consensus; the frontend (`weather-map-consensus.ts::computeConsensus`) recomputes worst/majority itself, so changing model OR metric is a pure rerender. Only day/hour changes trigger an API call (different snapshot set)

### Airport Profile Panel
- Right-clicking a forecast marker opens a side panel (`airport-profile-panel.ts`) with a single-airport time-axis cross-section.
- Data comes from `GET /maps/airport-profile` (SSE, in `api/airport_profile.py`) — an on-demand briefing-style cross-section generated per request, not from the snapshot cache. The panel has its own model selector (defaults to ECMWF, or the map's model when one is selected) and requests `_DEFAULT_WINDOW_H` (3) forward hours.
- Both the open ICAO (`fc.apt`) and panel model (`fc.apModel`) are deep-linked, so a shared URL re-opens the panel.

### Synoptic
- Hewson frontal-analysis overlay (`synoptic-map.ts` + hewson adapters/colormaps). Inner controls (model/init/level/metric) are NOT yet deep-linked — only the `tab=synoptic` switch is preserved. See [frontal-detection.md](./frontal-detection.md).

### Climatology
- `climatology-tab.ts`. Opening it fires the `CLIMATOLOGY_OPENED` analytics event.

### Accuracy Stats (`stats`)
- Embedded iframe to `/verification.html?embed`; loaded lazily on first switch. No inner filters are deep-linked from this page (the iframe carries its own state).

## URL State & Share Links

The forecast tab deep-links via the URL query string so any view can be shared as a stable link (PR #117).

- **Encoder/decoder**: `web/ts/utils/url-state.ts` (`createUrlState`) — a schema of `{key: {default, values?}}` parses + serialises the current view to/from `URLSearchParams`. `maps-main.ts` defines `mapsUrlState` with keys: `tab`, `fc.day`, `fc.hour`, `fc.model`, `fc.metric`, `fc.apt` (open airport-profile ICAO), `fc.apModel`. State changes write back via `history.replaceState` (no history pollution). Keys whose value equals the default are omitted, so an untouched view yields a bare `/maps.html`.
- **Share button**: `web/ts/utils/share-link.ts` — copies the canonical URL for the current view to the clipboard with a transient toast.
- **`tab=` is the dispatch key** with values `forecast | synoptic | climatology | stats`. Only the forecast tab's inner controls are deep-linked; synoptic/climatology/stats preserve the tab switch but not their inner state.
- **Backwards-compatible**: bookmarks without `tab=` default to forecast tab with the page-level defaults.

Pattern: any new control on the forecast tab that affects the rendered view should be added to the `mapsUrlState` schema so the share-link round-trip stays lossless.

## Consensus Algorithm

Consensus now lives in **two** places that must stay behaviourally aligned:
- **Server**: `analysis/airport_consensus.py::consensus()` (re-exported into `map_queries.py` as `_consensus`/`_shared_consensus`). Used to bake a `consensus` field into every airport in the API response (worst mode).
- **Client**: `web/ts/visualization/weather-map-consensus.ts::computeConsensus()`. Recomputes worst OR majority from per-model data on every rerender — this is what actually colors the markers, so the client rules are authoritative for the UI.

Rules (both implementations):
- **Worst mode**: Pick most restrictive flight category across models (VFR=0 < MVFR < IFR < LIFR=3)
- **Majority mode**: Most common category; ties broken by worst severity
- **Agreement scoring**: Uses `compare_models()` divergence on wind/ceiling/CAPE/visibility/cloud → good/moderate/poor
- **Numeric consensus**: Wind direction uses circular mean; others arithmetic mean (server) / median in majority mode (client)
- **Crosswind consensus**: max (largest crosswind) across models — the conservative pick
- **Headwind consensus**: server takes `min` (weakest headwind / strongest tailwind = least favourable); client `weather-map-consensus.ts` takes `max`. NOTE: these disagree — verify intent before relying on headwind consensus values

## Cache Layer

Both map endpoints use a `verification_cache` table (see [metar-taf-accuracy.md](./metar-taf-accuracy.md)) to serve pre-computed JSON responses. Cache is rebuilt after each standalone verification cycle via `cache_builder.rebuild_all()`.

**Forecast map**: One cache entry per `forecast_map:{day}:{hour}` (no mode dimension — the cached payload holds per-model data + baked worst consensus, and the client derives every model/majority view from it). Staleness checked against `MAX(AirportForecastSnapshotRow.fetched_at)`.

**Fallback**: If cache is stale or missing, the endpoint falls back to a live `get_forecast_map_data()` query transparently.

## Key Queries

### Forecast Map (`get_forecast_map_data`)
1. Find latest `model_init_time` per model that has data for the target forecast_hour
2. Fetch all `AirportForecastSnapshotRow` for those (model, init_time, hour) combos
3. Derive `flight_category` from ceiling + visibility via `classify_flight_category()`
4. Enrich each snapshot with runway crosswind/headwind: load runway headings from airports DB, compute `compute_runway_winds()` per model, select best runway (min crosswind, max headwind), attach `crosswind_kt`, `headwind_kt`, `best_runway_id`, and gust equivalents
5. Group by airport → per-model data + computed consensus

## Color Scales

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

- **Hour availability**: Not all sample hours have data for all days — `fetchAvailableHours()` checks and disables unavailable hour buttons; `fetchAvailableDays()` does the same for the D-0..D-3 day buttons
- **Consensus is client-side**: Switching worst/majority/individual-model is all a pure client rerender (`computeConsensus`); only day/hour hits the API. Don't reintroduce a per-mode server query or per-mode cache key
- **Per-model-only metrics (client `computeConsensus`)**: `convective_risk` uses worst-across-models (ordinal max) in consensus mode; `cloud_cover_pct` worst = max; `crosswind_kt` worst = max; `headwind_kt` worst = max (note the server's `airport_consensus.py` uses min for headwind — see Consensus Algorithm)
- **Runway wind data**: Crosswind/headwind require runway headings from the airports database; airports without runway data show no crosswind/headwind values. Best runway is selected by minimizing crosswind then maximizing headwind
- **Marker sizing**: Radius scales with zoom level (5px at z<=4 up to 11px at z>7) for readability at all zoom levels
- **All map endpoints require authentication**: forecast data is available to any authenticated user

## References

- Data source pipeline: [metar-taf-accuracy.md](./metar-taf-accuracy.md)
- Flight category logic: `src/weatherbrief/analysis/airport_conditions.py`
- Model comparison: `src/weatherbrief/analysis/comparison.py`
- Consensus (server): `src/weatherbrief/analysis/airport_consensus.py` (`consensus`, `enrich_wind`; re-exported into `map_queries.py` as `_consensus`/`_enrich_wind`)
- Consensus (client): `web/ts/visualization/weather-map-consensus.ts` (`computeConsensus`)
- API: `src/weatherbrief/api/maps.py`, `src/weatherbrief/api/airport_profile.py` (airport-profile SSE)
- Queries: `src/weatherbrief/tasks/map_queries.py`
- Frontend: `web/ts/maps-main.ts`, `web/ts/visualization/weather-map.ts`, `web/ts/visualization/airport-profile-panel.ts`, `web/ts/visualization/synoptic-map.ts`, `web/ts/visualization/climatology-tab.ts`
- Synoptic / frontal overlay: [frontal-detection.md](./frontal-detection.md)
