# Frontal Detection — Implementation Plan for FlyFun Weather

> **Status (2026-06-13): largely IMPLEMENTED and promoted.** Phase 1 detection core +
> CLI is built and has been promoted to the live design doc
> [`designs/frontal-detection.md`](../frontal-detection.md) (indexed in INDEX.md) — read
> that for **current truth**. Several specifics in this plan are now stale: the gradient
> threshold is **2.0 K/100km** (not 0.8 — at 0.8 >50% of the European domain triggered),
> the grid is **0.25° / 35-60°N, -20-28°E** (not 0.5°), `_MIN_FRONTAL_POINTS = 32` (not 8),
> and the module set grew well beyond the plan: a **Hewson TFP route locator**
> (`detect.compute_hewson_diagnostics`, `sources.py`, `route_sampling.py`, `gates.py`'s
> `FrontGateConfig` preset registry, `contour_fronts.py`, `case.py`) plus a top-level `src/weatherbrief/hewson/`
> package (precompute/era5_case/cli — NOT under `frontal/`), `api/hewson_map.py`,
> `models/fronts.py`, `tasks/fronts.py`, and DB storage. The
> project **pivoted from zone-level detection toward per-leg Hewson advisories** — see
> `designs/future/hewson-fields-aviation-advisories.md` and `designs/future/front-calibration.md`.
>
> This plan is retained for its **forward-looking phases** (Phase 2-4 integration sequence,
> Future Enhancements) and its rejected-options reasoning. Treat Parts 1-3 as historical
> design intent, not current code. Do NOT calibrate against the numbers below.

## Overview

### Goal

Frontal detection is supporting infrastructure for the FlyFun Weather briefing products. It sits one level above the precision flight data (cloud layers, icing risk, convective indices, turbulence) with two specific jobs:

- **Synoptic text narrative**: feed structured frontal data to the LLM digest so it can write natural-language synoptic narrative — which fronts are active, where they are, when they are predicted to clear the route, and how much the models agree on that timing. A 12-hour model spread on frontal clearance over France is a more intuitive uncertainty signal than any raw model score.
- **Route frontal table and departure window**: deterministic, template-generated outputs showing front presence per zone per model, and when models agree the route clears.

Frontal detection does not need to locate a front to within 50km. It needs to be accurate enough to say "cold front over northern France moving southeast, expected to reach the Alps in 12-18 hours" — which is what the cross-section then shows in detail.

Cross-section annotation (overlaying frontal labels on the cross-section) is explicitly deferred — the cross-section shows front effects (cloud, icing, wind shifts) at route-point resolution, while frontal analysis operates at zone scale (~200-400km). Drawing annotation boundaries would imply precision the method doesn't have. This can be revisited if a good non-misleading presentation is found (e.g. a single route-level banner).

### Relationship to Regime Clustering

Frontal detection operates independently of regime clustering (see `synoptic-regime-clustering-plan.md`). The two touch points are narrow:
- The synoptic narrative opens with the regime label (if available)
- An uncertainty flag can combine regime confidence with frontal timing spread

Both are optional inputs — frontal detection is self-contained.

### Key Architecture Decisions

These decisions were made during planning discussions (2026-04-15):

**1. Separate background pipeline with CLI for development**
Frontal analysis runs as a standalone pipeline, decoupled from briefing generation. Briefings read pre-computed frontal results. The same core code is used in two modes:
- **CLI tool**: run frontal analysis on demand for interactive testing, validation, and development. This is the primary development workflow — get the detection right interactively before integrating into scheduled runs.
- **Scheduled pipeline**: once validated, runs on a schedule (twice daily after 00Z and 12Z model deliveries). Writes results to DB for briefings to consume.

The code must be structured so the same functions are called in both modes — no separate "CLI version" vs "pipeline version".

**2. Gridded data from Open-Meteo, 0.5deg resolution**
Frontal detection requires 2D gridded fields (not point soundings) to compute spatial temperature gradients. Rather than processing GRIB files (which may not be downloaded for all models), fetch a regular grid from Open-Meteo using the existing `OpenMeteoClient`.

- **Resolution**: 0.5deg (~55km). At 1deg, TFP second derivatives (gradient of the gradient) would be computed over only 2-3 cells, making front classification noisy. 0.5deg gives 4-6 cells across a typical frontal zone, with well-resolved second derivatives. Compute cost is trivial (numpy on ~4000 points); the only real cost is API requests.
- **Domain**: 35-60N, -12 to 28E (4131 grid points). Covers all defined analysis zones with margin.
- **Variables per grid point**: `temperature_850hPa`, `dewpoint_850hPa`, `wind_speed_850hPa`, `wind_direction_850hPa` (wind reconstructed to u/v, T+Td used for θe). Just 4 variables at 1 level — tiny compared to full sounding fetches.
- **Fetch volume**: Open-Meteo returns hourly time series per request, so all forecast horizons come back in one response. At 1000 points per chunk, that's ~5 requests per model × 3 models = **~15 requests total**. At 2 runs/day, ~900 API calls/month — negligible against the 1M monthly plan.
- **All three models provide 850hPa via Open-Meteo**: GFS (in extended levels), ECMWF (in 13-level set), ICON-EU (in 19-level set).
- **Chunk size**: the existing `OpenMeteoClient.fetch_multi_point()` accepts a `chunk_size` parameter (default 150 for full soundings). The frontal grid fetch uses 1000 — with only 4 variables the `hourly` param is ~90 chars, leaving the URL budget almost entirely for coordinates (~12KB per request, well within limits).

**3. LLM digest integration (structured data + LLM narrative)**
Two output types with different approaches:
- **Route frontal table**: deterministic, template-generated. Shows front presence per zone per model — must be exact, not LLM-generated.
- **Synoptic narrative**: structured frontal data (zone results, clearance timing, model agreement) fed as context to the LLM digest. The LLM writes natural prose informed by data it can't hallucinate.

This **complements** the current DWD surface chart pipeline. DWD Bodenwetterkarten show pressure centers, trough lines, and convergence zones that T850 gradients won't capture — the two products answer different questions. Both are fed to the LLM digest as structured context; the LLM synthesizes them into a coherent narrative. DWD charts may become optional once frontal analysis is well-validated, but the default should be to run both.

**4. Validation via Meteo-France carte des fronts**
Access to Meteo-France official front analysis charts is pending. Validation workflow:
1. Pull carte des fronts image for a given analysis time
2. Run TFP on corresponding model T850 field (or ERA5)
3. Plot detected fronts on a map
4. Cross-check visually (Claude vision can assist with comparison)

This calibrates the gradient threshold (starting at 0.8 K/100km), validates front type classification, and measures miss/false alarm rates. Build as an offline validation tool (notebook or CLI), not part of production.

### Architecture: Two Sub-Problems

1. **Detection algorithm**: gradient-magnitude thresholding on 850hPa temperature and θe fields, classifying cold/warm/indeterminate using wind-driven temperature advection. Pure meteorological signal processing.
2. **Route corridor framework**: an 18-zone geographic library covering European GA chokepoints, route templates, dynamic zone assembly. Indexes frontal results by geographic region and produces pilot-facing outputs (route table, digest context, departure window).

### Output Surfaces

| Component | Where it appears | Generation |
|---|---|---|
| Route frontal table | Per-zone model agreement table | Deterministic template |
| Synoptic narrative | Plain-language briefing text | LLM digest with structured frontal context |
| Departure window | Summary of when route clears | Deterministic from clearance timing |

---

## Module Structure

```
src/weatherbrief/frontal/
    __init__.py
    grid.py       — grid definition, lightweight Open-Meteo fetch, reshape to 2D arrays
    cache.py      — raw grid data cache for iterative development (keyed by model + init time)
    detect.py     — TFP computation, front classification, gradient thresholds
    zones.py      — zone definitions, route templates, zone intersection logic
    tracking.py   — zone timeseries, zone label classification from local timeseries
    pipeline.py   — orchestrates fetch -> detect -> label -> store, used by both CLI and scheduler
    cli.py        — CLI entry point for interactive testing and validation
```

The key design constraint: `pipeline.py` exposes functions like `run_frontal_analysis(models, horizons, ...)` that return structured results. The CLI calls these same functions and adds display/plotting. The scheduler calls these same functions and writes to DB.

---

## Part 1 — Data Fetch: Gridded 850hPa Fields

### 1.1 Grid Definition

```python
# grid.py

FRONTAL_GRID = {
    'lat_min': 35.0,
    'lat_max': 60.0,
    'lon_min': -12.0,
    'lon_max': 28.0,
    'resolution': 0.5,  # degrees
}

# Generates grid points covering all defined analysis zones:
# lat: 35.0, 35.5, 36.0, ..., 60.0  (51 points)
# lon: -12.0, -11.5, ..., 28.0      (81 points)
# total: 51 x 81 = 4131 points
```

### 1.2 Open-Meteo Fetch

Reuse the existing `OpenMeteoClient` infrastructure. For each model, fetch the full time series for all grid points:
- `temperature_850hPa` (returned in Celsius by Open-Meteo)
- `dewpoint_850hPa` (returned in Celsius — for θe computation, see §2.4)
- `wind_speed_850hPa` (returned in **knots** — the client sets `wind_speed_unit=kn`), `wind_direction_850hPa` (degrees)

**Confirmed availability**: all three models expose 850hPa temperature, dewpoint, wind speed, and wind direction via Open-Meteo. GFS has 27 pressure levels, ECMWF has 13, ICON-EU has 19 — all include 850hPa. Dewpoint is either provided directly or derived from T + RH via the Magnus formula (already implemented in the codebase). The only per-model gap relevant here is vertical_velocity (unavailable for ICON), which we don't need.

**Lightweight fetch path**: the existing `fetch_multi_point()` returns full `WaypointForecast` objects with all surface variables and all pressure levels — far more data than we need. Rather than forcing ~4000 grid points through the full sounding pipeline, add a **separate** `fetch_grid_fields()` method to `OpenMeteoClient` that:
- Accepts bare `(lat, lon)` coordinate lists (not `RoutePoint` objects — grid points aren't route points)
- Requests only `temperature_850hPa`, `dewpoint_850hPa`, `wind_speed_850hPa`, `wind_direction_850hPa` (4 variables)
- Skips all surface variables
- Returns raw numpy arrays directly (no `WaypointForecast` construction)
- Uses `chunk_size=1000` — with only 4 variables the `hourly` param is ~90 chars, leaving the URL budget almost entirely for coordinates. 1000 points × ~12 chars each ≈ 12KB URL, well within limits.

This is a separate method (not a mode flag on `fetch_multi_point()`) because the input types, output types, and chunk sizing are all different. It shares the same HTTP client, retry logic, and rate limiting. This keeps the frontal fetch fast and avoids building ~4000 heavyweight model objects.

**U/V reconstruction**: Open-Meteo returns speed and direction (meteorological convention: direction wind is coming *from*, in degrees). Wind speed is in knots; convert to km/h for advection calculations (1 kt = 1.852 km/h), then to u/v components:
```python
import numpy as np

KT_TO_KMH = 1.852

def wind_to_uv(speed_kt: np.ndarray, direction_deg: np.ndarray):
    """Convert wind speed (knots) + direction (meteorological) to u/v in km/h."""
    speed_kmh = speed_kt * KT_TO_KMH
    direction_rad = np.radians(direction_deg)
    u = -speed_kmh * np.sin(direction_rad)
    v = -speed_kmh * np.cos(direction_rad)
    return u, v

# Note: wind direction is circular (359° ≈ 1°). To eliminate the
# circular-averaging hazard structurally, call prepare_field() on
# u/v components (after wind_to_uv) rather than on raw speed/direction.
# Nearest-neighbor fill on u/v is safe — no circularity issue.
# Never fill raw direction with linear interpolation: averaging 350°
# and 10° as scalars gives 180° (southerly), not 0° (northerly).

# Sanity check: westerly wind (270°) → u > 0, v ≈ 0
# Southerly wind (180°) → u ≈ 0, v > 0
# These cases (and their reverses) should be covered by unit tests
# to catch sign errors in the meteorological convention.
```

**Reshaping**: the lightweight fetch returns raw arrays per variable per grid point. Reshape into 2D grids:
```python
def reshape_to_grid(raw: dict[str, list[list[float]]],
                    n_lat: int, n_lon: int,
                    hour_index: int) -> dict:
    """
    Extract a single forecast hour from raw Open-Meteo response
    and reshape into (n_lat, n_lon) 2D arrays.

    raw: {'temperature_850hPa': [[hourly values per point], ...], ...}
    Points are ordered lat-major (row by row from south to north).
    """
    T850 = np.array([pt[hour_index] for pt in raw['temperature_850hPa']]).reshape(n_lat, n_lon)
    Td850 = np.array([pt[hour_index] for pt in raw['dewpoint_850hPa']]).reshape(n_lat, n_lon)
    ws = np.array([pt[hour_index] for pt in raw['wind_speed_850hPa']]).reshape(n_lat, n_lon)
    wd = np.array([pt[hour_index] for pt in raw['wind_direction_850hPa']]).reshape(n_lat, n_lon)
    u850, v850 = wind_to_uv(ws, wd)

    # NaN fill on u/v (not speed/direction) — see wind_to_uv note on circularity.
    # prepare_field() is called on T850, Td850, u850, v850 — if any returns None
    # (≥5% missing), the entire forecast hour is skipped.
    T850 = prepare_field(T850)
    Td850 = prepare_field(Td850)
    u850 = prepare_field(u850)
    v850 = prepare_field(v850)
    if any(f is None for f in (T850, Td850, u850, v850)):
        return None  # too much missing data — caller skips this hour

    theta_e = compute_theta_e(T850, Td850, pressure_hPa=850.0)
    return {'T850': T850, 'Td850': Td850, 'theta_e': theta_e, 'u850': u850, 'v850': v850}
```

**NaN handling — field preparation before any computation**: Open-Meteo can return `null` for individual hourly values (especially at longer horizons or near model boundaries). All NaN must be resolved *before* the field reaches `gaussian_filter` or `np.gradient`, because both functions handle NaN incorrectly:
- `gaussian_filter` treats NaN as zero (or propagates it depending on scipy version), silently corrupting neighboring cells — not just producing NaN, but producing *wrong values* at valid cells.
- `np.gradient` propagates NaN through central differences: one NaN cell produces NaN gradients at all 4 adjacent cells, and TFP second derivatives propagate further (~12+ cells affected).

Letting NaN reach these functions is not a recoverable error — the corruption is silent and spreads.

```python
# grid.py

def prepare_field(raw_field: np.ndarray,
                  max_nan_fraction: float = 0.05) -> np.ndarray | None:
    """
    Resolve missing-data NaN before any computation.
    Returns a clean field with no NaN, or None if too much is missing.
    """
    nan_mask = np.isnan(raw_field)
    nan_frac = nan_mask.sum() / raw_field.size

    if nan_frac >= max_nan_fraction:
        return None  # skip this hour — log for monitoring

    if nan_frac == 0:
        return raw_field

    # Nearest-neighbor fill: the most conservative interpolation for
    # gradient analysis — it cannot invent gradients that don't exist
    # in the data (unlike linear interpolation, which could create
    # artificial slopes across filled regions).
    # Note: fill_terrain() uses linear interpolation for a different reason —
    # terrain cells are large contiguous clusters where nearest-neighbor would
    # create staircase artifacts. Missing-data NaN are typically isolated
    # cells where nearest-neighbor is appropriate.
    from scipy.interpolate import griddata
    valid = ~nan_mask
    coords_valid = np.argwhere(valid)
    coords_nan = np.argwhere(nan_mask)
    filled = raw_field.copy()
    filled[nan_mask] = griddata(
        coords_valid, raw_field[valid], coords_nan, method='nearest'
    )
    # Performance note: griddata rebuilds a KD-tree per call. With
    # ~864 calls per run (4 vars × 72h × 3 models), this could add up
    # if NaN is common. If profiling shows it's slow, switch to
    # scipy.ndimage.distance_transform_edt + index lookup, which is
    # faster for nearest-neighbor fill on regular grids.
    return filled
```

The `reshape_to_grid()` function calls `prepare_field()` on each variable immediately after reshaping. If any variable returns `None` (≥5% missing), the entire forecast hour is skipped and logged so pipeline monitoring can detect systematic data gaps.

**Fetch retry and partial failure**: the grid fetch must use the existing `OpenMeteoClient` retry logic (exponential backoff). If a chunk fails after retries, the pipeline continues with the remaining chunks rather than skipping the entire model. Each chunk covers a contiguous block of grid points — a missing chunk leaves a spatial gap, but detection can still run on zones that have full coverage. The pipeline tracks which grid regions have data and skips zones whose bounding box overlaps the gap (those zones would have artificial gradient boundaries at the gap edge). Zones fully outside the gap are unaffected. This means a single chunk failure degrades gracefully — typically losing 2-4 zones rather than the entire model — preserving inter-model comparison for the remaining zones. Log which zones were skipped so pipeline monitoring can detect persistent chunk failures.

**Fetch volume**: Open-Meteo returns the full hourly time series in each response, so all forecast horizons come back at once. At 1000 points per chunk: ~5 requests per model, ~15 requests total for all three models. At 2 runs/day, that's ~900 API calls/month — negligible against the 1M monthly plan. Under 30 seconds with the lightweight path.

**Raw data caching for development**: during Phase 1, the CLI is used iteratively — run analysis, inspect results, tune thresholds, re-run. Without caching, each iteration re-fetches ~15 API requests (~30s), which adds up fast during threshold calibration.

The cache stores raw Open-Meteo responses to disk, keyed by `(model, init_time)`. When the CLI requests data for a model whose init time matches a cached entry, the fetch is skipped entirely. This means threshold tuning, zone adjustments, and detection code changes require zero API calls.

```python
# cache.py

import json
import hashlib
from pathlib import Path

# Default: project data dir / frontal_cache. CLI can override via --cache-dir.
_DEFAULT_CACHE_DIR = Path("data/frontal_cache")

def _cache_key(model: str, init_time: str) -> str:
    """Cache key from model name and init time (e.g. 'ecmwf_2026041500')."""
    return f"{model}_{init_time}"

def save_raw_response(model: str, init_time: str, raw_data: dict,
                      cache_dir: Path = _DEFAULT_CACHE_DIR) -> Path:
    """Save raw Open-Meteo grid response to disk."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{_cache_key(model, init_time)}.json"
    path.write_text(json.dumps(raw_data))
    return path

def load_raw_response(model: str, init_time: str,
                      cache_dir: Path = _DEFAULT_CACHE_DIR) -> dict | None:
    """Load cached response, or None if not cached / stale."""
    path = cache_dir / f"{_cache_key(model, init_time)}.json"
    if path.exists():
        return json.loads(path.read_text())
    return None
```

The cache is **development-only** — the scheduled pipeline always fetches fresh data. The CLI enables it by default (`--no-cache` to force fresh fetch). Cache files are small (~2-4MB per model as JSON) and can be cleaned with `--clear-cache`. No TTL needed — the init_time in the key means a new model run naturally writes a new entry, and old entries are harmless (just disk space).

### 1.3 Forecast Horizons

**Analysis horizons**: hourly from T+0 through T+72. Since Open-Meteo returns the full hourly time series in a single response, there's zero additional fetch cost to analyze every hour. Hourly resolution is important for clearance timing — a fast-moving front can transit a small zone in 3-4 hours, which 6h snapshots would miss entirely.

For **output and storage**, results are aggregated to 6h blocks (T+0, 6, 12, 18, 24, 36, 48, 60, 72) — but clearance timing uses hourly precision internally to find the exact hour each zone clears.

**Horizon limits**:
- **T+0 to T+48**: models reliably place major fronts, position errors ~100km. Full confidence in clearance timing.
- **T+48 to T+72**: fronts still identifiable, position errors ~300-500km in Atlantic regime (exceeds method accuracy). Clearance timing usable but with significantly wider uncertainty — inter-model spread at these horizons is the primary confidence signal.
- **Beyond T+72**: individual fronts become unreliable. Not analyzed for clearance timing.

T+72 is the cutoff for route tables and departure window calculations.

### 1.4 Model Initialization Time Alignment

Inter-model comparison (clearance timing spread, agreement tables) requires comparing fields at the **same valid time**, not the same forecast horizon. GFS, ECMWF, and ICON have different init times and delivery schedules:
- **GFS**: init 00Z/06Z/12Z/18Z, data available ~3.5-4h after init
- **ECMWF**: init 00Z/12Z, data available ~7-8h after init
- **ICON-EU**: init 00Z/03Z/06Z/09Z/12Z/15Z/18Z/21Z, available ~2-3h after init

When the pipeline runs (e.g., at 10Z triggered by ECMWF 00Z delivery), the latest available runs differ: ECMWF 00Z, GFS 06Z, ICON-EU 09Z. The valid time for "T+24" is different for each.

**Approach**: align by valid time, not forecast horizon. The pipeline:
1. Determines the target valid times (hourly from now through +72h)
2. For each model, identifies the latest available init time
3. Maps each target valid time to the corresponding forecast horizon for that model's init time
4. Compares model outputs at matching valid times

This means a GFS 06Z T+18 forecast is compared with ECMWF 00Z T+24 — both valid at 00Z+24h = the same physical moment. The `frontal_analysis` DB table stores `valid_time` (or derives it from `run_date + init_hour + horizon_h`) so briefing queries can join across models on valid time.

Open-Meteo simplifies this: it always serves the latest available run for each model, and returns UTC timestamps for each hourly value. The pipeline can align on these timestamps directly rather than computing init-time offsets manually.

**Init time consistency**: Open-Meteo could update to a newer model run mid-fetch (e.g., GFS 12Z becomes available while chunked requests are in progress). This would mix init times within a single model's data. Mitigation: fetch model init time from the existing `meta.json` endpoint (via `fetch_model_metadata()` in `model_status.py`) *before* starting the grid fetch. Record the init time. After all chunks are fetched, re-check `meta.json` — if the init time has changed, re-fetch all chunks. This is simpler and more reliable than per-chunk response inspection (Open-Meteo forecast responses don't include init time metadata — it's only available from the separate `meta.json` endpoint). At ~5 requests per model taking <10 seconds total, mid-fetch model updates are rare.

### 1.5 ERA5 Fields for Validation

For offline validation against Meteo-France carte des fronts, ERA5 reanalysis provides "ground truth" gridded fields. Add to CDS request if needed:

```python
c.retrieve(
    'reanalysis-era5-pressure-levels',
    {
        'variable': ['temperature', 'u_component_of_wind', 'v_component_of_wind'],
        'pressure_level': ['850'],
        'area': [60, -12, 35, 28],   # N, W, S, E
        'grid': [1.0, 1.0],
        ...
    },
    'era5_850hPa_validation.nc'
)
```

This is only needed for validation, not production.

---

## Part 2 — Frontal Zone Detection

### 2.1 Thermal Front Parameter Method

Inspired by the thermal front parameter (TFP) literature (Hewson 1998), but **simplified to gradient-magnitude thresholding** for zone-scale presence detection. The core idea: a frontal zone is a region where the horizontal temperature gradient at 850hPa exceeds a calibrated threshold.

Hewson's full TFP method locates front *lines* via zero-crossings of the TFP field combined with gradient and second-derivative masking. That precision is unnecessary for our purpose — we need zone-scale presence ("is there a front in northern France?"), not line-drawing. Gradient thresholding is simpler, more robust at 0.5° resolution, and sufficient for zone-level detection with fractional coverage filtering.

The TFP field is still computed and retained for **CLI map plotting only**, where TFP zero-crossings give sharper visual output for validation against Meteo-France carte des fronts. It is not used in the detection pipeline, zone results, or LLM digest.

```python
# detect.py

import numpy as np
from scipy.ndimage import gaussian_filter

def compute_frontal_zones(T850: np.ndarray,
                           lat: np.ndarray,
                           lon: np.ndarray,
                           smooth_sigma: float = 0.5,
                           gradient_threshold: float = 0.8,
                           terrain_mask: np.ndarray = None) -> dict:
    """
    Detect frontal zones from 850hPa temperature field.

    T850: 2D array (lat x lon) in Celsius — must be NaN-free
        (missing data resolved by prepare_field() before this call).
        Gradient thresholds are in K/100km, which equals °C/100km — no
        conversion needed (θe fields are in Kelvin but same gradient units).
    lat, lon: 1D coordinate arrays (degrees)
    smooth_sigma: Gaussian smoothing sigma in grid points. At 0.5deg resolution
        the grid is already fairly coarse, so minimal smoothing suffices.
        0.5 (= 0.25deg) removes single-cell noise without blurring narrow fronts.
    gradient_threshold: K per 100km — frontal zone threshold
    terrain_mask: boolean (True=valid). Terrain cells are filled with
        smoothly interpolated values before computation so they don't
        create artificial gradients at boundaries. The mask is applied
        to results at the end — see §2.4 for rationale.

    Returns dict with:
        'gradient': 2D gradient magnitude field (K per 100km)
        'frontal_mask': boolean mask of frontal zones (terrain excluded)
        'tfp': thermal front parameter field
        'T_smooth': smoothed, terrain-filled T850 — pass to classify_front_type()
    """
    # Fill terrain cells before smoothing (see §2.4 for full rationale).
    # Smooth interpolation from valid neighbors prevents artificial gradients
    # at terrain boundaries. We don't care about the filled values themselves
    # — only that they don't corrupt gradients at adjacent valid cells.
    # The terrain mask is applied to the frontal_mask at the end.
    T_input = T850
    if terrain_mask is not None:
        T_input = fill_terrain(T850, terrain_mask)

    # Smooth temperature to remove single-cell noise.
    # T_input is NaN-free (missing data resolved by prepare_field(),
    # terrain filled above), so gaussian_filter operates correctly.
    T_smooth = gaussian_filter(T_input, sigma=smooth_sigma)

    # Compute grid spacing in km — dlat is constant, dlon varies with latitude
    dlat_km = 111.0  # km per degree latitude (constant)
    dlon_km = 111.0 * np.cos(np.radians(lat))  # 1D array, one value per latitude row
    # At 35N: ~91 km/deg, at 60N: ~55 km/deg — ~40% variation across domain

    # Temperature gradient components (K per km)
    # Use latitude-varying spacing for the longitude (x) axis.
    # Both axes use np.gradient's built-in spacing for consistent
    # finite-difference treatment (including one-sided at boundaries).
    dlat_spacing = dlat_km * np.abs(np.diff(lat).mean())  # scalar (km)
    dlon_spacing_per_row = dlon_km * np.abs(np.diff(lon).mean())  # 1D (n_lat,)

    dT_dy = np.gradient(T_smooth, dlat_spacing, axis=0)
    # For lon axis, spacing varies by latitude row — compute per-row
    # then divide, since np.gradient doesn't accept per-row spacing.
    dT_dx = np.gradient(T_smooth, axis=1) / dlon_spacing_per_row[:, np.newaxis]

    # Gradient magnitude (K per km)
    grad_mag = np.sqrt(dT_dx**2 + dT_dy**2)

    # Convert to K per 100km for threshold comparison
    grad_mag_100km = grad_mag * 100.0

    # Frontal zone mask — exclude terrain cells from results
    frontal_mask = grad_mag_100km > gradient_threshold
    if terrain_mask is not None:
        frontal_mask &= terrain_mask

    # Thermal Front Parameter (TFP) — Hewson 1998 simplified
    # TFP = -nabla|nablaT| . (nablaT / |nablaT|)
    # Zero crossings of TFP within frontal zones locate the front line
    grad_norm = np.where(grad_mag > 1e-10, grad_mag, 1e-10)
    unit_grad_x = dT_dx / grad_norm
    unit_grad_y = dT_dy / grad_norm

    # Second derivatives must use physical spacing (km) to match unit vectors
    d_gradmag_dy = np.gradient(grad_mag, dlat_spacing, axis=0)
    d_gradmag_dx = np.gradient(grad_mag, axis=1) / dlon_spacing_per_row[:, np.newaxis]
    tfp = -(d_gradmag_dx * unit_grad_x + d_gradmag_dy * unit_grad_y)

    # Front orientation: the temperature gradient points perpendicular to the
    # front (warm→cold). The front *line* runs parallel to isotherms, i.e.
    # 90° from the gradient. Compute as compass bearing (0°=N, 90°=E).
    # atan2(dT_dx, dT_dy) gives the gradient direction; add 90° for front
    # orientation. Only meaningful at frontal points — elsewhere it's noise.
    grad_direction = np.degrees(np.arctan2(dT_dx, dT_dy))  # gradient bearing
    front_orientation = (grad_direction + 90.0) % 360.0     # front line bearing

    return {
        'gradient': grad_mag_100km,
        'frontal_mask': frontal_mask,
        'tfp': tfp,
        'T_smooth': T_smooth,
        'front_orientation': front_orientation,
        'dT_dx': dT_dx,
        'dT_dy': dT_dy,
    }
```

**Front orientation**: the gradient direction at each frontal point gives the front's orientation for free — the front line runs perpendicular to the temperature gradient. This is computed as a compass bearing (0°=N, 90°=E, etc.) and averaged over each zone's frontal points to produce a single orientation label like "NE-SW" for the LLM narrative context. A zone-mean orientation is meaningful because real fronts are coherent — the gradient direction doesn't vary wildly across a zone's frontal points. The circular mean (averaging unit vectors, not raw angles) avoids the 359°/1° wraparound problem.

```python
# zones.py — added to find_fronts_in_regions() result per zone

_COMPASS_LABELS = [
    (  0, 'N-S'), ( 22.5, 'NNE-SSW'), ( 45, 'NE-SW'), ( 67.5, 'ENE-WSW'),
    ( 90, 'E-W'), (112.5, 'ESE-WNW'), (135, 'SE-NW'), (157.5, 'SSE-NNW'),
]

def _orientation_label(front_orientation: np.ndarray,
                       frontal_in_region: np.ndarray) -> str:
    """
    Compute mean front orientation over a zone's frontal points.
    Returns a compass label like 'NE-SW'.

    Uses circular mean (average of unit vectors) to handle wraparound.
    Orientation is axial (0° ≡ 180°), so double the angle before averaging
    to map to a full circle, then halve the result.
    """
    bearings = front_orientation[frontal_in_region]
    # Axial mean: double angles, average as vectors, halve result
    rad2 = np.radians(2 * bearings)
    mean_angle = np.degrees(np.arctan2(np.mean(np.sin(rad2)),
                                        np.mean(np.cos(rad2)))) / 2
    mean_angle = mean_angle % 180  # normalize to [0, 180)

    # Find closest compass label
    best = min(_COMPASS_LABELS, key=lambda c: min(
        abs(mean_angle - c[0]), 180 - abs(mean_angle - c[0])
    ))
    return best[1]
```

The orientation label is included in the zone result dict and in the structured LLM digest context — enabling narratives like "cold front oriented NE-SW across northern France" instead of just "cold front in northern France."

Note: `dT_dx` and `dT_dy` are also returned from `compute_frontal_zones()` so that `classify_front_type()` can reuse them directly rather than recomputing the gradient (see §2.2).

### 2.2 Front Classification: Cold vs Warm

Classify each detected frontal zone as cold, warm, or indeterminate using temperature advection in physical units.

```python
def classify_front_type(dT_dx: np.ndarray,
                         dT_dy: np.ndarray,
                         u850: np.ndarray,
                         v850: np.ndarray,
                         frontal_mask: np.ndarray,
                         advection_threshold: float = 0.5,
                         detected_by: np.ndarray = None) -> np.ndarray:
    """
    For each frontal zone grid point, classify as:
        1 = cold front (cold air advancing)
        2 = warm front (warm air advancing)
        3 = indeterminate (advection near-zero or ambiguous)
        0 = not a front

    Uses temperature advection: -V . nablaT
    Negative advection = cold front, positive = warm front.
    Points with weak advection are labeled "indeterminate" rather than
    "occluded" — true occluded fronts require multi-level structure and
    cyclone-relative geometry that single-level T850 cannot resolve.
    Stationary fronts (high gradient, near-zero advection) also fall
    into this category. The LLM can infer stationarity from the
    timeseries (front present for many consecutive hours without clearing).

    dT_dx, dT_dy: temperature gradient components (K/km) from
    compute_frontal_zones(). Reusing the same gradient ensures
    classification is consistent with detection — no risk of
    divergence from recomputing with different parameters.

    advection_threshold: K/hr — minimum advection magnitude to classify.
        At typical frontal wind speeds (30-50 km/h) and gradients
        (1-3 K/100km), advection is ~0.3-1.5 K/hr. 0.5 K/hr filters
        out weak/ambiguous cases.

    u850, v850: wind components in km/h (already converted from knots
    by wind_to_uv()).

    Note on advection evaluation location: at the actual front axis,
    wind is often nearly parallel to isotherms, making V·∇T ≈ 0.
    This produces a ring of "indeterminate" classifications around
    every front. To improve classification, evaluate advection 1-2
    grid cells offset from each frontal point along the ∇T direction
    (toward the warm side for cold front identification, cold side
    for warm). Implementation: for each frontal point, step 1-2 cells
    along (dT_dx, dT_dy) unit vector and sample advection there.
    If offset advection is clearly negative → cold; clearly positive
    → warm. Fall back to on-point advection if offset is out of bounds.

    detected_by: optional uint8 array from compute_frontal_zones_dual().
        Bit 0 = T850 detection, bit 1 = θe detection. Points detected
        only by θe (value == 2) with weak T advection are biased toward
        warm front rather than indeterminate — these are precisely the warm
        fronts that θe was added to catch. Validate during Phase 1.
    """
    # Temperature advection: -(u * dT/dx + v * dT/dy)
    # u850/v850 in km/h, dT/dx in K/km => advection in K/hr
    T_adv = -(u850 * dT_dx + v850 * dT_dy)

    front_type = np.zeros(frontal_mask.shape, dtype=int)

    cold_mask = frontal_mask & (T_adv < -advection_threshold)
    warm_mask = frontal_mask & (T_adv > advection_threshold)
    indeterminate_mask = frontal_mask & ~cold_mask & ~warm_mask

    # Points detected only by θe (bit 1 in detected_by) with weak T850
    # advection are likely warm fronts — the whole reason θe detection
    # exists is to catch warm fronts with weak T gradients. Bias these
    # toward warm rather than defaulting to indeterminate.
    # Validate this assumption during Phase 1 against carte des fronts.
    if detected_by is not None:
        theta_e_only = (detected_by == 2)  # bit 1 only, no T850 detection
        reclassify = indeterminate_mask & theta_e_only
        indeterminate_mask = indeterminate_mask & ~reclassify
        warm_mask = warm_mask | reclassify

    front_type[cold_mask] = 1
    front_type[warm_mask] = 2
    front_type[indeterminate_mask] = 3

    return front_type
```

### 2.3 Equivalent Potential Temperature (θe) for Moisture-Gradient Fronts

T850 gradients reliably detect cold fronts but often miss warm fronts, where the temperature contrast is weak but the moisture contrast is sharp. Equivalent potential temperature (θe) incorporates both temperature and moisture, making it the better discriminator for warm fronts — and a useful complement to T850 for all front types.

**θe computation**: from T850 (°C) and Td850 (dewpoint, °C) at 850hPa:

```python
# detect.py

import metpy.calc as mpcalc
from metpy.units import units

def compute_theta_e(T850: np.ndarray, Td850: np.ndarray,
                     pressure_hPa: float = 850.0) -> np.ndarray:
    """
    Compute equivalent potential temperature from T and Td at 850hPa.

    Uses MetPy (already a project dependency for thermodynamic computations).
    Returns θe in Kelvin as a 2D array matching input shape.
    """
    theta_e = mpcalc.equivalent_potential_temperature(
        pressure_hPa * units.hPa,
        T850 * units.degC,
        Td850 * units.degC,
    )
    return theta_e.to('kelvin').magnitude
```

**Dual-gradient detection**: run the same gradient analysis on both T850 and θe fields, then combine:

```python
def compute_frontal_zones_dual(T850, theta_e, lat, lon,
                                terrain_mask=None,
                                t_gradient_threshold=0.8,
                                te_gradient_threshold=4.0,
                                **kwargs):
    """
    Detect frontal zones using both T850 and θe gradients.

    A grid point is frontal if EITHER gradient exceeds its threshold.
    Cold fronts show up in both fields; warm fronts primarily in θe.

    te_gradient_threshold: K per 100km for θe. Higher than T threshold
    because θe has a larger dynamic range. Across a typical front, θe
    varies ~15-20K over ~200km (7.5-10 K/100km) vs ~5-8K for T850
    (2.5-4 K/100km). Starting value 4.0 — at 2.0, nearly every θe
    gradient in the domain would exceed threshold (air mass boundaries,
    moisture plumes, sea-land contrasts all produce θe gradients of
    2-3 K/100km without any front). 4.0 catches real fronts while
    filtering synoptic-scale moisture variability. Calibrate alongside
    the T threshold during Phase 1 validation — tune down if warm
    fronts are missed, up if moisture boundaries trigger false positives.

    Returns the same dict as compute_frontal_zones(), with additional
    fields for the θe gradient and which detection triggered each point.
    """
    t_result = compute_frontal_zones(T850, lat, lon,
                                      terrain_mask=terrain_mask,
                                      gradient_threshold=t_gradient_threshold,
                                      **kwargs)
    te_result = compute_frontal_zones(theta_e, lat, lon,
                                       terrain_mask=terrain_mask,
                                       gradient_threshold=te_gradient_threshold,
                                       **kwargs)

    # Union of both masks — front detected by either method
    combined_mask = t_result['frontal_mask'] | te_result['frontal_mask']

    # Track which method triggered detection (useful for diagnostics)
    detected_by = np.zeros_like(T850, dtype=np.uint8)
    detected_by[t_result['frontal_mask']] |= 1   # bit 0 = T850
    detected_by[te_result['frontal_mask']] |= 2   # bit 1 = θe

    return {
        **t_result,
        'frontal_mask': combined_mask,
        'te_gradient': te_result['gradient'],
        'te_frontal_mask': te_result['frontal_mask'],
        'detected_by': detected_by,
    }
```

**Why this works**: a simple OR-union is the right fusion rule because T850 and θe catch *different* fronts:
- **Cold fronts**: strong T850 gradient AND strong θe gradient → detected by both (no change vs T-only)
- **Warm fronts**: weak T850 gradient but strong θe gradient → detected by θe (the key improvement)
- **Noise**: random noise would need to exceed threshold in *either* field, slightly raising the false-positive rate. Mitigated by the terrain mask and the `_MIN_FRONTAL_FRACTION` zone threshold — isolated noisy cells don't cross the zone coverage threshold.

**Fetch cost**: adding `dewpoint_850hPa` increases the `hourly` param from ~67 chars to ~90 chars — negligible. Chunk size of 1000 is still well within URL limits.

**Threshold calibration**: the θe gradient threshold (starting at 4.0 K/100km) must be calibrated alongside the T threshold during Phase 1 validation. Use the Meteo-France carte des fronts, specifically looking at warm front segments that T850 alone misses. If 4.0 misses too many warm fronts, lower cautiously — but below 3.0, moisture boundaries (sea-land, dry/moist air mass contacts) start triggering false positives.

**Impact on front classification**: `classify_front_type()` continues to use T850 advection (not θe advection) for cold/warm/indeterminate classification. The θe gradient only improves *detection* — determining whether a front exists. Once detected, the physical advection pattern still determines front type. Points detected only by θe (bit 1 in `detected_by`) are likely warm fronts, which provides a useful cross-check.

### 2.4 Terrain Masking — Filtering Orographic False Positives

The Alps, Pyrenees, and Scandinavian mountains create persistent T850 temperature gradients that are orographic, not frontal. At 850hPa (~1500m / ~4920ft), valleys in the Alps are below ground level — the model extrapolates T850 below terrain, creating artificial gradients that would be detected as permanent "fronts" in mountainous zones (`alps`, `iberia_north`, `scandinavia_south`).

**Solution**: a two-step fill-then-mask approach. The terrain mask identifies grid points above 1500m. Before any computation, those cells are filled with values smoothly interpolated from surrounding valid cells. After all computation (smoothing, gradient, thresholding), the mask is applied to the *results* — fronts cannot be detected on terrain, but terrain cells don't corrupt gradients at neighboring valid cells. See "Integration with detection" below for the full rationale.

The existing SRTM3 terrain framework (`weatherbrief.fetch.elevation`) provides 90m-resolution elevation lookups at arbitrary lat/lon points via `srtm.get_data().get_elevation(lat, lon)`.

```python
# grid.py

import srtm
from weatherbrief.fetch.elevation import SRTM_CACHE_DIR

# 850hPa ≈ 1500m in standard atmosphere
_TERRAIN_MASK_THRESHOLD_M = 1500

def build_terrain_mask(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """
    Build a boolean mask (True = valid, False = terrain above 850hPa).

    Uses SRTM3 (90m resolution) for elevation lookups. At the 0.5deg
    frontal grid (~55km spacing), each grid point represents a large area —
    use the SRTM point elevation as representative. Points where terrain
    exceeds 1500m are masked out so orographic temperature gradients
    don't generate false frontal detections.

    The mask is static for a given grid and can be computed once and reused.
    """
    elevation_data = srtm.get_data(
        local_cache_dir=str(SRTM_CACHE_DIR), srtm3=True
    )
    mask = np.ones((len(lat), len(lon)), dtype=bool)

    for i, la in enumerate(lat):
        for j, lo in enumerate(lon):
            elev = elevation_data.get_elevation(la, lo)
            if elev is not None and elev > _TERRAIN_MASK_THRESHOLD_M:
                mask[i, j] = False

    return mask
```

**Integration with detection — fill-then-mask, not NaN**:

The original approach was to set terrain cells to NaN before gradient computation, relying on NaN propagation through `np.gradient` to create a "buffer" at terrain boundaries. This has three problems:

1. **`gaussian_filter` doesn't handle NaN.** If smoothing runs before NaN insertion, terrain-adjacent cells absorb the extrapolated below-ground values. If NaN is inserted before smoothing, `gaussian_filter` corrupts a blob proportional to the kernel size. Either order is wrong.
2. **NaN propagation is uncontrolled.** One NaN cell produces NaN gradients at 4 neighbors. A cluster of mountain cells creates a large irregular excluded zone whose shape depends on cell arrangement, not physical reasoning. TFP second derivatives propagate further (~12+ cells).
3. **It conflates two operations.** "Exclude this point from results" and "prevent this point from corrupting neighbor gradients" are different goals. NaN does both, but crudely — you can't tune the exclusion zone or the boundary behavior independently.

The correct approach is **fill-then-mask**: fill terrain cells with smoothly interpolated values from surrounding valid cells *before* any computation, then apply the terrain mask to *results* at the end.

```python
# grid.py

def fill_terrain(field: np.ndarray, terrain_mask: np.ndarray) -> np.ndarray:
    """
    Replace terrain-masked cells with values interpolated from valid neighbors.

    The goal is NOT to produce a physical value at terrain cells — it's to
    prevent artificial gradients at terrain boundaries. Linear interpolation
    creates smooth transitions, so the gradient at a valley cell adjacent to
    terrain reflects the large-scale field rather than the below-ground
    extrapolation artifact.

    The filled values never appear in results — compute_frontal_zones()
    applies terrain_mask to the frontal_mask at the end, so only valid
    lowland cells can be reported as frontal.
    """
    valid = terrain_mask  # True = below 1500m
    if valid.all():
        return field

    from scipy.interpolate import griddata
    coords_valid = np.argwhere(valid)
    coords_invalid = np.argwhere(~valid)
    filled = field.copy()
    # Linear interpolation for smooth transitions — no artificial gradients
    filled[~valid] = griddata(
        coords_valid, field[valid], coords_invalid, method='linear'
    )
    # griddata returns NaN for points outside the convex hull of valid data
    # (domain edges near terrain). Fall back to nearest for those.
    still_nan = np.isnan(filled)
    if still_nan.any():
        filled[still_nan] = griddata(
            coords_valid, field[valid], np.argwhere(still_nan), method='nearest'
        )
    return filled
```

**Why fill-then-mask is correct**: we don't care what value terrain cells hold after filling — they're excluded from results at the end. We only care that they don't create artificial gradients at their *neighbors*. Smooth fill achieves this: the gradient at a valley cell adjacent to terrain reflects the large-scale temperature field, not the extrapolated below-ground artifact. The only scenario where this could matter is a real front running along a terrain boundary — but that's exactly where T850 gradients are unreliable regardless, and where conservative detection is preferred.

**Pipeline order**:
```
T850 (NaN-free after prepare_field())
  → fill_terrain()          # smooth fill at terrain cells
  → gaussian_filter()       # clean input, correct output everywhere
  → np.gradient()           # valid gradients at all cells including terrain-adjacent
  → gradient thresholding   # no NaN, just real values
  → frontal_mask &= terrain_mask  # exclude terrain from results
```

No NaN reaches any computation step. Smoothing, gradient, and TFP all operate on a clean, continuous field.

**Performance**: both `build_terrain_mask()` and `fill_terrain()` depend only on grid geometry and are cheap. The mask is computed once at startup (or cached to disk) and reused across all models and forecast hours. `fill_terrain()` runs once per field per forecast hour — `griddata` on ~1000 terrain cells among 4131 total takes <100ms. Total overhead is negligible.

**Impact on affected zones**: the `alps` zone (45-49N, 6-16E) has ~30-40% of its grid points masked, but the remaining lowland points (Po Valley margin, Bavarian foreland, Rhine valley) are exactly where frontal detection matters for GA routing. Unlike the NaN approach, gradients at these valley cells are computed correctly — they see smooth interpolated values on the mountain side rather than extrapolated below-ground artifacts or NaN. Similarly, `iberia_north` loses Pyrenean ridge points but keeps the Ebro valley and Atlantic approaches with clean gradient computation at terrain edges.

---

## Part 3 — Route Corridor Framework

### 3.1 Analysis Zones

18 geographic boxes covering European GA chokepoints. Each zone is at least 3x4 degrees, giving a minimum of ~96 grid points at 0.5deg resolution — plenty for the fractional coverage threshold to distinguish real fronts from noise, and for gradient/TFP second derivatives to be well-resolved.

Design principles:
- **Sized for detection, not annotation**: zones must be large enough for reliable gradient analysis, not chopped into narrow corridors that look good on a map but have 3 grid points.
- **Operationally named**: each zone maps to a region a pilot would recognize.
- **Minimal overlap**: some overlap at boundaries is acceptable (a front spanning two zones is correctly reported in both).
- **Refinable**: the zone dict is the only thing that changes if we need to split or merge. Detection and pipeline code is zone-agnostic.

Possible future refinement: splitting France east/west (Atlantic vs Rhone/Alps approach). At 0.5deg, each half would have ~70 grid points — well above the minimum needed. Let validation data drive this decision.

```python
# zones.py

ZONES = {
    # British Isles
    'uk_south':              {'lat': (49, 53), 'lon': ( -6,   3), 'display': 'Southern England & Channel'},
    'uk_north_ireland':      {'lat': (53, 59), 'lon': (-10,   0), 'display': 'Northern UK & Ireland'},

    # Low Countries / North Sea
    'benelux_north_sea':     {'lat': (50, 55), 'lon': (  2,   8), 'display': 'Benelux & North Sea'},

    # Germany
    'n_germany_baltic':      {'lat': (52, 57), 'lon': (  8,  16), 'display': 'Northern Germany & Baltic'},
    'central_germany':       {'lat': (48, 52), 'lon': (  7,  14), 'display': 'Central Germany'},

    # Scandinavia
    'scandinavia_south':     {'lat': (55, 60), 'lon': (  8,  20), 'display': 'Southern Scandinavia'},

    # France
    'north_france':          {'lat': (47, 51), 'lon': ( -2,   6), 'display': 'Northern France'},
    'south_france':          {'lat': (43, 47), 'lon': ( -1,   7), 'display': 'Southern France'},

    # Alps
    'alps':                  {'lat': (45, 49), 'lon': (  6,  16), 'display': 'Alps & Bavaria'},

    # Biscay & Iberia
    'bay_of_biscay':         {'lat': (43, 48), 'lon': ( -8,  -1), 'display': 'Bay of Biscay'},
    'iberia_north':          {'lat': (40, 44), 'lon': ( -9,   1), 'display': 'Northern Iberia & Pyrenees'},
    'iberia_south':          {'lat': (36, 40), 'lon': ( -9,   0), 'display': 'Central & Southern Iberia'},

    # Mediterranean
    'western_med':           {'lat': (40, 44), 'lon': (  3,  10), 'display': 'Western Mediterranean'},
    'balearics':             {'lat': (37, 41), 'lon': ( -1,   5), 'display': 'Balearic Islands'},

    # Italy
    'po_valley':             {'lat': (43, 47), 'lon': (  8,  14), 'display': 'Po Valley & Northern Italy'},
    'central_south_italy':   {'lat': (37, 43), 'lon': ( 11,  17), 'display': 'Central & Southern Italy'},

    # Adriatic & Balkans
    'adriatic':              {'lat': (41, 46), 'lon': ( 13,  19), 'display': 'Adriatic'},
    'balkans':               {'lat': (38, 46), 'lon': ( 19,  27), 'display': 'Balkans & Greece'},
}

# Grid points per zone at 0.5deg resolution (all >= 96):
# uk_south: 9x19=171, uk_north_ireland: 13x21=273, benelux_north_sea: 11x13=143,
# n_germany_baltic: 11x17=187, central_germany: 9x15=135, scandinavia_south: 11x25=275,
# north_france: 9x17=153, south_france: 9x17=153, alps: 9x21=189,
# bay_of_biscay: 11x15=165, iberia_north: 9x21=189, iberia_south: 9x19=171,
# western_med: 9x15=135, balearics: 9x13=117, po_valley: 9x13=117,
# central_south_italy: 13x13=169, adriatic: 11x13=143, balkans: 17x17=289
```

### 3.2 Route Templates

Ordered zone sequences for major European GA route families. With 18 zones, routes are shorter and simpler — typically 3-5 zones per route.

```python
ROUTE_TEMPLATES = {
    # UK departures
    'uk_alps':           ['uk_south', 'north_france', 'south_france', 'alps'],
    'uk_western_med':    ['uk_south', 'north_france', 'south_france', 'western_med'],
    'uk_iberia':         ['uk_south', 'north_france', 'bay_of_biscay', 'iberia_north'],
    'uk_balearics':      ['uk_south', 'north_france', 'south_france', 'balearics'],
    'uk_italy':          ['uk_south', 'north_france', 'alps', 'po_valley'],
    'uk_greece':         ['uk_south', 'north_france', 'alps', 'adriatic', 'balkans'],

    # Germany/Benelux departures
    'germany_alps':      ['central_germany', 'alps'],
    'germany_italy':     ['central_germany', 'alps', 'po_valley'],
    'germany_adriatic':  ['central_germany', 'alps', 'adriatic'],
    'germany_med':       ['central_germany', 'south_france', 'western_med'],
    'benelux_med':       ['benelux_north_sea', 'north_france', 'south_france', 'western_med'],
    'benelux_iberia':    ['benelux_north_sea', 'north_france', 'bay_of_biscay', 'iberia_north'],

    # Scandinavian departures
    'scandinavia_uk':    ['scandinavia_south', 'benelux_north_sea', 'uk_south'],
    'scandinavia_alps':  ['scandinavia_south', 'n_germany_baltic', 'central_germany', 'alps'],

    # Within-Mediterranean / southern routes
    'iberia_balearics':  ['iberia_north', 'iberia_south', 'balearics'],
    'france_med':        ['south_france', 'western_med'],
    'italy_greece':      ['po_valley', 'adriatic', 'balkans'],
}
```

### 3.3 Dynamic Route Assembly

For arbitrary departure/destination airports, assemble a zone sequence. The briefing pipeline already computes route waypoints (stored in `FlightRow` / used for cross-section generation), so reuse those coordinates rather than adding a `pyproj` dependency:

```python
def find_route_zones(waypoints: list[tuple[float, float]]) -> list[str]:
    """
    Given route waypoints [(lat, lon), ...] from the flight planning
    pipeline, find which ZONES each falls in and return ordered unique
    zone list.

    For CLI use without pre-computed waypoints, a simple great-circle
    interpolation can generate the waypoint list.

    Note: some zones overlap at boundaries (e.g. north_france/south_france
    share the 47N parallel). A waypoint in an overlap region is assigned
    to the first matching zone in ZONES iteration order (insertion order).
    This is deterministic but arbitrary — acceptable because both zones
    would report the same front anyway. If this causes confusing route
    tables, consider making zones non-overlapping at boundary parallels.
    """
    route_zones = []
    for lat, lon in waypoints:
        for zone_name, bounds in ZONES.items():
            if (bounds['lat'][0] <= lat <= bounds['lat'][1] and
                    bounds['lon'][0] <= lon <= bounds['lon'][1]):
                if not route_zones or route_zones[-1] != zone_name:
                    route_zones.append(zone_name)
                break

    return route_zones
```

### 3.4 Zone Intersection

Check whether detected frontal zones overlap with analysis zones.

```python
def _zone_grid_count(lat: np.ndarray, lon: np.ndarray, bounds: dict) -> int:
    """Count how many grid points fall within a zone's bounds."""
    lat_grid, lon_grid = np.meshgrid(lat, lon, indexing='ij')
    mask = (
        (lat_grid >= bounds['lat'][0]) & (lat_grid <= bounds['lat'][1]) &
        (lon_grid >= bounds['lon'][0]) & (lon_grid <= bounds['lon'][1])
    )
    return int(mask.sum())


# Minimum fraction of zone grid points that must be frontal to count
# as "front present". A real front is a *line*, not an area — at 0.5deg
# resolution with light smoothing, a front crossing a 9x17 zone diagonally
# is only 1-2 cells wide, touching ~10-15% of grid points. 0.08 catches
# narrow fronts while filtering isolated gradient noise.
# Tune upward if false positives appear during validation.
_MIN_FRONTAL_FRACTION = 0.08

# Absolute minimum frontal points alongside the fraction. A fraction-only
# threshold is biased by zone size — a narrow front crossing balkans
# (289 pts) covers ~5-8% vs ~20% in balearics (117 pts). The absolute
# floor prevents large zones from systematically under-detecting.
# Both thresholds must be met: fraction >= 0.08 AND count >= 8.
_MIN_FRONTAL_POINTS = 8


def find_fronts_in_regions(frontal_mask: np.ndarray,
                            front_type: np.ndarray,
                            gradient: np.ndarray,
                            front_orientation: np.ndarray,
                            lat: np.ndarray,
                            lon: np.ndarray,
                            terrain_mask: np.ndarray = None,
                            regions: dict = None) -> dict:
    """
    For each region, return whether a front is present, its type,
    intensity (peak gradient), and orientation.
    regions defaults to ZONES if not provided.
    terrain_mask: if provided, the frontal fraction denominator uses
    only unmasked points (terrain below 850hPa) rather than total
    zone area. This prevents terrain-heavy zones like 'alps' from
    having artificially low frontal fractions.
    """
    if regions is None:
        regions = ZONES

    results = {}
    lat_grid, lon_grid = np.meshgrid(lat, lon, indexing='ij')

    for region_name, bounds in regions.items():
        region_mask = (
            (lat_grid >= bounds['lat'][0]) & (lat_grid <= bounds['lat'][1]) &
            (lon_grid >= bounds['lon'][0]) & (lon_grid <= bounds['lon'][1])
        )

        # Use unmasked points as denominator so terrain-heavy zones
        # (alps, iberia_north) aren't penalised by masked-out points.
        if terrain_mask is not None:
            valid_region = region_mask & terrain_mask
        else:
            valid_region = region_mask

        n_region_points = valid_region.sum()
        if n_region_points == 0:
            results[region_name] = {'present': False}
            continue

        frontal_in_region = frontal_mask & region_mask
        n_frontal_points = frontal_in_region.sum()
        frontal_fraction = n_frontal_points / n_region_points

        if (frontal_fraction < _MIN_FRONTAL_FRACTION
                or n_frontal_points < _MIN_FRONTAL_POINTS):
            results[region_name] = {'present': False}
            continue

        types_in_region = front_type[frontal_in_region]
        type_counts = np.bincount(types_in_region[types_in_region > 0],
                                   minlength=4)
        dominant_type = np.argmax(type_counts[1:]) + 1

        type_names = {1: 'cold', 2: 'warm', 3: 'indeterminate'}

        results[region_name] = {
            'present': True,
            'type': type_names.get(dominant_type, 'unknown'),
            'intensity': float(gradient[frontal_in_region].max()),
            'orientation': _orientation_label(front_orientation, frontal_in_region),
            'coverage_fraction': float(frontal_fraction),
        }

    return results
```

---

## Part 4 — Frontal Passage Timing and Inter-Model Comparison

### 4.1 Clearance Timing

The most pilot-relevant metric: when does each model predict the front to clear a given region?

Clearance timing is **derived at query time** from the per-horizon DB rows (or from the in-memory zone timeseries during pipeline runs), not stored in the database. This keeps the schema as a pure fact layer and allows different consumers (departure window, LLM narrative, route table) to interpret frontal passages differently — e.g., finding flyable gaps between two fronts, computing route-wide clearance, or reporting per-zone clearance.

Since Open-Meteo returns hourly data and we fetch it all in one request, clearance timing uses **hourly resolution** internally during pipeline computation. This catches fast-moving fronts that would slip between 6h snapshots (a front can transit a 3-4 degree zone in 4-6 hours). When derived from DB rows (stored at 6h intervals), clearance precision is 6h — sufficient for briefing outputs.

```python
def find_frontal_clearance_time(zone_timeseries: dict,
                                 region_name: str,
                                 max_horizon: int = 72,
                                 min_clear_hours: int = 3,
                                 ) -> int | None:
    """
    Find the earliest forecast hour at which a front clears the
    specified region for a single model.

    Reuses the pre-computed zone timeseries from build_zone_timeseries()
    rather than recomputing detection per hour — the pipeline computes
    the timeseries once and both clearance timing and front tracking
    consume it.

    min_clear_hours: require N consecutive clear hours before declaring
    clearance. A front near threshold can briefly dip below detection
    at one timestep then reappear — requiring 3 consecutive clear hours
    filters these false clearances. The clearance time reported is the
    first of the N clear hours.

    Known limitation: this finds the *first* sustained clearance but does
    not check for re-entry by a secondary front. A zone that clears at
    T+24 but gets hit by a new front at T+36 would report clearance at
    T+24. For route-level departure windows, the caller should verify
    that no zone along the route has frontal activity *after* the
    reported clearance — or extend this function to return the last
    clearance if multiple frontal passages occur.

    Returns clearance hour or None if front persists through max_horizon.
    """
    entries = zone_timeseries.get(region_name, [])
    consecutive_clear = 0
    clearance_start = None

    for entry in entries:
        if entry['hour'] > max_horizon:
            break
        if not entry['present']:
            if consecutive_clear == 0:
                clearance_start = entry['hour']
            consecutive_clear += 1
            if consecutive_clear >= min_clear_hours:
                return clearance_start
        else:
            consecutive_clear = 0
            clearance_start = None

    return None


def find_clearance_times_all_models(
        all_timeseries: dict[str, dict],
        region_name: str,
        max_horizon: int = 72,
        min_clear_hours: int = 3,
) -> dict:
    """
    For each model, find the clearance time from pre-computed timeseries.
    all_timeseries: {model_name: zone_timeseries_dict}
    Returns: {model: clearance_hour or None}
    """
    return {
        model: find_frontal_clearance_time(
            ts, region_name, max_horizon, min_clear_hours
        )
        for model, ts in all_timeseries.items()
    }


def compute_timing_spread(clearance_times: dict) -> dict:
    """
    Compute inter-model timing spread in hours.
    """
    valid_times = [t for t in clearance_times.values() if t is not None]

    if len(valid_times) < 2:
        return {'spread_hours': None, 'agreement': False, 'note': 'insufficient_data'}

    spread = max(valid_times) - min(valid_times)

    return {
        'spread_hours': int(spread),
        'min_clearance': min(valid_times),
        'max_clearance': max(valid_times),
        'by_model': clearance_times,
        'agreement': spread <= 6,
    }
```

### 4.2 Zone Timeseries

Build a per-zone, per-hour timeseries of frontal presence. This is the core data structure consumed by clearance timing, zone labels, and the LLM digest context.

```python
def build_zone_timeseries(model_forecasts: dict,
                          lat: np.ndarray,
                          lon: np.ndarray,
                          hours: range,
                          terrain_mask: np.ndarray = None) -> dict:
    """
    For one model, compute frontal presence per zone per hour.

    This is the single hourly detection loop — clearance timing
    and zone labels both consume this output rather than recomputing
    detection independently.

    Returns: {zone_name: [{hour, present, type, intensity}, ...]}
    """
    timeseries = {zone: [] for zone in ZONES}

    for h in hours:
        if h not in model_forecasts:
            continue
        fields = model_forecasts[h]
        zones = compute_frontal_zones_dual(
            fields['T850'], fields['theta_e'], lat, lon,
            terrain_mask=terrain_mask,
        )
        front_type_grid = classify_front_type(
            zones['dT_dx'], zones['dT_dy'],
            fields['u850'], fields['v850'],
            zones['frontal_mask'],
            detected_by=zones.get('detected_by'),
        )
        region_results = find_fronts_in_regions(
            zones['frontal_mask'], front_type_grid, zones['gradient'],
            zones['front_orientation'],
            lat, lon, terrain_mask=terrain_mask
        )
        for zone_name, result in region_results.items():
            timeseries[zone_name].append({
                'hour': h,
                'present': result['present'],
                'type': result.get('type'),
                'intensity': result.get('intensity'),
                'orientation': result.get('orientation'),
            })

    return timeseries
```

**No cross-zone front event tracking**: the pipeline deliberately does not try to identify the "same front" across multiple zones (e.g., "this cold front in north_france is the same one that reaches alps 12h later"). Zone labels and clearance timing are computed from each zone's local timeseries. The LLM digest receives per-zone structured data and infers spatial relationships — it's good at synthesizing "cold front in north_france at T+6, cold front in south_france at T+12" into "cold front moving south through France." See "Cross-Zone Front Event Tracking" under Future Enhancements for options if this proves insufficient.

### 4.3 Zone Label Classification

Zone labels are derived from the **local timeseries** — no cross-zone front tracking required. Each zone's own history of frontal presence is sufficient for the operationally useful labels.

```python
def classify_zone_label(zone_name: str,
                        hour: int,
                        timeseries: dict) -> str:
    """
    Assign a synoptic label to a zone at a given hour based on its
    local frontal timeseries.

    Logic:
    1. If a front is currently present → 'cold_frontal',
       'warm_frontal', or 'indeterminate_frontal' based on front type.
    2. If a front was present but has cleared within the last 12h
       → 'post_frontal'.
    3. If no front now but one arrives within the next 12h
       → 'pre_frontal' (lookahead in the same zone's timeseries).
    4. If the zone sees a warm front passage followed by a cold
       front arrival (warm→clear→cold pattern in local timeseries)
       and we're in the clear interval → 'warm_sector'.
    5. Otherwise → 'air_mass' (no front influence).
    """
    zone_data = timeseries[zone_name]
    current = next((d for d in zone_data if d['hour'] == hour), None)

    # Case 1: front currently present
    if current and current['present']:
        type_map = {'cold': 'cold_frontal', 'warm': 'warm_frontal',
                    'indeterminate': 'indeterminate_frontal'}
        return type_map.get(current['type'], 'frontal_zone')

    # Case 2: front recently cleared (post-frontal)
    recent_clearance = _find_recent_clearance(zone_data, hour, lookback_h=12)
    if recent_clearance:
        return 'post_frontal'

    # Case 3: front arriving soon (pre-frontal)
    # Look forward in the same zone's timeseries — if a front appears
    # within 12h, the zone is pre-frontal. No cross-zone tracking needed:
    # a zone where a front will arrive is pre-frontal regardless of
    # where the front currently is.
    arriving = _front_arriving_soon(zone_data, hour, lookahead_h=12)
    if arriving:
        return 'pre_frontal'

    # Case 4: warm sector — local warm→clear→cold pattern
    # If zone had a warm front passage (cleared within lookback) and a
    # cold front is arriving (within lookahead), the interval is the
    # warm sector. This detects the classic pattern without needing to
    # know the two fronts belong to the same cyclone system.
    if (_find_recent_clearance_of_type(zone_data, hour, 'warm', lookback_h=18)
            and _front_arriving_soon_of_type(zone_data, hour, 'cold', lookahead_h=18)):
        return 'warm_sector'

    # Case 5: no frontal influence
    return 'air_mass'
```

**Pre-frontal from local lookahead**: instead of checking adjacent zones for an "approaching" front (which requires cross-zone tracking), look forward in the same zone's own timeseries. If a front will be present in this zone at T+8, the zone is pre-frontal now. This is slightly less informative (can't say "approaching from the northwest") but is simpler and equally accurate for the label itself.

**Warm sector from local pattern**: the classic warm-sector signature — warm front passage followed by cold front arrival — is detectable purely from the zone's own timeseries. The 18h lookback/lookahead window is wide enough to catch typical warm-sector durations (6-18h) without requiring knowledge of which fronts are "the same system."

**What's lost**: without cross-zone tracking, the pipeline can't produce "cold front expected to reach the Alps in 12h" directly. It can say "Alps is pre-frontal (front arriving in ~12h)" and the LLM can combine this with "cold front currently in south_france" to write the full narrative. The structured context gives the LLM all the per-zone data; spatial synthesis is something LLMs handle well.

---

## Part 5 — Output Surfaces

### 5.1 Zone Annotation Labels

Labels derived from frontal presence and type, used in route tables and LLM digest context:

```python
ZONE_LABELS = {
    'pre_frontal':      'Pre-frontal — deteriorating conditions expected',
    'frontal_zone':     'Frontal zone — cloud, icing, possible precipitation',
    'warm_sector':      'Warm sector — low cloud, reduced visibility likely',
    'cold_frontal':     'Cold front passage — embedded convection risk',
    'warm_frontal':     'Warm front — extensive cloud and icing layer',
    'indeterminate':    'Complex frontal zone — mixed or ambiguous front type',
    'post_frontal':     'Post-frontal — improving, possible showers and convection',
    'air_mass':         'Air mass — conditions governed by local thermodynamics',
    'clear_sector':     'Clear sector — settled conditions expected',
}
```

These labels are **not** overlaid on the cross-section. Synoptic context belongs in the narrative text and route table, not on the precision product. See Overview for rationale.

### 5.2 Structured Context for LLM Digest

Instead of generating a template narrative directly, feed structured data to the LLM digest prompt. Example context block:

```
Frontal analysis for route EGTF-LSGS (T+48):
- cold front detected in zones: north_france (intensity 2.1, oriented NE-SW), south_france (1.8, NE-SW), alps (1.9, NE-SW)
- model agreement: GFS/ECMWF/ICON agree on front in north_france/south_france/alps
- model disagreement: uk_south (ECMWF sees front, GFS/ICON don't)
- clearance timing: GFS T+48h, ICON T+48h, ECMWF T+60h (12h spread)
- zone labels: uk_south=clear_sector, north_france=cold_frontal, south_france=cold_frontal, alps=cold_frontal
```

The LLM writes prose like: "A cold front oriented NE-SW extending from northern France through southern France to the Alps will dominate your route. Models agree on the front's presence but ECMWF keeps it active 12 hours longer than GFS and ICON..."

This complements the DWD surface chart as synoptic context input. Both are fed to the LLM; the DWD chart provides pressure centers and trough lines that T850 gradients don't capture.

### 5.3 Route Frontal Table (Deterministic)

Template-generated table showing front presence per zone per model:

```
## EGTF -> LSGS  |  Departure Saturday 12Z  |  T+48h forecast

Segment                       GFS         ECMWF       ICON        Agreement
----------------------------------------------------------------------------
Southern England & Channel    —           COLD        —           N
Northern France               COLD!       COLD!       COLD        Y
Southern France               COLD!       COLD!       COLD!       Y
Alps & Bavaria                COLD!       COLD!       COLD!       Y

Model disagreement on: Southern England & Channel.
```

### 5.4 Departure Window Summary

```
Departure window: GFS and ICON clear full route at T+48h.
ECMWF not clear until T+60h. Spread: 12h.
Recommend monitoring T+36h update before committing to Saturday departure.
```

---

## Part 6 — CLI Tool

### 6.1 Purpose

The CLI tool is the primary development and validation interface. It runs the same code as the scheduled pipeline but with interactive output: console tables, optional map plots, and the ability to compare against Meteo-France carte des fronts.

### 6.2 Usage Examples

```bash
# Run frontal analysis for all models, current time
python -m weatherbrief.frontal.cli analyze

# Run for a specific model and date
python -m weatherbrief.frontal.cli analyze --model ecmwf --date 2026-04-15

# Show results for a specific route
python -m weatherbrief.frontal.cli route --template uk_alps
python -m weatherbrief.frontal.cli route --from EGTF --to LSGS

# Show all zones with frontal activity
python -m weatherbrief.frontal.cli zones

# Generate map plot (for visual comparison with carte des fronts)
python -m weatherbrief.frontal.cli analyze --plot --output fronts_20260415.png

# Show the structured context that would be fed to LLM digest
python -m weatherbrief.frontal.cli digest-context --template uk_alps

# Dry run — show grid points, zones, and what would be fetched without hitting Open-Meteo
python -m weatherbrief.frontal.cli analyze --dry-run

# Force fresh fetch (ignore cache)
python -m weatherbrief.frontal.cli analyze --no-cache

# Clear cached grid data
python -m weatherbrief.frontal.cli clear-cache
```

### 6.3 Development Workflow

1. **Implement detection core** (`detect.py`, `zones.py`, `grid.py`, `cache.py`)
2. **Wire up CLI** (`cli.py`) — run interactively, inspect results
3. **Validate against carte des fronts** — tune gradient threshold, check front types
4. **Iterate** until detection is reliable across a range of synoptic situations (cache means re-runs are instant)
5. **Then** integrate into scheduled pipeline and briefing output

The CLI remains useful post-deployment for debugging, manual inspection, and one-off analysis. Cache is CLI-only — the scheduled pipeline always fetches fresh data.

---

## Part 7 — Storage Schema

### 7.1 Database Table

Results are written to a DB table that briefings read from:

```sql
CREATE TABLE frontal_analysis (
    id INTEGER PRIMARY KEY,
    run_date DATE NOT NULL,
    init_hour INTEGER NOT NULL,        -- 0 or 12
    model VARCHAR(10) NOT NULL,        -- gfs, ecmwf, icon
    horizon_h INTEGER NOT NULL,        -- 0, 6, 12, 18, 24, 36, 48, 60, 72
    zone_name VARCHAR(30) NOT NULL,
    front_present BOOLEAN NOT NULL,
    front_type VARCHAR(15),            -- cold, warm, indeterminate, NULL
    intensity FLOAT,                   -- peak gradient K/100km, NULL if no front
    coverage_fraction FLOAT,           -- fraction of zone with frontal activity
    orientation VARCHAR(10),           -- front orientation label e.g. 'NE-SW', NULL if no front
    computed_at TIMESTAMP NOT NULL,
    UNIQUE(run_date, init_hour, model, horizon_h, zone_name)
);
```

**No `clearance_hour` column**: clearance timing is derived at query time from the per-horizon rows rather than stored. This avoids encoding assumptions about what "clearance" means into the schema — the same rows support single clearance, multiple frontal passages with flyable gaps between them, or "fronts persist through T+72." The briefing code and LLM context builder scan the horizon rows for each zone and derive whatever clearance/window summary fits the context. Different consumers (departure window, LLM narrative, route table) can interpret the timeseries differently without schema changes.

Briefings query this table for the latest available run. The UNIQUE constraint allows upserts when re-running analysis. To find the latest run:

```sql
SELECT DISTINCT run_date, init_hour
FROM frontal_analysis
ORDER BY run_date DESC, init_hour DESC
LIMIT 1;
```

Then filter by route zones to get relevant results.

### 7.2 Uncertainty Flag

Combined uncertainty metric stored alongside briefing data:

```python
def compute_uncertainty_flag(max_frontal_spread_h: float,
                              regime_confidence: float = None) -> str:
    """
    Frontal-only uncertainty, optionally combined with regime confidence.
    """
    if regime_confidence is None:
        if max_frontal_spread_h <= 6:
            return 'low'
        elif max_frontal_spread_h > 18:
            return 'high'
        else:
            return 'medium'

    if regime_confidence > 0.6 and max_frontal_spread_h <= 6:
        return 'low'
    elif regime_confidence < 0.3 or max_frontal_spread_h > 18:
        return 'high'
    else:
        return 'medium'
```

---

## Part 8 — Implementation Sequence

### Phase 1 — Detection core + CLI (focus here first)

- Implement `grid.py`: grid definition, lightweight Open-Meteo fetch for 0.5deg Europe grid (T850 + Td850 + wind), reshape to 2D arrays, θe computation, terrain mask via SRTM3
- Implement `cache.py`: raw grid data cache keyed by (model, init_time) for iterative development
- Implement `detect.py`: `compute_frontal_zones()` with terrain mask and front orientation, `compute_frontal_zones_dual()` with T850+θe, `classify_front_type()`
- Implement `zones.py`: zone definitions, `find_fronts_in_regions()` with fractional coverage threshold and orientation labels
- Implement `cli.py`: `analyze` command with console output, TFP map plot for visual validation, cache enabled by default
- **Unit tests with synthetic fields**: construct known T850 fields (e.g., hyperbolic tangent step function) with controlled gradient, front position, and advection sign. Assert that detection recovers the known front position, classification returns the correct type, and terrain masking excludes the right cells. These are cheap regression tests that catch threshold-tuning breakage.
- **Validate interactively** against Meteo-France carte des fronts
- Tune T850 gradient threshold (starting 0.8 K/100km) and θe gradient threshold (starting 4.0 K/100km), verify front type classification, verify terrain fill-then-mask removes Alpine/Pyrenean false positives, check that θe improves warm front detection vs T-only

### Phase 2 — Route analysis, clearance timing, and zone labels

- Add `find_frontal_clearance_time()` with hourly resolution, `compute_timing_spread()`
- Implement `tracking.py`: `build_zone_timeseries()`, `classify_zone_label()` (local timeseries only, no cross-zone tracking)
- Add route template support to CLI (`route` command)
- Add dynamic route assembly (`find_route_zones()`) reusing existing flight waypoints
- Build route frontal table output
- Add `--plot` option to CLI for map visualization
- Add `--dry-run` to CLI for testing grid/zone logic without fetching

### Phase 3 — LLM digest integration

- Build structured context generator for LLM digest input
- Wire frontal context into digest prompt alongside existing inputs (complementing DWD charts, not replacing)
- Test digest quality with frontal context + DWD charts combined
- Optionally make DWD configurable if frontal analysis proves self-sufficient

### Phase 4 — Scheduled pipeline + briefing integration

- Implement `pipeline.py`: orchestrate fetch -> detect -> store
- Add `frontal_analysis` DB table (alembic migration)
- Schedule to run after 00Z and 12Z model deliveries
- Wire briefing to read from `frontal_analysis` table
- Add route frontal table and departure window to briefing output

---

## Key Dependencies

| Library | Purpose |
|---|---|
| `numpy` | Array operations, gradient computation |
| `scipy.ndimage` | Gaussian smoothing for frontal detection |
| `scipy.interpolate` | Terrain fill and missing-data interpolation (griddata) |
| `metpy` | Equivalent potential temperature (θe) computation |
| `matplotlib` + `cartopy` | Map plotting for CLI validation (optional, `[frontal-dev]` extra) |

`cartopy` is the only new dependency — added as an optional extra (`pip install -e ".[frontal-dev]"`) since it requires GEOS/PROJ C libraries and is only needed for CLI map plotting during development/validation, not in production. MetPy is already used for thermodynamic computations in `weatherbrief.analysis.sounding.thermodynamics`. Dynamic route assembly reuses existing flight waypoints instead of adding `pyproj`. Open-Meteo fetch reuses existing `OpenMeteoClient` with a new lightweight grid-fetch method. Terrain masking reuses the existing SRTM3 framework (`srtm` package, already installed).

---

## Important Caveats

**Frontal detection limitations**: the dual T850/θe gradient method reliably detects strong cold fronts (via T850) and warm fronts (via θe moisture gradients), but will miss weak frontal boundaries and shallow cold pools. True occluded fronts require multi-level structure and cyclone-relative geometry that single-level analysis cannot resolve — the `indeterminate` label is used honestly for ambiguous cases rather than claiming occlusion detection. Stationary fronts (high gradient, near-zero advection) also fall into the indeterminate category; the LLM can infer stationarity from the timeseries. For shallow convective situations common over the Mediterranean in summer, frontal detection is less meaningful than upper-level diagnostics. Flag frontal intensity below threshold as "weak frontal activity" rather than asserting no front is present.

**Gradient threshold tuning**: the T850 threshold (0.8 K/100km) and θe threshold (4.0 K/100km) are starting points. Too low = noise, too high = missed fronts. The θe threshold is set higher relative to its dynamic range because moisture boundaries (sea-land, air mass contacts) commonly produce θe gradients of 2-3 K/100km without any front — below 3.0 K/100km, false positives from non-frontal moisture variability become significant. Calibrate both against Meteo-France carte des fronts during Phase 1, paying particular attention to warm fronts where the θe threshold is the primary control.

**Resolution and smoothing**: at 0.5deg resolution (~55km), the grid resolves synoptic-scale fronts with 4-6 cells across a typical gradient zone. The Gaussian smoothing sigma is set to 0.5 grid cells (= 0.25deg ≈ 28km) — just enough to suppress single-cell noise without blurring narrow fronts. If validation shows too much noise at sigma=0.5, increase cautiously — but at 0.5deg, over-smoothing is the bigger risk.

**Resolution vs. accuracy**: at 0.5deg resolution, gradient thresholding gives front positions accurate to ~75-100km. The zone boxes are 3-5deg wide. This is appropriate for "cold front over northern France" but the code should not imply more precision than the method delivers. At T+48-72, model position errors (~300-500km) dominate over method accuracy — inter-model spread is the only reliable confidence signal at those horizons.

**Zone refinement**: the 18-zone set is sized for reliable detection (all zones >= 96 grid points at 0.5deg). If validation reveals that certain zones are too coarse (e.g. a front in eastern France but not western), zones can be split freely — even a half-zone has ~50+ grid points at 0.5deg. Let the data drive this decision.

**Orographic gradients**: the Alps, Pyrenees, and Scandinavian mountains create persistent T850 gradients from below-terrain extrapolation. This is handled by the fill-then-mask approach (§2.4): terrain cells above 1500m are filled with smoothly interpolated values before computation so they don't create artificial gradients at neighboring valid cells, then excluded from results at the end. The mask is static and cheap to compute. If validation shows the 1500m threshold is too aggressive (masking too many points in mountain zones), it can be raised — but the default errs on the side of fewer false positives.

**TFP usage**: the Thermal Front Parameter is computed but not used for zone-level frontal detection (which relies on gradient-magnitude thresholding — see §2.1). TFP is retained for CLI map plotting only, where TFP zero-crossings show front *lines* rather than broad gradient zones, giving sharper visual output for validation against Meteo-France carte des fronts. TFP is not fed to the LLM digest or route tables.

**Terrain-adjacent classification**: the fill-then-mask approach gives correct *gradients* at terrain-adjacent cells, but front *type classification* at those cells uses temperature advection computed from filled (fictional) temperature values combined with real winds. This could misclassify front type at cells immediately bordering masked terrain (e.g. Alpine valley cells). In practice this affects very few cells and only matters when a real front runs along a terrain boundary — a scenario where T850 is unreliable regardless. Monitor during validation; if systematic, consider extending the terrain mask by one cell for classification purposes.

**Zone adjacency through terrain gaps**: if cross-zone front event tracking is added later (see "Cross-Zone Front Event Tracking" under Future Enhancements), zone adjacency must account for terrain. Some zone pairs share a boundary through partial terrain (e.g. `alps` and `central_germany` through the Bavarian foreland) — the adjacency definition should require a minimum fraction of unmasked boundary points to prevent front events from connecting zones physically separated by terrain. See the front event tracking discussion under Future Enhancements for full analysis.

**Diurnal and seasonal false positives**: land-sea temperature contrasts at 850hPa can produce persistent gradients in coastal zones (`balearics`, `western_med`, `adriatic`) especially in summer. At 0.5° resolution these are largely smoothed out, but diurnal heating cycles and summer sea-land contrasts may approach the 0.8 K/100km threshold. The `_MIN_FRONTAL_FRACTION` zone threshold filters isolated gradient cells, but monitor coastal zones during summer validation. If false positives appear, consider a seasonal threshold adjustment or a static coastal gradient baseline subtraction.

**Mediterranean summer**: shallow convective situations are not well captured by T850 gradients. Frontal detection is most valuable in the Atlantic/continental regime (autumn through spring) when synoptic-scale fronts dominate European weather.

**Open-Meteo usage**: ~15 requests per analysis run, ~900 calls/month at 2 runs/day — negligible against the 1M monthly API plan. Room to densify further or add models if needed.

---

## Future Enhancements to Consider

Complementary improvements to evaluate after Phase 1 validation.

### Cross-Zone Front Event Tracking

The current design (§4.2–4.3) uses zone-local timeseries only — each zone's labels are derived from its own history, and the LLM synthesizes spatial relationships from per-zone structured data. If validation shows the LLM struggles to produce coherent spatial narratives (e.g., consistently failing to connect "cold front in north_france at T+6" with "cold front in south_france at T+12"), cross-zone front event tracking would be the upgrade.

**What it adds**: explicit "front event" objects grouping the same front across zones and time. Enables direct statements like "cold front moving SE at ~40 km/h, currently over northern France, expected to reach the Alps by T+18." Also enables propagation speed estimates and more confident warm-sector detection (by linking warm and cold fronts from the same cyclone).

**What it costs**: a zone adjacency graph (terrain-aware), a cross-zone grouping algorithm, and handling of fronts that change type as they propagate.

Two candidate approaches were evaluated:

**Option A — Connected-component on (zone, hour) graph**: build a graph where nodes are (zone, hour) pairs with frontal activity. Edges connect same-zone consecutive hours and adjacent-zone overlapping times (within ±N hours), constrained to matching front type. Run connected-components to find events.
- *Pros*: simple (~50 lines), standard algorithm, naturally handles stalling fronts.
- *Cons*: "same type" constraint is fragile — a front that's cold in one zone and indeterminate in the next (common across mountains) splits into two events. The ±6h cross-zone window is arbitrary and resolution-dependent. No built-in propagation direction — two fronts arriving from opposite sides could merge. Direction and speed must be inferred post-hoc.

**Option B — Forward-chaining / greedy propagation**: start from the earliest frontal activity, follow forward in time using gradient direction to determine "downstream" zones. A front event grows until no continuation is found.
- *Pros*: propagation direction is built-in from the start. Naturally handles type evolution (cold→indeterminate stays one event). More physical — tracks a front as it moves.
- *Cons*: greedy — early decisions can't be revised if two fronts merge. More complex (~100 lines). Needs careful handling of stalling fronts and simultaneous active events to avoid cross-contamination.

**Zone adjacency** (required by both options): derived statically from zone bounds and terrain mask — two zones are adjacent if their bounding boxes share an edge or overlap AND at least 20% of the shared-boundary grid points are unmasked (terrain below 1500m). This prevents connecting zones physically separated by terrain (e.g. `alps` and `po_valley` through heavily masked Alpine terrain). Some zone pairs sharing a boundary through partial terrain (e.g. `alps` and `central_germany` through the Bavarian foreland) may still connect — this is physically correct (fronts propagate through the foreland) but could create artifacts where a front appears to "jump" the Alps. Validate against real frontal passages.

**Recommendation if implementing**: start with Option A (simpler, fewer failure modes) with a relaxed type constraint (cold↔indeterminate compatible, cold↔warm not). If the ±6h window proves too rigid, switch to Option B. Either way, this is a Phase 2+ enhancement — the zone-local approach should be validated first.

**Gridded front speed tracking for better clearance predictions**: rather than inferring front propagation from coarse zone-to-zone timing (~200km resolution), track the gradient maximum position between consecutive hourly frames at grid resolution and compute actual front speed in km/h. This would improve clearance time estimates (extrapolate from measured speed rather than waiting for zone-level absence) and produce more physical narratives ("cold front moving SE at 40 km/h, expected to clear the Alps by 18Z"). The detection infrastructure (hourly gridded fields) already supports this — it's a post-processing step on the existing gradient fields.

**Vorticity-based detection as a third criterion**: fronts are associated with vorticity maxima at 850hPa. Relative vorticity can be computed from the u/v fields already fetched (no additional data cost). Adding a vorticity check could reduce false positives from non-frontal baroclinic zones — persistent temperature gradients that aren't fronts typically lack the associated vorticity signature. Could be used as a filter on detected frontal zones rather than a standalone detector: require gradient threshold AND vorticity above a minimum. Low priority but the data is already available.

**Temporal gradient change (frontogenesis/frontolysis)**: instead of just spatial gradient magnitude at each timestep, the *rate of change* of gradient between consecutive hours indicates whether a front is strengthening (frontogenesis) or weakening (frontolysis). Hourly fields make this trivial to compute. Benefits: (1) better clearance predictions — a front undergoing rapid frontolysis will clear sooner than the static gradient suggests; (2) pre-frontal warnings — frontogenesis ahead of a zone signals worsening conditions before the gradient exceeds the detection threshold; (3) more physical narratives ("weakening cold front").

**Adaptive thresholds by season**: the European background T850 gradient is stronger in winter (~0.5 K/100km ambient) than summer (~0.2 K/100km). A fixed 0.8 K/100km threshold is more selective in summer (may miss weak summer fronts) and less selective in winter (may detect non-frontal baroclinic zones). Options: (a) seasonal threshold lookup table, (b) anomaly-based detection (subtract a climatological gradient field and threshold on the anomaly), (c) percentile-based threshold (detect the top N% of gradient values per analysis). Seasonal lookup is simplest; anomaly-based is most principled. Evaluate after accumulating validation data across seasons.

**Front depth estimation from multi-level data**: using only 850hPa means shallow fronts (surface to 800hPa) and deep fronts (surface to 500hPa) are indistinguishable. For GA pilots, this matters — a shallow front may allow VFR on top at FL100, while a deep front means IFR all the way up. Adding 700hPa and 500hPa temperature fields (available from all three models via Open-Meteo) would allow estimating front depth by checking whether the gradient signal extends upward. This is a significant scope increase (3x the fields per level) but the fetch infrastructure supports it. The cross-section already shows vertical extent at route-point resolution, so this is lower priority — but would improve the zone-level narrative.

**925 hPa / surface level for shallow front detection**: the current single-level (850hPa) approach will miss shallow cold fronts (common over the UK) and may misplace warm fronts. Adding 925hPa or surface temperature would enable simple multi-level rules: front at 850 but not 925 → elevated front; front at 925 but weak at 850 → shallow/surface front. This requires checking whether Open-Meteo exposes 925hPa for all three models (GFS has it in 27 levels; ECMWF and ICON need verification). Lower complexity than the full multi-level depth estimation above — just one additional level with a simple agreement rule. Evaluate after Phase 1 if shallow front misses are a validated problem.

**MSLP field for synoptic context**: pressure troughs and cyclone centers are not detectable from temperature gradients alone. Adding mean sea-level pressure gradient or Laplacian could help distinguish real fronts from non-frontal thermal boundaries (air mass contacts, sea-land contrasts). However, the DWD Bodenwetterkarten already provide pressure/trough context to the LLM digest, so this would be partially redundant. Consider if DWD charts are dropped or if standalone frontal analysis needs to be self-sufficient.

**Gradient-weighted zone detection score**: instead of fraction-based detection (count of cells above threshold / total cells), use a gradient-intensity-weighted score: `sum(gradient within zone) > threshold`. This prevents missing narrow but strong fronts that have few cells above threshold but high peak gradients. The current approach (fraction + absolute floor) is simpler — evaluate this alternative if validation shows missed narrow intense fronts.

**Aviation impact labels in structured context**: connect front types directly to aviation impact in the LLM digest context: cold front → convective risk / turbulence, warm front → stratiform cloud / icing, post-frontal → showery / gusty. Currently the LLM derives these associations from general knowledge, but explicit impact labels in the structured context would make the narrative more consistently pilot-focused. Could be added to the zone result dict without changing detection logic.

**Run-level metadata table**: when moving to Phase 4 DB storage, add a `frontal_runs` parent table recording the gradient threshold, smoothing sigma, grid resolution, and code version used for each run. This enables comparing runs during threshold calibration to distinguish code changes from real weather evolution. Not needed for Phase 1 CLI development.

**Front classification via gradient alignment**: instead of pure temperature advection magnitude for cold/warm classification, use the angle between wind vector and temperature gradient. Cold front: wind crosses gradient toward cold side; warm front: wind crosses toward warm side. This is more stable than advection magnitude (which depends on wind strength) and may reduce the indeterminate-ring problem around front axes. Evaluate alongside the offset-advection approach adopted in the plan — the two techniques could complement each other.

**Percentile-based adaptive thresholds**: instead of fixed gradient thresholds, detect the top N% of gradient values per analysis (e.g., 85th or 90th percentile), with a minimum floor. This automatically adapts to seasonal variation (stronger winter background gradients vs weaker summer) and model-to-model differences. More principled than a seasonal lookup table. Risk: in a no-front situation, the top 15% of gradients would still be flagged — needs a minimum absolute gradient floor alongside the percentile. Evaluate after accumulating Phase 1 validation data across seasons.
