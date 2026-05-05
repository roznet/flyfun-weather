# Forecast Page

> Pan-European weather overview map with per-airport forecast visualization and model accuracy heatmaps, powered by standalone verification snapshots.

## Intent

Provide a spatial overview of current weather conditions and model accuracy across ~830 European airports. Three tabs: forecast overview (color-coded map by metric), model accuracy (verification heatmap), and detailed stats (embedded verification dashboard). This page reuses data already collected by the standalone verification pipeline — no additional data fetching required.

## Architecture

```
Backend                                    Frontend
────────                                   ────────
api/maps.py                                maps.html (template)
├── GET /maps/forecast                     maps-main.ts (controller)
│   → cache or map_queries.get_forecast_   ├── state mgmt (day/hour/model/metric)
│     map_data()                           ├── tab switching (forecast/accuracy/stats)
├── GET /maps/forecast/hours               └── data loading + rerender
│   → available hours for a day            adapters/maps-adapter.ts (API client)
├── GET /maps/verification                 ├── fetchForecastMap()
│   → cache or map_queries.get_            ├── fetchVerificationMap()
│     verification_map_data()              └── fetchAvailableHours()
                                           visualization/weather-map.ts (Leaflet map)
Cache layer:                               ├── setForecastData()
  verification_cache table (JSON blobs)    └── setVerificationData()
  ├── forecast_map:{day}:{hour}
  ├── verif_map:{model}:{days_out}:{period}
  └── staleness: source_max_time vs live MAX

Data source:
  airport_forecast_snapshots table
  verification_scores table
  (from standalone verification pipeline)
```

## Tabs

### Forecast Overview
- ~830 airport markers on Leaflet map, color-coded by selectable metric
- **Metrics**: Flight Category, Wind Speed, Crosswind, Headwind, Ceiling, CAPE, Convective Risk, Cloud Cover
- **Controls**: Day (D-0 to D-3), hour (sample hours), model selector
- **Model modes**: Worst consensus, Majority consensus, or individual model (GFS/ICON/ECMWF)
- **Agreement indicator**: Border color shows model divergence (good/moderate/poor) in consensus modes
- Switching metric or model is client-side rerender; switching day/hour/consensus mode triggers API call

### Model Accuracy
- Per-airport verification accuracy, marker size scales with sample count
- **Filters**: Period (7d/30d), days-out (D-0 to D-3), model, metric
- **Metrics**: Category Match %, Ceiling MAE, Wind MAE, Temperature MAE
- Authenticated users (same as forecast tab)

### Accuracy Stats
- Embedded iframe to `/verification.html?embed`

## URL State & Share Links

The forecast and accuracy tabs both deep-link via the URL query string so any view can be shared as a stable link (PR #117).

- **Encoder/decoder**: `web/ts/utils/url-state.ts` — parses + serialises the current view (active tab, day, hour, model, metric, consensus mode, accuracy filters) to/from `URLSearchParams`. State changes write back via `history.replaceState` (no history pollution).
- **Share button**: `web/ts/utils/share-link.ts` — copies the canonical URL for the current view to the clipboard with a transient toast.
- **Two URL shapes**: forecast tab uses `?tab=forecast&day=&hour=&model=&metric=&consensus=`; accuracy tab uses `?tab=accuracy&model=&days_out=&metric=&period=`. The `tab=` param is the dispatch key.
- **Backwards-compatible**: bookmarks without `tab=` default to forecast tab with the page-level defaults — unchanged behavior.

Pattern: any new control on these tabs that affects the rendered view should be added to the encoder/decoder so the share-link round-trip stays lossless.

## Consensus Algorithm

Server-side in `map_queries.py::_consensus()`:

- **Worst mode**: Pick most restrictive flight category across models (VFR=0 < MVFR < IFR < LIFR=3)
- **Majority mode**: Most common category; ties broken by worst severity
- **Agreement scoring**: Uses `compare_models()` divergence on wind/ceiling/CAPE → good/moderate/poor
- **Numeric consensus**: Wind direction uses circular mean; others use arithmetic mean
- **Crosswind/headwind consensus**: Worst (max) value across models, matching the conservative approach of flight category consensus

## Cache Layer

Both map endpoints use a `verification_cache` table (see [metar-taf-accuracy.md](./metar-taf-accuracy.md)) to serve pre-computed JSON responses. Cache is rebuilt after each standalone verification cycle via `cache_builder.rebuild_all()`.

**Forecast map**: Cached for "worst" consensus mode only (default). Cache key: `forecast_map:{day}:{hour}`. Staleness checked against `MAX(AirportForecastSnapshotRow.fetched_at)`. Individual model or majority consensus always runs live.

**Verification map**: Cached for days_out 0 and 1 only. Cache key: `verif_map:{model}:{days_out}:{period}`. Staleness checked against `MAX(VerificationScoreRow.observation_time)` filtered by source='standalone'. Other days_out values run live.

**Fallback**: If cache is stale or missing, both endpoints fall back to live queries transparently.

## Key Queries

### Forecast Map (`get_forecast_map_data`)
1. Find latest `model_init_time` per model that has data for the target forecast_hour
2. Fetch all `AirportForecastSnapshotRow` for those (model, init_time, hour) combos
3. Derive `flight_category` from ceiling + visibility via `classify_flight_category()`
4. Enrich each snapshot with runway crosswind/headwind: load runway headings from airports DB, compute `compute_runway_winds()` per model, select best runway (min crosswind, max headwind), attach `crosswind_kt`, `headwind_kt`, `best_runway_id`, and gust equivalents
5. Group by airport → per-model data + computed consensus

### Verification Map (`get_verification_map_data`)
1. Filter `VerificationScoreRow` by source='standalone', days_out, time window, optional model
2. Aggregate per ICAO: sample_count, category_match_rate, MAE for ceiling/wind/temp/vis, ceiling_bias

## Color Scales

| Metric | Scale |
|--------|-------|
| Flight Category | VFR=green, MVFR=blue, IFR=red, LIFR=purple |
| Wind Speed | Green (<10kt) → dark red (35+kt) |
| Crosswind | Green (<5kt) → dark red (25+kt), best-runway selection |
| Headwind | Green (<10kt) → dark red (30+kt), negative = tailwind, best-runway selection |
| Ceiling | Green (>3000ft) → purple (<500ft) |
| CAPE | Green (<100 J/kg) → dark red (2000+) |
| Convective Risk | 5-level: none → extreme (green → dark red) |
| Cloud Cover | Grayscale 0-100% |
| Accuracy % | Green (>=80%) → red (<40%) |
| MAE | Per-metric thresholds (ceiling 1500ft, wind 10kt, temp 5C) |

## Gotchas

- **Hour availability**: Not all sample hours have data for all days — `fetchAvailableHours()` checks and disables unavailable hour buttons
- **Consensus requires server round-trip**: Switching between worst/majority triggers API call (server computes consensus); switching to individual model is client-side only
- **Per-model-only metrics**: `convective_risk` uses worst-across-models in consensus mode; `cloud_cover_pct` uses the average; `crosswind_kt`/`headwind_kt` use max (worst) across models
- **Runway wind data**: Crosswind/headwind require runway headings from the airports database; airports without runway data show no crosswind/headwind values. Best runway is selected by minimizing crosswind then maximizing headwind
- **Marker sizing**: Radius scales with zoom level (3-8px) for readability at all zoom levels
- **All map endpoints require authentication**: Both forecast and verification data are available to any authenticated user

## References

- Data source pipeline: [metar-taf-accuracy.md](./metar-taf-accuracy.md)
- Flight category logic: `src/weatherbrief/analysis/airport_conditions.py`
- Model comparison: `src/weatherbrief/analysis/comparison.py`
- API: `src/weatherbrief/api/maps.py`
- Queries: `src/weatherbrief/tasks/map_queries.py`
- Frontend: `web/ts/maps-main.ts`, `web/ts/visualization/weather-map.ts`
