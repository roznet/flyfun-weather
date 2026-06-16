# Route Advisory System

> Deterministic evaluation of weather hazards along a flight route with per-model severity assessments

## Intent

Provide actionable, severity-graded (GREEN/AMBER/RED) advisories from 20 hazard evaluators (grouped into icing, cloud, precipitation, turbulence, convective, wind, model, airport, feasibility, fronts, and sun categories) along the route. Evaluators analyze existing route analysis data — no additional data fetch. User-tunable parameters allow recalculation without re-running the pipeline. This is a **route-level** system (advisory per route), complementing the per-waypoint `AltitudeAdvisories` in the sounding subpackage.

## Architecture

```
RouteContext (immutable)
  ├── analyses: list[RoutePointAnalysis]   (~20 points along route)
  ├── cross_sections: list[RouteCrossSection]  (per-model forecast grids)
  ├── elevation: ElevationProfile | None
  ├── airport_conditions: AirportConditions | None  (dep + arr weather)
  ├── sun: RouteSunAnalysis | None  ├── route_fronts: RouteFrontsManifest | None
  ├── cruise_speed_ias_kt, flight_duration_hours  (headwind trip-time inputs)
  ├── models, cruise_altitude_ft, flight_ceiling_ft, total_distance_nm, locale
      ↓
Registry → evaluate_all(ctx, enabled_ids?, user_params?, aggregation?)
  ├── @register IcingEscapeEvaluator       # en-route icing
  ├── @register FIKIIcingEvaluator
  ├── @register FreezingPrecipEvaluator
  ├── @register CloudTopEvaluator          # en-route cloud
  ├── @register VMCCruiseEvaluator
  ├── @register EnroutePrecipEvaluator     # precipitation (en-route visibility proxy)
  ├── @register TurbulenceEvaluator        # en-route turbulence
  ├── @register MountainWindEvaluator
  ├── @register ConvectiveEvaluator        # convective
  ├── @register HeadwindEvaluator          # wind (trip impact)
  ├── @register ModelAgreementEvaluator    # model quality (cross-model)
  ├── @register DDvsNWPAgreementEvaluator  # model quality (within-model)
  ├── @register FlightCategoryEvaluator    # airport conditions (+ terminal convective)
  ├── @register AirportWindEvaluator
  ├── @register DensityAltitudeEvaluator
  ├── @register LLWSEvaluator
  ├── @register VFRFeasibilityEvaluator    # composite go/no-go
  ├── @register IFRFeasibilityEvaluator
  ├── @register FrontsEvaluator            # fronts (experimental, gated on artifact)
  └── @register SunEvaluator               # sun (glare + night-proximity + seating note)
      ↓
RouteAdvisoriesManifest (advisories + catalog + aggregation mode)
  → route_advisories.json
```

**All files in `src/weatherbrief/analysis/advisories/`.**

## Evaluator Protocol

Every evaluator implements two static methods — no inheritance, no base class:

```python
class MyEvaluator:
    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        # Metadata: id, name, description, category, parameters, default_enabled

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        # Per-model iteration → aggregate
```

The `@register` decorator auto-discovers evaluators at import time. New evaluators: create file, implement protocol, add `@register` — no central config changes needed.

## Three-Level Aggregation

Every evaluator follows the same pattern:

1. **Point level**: Iterate route points, count affected vs total per model
2. **Model level**: `ModelAdvisoryResult.build(status, detail, affected, total, total_distance_nm)` — computes percentage and distance metrics
3. **Route level**: `RouteAdvisoryResult.from_per_model(id, per_model, params, aggregation=)` — aggregates per-model statuses using the chosen mode

Aggregation is controlled by `AdvisoryAggregation` enum (`WORST` or `MAJORITY`). The registry passes the aggregation mode; individual evaluators always produce per-model results without knowing the aggregation strategy.

Detail text comes from the worst-performing model. Shared classmethods on the models eliminated ~115 lines of boilerplate.

**Localized detail text** (`strings.py`): evaluators build detail strings via `adv_t(key, locale, **params)` against the `_STRINGS` catalog (en/fr/de/es), keyed `<evaluator>.<msg>`. Aviation abbreviations (VFR, OVC, BKN, FL, kt, ICAO codes…) are never translated. `ctx.locale` flows in from the flight profile and is honored on recalc.

### Aggregation Modes

- **WORST**: If ANY model shows RED, the aggregate is RED. Conservative — pilots see the worst-case scenario.
- **MAJORITY** (default, changed from WORST): The most common status across models wins. Ties broken by worst status among the tied group. Example: 2 AMBER, 2 GREEN, 1 RED → tie between AMBER and GREEN → worst of tied = AMBER. Changed to default because WORST mode was too noisy — a single outlier model could make the whole route RED.

`AdvisoryStatus.majority(statuses)` implements the majority logic: count each status (ignoring UNAVAILABLE), find max count, return worst among tied leaders. The registry re-aggregates after each evaluator returns if mode isn't WORST, so evaluator code is unchanged.

## The 20 Evaluators

### Icing

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `IcingEscapeEvaluator` | icing | Non-FIKI: can we descend below freezing to escape icing? Checks FZ level vs terrain + margin. Altitude-aware: ignores icing above cruise + buffer. Any no-escape point → amber; `no_escape_pct_red` escalates to red. `icing_coverage_pct_amber` warns when escapable icing covers a significant portion of the route | `terrain_margin_ft`, `tight_margin_ft`, `icing_altitude_buffer_ft`, `icing_coverage_pct_amber` (20%), `no_escape_pct_red` (15%) |
| `FIKIIcingEvaluator` | icing | FIKI-equipped: evaluates icing layer thickness and severity (transit OK, loiter not) | `thickness_amber_ft`, `thickness_red_ft`, `severe_is_red` |
| `FreezingPrecipEvaluator` | icing | Freezing rain / ice pellets: active FZRA/PL surface phase anywhere → RED (exceeds all icing certification, incl. FIKI; below-cloud hazard invisible to in-cloud icing methods). Freezing-rain-shaped profile without active precip (warm nose over sub-zero surface, via `detect_warm_nose`) → AMBER above coverage threshold | `primed_pct_amber` (5%) |

### Cloud

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `CloudTopEvaluator` | cloud | Can we fly above the clouds? Only considers layers pilot would enter (base ≤ ceiling), ignores high cirrus | `margin_ft`, `pct_amber` |
| `VMCCruiseEvaluator` | cloud | Cloud coverage at cruise altitude specifically (BKN/OVC percentage along route) | `bkn_pct_amber`, `ovc_pct_red` |

### Precipitation

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `EnroutePrecipEvaluator` | precipitation | Precipitation along the route as the en-route **visibility proxy** (no model forecasts visibility at altitude; surface phase/intensity from `PrecipitationAssessment` works for all 7 models). Snow is the VFR killer: any snow ≥ amber threshold → AMBER, widespread moderate+ snow → RED. Moderate+ rain → AMBER (capped — rain degrades, rarely prohibits). FZRA/PL count toward extent only; severity owned by `freezing_precip`. Shared classifier (`classify_enroute_precip`) feeds the VFR composite capped at AMBER | `snow_pct_amber` (5%), `snow_moderate_pct_red` (25%), `rain_pct_amber` (30%) |

### Turbulence

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `TurbulenceEvaluator` | turbulence | CAT layers at cruise + strong vertical motion. SEVERE CAT anywhere → RED | `route_pct_amber`, `strong_w_fpm` |
| `MountainWindEvaluator` | turbulence | Wind speed near significant terrain corroborated by wave signatures: an inversion overlapping ridge top (−1000/+4000ft band) or an OSCILLATING vertical-motion classification. Signature present → RED bar drops from `wind_red_kt` to `corroborated_red_kt` ("rotor day" vs "windy ridge"). Cross-ridge direction deliberately NOT assessed (1-D terrain profile — ridge orientation unknown). Only evaluates where terrain > threshold | `terrain_threshold_ft`, `altitude_margin_ft`, `wind_amber_kt`, `wind_red_kt` (40), `corroborated_red_kt` (30) |

### Other

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `ConvectiveEvaluator` | convective | Route points with convective risk ≥ threshold. Altitude-aware: ignores convection whose tops are below `cruise_ft - top_clearance_ft`. HIGH/EXTREME → instant RED. LOW risk capped at AMBER (prevents false alarms for marginal instability) | `min_risk`, `affected_pct_amber`, `affected_pct_red`, `top_clearance_ft` (2000) |
| `HeadwindEvaluator` | wind | Route-average cruise-level headwind + trip-time delta vs still air. TAS is derived from `ctx.cruise_speed_ias_kt` (aircraft/profile cruise IAS via `atmo.resolve_cruise_speed_ias`, converted to TAS at cruise altitude), falling back to `total_distance_nm / flight_duration_hours` — both ride on `RouteContext` so recalc-from-pack still works. Altitude-aware (cross-section winds at the evaluated altitude → the altitude table shows the wind trade per level; falls back to precomputed `wind_components`). Informational bias: AMBER on mean ≥ 20kt, RED only ≥ 40kt; tailwinds report minutes saved | `mean_amber_kt` (20), `mean_red_kt` (40) |
| `ModelAgreementEvaluator` | model | Cross-model divergence (POOR/MODERATE agreement). Evaluated once, not per-model. **Disabled by default** — user must enable via profile | `min_poor_vars` (3), `poor_pct_amber`, `poor_pct_red` |
| `DDvsNWPAgreementEvaluator` | model | Within-model agreement: compares DD (thermodynamic) vs NWP tracks per model at each route point. Checks freezing-level delta, cloud layer Jaccard (only when NWP has real boundaries — `source="nwp_3d"` or `"grib"`, never `"synthesized"` which is circular), and convective risk category distance. Surfaces e.g. mid-level decks GFS sees that DD misses, or ECMWF cirrus ice-supersaturation that `cc` rejects. **Disabled by default** — dev/calibration signal, not pilot-facing | `freezing_delta_ft` (2000), `cloud_overlap_min` (30%), `amber_pct` (30), `red_pct` (60) |

### Airport

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `FlightCategoryEvaluator` | airport | Ceiling/visibility at departure + arrival (OR logic: either metric below threshold triggers; defaults match MVFR/IFR boundaries) PLUS terminal-area convective risk within `conv_radius_nm` of each end with **no coverage dilution** (a deviation is not an option on climb-out/approach): MODERATE → AMBER, HIGH/EXTREME → RED, no altitude filter | `amber_ceiling_ft` (3000), `amber_vis_sm` (5), `red_ceiling_ft` (1000), `red_vis_sm` (3), `conv_radius_nm` (25) |
| `AirportWindEvaluator` | airport | Crosswind on best runway + gust severity at departure + arrival. Worst of dep/arr becomes the status | `xwind_green_kt`, `xwind_red_kt`, `gust_green_kt`, `gust_red_kt` |
| `DensityAltitudeEvaluator` | airport | Density altitude at departure + arrival from forecast T/QNH at the expected times + field elevation (terrain profile). Triggers on absolute DA OR DA-above-field (hot-day performance loss). UNAVAILABLE without temperature/terrain — never green-by-absence | `da_amber_ft` (5000), `da_red_ft` (8000), `delta_amber_ft` (3000), `delta_red_ft` (5000) |
| `LLWSEvaluator` | airport | Low-level wind shear at departure + arrival: 0–1km bulk shear from the airport-point sounding (catches the nocturnal LLJ AirportWind can't see) OR gust factor (gust − sustained). Surface-based inversion reported alongside significant shear. Worst of dep/arr | `shear_amber_kt` (20), `shear_red_kt` (30), `gust_factor_amber_kt` (15) |

### Feasibility (Composite)

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `VFRFeasibilityEvaluator` | feasibility | Composite VFR go/no-go combining: airport flight category, en-route cloud clearance (base vs cruise), VMC compliance (BKN/OVC percentage), climb-out/descent corridor decks (BKN/OVC fully below cruise within `terminal_corridor_nm` of either end — OVC→RED, BKN→AMBER, see meteorology-decisions §10), and en-route precipitation (shared `classify_enroute_precip`, capped at AMBER in the composite). Worst of sub-assessments wins | `cloud_clearance_ft`, `imc_pct_amber`, `imc_pct_red`, `terminal_corridor_nm` (5) |
| `IFRFeasibilityEvaluator` | feasibility | Composite IFR go/no-go combining: airport IFR viability (LIFR→amber, below minimums→red), en-route icing exposure (uses shared `has_relevant_icing()` helper aligned with FIKI advisory), convective risk along route | `min_dep_ceiling_ft`, `min_arr_ceiling_ft`, `icing_pct_amber`, `icing_pct_red`, `icing_altitude_buffer_ft` |

### Sun

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `SunEvaluator` | sun | Informational, never go/no-go. Thin classifier over the precomputed `RouteSunAnalysis` on `ctx.sun` (built by `analysis/sun.py:compute_route_sun`). Model-independent → single `per_model=["all"]` like `ModelAgreementEvaluator`. AMBER when a low sun sits roughly down the wind-best runway on takeoff/landing (glare, recomputed from stored geometry so params honour recalc), and — for day-VFR profiles — when the leg ends near/after sunset or starts near/before sunrise (gated by `warn_near_sunset`; glare AMBER always applies). Detail text always carries the sun-side seating note. UNAVAILABLE (per-model) on old packs / when `ctx.sun is None` | `glare_azimuth_deg` (30), `glare_elev_max_deg` (15), `warn_near_sunset` (true), `sunset_margin_min` (30) |

**Sun pipeline (issue #227):** the heavy/airport-independent parts (night intervals + sun-side summary) are computed once in `tasks/analyze.py` and stored on `RouteAnalysesManifest.sun` (served to the client for cross-section night shading). The advise stage recomputes the full `RouteSunAnalysis` — adding dep/arr glare from the wind-best runway (shared `select_best_runway` helper in `airport_conditions.py`) — onto `RouteContext.sun`, where `run_advisories_from_pack` also rebuilds it so recalculation works. Solar math lives in the `euro_aip` solar primitive (`euro_aip/utils/solar.py`, astral-backed); azimuths and runway headings are both **true** degrees, so glare needs no magnetic conversion.

## Shared Helpers (`_helpers.py`)

- **`format_extent(affected, total, total_distance_nm)`** → `"30nm/55nm (55%)"` — human-readable spatial extent
- **`icing_zones_in_altitude_range(zones, floor_ft, ceiling_ft)`** → filter zones overlapping an altitude band
- **`has_relevant_icing(zones, cruise_altitude_ft, buffer_ft=2000)`** → True if any zone overlaps `[0, cruise + buffer]`. Used by IFR feasibility, icing escape, and FIKI evaluators to ignore icing far above cruise altitude
- **`min_icing_clearance(zones, cruise_altitude_ft)`** → minimum vertical distance (ft) from cruise to nearest icing zone. Used by FIKI evaluator
- **`pct_above_threshold(affected, total, amber_pct, red_pct)`** → common GREEN/AMBER/RED from percentage
- **`terrain_at_distance(elevation, distance_nm)`** → binary search + linear interpolation for terrain altitude
- **`max_terrain_near_point(elevation, distance_nm, radius_nm=5)`** → peak elevation within radius
- **`wind_at_altitude(cross_sections, model, point_index, target_alt_ft, target_time)`** → wind at the pressure level nearest a target altitude, for the hourly forecast nearest `target_time` (not the first hour — matters on multi-hour legs). Delegates the level pick to `analysis/wind.py:pick_wind_at_pressure`

## User Parameters

Each evaluator declares parameters with `AdvisoryParameterDef` (key, label, type, unit, default, min, max, step). At evaluation time:

```python
params = {**defaults_from_catalog, **user_overrides}
result = evaluator.evaluate(ctx, params)
```

User overrides stored in `flight_profiles.settings_json` under `advisories: {enabled: {id: bool}, params: {id: {key: val}}, aggregation: "worst"|"majority"}`. Recalculation endpoint loads the flight's profile settings (including aggregation mode), re-evaluates without re-fetching weather data.

## Pipeline Integration

In `tasks/advise.py` via `run_advisories()`:
1. **Method resolution**: `_resolve_analyses(rp_analyses, icing_method, cloud_method, convective_method)` returns new objects with the user's preferred icing/cloud/convective method resolved into the active `icing_zones`/`cloud_layers`/`convective` slots via `model_copy()` — originals are never mutated. Returns the original list unchanged when no swap is needed. See [analysis.md](./analysis.md) for method details.
2. **Model filtering**: `advisory_models` preference selects which models to evaluate (default excludes `best_match`)
3. Build `RouteContext` from existing route analyses, cross-sections, elevation, airport conditions
4. Call `evaluate_all(ctx)` → `list[RouteAdvisoryResult]`
5. Save `RouteAdvisoriesManifest` to `route_advisories.json`

Also supports `run_advisories_from_pack()` for re-evaluation from saved artifacts without re-fetching.

**Altitude table** (`analysis/advisories/altitude_table.py` → `compute_altitude_table`, driven by `run_altitude_table_from_pack`): sweeps the *altitude-dependent* evaluators (`get_altitude_dependent_ids()`, derived from each catalog entry's `altitude_dependent` flag) across an altitude range at `step_ft` intervals, returning an `AltitudeTableResult` with per-altitude advisory rows plus `best_below_cruise`/`best_above_cruise` picks. Pure analysis module — does NOT import from `tasks/`. `diff_altitude_rows()` (with `row_for_altitude`) is the canonical altitude-diff primitive — "which advisories improve/worsen at altitude X vs the planned row" — shared by the digest prompt builder (the deterministic `=== ALTITUDE OPTIONS ===` block; see [digest.md](./digest.md)) and mirrored by a client TypeScript twin (`web/ts/helpers/altitude-diff.ts`). The table is **precomputed at briefing time and fed into the digest input** so the LLM phrases the altitude trade-off without inventing numbers (#259).

**Alt departure** (`run_alt_from_pack` + `derive_assessment_from_advisories`): re-runs analysis + advisories against the already-fetched forecast at a flight's `alt_departure_time`, writing `route_advisories_alt.json` and deriving an overall GREEN/AMBER/RED assessment (worst aggregate status) for the alt scenario.

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `.../packs/{ts}/advisories` | GET | Return saved advisories JSON |
| `.../packs/{ts}/advisories/recalculate` | POST | Re-evaluate with user prefs (enabled IDs + param overrides) |
| `.../packs/{ts}/advisories/altitude-table` | POST | Altitude sweep (`step_ft` 500–5000, default 2000) → `AltitudeTableResult` |
| `.../packs/{ts}/advisories/alt` | GET | Return saved alt-departure advisories JSON |
| `.../packs/{ts}/advisories/alt/compute` | POST | Compute alt-departure advisories on-demand (requires flight `alt_departure_time`) |
| `.../advisories/catalog` | GET | Catalog of all evaluators + parameter defs (on the preferences router) |

Recalculate loads route analyses + elevation + cross-sections from disk, applies user preferences, returns fresh manifest. Recalculate and altitude-table share `_load_advisory_profile()` to resolve enabled IDs, param overrides, aggregation, advisory models, icing/cloud/convective methods, and locale.

## Frontend

**Dashboard** (`managers/advisories-ui.ts`):
- Summary bar: badge counts per severity (e.g., "3 RED 2 AMBER 5 GREEN")
- Advisory cards sorted RED → AMBER → GREEN → UNAVAILABLE
- Each card: aggregate status badge + name + info button + per-model badges + detail text
- **Altitude slider**: re-evaluates advisories at different cruise altitudes without recalculating the full pipeline (altitude-dependent evaluators only)
- **Altitude table button**: opens a popup rendering the `/advisories/altitude-table` sweep — per-altitude advisory grid with best-below/best-above-cruise picks
- **Profile selector** (`ProfileSelectorConfig`, owner-only): dropdown to switch the flight's advisory profile, re-running advisories with that profile's enabled/params/aggregation
- **Alt-time toggle**: switches the displayed advisories between the planned departure and `alt_departure_time` (`routeAdvisories` vs `altAdvisories`)
- **Advisory chips** (`handleAdvisoryChip`): clickable per-advisory chips that cross-link to the relevant map/cross-section context
- Recalculate button triggers POST endpoint and re-renders. The top-level entry point is `renderAdvisories(...)` in `briefing-main.ts`, fed by `getEffectiveAdvisories(state)`

**Info popup** (`components/info-popup.ts`):
- Full description, category, parameter table with values used
- Reuses shared modal infrastructure from metrics info popups

**Store**: `briefingStore.routeAdvisories` + `recalculateAdvisories()` action.

## Key Choices

- **Protocol over inheritance** — evaluators are peer classes, no hierarchy. Easier to test and extend.
- **Immutable RouteContext** — frozen dataclass prevents accidental mutation across evaluators.
- **Configurable aggregation** — MAJORITY (default, most common status, ties→worst) or WORST (conservative). Set per-profile in `advisories.aggregation`.
- **Lazy evaluation** — advisories evaluated fresh each time, not cached. Enables fast parameter tuning.
- **Altitude-aware icing** — IFR feasibility, icing escape, and FIKI all filter icing by altitude relevance. Icing above `cruise_altitude_ft + buffer` (default 2000ft) is ignored. Prevents false alerts from high-altitude icing a pilot will never encounter. Buffer is tunable per evaluator via `icing_altitude_buffer_ft`.
- **Cloud top filtering** — only considers layers pilot would enter (base ≤ ceiling). High cirrus above ceiling is irrelevant.
- **Mountain wind: GREEN not UNAVAILABLE** — if no mountains on route, "no hazard" is better UX than "N/A".
- **Model agreement evaluated once** — inherently cross-model, returned as single "all" model result.
- **Parameters as JSON, not schema** — no DB migration needed to add new evaluator parameters.

## Gotchas

- Evaluator exceptions are caught and logged — one failure doesn't break the whole advisory set
- `ModelAgreementEvaluator` has `per_model=["all"]` (not actual model names) since it's cross-model
- `format_extent` falls back to percentage-only if route has too few points for meaningful distance
- `wind_at_altitude` picks the level via `pick_wind_at_pressure` and the hour via `at_time` — don't reintroduce a "first hourly" shortcut, it lags the route point's valid time on long legs

## References

- Data models: [data-models.md](./data-models.md) (RouteAdvisoryResult, AdvisoryCatalogEntry, etc.)
- Analysis layer: [analysis.md](./analysis.md) (sounding analysis that feeds evaluators)
- Architecture: [architecture.md](./architecture.md) (pipeline, API endpoints)
- Per-waypoint advisories: `analysis/sounding/advisories.py` (different system — vertical regimes)
