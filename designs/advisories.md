# Route Advisory System

> Deterministic evaluation of weather hazards along a flight route with per-model severity assessments

## Intent

Provide actionable, severity-graded (GREEN/AMBER/RED) advisories for 9 weather hazard categories along the route. Evaluators analyze existing route analysis data — no additional data fetch. User-tunable parameters allow recalculation without re-running the pipeline. This is a **route-level** system (advisory per route), complementing the per-waypoint `AltitudeAdvisories` in the sounding subpackage.

## Architecture

```
RouteContext (immutable)
  ├── analyses: list[RoutePointAnalysis]   (~20 points along route)
  ├── cross_sections: list[RouteCrossSection]  (per-model forecast grids)
  ├── elevation: ElevationProfile | None
  ├── models, cruise_altitude_ft, flight_ceiling_ft, total_distance_nm
      ↓
Registry → evaluate_all(ctx, enabled_ids?, user_params?)
  ├── @register IcingEscapeEvaluator
  ├── @register FIKIIcingEvaluator
  ├── @register FreezingLevelEvaluator
  ├── @register CloudTopEvaluator
  ├── @register VMCCruiseEvaluator
  ├── @register TurbulenceEvaluator
  ├── @register MountainWindEvaluator
  ├── @register ConvectiveEvaluator
  └── @register ModelAgreementEvaluator
      ↓
RouteAdvisoriesManifest (advisories + catalog)
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
3. **Route level**: `RouteAdvisoryResult.from_per_model(id, per_model, params)` — worst status across models becomes aggregate

Detail text comes from the worst-performing model. Shared classmethods on the models eliminated ~115 lines of boilerplate.

## The 9 Evaluators

### Icing

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `IcingEscapeEvaluator` | icing | Non-FIKI: can we descend below freezing to escape icing? Checks FZ level vs terrain + margin | `terrain_margin_ft`, `tight_margin_ft`, `route_pct_amber` |
| `FIKIIcingEvaluator` | icing | FIKI-equipped: evaluates icing layer thickness and severity (transit OK, loiter not) | `thickness_amber_ft`, `thickness_red_ft`, `severe_is_red` |
| `FreezingLevelEvaluator` | icing | Freezing level vs max terrain height (mountain icing risk) | `margin_ft`, `tight_margin_ft` |

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
| `ConvectiveEvaluator` | convective | Route points with convective risk ≥ threshold. HIGH/EXTREME → instant RED | `min_risk`, `affected_pct_amber`, `affected_pct_red` |
| `ModelAgreementEvaluator` | model | Cross-model divergence (POOR/MODERATE agreement). Evaluated once, not per-model | `poor_pct_amber`, `poor_pct_red` |

## Shared Helpers (`_helpers.py`)

- **`format_extent(affected, total, total_distance_nm)`** → `"30nm/55nm (55%)"` — human-readable spatial extent
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

User overrides stored as JSON in `user_preferences` table. Recalculation endpoint loads prefs, re-evaluates without re-fetching weather data.

## Pipeline Integration

In `pipeline.py` at the `"route_advisories"` stage (0.62 progress):
1. Build `RouteContext` from existing route analyses, cross-sections, elevation
2. Call `evaluate_all(ctx)` → `list[RouteAdvisoryResult]`
3. Save `RouteAdvisoriesManifest` to `route_advisories.json`

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
- Recalculate button triggers POST endpoint and re-renders

**Info popup** (`components/info-popup.ts`):
- Full description, category, parameter table with values used
- Reuses shared modal infrastructure from metrics info popups

**Store**: `briefingStore.routeAdvisories` + `recalculateAdvisories()` action.

## Key Choices

- **Protocol over inheritance** — evaluators are peer classes, no hierarchy. Easier to test and extend.
- **Immutable RouteContext** — frozen dataclass prevents accidental mutation across evaluators.
- **Worst-model aggregation** — conservative: if ANY model shows RED, the advisory is RED.
- **Lazy evaluation** — advisories evaluated fresh each time, not cached. Enables fast parameter tuning.
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
