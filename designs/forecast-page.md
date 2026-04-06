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
│   → map_queries.get_forecast_map_data()  ├── state mgmt (day/hour/model/metric)
├── GET /maps/forecast/hours               ├── tab switching (forecast/accuracy/stats)
│   → available hours for a day            └── data loading + rerender
├── GET /admin/maps/verification           adapters/maps-adapter.ts (API client)
│   → map_queries.get_verification_map_data()  ├── fetchForecastMap()
                                           ├── fetchVerificationMap()
Data source:                               └── fetchAvailableHours()
  airport_forecast_snapshots table         visualization/weather-map.ts (Leaflet map)
  verification_scores table                ├── setForecastData()
  (from standalone verification pipeline)  └── setVerificationData()
```

## Tabs

### Forecast Overview
- ~830 airport markers on Leaflet map, color-coded by selectable metric
- **Metrics**: Flight Category, Wind Speed, Ceiling, CAPE, Convective Risk, Cloud Cover
- **Controls**: Day (D-0 to D-3), hour (sample hours), model selector
- **Model modes**: Worst consensus, Majority consensus, or individual model (GFS/ICON/ECMWF)
- **Agreement indicator**: Border color shows model divergence (good/moderate/poor) in consensus modes
- Switching metric or model is client-side rerender; switching day/hour/consensus mode triggers API call

### Model Accuracy
- Per-airport verification accuracy, marker size scales with sample count
- **Filters**: Period (7d/30d), days-out (D-0 to D-3), model, metric
- **Metrics**: Category Match %, Ceiling MAE, Wind MAE, Temperature MAE
- Admin-only endpoint (`require_admin`)

### Accuracy Stats
- Embedded iframe to `/verification.html?embed`

## Consensus Algorithm

Server-side in `map_queries.py::_consensus()`:

- **Worst mode**: Pick most restrictive flight category across models (VFR=0 < MVFR < IFR < LIFR=3)
- **Majority mode**: Most common category; ties broken by worst severity
- **Agreement scoring**: Uses `compare_models()` divergence on wind/ceiling/CAPE → good/moderate/poor
- **Numeric consensus**: Wind direction uses circular mean; others use arithmetic mean

## Key Queries

### Forecast Map (`get_forecast_map_data`)
1. Find latest `model_init_time` per model that has data for the target forecast_hour
2. Fetch all `AirportForecastSnapshotRow` for those (model, init_time, hour) combos
3. Derive `flight_category` from ceiling + visibility via `classify_flight_category()`
4. Group by airport → per-model data + computed consensus

### Verification Map (`get_verification_map_data`)
1. Filter `VerificationScoreRow` by source='standalone', days_out, time window, optional model
2. Aggregate per ICAO: sample_count, category_match_rate, MAE for ceiling/wind/temp/vis, ceiling_bias

## Color Scales

| Metric | Scale |
|--------|-------|
| Flight Category | VFR=green, MVFR=blue, IFR=red, LIFR=purple |
| Wind Speed | Green (<10kt) → dark red (35+kt) |
| Ceiling | Green (>3000ft) → purple (<500ft) |
| CAPE | Green (<100 J/kg) → dark red (2000+) |
| Convective Risk | 5-level: none → extreme (green → dark red) |
| Cloud Cover | Grayscale 0-100% |
| Accuracy % | Green (>=80%) → red (<40%) |
| MAE | Per-metric thresholds (ceiling 1500ft, wind 10kt, temp 5C) |

## Gotchas

- **Hour availability**: Not all sample hours have data for all days — `fetchAvailableHours()` checks and disables unavailable hour buttons
- **Consensus requires server round-trip**: Switching between worst/majority triggers API call (server computes consensus); switching to individual model is client-side only
- **Marker sizing**: Radius scales with zoom level (3-8px) for readability at all zoom levels
- **Verification data is admin-only**: `/admin/maps/verification` requires admin auth; forecast overview is available to all authenticated users

## References

- Data source pipeline: [metar-taf-accuracy.md](./metar-taf-accuracy.md)
- Flight category logic: `src/weatherbrief/analysis/airport_conditions.py`
- Model comparison: `src/weatherbrief/analysis/comparison.py`
- API: `src/weatherbrief/api/maps.py`
- Queries: `src/weatherbrief/tasks/map_queries.py`
- Frontend: `web/ts/maps-main.ts`, `web/ts/visualization/weather-map.ts`
