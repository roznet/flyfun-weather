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
├── grid.py          — grid definition, Open-Meteo fetch, field prep, terrain mask
├── detect.py        — gradient thresholding, dual T850+θe, front type, Hewson diagnostics
│                      (theta_e_gradient_components shared with the snapshot source)
├── zones.py         — 20 European zones, 19 route templates, zone intersection
├── tracking.py      — two-pass anomaly filtering, zone timeseries, persistence + clearance timing
├── gates.py         — FrontGateConfig (one serializable detection recipe) + preset registry
├── sources.py       — HewsonFieldSource: SnapshotFieldSource (precompute NPZ, prod) +
│                      CaseFieldSource (recompute, calibration). One detector, swappable data.
├── route_sampling.py — on-track locator: sample → candidates → decisions (gated by config) +
│                      off-track proximity gating (#168). Takes a source, not a Case.
├── contour_fronts.py — 2-D TFP=0 front-line extractor (contourpy) gated by the same config (#195 §C2)
├── case.py          — calibration Case loader (Open-Meteo + ERA5 sources), field save/load
├── cache.py         — file cache keyed by (model, init_time) for dev iteration
├── cli.py           — analyze/zones/route/score/validate/diagnose/new-case/charts +
│                      plot-hewson/redraw-zones/route-hewson/route-fronts/front-calibrate/clear-cache
└── __main__.py      — python -m weatherbrief.frontal.cli
```

### Front detection: source-agnostic, config-driven (#195)

The per-leg locator (`route_sampling.py`) is split along two seams so one
detection algorithm serves the briefing pipeline, calibration, and the 2-D map:

- **Data source** (`sources.py`): `analyze_route_fronts(source, ...)` takes a
  `HewsonFieldSource`. Production reads the precomputed Hewson NPZ snapshot
  (`SnapshotFieldSource` — zero fetch, derivatives already on the full grid);
  calibration/ERA5 recompute from a `Case` (`CaseFieldSource`). Chosen in code,
  not a user toggle (design `hewson-fields-aviation-advisories.md` §6.2).
- **Gate recipe** (`gates.py`): the ~10 scattered threshold constants collapse
  into one frozen, serializable `FrontGateConfig` (carries the pressure level;
  gates are level-specific). Presets: `default / strict / sensitive /
  gradient-only`. Stamped into every `route_fronts.json` for reproducibility.
- **Candidate/decision split**: sample once → `generate_front_candidates`
  (all TFP zero-crossings, ungated) → `apply_gate_config` (`FrontDecision` with
  `accepted` / `rejected_by` / margins). N configs re-score one candidate set
  with zero re-sampling — the basis of the `front-calibrate` sweep CLI.

Pipeline wiring lives in `tasks/fronts.py` (`run_fronts` + `run_fronts_from_pack`),
gated by the experimental `auto_front_detection` preference (default off); the
artifact is served at `GET .../packs/{ts}/route-fronts`. The 2-D extractor is
served at `GET /api/hewson-map/fronts` and overlaid on the synoptic map.

**Two independent per-profile controls (#196, model B).** `auto_front_detection`
is the *master*: it generates `route_fronts.json` and drives the map / cross-section
overlays. The `fronts` advisory toggle (in the per-profile advisory catalog)
*independently* gates whether the GREEN/AMBER/RED grade surfaces — which can move
the overall flight assessment. `tasks/advise.py:_front_context` enables the advisory
by default when the artifact is present (discoverable the moment the master is on)
but honors an explicit `advisories.enabled["fronts"] = false` opt-out, so a pilot
can keep the overlays for situational awareness without letting the experimental
grade affect the badge. The settings UI defaults the `fronts` checkbox to the
master state and disables it while the master is off.

The zone-aggregation path (zones.py + tracking.py) is the original calibration
target; `route_sampling.py` is the newer per-leg Hewson direction (see Key Choices /
the pivot noted under Calibration). Both share the grid + detect primitives.

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
- **Domain**: 35-60°N, -20 to 28°E at **0.25° resolution** (101 × 193 = 19,493 points). Aligned with ERA5's default CDS regridding so ERA5 and Open-Meteo land on identical grid points (see `designs/future/hewson-fields-aviation-advisories.md` for rationale).
- **Fetch**: Lightweight `fetch_grid_fields(client, model_key, ...)` module-level helper (takes an `OpenMeteoClient`, not a method on it) — 4 variables per level (T, Td, wind speed/dir), `_GRID_CHUNK_SIZE=500`, returns raw arrays (no WaypointForecast objects). `levels` defaults to `[850]` for single-level detection; pass `[925, 850, 700]` for Hewson multi-level precompute (variable names are level-suffixed, e.g. `temperature_850hPa`)
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

- **`compute_hewson_diagnostics()`**: Raw θe-based Hewson derivatives (gradient, TFP, −∇²θe, advection) with NO threshold/mask — for visualization, threshold calibration, and the route-sampling path. Consumed by `route_sampling.py`.

- **`classify_front_type()`**: Cross-front wind component
  - Wind projected onto temperature gradient direction (cold→warm vector)
  - Positive = cold front, negative = warm front
  - Threshold: 2.0 km/h (~1 knot) — classifies ~85-90% of frontal points
  - θe-only detections with weak cross-front wind biased toward warm (these are exactly the warm fronts θe catches)
  - Returns: 0=not front, 1=cold, 2=warm, 3=indeterminate

### Zones (`zones.py`)
- **20 zones** covering European GA chokepoints — each ≥3×4°. A bare 3×4° box at 0.25° is 13×17 = 221 points; our smallest real zones (balearics, uk_south) carry 400–700 points.
- **19 route templates** (e.g., `uk_alps`, `germany_med`) — ordered zone lists for common GA corridors
- **`find_fronts_in_regions()`**: Aggregates grid detections to zone scale
  - Threshold: ≥8% coverage AND ≥32 absolute points (4× the old 8-point floor, preserving the same fraction-of-minimum-zone threshold at the 4× denser grid)
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
- **Persistence filter**: `_apply_persistence_filter()` — suppresses zones flagged "present" for too many consecutive hours (catches residual stationary gradients the anomaly check misses)
- **Clearance timing**: `find_frontal_clearance_time()` — earliest hour with 3+ consecutive clear hours (`find_clearance_times_all_models()` runs it across models)
- **Timing spread**: `compute_timing_spread()` — inter-model agreement if spread ≤6h

## Key Choices

1. **Dual T850+θe detection** — OR-union catches both cold fronts (strong T gradient) and warm fronts (primarily θe gradient). The θe threshold (4.0) is higher than T850 (2.0) because θe has a broader range.

2. **Cross-front wind for type classification** (not temperature advection) — more stable because it depends only on wind direction relative to the front, not on wind speed × gradient magnitude.

3. **Per-channel anomaly filtering** — each detection channel (T850, θe) is filtered against its own time-mean background with scaled thresholds. Prevents the T850 background from killing θe-detected fronts (which are often maritime/warm fronts with weak T gradient but strong moisture signal). θe thresholds default to 2× T thresholds.

4. **Zone-scale aggregation** (not grid-point detection) — intentionally coarse. The system says "cold front over northern France" not "front at 48.5°N, 3.0°E". Coverage + absolute point thresholds prevent spurious tiny detections.

5. **Gradient threshold 2.0 K/100km** (not plan's initial 0.8) — calibrated empirically. At 0.8, >50% of domain exceeds threshold due to background European T850 gradients (~1 K/100km in spring).

## CLI Usage

```bash
# Full analysis (auto-downloads DWD charts)
python -m weatherbrief.frontal.cli analyze --model ecmwf --plot

# Zone activity at hour 24
python -m weatherbrief.frontal.cli zones --hour 24

# Route frontal table with clearance timing
python -m weatherbrief.frontal.cli route --template uk_alps

# Create a new calibration case (fetches data + DWD charts + skeleton YAML)
python -m weatherbrief.frontal.cli new-case

# Score against calibration data
python -m weatherbrief.frontal.cli score --case data/calibration/2026-04-17_00Z

# 4-column visual comparison (reference chart, expected, ECMWF, GFS)
python -m weatherbrief.frontal.cli validate --charts ... --times ... --expected ...

# Deep diagnostic: replay pipeline for one zone/hour
python -m weatherbrief.frontal.cli diagnose --case data/calibration/2026-04-17_00Z \
  --model ecmwf --hour 0 --zone uk_south -v --plot

# Download DWD charts with zone overlay
python -m weatherbrief.frontal.cli charts --zones
```

## Calibration

**Reference charts**: DWD Bodenwetterkarte (surface analysis) and ICON forecast charts, downloaded via `charts` subcommand with `If-Modified-Since` HTTP caching. DWD charts have clear color-coded front lines (blue=cold, red=warm, purple=occluded) — preferred over Météo-France carte des fronts.

**Georeferencing**: Both DWD chart templates have fixed polar stereographic projections calibrated from user-provided gridline intersections (~1-3px accuracy). `_dwd_lonlat_to_pixel()` converts geographic coordinates to chart pixel positions for zone overlay rendering.

**Calibration dataset** in `data/calibration/<case>/`:
- `expected.yaml` — ground truth zones annotated from DWD charts with front types
- `raw/` — cached Open-Meteo responses for reproducible scoring
- `reference/` — DWD chart images + zone overlay

**Workflow**: `new-case` creates a complete case directory (fetches model data, downloads DWD charts, generates zone overlay, creates skeleton YAML). The pilot annotates expected zones, then `score` computes POD/FAR/CSI.

**Current baseline**: not re-established at 0.25°. Prior scoring ran at 0.5° with the old `_MIN_FRONTAL_POINTS = 8` threshold on two weak-front cases that have since been deleted as part of the grid-resolution upgrade. Baselines will be re-computed once the ERA5 retrospective dataset (see `designs/future/hewson-fields-aviation-advisories.md`) provides strong-front and synoptically-diverse calibration cases.

Historical reference (0.5°, 8-point floor, the two deleted cases):
- Case 2026-04-16: ECMWF POD=57%, FAR=77%, CSI=19%
- Case 2026-04-17: ECMWF POD=100%, FAR=77%, CSI=23%
- Candidate higher thresholds (T=3.0, θe=6.0): FAR drops to ~57% at cost of missing weak fronts

These informed our pivot away from zone-level detection toward per-leg Hewson advisories — the numbers aren't operational-ready and won't be before the new calibration set lands.

## Gotchas

- **NaN must be resolved before gradient computation** — `gaussian_filter` silently corrupts neighbors (treats NaN as zero), `np.gradient` propagates NaN to ~12+ adjacent cells. Always call `prepare_field()` first.
- **Wind direction is circular** — never interpolate raw direction. Convert to u/v first via `wind_to_uv()`, then fill/smooth u/v components.
- **Terrain mask fills, not removes** — `fill_terrain()` interpolates across terrain cells so gradients at terrain boundaries are smooth, not artificial edges. The mask is applied to results only.
- **Grid spacing is lat-dependent** — dlon_km varies with cos(lat). The code handles this per-row.

## References

- Full plan (with future phases): [frontal-detection-plan.md](./future/frontal-detection-plan.md)
- Calibration workflow: [front-calibration.md](./future/front-calibration.md)
- Tests: `tests/test_frontal_*.py` (7 files covering detect, grid, zones, tracking, cache, case, route_sampling)
- Key code: `src/weatherbrief/frontal/`
