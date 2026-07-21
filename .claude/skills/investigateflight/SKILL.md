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
Use the SSH user/host from the deploy skill or CLAUDE.md config.

**Important:** On the server, pack data does NOT live under the project directory.
First, find the actual data path by checking the remote `.env`:

```bash
# Get the host data directory from the server's .env
ssh {user}@{server} "grep HOST_DATA_DIR flyfun-weather/.env"
# This returns something like: HOST_DATA_DIR=/mnt/flyfun_data/weather/data
```

Then use that path (`{HOST_DATA_DIR}`) to find and rsync the pack:

```bash
# Find the pack directory on the server (user_id varies — use wildcard)
ssh {user}@{server} "ls {HOST_DATA_DIR}/packs/*/{flight_id}/"

# Rsync to local for offline analysis
rsync -avz {user}@{server}:{HOST_DATA_DIR}/packs/\*/{flight_id}/ \
  data/packs/debug/{flight_id}/
```

On the server, data lives at `{HOST_DATA_DIR}/packs/{user_id}/{flight_id}/{timestamp}/`.

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

# --- Load advisories (raw JSON — no typed model for the manifest envelope) ---
import json
advisories_data = json.loads((pack_dir / "route_advisories.json").read_text())
# Top-level keys: advisories (list), catalog, route_name, cruise_altitude_ft,
#                 flight_ceiling_ft, total_distance_nm, models, aggregation, airport_conditions
# Each advisory in advisories_data["advisories"] has keys:
#   advisory_id, aggregate_status, aggregate_detail, per_model, parameters_used
```

## Step 5 — Reproduce and debug specific computations

### A. Recompute analysis (without re-fetching weather data)

Re-runs sounding analysis from saved cross-sections and route points. Useful when you've
changed analysis logic (cloud layers, icing, vertical motion, etc.) and want to verify
against an existing briefing.

```python
from datetime import datetime, timezone
from weatherbrief.tasks.analyze import run_analysis_from_pack
from weatherbrief.models import RouteConfig

# Load route from briefing data
route = RouteConfig.model_validate(briefing_data["route"])

result = run_analysis_from_pack(
    pack_dir=pack_dir,
    route=route,
    departure_time=datetime(2026, 2, 27, 9, tzinfo=timezone.utc),
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
    persist=False,   # ALWAYS when investigating — see warning below
)
# result.manifest is the RouteAdvisoriesManifest
```

> ⚠️ **`persist=False` is not optional here.** The default is `persist=True`, which
> **overwrites `route_advisories.json` in the pack you are investigating** — and when
> combined with `enabled_ids` it writes back a manifest containing *only* those
> evaluators, silently discarding the rest of the saved results. The default is correct
> for its production caller (the API recalculate endpoint, where the pack is *meant* to
> track the user's current advisory selection), but it is wrong for every debugging use.
> `eval_workbench/rerun.py` passes `persist=False` for the same reason: a "what changed"
> comparison must never clobber the baseline it is comparing against.
>
> The same applies to any investigation helper that takes a `pack_dir` — check whether it
> writes before you run it on a real pack. Prefer copying the pack to `data/packs/debug/`
> first if you are unsure.

### C. Inspect sounding analysis at a specific point

```python
manifest = load_route_analyses(pack_dir)

# Pick a route point (by index or waypoint name)
for rpa in manifest.analyses:
    name = rpa.waypoint_icao or rpa.waypoint_name or f"pt{rpa.point_index}"
    print(f"Point {rpa.point_index}: {name}")
    for model, sounding in rpa.sounding.items():
        print(f"  {model}:")
        print(f"    Cloud layers: {[(c.base_ft, c.top_ft) for c in sounding.cloud_layers]}")
        print(f"    Icing zones:  {[(i.base_ft, i.top_ft, i.risk) for i in sounding.icing_zones]}")
        if sounding.vertical_motion:
            vm = sounding.vertical_motion
            # CAT layer fields: base_ft, top_ft, risk (CATRiskLevel), richardson_number
            print(f"    CAT: {[(c.base_ft, c.top_ft, c.risk, c.richardson_number) for c in vm.cat_risk_layers]}")
        if sounding.indices:
            print(f"    Freezing lvl: {sounding.indices.freezing_level_ft}")
```

### D. Recompute altitude advisories from a sounding

```python
from weatherbrief.analysis.sounding.advisories import compute_altitude_advisories

for rpa in manifest.analyses:
    name = rpa.waypoint_icao or rpa.waypoint_name or f"pt{rpa.point_index}"
    for model, sounding in rpa.sounding.items():  # note: .sounding not .soundings
        alt_adv = compute_altitude_advisories(
            sounding=sounding,
            cruise_altitude_ft=manifest.cruise_altitude_ft,
        )
        print(f"{name} / {model}: {alt_adv}")
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

# snapshot.forecasts is a list[WaypointForecast]
# WaypointForecast fields: waypoint, model (ModelSource enum), fetched_at, hourly
# HourlyForecast fields: time, pressure_levels (list[PressureLevelData]), plus surface obs
# PressureLevelData fields: pressure_hpa, temperature_c, relative_humidity_pct, dewpoint_c,
#   wind_speed_kt (NOT _kts), wind_direction_deg, geopotential_height_m,
#   vertical_velocity_pa_s, cloud_liquid_water_kg_kg, ice_mixing_ratio_kg_kg,
#   cloud_area_fraction_pct, clw_interpolated
for fc in snapshot.forecasts:
    wp = fc.waypoint
    wp_name = wp.icao if hasattr(wp, 'icao') else str(wp)
    print(f"Waypoint: {wp_name}, Model: {fc.model.value}, hours: {len(fc.hourly)}")
    for h in fc.hourly[:1]:
        print(f"  {len(h.pressure_levels)} pressure levels")
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

# snapshot.forecasts is a flat list of WaypointForecast (one per waypoint×model)
# Filter by waypoint and model:
fc = next(f for f in snapshot.forecasts
          if f.waypoint.icao == "EGTF" and f.model.value == "gfs")
hourly = fc.hourly[0]  # first forecast hour

# 2. Re-run the full sounding analysis pipeline
from weatherbrief.analysis.sounding import analyze_sounding
result = analyze_sounding(hourly.pressure_levels, hourly, icing_severity_enhance=True)

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
- `hourly.pressure_levels` — raw `PressureLevelData` list (the input)
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

## Step 7 — Compare with GRAMET cross-section

The pack includes `gramet.pdf` — the Autorouter GRAMET cross-section image (same GFS data).
Use this for a broad visual sanity check of our computed cloud, icing, and convection bands.

### Verify GFS reference times match (MANDATORY first step)

The GRAMET and our analysis must use the **same GFS model run** for any comparison to be valid.
Three times must be consistent:

1. **GRAMET GFS RefTime** — printed at the bottom of the GRAMET PDF (e.g., "GFS RefTime 2026-02-27 18:00Z")
2. **Our Open-Meteo GFS init time** — `model_init_times.gfs` in the pack metadata (unix timestamp)
3. **Our GRIB GFS init time** — `grib_init_times.gfs` in the pack metadata (unix timestamp, if GRIB was fetched)

```python
from datetime import datetime, timezone

# Get init times from the API (if server running) or DB
# API: curl -s http://localhost:8000/api/flights/{flight_id}/packs/latest
# Fields: model_init_times.gfs, grib_init_times.gfs (unix timestamps)

om_gfs_init = 1772193600   # from model_init_times.gfs
grib_gfs_init = 1772193600  # from grib_init_times.gfs (may be absent)

print("Open-Meteo GFS init:", datetime.fromtimestamp(om_gfs_init, tz=timezone.utc))
print("GRIB GFS init:     ", datetime.fromtimestamp(grib_gfs_init, tz=timezone.utc))
# Compare with GRAMET's "GFS RefTime" from the PDF bottom line
```

**If any of the three times differ, STOP — the comparison is not valid.** Different GFS runs
produce different forecasts, so mismatched runs would lead to misleading false negatives
(real discrepancies that aren't bugs). Note this to the user and skip the comparison.

### Read the GRAMET

Use the `Read` tool on `{pack_dir}/gramet.pdf` (with `pages: "1"`). The PDF renders as
a visual cross-section showing:
- **White cloud masses** — cloud coverage by altitude along route
- **Cyan/turquoise dashed contours** — icing zones
- **Brown dashed isotherms** — temperature lines (0°C, -10°C, -20°C, etc.)
- **Magenta horizontal line** — freezing level
- **Grey/brown terrain profile** — along the bottom
- **Wind barbs** — at regular grid points
- **Waypoint labels** — along the x-axis with distance (nm) and time (UTC)

Note approximate FL/altitude positions from the y-axis (left side shows FL, right shows hPa).

### Extract our computed data

```python
from pathlib import Path
from weatherbrief.tasks.artifacts import load_route_analyses

pack_dir = Path("data/packs/{user_id}/{flight_id}/{timestamp}")
manifest = load_route_analyses(pack_dir)

last_dist = -40
for rpa in manifest.analyses:
    dist_nm = rpa.distance_from_origin_nm or 0
    name = rpa.waypoint_icao or rpa.waypoint_name or f"pt{rpa.point_index}"
    is_waypoint = rpa.waypoint_icao is not None

    # Sample every ~40nm or at waypoints
    if not is_waypoint and (dist_nm - last_dist) < 40:
        continue
    last_dist = dist_nm

    print(f"=== {name} @ {dist_nm:.0f} nm ===")

    for model, s in rpa.sounding.items():
        if model != "gfs":
            continue
        clouds = [(c.base_ft, c.top_ft) for c in s.cloud_layers]
        icing = [(i.base_ft, i.top_ft, i.risk, i.icing_type) for i in s.icing_zones]
        conv = s.convective
        nwp = s.nwp_cloud_diagnostics

        print(f"  Clouds: {clouds}")
        if icing:
            print(f"  Icing:  {icing}")
        if conv and "NONE" not in str(conv.risk_level):
            print(f"  Conv: risk={conv.risk_level}")
        if nwp:
            for ln in ['low', 'mid', 'high']:
                layer = getattr(nwp, ln, None)
                if layer and layer.cover_pct and layer.cover_pct > 5:
                    base_fl = round(layer.base_ft / 100)
                    top_fl = round(layer.top_ft / 100)
                    print(f"  NWP-{ln}: {layer.cover_pct:.0f}% FL{base_fl}-FL{top_fl}")
        if s.indices and s.indices.freezing_level_ft:
            fz = s.indices.freezing_level_ft
            print(f"  Fz lvl: FL{round(fz/100)} ({fz:.0f} ft)")
    print()
```

### Compare

Produce a side-by-side comparison table covering these areas:

| Area | GRAMET (visual) | Our analysis |
|------|----------------|--------------|
| **Freezing level** | Read from magenta line / 0°C isotherm | `indices.freezing_level_ft` |
| **Cloud layers** | White masses — note approximate FL bands at key distances | `cloud_layers` base/top + `nwp_cloud_diagnostics` low/mid/high |
| **Icing** | Cyan contour positions | `icing_zones` base/top/risk |
| **Convection** | CB symbols or convective markers | `convective.risk_level` |

Precision expectation: both use the same GFS data, so broad patterns should match.
Differences of ~500-1000ft in altitude or ~20nm along route are normal given visual
reading precision. Flag significant discrepancies (e.g., our analysis misses a cloud
layer the GRAMET clearly shows, or icing zones at very different altitudes).

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

## Common pitfalls (field names & nullability)

The first one below silently destroys data rather than erroring — the rest cause
runtime errors:

- **`run_advisories_from_pack` writes to the pack unless you pass `persist=False`** (see
  step 5B). With `enabled_ids` it replaces the saved manifest with only those evaluators.
  Investigation must never mutate the artifact it is investigating.
- **`AdvisoryResult` is not the manifest** — use `result.manifest.advisories`, not
  `result.advisories`.

These are the mistakes most likely to cause runtime errors during investigation:

- **`PressureLevelData.wind_speed_kt`** — NOT `wind_speed_kts` (no trailing 's')
- **`PressureLevelData.vertical_velocity_pa_s`** — NOT `omega_pa_s`
- **`HourlyForecast.pressure_levels`** — NOT `levels`
- **`ForecastSnapshot.forecasts`** — flat `list[WaypointForecast]`, NOT `.waypoints[i].forecasts[model]`. Each `WaypointForecast` has `.waypoint`, `.model` (enum, use `.value` for string), `.hourly`
- **`RouteAnalysesManifest`** has no `flight_ceiling_ft` field (only `cruise_altitude_ft`)
- **Advisory JSON** — `route_advisories.json` top-level is a dict with `advisories` (list), each item keyed by `advisory_id` (NOT `evaluator_id`)
- **Many numeric fields can be `None`** — always guard before formatting: `f"{val:.1f}" if val is not None else "N/A"`. Common None fields: `max_w_fpm`, `max_omega_pa_s`, `richardson_number`, `bv_freq_squared_per_s2`, `w_fpm`, `omega_pa_s` on DerivedLevel
- **`model` on WaypointForecast is an enum** (`ModelSource`) — use `.value` for string comparison (e.g., `fc.model.value == "gfs"`)
- **`rpa.sounding`** (not `.soundings`) — dict mapping model name (str) to SoundingAnalysis

## Tips

- Use `python -m json.tool` or `jq` to pretty-print API responses
- The `forecasts.json` file can be very large (10+ MB) — use `jq` to extract specific parts
- For frontend visualization debugging, the cross-section renderer is at `web/ts/visualization/cross-section/renderer.ts` and layer definitions at `web/ts/visualization/cross-section/layers.ts`
- The data extraction pipeline (JSON → canvas) is in `web/ts/visualization/data-extract.ts`
- Route map rendering (colored segments) is in `web/ts/visualization/route-map/renderer.ts`
