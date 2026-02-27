# Time & Spatial Alignment

> How the pipeline aligns time and location when merging GRIB enrichment data with Open-Meteo forecasts and looking up data along routes.

## Datetime Convention

All datetimes throughout the pipeline are **timezone-aware UTC** (`tzinfo=timezone.utc`).

| Layer | Where | How |
|-------|-------|-----|
| Open-Meteo | `open_meteo.py` | `datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)` |
| Analysis | `analyze.py`, `pipeline.py` | `datetime(..., target_hour, tzinfo=timezone.utc)` |
| GRIB | `grib/__init__.py` | `datetime.strptime(...).replace(tzinfo=timezone.utc)` |
| Interpolated times | `compute_interpolated_time()` | Propagated from departure (aware in → aware out) |
| Pack loading | `packs.py _parse_target_time()` | Promotes naive (old packs) to aware UTC |

### Old Pack Compatibility

Packs created before the aware-UTC migration store naive ISO strings (e.g. `"2026-02-27T09:00:00"`). Pydantic deserializes these as naive datetimes. `at_time()` in `WaypointForecast` normalizes both sides to aware UTC before comparing, so old packs load without error. New packs serialize with `+00:00`, which JS `new Date()` handles correctly (and more reliably — removes browser timezone ambiguity around bare ISO strings).

## Spatial Mapping

Every stage maintains a consistent spatial index: `route_points[i]` ↔ `point_forecasts[i]` ↔ `decoded_points[i]`.

| Step | Logic |
|------|-------|
| Route interpolation | `walk_route()` with haversine great-circle via `NavPoint` |
| Open-Meteo multi-point | Comma-separated lat/lon; response order matches input |
| Cross-section storage | `point_forecasts[i]` ↔ `route_points[i]` by array index |
| GFS GRIB spatial interp | xarray bilinear `interp()` with `lon % 360` (GFS uses 0–360°) |
| ICON-EU GRIB spatial interp | xarray bilinear `interp()`, no lon conversion (native -180/180°) |
| ICON-EU domain check | `route_in_icon_eu_domain()` validates all points before fetch |
| GFS pressure levels | Extracted from existing Open-Meteo data, so exact match guaranteed |
| ICON-EU log-pressure interp | Ascending sort, NaN filter, no extrapolation, clamp ≥ 0 |

## Per-Hour GRIB Enrichment

GRIB enrichment fetches data for **each UTC hour of the flight window**, not just the departure hour. Each forecast hour's data is merged only into the matching hourly entry in the cross-section.

### Flight Window Computation

`compute_flight_window_hours()` (GFS) and `compute_icon_eu_flight_window_hours()` (ICON-EU) compute the set of forecast hours covering `[departure, departure + ceil(duration)]`:

1. For each UTC hour in the window, compute `delta = (utc_hour - init_time)` in hours
2. Snap to the model's temporal grid:
   - **GFS:** 1-hourly for f000–f120, 3-hourly for f120–f384
   - **ICON-EU:** 1-hourly for 0–78h, 3-hourly for 78–120h
3. Deduplicate and sort

Edge cases:
- `flight_duration_hours=0` → `ceil(0)+1 = 1` hour → departure hour only (same as a point-to-point enrichment)
- GFS init after departure → `max(0, delta)` clamp → f000
- Cross-midnight flights → hours in the next day that fall outside the 24h Open-Meteo cross-section are silently skipped (no matching `hourly.time.hour`)

### Enrichment Flow

`enrich_forecasts()` receives `flight_duration_hours` from `RouteConfig` via the call chain:

```
run_fetch(route, ...) → enrich_forecasts(flight_duration_hours=route.flight_duration_hours)
  → _enrich_gfs(flight_duration_hours)
  → _enrich_icon_eu(flight_duration_hours)
```

For each model, the enrichment loops over the computed forecast hours:

**GFS CLWMR/ICMR** (`_enrich_clwmr_icmr`):
```
for fhour in forecast_hours:
    decoded = _fetch_clwmr_icmr_for_fhour(fhour)  # fetch → cache → decode
    valid_utc = _forecast_hour_to_utc(init_date, init_hour, fhour)
    _merge_cloud_water_into_sections(..., valid_utc=valid_utc)
```

**GFS Cloud Diagnostics** (`_enrich_cloud_diagnostics`):
```
for fhour in forecast_hours:
    decoded = _fetch_cloud_diag_for_fhour(fhour)
    diagnostics = [build_cloud_diagnostics(raw) for raw in decoded]
    valid_utc = _forecast_hour_to_utc(init_date, init_hour, fhour)
    _apply_cloud_diagnostics_to_sections(..., valid_utc=valid_utc)
```

**ICON-EU QC/QI** (`_enrich_icon_eu`): Same loop, but processes one fhour at a time with `del decoded_points; gc.collect()` between iterations to limit memory (~800 MB per decoded ICON-EU hour).

**ICON-EU Cloud Diagnostics** (`_enrich_icon_eu_cloud_diagnostics`): Same per-hour loop for single-level ceiling/convective fields.

### Hour Matching

`_merge_cloud_water_into_sections()` and `_apply_cloud_diagnostics_to_sections()` accept `valid_utc: datetime | None`:

```python
for hourly in wf.hourly:
    if valid_utc is not None and hourly.time.hour != valid_utc.hour:
        continue  # skip non-matching hours
```

`valid_utc=None` enriches all hours (unused in practice, preserved for backward compatibility).

### Why This Matters for Icing

For a 3-hour flight departing 09:00 UTC, a route point near the destination is analyzed at ~12:00 UTC:

| Variable | Source | Time |
|----------|--------|------|
| Temperature | Open-Meteo | 12:00 UTC |
| Humidity/RH | Open-Meteo | 12:00 UTC |
| Wind | Open-Meteo | 12:00 UTC |
| CLWMR (cloud liquid water) | GRIB f012 | 12:00 UTC |
| ICMR (ice mixing ratio) | GRIB f012 | 12:00 UTC |
| Cloud cover (low/mid/high) | GRIB f012 override | 12:00 UTC |

All variables are time-aligned at 12:00 UTC. Cloud water matches the cloud cover which matches the temperature — icing zones align with actual cloud areas.

## Route-Point Time Interpolation

`interpolated_time = departure + (distance / total_distance) × duration`

Linear time mapping assuming constant ground speed. `at_time()` picks the closest hourly forecast (≤30 min error). Standard approach for aviation NWP cross-sections.

## GFS / ICON-EU Priority

GFS enriches first. ICON-EU checks `hourly.nwp_cloud_diagnostics is None` before writing cloud diagnostics, preventing contradictory overrides. For CLWMR/ICMR, ICON-EU (QC/QI) overwrites GFS values at matching pressure levels — ICON-EU has higher resolution over Europe.

## Minor Notes

- `forecast_hour` in `analyze.py` stores the last model's value (cosmetic — not used in computation)
- GFS has no domain bounds check — it's global (0.25°); xarray returns NaN for out-of-bounds, gracefully skipped
- Empty `decoded_points` arrays are skipped silently; logging exists at aggregate level

## Key Files

| File | Role |
|------|------|
| `fetch/grib/__init__.py` | GRIB enrichment entry point, per-hour merge logic |
| `fetch/grib/grib_fetch.py` | GFS HTTP range downloads, `compute_flight_window_hours()` |
| `fetch/grib/icon_eu_fetch.py` | ICON-EU downloads, `compute_icon_eu_flight_window_hours()` |
| `fetch/grib/decode.py` | GRIB2 decode and spatial interpolation |
| `fetch/grib/icon_eu_levels.py` | Model-level to pressure-level interpolation |
| `fetch/open_meteo.py` | Open-Meteo API client |
| `tasks/fetch.py` | Fetch orchestration, passes `flight_duration_hours` |
| `tasks/analyze.py` | Route-point analysis, `compute_interpolated_time()` |
| `models/analysis.py` | Data models, `at_time()` with naive/aware compat |
| `api/packs.py` | Pack loading, `_parse_target_time()` |
