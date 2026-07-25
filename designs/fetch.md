# Fetch Layer

> Weather data retrieval: Open-Meteo multi-model, DWD text forecasts, Autorouter GRAMET, SRTM elevation, model freshness

## Intent

Centralize all external data fetching with graceful failure handling. Each data source may be unavailable; the pipeline continues with whatever succeeds.

## Open-Meteo Client (`fetch/open_meteo.py`)

Multi-model NWP data from the free Open-Meteo API.

### Model Endpoints (`fetch/variables.py`)

Each model has a `ModelEndpoint` dataclass (`name`, `base_url`, `max_days`, `model_param`, `unavailable_surface`, `unavailable_pressure`, `pressure_levels`, `default`, `region`, `required_icao_prefixes`) in `MODEL_ENDPOINTS`. There are **6** endpoints (no `best_match` — Open-Meteo's auto-blend was dropped):

| Model | Endpoint | Max Days | Region / gate | Notes |
|-------|----------|----------|---------------|-------|
| `ecmwf` | `/v1/ecmwf` | 10 | global, default | No freezing_level, visibility, lifted_index. vertical_velocity now available |
| `gfs` | `/v1/gfs` | 16 | global, default | NCEP GFS — full surface + pressure-level set |
| `icon` | `/v1/dwd-icon` | 7 | europe, default | No convective_inhibition, lifted_index; no vertical_velocity at pressure levels |
| `ukmo` | `/v1/forecast?models=ukmo_seamless` | 7 | europe, needs `EG…` | Uses `model_param`; no precip_probability, lifted_index |
| `meteofrance` | `/v1/meteofrance` | 6 | europe, needs `LF…` | No precip_probability, freezing_level, visibility, convective_inhibition, lifted_index, vertical_velocity |
| `gem` | `/v1/gem` | 10 | north_america | No freezing_level, visibility, convective_inhibition, lifted_index, vertical_velocity |

`region` (`ModelRegion.GLOBAL/EUROPE/NORTH_AMERICA`) and `required_country` let the pipeline skip models irrelevant to a route (`_should_skip_for_region` in `tasks/fetch.py`). The broad region comes from `detect_model_region(route)`, which classifies on the **origin/destination** ICAO prefixes only (`K`/`C`/`P` → North America) — intermediate nav fixes like `KONAN`/`CINDY` are ignored so they can't misclassify a route. Country-specific models (Météo-France→`FR`, UKMO→`GB`) are gated on `airports.route_countries(route)`, which samples the great-circle route into timezone polygons and so detects genuine **overflight**, not just landings. If that geometry can't be resolved it falls back to `route_covers_prefixes(route, prefixes)` (4-letter ICAO airports only).

`build_hourly_params()` constructs the API parameter string, excluding each model's unavailable variables. Pressure levels are **per-model** via `ModelEndpoint.pressure_levels` (named constants in `variables.py`):

| Model | Count | Constant | Notes |
|-------|-------|----------|-------|
| GFS | 28 | `EXTENDED_PRESSURE_LEVELS` | 25 hPa spacing below 500, up to 150 hPa |
| ECMWF | 13 | `ECMWF_PRESSURE_LEVELS` | 1000–50 hPa, no intermediate levels (OM limitation) |
| ICON | 19 | `ICON_PRESSURE_LEVELS` | 1000–30 hPa; GRIB enrichment interpolates to extended levels |
| Météo-France | 19 | `METEOFRANCE_PRESSURE_LEVELS` | 1000–150 hPa |
| UKMO | 20 | `UKMO_PRESSURE_LEVELS` | 1000–150 hPa, supports vertical_velocity |
| GEM | 20 | `GEM_PRESSURE_LEVELS` | 1000–150 hPa |

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
- **Chunking**: requests are split into batches of `_MAX_POINTS_PER_REQUEST = 150` points. Open-Meteo supports ~400 locations, but the expanded pressure-level parameter list makes URLs long. Normal routes (50-80 points) fit in a single chunk; the limit is a safety net for unusually long routes or standalone verification batches.
- Multi-point response is a `list[dict]`; single-point is a `dict` (handled automatically via type check)
- `start_date`/`end_date` window the time range to the target date (24h instead of full horizon)
- Returns one `WaypointForecast` per point; named waypoints get full airport name from `RoutePoint.waypoint_name`, interpolated points get synthetic IDs like `"RP042"`

### Retry & Rate Limiting

`_get_with_retry()` handles Open-Meteo 429 and 5xx responses with a generous backoff schedule:

- **Max retries**: `_MAX_RETRIES = 4`, with `_RETRY_BACKOFF = [10, 30, 60, 90]` seconds (generous for expanded pressure-level requests)
- Respects `Retry-After` header from server if present; falls back to backoff schedule otherwise
- Retries on `_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}` — rate-limit and transient server errors; other HTTP errors propagate immediately
- Logging includes attempt count, wait time, and truncated URL for debugging
- `_prepare_request()` swaps the host for historical (`historical-forecast-api`) or customer-API-key (`customer-api`) modes; `get_json()` is the public GET-with-retry entry used by callers building their own URLs (e.g. frontal grid fetch)

### Route Walking (`fetch/route_walk.py`)

Common route generator used by both interpolation and elevation profiling. Yields `(lat, lon, distance_nm, icao, name)` tuples along multi-leg routes at configurable spacing.

- Uses `euro_aip.models.navpoint.NavPoint` for great-circle math (`haversine_distance`, `point_from_bearing_distance`)
- Always includes actual waypoints; interpolated points every `spacing_nm`

### Route Interpolation (`fetch/route_points.py`)

Generates evenly-spaced `RoutePoint` objects along a multi-leg route for cross-section data. Delegates to `walk_route()`.

```python
route_points = interpolate_route(route, spacing_nm=10.0)  # default 10nm
# → ~40 RoutePoint objects for a 400nm route
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
- **Chunking at 150 points** — conservative limit avoids 414 URI Too Large; most routes fit in one chunk
- **Generous retry backoff** — [10, 30, 60, 90]s schedule for 429s; respects server Retry-After header
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

## Autorouter GRAMET (`tasks/outputs.py:run_gramet()`)

Vertical cross-section PDF from the Autorouter API. The fetch lives in `tasks/outputs.py` (there is no longer a `fetch/gramet.py`); the client itself is `euro_aip.briefing.sources.autorouter_gramet.AutorouterGrametSource` — push the complexity into the library, not weatherbrief.

```python
result = run_gramet(
    route, departure_time, pack_dir=..., data_dir=...,
    autorouter_token=token, user_id=user_id,
)  # → GrametResult(path=..., fetched=True) | GrametResult(diagnostic=...)
# internally:
cred_manager = AutorouterCredentialManager(cache_dir)
cred_manager.set_token(autorouter_token)
gramet_client = AutorouterGrametSource(cred_manager)
data = gramet_client.fetch_gramet(
    waypoints=[wp.icao for wp in route.waypoints],
    altitude_ft=route.cruise_altitude_ft,
    departure_time=departure_time,
    duration_hours=route.flight_duration_hours or 2.0,
    fmt="pdf",
)  # → bytes (PDF)
```

- Auth via a pre-obtained `autorouter_token` set on `AutorouterCredentialManager` (no DB username/password in the fetch path)
- **Per-user token cache**: when `user_id` + `data_dir` are present, the cache dir is `{data_dir}/.cache/autorouter/{user_id}/` so users don't share cached OAuth tokens; otherwise `~/.cache/weatherbrief`
- No token → `GrametResult()` (expected user state, no diagnostic). Output written to `{pack_dir}/gramet.pdf` (or a dated `data_dir/gramet/...` path). Missing euro_aip → info diagnostic; fetch error → warn diagnostic with `GrametCode`

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

## Model Freshness (`fetch/freshness/`)

Marker-based per-(model, source) staleness decision used by `/packs/freshness` and the auto-refresh scheduler. Replaces the old per-call `meta.json` fan-out — most freshness HTTP calls are now pure compute (no I/O).

A 5-min background loop populates an in-memory `MarkerStore`. The HTTP-side check is `api/packs._build_data_status(pack, flight)`, which compares each `(model, source)` recorded on the pack against the matching marker — horizon-aware, so a new run only flags stale if its forecast horizon actually covers the flight's end time.

```python
# Used by /packs/freshness handler + scheduler auto-refresh:
status = _build_data_status(pack, flight)
if not status.fresh:
    schedule_refresh()
```

Ten tracked source/model pairs (`SOURCE_REGISTRY` in `fetch/freshness/registry.py`): 4 direct GRIB (`ecmwf:direct`, `gfs:noaa`, `icon_eu:dwd`, `icon_d2:dwd`) + 6 Open-Meteo republishes (`gfs/ecmwf/icon/meteofrance/ukmo/gem:openmeteo`). Each `SourceConfig` carries schedule (cycles, delivery_offset, horizon) plus descriptive metadata (model/provider label, role, resolution, coverage, pressure_levels) feeding `/api/data-sources` and the help-page table.

The legacy `fetch/model_status.py` module (`fetch_model_metadata`, `check_freshness`, `compute_next_update`) is no longer consumed by the freshness endpoint's per-request path. `fetch_model_metadata` survives as a direct Open-Meteo init-time probe: it backs the Open-Meteo marker population in `fetch/freshness/sources.py` (background loop, not per-call) and is called directly by `tasks/alternates.py`, `tasks/standalone_verification.py`, `hewson/precompute.py`, `frontal/` (grid + CLI), and pack building in `api/packs.py`.

→ Full doc: [freshness-markers.md](./freshness-markers.md)

## GRIB2 Enrichment (`fetch/grib/`)

GRIB2 enrichment for Cloud Liquid Water, Ice Mixing Ratio, and cloud diagnostics — variables not available from Open-Meteo. Supports GFS (via NOAA S3), ICON-EU (via DWD opendata), and ECMWF IFS (via ECPDS local delivery). Enabled via `BriefingOptions.enrich_grib=True` (always on in API, opt-in via `--enrich-grib` in CLI).

```python
from weatherbrief.fetch.grib import enrich_forecasts
grib_init_times, grib_skip_reasons = enrich_forecasts(
    cross_sections, all_forecasts, route_points, departure_time,
    data_dir=data_dir, flight_duration_hours=4.5,
    as_of_time=None, priority=None,  # priority defaults to the _DECODE_PRIORITY ContextVar
)
# Modifies PressureLevelData in-place: sets cloud_liquid_water_kg_kg, ice_mixing_ratio_kg_kg
# Also attaches NWPCloudDiagnostics and overrides cloud cover for model-run consistency
```

### Architecture

| Module | Purpose |
|--------|---------|
| `gfs_idx.py` | Parse `.idx` files, plan HTTP byte ranges for CLMR/ICMR and cloud diagnostic variables |
| `grib_fetch.py` | Find latest GFS run, bracket forecast hours, download via HTTP Range from S3 |
| `icon_eu_fetch.py` | Find latest DWD ICON run + download model-level (QC/QI/P) and single-level diagnostics. Parametrized by `IconVariant` (`ICON_EU` / `ICON_D2`) — one code path, two variants differing in domain, cycles, horizon, level slice, filename conventions, cache slug and freshness source key (#456). Per-variable download for memory-efficient chunked decode. D2 additionally lists `explicit_conv_variables` (dbz_ctmax/echotop/lpi_max/w_ctmax/uh_max, #462; `grau_gsp` dropped #468) fetched into per-variable blobs; `needs_predecessor_step` generalizes the f(H−1) lead-fetch gate (rain_con de-accum on EU; echo-top quarters on D2) |
| `icon_eu_levels.py` | Log-pressure interpolation from ICON-EU model levels to pressure levels |
| `ecmwf_fetch.py` | Parse ECPDS filenames, scan delivery directory, find latest run. No HTTP — files land on local disk via ECPDS push |
| `decode.py` | cfgrib → xarray decode, bilinear interpolation to route points. Chunked ICON-EU decoder (`decode_icon_eu_per_point_chunked()`) processes one variable at a time with explicit `gc.collect()` between — peak ~270MB vs ~800MB. ECMWF decoders handle multi-grid files (first-wins per point). Separate eccodes **message-level** path for D2 explicit-convection fields (`decode_icon_d2_explicit_conv_per_point`, #462): stepRange-in-minutes selection + corridor-extremum reduction (never cfgrib merge, never bilinear); also `build_d2_validity_mask`/`d2_corridor_fully_valid` for the hardened domain gate |
| `decode_worker.py` | Thin process-pool worker entry points (Phase B-3): serialisable args only, GRIB bytes read from disk inside the worker. Escapes the GIL contention between concurrent `enrich_forecasts()` calls |
| `ecmwf_watcher.py` | Background scanner: writes `.ready_{date}_{cycle}z` sentinel files when an ECMWF delivery run is complete. Manifest from `delivery_config.json` co-located with the data |
| `precache.py` | Pre-warms ICON-EU/GFS byte-range cache for the `/maps.html` D-0..D-3 airport-profile selectables (issue #126) plus the ICON-D2 cache for upcoming flights (#469). Shares the per-flight disk cache so warmed runs also speed briefings. Warming is discretionary: it runs only inside the per-model wall-clock window (`MODEL_WARMING_WINDOW_UTC`, #475) and **yields to interactive briefings** — `interactive_refresh_active()` polls `refresh_registry.idle_seconds()` between units of work (per variable for EU, per forecast hour for GFS, per flight for D2) and the pass returns `deferred=1` so the scheduler leaves `last_done` unset and the next 5-min tick resumes (#490) |
| `fill.py` | Time-axis fill of GRIB-enriched fields. Linear interp for GFS cloud diagnostics (window-midpoint, see meteorology-decisions §3) and CLW/ICMR when `gfs_init` is provided; forward-fill for ICON-EU/ECMWF and the GFS fallback path. Also hosts `apply_gfs_rh_condensate_gate` — drops GFS phantom layers where pressure-level RH and condensate disagree with the averaged cover |
| `cache.py` | Disk cache per model (`data/.cache/grib/{model}/{date}_{cycle}z/`). Per-model TTL via `MODEL_TTL_SECONDS` (GFS 24h, ICON-EU 12h; 12h fallback) — ECMWF reads local ECPDS disk so it isn't cached here |

### How It Works

**GFS enrichment** (`_enrich_gfs()`):
1. Find latest available GFS cycle (00z/06z/12z/18z) — HEAD request on `.idx` file
2. Bracket target time between two forecast hours (1-hourly f000–f120, 3-hourly f120–f384)
3. Parse `.idx` to find byte offsets for CLMR + ICMR at all pressure levels + cloud diagnostics
4. Download via HTTP Range requests from `noaa-gfs-bdp-pds.s3.amazonaws.com` (public, no auth)
5. Decode with cfgrib → xarray, bilinear interpolation to each route point
6. Merge CLWMR/ICMR into `PressureLevelData` objects in-place
7. Attach `NWPCloudDiagnostics` and override Open-Meteo cloud cover with GRIB values

**ICON enrichment — EU or D2** (two-phase sequential decode for memory safety):
0. **Variant gate (#456, hardened #462):** if the *whole* route fits the ICON-D2 domain (43.18–58.08°N, 3.94°W–20.34°E) AND a complete D2 run's 48h horizon reaches the flight-window end AND the route's entire ~10 NM corridor buffer lies in valid (unmasked) D2 cells (`_d2_corridor_mask_ok` — the regular-ll product bitmap-masks ~17% corner cells; the mask is built once from a delivered message's bitmap and cached as `icon-d2-validity-mask-v1.npz`, failing OPEN to the bbox gate when unobtainable), source the icon slot from **ICON-D2** (2.2 km); otherwise **ICON-EU** (6.5 km). All-or-nothing, never a per-point mix. `_prepare_icon_eu` resolves the D2 run inline while gating so it never picks D2 without a usable run; on total D2 failure the slot re-runs cleanly on ICON-EU. The chosen source is reported out of `enrich_forecasts` via `grib_sources` and recorded on the pack (`model_sources["icon"]` = `icon_eu:dwd` / `icon_d2:dwd`) so the freshness bar can badge `ICON (D2)`.
1. Check route is within the chosen variant's domain — silently skip if outside
2. Find latest run (3h cycles; EU ~3h / D2 ~2h publication delay)
3. **Phase 1 (parallel with GFS):** Download model-level files (QC/QI/P levels 35–74) and single-level diagnostics (CEILING, CLCL/CLCM/CLCH/CLCT, HBAS_CON, HTOP_CON) to disk cache. D2 also downloads the explicit-convection fields (`IconVariant.explicit_conv_variables`: dbz_ctmax, echotop, lpi_max, w_ctmax, uh_max; `grau_gsp` dropped #468) into **per-variable** blobs (`ICON_D2_EXPL_<VAR>_V1` cache keys — the decoder must know which field each blob holds, and several are multi-message sub-hourly files). The predecessor-step fetch is gated by the variant-level `needs_predecessor_step` flag (EU: rain_con de-accumulation; D2: the prior file's three echo-top quarter-windows)
4. **Phase 2 (after GFS completes):** Chunked decode — process one variable at a time via `decode_icon_eu_per_point_chunked()`, explicitly freeing memory between variables. Log-pressure interpolate to ICON pressure levels
5. Merge QC→CLWMR, QI→ICMR into pressure-level data
6. Attach `NWPCloudDiagnostics` and override cloud cover (only if GFS hasn't already enriched the point)
7. **D2 only (#462):** `_enrich_icon_d2_explicit_convective` decodes the explicit-conv blobs via `decode_icon_d2_explicit_conv_per_point` — eccodes **message-level** decode selecting messages by `stepRange` in minutes (never a cfgrib blob merge: echotop files carry 4×15-min `min_pres` windows), reducing each field to a **corridor extremum** over a ~10 NM route buffer (max; min-pressure for echo top; signed argmax|uh|) instead of per-point bilinear sampling (a 2.2 km cell between 10 NM route points would otherwise vanish). The loop then constructs the hourly echo top as the min over exactly the four quarter-windows ending in `(H−1, H]` (a missing quarter degrades `echo_top_complete` — never a partial min), converts Pa→ft against the hour's own geopotential column — extrapolating along the nearest two levels for an echo above the aviation slice rather than switching datum to ISA mid-field, ISA only as the <2-levels fallback (#465 idea, meteorology-decisions §19 rule 1) — and attaches `NWPExplicitConvectiveDiagnostics` to matching hours. These fields are deliberately **excluded** from the time-axis fill and spatial interpolation (registered skips in `fill.py` / `spatial_interpolation.py`): corridor extrema are already spatial reductions, dBZ is logarithmic, and a failed hour's 1-h interval max has no covering interval to hold over — it stays honestly unavailable via the completeness flags

**ECMWF IFS enrichment** (`_enrich_ecmwf()`):
1. Scan the ECPDS delivery directory for ECMWF GRIB files (local disk, no HTTP)
2. Parse filenames to extract run metadata (model, base time, step, a1/a2 part)
3. Pick the latest operational run; select steps via `_select_ecmwf_window_steps` — everything within the ±3h `ECMWF_FLIGHT_WINDOW_MARGIN`, **union the immediately bracketing steps** (latest at/before departure, earliest at/after the window end). The bracket union is what makes this cadence-independent: ECMWF is hourly to +90h, 3-hourly to +144h, then 6-hourly to +168h, and a fixed margin alone cannot retain two anchors in the 6-hourly range (#483). The margin constant stays 3h so `tasks/time_scan.py`'s coverage rule window can only under-claim relative to what was decoded, never over-claim
4. **Pressure levels (a2 files):** Decode clwc/ciwc/cc at 25 pressure levels per point. Multi-grid files (main Europe + Nordic extension) handled via first-wins: each point uses whichever sub-grid covers it
5. **Surface diagnostics (a1 files):** Decode ceil, cbh, lcc/mcc/hcc/tcc, `hcct` (convective cloud top), `deg0l` (freezing level). Heights in meters (9999m = no-cloud sentinel → None). Fractions 0–1 → converted to 0–100%.
6. Merge cloud water into ECMWF cross-sections; attach `NWPCloudDiagnostics`. When `deg0l` is present, `_apply_cloud_diagnostics()` also overwrites `hourly.freezing_level_m` with the GRIB-native value so `indices.nwp_freezing_level_ft` carries the model's own freezing level rather than Open-Meteo's.

**Cloud cover field separation:** `_apply_cloud_diagnostics()` attaches a typed `NWPCloudDiagnostics` payload to `hourly.nwp_cloud_diagnostics` and never writes the bulk `hourly.cloud_cover_low/mid/high_pct` fields — those keep the Open-Meteo hourly-interpolated values across both native GRIB steps and gap hours. Consumers that need GRIB-derived cover read `nwp_cloud_diagnostics.{low,mid,high}.cover_pct`. The two are deliberately separate so the OM bulk values remain available as a temporally-smooth comparison signal alongside the GRIB-anchored per-band layer geometry. GFS still takes priority over ICON-EU for points where both provide diagnostics. The only Open-Meteo field that GRIB cloud-diag enrichment overrides is `freezing_level_m` (from ECMWF `deg0l`).

**Gap-filling for GRIB-enriched fields:** GRIB enrichment only targets native model forecast hours (e.g. every 3h for GFS at longer lead times), and some grid cells may return None. Two gap-filling passes ensure all route points and hours have data:

1. **Time axis** (`fetch/grib/fill.py`): After all GRIB enrichment completes, `propagate_all()` fills gap hours between native steps. The strategy depends on the field and source:
   - **GFS cloud diagnostics** — when `gfs_init` is provided, `_fill_cloud_diagnostics` uses **window-midpoint linear interpolation** (`_interp_gfs_diag_hourly`) for low/mid/high cover. GFS publishes both instantaneous and time-averaged LCDC/MCDC/HCDC; the idx parser selects the **averaged** form (`_PREFER_AVERAGED_PAIRS`) so cover matches its averaged-only base/top/temp geometry (#441), so the value at each native step is anchored at `step - window_length/2` and interpolated between neighbouring midpoints. Layer geometry (base/top/temp) is held over from the higher-cover endpoint; sub-5 % covers drop the layer entirely. Boundary-layer cover is averaged-only and is likewise window-midpoint aligned; convective, total cover, and ceiling are instantaneous and use step-time anchoring. See [meteorology-decisions §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate) for the rationale.
   - **GFS CLW/ICMR overlay** — when `gfs_init` is provided, `_fill_cloud_water` uses step-time linear interp via `_interp_gfs_clw_hourly` (instantaneous mixing ratios — no midpoint adjustment).
   - **ICON-EU / ECMWF cloud diagnostics** and the **GFS fallback path** (no `gfs_init`) — forward-fill from the preceding native hour. ICON-EU and ECMWF publish instantaneous cover; forward-fill is the right persistence semantic.
   - **ECMWF / ICON-EU pressure-level soundings** — `_linear_interp_pressure_levels` rebuilds the full `pressure_levels` list on gap hours via per-level linear interp; dewpoint is derived from interpolated (T, RH) via Magnus rather than interpolated directly.
   - **ECMWF surface scalars** — `_linear_interp_ecmwf_surface` linearly interpolates the genuinely-instantaneous scalars (temperature, dewpoint, visibility, CAPE, pressure) between GRIB anchors (identified via `nwp_cloud_diagnostics`). Wind speed+direction are interpolated as **U/V components** (`_lerp_wind`, with a calm-speed gate) rather than scalar speed + circular direction; the gust (`10fg`) is a window **maximum**, so it is held over the covering interval, not linearly interpolated (#441).

   After `propagate_all`, **`apply_gfs_rh_condensate_gate`** runs on GFS sections: each low/mid/high band is dropped when its pressure-level `max(RH)` is below the per-band threshold (`_GFS_GATE_RH_LOW_PCT` = 60, `_GFS_GATE_RH_MID_PCT` = 70, `_GFS_GATE_RH_HIGH_PCT` = 70) AND `sum(CLMR + ICMR)` within `[base_ft, top_ft]` is zero. This protects against averaged-window phantom layers that survive interpolation — see [meteorology-decisions §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate).

2. **Spatial axis — linear interpolation** (`analysis/spatial_interpolation.py`): Before sounding analysis, `interpolate_all_spatially()` fills gaps where a route point's GRIB grid cell returned None by linearly interpolating in distance-space from left/right neighbors. Applies to both CLW/ICMR (per-pressure-level) and cloud diagnostics (all numeric sub-fields). Requires both neighbors; max gap 100 nm; edge gaps skipped.

3. **Vertical axis — linear in pressure** (`analysis/sounding/__init__.py`): During sounding analysis, `_interpolate_cloud_water()` fills intermediate pressure levels (25-hPa spacing) between native GRIB levels (50-hPa spacing) by linear interpolation in pressure-space. Only applies to CLW/ICMR.

**When adding new GRIB-enriched fields:** add a time-axis fill in `fill.py` (linear interp where the field is meaningfully continuous, forward-fill where persistence is the right semantic) and a spatial interpolation function in `spatial_interpolation.py` (spatial axis). If the field is per-pressure-level, also add vertical interpolation in the sounding analysis.

### Key Choices
- **GFS + ICON-EU + ECMWF IFS** — GFS for global coverage, ICON-EU for higher-resolution European data (~6.5km vs ~27km), ECMWF IFS for model-native cloud microphysics on ECMWF cross-sections
- **GFS takes priority** — if GFS already attached diagnostics, ICON-EU skips that point (avoids contradictory overrides)
- **ECMWF enriches its own model only** — ECMWF GRIB data is only applied to ECMWF cross-sections, never mixed with GFS/ICON data
- **Cloud cover override** — eliminates model-run mismatches between Open-Meteo (which may lag) and GRIB (latest run)
- **Per-point interpolation** — `decode_grib_per_point()` / `decode_icon_eu_per_point()` / `decode_ecmwf_pressure_per_point()` return values per route point
- **Graceful degradation** — enrichment failure logged but pipeline continues with Open-Meteo data only
- **Init time tracking** — `enrich_forecasts()` returns `(grib_init_times, grib_skip_reasons)`. `grib_init_times: dict[str, int]` maps model names (`"gfs"`, `"icon"`, `"ecmwf"`) to Unix timestamps. `grib_skip_reasons: dict[str, str]` explains why models were skipped (e.g., `"out_of_range"`). Stored in `BriefingPackMeta` and displayed in the freshness bar. `model_init_times` keeps each model's Open-Meteo run time (including ECMWF); `grib_init_times` carries the parallel GRIB run time so the freshness bar can annotate `ECMWF <om-run> (GRIB <grib-run>)` when the two differ — ECPDS delivery typically leads Open-Meteo for ECMWF.
- **Two-phase sequential decode** — GFS download+decode runs in parallel with ICON-EU download-only and ECMWF disk decode. ICON-EU decode runs after GFS completes to prevent OOM (ICON-EU peaks at ~270MB per variable during cfgrib decode)
- **Per-fhour cleanup in GRIB enrichment loops** — `_enrich_clwmr_icmr` (GFS cloud water), `_enrich_cloud_diagnostics` (GFS cloud diag), and `_enrich_icon_eu_cloud_diagnostics` all `del decoded_points; gc.collect()` between fhour iterations. Without this, dicts accumulate across the loop and contribute to OOM on long-route briefings (84+ route points × multiple fhours). Diagnostics arrays are deleted alongside in the diag loops. **Exception:** `_decode_and_merge_icon_eu` and `_enrich_ecmwf_inner` fan out *all* fhour/step decodes to the pool in parallel before merging (issue #133), so peak memory is `n_workers × per-fhour RSS` during decode. This is the intentional trade-off behind the speed-up; the default of 2 workers keeps peak resident bounded (~+125 MB ICON peak vs. sequential). Each fhour's decoded dict is dropped immediately after its merge; a single `_grib_gc()` at the end of the merge loop reclaims them. Raise via `GRIB_DECODE_WORKERS` on hosts with spare RAM.
- **Warm passes yield to interactive briefings (#490)** — the cgroup sits near its limit because warm cache writes are charged to it, so a concurrent anon-memory spike from a briefing's decode workers has no reclaim headroom (2026-07-23 05:09Z: OOM killer took the container down mid-refresh). The warm loop therefore checks `precache.interactive_refresh_active()` between units of work and bails out; the gate is True while `api/packs.py::refresh_registry` has anything queued/refreshing (user *or* scheduler-triggered) and for `WARM_YIELD_COOLDOWN_SECONDS` (60 s) after the last one finishes, so warming doesn't restart into the tail of a refresh. A bailed-out pass reports `deferred=1` and `run_grib_precache_loop` skips both `last_done` and the cap walk, so the next tick resumes the same run — every already-fetched combo is an `is_cached` skip, making resumption near-free. Complements the two shipped mitigations (fsync + `POSIX_FADV_DONTNEED` on `put_cached`; halved prefetch units + connection pool for warms).
- **Parallel decode dispatch** — `_dispatch_decode_parallel(jobs)` submits a batch of `(worker_fn_name, args)` jobs to the pool concurrently and returns results in input order. Falls back to sequential in-process execution when `GRIB_DECODE_WORKERS=0`. Use this whenever a decode loop processes independent fhours/steps; sequential `_dispatch_decode` calls in a `for` loop only ever exercise one pool worker. The standalone ECMWF and GFS/ICON cloud-diag loops were switched onto it in #459. An optional `max_inflight=<int>` throttles concurrency *below* the pool (sliding window) for a memory-heavy batch — the standalone ECMWF leg passes `GRIB_DECODE_WORKERS_ECMWF` so a small-RAM fallback host can keep decode serial while the shared pool stays wide; see the grib-decode-dispatcher doc.
- **ECMWF local disk I/O** — no HTTP, no cache needed. Files delivered by ECPDS to `ECMWF_GRIB_DIR` (default `/data/ecmwf`). Read-only volume in Docker
- **Multi-grid handling** — ECMWF files may contain multiple geographic sub-grids (e.g. main Europe + Nordic extension). cfgrib splits these into separate Datasets; the decoder uses first-wins per point across all sub-grids
- **ECMWF file naming** — structured convention: `dest_feed_model_class_stream_type_baseTime_validTime_step[_expver]`. Parsed by `parse_ecmwf_filename()` into `ECMWFFileInfo` dataclass. a1 = surface, a2 = pressure levels

### Gotchas
- **cfgrib uses lazy loading** — temp file must stay alive through interpolation, not just `open_datasets()`. Deleting too early causes `FileNotFoundError`.
- GFS S3 bucket has ~4.5h delay after init time before data is available
- ICON-EU: model levels (not pressure levels) — requires P field for vertical interpolation
- ICON-EU: bz2-compressed individual files, ~240 files per enrichment — parallel download essential
- Cache is shared across users (same model run = same data)
- **ECMWF no-cloud sentinel** — heights ≥ 9998m (nominally 9999m) mean "no cloud", converted to None
- **ECMWF fractions** — `cc` (cloud cover) is 0–1 in GRIB, multiplied ×100 to match our `cloud_area_fraction_pct` convention. Surface covers (lcc/mcc/hcc/tcc) similarly converted in `build_ecmwf_cloud_diagnostics()`
- **ECMWF longitude convention** — uses -180/+180 (same as route points), unlike GFS which uses 0–360. No longitude normalization needed for ECMWF decode
- **ECMWF files may have no extension** — ECPDS default delivery has no `.grib2` suffix. Scanner accepts any filename matching the naming convention

## Gotchas

- ECMWF now has relative_humidity and dewpoint at pressure levels, plus vertical_velocity
- Open-Meteo API returns flat arrays keyed by variable name, indexed by time step
- DWD URLs use `_LATEST` suffix for most recent version of each forecast type

## References

- Variable definitions: `fetch/variables.py`
- GRIB2 enrichment: `fetch/grib/`
- Data models: [data-models.md](./data-models.md)
- Analysis consumers: [analysis.md](./analysis.md)
