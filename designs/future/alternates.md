# Weather-Based Alternate Airports

**Status: Design — not yet implemented.** Captures the decisions from the
"alternate" planning thread so an implementing agent has enough to build v1.

Goal: when a destination's forecast is marginal, surface the **closest airports
a pilot could divert to that fix the specific problem** (flight category, wind,
crosswind), classify each as reachable **before** or **after** the destination
along the route, and present the result on the briefing (D-2 inward), the route
map, and in the AI digest.

The "prediction" for each alternate MUST be computed by the **same code path** as
the pan-European forecast map, so the same airport shows the same category /
crosswind / consensus in both places.

## Why this matters

NWP + METAR/TAF tell a pilot the destination is IFR with a 22 kt crosswind. They
don't tell the pilot *where to go instead*, or — critically — that the good
alternate is 20 nm **behind** them, so the divert decision has to be made early
or it costs backtracking. This feature answers "where, how much better, and how
early must I decide."

## Core concepts

### Before vs after (early-decision vs fallback)

Project each candidate airport onto the route polyline. euro_aip already does
this: `model.find_airports_near_route(route, distance_nm)` returns per airport
`enroute_distance_nm` (along-track) and `segment_distance_nm` (cross-track).

- **before** — `enroute_distance_nm < dest_enroute_distance − margin`. Diverting
  here means bailing early and flying *less* total distance, but you must commit
  early (before you've seen how the destination actually develops).
- **after** — at/beyond the destination's along-track position. Only useful if
  you press on and find the destination unusable.

Surface a quantitative **detour pair** rather than relying on the binary alone:

- `detour_early_nm = dist(Dep→Alt) − dist(Dep→Dest)` — extra miles if you commit
  to the alternate from the start.
- `detour_late_nm = dist(Dep→Dest_area→Alt) − dist(Dep→Dest)` — extra miles if
  you fly toward the destination then divert.

"before" alternates are exactly those where `detour_early ≪ detour_late`. Show
e.g. *"+8 nm early / +47 nm late"* — it communicates the whole idea.

Use `euro_aip.models.navpoint.NavPoint.haversine_distance` for the distances.
Airports near the destination but laterally off the route (large
`segment_distance_nm`) are **not** caught by `find_airports_near_route` with a
tight corridor — add a separate destination-radius query (great-circle from the
destination airport) and union the two candidate sets.

### "Better" is multi-axis, per-axis

A field can be VFR-but-gusty or IFR-but-calm. Do **not** collapse into one score.
Axes for v1: **flight category**, **wind speed**, **best-runway crosswind**.
(Headwind, ceiling, convective/icing-at-field are v2.)

Primary output is the **nearest-improving alternate per deficient axis** — the
"scan outward by distance" the user asked for:

> Destination EGxx: **IFR**, 18 kt wind, **24 kt crosswind**.
> - nearest **VFR** field: EGyy, 22 nm, before (+9 / +41 nm)
> - nearest **crosswind < 15 kt**: EGzz, 14 nm, after
> - nearest better on **both**: EGww, 31 nm, before

Plus a ranked table (closest-first) of all qualifying alternates with a
"dominates destination on all axes" (Pareto) flag.

### "Consistently better"

Gate recommendations so we don't chase noise:
1. **Across models** — better in **worst-consensus** mode AND model agreement not
   "divergent" (reuse `compare_models`).
2. **Across time** (v2) — better across ETA ± window, not one lucky hour.

## Gating

Two gates, both required:

1. **`days_out <= 2`** (D-2, D-1, D-0). No point computing alternates early —
   the medium-range picture is too uncertain and the pilot won't act on it.
   Mirror the `days_out == 0` gate that wraps `run_route_weather`
   (`pipeline.py:456`); use `<= 2`.
2. **`options.compute_alternates`** user preference (default off). When enabled,
   "do it right"; when off (a "fast brief"), skip the whole stage.

Also requires `options.airports_db_path` (euro_aip), like route_weather.

## Candidate selection

1. **Geometry**: union of
   - `model.find_airports_near_route(route, distance_nm=ALT_CORRIDOR_NM)` (default
     ~40 nm), and
   - airports within `ALT_DEST_RADIUS_NM` (default ~50 nm) of the destination
     (great-circle), to catch laterally-offset and "after" fields.
2. **Drop the destination itself** and the departure.
3. **GA-appropriateness** (heuristic — euro_aip has no single flag):
   - exclude `airport.type == "large_airport"`
   - exclude `airport.scheduled_service == "yes"`
   (together these drop Heathrow/Gatwick/CDG/AMS-type fields).
4. **Suitability** against the aircraft profile when available:
   - `airport.has_hard_runway` (configurable; some pilots accept grass)
   - `airport.longest_runway_length_ft >= aircraft min` (use the flight's
     `FlightProfile`/`UserAircraftRow` if present, else a conservative default)
5. **Instrument-approach gate** (the key D-2 rule): **if the destination's
   consensus category is IFR or MVFR**, restrict candidates to those with an
   instrument approach (`airport.procedures_query.approaches().exists()` /
   `with_procedures("approach")`). We do **not** have published minima, so this
   is imperfect — but a field with an ILS beats one with no approach. Carry the
   **best approach precision tier** (`approaches().most_precise().approach_type`,
   ILS > RNP > RNAV > VOR > NDB) onto the record as a minima proxy and show it.
   If destination is VFR, no approach requirement.
6. Cap the candidate count (e.g. nearest ~30 by dest distance) and `log()` the
   cap so coverage is honest.

## Data & consistency (the important part)

**Decision: always fresh-recompute at the exact ETA** through the shared map
math. We do NOT read the standalone `airport_forecast_snapshots` store (that's
watchlist-only, 5 sample hours, D-0…D-3). Fresh-fetch covers all airports at the
real ETA; consistency is guaranteed by reusing the *functions*, not the cached
rows.

### Per-model fetch split (mirror the forecast map exactly)

| Model | Source | Reuse |
|-------|--------|-------|
| GFS, ICON | Open-Meteo multi-point + GFS/ICON GRIB ceiling | `_fetch_forecasts_for_model` + `_enrich_with_grib` (`standalone_verification.py:275`, `:483`) |
| ECMWF | local GRIB a1 (surface incl. **visibility**) + a2 (pressure) | `fetch_ecmwf_grib_snapshots` + `_select_ecmwf_grib_run` (`:593`, `:559`) |

ECMWF visibility is **only** available from GRIB — Open-Meteo doesn't republish
it. This is exactly what the forecast map does today (`STANDALONE_MODELS =
["gfs","icon","ecmwf"]`). All three then run `_enrich_with_sounding` →
`analyze_sounding_lite` for ceiling/CAPE/convective.

GRIB decode is **point-count-insensitive**: `decode_ecmwf_surface(path, lats,
lons)` decodes the whole airport list in one call (the standalone pipeline does
~830 airports per step). A few dozen alternates is effectively free on decode;
the real cost is N×3 **lite** soundings.

### The one required code change ("Seam 1, minimal")

`_fetch_forecasts_for_model` (GFS/ICON) hard-filters to module constant
`SAMPLE_HOURS_UTC = [6,9,12,15,18]` (`standalone_verification.py:178`). To target
the flight's ETA hour, promote the sample-hour selection to a parameter (default
preserves current behavior). `fetch_ecmwf_grib_snapshots` already takes
`sample_hours`/`days`, so no change there. This is a small parametrization done
in place — NOT a module relocation. The alternates stage imports these fetchers
from `standalone_verification` (or, optionally later, a shared
`tasks/airport_forecast.py`).

### Shared assembly ("Seam 2" — the consistency guarantee)

Lift the snapshot→category→crosswind→consensus functions out of
`tasks/map_queries.py` so both the forecast map and alternates call the SAME
code, and refactor them to read a **plain snapshot dict** (keys already equal the
`AirportForecastSnapshotRow` column names) instead of the ORM row:

- `_best_ceiling` (`:88`) — ceiling priority `sounding_ceiling_ft → nwp_ceiling_ft
  → cloud_base_ft → lcl_ft`
- `_flight_category` (`:94`) — `classify_flight_category(ceiling, visibility_m /
  1609.34)` (visibility always converted to statute miles)
- `_snap_to_dict` (`:98`)
- `_enrich_wind` (`:114`) — `compute_runway_winds` → `best = min(crosswind_kt,
  -headwind_kt)` + gust components
- `_consensus` (`:143`) — `FlightCategory.worst(...)` for the headline (use
  `mode="worst"` to match map default); crosswind/headwind consensus = `max`
  (worst) across models; agreement via `compare_models`

Suggested home: `analysis/airport_consensus.py` (pure). `map_queries.py` imports
from it; the only map change is feeding `row → dict` (e.g. via a small adapter).

For non-watchlist alternates, fetch runway ends per-request via
`weatherbrief.airports.get_runway_ends(icaos, airports_db_path)` (the map caches
watchlist runways only).

### Consistency risk register

| Risk | Mitigation |
|------|------------|
| ECMWF visibility missing | use GRIB path for ECMWF (never Open-Meteo) |
| GFS/ICON ceiling differs | call `_enrich_with_grib` too, else `_best_ceiling` falls back differently |
| Wrong consensus mode | use `mode="worst"` for the headline category |
| Runway cache is watchlist-only | per-request `get_runway_ends` |
| Init time / hour drift vs map | accepted: fresh-fetch is method-identical, not byte-identical; the *recipe* matches |

## Output data model

New models (suggest `models/alternates.py`), attached to `ForecastSnapshot`
alongside `route_observations` / `route_sigmets` (`models/analysis.py:851`):

```python
class AlternateAirport(BaseModel):
    icao: str
    name: str | None
    lat: float
    lon: float
    # geometry
    distance_from_dest_nm: float
    enroute_distance_nm: float       # along-track
    segment_distance_nm: float       # cross-track
    position: str                    # "before" | "after"
    detour_early_nm: float
    detour_late_nm: float
    # consensus assessment (same shape as forecast-map consensus)
    flight_category: str
    wind_speed_kt: float | None
    crosswind_kt: float | None
    headwind_kt: float | None
    best_runway_id: str | None
    ceiling_ft: float | None
    visibility_m: float | None
    agreement: dict[str, str]
    per_model: dict[str, dict]       # raw per-model dicts (like map "models")
    # suitability
    has_instrument_approach: bool
    best_approach_type: str | None   # ILS/RNP/RNAV/... precision tier (minima proxy)
    longest_runway_ft: int | None
    has_hard_runway: bool
    point_of_entry: bool             # customs/border crossing
    # vs-destination flags
    better_category: bool
    better_wind: bool
    better_crosswind: bool
    dominates_destination: bool      # better-or-equal on all axes (Pareto)

class AlternateAxisPick(BaseModel):
    axis: str                        # "category" | "wind" | "crosswind"
    icao: str | None                 # nearest improving alternate (None if none)
    distance_from_dest_nm: float | None
    position: str | None

class RouteAlternates(BaseModel):
    destination_icao: str
    destination_category: str
    destination_crosswind_kt: float | None
    eta: datetime | None
    corridor_nm: float
    radius_nm: float
    require_approach: bool            # whether the IAP filter was applied
    candidates_evaluated: int
    alternates: list[AlternateAirport]   # ranked, closest-first
    nearest_improving: list[AlternateAxisPick]
    computed_at: datetime
```

`ForecastSnapshot.alternates: RouteAlternates | None = None`.

## Pipeline integration

New gated optional stage, structurally like `run_route_weather` / `run_fronts`:

```python
# pipeline.py, after analysis (and after dest category is known)
if days_out <= 2 and options.compute_alternates and options.airports_db_path \
        and not options.historical_mode:
    _t0 = perf_counter()
    try:
        from weatherbrief.tasks.alternates import run_alternates
        result.alternates = run_alternates(
            route, route_analyses, target_time, options.airports_db_path, ...
        )
        result.usage.alternates_computed = result.alternates is not None
        result.usage.alternates_count = len(result.alternates.alternates) if result.alternates else 0
        result.usage.alternates_candidates = result.alternates.candidates_evaluated if result.alternates else 0
    except Exception:
        logger.warning("Alternates stage failed", exc_info=True)
    stage_timings["alternates"] = perf_counter() - _t0
```

`run_alternates` (new `tasks/alternates.py`): candidate selection → fetch per-
model snapshot dicts at ETA (the fetchers above) → shared Seam-2 assembly
(category/wind/consensus) → before/after + detour + nearest-improving ranking →
`RouteAlternates`. Store on the snapshot; gracefully degrade on fetch failure
(same try/except pattern as route_weather).

`RouteAlternates` must be wired into the realtime refresh seam too if we want it
to refresh on D-0 (optional v1 — can be briefing-time only first).

## Presentation

### Briefing web UI — the D-2 section

Mirror the METAR/TAF observations section exactly (it's the established pattern):

- `web/briefing.html`: add `<div id="alternates-section">` near
  `observations-section` (`briefing.html:112`) inside an `alternates-wrapper`.
- `web/ts/managers/briefing-ui.ts`: add `renderRouteAlternates(snapshot)`
  mirroring `renderRouteObservations` (`:923`). **Hide the wrapper when
  `snapshot.alternates` is null** — so the section only appears once alternates
  are populated, i.e. D-2 inward. This satisfies "section appears in D-2."
- `web/ts/briefing-main.ts`: call `ui.renderRouteAlternates(state.snapshot)`
  next to the existing `renderRouteObservations` calls (`:1021`, `:1433`).

Section content:
- **Header line**: destination category + crosswind, then the nearest-improving
  picks ("nearest VFR: EGyy 22 nm before (+9/+41)").
- **Table** (closest-first): ICAO · dist-from-dest · before/after (+ detour pair)
  · category (with agreement badge) · wind · best-runway crosswind · approach
  type · Δ-vs-destination tags · "dominates" flag. Color categories with the
  same scale as the forecast map.
- Info popup per row (like observations) with per-model breakdown.

### Route map overlay (v1 or v2)

Add alternate markers to the Leaflet route map, colored by category/crosswind
(reuse forecast-map color scales), before vs after distinguished by
shape/halo, faint divert lines from the route.

### AI digest

Add an `=== ALTERNATES ===` section to `digest/prompt_builder.py` and a
`--- Alternates ---` block to `digest/text.py` with the nearest-improving picks
so the LLM can say it in prose. High value, low effort.

### HTML report

Table after airport conditions in `report/templates/briefing.html`, same columns
as the web table.

## Settings / preference wiring

Copy the `auto_front_detection` experimental-flag pattern verbatim:
- add `compute_alternates: bool | None` to the profile-settings model
  (`api/profiles.py:38–44`)
- read it in `api/packs.py` (`profile_settings.get("compute_alternates", False)`)
  and set a new `BriefingOptions.compute_alternates` (`pipeline.py:67`)
- gate the stage on it

Future: a "brief detail level / fast profile" pref that bundles
`compute_alternates` with other optional-heavy steps (fronts, charts, GRAMET).
The per-feature boolean is the right primitive to build first.

## Instrumentation (so we can decide the optimization later)

The user explicitly wants timing kept so we can later decide whether reusing the
forecast-map snapshots saves significant time. Hooks already exist:
- `stage_timings["alternates"]` (the `perf_counter()` pattern in `pipeline.py`)
- `BriefingUsage` (`pipeline.py:105`) new fields: `alternates_computed: bool`,
  `alternates_count: int`, `alternates_candidates: int`, and (recommended)
  `alternates_fetch_calls: int` so we can see fetch-vs-sounding split.

## v1 / v2 split

**v1**: D-2 gate + `compute_alternates` pref · candidate selection (near-route ∪
dest-radius, GA-exclusion, runway suitability, IAP-if-IFR/MVFR with precision
tier) · fresh fetch at ETA via the per-model split · Seam-2 shared assembly ·
before/after + detour + nearest-improving-per-axis · briefing table section +
digest line · instrumentation.

**v2**: route map markers · GRIB-native ceiling for GFS/ICON shortlist ·
convective/icing-at-field axis (needs full sounding) · across-time consistency
window · realtime-refresh wiring · suitability from AIP (PPR/customs/hours) ·
optional forecast-map snapshot reuse if profiling shows the lite-sounding cost
matters · "return-to-departure" bucket.

## Concerns / edge cases

- Doglegged routes: ambiguous CPA on sharp turns — `find_airports_near_route`
  returns the closest segment; document the limitation.
- Near-departure "alternates" (`enroute_distance ≈ 0`): really "turn back" —
  consider a separate bucket or exclude.
- Weather ≠ sufficiency: a CAVOK 400 m grass strip is useless — the runway
  filter handles the worst of it; PPR/customs deferred to v2.
- No minima: approach **presence + precision tier** is a proxy, not a guarantee
  — label it advisory.
- Must read clearly as **planning-grade divert candidates**, not an operational
  alternate-minima computation.

## Key references

- Geometry: `euro_aip` `model.find_airports_near_route` (returns
  `enroute_distance_nm` + `segment_distance_nm`), `NavPoint.haversine_distance`
- Approaches / GA flags: `euro_aip` airport `.type`, `.scheduled_service`,
  `.point_of_entry`, `.has_hard_runway`, `.longest_runway_length_ft`,
  `.procedures_query.approaches().most_precise()`
- Fetch reuse: `tasks/standalone_verification.py:275` (`_fetch_forecasts_for_model`),
  `:483` (`_enrich_with_grib`), `:437` (`_enrich_with_sounding`), `:593`
  (`fetch_ecmwf_grib_snapshots`), `:559` (`_select_ecmwf_grib_run`),
  `:178` (`SAMPLE_HOURS_UTC` — the param to promote)
- Shared assembly to extract: `tasks/map_queries.py:88/94/98/114/143`
- Snapshot schema: `db/models.py` `AirportForecastSnapshotRow`
- Pipeline gate template: `pipeline.py:456` (route_weather), `:316`
  (auto_front_detection), `:105` (`BriefingUsage`)
- UI pattern: `web/ts/managers/briefing-ui.ts:923` (`renderRouteObservations`),
  `web/briefing.html:112` (`observations-section`), `web/ts/briefing-main.ts:1021`
- Pref pattern: `api/profiles.py:42` (`auto_front_detection`), `api/packs.py`
- Related: [standalone-verification-plan.md](./standalone-verification-plan.md),
  [hewson-fields-aviation-advisories.md](./hewson-fields-aviation-advisories.md)
  (gated-experimental-stage precedent)
