# Frontal Detection — Implementation Plan for FlyFun Weather

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
- **Variables per grid point**: `temperature_850hPa`, `wind_speed_850hPa`, `wind_direction_850hPa` (reconstructed to u/v). Just 3 variables at 1 level — tiny compared to full sounding fetches.
- **Fetch volume**: Open-Meteo returns hourly time series per request, so all forecast horizons come back in one response. At 1000 points per chunk, that's ~5 requests per model × 3 models = **~15 requests total**. At 2 runs/day, ~900 API calls/month — negligible against the 1M monthly plan.
- **All three models provide 850hPa via Open-Meteo**: GFS (in extended levels), ECMWF (in 13-level set), ICON-EU (in 19-level set).
- **Chunk size**: the existing `OpenMeteoClient.fetch_multi_point()` accepts a `chunk_size` parameter (default 150 for full soundings). The frontal grid fetch uses 1000 — with only 3 variables the `hourly` param is ~67 chars, leaving the URL budget almost entirely for coordinates (~12KB per request, well within limits).

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

1. **Detection algorithm**: computing the Thermal Front Parameter (TFP) from 850hPa temperature, classifying cold/warm/occluded using wind and temperature advection. Pure meteorological signal processing.
2. **Route corridor framework**: a ~33-zone geographic library covering European GA chokepoints, route templates, dynamic zone assembly. Indexes frontal results by geographic region and produces pilot-facing outputs (route table, digest context, departure window).

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
    detect.py     — TFP computation, front classification, gradient thresholds
    zones.py      — zone definitions, route templates, zone intersection logic
    tracking.py   — front event tracking across zones/time, zone label classification
    pipeline.py   — orchestrates fetch -> detect -> track -> store, used by both CLI and scheduler
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
- `wind_speed_850hPa` (returned in **knots** — the client sets `wind_speed_unit=kn`), `wind_direction_850hPa` (degrees)

**Confirmed availability**: all three models expose 850hPa temperature, wind speed, and wind direction via Open-Meteo. GFS has 27 pressure levels, ECMWF has 13, ICON-EU has 19 — all include 850hPa. The only per-model gap relevant here is vertical_velocity (unavailable for ICON), which we don't need.

**Lightweight fetch path**: the existing `fetch_multi_point()` returns full `WaypointForecast` objects with all surface variables and all pressure levels — far more data than we need. Rather than forcing ~4000 grid points through the full sounding pipeline, add a dedicated `fetch_grid_fields()` method to `OpenMeteoClient` that:
- Requests only `temperature_850hPa`, `wind_speed_850hPa`, `wind_direction_850hPa` (3 variables)
- Skips all surface variables
- Returns raw numpy arrays directly (no `WaypointForecast` construction)
- Uses `chunk_size=1000` — with only 3 variables the `hourly` param is ~67 chars, leaving the URL budget almost entirely for coordinates. 1000 points × ~12 chars each ≈ 12KB URL, well within limits.

This keeps the frontal fetch fast and avoids building ~4000 heavyweight model objects.

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
    ws = np.array([pt[hour_index] for pt in raw['wind_speed_850hPa']]).reshape(n_lat, n_lon)
    wd = np.array([pt[hour_index] for pt in raw['wind_direction_850hPa']]).reshape(n_lat, n_lon)
    u850, v850 = wind_to_uv(ws, wd)
    return {'T850': T850, 'u850': u850, 'v850': v850}
```

**Fetch volume**: Open-Meteo returns the full hourly time series in each response, so all forecast horizons come back at once. At 1000 points per chunk: ~5 requests per model, ~15 requests total for all three models. At 2 runs/day, that's ~900 API calls/month — negligible against the 1M monthly plan. Under 30 seconds with the lightweight path.

### 1.3 Forecast Horizons

**Analysis horizons**: hourly from T+0 through T+72. Since Open-Meteo returns the full hourly time series in a single response, there's zero additional fetch cost to analyze every hour. Hourly resolution is important for clearance timing — a fast-moving front can transit a small zone in 3-4 hours, which 6h snapshots would miss entirely.

For **output and storage**, results are aggregated to 6h blocks (T+0, 6, 12, 18, 24, 36, 48, 60, 72) — but clearance timing uses hourly precision internally to find the exact hour each zone clears.

**Horizon limits**:
- **T+0 to T+48**: models reliably place major fronts, position errors ~100km. Full confidence in clearance timing.
- **T+48 to T+72**: fronts still identifiable, position errors ~200-300km (comparable to method accuracy). Clearance timing usable but with wider uncertainty.
- **Beyond T+72**: individual fronts become unreliable. Not analyzed for clearance timing.

T+72 is the cutoff for route tables and departure window calculations.

### 1.4 ERA5 Fields for Validation

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

Based on the **thermal front parameter (TFP)** method of Hewson (1998), simplified for operational use. The core idea: a front is a line of maximum horizontal temperature gradient at 850hPa.

```python
# detect.py

import numpy as np
from scipy.ndimage import gaussian_filter

def compute_frontal_zones(T850: np.ndarray,
                           lat: np.ndarray,
                           lon: np.ndarray,
                           smooth_sigma: float = 0.5,
                           gradient_threshold: float = 0.8) -> dict:
    """
    Detect frontal zones from 850hPa temperature field.

    T850: 2D array (lat x lon) in Celsius
    lat, lon: 1D coordinate arrays (degrees)
    smooth_sigma: Gaussian smoothing sigma in grid points. At 0.5deg resolution
        the grid is already fairly coarse, so minimal smoothing suffices.
        0.5 (= 0.25deg) removes single-cell noise without blurring narrow fronts.
    gradient_threshold: K per 100km — frontal zone threshold

    Returns dict with:
        'gradient': 2D gradient magnitude field (K per 100km)
        'frontal_mask': boolean mask of frontal zones
        'tfp': thermal front parameter field
    """
    # Smooth temperature to remove single-cell noise
    T_smooth = gaussian_filter(T850, sigma=smooth_sigma)

    # Compute grid spacing in km — dlat is constant, dlon varies with latitude
    dlat_km = 111.0  # km per degree latitude (constant)
    dlon_km = 111.0 * np.cos(np.radians(lat))  # 1D array, one value per latitude row
    # At 35N: ~91 km/deg, at 60N: ~55 km/deg — ~40% variation across domain

    # Temperature gradient components (K per km)
    # Use latitude-varying spacing for the longitude (x) axis
    dlat_spacing = dlat_km * np.abs(np.diff(lat).mean())  # scalar
    dlon_spacing = dlon_km * np.abs(np.diff(lon).mean())  # 1D array (n_lat,)

    dT_dy, dT_dx = np.gradient(T_smooth, dlat_spacing, axis=0), \
                    np.gradient(T_smooth, axis=1) / dlon_spacing[:, np.newaxis]

    # Gradient magnitude (K per km)
    grad_mag = np.sqrt(dT_dx**2 + dT_dy**2)

    # Convert to K per 100km for threshold comparison
    grad_mag_100km = grad_mag * 100.0

    # Frontal zone mask
    frontal_mask = grad_mag_100km > gradient_threshold

    # Thermal Front Parameter (TFP) — Hewson 1998 simplified
    # TFP = -nabla|nablaT| . (nablaT / |nablaT|)
    # Zero crossings of TFP within frontal zones locate the front line
    grad_norm = np.where(grad_mag > 1e-10, grad_mag, 1e-10)
    unit_grad_x = dT_dx / grad_norm
    unit_grad_y = dT_dy / grad_norm

    d_gradmag_dy, d_gradmag_dx = np.gradient(grad_mag)
    tfp = -(d_gradmag_dx * unit_grad_x + d_gradmag_dy * unit_grad_y)

    return {
        'gradient': grad_mag_100km,
        'frontal_mask': frontal_mask,
        'tfp': tfp,
        'T_smooth': T_smooth,
    }
```

### 2.2 Front Classification: Cold vs Warm

Classify each detected frontal zone as cold, warm, or occluded using temperature advection in physical units.

```python
def classify_front_type(T850: np.ndarray,
                         u850: np.ndarray,
                         v850: np.ndarray,
                         frontal_mask: np.ndarray,
                         lat: np.ndarray,
                         lon: np.ndarray,
                         advection_threshold: float = 0.5) -> np.ndarray:
    """
    For each frontal zone grid point, classify as:
        1 = cold front (cold air advancing)
        2 = warm front (warm air advancing)
        3 = occluded (indeterminate)
        0 = not a front

    Uses temperature advection: -V . nablaT
    Negative advection = cold front, positive = warm front.

    advection_threshold: K/hr — minimum advection magnitude to classify.
        At typical frontal wind speeds (30-50 km/h) and gradients
        (1-3 K/100km), advection is ~0.3-1.5 K/hr. 0.5 K/hr filters
        out weak/ambiguous cases.

    u850, v850: wind components in km/h (already converted from knots
    by wind_to_uv()).
    """
    # Compute temperature gradient in physical units (K/km)
    dlat_km = 111.0
    dlon_km = 111.0 * np.cos(np.radians(lat))  # 1D array varying with latitude

    dlat_spacing = dlat_km * np.abs(np.diff(lat).mean())
    dlon_spacing = dlon_km * np.abs(np.diff(lon).mean())  # 1D array

    dT_dy = np.gradient(T850, dlat_spacing, axis=0)
    dT_dx = np.gradient(T850, axis=1) / dlon_spacing[:, np.newaxis]

    # Temperature advection: -(u * dT/dx + v * dT/dy)
    # u850/v850 in km/h, dT/dx in K/km => advection in K/hr
    T_adv = -(u850 * dT_dx + v850 * dT_dy)

    front_type = np.zeros_like(T850, dtype=int)

    cold_mask = frontal_mask & (T_adv < -advection_threshold)
    warm_mask = frontal_mask & (T_adv > advection_threshold)
    occluded_mask = frontal_mask & ~cold_mask & ~warm_mask

    front_type[cold_mask] = 1
    front_type[warm_mask] = 2
    front_type[occluded_mask] = 3

    return front_type
```

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
# as "front present". A real front is a *line*, not an area — a front
# crossing a 4x8 deg zone diagonally might only touch ~25% of grid points.
# 0.15 catches real fronts while filtering isolated gradient noise.
# Tune upward if false positives appear during validation.
_MIN_FRONTAL_FRACTION = 0.15


def find_fronts_in_regions(frontal_mask: np.ndarray,
                            front_type: np.ndarray,
                            gradient: np.ndarray,
                            lat: np.ndarray,
                            lon: np.ndarray,
                            regions: dict = None) -> dict:
    """
    For each region, return whether a front is present, its type,
    and its intensity (peak gradient in region).
    regions defaults to ZONES if not provided.
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

        n_region_points = region_mask.sum()
        if n_region_points == 0:
            results[region_name] = {'present': False}
            continue

        frontal_in_region = frontal_mask & region_mask
        n_frontal_points = frontal_in_region.sum()
        frontal_fraction = n_frontal_points / n_region_points

        if frontal_fraction < _MIN_FRONTAL_FRACTION:
            results[region_name] = {'present': False}
            continue

        types_in_region = front_type[frontal_in_region]
        type_counts = np.bincount(types_in_region[types_in_region > 0],
                                   minlength=4)
        dominant_type = np.argmax(type_counts[1:]) + 1

        type_names = {1: 'cold', 2: 'warm', 3: 'occluded'}

        results[region_name] = {
            'present': True,
            'type': type_names.get(dominant_type, 'unknown'),
            'intensity': float(gradient[frontal_in_region].max()),
            'coverage_fraction': float(frontal_fraction),
        }

    return results
```

---

## Part 4 — Frontal Passage Timing and Inter-Model Comparison

### 4.1 Clearance Timing

The most pilot-relevant metric: when does each model predict the front to clear a given region?

Since Open-Meteo returns hourly data and we fetch it all in one request, clearance timing uses **hourly resolution** internally. This catches fast-moving fronts that would slip between 6h snapshots (a front can transit a 3-4 degree zone in 4-6 hours).

```python
def find_frontal_clearance_time(model_forecasts: dict,
                                 region_name: str,
                                 lat: np.ndarray,
                                 lon: np.ndarray,
                                 max_horizon: int = 72,
                                 ) -> dict:
    """
    For each model, find the earliest forecast hour at which
    the frontal zone clears the specified region.

    Uses hourly resolution for precise clearance timing.
    model_forecasts: {model: {hour: {'T850': ..., 'u850': ..., 'v850': ...}}}
    Returns dict: {model: clearance_hour or None if front persists through max_horizon}
    """
    clearance_times = {}

    for model, forecasts in model_forecasts.items():
        clearance_h = None
        for h in range(0, max_horizon + 1):
            if h not in forecasts:
                continue
            T850 = forecasts[h]['T850']
            u850 = forecasts[h]['u850']
            v850 = forecasts[h]['v850']

            zones = compute_frontal_zones(T850, lat, lon)
            front_type_grid = classify_front_type(
                T850, u850, v850, zones['frontal_mask'], lat, lon
            )
            region_fronts = find_fronts_in_regions(
                zones['frontal_mask'], front_type_grid, zones['gradient'],
                lat, lon
            )

            if not region_fronts[region_name]['present']:
                clearance_h = h
                break

        clearance_times[model] = clearance_h

    return clearance_times


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

### 4.2 Front Tracking Across Zones and Time

To generate useful narratives ("cold front over northern France moving southeast toward the Alps"), the pipeline needs to identify the **same front** across zones and time steps. This doesn't require sophisticated object tracking — a simple temporal continuity approach works at zone scale.

**Approach**: build a per-zone time series of frontal presence, then group adjacent zones that share a front at overlapping times into **front events**.

```python
def build_zone_timeseries(model_forecasts: dict,
                          lat: np.ndarray,
                          lon: np.ndarray,
                          hours: range) -> dict:
    """
    For one model, compute frontal presence per zone per hour.

    Returns: {zone_name: [{hour, present, type, intensity}, ...]}
    """
    timeseries = {zone: [] for zone in ZONES}

    for h in hours:
        if h not in model_forecasts:
            continue
        fields = model_forecasts[h]
        zones = compute_frontal_zones(fields['T850'], lat, lon)
        front_type_grid = classify_front_type(
            fields['T850'], fields['u850'], fields['v850'],
            zones['frontal_mask'], lat, lon
        )
        region_results = find_fronts_in_regions(
            zones['frontal_mask'], front_type_grid, zones['gradient'],
            lat, lon
        )
        for zone_name, result in region_results.items():
            timeseries[zone_name].append({
                'hour': h,
                'present': result['present'],
                'type': result.get('type'),
                'intensity': result.get('intensity'),
            })

    return timeseries


def identify_front_events(timeseries: dict) -> list[dict]:
    """
    Group connected frontal activity across zones and time into events.

    A front event is a set of (zone, hour_range) pairs where:
    - The front type is consistent (cold/warm/occluded)
    - Zones are geographically adjacent (share a lat/lon boundary)
    - The front appears in later zones at later times (propagation)

    Returns list of events, each with:
    - type: cold/warm/occluded
    - zones: ordered list of zones the front passes through
    - timing: {zone: (first_hour, last_hour)} per zone
    - direction: estimated propagation direction (e.g. 'NW to SE')
    """
    # Implementation: connected-component analysis on the
    # (zone, hour) graph where edges connect:
    # 1. Same zone, consecutive hours with same front type
    # 2. Adjacent zones where front appears within ±6h, same type
    #
    # Zone adjacency is precomputed from ZONES bounds (zones that
    # share or overlap a lat/lon boundary).
    ...
```

**Zone adjacency** is derived statically from the zone bounds — two zones are adjacent if their bounding boxes share an edge or overlap. This is a one-time computation (~18x18 = 324 pairs to check).

**Propagation direction** is estimated from the order in which zones see the front: if `north_france` sees it at T+6 and `south_france` at T+12, the front is moving southward. This is coarse (~200km zone resolution) but sufficient for narrative purposes.

### 4.3 Zone Label Classification

The zone labels (pre-frontal, warm sector, post-frontal, etc.) require knowing where a zone sits relative to a tracked front event. This builds on the front tracking from 4.2.

```python
def classify_zone_label(zone_name: str,
                        hour: int,
                        front_events: list[dict],
                        timeseries: dict) -> str:
    """
    Assign a synoptic label to a zone at a given hour based on its
    relationship to tracked front events.

    Logic:
    1. If a front is currently present in the zone → 'cold_frontal',
       'warm_frontal', or 'occluded' based on front type.
    2. If a front was present but has cleared → 'post_frontal'
       (within 12h of clearance).
    3. If a front is approaching (present in an adjacent upstream
       zone, or will arrive within 12h) → 'pre_frontal'.
    4. If zone is between a warm front (ahead) and cold front
       (behind) in the same event → 'warm_sector'.
    5. Otherwise → 'air_mass' (no front influence).
    """
    zone_data = timeseries[zone_name]
    current = next((d for d in zone_data if d['hour'] == hour), None)

    # Case 1: front currently present
    if current and current['present']:
        type_map = {'cold': 'cold_frontal', 'warm': 'warm_frontal',
                    'occluded': 'occluded'}
        return type_map.get(current['type'], 'frontal_zone')

    # Case 2: front recently cleared (post-frontal)
    recent_clearance = _find_recent_clearance(zone_data, hour, lookback_h=12)
    if recent_clearance:
        return 'post_frontal'

    # Case 3: front approaching (pre-frontal)
    approaching = _front_approaching(zone_name, hour, front_events,
                                      lookahead_h=12)
    if approaching:
        return 'pre_frontal'

    # Case 4: warm sector (between warm front ahead and cold front behind)
    # Requires two front events of different types affecting the zone
    # sequence within the forecast window
    if _in_warm_sector(zone_name, hour, front_events):
        return 'warm_sector'

    # Case 5: no frontal influence
    return 'air_mass'
```

**How "approaching" works**: for each front event, check if the front is currently in an adjacent zone upstream (relative to propagation direction) and is expected to reach this zone within `lookahead_h`. This uses the propagation timing from `identify_front_events()`.

**How "warm sector" works**: if the zone timeseries shows a warm front passage followed by an approaching cold front (from the same cyclone system), the interval between them is the warm sector. This is the classic warm-sector pattern and is the most operationally relevant label for GA pilots (low cloud, poor visibility, stable air).

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
    'occluded':         'Occluded front — complex cloud structure',
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
- cold front detected in zones: north_france (intensity 2.1), south_france (1.8), alps (1.9)
- model agreement: GFS/ECMWF/ICON agree on front in north_france/south_france/alps
- model disagreement: uk_south (ECMWF sees front, GFS/ICON don't)
- clearance timing: GFS T+48h, ICON T+48h, ECMWF T+60h (12h spread)
- zone labels: uk_south=clear_sector, north_france=cold_frontal, south_france=cold_frontal, alps=cold_frontal
```

The LLM writes prose like: "A cold front extending from northern France through southern France to the Alps will dominate your route. Models agree on the front's presence but ECMWF keeps it active 12 hours longer than GFS and ICON..."

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
```

### 6.3 Development Workflow

1. **Implement detection core** (`detect.py`, `zones.py`, `grid.py`)
2. **Wire up CLI** (`cli.py`) — run interactively, inspect results
3. **Validate against carte des fronts** — tune gradient threshold, check front types
4. **Iterate** until detection is reliable across a range of synoptic situations
5. **Then** integrate into scheduled pipeline and briefing output

The CLI remains useful post-deployment for debugging, manual inspection, and one-off analysis.

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
    front_type VARCHAR(10),            -- cold, warm, occluded, NULL
    intensity FLOAT,                   -- peak gradient K/100km, NULL if no front
    coverage_fraction FLOAT,           -- fraction of zone with frontal activity
    computed_at TIMESTAMP NOT NULL,
    UNIQUE(run_date, init_hour, model, horizon_h, zone_name)
);
```

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

- Implement `grid.py`: grid definition, lightweight Open-Meteo fetch for 0.5deg Europe grid, reshape to 2D arrays
- Implement `detect.py`: `compute_frontal_zones()`, `classify_front_type()`
- Implement `zones.py`: zone definitions, `find_fronts_in_regions()` with fractional coverage threshold
- Implement `cli.py`: `analyze` command with console output
- **Validate interactively** against Meteo-France carte des fronts
- Tune gradient threshold, verify front type classification

### Phase 2 — Route analysis, clearance timing, and front tracking

- Add `find_frontal_clearance_time()` with hourly resolution, `compute_timing_spread()`
- Implement `tracking.py`: `build_zone_timeseries()`, `identify_front_events()`, `classify_zone_label()`
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
| `matplotlib` + `cartopy` | Map plotting for CLI validation (optional) |

No new dependencies beyond what's already in the project. Dynamic route assembly reuses existing flight waypoints instead of adding `pyproj`. Open-Meteo fetch reuses existing `OpenMeteoClient` with a new lightweight grid-fetch method.

---

## Important Caveats

**Frontal detection limitations**: the T850 gradient method reliably detects strong fronts but will miss weak frontal boundaries and shallow cold pools. Occluded fronts are particularly unreliable. For shallow convective situations common over the Mediterranean in summer, surface-based frontal detection is less meaningful than upper-level diagnostics. Flag frontal intensity below threshold as "weak frontal activity" rather than asserting no front is present.

**Gradient threshold tuning**: the 0.8 K/100km threshold is a starting point. Too low = noise, too high = missed fronts. Calibrate against Meteo-France carte des fronts during Phase 1.

**Resolution and smoothing**: at 0.5deg resolution (~55km), the grid resolves synoptic-scale fronts with 4-6 cells across a typical gradient zone. The Gaussian smoothing sigma is set to 0.5 grid cells (= 0.25deg ≈ 28km) — just enough to suppress single-cell noise without blurring narrow fronts. If validation shows too much noise at sigma=0.5, increase cautiously — but at 0.5deg, over-smoothing is the bigger risk.

**Resolution vs. accuracy**: at 0.5deg resolution, the TFP method gives front positions accurate to ~75-100km. The zone boxes are 3-5deg wide. This is appropriate for "cold front over northern France" but the code should not imply more precision than the method delivers.

**Zone refinement**: the 18-zone set is sized for reliable detection (all zones >= 96 grid points at 0.5deg). If validation reveals that certain zones are too coarse (e.g. a front in eastern France but not western), zones can be split freely — even a half-zone has ~50+ grid points at 0.5deg. Let the data drive this decision.

**Mediterranean summer**: shallow convective situations are not well captured by T850 gradients. Frontal detection is most valuable in the Atlantic/continental regime (autumn through spring) when synoptic-scale fronts dominate European weather.

**Open-Meteo usage**: ~15 requests per analysis run, ~900 calls/month at 2 runs/day — negligible against the 1M monthly API plan. Room to densify further or add models if needed.
