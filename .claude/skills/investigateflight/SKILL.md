---
name: investigateflight
description: Investigate a flight briefing — load its data, reproduce advisories, cross-section layers, and level data for debugging
---

# Investigate Flight

Debug and understand how weather data, advisories, cross-section layers, and metrics are computed for a specific flight.

## Step 1 — Extract the flight ID from the URL

The user provides a URL. Extract `{flight_id}` and optionally `{timestamp}`:

| URL pattern | Example |
|-------------|---------|
| `localhost:8000/briefing.html?flight={flight_id}` | `localhost:8000/briefing.html?flight=egtf_lflx_lfmd-2026-02-27-1a52` |
| `weather.flyfun.aero/briefing.html?flight={flight_id}` | same pattern on prod |

The **flight ID** format is: `{safe_route_name}-{target_date}-{params_hash}`
- `safe_route_name`: lowercased waypoints joined by `_` (e.g., `egtf_lflx_lfmd`)
- `target_date`: `YYYY-MM-DD`
- `params_hash`: 4-char hex hash of `{time, alt, ceil, dur}` — see `src/weatherbrief/api/flights.py:168-178`

## Step 2 — Get the pack data onto local disk

The primary investigation method is **disk + Python** — loading artifacts directly and calling
individual functions. The API is only useful for quick final-result checks; it can't expose
intermediate computations needed for real debugging.

### Local flight (URL was localhost)

Data is already on disk. Find the pack directory:

```bash
# List packs for this flight
ls data/packs/*/{flight_id}/
```

If the dev server is running, you can also use the API to find the latest timestamp:
```bash
curl -s http://localhost:8000/api/flights/{flight_id}/packs/latest | python -m json.tool
```

### Production flight (URL was weather.flyfun.aero)

Rsync the pack data from prod to a local `debug/` directory, then work locally.
Use the SSH user/host and project directory from the deploy skill or CLAUDE.md config.

```bash
# Find the pack directory on the server (user_id varies — use wildcard)
ssh {user}@{server} "ls {project_dir}/data/packs/*/{flight_id}/"

# Rsync to local for offline analysis
rsync -avz {user}@{server}:{project_dir}/data/packs/\*/{flight_id}/ \
  data/packs/debug/{flight_id}/
```

On the server, data lives at `{project_dir}/data/packs/{user_id}/{flight_id}/{timestamp}/`.

## Step 3 — Locate the pack directory on disk

Locally, pack data is stored at:
```
data/packs/{user_id}/{flight_id}/{timestamp}/
```

The timestamp directory name uses safe characters: colons → dashes, `+` → `p`.
Example: `2026-02-25T13-23-00.013596p00-00`

### Pack contents

| File | Contents |
|------|----------|
| `briefing.json` | Route + analyses + observations + metadata (no raw forecasts) |
| `forecasts.json` | Route + metadata + raw forecasts only (large file) |
| `cross_section.json` | `RouteCrossSection[]` — interpolated vertical slices for visualization |
| `route_analyses.json` | `RouteAnalysesManifest` — sounding analysis per waypoint & route point |
| `elevation_profile.json` | `ElevationProfile` — SRTM terrain along route |
| `route_advisories.json` | `RouteAdvisoriesManifest` — all 13 advisory evaluator results |
| `route_points.json` | `RoutePoint[]` — interpolated waypoints with lat/lon/distance |
| `fetch_meta.json` | Metadata: fetched_at, models_fetched |
| `gramet.pdf` | GRAMET cross-section image (Autorouter) |
| `skewt/*.png` | Skew-T diagrams per waypoint/model |
| `digest.md` / `digest.json` | LLM-generated weather digest |

> **Legacy note**: Old packs may have a single `snapshot.json` instead of the split files. The `load_briefing()` / `load_forecasts()` helpers handle fallback automatically.

## Step 4 — Load and inspect data in Python

Activate the venv first (check for `venv/` or `../main/venv/`).

```python
from pathlib import Path
import json

# Set the pack directory
pack_dir = Path("data/packs/{user_id}/{flight_id}/{timestamp}")

# --- Load artifacts using project helpers ---
from weatherbrief.tasks.artifacts import (
    load_route_analyses,
    load_cross_sections,
    load_elevation_profile,
    load_briefing,
    load_forecasts,
)

manifest = load_route_analyses(pack_dir)
cross_sections = load_cross_sections(pack_dir)
elevation = load_elevation_profile(pack_dir)

# --- Load briefing data (route + analyses + observations) ---
briefing_data = load_briefing(pack_dir)  # dict, auto-falls back to snapshot.json

# --- Load raw forecasts (large — only when needed) ---
forecasts_data = load_forecasts(pack_dir)  # dict, auto-falls back to snapshot.json

# --- Parse into typed model ---
from weatherbrief.models import ForecastSnapshot
snapshot = ForecastSnapshot.model_validate(forecasts_data)

# --- Load advisories ---
from weatherbrief.models.analysis import RouteAdvisoriesManifest
advisories = RouteAdvisoriesManifest.model_validate_json(
    (pack_dir / "route_advisories.json").read_text()
)
```

## Step 5 — Reproduce and debug specific computations

### A. Recompute analysis (without re-fetching weather data)

Re-runs sounding analysis from saved cross-sections and route points. Useful when you've
changed analysis logic (cloud layers, icing, vertical motion, etc.) and want to verify
against an existing briefing.

```python
from weatherbrief.tasks.analyze import run_analysis_from_pack
from weatherbrief.models import RouteConfig

# Load route from briefing data
route = RouteConfig.model_validate(briefing_data["route"])

result = run_analysis_from_pack(
    pack_dir=pack_dir,
    route=route,
    target_date="2026-02-27",
    target_hour=9,
    icing_severity_enhance=True,
)
# result.waypoint_analyses — per-waypoint sounding analyses
# result.route_analyses — per-route-point analyses (used by advisories)
# result.route_analyses_manifest — RouteAnalysesManifest
```

### B. Recompute advisories (without re-fetching weather data)

```python
from weatherbrief.tasks.advise import run_advisories_from_pack
from weatherbrief.analysis.advisories.registry import AdvisoryAggregation

result = run_advisories_from_pack(
    pack_dir=pack_dir,
    flight_ceiling_ft=18000,
    advisory_models=["gfs", "ecmwf"],       # optional: subset of models
    enabled_ids={"icing_escape", "convective"},  # optional: specific evaluators
    user_params={"icing_escape": {"threshold_ft": 4000}},  # optional: tuned params
    aggregation=AdvisoryAggregation.WORST,
)
# result is a RouteAdvisoriesManifest
```

### B. Inspect sounding analysis at a specific point

```python
manifest = load_route_analyses(pack_dir)

# Pick a route point (by index or location name)
for rpa in manifest.analyses:
    print(f"Point {rpa.route_point_index}: {rpa.location_name}")
    for model, sounding in rpa.soundings.items():
        print(f"  {model}: CAPE={sounding.cape_j_per_kg} CIN={sounding.cin_j_per_kg}")
        print(f"    Cloud layers: {[(c.base_ft, c.top_ft) for c in sounding.cloud_layers]}")
        print(f"    Icing layers: {[(i.base_ft, i.top_ft, i.severity) for i in sounding.icing_layers]}")
        if sounding.vertical_motion:
            vm = sounding.vertical_motion
            print(f"    CAT: {[(c.base_ft, c.top_ft, c.severity) for c in vm.cat_layers]}")
```

### D. Recompute altitude advisories from a sounding

```python
from weatherbrief.analysis.sounding.advisories import compute_altitude_advisories

for rpa in manifest.analyses:
    for model, sounding in rpa.soundings.items():
        alt_adv = compute_altitude_advisories(
            sounding=sounding,
            cruise_altitude_ft=manifest.cruise_altitude_ft,
            flight_ceiling_ft=manifest.flight_ceiling_ft,
        )
        print(f"{rpa.location_name} / {model}: {alt_adv}")
```

### E. Inspect cross-section data for a layer

```python
cross_sections = load_cross_sections(pack_dir)

for cs in cross_sections:
    print(f"Model: {cs.model}, points: {len(cs.points)}")
    # Each point has pressure levels with weather data
    for pt in cs.points[:2]:  # first 2 points
        print(f"  lat={pt.lat:.2f} lon={pt.lon:.2f} dist={pt.distance_km:.1f}km")
        for lvl in pt.levels[:3]:  # first 3 levels
            print(f"    {lvl.pressure_hpa}hPa: T={lvl.temperature_c}C RH={lvl.relative_humidity}%"
                  f" wind={lvl.wind_speed_kts}kt cloud={lvl.cloud_cover}")
```

### F. Inspect raw forecast data for a specific waypoint/model/level

```python
# Load forecasts (large file — only when you need raw data)
forecasts_data = load_forecasts(pack_dir)
snapshot = ForecastSnapshot.model_validate(forecasts_data)

# snapshot structure: snapshot.waypoints[i].forecasts[model_name].levels[j]
for wp in snapshot.waypoints:
    print(f"Waypoint: {wp.icao} ({wp.lat}, {wp.lon})")
    for model_name, forecast in wp.forecasts.items():
        print(f"  Model: {model_name}, {len(forecast.levels)} levels")
```

### G. Re-run analysis pipeline on a single point's forecast

When a metric looks wrong at a specific route point (icing, clouds, SFIP, precip,
vertical motion, etc.), the pattern is always: **get the `HourlyForecast` → call
`analyze_sounding()` → inspect the result**. This re-runs the full pipeline
(thermodynamics → clouds → inversions → icing → SFIP → precip → vertical motion)
and gives you all intermediate results.

```python
# 1. Load forecasts and get the HourlyForecast for the point of interest
forecasts_data = load_forecasts(pack_dir)
snapshot = ForecastSnapshot.model_validate(forecasts_data)

wp = snapshot.waypoints[point_index]   # by index, or iterate by lat/lon
forecast = wp.forecasts["gfs"]         # or "ecmwf", etc.
hourly = forecast.hourly[hour_index]   # the target hour

# 2. Re-run the full sounding analysis pipeline
from weatherbrief.analysis.sounding import analyze_sounding
result = analyze_sounding(hourly.levels, hourly, icing_severity_enhance=True)

# 3. Inspect whichever part is relevant:
#    result.cloud_layers         — EnhancedCloudLayer list
#    result.icing_zones          — IcingZone list (Ogimet index)
#    result.sfip_zones           — SFIPZone list (fuzzy-logic)
#    result.inversion_layers     — InversionLayer list
#    result.precipitation        — PrecipitationAssessment
#    result.vertical_motion      — VerticalMotionAssessment (CAT layers)
#    result.convective           — ConvectiveAssessment
#    result.indices              — ThermodynamicIndices (CAPE, freezing level, etc.)
#    result.derived_levels       — per-level DerivedLevel with all computed fields
#    result.nwp_cloud_diagnostics — NWPCloudDiagnostics (GFS per-layer base/top)
```

The `HourlyForecast` (`hourly`) also carries the NWP inputs that feed the pipeline:
- `hourly.levels` — raw `PressureLevelData` list (the input)
- `hourly.nwp_cloud_diagnostics` — `NWPCloudDiagnostics` with `.low`/`.mid`/`.high`
  layers, each having `cover_pct`, `base_ft`, `top_ft`, `top_temp_c`
- `hourly.cloud_cover_low_pct` / `_mid_pct` / `_high_pct` — bulk NWP cloud %

For deeper tracing, call individual sub-modules directly (they're all in
`weatherbrief.analysis.sounding.*`: `clouds`, `icing`, `sfip`, `precipitation`,
`vertical_motion`, `inversions`). Check each module's main function signature —
they take `derived_levels` + relevant context from `hourly`.

## Step 6 — Run a single advisory evaluator in isolation

```python
from weatherbrief.analysis.advisories.registry import get_evaluator
from weatherbrief.analysis.advisories import RouteContext

# Build context from loaded data
context = RouteContext(
    route_analyses=manifest,
    cross_sections=cross_sections,
    elevation=elevation,
    flight_ceiling_ft=18000,
)

evaluator = get_evaluator("icing_escape")
result = evaluator.evaluate(context, model="gfs", params={"threshold_ft": 4000})
print(f"Severity: {result.severity}, message: {result.message}")
```

## Appendix — API endpoints (quick checks only)

For quick inspection when the server is running. Not needed for the main debugging workflow above.
Pattern: `/api/flights/{flight_id}/packs/{timestamp}/{resource}` (use `latest` for most recent pack).

| Suffix | Returns |
|--------|---------|
| `/snapshot` | Raw forecast JSON |
| `/route-analyses` | RouteAnalysesManifest |
| `/advisories` | RouteAdvisoriesManifest |
| `/advisories/recalculate` (POST) | Re-evaluate with custom params |
| `/elevation` | ElevationProfile |
| `/skewt/{icao}/{model}` | Skew-T PNG |
| `/digest/json` | Structured LLM digest |

## Available advisory evaluators (13 total)

| ID | Category | Description |
|----|----------|-------------|
| `fiki_icing` | Icing | FIKI-capable layer thickness |
| `icing_escape` | Icing | Escape viability (non-FIKI) |
| `cloud_top` | Icing | Cloud top vs ceiling |
| `turbulence` | Turbulence | CAT + vertical motion |
| `mountain_wind` | Turbulence | Orographic/rotor risk |
| `ifr_feasibility` | Feasibility | Composite IFR go/no-go |
| `vfr_feasibility` | Feasibility | Composite VFR go/no-go |
| `airport_wind` | Airport | Crosswind + gust |
| `flight_category` | Airport | Ceiling/visibility |
| `convective` | Convective | Convective risk along route |
| `vmc_cruise` | Convective | Cloud coverage at cruise |
| `freezing_level` | Other | Freezing level vs terrain |
| `model_agreement` | Other | Cross-model divergence |

## Tips

- Use `python -m json.tool` or `jq` to pretty-print API responses
- The `forecasts.json` file can be very large (10+ MB) — use `jq` to extract specific parts
- For frontend visualization debugging, the cross-section renderer is at `web/ts/visualization/cross-section/renderer.ts` and layer definitions at `web/ts/visualization/cross-section/layers.ts`
- The data extraction pipeline (JSON → canvas) is in `web/ts/visualization/data-extract.ts`
- Route map rendering (colored segments) is in `web/ts/visualization/route-map/renderer.ts`
