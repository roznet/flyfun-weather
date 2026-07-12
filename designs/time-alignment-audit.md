# Time & Spatial Alignment

> How the pipeline aligns time and location when merging GRIB enrichment data with Open-Meteo forecasts and looking up data along routes.

## Datetime Convention

All datetimes throughout the pipeline are **timezone-aware UTC** (`tzinfo=timezone.utc`).

| Layer | Where | How |
|-------|-------|-----|
| Open-Meteo | `open_meteo.py` | `datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)` |
| DB / Flight model | `storage/flights.py` | `departure_time` stored as `DateTime(timezone=True)`; `_ensure_utc()` promotes naive on read |
| Analysis | `analyze.py`, `pipeline.py` | `departure_time` passed through; `target_date`/`target_hour` derived internally |
| GRIB | `grib/__init__.py` | `datetime.strptime(...).replace(tzinfo=timezone.utc)` |
| Interpolated times | `compute_interpolated_time()` | Propagated from departure (aware in → aware out) |
| Pack loading | `packs.py _parse_target_time()` | Promotes naive (old packs) to aware UTC |

### Old Pack Compatibility

Packs created before the aware-UTC migration store naive ISO strings (e.g. `"2026-02-27T09:00:00"`). Pydantic deserializes these as naive datetimes. `at_time()` in `WaypointForecast` normalizes both sides to aware UTC before comparing, so old packs load without error. New packs serialize with `+00:00`, which JS `new Date()` handles correctly (and more reliably — removes browser timezone ambiguity around bare ISO strings).

### DB Datetime Storage

`Flight.departure_time` and `BriefingPackMeta.fetch_timestamp` are stored as proper `DateTime(timezone=True)` columns (migration 014). Both SQLite and MySQL store datetimes as naive values internally:
- **SQLite**: text like `2026-02-21 09:00:00` (no timezone suffix)
- **MySQL**: `DATETIME(6)` with microsecond precision (migration 015)

The storage layer uses `_ensure_utc()` to promote naive datetimes back to aware UTC on read. `load_pack_meta()` strips tzinfo before querying to match the naive stored format. `_get_pack_dir()` reads `artifact_path` from the DB rather than reconstructing from timestamp — avoids precision mismatches.

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

The GFS path also adds coverage hours the simple loop would miss: a non-zero-minute departure gets an extra bracket hour (`extra = 1 if minute > 0`) plus its floor (`minute=0`) hour, and the flooring `_snap_to_gfs_grid_floor(dep_delta)` native hour is always included so the preceding native step exists for forward-fill in the 3-hourly region (>120h).

Edge cases:
- `flight_duration_hours=0`, round departure → `max(1, ceil(0)+1+0) = 1` hour → departure hour only (same as a point-to-point enrichment)
- GFS init after departure → `max(0, delta)` clamp → f000
- Cross-midnight flights → hours in the next day that fall outside the 24h Open-Meteo cross-section are silently skipped (no matching `hourly.time.hour`)

### Enrichment Flow

`enrich_forecasts()` receives `flight_duration_hours` from `RouteConfig` (via `run_fetch` in `tasks/fetch.py`) and fans out to three models. Phase 1 runs GFS (download+decode), ECMWF (local-disk decode), and ICON-EU download in a 3-worker thread pool; Phase 2 decodes ICON-EU sequentially (memory-heavy) after GFS is done:

```
run_fetch(route, ...) → enrich_forecasts(flight_duration_hours=route.flight_duration_hours)
  Phase 1 (parallel pool): _enrich_gfs · _enrich_ecmwf · _prefetch_icon_eu_data
  Phase 2 (sequential):    ICON-EU decode/merge
  then propagate_all(...) + apply_gfs_rh_condensate_gate(...)
```

GFS and ICON-EU write into the shared cross-sections; ECMWF writes its own `ecmwf_sections` (per-model cross-section). For each model, the enrichment loops over the computed forecast hours:

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

**ICON-EU QC/QI** (`_prefetch_icon_eu_data` in Phase 1, `_decode_and_merge_icon_eu` in Phase 2): split into a download-only prefetch and a decode+merge step. Unlike the GFS loops, the per-fhour decodes are fanned out in **parallel** via `_dispatch_decode_parallel` (issue #133 — sequential dispatch was using only one pool worker), then merged in `forecast_hours` order so the valid-time invariants hold. Memory is reclaimed by `del decoded_points` + nulling the per-fhour decode-result entry inside the merge loop, with a single `_grib_gc()` after the loop (not per-iteration).

**ICON-EU Cloud Diagnostics** (`_enrich_icon_eu_cloud_diagnostics`): Same per-hour loop for single-level ceiling/convective fields.

Three of these (GFS cloud water, GFS cloud diag, ICON-EU cloud diag) follow the same `del decoded_points; _grib_gc()` pattern at the end of each fhour iteration. The ICON-EU QC/QI path decodes in parallel and `_grib_gc()`s once after its merge loop. Without these collections, decoded_points dicts accumulate across the loop on long-route briefings and contribute to OOM pressure. Diagnostics arrays (`diagnostics_per_point`) are deleted alongside in the diag loops. `_grib_gc()` is the timing-instrumented `gc.collect()` wrapper used throughout GRIB enrichment.

### Hour Matching

`_merge_cloud_water_into_sections()` and `_apply_cloud_diagnostics_to_sections()` accept `valid_utc: datetime | None` and match via the `_matches_valid_time()` helper:

```python
def _matches_valid_time(hourly_time, valid_utc):
    if valid_utc is None:
        return True
    return hourly_time.date() == valid_utc.date() and hourly_time.hour == valid_utc.hour
```

It compares **both date and hour** (not just the hour) — necessary because long GRIB steps (e.g. ECMWF out to 192h) span multiple days, where an hour-only match would cross-day-collide. `valid_utc=None` enriches all hours (unused in practice, preserved for backward compatibility).

### Interpolated-Hour Gap and Propagation

Open-Meteo provides **hourly** forecast data for the full 24h cross-section, including hours interpolated between the underlying NWP model's native temporal resolution. At longer lead times, GFS outputs 3-hourly (f120–f384) and ICON-EU 3-hourly (78–120h). GRIB enrichment only targets native model steps (matching by `valid_utc.hour`), leaving interpolated hours **without GRIB-derived data**.

**What's affected on interpolated hours:**

| Field | Native hours | Interpolated hours (GFS, `gfs_init` set) | Interpolated hours (ICON-EU / ECMWF / GFS fallback) |
|-------|-------------|-----------------|------------------------------------|
| `nwp_cloud_diagnostics.low/mid/high.cover_pct` | GRIB averaged-window cover (GFS) or instantaneous cover | **Window-midpoint linear interp**; sub-5 % drops layer; RH/condensate gate may also drop layer post-interp | Forward-filled |
| `nwp_cloud_diagnostics.low/mid/high.base_ft/top_ft/top_temp_c` | GRIB geometry | Held over from the bracketing higher-cover endpoint | Forward-filled |
| `nwp_cloud_diagnostics` instantaneous fields (convective, boundary, total, ceiling, freezing level) | GRIB instantaneous | Step-time linear interp | Forward-filled |
| `cloud_cover_low/mid/high_pct` (bulk OM field) | Open-Meteo hourly interp (never written by GRIB enrichment) | same | same |
| `cloud_liquid_water_kg_kg` / `ice_mixing_ratio_kg_kg` (per level) | GRIB CLMR / ICMR | Step-time linear interp (`_interp_gfs_clw_hourly`) | Forward-filled (`_fill_clw_hourly`) for GFS without `gfs_init`; ECMWF / ICON-EU rebuild full `pressure_levels` via `_linear_interp_pressure_levels` |

**Cloud diagnostics propagation** (`_fill_cloud_diagnostics` in `fetch/grib/fill.py`, called from `propagate_all`):

After all GRIB enrichment completes, `propagate_all` fills `nwp_cloud_diagnostics` on gap hours between native GRIB steps. The strategy is source-dependent:

- **GFS, when `gfs_init` is provided** — window-midpoint linear interpolation (`_interp_gfs_diag_hourly`). NCEP publishes only the averaged form of LCDC/MCDC/HCDC, so each anchor sits at `step - window_length/2` (1/2/3 h depending on step position; 3 h past f120) and low/mid/high cover interpolate linearly between bracketing midpoints. Layer geometry (`base_ft`, `top_ft`, `top_temp_c`) holds over from the higher-cover endpoint; sub-5 % (`_GFS_LAYER_DROP_THRESHOLD_PCT`) covers drop the layer entirely. Convective, boundary, total cover, ceiling, and freezing level interpolate linearly with **step-time** anchoring (instantaneous in GFS pgrb2). A follow-up `apply_gfs_rh_condensate_gate` drops any layer whose pressure-level RH and condensate inside `[base_ft, top_ft]` contradict the averaged cover (per-band thresholds `_GFS_GATE_RH_LOW_PCT` = 60, `_GFS_GATE_RH_MID_PCT` = 70, `_GFS_GATE_RH_HIGH_PCT` = 70). See [meteorology-decisions §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate) for the rationale.
- **ICON-EU, ECMWF, and the GFS fallback** (no `gfs_init`) — forward-fill (`_fill_diag_hourly`). ICON-EU and ECMWF publish instantaneous cover, so persistence is the right semantic.

GFS path (with `gfs_init`), illustrating the f132 / f135 case past f120:

```
Hour 12 Z (f132 native, avg over 09–12 Z) → midpoint anchor at 10:30 Z
Hour 13 Z (interp) → linearly interpolated between f132 midpoint (10:30) and f135 midpoint (13:30)
Hour 14 Z (interp) → just past f135 midpoint, so dominated by f135's value
Hour 15 Z (f135 native, avg over 12–15 Z) → midpoint anchor at 13:30 Z
Hour 16 Z (interp) → linearly interpolated between f135 midpoint and f138 midpoint
...
```

Forward-fill path (ICON-EU / ECMWF / GFS fallback):

```
Hour 06 (native) → diagnostics from GRIB
Hour 07 (interp) → copied from 06
Hour 08 (interp) → copied from 06
Hour 09 (native) → diagnostics from GRIB (replaces 06's)
Hour 10 (interp) → copied from 09
...
```

Hours before the first native step (e.g., 00–05 when the first GRIB hour is 06) remain without diagnostics — bulk NWP percentages apply as fallback.

**Why this was critical**: Without `nwp_cloud_diagnostics`, multiple downstream consumers fall back to applying the bulk NWP cloud percentage across the full ICAO altitude band (6500–20000ft for mid cloud). When the actual cloud is at 18000ft but the bulk percentage is 89%, this triggers false icing and inflated SFIP scores at 8000ft in bone-dry air (DD > 8°C).

**Consumers that use altitude-aware diagnostics:**

| Consumer | Field used | Fallback without diagnostics |
|----------|-----------|------------------------------|
| Ogimet icing NWP fallback (`analysis/sounding/icing.py`) | `nwp_cloud_diagnostics` | Bulk % across full band → **false icing** |
| SFIP cloud cover input (`analysis/sounding/sfip.py`) | `nwp_cloud_diagnostics` | Bulk % by pressure band → **inflated scores** |
| Altitude advisories VMC/IMC (`analysis/sounding/advisories.py`) | `nwp_cloud_diagnostics` | Bulk % by ICAO band → **wrong regime labels** |
| NWP ceiling (`analysis/sounding/__init__.py`) | `nwp_cloud_diagnostics.ceiling_ft` | `None` → no NWP ceiling |
| Cross-section NWP cloud layer viz (frontend) | `nwp_cloud_diagnostics` | `null` → no layer rendering |

**CLWMR/ICMR gap**: Not propagated. At lead times where the gap exists (>120h for GFS), CLWMR is often unavailable entirely. When present only at native hours, interpolated hours fall through to the Ogimet path (the standard icing method) instead of the LWC-direct path — acceptable since Ogimet is the primary assessment and the cloud diagnostics propagation ensures the NWP fallback works correctly regardless.

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
| Cloud diagnostics (base/top) | GRIB f012 (or propagated from nearest native) | 12:00 UTC |

All variables are time-aligned at 12:00 UTC. Cloud water matches the cloud cover which matches the temperature — icing zones align with actual cloud areas.

## Route-Point Time Interpolation

`interpolated_time = departure + (distance / total_distance) × duration`

Linear time mapping assuming constant ground speed. `at_time()` picks the closest hourly forecast (≤30 min error). Standard approach for aviation NWP cross-sections.

## GFS / ICON-EU Priority

GFS and ICON-EU share one set of cross-sections. GFS enriches first; ICON-EU checks `hourly.nwp_cloud_diagnostics is None` before writing cloud diagnostics, preventing contradictory overrides. For CLWMR/ICMR, ICON-EU (QC/QI) overwrites GFS values at matching pressure levels — ICON-EU has higher resolution over Europe. ECMWF enriches a separate per-model `ecmwf_sections`, so there is no GFS/ICON-EU-vs-ECMWF priority contest — ECMWF replaces full pressure-level soundings (a2) and applies surface + cloud diagnostics (a1) into its own sections.

## Minor Notes

- `forecast_hour` in `analyze.py` stores the last model's value (cosmetic — not used in computation)
- GFS has no domain bounds check — it's global (0.25°); xarray returns NaN for out-of-bounds, gracefully skipped
- Empty `decoded_points` arrays are skipped silently; logging exists at aggregate level

## Key Files

| File | Role |
|------|------|
| `fetch/grib/__init__.py` | GRIB enrichment entry point, per-hour merge logic; invokes `propagate_all` and `apply_gfs_rh_condensate_gate` after enrichment |
| `fetch/grib/fill.py` | Time-axis fill — window-midpoint interp for GFS cloud diagnostics, step-time interp for GFS CLW/ICMR + ECMWF surface + ECMWF / ICON-EU sounding rebuild, forward-fill for ICON-EU / ECMWF cloud diagnostics, and the GFS RH/condensate phantom-layer gate (see [meteorology-decisions §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate)) |
| `fetch/grib/grib_fetch.py` | GFS HTTP range downloads, `compute_flight_window_hours()` |
| `fetch/grib/icon_eu_fetch.py` | ICON-EU downloads, `compute_icon_eu_flight_window_hours()` |
| `fetch/grib/decode.py` | GRIB2 decode and spatial interpolation |
| `fetch/grib/icon_eu_levels.py` | Model-level to pressure-level interpolation |
| `fetch/open_meteo.py` | Open-Meteo API client |
| `tasks/fetch.py` | Fetch orchestration, passes `flight_duration_hours` |
| `tasks/analyze.py` | Route-point analysis, `compute_interpolated_time()` |
| `models/analysis.py` | Data models, `at_time()` with naive/aware compat |
| `api/packs.py` | Pack loading; imports `parse_target_time()` from `tasks/artifacts.py` (aliased `_parse_target_time`) |
