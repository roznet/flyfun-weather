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
| `icon` | `/v1/dwd-icon` | 7 | europe, default | No convective_inhibition, lifted_index; no vertical_velocity at pressure levels — skipped past ICON-EU's GRIB horizon or when the route leaves its domain (meteorology-decisions §66) |
| `ukmo` | `/v1/forecast?models=ukmo_seamless` | 5 | europe; route inside the UKV box | Uses `model_param`; no precip_probability, lifted_index. Live slots are built from Met Office UKV on AWS with no Open-Meteo fallback (#937, meteorology-decisions §72); this endpoint serves historical briefings and the `WB_GRIB_PRIMARY` rollback only |
| `meteofrance` | none since #940 (AROME GRIB only, meteorology-decisions §73) | n/a | AROME domain, ≤ ~48 h | No precip_probability, visibility, convective_inhibition, lifted_index, rain/showers/snowfall split |
| `gem` | `/v1/gem` | 10 | north_america | No freezing_level, visibility, convective_inhibition, lifted_index, vertical_velocity |

`region` (`ModelRegion.GLOBAL/EUROPE/NORTH_AMERICA`) and `required_country` let the pipeline skip models irrelevant to a route (`_should_skip_for_region` in `tasks/fetch.py`). The broad region comes from `detect_model_region(route)`, which classifies the **origin/destination** by coordinates (`waypoint_in_north_america`: west of 50°W and north of 10°N → North America), not by identifier — an endpoint can be a fix or reporting point (`PEPUL`, `KONAN`) whose first letter says nothing about where it is. Intermediate waypoints are ignored. Country-specific models (Météo-France→`FR`) are gated on `airports.route_countries(route)`, which samples the great-circle route into timezone polygons and so detects genuine **overflight**, not just landings. If that geometry can't be resolved it falls back to `route_covers_prefixes(route, prefixes)` (4-letter ICAO airports only). UKMO is gated on geometry instead: every route point inside the UKV box (`ukv.route_in_ukv_domain`, §72), in every mode.

`build_hourly_params()` constructs the API parameter string, excluding each model's unavailable variables. Pressure levels are **per-model** via `ModelEndpoint.pressure_levels` (named constants in `variables.py`):

| Model | Count | Constant | Notes |
|-------|-------|----------|-------|
| GFS | 28 | `EXTENDED_PRESSURE_LEVELS` | 25 hPa spacing below 500, up to 150 hPa |
| ECMWF | 13 | `ECMWF_PRESSURE_LEVELS` | 1000–50 hPa, no intermediate levels (OM limitation) |
| ICON | 19 | `ICON_PRESSURE_LEVELS` | 1000–30 hPa; GRIB enrichment interpolates to extended levels |
| Météo-France | 19 | `METEOFRANCE_PRESSURE_LEVELS` | 1000–150 hPa; unused since #940, the slot is AROME GRIB on its 24 levels (1000–100 hPa) |
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
- **Timeouts / connection errors** (`requests.Timeout`, `requests.ConnectionError`) are retried by `_send_with_network_retry`: 2 retries at `_NETWORK_RETRY_BACKOFF = [5, 15]` s per `_get_with_retry` call, one budget shared across the 429/5xx attempts so a flaky host can't multiply them, short because a briefing is waiting and the stalled call already cost `timeout` (30 s). The last failure raises (#835)
- Logging includes attempt count, wait time, and truncated URL for debugging
- `_prepare_request()` swaps the host for historical (`historical-forecast-api`) or customer-API-key (`customer-api`) modes; `get_json()` is the public GET-with-retry entry used by callers building their own URLs (e.g. frontal grid fetch)

### Route Walking + Interpolation (`fetch/route_walk.py`, `fetch/route_points.py`)

`walk_route()` is the common generator used by both cross-section interpolation and elevation profiling: it yields `(lat, lon, distance_nm, icao, name)` along multi-leg routes at configurable spacing, always including the real waypoints, using `euro_aip.models.navpoint.NavPoint` for great-circle math. `interpolate_route()` wraps it into `RoutePoint` objects.

```python
route_points = interpolate_route(route, spacing_nm=10.0)  # default 10nm
# → ~40 RoutePoint objects for a 400nm route
```

- Each point has `distance_from_origin_nm` for cross-section visualization
- Named waypoints keep `waypoint_icao` + `waypoint_name`; interpolated points have `waypoint_icao=None`

### Key Choices

- **Wind in knots** — `wind_speed_unit=kn` for aviation
- **Magnus dewpoint derivation** — when API doesn't provide dewpoint at pressure levels, derived from T + RH using `magnus_dewpoint(temp_c, rh_pct)` (b=17.67, c=243.5)
- **Range filtering** — pipeline skips models where `days_out >= max_days`. Skipped models shown as info-level "skipped" (not "fetch failed") in diagnostics
- **Graceful failure** — individual model failures logged, others continue
- **UKMO model_param** — uses generic `/v1/forecast` with `?models=ukmo_seamless` query param
- **Multi-point over per-waypoint** — reduces API calls from N×M to M; trivially within free-tier rate limits (600/min, 5K/hour). The 150-point chunk avoids 414 URI Too Large
- **24h time window** — only fetch target date data, not the full 16-day horizon (~150KB vs ~1MB per model)
- Gotchas: the API returns flat arrays keyed by variable name and indexed by time step; ECMWF now serves relative_humidity, dewpoint and vertical_velocity at pressure levels; DWD text URLs use a `_LATEST` suffix

## Text Forecasts (Route-Aware)

Regional text forecasts are fetched based on route destination. A unified `TextForecasts` container (`fetch/text_forecasts.py`) dispatches to the right source.

```python
from weatherbrief.fetch.text_forecasts import fetch_text_forecasts
text_fcsts = fetch_text_forecasts(route=route)  # → TextForecasts | None
text_fcsts.region        # ForecastRegion.US or .EUROPE
text_fcsts.source_label  # "NWS AFD" or "DWD Synoptic Overview"
text_fcsts.entries       # list[TextForecastEntry(label, text)]
```

**Region detection** (`detect_region(route)`): destination (last waypoint) coordinates via `waypoint_in_north_america` → US, else Europe. Not the identifier prefix: a UK route ending on reporting point `PEPUL` read as US and lost its DWD/Met Office charts and European text forecasts (prod 2026-10-26).

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
# internally: AutorouterCredentialManager(cache_dir).set_token(token) →
# AutorouterGrametSource(...).fetch_gramet(waypoints=[icao...],
#   altitude_ft=route.cruise_altitude_ft, departure_time=...,
#   duration_hours=route.flight_duration_hours or 2.0, fmt="pdf") → bytes
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

- Uses the `srtm.py` library, cached in `data/.cache/srtm/` (Docker volume-mounted); walks the route via `walk_route(route, spacing_nm=0.5)` for terrain-grade resolution
- `ElevationProfile` carries `max_elevation_ft`, `total_distance_nm` and per-point `(distance_nm, elevation_ft, lat, lon)`; saved as `elevation_profile.json` in the pack directory
- Runs early in the pipeline (before fetch) since it doesn't depend on NWP data

## Model Freshness (`fetch/freshness/`)

Marker-based per-(model, source) staleness decision used by `/packs/freshness` and the auto-refresh scheduler. Replaces the old per-call `meta.json` fan-out — most freshness HTTP calls are now pure compute (no I/O).

A 5-min background loop populates an in-memory `MarkerStore`. The HTTP-side check is `api/packs._build_data_status(pack, flight) -> DataStatus` (used by both the `/packs/freshness` handler and scheduler auto-refresh), which compares each `(model, source)` recorded on the pack against the matching marker — horizon-aware, so a new run only flags stale if its forecast horizon actually covers the flight's end time.

Twelve tracked source/model pairs (`SOURCE_REGISTRY` in `fetch/freshness/registry.py`): 7 direct (`ecmwf:direct`, `gfs:noaa`, `hrrr:noaa`, `icon_eu:dwd`, `icon_d2:dwd`, `arome:mf`, the `meteofrance` slot's primary source since #940; `WB_AROME_ENABLED=0` switches it off; `ukmo:ukv`, the `ukmo` slot's source since #937, hourly cycles, readiness from the AWS bucket listing) + 5 Open-Meteo republishes (`gfs/ecmwf/icon/meteofrance/gem:openmeteo`). Each `SourceConfig` carries schedule (cycles, delivery_offset, horizon) plus descriptive metadata (model/provider label, role, resolution, coverage, pressure_levels) feeding `/api/data-sources` and the help-page table.

The legacy `fetch/model_status.py` module (`fetch_model_metadata`, `check_freshness`, `compute_next_update`) is no longer consumed by the freshness endpoint's per-request path. `fetch_model_metadata` survives as a direct Open-Meteo init-time probe: it backs the Open-Meteo marker population in `fetch/freshness/sources.py` (background loop, not per-call) and is called directly by `tasks/alternates.py`, `tasks/standalone_verification.py`, `hewson/precompute.py`, `frontal/` (grid + CLI), and pack building in `api/packs.py`.

→ Full doc: [freshness-markers.md](./freshness-markers.md)

## GRIB2 Enrichment (`fetch/grib/`)

Model-native GRIB2 data for the three model slots, going well beyond what Open-Meteo publishes. Enabled via `BriefingOptions.enrich_grib=True` (always on in API, opt-in via `--enrich-grib` in CLI).

Two distinct shapes — do not confuse them:

- **Overlay** (GFS only): keeps the Open-Meteo sounding and patches CLW/ICMR onto it (`_merge_cloud_water_into_sections`), plus cloud diagnostics.
- **Full sounding replacement** (ECMWF, ICON-EU/D2, HRRR): `_replace_pressure_levels_from_grib` rebuilds the whole `pressure_levels` list from GRIB (T, RH/Td, wind, geopotential, vertical velocity, condensate) and discards Open-Meteo's. The outgoing Open-Meteo levels are still handed in as a **geopotential datum reference** (#486) — ICON ships no geopotential on model levels and would otherwise anchor on ISA.

Per-level condensate is five species, not two: `CONDENSATE_LEVEL_FIELDS` in `models/analysis.py` enumerates cloud liquid, cloud ice, and (ICON-D2 only, #530) rain/snow/graupel. The GRIB merge, the replacement passthrough and both interpolation axes all derive their field list from that tuple — never hand-list species at a new site, or a level ends up with cloud water but no rain water.

```python
from weatherbrief.fetch.grib import enrich_forecasts
grib_init_times, grib_skip_reasons, grib_sources = enrich_forecasts(
    cross_sections, all_forecasts, route_points, departure_time,
    data_dir=data_dir, flight_duration_hours=4.5,
    as_of_time=None, priority=None,  # priority defaults to the _DECODE_PRIORITY ContextVar
)
# Modifies PressureLevelData / HourlyForecast in-place.
# grib_sources: model slot → freshness source key actually used ("gfs" → "hrrr:noaa").
```

### Architecture

| Module | Purpose |
|--------|---------|
| `gfs_idx.py` | Parse NCEP `.idx` files, plan HTTP byte ranges for pressure-level and cloud diagnostic variables. Parametrized on variable sets (#457) — defaults keep GFS CLMR/ICMR behaviour; HRRR passes its own sets |
| `grib_fetch.py` | Find latest GFS run, flight-window forecast hours (`contiguous_window_forecast_hours`, also used by ICON), download via HTTP Range from S3 |
| `hrrr_fetch.py` | HRRR CONUS access (#457): 2-digit-fhour URLs, hourly-cycle run selection with per-cycle horizons (00/06/12/18z→48h, others 18h; probe the last-needed fhour — publication is progressive), Lambert-projection domain gate (`route_in_hrrr_domain` — same pyproj math as decode), flight-window hours, idx planning for the full-sounding + diagnostics sets, byte-range fetch |
| `icon_eu_fetch.py` | Find latest DWD ICON run (probes f000 **and** the window's last bracket hour — model-level P and single-level `t_2m`, since DWD publishes a run hour by hour and the single-level files land after the model levels — so a run still uploading the needed hours steps back to the previous one) + download model-level sounding and single-level diagnostics. Parametrized by `IconVariant` (`ICON_EU` / `ICON_D2`) — one code path, two variants differing in domain, cycles, horizon, level slice (EU 35–74, D2 16–65, both ≈ surface→FL280/310), filename conventions, cache slug and freshness source key (#456). **Every variable list is per-variant** — `model_level_variables` (EU: qc/qi/clc/p/t/qv/u/v/w; D2 adds qr/qs/qg, #530), `cloud_diag_variables` (EU has cape_ml/cin_ml/rain_con/hbas_con/htop_con/lpi_con_max/cape_con; D2 deliberately smaller — it runs no deep-convection scheme, so the parameterized fields are absent, not zero), `explicit_conv_variables` (D2 only: dbz_ctmax/echotop/lpi_max/w_ctmax/uh_max, #462; `grau_gsp` dropped #468). `tke` is published by both and fetched by neither unless `WB_ICON_TKE` names a variant (no consumer; EU's costs ~36 MB/fhour). Per-variable download for chunked decode; D2 caches per (variable, level) (`per_level_cache`, #478) so a partial fetch is detectable. `needs_predecessor_step` generalizes the f(H−1) lead-fetch gate (rain_con de-accum on EU; echo-top quarters on D2) |
| `icon_eu_levels.py` | Log-pressure interpolation from ICON-EU model levels to pressure levels. `interpolate_columns_to_pressure_levels` is the batched form the decoders use (#704): filter/sort/log over the whole (points × levels) matrix, then one `np.interp` per column over all its in-range targets. It must return **exactly** what the scalar `interpolate_model_to_pressure_levels` returns per column (keys, order, bits) — the final step stays `np.interp` because numpy fuses it into an FMA on arm64 and not on x86, so a numpy-arithmetic rewrite differs by 1 ulp; columns with repeated pressures fall back to the scalar call. Pinned by `tests/test_grib_loop_parity.py` |
| `ecmwf_fetch.py` | Parse ECPDS filenames, scan delivery directory, find latest run. No HTTP — files land on local disk via ECPDS push |
| `decode.py` | Decode + bilinear interpolation to route points. **ECMWF a1/a2 and ICON-EU/D2 model levels read messages directly with eccodes** via `grib_reader.py` (#674 phase 1; ~14× a1, ~3× a2/ICON; the cfgrib rollback was deleted in #678); GFS/HRRR/cloud-diag paths still go cfgrib → xarray. Chunked ICON-EU decoder (`decode_icon_eu_per_point_chunked()`) processes one variable at a time with explicit `gc.collect()` between — peak ~270MB vs ~800MB. ECMWF decoders handle multi-grid files (first-wins per point). Separate eccodes **message-level** path for D2 explicit-convection fields (`decode_icon_d2_explicit_conv_per_point`, #462): stepRange-in-minutes selection + corridor-extremum reduction (never cfgrib merge, never bilinear); also `build_d2_validity_mask`/`d2_corridor_fully_valid` for the hardened domain gate. **Lambert-projected grids (#457):** the shared interpolation helpers branch when a dataset has no 1-D lat/lon dims but (y, x) dims + Lambert GRIB attrs — build the projection with pyproj, transform targets to fractional grid indices, same vectorised bilinear on the projected axes. HRRR decoders (`decode_hrrr_pressure_per_point` incl. grid→earth wind rotation, `decode_hrrr_cloud_diag_per_point`, `build_hrrr_cloud_diagnostics`) ride this path. **Per-point result loops are row-wise (#704):** the ECMWF a1/a2 decoders apply the NaN / first-grid-wins test and the `cc` ×100 to whole gathered rows and touch the result dicts only for values that land; the ICON decoders interpolate a field for all points at once. Any change here must keep the output identical to the per-value loops — same dict keys and key order, same Python floats — which `tests/test_grib_loop_parity.py` checks against verbatim copies of the old code |
| `arome_fetch.py` / `arome_domain.py` | Météo-France AROME 0.025° (#529; the whole `meteofrance` slot since #940, on unless `WB_AROME_ENABLED=0`): mirror URLs per package (IP1, IP2, SP1, SP2), 6-hour package-group mapping (download only the groups the window touches; SP1 also the group of the hour before the window, for the first `tp` difference), `arome_horizon_end`, contiguous window hours, run finder probing the last-needed group, streamed download with a length check. The domain predicate (`route_in_arome_domain`) sits in its own module over a generated band table (`arome_domain_bands.py`, regenerated by `scripts/regen_arome_domain.py` from a real message's bitmap). Details in weather-engine-specs § AROME |
| `grib_reader.py` | Shared eccodes message reader for the direct decoders (#674): axes from `distinctLatitudes/Longitudes` exactly as cfgrib, bitmap → NaN via cfgrib's float32-max `missingValue`, float32 values, header-only selection before unpack, `gather_bilinear` with the zero-weight-corner rule. Output pinned in `tests/test_grib_reader.py` against `tests/fixtures/grib_reader/*.json`, generated through the cfgrib decoders before #678 deleted them (regenerate with `WB_FREEZE_GRIB_FIXTURES=1`) |
| `icon_zarr.py` | ICON-EU model levels cached as zarr v3 (#705): `cache_model_level_blob` converts each whole-column blob on write (in the decode pool) to `f…_ICON_EU_{VAR}.zarr/` and deletes the GRIB; `read_model_level_columns` reads only the 64×64 tiles under the route, bit-identical to the direct decoder; `cached_model_level_entry` is the "store or GRIB" lookup the prefetch and decode planners use. `WB_ICON_ZARR=0` stops conversion. Details in weather-engine-specs.md, *ICON-EU model levels as zarr* |
| `decode_worker.py` | Thin process-pool worker entry points (Phase B-3): serialisable args only, GRIB bytes read from disk inside the worker. Escapes the GIL contention between concurrent `enrich_forecasts()` calls |
| `ecmwf_watcher.py` | Background scanner: writes `.ready_{date}_{cycle}z` sentinel files when an ECMWF delivery run is complete. Manifest from `delivery_config.json` co-located with the data |
| `flight_warm.py` | Flight-scoped pre-warm (#929, generalising #469): per new run of ICON-D2, ICON-EU, GFS or HRRR, downloads (no decode) what each upcoming flight's briefing would fetch from that source, through the briefing's own run finders, gates and download helpers. Soonest departure first under per-pass byte / time / free-disk / flight-count limits (`warm_limits`, env-tunable); limits cut the tail and are logged with flight ids. Bytes come from `cache.cache_write_meter`. See weather-engine-specs "Flight-driven cache warming" |
| `precache.py` | Pre-warms ICON-EU/GFS byte-range cache for the `/maps.html` D-0..D-3 airport-profile selectables (#126); `precache_icon_d2_flights` is now a wrapper over `flight_warm`. Shares the per-flight disk cache so warmed runs also speed briefings. Warming is discretionary: only inside the per-model wall-clock window (`MODEL_WARMING_WINDOW_UTC`, #475), and it **yields to interactive briefings** (see the key choice below). The gate sits at one-download granularity everywhere — each EU variable/cloud-diag fetch, each uncached GFS hour, and (via the prefetch's `abort_if`) every D2 `(fhour, variable)` unit — and never runs ahead of a free `is_cached` hit, so a resumed pass fast-forwards through the warmed prefix. A part-warmed flight is left cached-and-skipped next tick, not counted in `flights_warmed` (#490, PR #498, #501) |
| `ukv.py` / `nc_range.py` | Met Office UKV for the UKMO slot (#937): schedule (hourly runs, 12/54/120 h horizons, 6 h 30 min delay), domain gate, run choice by bucket listing, point sampling (bilinear on the LAEA 2 km grid), conversions to the slot's fields, omega from the MO global model's `upward_air_velocity`. `nc_range.read_chunks` reads one NetCDF4 variable's chunks by HTTP range: header and tail, the v1 chunk B-tree walked directly (h5py's own lookup as fallback), then only the 128×128 tiles under the route; tiles cached as `.npy` under the run dir. See "UKV slot" below |
| `fill.py` | Time-axis fill of GRIB-enriched fields — see "Gap-filling" below. Also hosts `apply_gfs_rh_condensate_gate` — drops GFS phantom layers where pressure-level RH and condensate disagree with the averaged cover |
| `cache.py` | Disk cache per model (`data/.cache/grib/{model}/{date}_{cycle}z/`). Per-model TTL via `MODEL_TTL_SECONDS` (GFS 12h since #913 with a 20 GiB default cap, ~177 MB per GRIB-primary forecast hour; ICON-EU 12h, ICON-D2 6h, HRRR 9h, AROME 9h with a 12 GiB cap since #940, UKV 12h with a 2 GiB cap (#937) — an extended run must outlive the 6h gap to its successor plus publication delay; 12h fallback) — ECMWF reads local ECPDS disk so it isn't cached here. Active-run pins (`pin_run_dir`) exempt in-use runs from TTL and size-cap eviction, re-checked under the lock at deletion time |

### How It Works

**GRIB-primary slots (#912 ECMWF / ICON, #913 GFS, `fetch/grib/primary.py`).** `run_fetch` order is now: filter models (range, region, Open-Meteo live horizon; for ICON the earlier of that and `icon_eu_horizon_end`, and the route inside the ICON-EU domain, §66, not historical) → **GRIB-primary gate** → Open-Meteo fetch for the remaining models → skeletons for the gated ones → `enrich_forecasts` → **Open-Meteo fallback** → diagnostics and artifacts.
- **Gate** (`grib_primary_skip_reason`, network-free, needs `enrich_grib`): ECMWF passes when `find_best_ecmwf_run` (same `cover_until` as `_enrich_ecmwf_inner`, after the `as_of_time` filter) returns a run whose observed horizon reaches the window end and holds both a1 and a2 files. ICON passes when the whole route is in the ICON-EU domain, `icon_eu_window_out_of_range` is False, and the briefing is not historical (DWD keeps current runs only). The D2/EU choice stays in `_prepare_icon_eu`. GFS (#913) passes unless the briefing is historical, the route takes the HRRR upgrade (`hrrr_route`: `_hrrr_takes_route` mirrors the network-free gates of `_try_enrich_gfs_from_hrrr`, so that path is left exactly as it was), or the window ends past the newest publishable cycle + 384 h (`gfs_window_out_of_range`). A refusal is logged (`GRIB-primary <model>: not eligible (<reason>)`) and the model is fetched from Open-Meteo first exactly as before.
- **Skeleton** (`build_skeleton`): one `WaypointForecast` per route point, with Open-Meteo's labels (ICAO / `RP<nm>`) and Open-Meteo's day span (`target_date` 00:00 to `end_date` 23:00 UTC; `end_date` = `fetch_end_date`, the date of the window's last hour, so a 23:40Z arrival spans into the next day, #925), extended by `skeleton_end_date` to the date of the window's last hour + 6 h, so a coarse-step upper bracket past midnight (a 22Z window end on a 3-hourly grid is bracketed by 21Z and 00Z) has a slot to land in; without it the time fill stopped at 21Z and every point fell back to Open-Meteo. Coverage and the fallback still read `required_hours` and the Open-Meteo span. Every value None. Recorded through `_record_success`, so `all_forecasts` shares the objects as with an Open-Meteo slot. Keeping the day span keeps hour-indexed code unchanged: the time-scan baseline is 0 levels and an hour GRIB did not reach simply has no data.
- **Fallback** (`fill_grib_primary_gaps`, runs even when enrichment raised): a point is covered when every hour of `floor(dep) .. ceil(dep + ceil(duration))` has a sounding plus `temperature_2m_c`, `wind_speed_10m_kt`, `precipitation_mm`, `cloud_cover_pct`. Uncovered points get one `fetch_multi_point` call for that model and those points only, full variable set, same client (so `open_meteo_api_calls` → `BriefingUsage` counts it). `splice_open_meteo` then fills by valid time: an hour without levels takes Open-Meteo's column, a None scalar takes Open-Meteo's value, GRIB values and diagnostics are never touched. If that fetch raises or returns empty, the slot is dropped and recorded in `models_failed` / the empty bucket, exactly as a failed Open-Meteo fetch is today. Logged as `GRIB-primary <model>: Open-Meteo fallback for N/M points (<reason>)`.
- `FetchResult.grib_primary` = `{model: points that fell back}` for the slots built this way.
- `tasks/time_scan.confirm_candidate` runs the same gap fill after its candidate-window enrichment (a no-op, no request, on slots that have values).
- **Kill switch:** `WB_GRIB_PRIMARY=0` restores Open-Meteo-first for ECMWF, ICON, GFS and UKMO (Météo-France then has no slot, #940); a comma list (`ecmwf,icon,gfs,meteofrance` = the state before #937) limits it. Read per call.
- **UKV slot (#937, §72):** the UKMO gate (`_ukmo_skip_reason`) passes when the whole route is inside the UKV box, a published run covers the window (`ukv_window_out_of_range`) and the briefing is not historical. `_enrich_ukmo` (phase 1, beside ECMWF) picks the newest covering run whose listing holds every required file, reads every step (`fetch_steps`, all-or-nothing), writes the sounding with `_replace_pressure_levels_from_grib`, the surface as per-hour rates over the step period and `NWPCloudDiagnostics` (ceiling = lowest 50 % cloud-fraction height). UKMO is in `NO_FALLBACK_MODELS`: `fill_grib_primary_gaps` drops a short slot (`GribOnlyIncomplete` → `no_data`) instead of calling Open-Meteo, and the time scan does not extend it by an Open-Meteo adjacent day. Transfer: ~33 MB and ~2300 range requests for a 3-step UK route.
- **Pack record:** `fetch_meta.json` carries `fetch_facts` = `{grib_primary, grib_primary_dropped, open_meteo_calls, open_meteo_weighted_calls, grib_init_times, grib_sources}` from the fetch stage (`grib_primary_dropped` = `{model: reason}` for a GRIB-primary slot whose fallback failed and was dropped) (the pipeline's end-of-run rewrite keeps it). It is what the leak check and `scripts/compare_pack_surface_openmeteo.py` (a day's GRIB-built packs vs Open-Meteo's single run of the same init, sampled points, billing budget) read. `scripts/compare_grib_primary.py` replays one flight with the switch off and on (advisory statuses, calls, surface diff, plus 850/700/500 hPa for GFS); `--off ecmwf,icon --on 1` isolates GFS. The daily script compares GFS soundings too (`--levels`, Open-Meteo `ncep_gfs025`). The time-scan confirm re-fills gaps only for the slots listed in `grib_primary`, so an Open-Meteo-built slot stays as it was.
- **Failure drill (dev only):** `WB_GRIB_PRIMARY_DRILL=icon` (or `ecmwf:half`, `gfs`) empties that skeleton's points after enrichment so the fallback runs and reports on a healthy machine.
- Out-of-window hours of a GRIB-primary slot are empty (ECMWF keeps ±3 h around the window, ICON the window). `precipitation_probability_pct` and `lifted_index_raw` are None on these slots (no GRIB field; see meteorology-decisions §61).

**GFS enrichment** (`_enrich_gfs()`):
0. **HRRR gate (#457):** if the *whole* route fits the HRRR CONUS domain (Lambert-projection bounds check, not a lat/lon box) AND a run's horizon covers the flight window (48h on 00/06/12/18z cycles, 18h on the other hourly cycles; freshest covering run wins), the gfs slot is sourced from **HRRR** (3 km) as a FULL sounding replacement — `_enrich_gfs_from_hrrr` rebuilds `pressure_levels` from HRRR's 35 levels (T, Td direct, RH, wind grid→earth-rotated → kt, VVEL already Pa/s, HGT direct, CLMR + CIMIXR) via the ECMWF replacement flow, and attaches instantaneous cloud diagnostics (band covers, ceiling, overall cloud base, ML CAPE/CIN). All-or-nothing: any gate failure or a zero-hour HRRR result falls back to plain GFS below; a partial success keeps HRRR (fill covers gaps, same rule as ICON-D2). `_enrich_gfs` returns `(ts, source_key)` — `"hrrr:noaa"` or `"gfs:noaa"` — recorded via `grib_sources["gfs"]` so the pack badges `GFS (HRRR)`. When HRRR sourced the slot, `propagate_all` receives `gfs_init=None` and `apply_gfs_rh_condensate_gate` is skipped — the averaged-window machinery must never run on HRRR's all-instantaneous fields. **Kill switch:** `WB_HRRR_ENABLED=false` forces the plain-GFS path before the domain gate is even consulted (same shape as `WB_GRIB_PRECACHE_ENABLED`, read per call so a restart is enough) — it exists to measure the HRRR-vs-plain-GFS difference A/B on one route, and to route around an S3-side problem without a deploy. Default on.
1. Find latest available GFS cycle (00z/06z/12z/18z) — HEAD request on `.idx` file
2. Forecast hours = the contiguous range `floor(departure) .. ceil(departure + ceil(duration))` on the GFS grid (1-hourly f000–f120, 3-hourly f120–f384: coarse hours snap down, plus the step at/after the window end) via `contiguous_window_forecast_hours` — HRRR's shape, shared with ICON. Not sample-and-`round()`: ties-to-even dropped every odd hour for :30 departures (#807)
3. Parse `.idx` to find byte offsets for CLMR + ICMR at all pressure levels + cloud diagnostics
4. Download via HTTP Range requests from `noaa-gfs-bdp-pds.s3.amazonaws.com` (public, no auth)
5. Decode with cfgrib → xarray, bilinear interpolation to each route point
6. Merge CLWMR/ICMR into `PressureLevelData` objects in-place
7. Attach `NWPCloudDiagnostics` (the bulk `cloud_cover_*_pct` fields are left on Open-Meteo — see "Cloud cover field separation" below)
8. **GRIB-primary GFS (#913).** `enrich_forecasts(grib_primary=...)` carries the gate's decision (run_fetch: the gated models; time-scan confirm: `fetch_facts.grib_primary`), never inferred from empty hours; for a GRIB-primary GFS slot HRRR is not tried and `_enrich_gfs_run` splits the hours: those with no sounding go to `_enrich_gfs_from_grib`, the rest keep the CLWMR/ICMR patch (step 6). `read_gfs_grib_points` (public, also for map snapshots) downloads per hour a **sounding blob** (`GFS_SOUNDING_V1`: TMP RH UGRD VGRD HGT VVEL CLMR ICMR on the 28 Open-Meteo GFS levels from pgrb2 + pgrb2b, plan validated before download by `gfs_sounding_plan_problem`, all-or-nothing; a level subset gets its own cache key and skips pgrb2b when pgrb2 has every level) and a **surface blob** (`GFS_SURFACE_V1`: `gfs_idx.GFS_SURFACE_FIELDS`, instantaneous message or the 0-N running total, first .idx occurrence) for the window hours plus the step before, 4 hours downloading at a time; decodes both in the pool. A point whose column misses a mandatory variable at any level gets no column (`_gfs_column_problem`). Soundings go in with `_replace_pressure_levels_from_grib(only_empty=True)`, the surface with `_apply_gfs_surface_to_sections` (ICON pattern: instantaneous at the valid hour, ΔAPCP / ΔACPCP as per-hour rates over `(prev, valid]`, fill-only). Units and the rain / snow split: meteorology-decisions §62. The run dir is pinned for the whole GFS enrichment.

**ICON enrichment — EU or D2** (two-phase sequential decode for memory safety):
0. **Variant gate (#456, hardened #462):** if the *whole* route fits the ICON-D2 domain (43.18–58.08°N, 3.94°W–20.34°E) AND a complete D2 run's 48h horizon reaches the flight-window end AND the route's entire ~10 NM corridor buffer lies in valid (unmasked) D2 cells (`_d2_corridor_mask_ok` — the regular-ll product bitmap-masks ~17% corner cells; the mask is built once from a delivered message's bitmap and cached as `icon-d2-validity-mask-v1.npz`, failing OPEN to the bbox gate when unobtainable), source the icon slot from **ICON-D2** (2.2 km); otherwise **ICON-EU** (6.5 km). All-or-nothing, never a per-point mix. `_prepare_icon_eu` resolves the D2 run inline while gating so it never picks D2 without a usable run; on total D2 failure the slot re-runs cleanly on ICON-EU. The chosen source is reported out of `enrich_forecasts` via `grib_sources` and recorded on the pack (`model_sources["icon"]` = `icon_eu:dwd` / `icon_d2:dwd`) so the freshness bar can badge `ICON (D2)`.
1. Check route is within the chosen variant's domain — silently skip if outside
2. Find latest run (3h cycles; EU ~3h / D2 ~2h publication delay)
3. **Phase 1 (parallel with GFS):** Download the variant's `model_level_variables` and `cloud_diag_variables` to disk cache. D2 also downloads the explicit-convection fields into **per-variable** blobs (`ICON_D2_EXPL_<VAR>_V1` cache keys — the decoder must know which field each blob holds, and several are multi-message sub-hourly files). The predecessor-step fetch is gated by the variant-level `needs_predecessor_step` flag (EU: rain_con de-accumulation; D2: the prior file's three echo-top quarter-windows)
4. **Phase 2 (after GFS completes):** Chunked decode — process one variable at a time via `decode_icon_eu_per_point_chunked()`, explicitly freeing memory between variables. Log-pressure interpolate model levels → ICON pressure levels
5. **Full sounding replacement** via `_replace_pressure_levels_from_grib` — t, RH derived from qv, u/v, w, plus every `CONDENSATE_LEVEL_FIELDS` species the variant carries — not a CLW/ICMR patch onto Open-Meteo levels. ICON has no geopotential: the column borrows its datum from the outgoing Open-Meteo levels, else (GRIB-primary skeleton, #912) from the model's own `pmsl` at that point and step (`_height_above_msl_m`), else ISA
6. Attach `NWPCloudDiagnostics` (only if GFS hasn't already enriched the point; bulk cloud cover stays Open-Meteo's)
6b. **Surface fields (#912):** `ICON_SURFACE_VARIABLES` (t_2m, td_2m, relhum_2m, u_10m, v_10m, vmax_10m, pmsl, ps, tot_prec, rain_gsp, rain_con, snow_gsp, snow_con, vis, hzerocl) are fetched one file per variable and step (`<PREFIX>_SFC_<VAR>_V1` cache keys; window hours plus the lead step from `icon_eu_previous_step`, every variant), decoded by `decode_icon_surface_per_point` keyed by **file**, not shortName, and written by `_apply_icon_surface_to_sections` after the step's diagnostics: instantaneous fields plus the diagnostics' covers and `cape_ml` at the valid hour, de-accumulated precipitation as a per-hour rate over `(prev, valid]`. **Fill-only:** a field that already has an Open-Meteo value keeps it, so only a GRIB-primary skeleton changes
7. **D2 only (#462):** `_enrich_icon_d2_explicit_convective` decodes the explicit-conv blobs via `decode_icon_d2_explicit_conv_per_point` — eccodes **message-level** decode selecting messages by `stepRange` in minutes (never a cfgrib blob merge: echotop files carry 4×15-min `min_pres` windows), reducing each field to a **corridor extremum** over a ~10 NM route buffer (max; min-pressure for echo top; signed argmax|uh|) instead of per-point bilinear sampling (a 2.2 km cell between 10 NM route points would otherwise vanish). The loop then constructs the hourly echo top as the min over exactly the four quarter-windows ending in `(H−1, H]` (a missing quarter degrades `echo_top_complete` — never a partial min), converts Pa→ft against the hour's own geopotential column — extrapolating along the nearest two levels for an echo above the aviation slice rather than switching datum to ISA mid-field, ISA only as the <2-levels fallback (#465 idea, meteorology-decisions §19 rule 1) — and attaches `NWPExplicitConvectiveDiagnostics` to matching hours. These fields are deliberately **excluded** from the time-axis fill and spatial interpolation (registered skips in `fill.py` / `spatial_interpolation.py`): corridor extrema are already spatial reductions, dBZ is logarithmic, and a failed hour's 1-h interval max has no covering interval to hold over — it stays honestly unavailable via the completeness flags

**ECMWF IFS enrichment** (`_enrich_ecmwf()`):
1. Scan the ECPDS delivery directory for ECMWF GRIB files (local disk, no HTTP)
2. Parse filenames to extract run metadata (model, base time, step, a1/a2 part)
3. Pick the latest operational run; select steps via `_select_ecmwf_window_steps` — everything within the ±3h `ECMWF_FLIGHT_WINDOW_MARGIN`, **union the immediately bracketing steps** (latest strictly before the departure *hour*, earliest at/after the window end; the departure hour so that hour's de-accumulated rate has its predecessor, e.g. a 15:30 departure at +99.5h keeps +96, #912). The bracket union is what makes this cadence-independent: ECMWF is hourly to +90h, 3-hourly to +144h, then 6-hourly to +168h, and a fixed margin alone cannot retain two anchors in the 6-hourly range (#483). The margin constant stays 3h so `tasks/time_scan.py`'s coverage rule window can only under-claim relative to what was decoded, never over-claim
4. **Pressure levels (a2 files):** Decode the full sounding at 25 pressure levels per point — `ECMWF_SOUNDING_VARS` = t/r/u/v/w/gh (+z at 1 hPa only) and clwc/ciwc/cc — then **replace** the Open-Meteo levels. Multi-grid files (main Europe + Nordic extension) handled via first-wins: each point uses whichever sub-grid covers it
5. **Surface diagnostics (a1 files):** Decode ceil, cbh, lcc/mcc/hcc/tcc, `hcct` (convective cloud top), `deg0l` (freezing level), plus the surface scalars (T/Td, wind + `10fg` gust, vis, CAPE, MSLP, and the accumulated tp/sf/cp de-accumulated against the previous step). Heights in metres AGL; in real a1 data "no cloud" is a bitmap-missing (NaN) cell → None, with the documented 9999 m sentinel guarded defensively (≥ 9999 dropped for ceil/cbh; only the literal 9999 for `hcct`, whose real towers exceed it). `deg0l`, `blh` and `hcct` are normalized to MSL with the model's own orography (`model_surface_height_m`) and dropped when it is unavailable; `ceil`/`cbh` stay AGL (meteorology-decisions §21, #807). `sf` (m water) → `snowfall_cm` at Open-Meteo's 0.7 cm per mm water (`units.SNOW_CM_PER_MM_WATER`). Fractions 0–1 → converted to 0–100%
6. Attach `NWPCloudDiagnostics` and apply the surface scalars onto `HourlyForecast` (#912 adds `msl`, RH from t2m/d2m, CIN from `mlcin100`, the tcc/lcc/mcc/hcc covers, and `rain_mm` = (tp − cp) × (1 − sf/tp) (#958) / `showers_mm` = cp per hour; these new ones are **fill-only**, so an Open-Meteo slot keeps its values) — the two run together at the same valid time so `fill.py` can use `nwp_cloud_diagnostics is not None` as the GRIB-anchor detector. **`deg0l` datum (#487):** ECMWF ships it metres **above ground**, while `freezing_level_ft`/`freezing_level_m` are MSL everywhere else, so the decoder adds the model's own `terrain_elevation_m` and **drops the value entirely** when terrain is unavailable rather than passing an AGL number through. A negative `deg0l` is kept and converted (0 °C below model ground is a real cold-airmass forecast); `ceil`/`cbh`/`hcct` are AGL too and deliberately NOT converted (aviation convention)

**Cloud cover field separation:** `_apply_cloud_diagnostics()` attaches a typed `NWPCloudDiagnostics` payload to `hourly.nwp_cloud_diagnostics` and never writes the bulk `hourly.cloud_cover_low/mid/high_pct` fields — those keep the Open-Meteo hourly-interpolated values across both native GRIB steps and gap hours. Consumers that need GRIB-derived cover read `nwp_cloud_diagnostics.{low,mid,high}.cover_pct`. The two are deliberately separate so the OM bulk values remain available as a temporally-smooth comparison signal alongside the GRIB-anchored per-band layer geometry. GFS still takes priority over ICON-EU for points where both provide diagnostics. The only Open-Meteo field that GRIB cloud-diag enrichment overrides is `freezing_level_m` (from ECMWF `deg0l`). A GRIB-primary skeleton (#912) has no Open-Meteo covers, so there the bulk fields are filled from the GRIB covers (ECMWF a1 tcc/lcc/mcc/hcc, ICON clct/clcl/clcm/clch) at native steps and linearly interpolated between them; on an Open-Meteo slot they are untouched.

**Gap-filling for GRIB-enriched fields:** GRIB enrichment only targets native model forecast hours (e.g. every 3h for GFS at longer lead times), and some grid cells may return None. Two gap-filling passes ensure all route points and hours have data:

1. **Time axis** (`fetch/grib/fill.py`): After all GRIB enrichment completes, `propagate_all()` fills gap hours between native steps. The strategy depends on the field and source:
   - **GFS cloud diagnostics** — when `gfs_init` is provided, `_fill_cloud_diagnostics` uses **window-midpoint resampling** (`_interp_gfs_diag_hourly`) for the averaged fields (low/mid/high cover, boundary-layer cover). The idx parser prefers the averaged form (`_PREFER_AVERAGED_PAIRS`) so cover matches its averaged-only base/top/temp geometry (#441). NCEP's published windows are **nested** (0-4, 0-5, 0-6 within a 6 h cycle), so their midpoints bunch and leave a hole; `_deaveraged_anchor_knots` differences consecutive means into **disjoint** windows first, giving one evenly-spaced knot per anchor (#481). Resampling runs at **native anchor hours too**, not only gap hours — before #481 it was a no-op inside f120 where GFS is hourly, leaving every anchor labelled at its window's end. Layer geometry (base/top/temp) is not interpolated: it comes from the higher-cover endpoint, and sub-5 % resampled cover drops the layer. Convective cover, total cover and ceiling are instantaneous and stay step-time-anchored. See [meteorology-decisions §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate).
   - **GFS CLW/ICMR overlay** — when `gfs_init` is provided, `_fill_cloud_water` uses step-time linear interp via `_interp_gfs_clw_hourly` (instantaneous mixing ratios — no midpoint adjustment).
   - **ICON / ECMWF cloud diagnostics** and the **GFS fallback path** (no `gfs_init`) — persist from the preceding native anchor (instantaneous cover; slowly-varying categorical geometry). **Exception:** the fields in `NWP_CLOUD_DIAG_RATE_SCALARS` (convective precip rate, ICON `lpi_con_max`) are window quantities, so a gap hour takes the **next** anchor's value — the covering interval, not the preceding one. Forward-filling a rate presented the previous window inside the current one, shifting convective precip up to 6 h and letting the firing gate read "dry" during a firing window. Past the last anchor everything persists.
   - **ECMWF / ICON-EU pressure-level soundings** — `_linear_interp_pressure_levels` rebuilds the full `pressure_levels` list on gap hours via per-level linear interp; dewpoint is derived from interpolated (T, RH) via Magnus rather than interpolated directly.
   - **ECMWF / ICON surface scalars** — `_linear_interp_grib_surface` linearly interpolates the genuinely-instantaneous scalars (temperature, dewpoint, visibility, CAPE, pressure) between GRIB anchors (identified via `nwp_cloud_diagnostics`). Since #912 it also covers ICON, and `_GRIB_PRIMARY_FILL_FIELDS` (MSLP, RH, CIN, the four covers, freezing level); ICON and those fields are interpolated into **empty** gap hours only, so Open-Meteo's hourly values survive on a non-primary slot. Wind speed+direction are interpolated as **U/V components** (`_lerp_wind`, with a calm-speed gate) rather than scalar speed + circular direction; the gust (`10fg`) is a window **maximum**, so it is held over the covering interval, not linearly interpolated (#441).

   After `propagate_all`, **`apply_gfs_rh_condensate_gate`** runs on GFS sections: each low/mid/high band is dropped when its pressure-level `max(RH)` is below the per-band threshold (`_GFS_GATE_RH_LOW_PCT` = 60, `_GFS_GATE_RH_MID_PCT` = 70, `_GFS_GATE_RH_HIGH_PCT` = 70) AND `sum(CLMR + ICMR)` within `[base_ft, top_ft]` is zero. This protects against averaged-window phantom layers that survive interpolation — see [meteorology-decisions §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate).

2. **Spatial axis — linear interpolation** (`analysis/spatial_interpolation.py`): Before sounding analysis, `interpolate_all_spatially()` fills gaps where a route point's GRIB grid cell returned None by linearly interpolating in distance-space from left/right neighbors. Applies to both CLW/ICMR (per-pressure-level) and cloud diagnostics (all numeric sub-fields). Requires both neighbors; max gap 100 nm; edge gaps skipped.

3. **Vertical axis — linear in pressure** (`analysis/sounding/__init__.py`): During sounding analysis, `_interpolate_cloud_water()` fills intermediate pressure levels (25-hPa spacing) between native GRIB levels (50-hPa spacing) by linear interpolation in pressure-space. Only applies to CLW/ICMR.

**When adding new GRIB-enriched fields:** add a time-axis fill in `fill.py` (linear interp where the field is meaningfully continuous, forward-fill where persistence is the right semantic) and a spatial interpolation function in `spatial_interpolation.py` (spatial axis). If the field is per-pressure-level, also add vertical interpolation in the sounding analysis.

### Key Choices
- **Four model slots, six possible sources** — the `gfs` slot is served by HRRR (3 km) on CONUS routes else GFS; the `icon` slot by ICON-D2 (2.2 km) when the route fits its domain else ICON-EU (6.5 km); the `ecmwf` slot always by ECMWF IFS from ECPDS; the `meteofrance` slot is AROME GRIB or absent, never Open-Meteo (#940, §73). Each upgrade is **all-or-nothing per slot**, never a per-point mix, so a cross-section is always one model run
- **GFS takes priority** — if GFS already attached diagnostics, ICON skips that point (avoids contradictory overrides)
- **ECMWF enriches its own model only** — ECMWF GRIB data is only applied to ECMWF cross-sections, never mixed with GFS/ICON data
- **No bulk cloud-cover override** — GRIB cover lives on `nwp_cloud_diagnostics.{low,mid,high}.cover_pct`; `attach_nwp_diagnostics` never touches `hourly.cloud_cover_*_pct` (see "Cloud cover field separation")
- **Per-point interpolation** — `decode_grib_per_point()` / `decode_icon_eu_per_point_chunked()` / `decode_ecmwf_pressure_per_point()` / `decode_hrrr_pressure_per_point()` return values per route point
- **Graceful degradation** — enrichment failure logged but pipeline continues with Open-Meteo data only
- **Init time tracking** — `enrich_forecasts()` returns `(grib_init_times, grib_skip_reasons, grib_sources)`. `grib_init_times: dict[str, int]` maps model names (`"gfs"`, `"icon"`, `"ecmwf"`) to Unix timestamps. `grib_skip_reasons: dict[str, str]` explains why models were skipped (e.g., `"out_of_range"`). `grib_sources: dict[str, str]` records which variant actually served each slot (→ `model_sources` on the pack, so the freshness bar can badge `GFS (HRRR)` / `ICON (D2)`). Stored in `BriefingPackMeta` and displayed in the freshness bar. `model_init_times` keeps each model's Open-Meteo run time (including ECMWF); `grib_init_times` carries the parallel GRIB run time so the freshness bar can annotate `ECMWF <om-run> (GRIB <grib-run>)` when the two differ — ECPDS delivery typically leads Open-Meteo for ECMWF.
- **Two-phase sequential decode** — GFS download+decode runs in parallel with ICON-EU download-only and ECMWF disk decode. ICON-EU decode runs after GFS completes to prevent OOM (measured ~1.06 GB per forecast hour under cfgrib; the #674 direct reader holds one message at a time, so this is now much lower but not yet re-measured on prod)
- **Per-fhour cleanup in GRIB enrichment loops** — `_enrich_clwmr_icmr` (GFS cloud water), `_enrich_cloud_diagnostics` (GFS cloud diag), and `_enrich_icon_eu_cloud_diagnostics` all `del decoded_points; gc.collect()` between fhour iterations. Without this, dicts accumulate across the loop and contribute to OOM on long-route briefings (84+ route points × multiple fhours). Diagnostics arrays are deleted alongside in the diag loops. **Exception:** `_decode_and_merge_icon_eu` and `_enrich_ecmwf_inner` fan out *all* fhour/step decodes to the pool in parallel before merging (issue #133), so peak memory is `n_workers × per-fhour RSS` during decode. This is the intentional trade-off behind the speed-up; the default of 2 workers keeps peak resident bounded (~+125 MB ICON peak vs. sequential). Each fhour's decoded dict is dropped immediately after its merge; a single `_grib_gc()` at the end of the merge loop reclaims them. Raise via `GRIB_DECODE_WORKERS` on hosts with spare RAM.
- **`_grib_gc(label)` collects generations 0–1 only while the decode pool is on** (#704). The explicit collections date from in-process decode; with decode in the pool, a full collection mostly re-walked the long-lived main-process heap (~2.6 GB: euro_aip, pydantic packs) for a median 7.7 s per briefing while holding the GIL. `del` already frees the acyclic per-hour dicts by refcount; what a site can still reclaim is young cycles. With `GRIB_DECODE_WORKERS=0` (in-process decode) the full collection is kept. `WB_GRIB_GC_GENERATION=0|1|2` overrides both (`2` = old behaviour, no deploy needed). Each site passes a label, and a `GRIB gc (gen N):` log line reports time, objects collected and RSS freed per site, so sites that free nothing can be dropped on evidence. Decide on `objs=`, not `freed=`: `freed=` is the process-wide RSS delta around the collection, so concurrent briefings, decode-pool traffic and glibc holding freed memory make it noisy, and it can go negative. The generation is resolved once per `enrich_forecasts` call (on the `_GribTimer`), so a changed env var applies from the next briefing. The full `gc.collect()` after the cross-section clear in `pipeline.py` is unchanged (it is the one known to matter). Collections inside decode workers (`decode.py`) are unaffected.
- **Warm passes yield to interactive briefings (#490)** — the cgroup sits near its limit because warm cache writes are charged to it, so a concurrent anon-memory spike from a briefing's decode workers has no reclaim headroom (2026-07-23 05:09Z: OOM killer took the container down mid-refresh). The warm loop therefore checks `precache.interactive_refresh_active()` between units of work and bails out; the gate is True while `api/packs.py::refresh_registry` has anything queued/refreshing (user *or* scheduler-triggered) and for `WARM_YIELD_COOLDOWN_SECONDS` (60 s) after the last one finishes, so warming doesn't restart into the tail of a refresh. Registry entries older than `STALE_ENTRY_SECONDS` (30 min) count as leaked, not active, so a missed unregister can't disable warming until restart. A bailed-out pass reports `deferred=1` and `run_grib_precache_loop` skips both `last_done` and the cap walk, so the next tick resumes the same run — every already-fetched combo is an `is_cached` skip, making resumption near-free. Complements fsync + `POSIX_FADV_DONTNEED` on `put_cached` and the halved prefetch units for warms.
- **Parallel decode dispatch** — `_dispatch_decode_parallel(jobs)` submits a batch of `(worker_fn_name, args)` jobs to the pool concurrently and returns results in input order. Falls back to sequential in-process execution when `GRIB_DECODE_WORKERS=0`. Use this whenever a decode loop processes independent fhours/steps; sequential `_dispatch_decode` calls in a `for` loop only ever exercise one pool worker. The standalone ECMWF and GFS/ICON cloud-diag loops were switched onto it in #459. An optional `max_inflight=<int>` throttles concurrency *below* the pool (sliding window) for a memory-heavy batch — the standalone ECMWF leg passes `GRIB_DECODE_WORKERS_ECMWF` so a small-RAM fallback host can keep decode serial while the shared pool stays wide; see the grib-decode-dispatcher doc.
- **ECMWF local disk I/O** — no HTTP, no cache needed. Files delivered by ECPDS to `ECMWF_GRIB_DIR` (default `/data/ecmwf`). Read-only volume in Docker
- **Multi-grid handling** — ECMWF files may contain multiple geographic sub-grids (e.g. main Europe + Nordic extension). each sub-grid (`md5GridSection`) gets its own weights; the decoder uses first-wins per point across all sub-grids. The areas do not overlap, and nothing bridges the 59.5–60°N strip between Europe and Nordic (#672, still open)
- **ECMWF file naming** — structured convention: `dest_feed_model_class_stream_type_baseTime_validTime_step[_expver]`. Parsed by `parse_ecmwf_filename()` into `ECMWFFileInfo` dataclass. a1 = surface, a2 = pressure levels

### Gotchas
- **cfgrib uses lazy loading** — temp file must stay alive through interpolation, not just `open_datasets()`. Deleting too early causes `FileNotFoundError`.
- GFS S3 bucket has ~4.5h delay after init time before data is available
- ICON-EU: model levels (not pressure levels) — requires P field for vertical interpolation
- ICON-EU: bz2-compressed individual files, ~240 files per enrichment — parallel download essential
- Cache is shared across users (same model run = same data)
- **ECMWF no-cloud sentinel** — heights ≥ 9998m (nominally 9999m) mean "no cloud", converted to None
- **GFS / ICON / HRRR no-ceiling fill** (#847) — ICON `CEILING` ~16 km and NCEP HGT `cloudCeiling` 20,000 gpm mean "no ceiling"; `_drop_no_ceiling_fill` maps any ceiling (and ICON convective base / HRRR cloud base) ≥ 9999 m to None, the same cut-off as ECMWF. Convective tops are not cut. Bilinear per-point sampling can still blend the fill with a real ceiling at a cloud edge (meteorology-decisions §55)
- **ECMWF fractions** — `cc` (cloud cover) is 0–1 in GRIB, multiplied ×100 to match our `cloud_area_fraction_pct` convention. Surface covers (lcc/mcc/hcc/tcc) similarly converted in `build_ecmwf_cloud_diagnostics()`
- **Longitude convention is per dataset, not per model** (#673) — an ECMWF `a1` US area mixes GRIB1 messages on −128 … −71 with GRIB2 messages (ceil, CAPE/CIN, KX, TOTALX, ptype) on 232 … 289; the European GRIB2 grid crosses Greenwich and cfgrib shows it as −17.5 … 39.5. The shared interpolators (`_bilinear_grid_weights`, `_interpolate_per_point`) therefore map targets onto each dataset's own axis via `_align_lons_to_axis` (shift into `[min(axis), min(axis)+360)`; out-of-domain stays out). Decoders need no normalisation of their own (the GFS decoders' old `lon % 360` was removed). Lambert (HRRR) grids are projected and unaffected
- **ECMWF files may have no extension** — ECPDS default delivery has no `.grib2` suffix. Scanner accepts any filename matching the naming convention

## References

- Variable definitions: `fetch/variables.py`
- GRIB2 enrichment: `fetch/grib/`; decode pool + dispatcher: [grib-decode-dispatcher.md](./grib-decode-dispatcher.md)
- `fetch/{dwd,metoffice}_charts.py` + `chart_cache.py` live here but belong to the front-chart track — see [frontal-detection.md](./frontal-detection.md)
- Data models: [data-models.md](./data-models.md)
- Analysis consumers: [analysis.md](./analysis.md)
