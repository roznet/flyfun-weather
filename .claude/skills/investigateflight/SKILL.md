---
name: investigateflight
description: Investigate a flight briefing — load its data, reproduce advisories, cross-section layers, and level data for debugging
---

# Investigate Flight

Debug and understand how weather data, advisories, cross-section layers, and metrics are
computed for a specific flight.

**Read `designs/references/briefing-pack-data-model.md` alongside this.** It carries the pack
layout, the 13-evaluator catalog, the analysis result structure, the API surface, and — most
importantly — the field-name traps and the one destructive default that can silently overwrite
the pack you're investigating. This file holds the recipes; that one holds the facts.

## Step 1 — Extract the flight ID from the URL

The user provides a briefing URL of the form `.../briefing.html?flight={flight_id}` (localhost
or production). Extract `{flight_id}`, and `{timestamp}` if present. The ID format is
documented in the reference.

## Step 2 — Get the pack data onto local disk

The primary investigation method is **disk + Python** — loading artifacts directly and calling
individual functions. The API is only useful for quick final-result checks; it can't expose the
intermediate computations that real debugging needs.

**Local flight** — data is already on disk:

```bash
ls {DATA_DIR}/packs/*/{flight_id}/
```

If the dev server is running, the API can find the latest timestamp:

```bash
curl -s http://localhost:8000/api/flights/{flight_id}/packs/latest | python -m json.tool
```

**Production flight** — rsync the pack to a local scratch directory and work locally. Resolve
`<user>@<server>` and `<project-dir>` per `designs/references/deployment-paths.md`.

Pack data does **not** live under the project directory on the server, so find the real path
first. Anchor the grep with `^HOST_` — a bare `DATA_DIR` match returns the *container* path
(`/app/data`), which is not usable over plain ssh:

```bash
ssh <user>@<server> "grep '^HOST_DATA_DIR=' <project-dir>/.env | cut -d= -f2"
```

Then use that value (`{HOST_DATA_DIR}`) — the `user_id` segment varies, so wildcard it:

```bash
ssh <user>@<server> "ls {HOST_DATA_DIR}/packs/*/{flight_id}/"

rsync -avz <user>@<server>:{HOST_DATA_DIR}/packs/\*/{flight_id}/ \
  {DATA_DIR}/packs/debug/{flight_id}/
```

## Step 3 — Load data in Python

Activate the worktree's venv first. Pack layout and file contents: see the reference.

```python
from pathlib import Path
import json

pack_dir = Path("{DATA_DIR}/packs/{user_id}/{flight_id}/{timestamp}")

from weatherbrief.tasks.artifacts import (
    load_route_analyses,
    load_cross_sections,
    load_elevation_profile,
    load_briefing,
    load_forecasts,
)

manifest      = load_route_analyses(pack_dir)
cross_sections = load_cross_sections(pack_dir)
elevation     = load_elevation_profile(pack_dir)

briefing_data = load_briefing(pack_dir)    # route + analyses + observations
forecasts_data = load_forecasts(pack_dir)  # large — only when needed

from weatherbrief.models import ForecastSnapshot
snapshot = ForecastSnapshot.model_validate(forecasts_data)

# Advisories have no typed model for the manifest envelope — read the JSON.
advisories_data = json.loads((pack_dir / "route_advisories.json").read_text())
```

Both `load_briefing` and `load_forecasts` fall back to legacy `snapshot.json` automatically.

## Step 4 — Reproduce specific computations

### A. Recompute analysis (without re-fetching weather)

Re-runs sounding analysis from saved cross-sections and route points. Use when you've changed
analysis logic and want to verify against an existing briefing.

```python
from datetime import datetime, timezone
from weatherbrief.tasks.analyze import run_analysis_from_pack
from weatherbrief.models import RouteConfig

route = RouteConfig.model_validate(briefing_data["route"])

result = run_analysis_from_pack(
    pack_dir=pack_dir,
    route=route,
    departure_time=datetime(2026, 2, 27, 9, tzinfo=timezone.utc),
    icing_severity_enhance=True,
)
# result.waypoint_analyses / .route_analyses / .route_analyses_manifest
```

### B. Recompute advisories (without re-fetching weather)

```python
from weatherbrief.tasks.advise import run_advisories_from_pack
from weatherbrief.analysis.advisories.registry import AdvisoryAggregation

result = run_advisories_from_pack(
    pack_dir=pack_dir,
    flight_ceiling_ft=18000,
    advisory_models=["gfs", "ecmwf"],             # optional: subset of models
    enabled_ids={"icing_escape", "convective"},   # optional: specific evaluators
    user_params={"icing_escape": {"threshold_ft": 4000}},
    aggregation=AdvisoryAggregation.WORST,
    persist=False,   # ALWAYS when investigating
)
# result.manifest is the RouteAdvisoriesManifest
```

> ⚠️ **`persist=False` is not optional.** The default overwrites `route_advisories.json` in the
> pack you are investigating, and with `enabled_ids` it discards every evaluator you didn't
> name. See "Destructive default" in the reference — it applies to any helper taking a
> `pack_dir`.

### C. Inspect sounding analysis at a point

```python
manifest = load_route_analyses(pack_dir)

for rpa in manifest.analyses:
    name = rpa.waypoint_icao or rpa.waypoint_name or f"pt{rpa.point_index}"
    print(f"Point {rpa.point_index}: {name}")
    for model, sounding in rpa.sounding.items():
        print(f"  {model}:")
        print(f"    Cloud layers: {[(c.base_ft, c.top_ft) for c in sounding.cloud_layers]}")
        print(f"    Icing zones:  {[(i.base_ft, i.top_ft, i.risk) for i in sounding.icing_zones]}")
        if sounding.vertical_motion:
            vm = sounding.vertical_motion
            print(f"    CAT: {[(c.base_ft, c.top_ft, c.risk, c.richardson_number) for c in vm.cat_risk_layers]}")
        if sounding.indices:
            print(f"    Freezing lvl: {sounding.indices.freezing_level_ft}")
```

### D. Recompute altitude advisories from a sounding

```python
from weatherbrief.analysis.sounding.advisories import compute_altitude_advisories

for rpa in manifest.analyses:
    name = rpa.waypoint_icao or rpa.waypoint_name or f"pt{rpa.point_index}"
    for model, sounding in rpa.sounding.items():
        alt_adv = compute_altitude_advisories(
            sounding=sounding,
            cruise_altitude_ft=manifest.cruise_altitude_ft,
        )
        print(f"{name} / {model}: {alt_adv}")
```

### E. Inspect cross-section data

```python
for cs in load_cross_sections(pack_dir):
    print(f"Model: {cs.model}, points: {len(cs.points)}")
    for pt in cs.points[:2]:
        print(f"  lat={pt.lat:.2f} lon={pt.lon:.2f} dist={pt.distance_km:.1f}km")
        for lvl in pt.levels[:3]:
            print(f"    {lvl.pressure_hpa}hPa: T={lvl.temperature_c}C RH={lvl.relative_humidity}%"
                  f" wind={lvl.wind_speed_kts}kt cloud={lvl.cloud_cover}")
```

### F. Re-run the pipeline on a single point's forecast

When a metric looks wrong at a specific route point (icing, clouds, SFIP, precip, vertical
motion), the pattern is always: **get the `HourlyForecast` → call `analyze_sounding()` →
inspect the result**. That re-runs the full pipeline (thermodynamics → clouds → inversions →
icing → SFIP → precip → vertical motion) and exposes every intermediate.

```python
snapshot = ForecastSnapshot.model_validate(load_forecasts(pack_dir))

# .forecasts is a flat list of WaypointForecast (one per waypoint × model)
fc = next(f for f in snapshot.forecasts
          if f.waypoint.icao == "EGTF" and f.model.value == "gfs")
hourly = fc.hourly[0]

from weatherbrief.analysis.sounding import analyze_sounding
result = analyze_sounding(hourly.pressure_levels, hourly, icing_severity_enhance=True)
```

The reference lists every attribute on `result`, the NWP inputs `hourly` carries, and the
sub-modules to call for deeper tracing.

## Step 5 — Run a single advisory evaluator in isolation

```python
from weatherbrief.analysis.advisories.registry import get_evaluator
from weatherbrief.analysis.advisories import RouteContext

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

Evaluator IDs and categories: see the reference.

## Step 6 — Compare with the GRAMET

The pack includes `gramet.pdf` — Autorouter's cross-section from the same GFS data, useful as
a broad visual sanity check of our cloud, icing and convection bands.

**First verify the GFS reference times match.** The GRAMET's "GFS RefTime" (bottom of the PDF),
`model_init_times.gfs` and `grib_init_times.gfs` must all agree, or the comparison manufactures
false discrepancies. The reference explains the check and what each colour on the PDF means.

Read the PDF with the `Read` tool (`pages: "1"`), then extract our side:

```python
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
        print(f"  Clouds: {[(c.base_ft, c.top_ft) for c in s.cloud_layers]}")
        icing = [(i.base_ft, i.top_ft, i.risk, i.icing_type) for i in s.icing_zones]
        if icing:
            print(f"  Icing:  {icing}")
        if s.convective and "NONE" not in str(s.convective.risk_level):
            print(f"  Conv: risk={s.convective.risk_level}")
        if s.nwp_cloud_diagnostics:
            for ln in ("low", "mid", "high"):
                layer = getattr(s.nwp_cloud_diagnostics, ln, None)
                if layer and layer.cover_pct and layer.cover_pct > 5:
                    print(f"  NWP-{ln}: {layer.cover_pct:.0f}% "
                          f"FL{round(layer.base_ft/100)}-FL{round(layer.top_ft/100)}")
        if s.indices and s.indices.freezing_level_ft:
            fz = s.indices.freezing_level_ft
            print(f"  Fz lvl: FL{round(fz/100)} ({fz:.0f} ft)")
    print()
```

Then produce a side-by-side comparison table using the areas and tolerances in the reference.

## Tips

- `python -m json.tool` or `jq` to pretty-print API responses.
- `forecasts.json` can be 10+ MB — use `jq` to extract specific parts rather than loading it.
- Frontend rendering entry points are listed at the end of the reference.
