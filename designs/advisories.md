# Route Advisory System

> Deterministic evaluation of weather hazards along a flight route with per-model severity assessments

## Intent

Provide actionable, severity-graded (GREEN/AMBER/RED) advisories for 13 weather hazard categories along the route. Evaluators analyze existing route analysis data — no additional data fetch. User-tunable parameters allow recalculation without re-running the pipeline. This is a **route-level** system (advisory per route), complementing the per-waypoint `AltitudeAdvisories` in the sounding subpackage.

## Architecture

```
RouteContext (immutable)
  ├── analyses: list[RoutePointAnalysis]   (~20 points along route)
  ├── cross_sections: list[RouteCrossSection]  (per-model forecast grids)
  ├── elevation: ElevationProfile | None
  ├── airport_conditions: AirportConditions | None  (dep + arr weather)
  ├── models, cruise_altitude_ft, flight_ceiling_ft, total_distance_nm
      ↓
Registry → evaluate_all(ctx, enabled_ids?, user_params?, aggregation?)
  ├── @register IcingEscapeEvaluator       # en-route icing
  ├── @register FIKIIcingEvaluator
  ├── @register FreezingLevelEvaluator
  ├── @register CloudTopEvaluator          # en-route cloud
  ├── @register VMCCruiseEvaluator
  ├── @register TurbulenceEvaluator        # en-route turbulence
  ├── @register MountainWindEvaluator
  ├── @register ConvectiveEvaluator        # convective
  ├── @register ModelAgreementEvaluator    # model quality
  ├── @register FlightCategoryEvaluator    # airport conditions
  ├── @register AirportWindEvaluator
  ├── @register VFRFeasibilityEvaluator    # composite go/no-go
  └── @register IFRFeasibilityEvaluator
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

### Aggregation Modes

- **WORST**: If ANY model shows RED, the aggregate is RED. Conservative — pilots see the worst-case scenario.
- **MAJORITY** (default, changed from WORST): The most common status across models wins. Ties broken by worst status among the tied group. Example: 2 AMBER, 2 GREEN, 1 RED → tie between AMBER and GREEN → worst of tied = AMBER. Changed to default because WORST mode was too noisy — a single outlier model could make the whole route RED.

`AdvisoryStatus.majority(statuses)` implements the majority logic: count each status (ignoring UNAVAILABLE), find max count, return worst among tied leaders. The registry re-aggregates after each evaluator returns if mode isn't WORST, so evaluator code is unchanged.

## The 13 Evaluators

### Icing

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `IcingEscapeEvaluator` | icing | Non-FIKI: can we descend below freezing to escape icing? Checks FZ level vs terrain + margin. Altitude-aware: ignores icing above cruise + buffer. `min_route_pct` suppresses alerts when only a tiny fraction of route is affected | `terrain_margin_ft`, `tight_margin_ft`, `icing_altitude_buffer_ft`, `route_pct_amber`, `min_route_pct` (15%) |
| `FIKIIcingEvaluator` | icing | FIKI-equipped: evaluates icing layer thickness and severity (transit OK, loiter not) | `thickness_amber_ft`, `thickness_red_ft`, `severe_is_red` |
| `FreezingLevelEvaluator` | icing | Freezing level vs max terrain height (mountain icing risk). `min_route_pct` suppresses alerts when only isolated points are affected | `margin_ft`, `tight_margin_ft`, `min_route_pct` (15%) |

### Cloud

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `CloudTopEvaluator` | cloud | Can we fly above the clouds? Only considers layers pilot would enter (base ≤ ceiling), ignores high cirrus | `margin_ft`, `pct_amber` |
| `VMCCruiseEvaluator` | cloud | Cloud coverage at cruise altitude specifically (BKN/OVC percentage along route) | `bkn_pct_amber`, `ovc_pct_red` |

### Turbulence

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `TurbulenceEvaluator` | turbulence | CAT layers at cruise + strong vertical motion. SEVERE CAT anywhere → RED | `route_pct_amber`, `strong_w_fpm` |
| `MountainWindEvaluator` | turbulence | Wind speed near significant terrain (orographic/rotor risk). Only evaluates where terrain > threshold | `terrain_threshold_ft`, `altitude_margin_ft`, `wind_amber_kt`, `wind_red_kt` |

### Other

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `ConvectiveEvaluator` | convective | Route points with convective risk ≥ threshold. Altitude-aware: ignores convection whose tops are below `cruise_ft - top_clearance_ft`. HIGH/EXTREME → instant RED. LOW risk capped at AMBER (prevents false alarms for marginal instability) | `min_risk`, `affected_pct_amber`, `affected_pct_red`, `top_clearance_ft` (2000) |
| `ModelAgreementEvaluator` | model | Cross-model divergence (POOR/MODERATE agreement). Evaluated once, not per-model. **Disabled by default** — user must enable via profile | `min_poor_vars` (3), `poor_pct_amber`, `poor_pct_red` |

### Airport

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `FlightCategoryEvaluator` | airport | Ceiling/visibility at departure + arrival. OR logic: either metric below threshold triggers. Defaults match MVFR/IFR boundaries | `amber_ceiling_ft` (3000), `amber_vis_sm` (5), `red_ceiling_ft` (1000), `red_vis_sm` (3) |
| `AirportWindEvaluator` | airport | Crosswind on best runway + gust severity at departure + arrival. Worst of dep/arr becomes the status | `xwind_green_kt`, `xwind_red_kt`, `gust_green_kt`, `gust_red_kt` |

### Feasibility (Composite)

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `VFRFeasibilityEvaluator` | feasibility | Composite VFR go/no-go combining: airport flight category, en-route cloud clearance (base vs cruise), VMC compliance (BKN/OVC percentage). Worst of sub-assessments wins | `cloud_base_margin_ft`, `bkn_pct_amber`, `ovc_pct_red` |
| `IFRFeasibilityEvaluator` | feasibility | Composite IFR go/no-go combining: airport IFR viability (LIFR→amber, below minimums→red), en-route icing exposure (uses shared `has_relevant_icing()` helper aligned with FIKI advisory), convective risk along route | `min_dep_ceiling_ft`, `min_arr_ceiling_ft`, `icing_pct_amber`, `icing_pct_red`, `icing_altitude_buffer_ft` |

## Shared Helpers (`_helpers.py`)

- **`format_extent(affected, total, total_distance_nm)`** → `"30nm/55nm (55%)"` — human-readable spatial extent
- **`icing_zones_in_altitude_range(zones, floor_ft, ceiling_ft)`** → filter zones overlapping an altitude band
- **`has_relevant_icing(zones, cruise_altitude_ft, buffer_ft=2000)`** → True if any zone overlaps `[0, cruise + buffer]`. Used by IFR feasibility, icing escape, and FIKI evaluators to ignore icing far above cruise altitude
- **`min_icing_clearance(zones, cruise_altitude_ft)`** → minimum vertical distance (ft) from cruise to nearest icing zone. Used by FIKI evaluator
- **`pct_above_threshold(affected, total, amber_pct, red_pct)`** → common GREEN/AMBER/RED from percentage
- **`terrain_at_distance(elevation, distance_nm)`** → binary search + linear interpolation for terrain altitude
- **`max_terrain_near_point(elevation, distance_nm, radius_nm=5)`** → peak elevation within radius
- **`wind_at_altitude(cross_sections, model, point_index, target_alt_ft)`** → extract wind at specific altitude from cross-section data

## User Parameters

Each evaluator declares parameters with `AdvisoryParameterDef` (key, label, type, unit, default, min, max, step). At evaluation time:

```python
params = {**defaults_from_catalog, **user_overrides}
result = evaluator.evaluate(ctx, params)
```

User overrides stored in `flight_profiles.settings_json` under `advisories: {enabled: {id: bool}, params: {id: {key: val}}, aggregation: "worst"|"majority"}`. Recalculation endpoint loads the flight's profile settings (including aggregation mode), re-evaluates without re-fetching weather data.

## Pipeline Integration

In `tasks/advise.py` via `run_advisories()`:
1. **Method resolution**: `_resolve_analyses(rp_analyses, icing_method, cloud_method)` returns new objects with the user's preferred icing/cloud method resolved into the active `icing_zones`/`cloud_layers` slots via `model_copy()` — originals are never mutated. Returns the original list unchanged when no swap is needed. See [analysis.md](./analysis.md) for method details.
2. **Model filtering**: `advisory_models` preference selects which models to evaluate (default excludes `best_match`)
3. Build `RouteContext` from existing route analyses, cross-sections, elevation, airport conditions
4. Call `evaluate_all(ctx)` → `list[RouteAdvisoryResult]`
5. Save `RouteAdvisoriesManifest` to `route_advisories.json`

Also supports `run_advisories_from_pack()` for re-evaluation from saved artifacts without re-fetching.

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `.../packs/{ts}/advisories` | GET | Return saved advisories JSON |
| `.../packs/{ts}/advisories/recalculate` | POST | Re-evaluate with user prefs (enabled IDs + param overrides) |

Recalculate loads route analyses + elevation + cross-sections from disk, applies user preferences, returns fresh manifest.

## Frontend

**Dashboard** (`managers/advisories-ui.ts`):
- Summary bar: badge counts per severity (e.g., "3 RED 2 AMBER 5 GREEN")
- Advisory cards sorted RED → AMBER → GREEN → UNAVAILABLE
- Each card: aggregate status badge + name + info button + per-model badges + detail text
- **Altitude slider**: allows evaluating advisories at different cruise altitudes without recalculating the full pipeline
- Recalculate button triggers POST endpoint and re-renders

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
- `wind_at_altitude` does a linear scan of pressure levels (not binary search) — fine for ~15 levels

## References

- Data models: [data-models.md](./data-models.md) (RouteAdvisoryResult, AdvisoryCatalogEntry, etc.)
- Analysis layer: [analysis.md](./analysis.md) (sounding analysis that feeds evaluators)
- Architecture: [architecture.md](./architecture.md) (pipeline, API endpoints)
- Per-waypoint advisories: `analysis/sounding/advisories.py` (different system — vertical regimes)
