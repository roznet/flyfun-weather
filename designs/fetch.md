# Fetch Layer

> Weather data retrieval: Open-Meteo multi-model, DWD text forecasts, Autorouter GRAMET, SRTM elevation, model freshness

## Intent

Centralize all external data fetching with graceful failure handling. Each data source may be unavailable; the pipeline continues with whatever succeeds.

## Open-Meteo Client (`fetch/open_meteo.py`)

Multi-model NWP data from the free Open-Meteo API.

### Model Endpoints (`fetch/variables.py`)

Each model has a `ModelEndpoint` dataclass specifying URL, max forecast range, and unavailable variables:

| Model | Endpoint | Max Days | Notes |
|-------|----------|----------|-------|
| `best_match` | `/v1/forecast` | 16 | Open-Meteo's auto-blend |
| `gfs` | `/v1/gfs` | 16 | NCEP GFS |
| `ecmwf` | `/v1/ecmwf` | 10 | No freezing_level, visibility |
| `icon` | `/v1/dwd-icon` | 7 | No vertical_velocity at pressure levels |
| `ukmo` | `/v1/forecast?models=ukmo_seamless` | 7 | Uses `model_param` field, no precip_probability |
| `meteofrance` | `/v1/meteofrance` | 6 | No precip_probability, freezing_level, visibility, vertical_velocity |
| `gem` | `/v1/gem` | 10 | No freezing_level, visibility, vertical_velocity |

`build_hourly_params()` constructs the API parameter string, excluding each model's unavailable variables. Pressure levels are **per-model** via `ModelEndpoint.pressure_levels`:

| Model | Count | Range | Notes |
|-------|-------|-------|-------|
| GFS / Best-Match | 28 | 1000–150 hPa | 25 hPa spacing below 500, extends to upper atmosphere |
| ECMWF | 11 | 1000–150 hPa | Coarser spacing, includes 250/200/150 |
| ICON | 12 | 1000–300 hPa | 250/200/150 return null |
| Météo-France | 19 | 1000–150 hPa | |
| UKMO | 20 | 1000–150 hPa | Supports vertical_velocity |
| GEM | 20 | 1000–150 hPa | |

### Client Usage

```python
client = OpenMeteoClient()
# Single waypoint, single model (legacy, still available)
forecast = client.fetch_forecast(waypoint, ModelSource.GFS)
# Single waypoint, all models (skips out-of-range)
forecasts = client.fetch_all_models(waypoint, models, days_out=7)
# Multi-point: all route points in one API call per model (preferred)
point_forecasts = client.fetch_multi_point(
    route_points, ModelSource.GFS,
    start_date="2026-02-21", end_date="2026-02-21",
)
```

### Multi-Point Fetch (`fetch_multi_point`)

The pipeline uses `fetch_multi_point()` to consolidate API calls: **1 call per model** with all route points (comma-separated lat/lon), instead of 1 call per waypoint per model.

- Open-Meteo accepts comma-separated `latitude=lat1,lat2,...&longitude=lon1,lon2,...`
- Multi-point response is a `list[dict]`; single-point is a `dict` (handled automatically)
- `start_date`/`end_date` window the time range to the target date (24h instead of full horizon)
- Returns one `WaypointForecast` per point; named waypoints get full airport name from `RoutePoint.waypoint_name`, interpolated points get synthetic IDs like `"RP042"`

### Route Walking (`fetch/route_walk.py`)

Common route generator used by both interpolation and elevation profiling. Yields `(lat, lon, distance_nm, icao, name)` tuples along multi-leg routes at configurable spacing.

- Uses `euro_aip.models.navpoint.NavPoint` for great-circle math (`haversine_distance`, `point_from_bearing_distance`)
- Always includes actual waypoints; interpolated points every `spacing_nm`

### Route Interpolation (`fetch/route_points.py`)

Generates evenly-spaced `RoutePoint` objects along a multi-leg route for cross-section data. Delegates to `walk_route()`.

```python
route_points = interpolate_route(route, spacing_nm=20.0)
# → ~20 RoutePoint objects for a 400nm route
```

- Each point has `distance_from_origin_nm` for cross-section visualization
- Named waypoints included with `waypoint_icao` + `waypoint_name`; interpolated points have `waypoint_icao=None`

### Key Choices

- **Wind in knots** — `wind_speed_unit=kn` for aviation
- **Magnus dewpoint derivation** — when API doesn't provide dewpoint at pressure levels, derived from T + RH using `magnus_dewpoint(temp_c, rh_pct)` (b=17.67, c=243.5)
- **Range filtering** — pipeline skips models where `days_out >= max_days`. Skipped models shown as info-level "skipped" (not "fetch failed") in diagnostics
- **Graceful failure** — individual model failures logged, others continue
- **UKMO model_param** — uses generic `/v1/forecast` with `?models=ukmo_seamless` query param
- **Multi-point over per-waypoint** — reduces API calls from N×M to M; trivially within free-tier rate limits (600/min, 5K/hour)
- **24h time window** — only fetch target date data, not the full 16-day horizon (~150KB vs ~1MB per model)

## Text Forecasts (Route-Aware)

Regional text forecasts are fetched based on route destination. A unified `TextForecasts` container (`fetch/text_forecasts.py`) dispatches to the right source.

```python
from weatherbrief.fetch.text_forecasts import fetch_text_forecasts
text_fcsts = fetch_text_forecasts(route=route)  # → TextForecasts | None
text_fcsts.region        # ForecastRegion.US or .EUROPE
text_fcsts.source_label  # "NWS AFD" or "DWD Synoptic Overview"
text_fcsts.entries       # list[TextForecastEntry(label, text)]
```

**Region detection** (`detect_region(route)`): uses destination (last waypoint) ICAO prefix — `K`/`P` → US, else Europe.

### NWS Area Forecast Discussions (`fetch/nws_text.py`)

US routes get AFDs from aviationweather.gov. AFDs include synoptic discussion, aviation-specific sections, forecaster reasoning, and confidence levels (~6.5KB each).

- **WFO lookup** — `api.weather.gov/points/{lat},{lon}` returns the 3-letter CWA (Weather Forecast Office) code
- **CWA deduplication** — two airports in the same WFO area → one AFD fetch
- **ICAO prefix mapping** — CWA to ICAO station: `K` for CONUS, `PA` for Alaska, `PH` for Hawaii, `TJ` for Puerto Rico
- **AFD fetch** — `aviationweather.gov/api/data/fcstdisc?cwa={ICAO_CWA}&type=af`
- **WFO cache** — JSON file at `{cache_dir}/airport_wfo_cache.json` (ICAO → CWA), loaded/saved once per call
- **User-Agent required** — `api.weather.gov` mandates a UA header
- **Graceful failure** — per-waypoint and per-AFD; 15s timeout

### DWD Synoptic Overviews (`fetch/dwd_text.py`)

European routes get German synoptic overviews from DWD Open Data (free, no API key).

- SXDL31 Kurzfrist (2-3 day), updated 2x daily; SXDL33 Mittelfrist (7-day), updated daily ~10:30 UTC
- **latin-1 encoding** — DWD files use ISO 8859-1, not UTF-8
- **Graceful failure** — each forecast independently None-able
- Text is in German — the LLM translates and synthesizes as part of the digest

## Autorouter GRAMET (`fetch/gramet.py`)

Vertical cross-section images from the Autorouter API (requires euro_aip credentials).

```python
gramet = AutorouterGramet()  # uses AutorouterCredentialManager
data = gramet.fetch_gramet(
    icao_codes=["EGTK", "LFPB", "LSGS"],
    altitude_ft=8000,
    departure_time=dt,
    duration_hours=4.5,
    format="pdf",  # PDF for better cloud rendering; PNG fallback available
)  # → bytes (PDF)
```

- Auth via Bearer token from `euro_aip.utils.autorouter_credentials`
- **Per-user credentials**: in multi-user mode, each user's encrypted autorouter credentials are loaded from the DB and passed to `AutorouterGramet(username=..., password=...)`. A per-user token cache dir (`data/.cache/autorouter/{user_id}/`) prevents users from sharing cached OAuth tokens.
- API params: waypoints (space-separated), altitude (feet), departuretime (Unix), totaleet (seconds)

## Elevation Profile (`fetch/elevation.py`)

High-resolution terrain elevation along a route using SRTM3 data (90m resolution).

```python
from weatherbrief.fetch.elevation import get_elevation_profile
profile = get_elevation_profile(route)
# → ElevationProfile with ~800 points at 0.5nm spacing for 400nm route
```

- Uses `srtm.py` library with data cached in `data/.cache/srtm/` (Docker volume-mounted)
- Walks route via `walk_route(route, spacing_nm=0.5)` for terrain-grade resolution
- Returns `ElevationProfile` with `max_elevation_ft`, `total_distance_nm`, per-point `(distance_nm, elevation_ft, lat, lon)`
- Saved as `elevation_profile.json` in the pack directory
- Runs early in the pipeline (before fetch) since it doesn't depend on NWP data

## Model Freshness (`fetch/model_status.py`)

Checks whether NWP models have published new initialization runs since the last fetch.

```python
from weatherbrief.fetch.model_status import check_freshness
result = check_freshness(last_pack_init_times)
# → {"fresh": False, "stale_models": ["gfs"], "model_init_times": {...}}
```

- Queries Open-Meteo metadata API for current model init times (GFS, ECMWF, ICON, UKMO, MeteoFrance)
- Compares against `model_init_times` stored on the previous pack
- DWD text forecasts checked on assumed update schedule (06:00/18:00 UTC short-range, 10:30 UTC medium-range)
- `compute_next_update()` estimates when the next model run will be available
- Smart refresh in the API: skips pipeline if all models are still fresh

## GRIB2 Enrichment (`fetch/grib/`)

GRIB2 enrichment for Cloud Liquid Water, Ice Mixing Ratio, and cloud diagnostics — variables not available from Open-Meteo. Supports GFS (via NOAA S3) and ICON-EU (via DWD opendata). Enabled via `BriefingOptions.enrich_grib=True` (always on in API, opt-in via `--enrich-grib` in CLI).

```python
from weatherbrief.fetch.grib import enrich_forecasts
enrich_forecasts(cross_sections, all_forecasts, route_points,
                 target_date, target_hour, data_dir=data_dir)
# Modifies PressureLevelData in-place: sets cloud_liquid_water_kg_kg, ice_mixing_ratio_kg_kg
# Also attaches NWPCloudDiagnostics and overrides cloud cover for model-run consistency
```

### Architecture

| Module | Purpose |
|--------|---------|
| `gfs_idx.py` | Parse `.idx` files, plan HTTP byte ranges for CLMR/ICMR and cloud diagnostic variables |
| `grib_fetch.py` | Find latest GFS run, bracket forecast hours, download via HTTP Range from S3 |
| `icon_eu_fetch.py` | Find latest ICON-EU run, download model-level (QC/QI/P) and single-level diagnostics from DWD. Per-variable download for memory-efficient chunked decode |
| `icon_eu_levels.py` | Log-pressure interpolation from ICON-EU model levels to pressure levels |
| `decode.py` | cfgrib → xarray decode, bilinear interpolation to route points. Chunked ICON-EU decoder (`decode_icon_eu_per_point_chunked()`) processes one variable at a time with explicit `gc.collect()` between — peak ~270MB vs ~800MB |
| `fill.py` | Forward-fill GRIB-enriched fields across time axis (cloud diagnostics + microphysics) |
| `cache.py` | Disk cache per model (`data/.cache/grib/{model}/{date}_{cycle}z/`) with 48h TTL |

### How It Works

**GFS enrichment** (`_enrich_gfs()`):
1. Find latest available GFS cycle (00z/06z/12z/18z) — HEAD request on `.idx` file
2. Bracket target time between two forecast hours (1-hourly f000–f120, 3-hourly f120–f384)
3. Parse `.idx` to find byte offsets for CLMR + ICMR at all pressure levels + cloud diagnostics
4. Download via HTTP Range requests from `noaa-gfs-bdp-pds.s3.amazonaws.com` (public, no auth)
5. Decode with cfgrib → xarray, bilinear interpolation to each route point
6. Merge CLWMR/ICMR into `PressureLevelData` objects in-place
7. Attach `NWPCloudDiagnostics` and override Open-Meteo cloud cover with GRIB values

**ICON-EU enrichment** (two-phase sequential decode for memory safety):
1. Check route is within ICON-EU domain (29.5–70.5°N, 23.5°W–62.5°E) — silently skip if outside
2. Find latest ICON-EU run (3h cycles, ~3h publication delay)
3. **Phase 1 (parallel with GFS):** Download model-level files (QC/QI/P levels 35–74) and single-level diagnostics (CEILING, CLCL/CLCM/CLCH/CLCT, HBAS_CON, HTOP_CON) to disk cache
4. **Phase 2 (after GFS completes):** Chunked decode — process one variable at a time via `decode_icon_eu_per_point_chunked()`, explicitly freeing memory between variables. Log-pressure interpolate to ICON pressure levels
5. Merge QC→CLWMR, QI→ICMR into pressure-level data
6. Attach `NWPCloudDiagnostics` and override cloud cover (only if GFS hasn't already enriched the point)

**Cloud cover override strategy:** When GRIB diagnostics are available, `_apply_cloud_diagnostics()` overwrites the Open-Meteo `cloud_cover_low/mid/high_pct` values with GRIB-native values. This ensures all cloud data for a given model comes from the same initialization run. GFS takes priority over ICON-EU for points where both provide data.

**Gap-filling for GRIB-enriched fields:** GRIB enrichment only targets native model forecast hours (e.g. every 3h for GFS at longer lead times), and some grid cells may return None. Two gap-filling passes ensure all route points and hours have data:

1. **Time axis — forward-fill** (`fetch/grib/fill.py`): After all GRIB enrichment completes, `propagate_all()` forward-fills from the preceding native hour for both cloud diagnostics (`nwp_cloud_diagnostics`) and microphysics (`cloud_liquid_water_kg_kg`, `ice_mixing_ratio_kg_kg`). Cloud geometry and microphysics change slowly between 3h GFS steps.

2. **Spatial axis — linear interpolation** (`analysis/spatial_interpolation.py`): Before sounding analysis, `interpolate_all_spatially()` fills gaps where a route point's GRIB grid cell returned None by linearly interpolating in distance-space from left/right neighbors. Applies to both CLW/ICMR (per-pressure-level) and cloud diagnostics (all numeric sub-fields). Requires both neighbors; max gap 100 nm; edge gaps skipped.

3. **Vertical axis — linear in pressure** (`analysis/sounding/__init__.py`): During sounding analysis, `_interpolate_cloud_water()` fills intermediate pressure levels (25-hPa spacing) between native GRIB levels (50-hPa spacing) by linear interpolation in pressure-space. Only applies to CLW/ICMR.

**When adding new GRIB-enriched fields:** add a forward-fill in `fill.py` (time axis) and a spatial interpolation function in `spatial_interpolation.py` (spatial axis). If the field is per-pressure-level, also add vertical interpolation in the sounding analysis.

### Key Choices
- **GFS + ICON-EU** — GFS for global coverage, ICON-EU for higher-resolution European data (~6.5km vs ~27km)
- **GFS takes priority** — if GFS already attached diagnostics, ICON-EU skips that point (avoids contradictory overrides)
- **Cloud cover override** — eliminates model-run mismatches between Open-Meteo (which may lag) and GRIB (latest run)
- **Per-point interpolation** — `decode_grib_per_point()` / `decode_icon_eu_per_point()` return values per route point
- **Graceful degradation** — enrichment failure logged but pipeline continues with Open-Meteo data only
- **Init time tracking** — `enrich_forecasts()` returns `(grib_init_times, grib_skip_reasons)`. `grib_init_times: dict[str, int]` maps model names to Unix timestamps. `grib_skip_reasons: dict[str, str]` explains why models were skipped (e.g., `"out_of_range"`). Stored in `BriefingPackMeta` and displayed in the freshness bar
- **Two-phase sequential decode** — GFS download+decode runs in parallel with ICON-EU download-only. ICON-EU decode runs after GFS completes to prevent OOM (ICON-EU peaks at ~270MB per variable during cfgrib decode)

### Gotchas
- **cfgrib uses lazy loading** — temp file must stay alive through interpolation, not just `open_datasets()`. Deleting too early causes `FileNotFoundError`.
- GFS S3 bucket has ~4.5h delay after init time before data is available
- ICON-EU: model levels (not pressure levels) — requires P field for vertical interpolation
- ICON-EU: bz2-compressed individual files, ~240 files per enrichment — parallel download essential
- Cache is shared across users (same model run = same data)

## Gotchas

- ECMWF now has relative_humidity and dewpoint at pressure levels, plus vertical_velocity
- Open-Meteo API returns flat arrays keyed by variable name, indexed by time step
- DWD URLs use `_LATEST` suffix for most recent version of each forecast type

## References

- Variable definitions: `fetch/variables.py`
- GRIB2 enrichment: `fetch/grib/`
- Data models: [data-models.md](./data-models.md)
- Analysis consumers: [analysis.md](./analysis.md)
