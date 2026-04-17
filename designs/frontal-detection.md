# Frontal Detection

> Zone-scale frontal presence detection from 850hPa gridded fields for synoptic narrative and route frontal tables.

## Intent

Detect fronts at zone scale (~200-400km) to answer "which fronts are active, where, and when do they clear the route?" — not to pinpoint front location to 50km. Feeds two outputs:
- **Synoptic narrative**: structured frontal data → LLM digest → natural-language briefing
- **Route frontal table**: deterministic per-zone, per-model front presence + clearance timing

Cross-section annotation is explicitly deferred — the cross-section already shows front effects (cloud, icing, wind shifts) at route-point resolution.

**Integration status**: CLI-only development tool. Not yet integrated into the briefing pipeline or scheduler. Calibration-first approach — validate detection quality before production integration.

## Architecture

```
src/weatherbrief/frontal/
├── grid.py       — grid definition, Open-Meteo fetch, field prep, terrain mask
├── detect.py     — gradient thresholding, dual T850+θe, front type classification
├── zones.py      — 18 European zones, 19 route templates, zone intersection
├── tracking.py   — two-pass anomaly filtering, zone timeseries, clearance timing
├── cache.py      — file cache keyed by (model, init_time) for dev iteration
├── cli.py        — analyze, zones, route, score, validate, diagnose subcommands
└── __main__.py   — python -m weatherbrief.frontal.cli
```

### Data Flow

```
Open-Meteo 850hPa grid (T, Td, wind) per model
    → prepare_field() — NaN fill, terrain mask
    → compute_theta_e() — equivalent potential temperature
    → [Pass 1] mean gradient across all hours (background)
    → [Pass 2] detect anomalies above background per hour
        → compute_frontal_zones_dual() — T850 OR θe gradient thresholding
        → classify_front_type() — cross-front wind classification
        → find_fronts_in_regions() — aggregate to zone scale
    → zone timeseries → clearance timing → timing spread
```

## Key Components

### Grid (`grid.py`)
- **Domain**: 35-60°N, -20 to 28°E at 0.5° resolution (4,131 points)
- **Fetch**: Lightweight `fetch_grid_fields()` on `OpenMeteoClient` — only 4 variables (T850, Td850, wind speed/dir), `chunk_size=500`, returns raw arrays (no WaypointForecast objects)
- **Field prep**: `prepare_field()` — nearest-neighbor NaN fill, rejects fields with >5% missing. Must run before any gradient computation (gaussian_filter silently corrupts NaN neighbors)
- **Terrain**: `build_terrain_mask()` from SRTM3 data (>1500m masked), `fill_terrain()` for linear interpolation across masked cells
- **θe**: `compute_theta_e()` via MetPy from T850 + Td850

### Detection (`detect.py`)
- **`compute_frontal_zones(field)`**: Single-field gradient thresholding
  - Gaussian smooth (σ=0.5) → `np.gradient` with lat-dependent spacing → magnitude in K/100km
  - Default threshold: 2.0 K/100km (captures ~8-10% of domain — the plan's 0.8 was too low, >50% exceeded it)
  - Also computes TFP (thermal front parameter) for plotting only
  - Returns gradient, frontal_mask, front_orientation, TFP

- **`compute_frontal_zones_dual(T850, theta_e)`**: OR-union of T850 and θe masks
  - T850 threshold: 2.0, θe threshold: 4.0 K/100km
  - Tracks detection source via bitmask (bit 0=T850, bit 1=θe)
  - Cold fronts show in both; warm fronts primarily in θe

- **`classify_front_type()`**: Cross-front wind component
  - Wind projected onto temperature gradient direction (cold→warm vector)
  - Positive = cold front, negative = warm front
  - Threshold: 2.0 km/h (~1 knot) — classifies ~85-90% of frontal points
  - θe-only detections with weak cross-front wind biased toward warm (these are exactly the warm fronts θe catches)
  - Returns: 0=not front, 1=cold, 2=warm, 3=indeterminate

### Zones (`zones.py`)
- **18 zones** covering European GA chokepoints — each ≥3×4° (≥96 grid points at 0.5°)
- **19 route templates** (e.g., `uk_alps`, `germany_med`) — ordered zone lists for common GA corridors
- **`find_fronts_in_regions()`**: Aggregates grid detections to zone scale
  - Threshold: ≥8% coverage AND ≥8 absolute points (prevents spurious tiny detections)
  - Computes dominant front type (vote), max intensity, mean orientation (circular mean with axial doubling)
- **`find_route_zones(waypoints)`**: Maps arbitrary route waypoints to zone sequence

### Anomaly Filtering (`tracking.py`)
Two-pass, per-channel approach that automatically filters persistent orographic/thermal gradients:
1. **Background**: Mean gradient across all forecast hours — computed separately for T850 and θe
2. **Per-channel anomaly check**: Each point is filtered against the background of the channel that detected it (T-detected → T background, θe-detected → θe background). Points detected by both pass if either channel's anomaly check passes.
3. **Thresholds scale per channel**: T850 anomaly ≥ 1.0 K/100km + floor ≥ 2.0; θe anomaly ≥ 2.0 K/100km + floor ≥ 4.0 (2× T, since θe gradients are naturally ~2× larger)

This eliminates Alpine, Pyrenean, and sea-land gradients without per-zone tuning — a front passing through for ~12h out of 72h barely moves the mean. The per-channel design prevents T background from killing θe-detected maritime/warm fronts (the original single-channel approach had POD ~24%).

- **`apply_anomaly_filter()`**: Per-channel anomaly filtering, used by `build_zone_timeseries()` and CLI commands (`score`, `validate`, `diagnose`)
- **`build_zone_timeseries()`**: Full pipeline for one model → `{zone: [{hour, present, type, intensity, orientation}, ...]}`
- **Clearance timing**: `find_frontal_clearance_time()` — earliest hour with 3+ consecutive clear hours
- **Timing spread**: `compute_timing_spread()` — inter-model agreement if spread ≤6h

## Key Choices

1. **Dual T850+θe detection** — OR-union catches both cold fronts (strong T gradient) and warm fronts (primarily θe gradient). The θe threshold (4.0) is higher than T850 (2.0) because θe has a broader range.

2. **Cross-front wind for type classification** (not temperature advection) — more stable because it depends only on wind direction relative to the front, not on wind speed × gradient magnitude.

3. **Per-channel anomaly filtering** — each detection channel (T850, θe) is filtered against its own time-mean background with scaled thresholds. Prevents the T850 background from killing θe-detected fronts (which are often maritime/warm fronts with weak T gradient but strong moisture signal). θe thresholds default to 2× T thresholds.

4. **Zone-scale aggregation** (not grid-point detection) — intentionally coarse. The system says "cold front over northern France" not "front at 48.5°N, 3.0°E". Coverage + absolute point thresholds prevent spurious tiny detections.

5. **Gradient threshold 2.0 K/100km** (not plan's initial 0.8) — calibrated empirically. At 0.8, >50% of domain exceeds threshold due to background European T850 gradients (~1 K/100km in spring).

## CLI Usage

```bash
# Full analysis with map plot
python -m weatherbrief.frontal.cli analyze --model ecmwf --plot

# Zone activity at hour 24
python -m weatherbrief.frontal.cli zones --hour 24

# Route frontal table with clearance timing
python -m weatherbrief.frontal.cli route --template uk_alps

# Score against calibration data (Météo-France carte des fronts)
python -m weatherbrief.frontal.cli score --case data/calibration/2026-04-16_12Z

# 4-column visual comparison (MF chart, expected, ECMWF, GFS)
python -m weatherbrief.frontal.cli validate --expected data/calibration/.../expected.yaml

# Deep diagnostic: replay pipeline for one zone/hour, print every intermediate value
python -m weatherbrief.frontal.cli diagnose --case data/calibration/2026-04-16_12Z \
  --model ecmwf --hour 0 --zone uk_south -v --plot
```

## Calibration

Calibration data in `data/calibration/<date>_<run>/`:
- `expected.yaml` — ground truth zones from Météo-France carte des fronts with front types
- `raw/` — cached Open-Meteo responses for reproducible scoring
- `reference/` — reference chart images

The `score` subcommand computes POD (probability of detection), FAR (false alarm ratio), CSI (critical success index), and per-zone hit/miss/false-alarm counts.

## Gotchas

- **NaN must be resolved before gradient computation** — `gaussian_filter` silently corrupts neighbors (treats NaN as zero), `np.gradient` propagates NaN to ~12+ adjacent cells. Always call `prepare_field()` first.
- **Wind direction is circular** — never interpolate raw direction. Convert to u/v first via `wind_to_uv()`, then fill/smooth u/v components.
- **Terrain mask fills, not removes** — `fill_terrain()` interpolates across terrain cells so gradients at terrain boundaries are smooth, not artificial edges. The mask is applied to results only.
- **Grid spacing is lat-dependent** — dlon_km varies with cos(lat). The code handles this per-row.

## References

- Full plan (with future phases): [frontal-detection-plan.md](./future/frontal-detection-plan.md)
- Calibration workflow: [front-calibration.md](./future/front-calibration.md)
- Tests: `tests/test_frontal_*.py` (5 files covering detect, grid, zones, tracking, cache)
- Key code: `src/weatherbrief/frontal/`
