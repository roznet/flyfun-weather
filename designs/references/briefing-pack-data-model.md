# Briefing pack data model reference

Static facts about briefing packs: what a flight ID encodes, what a pack directory contains,
the advisory evaluator catalog, the read-only API surface, and the field-name traps that cause
most runtime errors when loading pack data by hand.

Companion to the `investigateflight` skill (`.claude/skills/investigateflight/SKILL.md`),
which holds the procedural recipes. Look things up here; run the recipes there.

## Flight ID format

`{safe_route_name}-{target_date}-{params_hash}` — e.g. `egtf_lflx_lfmd-2026-02-27-1a52`.

- `safe_route_name` — lowercased waypoints joined by `_` (e.g. `egtf_lflx_lfmd`)
- `target_date` — `YYYY-MM-DD`
- `params_hash` — 4-char hex hash of `{time, alt, ceil, dur}`; see
  `src/weatherbrief/api/flights.py`

It appears in briefing URLs as `briefing.html?flight={flight_id}`, on both localhost and
production.

## Pack directory layout

Locally: `{DATA_DIR}/packs/{user_id}/{flight_id}/{timestamp}/`. On a server the same shape
sits under the deployment's `HOST_DATA_DIR` — resolve it from the server's `.env` rather than
assuming a path, since pack data does not live under the project directory.

The timestamp directory name uses filesystem-safe characters: colons → dashes, `+` → `p`.
Example: `2026-02-25T13-23-00.013596p00-00`.

### Pack contents

| File | Contents |
|------|----------|
| `briefing.json` | Route + analyses + observations + metadata (no raw forecasts) |
| `forecasts.json` | Route + metadata + raw forecasts only (large — 10+ MB) |
| `cross_section.json` | `RouteCrossSection[]` — interpolated vertical slices for visualization |
| `route_analyses.json` | `RouteAnalysesManifest` — sounding analysis per waypoint & route point |
| `elevation_profile.json` | `ElevationProfile` — SRTM terrain along route |
| `route_advisories.json` | `RouteAdvisoriesManifest` — all 13 advisory evaluator results |
| `route_points.json` | `RoutePoint[]` — interpolated waypoints with lat/lon/distance |
| `fetch_meta.json` | Metadata: `fetched_at`, `models_fetched` |
| `gramet.pdf` | GRAMET cross-section image (Autorouter) |
| `skewt/*.png` | Skew-T diagrams per waypoint/model |
| `digest.md` / `digest.json` | LLM-generated weather digest |

**Legacy note:** old packs may carry a single `snapshot.json` instead of the split files. The
`load_briefing()` / `load_forecasts()` helpers fall back automatically.

`route_advisories.json` top-level keys: `advisories` (list), `catalog`, `route_name`,
`cruise_altitude_ft`, `flight_ceiling_ft`, `total_distance_nm`, `models`, `aggregation`,
`airport_conditions`. Each entry in `advisories` has `advisory_id`, `aggregate_status`,
`aggregate_detail`, `per_model`, `parameters_used`.

## Advisory evaluators (13)

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

## Read-only API surface

For quick inspection when a server is running — not the main debugging path, which is
disk + Python. Pattern: `/api/flights/{flight_id}/packs/{timestamp}/{resource}`, with `latest`
for the most recent pack.

| Suffix | Returns |
|--------|---------|
| `/snapshot` | Raw forecast JSON |
| `/route-analyses` | `RouteAnalysesManifest` |
| `/advisories` | `RouteAdvisoriesManifest` |
| `/advisories/recalculate` (POST) | Re-evaluate with custom params |
| `/elevation` | `ElevationProfile` |
| `/skewt/{icao}/{model}` | Skew-T PNG |
| `/digest/json` | Structured LLM digest |

## Analysis result structure

`analyze_sounding()` returns an object whose parts map to pipeline stages:

| Attribute | Type |
|---|---|
| `cloud_layers` | `EnhancedCloudLayer[]` |
| `icing_zones` | `IcingZone[]` (Ogimet index) |
| `sfip_zones` | `SFIPZone[]` (fuzzy-logic) |
| `inversion_layers` | `InversionLayer[]` |
| `precipitation` | `PrecipitationAssessment` |
| `vertical_motion` | `VerticalMotionAssessment` (CAT layers) |
| `convective` | `ConvectiveAssessment` |
| `indices` | `ThermodynamicIndices` (CAPE, freezing level, …) |
| `derived_levels` | per-level `DerivedLevel` with all computed fields |
| `nwp_cloud_diagnostics` | `NWPCloudDiagnostics` (GFS per-layer base/top) |

The `HourlyForecast` that feeds it also carries the NWP inputs directly:
`pressure_levels` (the raw input), `nwp_cloud_diagnostics` (`.low`/`.mid`/`.high`, each with
`cover_pct`, `base_ft`, `top_ft`, `top_temp_c`), and
`cloud_cover_low_pct` / `_mid_pct` / `_high_pct`.

Sub-modules live in `weatherbrief.analysis.sounding.*` — `clouds`, `icing`, `sfip`,
`precipitation`, `vertical_motion`, `inversions`. Each takes `derived_levels` plus relevant
context from `hourly`.

## Destructive default — read before running anything with a `pack_dir`

**`run_advisories_from_pack` defaults to `persist=True`, which overwrites
`route_advisories.json` in the pack you are investigating.** Combined with `enabled_ids` it
writes back a manifest containing *only* those evaluators, silently discarding the rest.

The default is correct for its production caller (the API recalculate endpoint, where the pack
is *meant* to track the user's current advisory selection) and wrong for every debugging use.
`eval_workbench/rerun.py` passes `persist=False` for the same reason: a "what changed"
comparison must never clobber the baseline it compares against.

The same caution applies to any helper taking a `pack_dir` — check whether it writes before
running it on a real pack. Copy the pack to a scratch directory first if unsure.

## Field-name and nullability traps

The destructive default above silently *loses data*; everything below merely causes runtime
errors, but they are the errors that actually happen.

- **`AdvisoryResult` is not the manifest** — use `result.manifest.advisories`, not
  `result.advisories`.
- **`PressureLevelData.wind_speed_kt`** — NOT `wind_speed_kts` (no trailing `s`).
- **`PressureLevelData.vertical_velocity_pa_s`** — NOT `omega_pa_s`.
- **`HourlyForecast.pressure_levels`** — NOT `levels`.
- **`ForecastSnapshot.forecasts`** is a flat `list[WaypointForecast]`, NOT
  `.waypoints[i].forecasts[model]`. Each `WaypointForecast` has `.waypoint`, `.model`, `.hourly`.
- **`WaypointForecast.model` is a `ModelSource` enum** — use `.value` for string comparison
  (e.g. `fc.model.value == "gfs"`).
- **`rpa.sounding`**, not `.soundings` — a dict mapping model name (str) to `SoundingAnalysis`.
- **`RouteAnalysesManifest` has no `flight_ceiling_ft`** — only `cruise_altitude_ft`.
- **Advisory JSON** — each item is keyed by `advisory_id`, NOT `evaluator_id`.
- **Many numeric fields can be `None`** — guard before formatting:
  `f"{val:.1f}" if val is not None else "N/A"`. Common offenders on `DerivedLevel`:
  `max_w_fpm`, `max_omega_pa_s`, `richardson_number`, `bv_freq_squared_per_s2`, `w_fpm`,
  `omega_pa_s`.

## GRAMET comparison

The pack's `gramet.pdf` is the Autorouter cross-section built from the same GFS data, useful
as a broad visual sanity check.

**Verify the GFS reference times match first — this is mandatory.** Three times must agree:

1. **GRAMET GFS RefTime** — printed at the bottom of the PDF (e.g. "GFS RefTime 2026-02-27 18:00Z")
2. **Our Open-Meteo GFS init** — `model_init_times.gfs` in pack metadata (unix timestamp)
3. **Our GRIB GFS init** — `grib_init_times.gfs` in pack metadata (unix timestamp, if fetched)

If any differ, **stop** — different runs produce different forecasts, so the comparison would
manufacture false discrepancies that look like bugs.

What the PDF shows: white cloud masses (coverage by altitude), cyan/turquoise dashed contours
(icing), brown dashed isotherms, a magenta freezing level, grey/brown terrain along the bottom,
wind barbs at grid points, and waypoint labels on the x-axis with distance (nm) and time (UTC).
The y-axis shows FL on the left and hPa on the right.

Comparison areas:

| Area | GRAMET (visual) | Our analysis |
|------|----------------|--------------|
| **Freezing level** | Magenta line / 0 °C isotherm | `indices.freezing_level_ft` |
| **Cloud layers** | White masses at key distances | `cloud_layers` base/top + `nwp_cloud_diagnostics` low/mid/high |
| **Icing** | Cyan contour positions | `icing_zones` base/top/risk |
| **Convection** | CB symbols / convective markers | `convective.risk_level` |

Both use the same GFS data, so broad patterns should match. Differences of ~500–1000 ft in
altitude or ~20 nm along route are normal given visual reading precision. Flag only
significant discrepancies — e.g. our analysis missing a cloud layer the GRAMET clearly shows,
or icing zones at very different altitudes.

## Frontend rendering entry points

For visualization debugging:

- `web/ts/visualization/cross-section/renderer.ts` — cross-section renderer
- `web/ts/visualization/cross-section/layers.ts` — layer definitions
- `web/ts/visualization/data-extract.ts` — JSON → canvas pipeline
- `web/ts/visualization/route-map/renderer.ts` — route map coloured segments
