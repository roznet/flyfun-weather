# Weather-Based Alternate Airports

> When a destination's forecast is marginal (D-2 inward), surface the closest
> airports a pilot could divert to that **fix the specific problem** (flight
> category, wind, crosswind), classified as reachable **before** or **after** the
> destination along the route.

**Status: Shipped (#210).** Gated behind the `compute_alternates` user preference
(default off) and `days_out <= 2`.

## Intent

NWP + METAR/TAF tell a pilot the destination is IFR with a 22 kt crosswind. They
don't tell the pilot *where to go instead*, or — critically — that the good
alternate is 20 nm **behind** them, so the divert decision has to be made early
or it costs backtracking. This feature answers "where, how much better, and how
early must I decide."

**Non-negotiable consistency rule:** the per-airport prediction MUST be computed
by the **same code path** as the pan-European forecast map, so the same airport
shows the same category / crosswind / consensus in both places. This is enforced
by sharing *functions* (`analysis/airport_consensus.py`), not cached rows — see
"Shared assembly" below.

This is **planning-grade divert candidates**, not an operational alternate-minima
computation. We have no published minima; approach presence + precision tier is a
proxy only.

## Core concepts

### Before vs after (early-decision vs fallback)
Each candidate is projected onto the route polyline (euro_aip's
`find_airports_near_route` gives `enroute_distance_nm` along-track,
`segment_distance_nm` cross-track). A waypoint's cumulative along-track distance
comes from `analysis/route_geometry.py:compute_route_distances(route)` (shared
with route_weather).

- **before** — `enroute_distance_nm < dest_enroute_nm − ALT_POSITION_MARGIN_NM`.
  Bail early, fly *less* total distance, but commit before seeing how the
  destination develops.
- **after** — at/beyond the destination's along-track position.

A quantitative **detour pair** is surfaced rather than the binary alone:
`detour_early_nm` (extra miles committing from the start) and `detour_late_nm`
(extra miles flying toward dest then diverting). Shown as e.g. *"+8 nm early /
+47 nm late"*.

### "Better" is multi-axis, per-axis
A field can be VFR-but-gusty or IFR-but-calm — never collapsed into one score.
Axes: **flight category**, **wind speed**, **best-runway crosswind**. The primary
output is the **nearest-improving alternate per deficient axis**
(`nearest_improving: list[AlternateAxisPick]`), plus a ranked closest-first table
with a `dominates_destination` flag (Pareto: better-or-equal on all axes). The UI
renders that flag as a **"Better"** badge.

### Consensus ("consistently better")
`airport_consensus.consensus(per_model, mode="worst")`:
- **flight category** — `FlightCategory.worst(...)` (matches map default).
- **crosswind** — `max` across models (worst = largest).
- **headwind** — `min` across models (worst = weakest headwind / strongest
  tailwind; a positive headwind helps, so the conservative pick is the smallest).
- agreement via `compare_models`.

## Architecture

### Shared assembly — the consistency guarantee
`analysis/airport_consensus.py` holds the pure snapshot→assessment functions,
operating on a **plain snapshot dict** whose keys equal `AirportForecastSnapshotRow`
column names (NOT the ORM row):

- `best_ceiling(snap)` — priority `sounding_ceiling_ft → nwp_ceiling_ft →
  cloud_base_ft → lcl_ft`
- `flight_category(snap)` — `classify_flight_category(ceiling, visibility_mi)`
- `snap_to_dict(snap)` — column-keyed → lightweight per-model dict
- `enrich_wind(d, runway_ends)` — `compute_runway_winds`, best runway, gusts
- `consensus(per_model, mode="worst")` — see above

`tasks/map_queries.py` imports these (aliased `_shared_*`) and wraps them with
row→dict adapters, so the forecast map and alternates run identical math. This is
the realization of the design's "Seam 2".

### Fetch (mirror the forecast map exactly)
`run_alternates` fresh-fetches every candidate at the real ETA hour — it does NOT
read the watchlist `airport_forecast_snapshots` store (5 sample hours only).
Per-model split reuses `tasks/standalone_verification.py`:

| Model | Source |
|-------|--------|
| GFS, ICON | Open-Meteo multi-point + GFS/ICON GRIB ceiling (`_fetch_forecasts_for_model` + `_enrich_with_grib`) |
| ECMWF | local GRIB a1 (surface incl. **visibility**) + a2 (pressure) (`fetch_ecmwf_grib_snapshots`) |

ECMWF visibility is GRIB-only (Open-Meteo doesn't republish it). GRIB decode is
point-count-insensitive, so a few dozen alternates is effectively free on decode;
the real cost is N×3 lite soundings. Runway ends are fetched per-request via
`weatherbrief.airports.get_runway_ends` (the map caches watchlist runways only).

### Entry point
```python
# tasks/alternates.py
run_alternates(
    route: RouteConfig,
    target_time: datetime,
    airports_db_path: str,
    *,
    corridor_nm=ALT_CORRIDOR_NM,        # 40.0
    radius_nm=ALT_DEST_RADIUS_NM,        # 50.0
    max_candidates=ALT_MAX_CANDIDATES,   # 30
    require_hard_runway=True,
    min_runway_ft=ALT_DEFAULT_MIN_RUNWAY_FT,  # 2000
    now=None,
) -> RouteAlternates | None
```
`ALT_POSITION_MARGIN_NM = 10.0`.

Candidate selection: (1) geometry union of near-route corridor ∪ dest-radius;
(2) drop departure + destination; (3) runway suitability (hard runway, length ≥
min); (4) cap to nearest `max_candidates` by dest distance; (5) fresh fetch +
shared assembly at ETA; (6) **per-candidate** approach gate (below).

> **No backend preference filtering (changed).** `large_airport` and
> `scheduled_service` are **no longer dropped** server-side — that silently
> excluded good IFR diversions (e.g. EGTE/Exeter: airline service, but a 6811 ft
> ILS runway 3 nm off the corridor). The backend now **returns every reachable
> candidate** and only flags `is_major = (type == "large_airport")`. Hiding
> majors and limiting distance are **view** choices, applied client-side (see
> "List-view controls"). Only genuine reachability/safety gates stay
> server-side: hard runway, `min_runway_ft`, and the sub-VFR-no-approach gate.

### The candidate funnel: "evaluated" vs "shown"
The summary count and the table row count are **two different stages**, and the
gap between them surprised a reader (e.g. *"25 candidates within 50 nm" but only
7 rows*). The full funnel:

| Stage | What it does | Reported as |
|-------|--------------|-------------|
| 1. Geometry | corridor (≤`corridor_nm`) ∪ dest-radius (≤`radius_nm`) | — |
| 2. Drop self | remove departure + destination | — |
| 3. Runway suitability | drop no hard runway, runway `< min_runway_ft`, missing coords (`large_airport` / scheduled service are **kept**, just flagged `is_major`) | — |
| 4. Cap | nearest `max_candidates` (30) by dest distance | **`candidates_evaluated`** (the "N candidates" headline) |
| 5. Weather fetch | drop any candidate with **no model snapshot** at the ETA hour (`_assess` → None) | — |
| 6. Approach gate | drop a field that is **itself** MVFR/IFR/LIFR *and* has **no published IAP** (unreachable in those conditions); VFR always kept, any field with an IAP always kept | — |
| → | survivors, ranked closest-first | **`len(alternates)`** (returned to the client) |
| 7. View filters (client) | hide `is_major` (default on) + max-distance-from-dest (default 100 nm) | the **rows the pilot sees** |

So **`candidates_evaluated` is the post-cap count (stage 4)**, *not* the number
returned. The returned list is what survives stages 5–6; the **shown** list is
that minus the client-side view filters (stage 7). For a typical IFR destination
the gate (stage 6) is the dominant server-side reducer — most nearby fields are
sub-VFR with no published approach.

**Cap × majors interaction:** because majors now reach stage 4, they compete for
the 30 nearest-to-dest slots. The effect is minor (large_airports are rare within
the corridor/radius) and the default-visible set — non-major fields closest to the
destination — is exactly what the nearest-to-dest cap favours, so it survives.
The distance selector's **"All"** means *all returned* candidates, still bounded
by the stage-4 cap (it does not re-query the backend for a wider net).

The returned list is **not** filtered to "beats the destination" — it shows every
survivor with a per-row `dominates_destination` flag and `vs dest` Δ tags; the
"which is the nearest *improvement*" question is answered separately by
`nearest_improving` (computed over the full returned set, **not** re-filtered by
the view controls). `approach_filter_relaxed` (stage 6 graceful degradation)
suppresses the gate entirely.

### List-view controls
`renderRouteAlternates` (web) renders two controls above the table, defaulting to
a concise near-destination view; both are **session state** (persist across store
re-renders, reset on reload), not user prefs:

- **Hide major airports** (default **on**) — hides `is_major` rows
  (`large_airport`: Heathrow/Gatwick/Manchester). Regional fields with airline
  service (Exeter, Newquay, Bournemouth) are *not* major and stay visible. When
  off, major rows carry a `MAJOR` chip.
- **Within 50 / 100 / All nm** (default **100 nm**) — filters by
  `distance_from_dest_nm`. 100 nm shows near-destination diversions (incl. Exeter
  ≈ 63 nm) while hiding the far en-route/early-divert corridor candidates; "All"
  shows everything returned.

The summary line reports the breakdown (`N evaluated · M shown within Dnm · K
major hidden · …`). The **text digest mirrors this default view** (hide major,
≤ 100 nm; `_format_route_alternates` in `digest/text.py`), appending a
`(+N more … — see web)` line when items are hidden.

### Per-candidate instrument-approach gate
Applied **after** each candidate's own weather is assessed (not at
destination-level as the original design sketched). For each candidate, if its
*own* consensus category needs an approach (MVFR/IFR/LIFR via `_needs_approach`)
and it has no IAP, it is gated out. The record carries `best_approach_type`
(ILS > RNP > RNAV > VOR > NDB) as a minima proxy.

**Graceful degradation:** if no candidate has *any* IAP data at all (procedure
data absent) yet some would be gated out, the filter is relaxed and
`RouteAlternates.approach_filter_relaxed = True` is set instead of going dark.
The UI surfaces a warning when this flag is set.

## Pipeline integration
```python
# pipeline.py, after analysis (dest category known)
if days_out <= 2 and options.compute_alternates \
        and options.airports_db_path and not options.historical_mode:
    result.alternates = run_alternates(route, target_time, options.airports_db_path, ...)
    # BriefingUsage: alternates_computed, alternates_count, alternates_candidates
    # stage_timings["alternates"] tracked via perf_counter()
```
`ForecastSnapshot.alternates: RouteAlternates | None`.

Preference wiring copies the `auto_front_detection` pattern: `compute_alternates`
on the profile-settings model (`api/profiles.py`), read in `api/packs.py`
(`profile_settings.get("compute_alternates", False)`), set on `BriefingOptions`.

### Regulatory layer — separate co-located subsystem (#249)
`run_alternates` does NOT compute regulatory minima. A **pipeline post-step**
(`tasks/alternate_requirement.py:run_alternate_requirement`, called after
alternates + route weather) mutates `snapshot.alternates` **in place**: it sets
`RouteAlternates.alternate_requirement` (the destination "is a filed alternate
required?" trigger, FAA + EASA) and fills each `AlternateAirport.faa/.easa`
(per-candidate alternate-minima qualification). `run_alternates` only seeds the
NWP fallback inputs (`destination_ceiling_ft/_visibility_m`) it will read when no
destination TAF exists. That whole regime — FAA 14 CFR 91.169 binary + EASA
Part-NCO band, plate-minima proxy, TAF TEMPO/PROB conditional handling — has its
own design: **[alternate-requirement.md](./alternate-requirement.md)**. Keep the
two docs in their lanes: weather-divert-candidate geometry/assessment here,
regulatory minima there.

## Output data model (`models/alternates.py`)
`AlternateAirport` (geometry: `distance_from_dest_nm`, `enroute_distance_nm`,
`segment_distance_nm`, `position`, `detour_early_nm`, `detour_late_nm`;
assessment: `flight_category`, `wind_speed_kt`, `crosswind_kt`, `headwind_kt`,
`best_runway_id`, `ceiling_ft`, `visibility_m`, `agreement`, `per_model`;
suitability: `has_instrument_approach`, `best_approach_type`, `longest_runway_ft`,
`has_hard_runway`, `point_of_entry`, `is_major` (== `large_airport`; hidden by
default in the UI list controls); vs-dest flags: `better_category`,
`better_wind`, `better_crosswind`, `dominates_destination`; regulatory:
`faa`, `easa` — per-candidate alternate-minima qualification, filled by the
**alternate-requirement post-step**, NOT by `run_alternates` — see below).

`AlternateAxisPick` (`axis`, `icao`, `distance_from_dest_nm`, `position`).
`ALT_AXIS_LABELS` maps axis keys to display labels (used by text digest + UI).

`RouteAlternates` (`destination_icao/_category/_crosswind_kt`, `eta`,
`corridor_nm`, `radius_nm`, `require_approach`, `approach_filter_relaxed`,
`candidates_evaluated`, `alternates`, `nearest_improving`, `computed_at`;
plus `destination_ceiling_ft/_visibility_m` and `alternate_requirement` —
all three populated by the post-step, see "Regulatory layer").

## Presentation
- **Briefing web UI**: `briefing-ui.ts:renderRouteAlternates(snapshot)` mirrors
  `renderRouteObservations`. Wrapper hidden when `snapshot.alternates` is null, so
  the section only appears D-2 inward. Columns: alternate (ICAO + info button) ·
  from-dest dist · before/after (+ detour pair) · category (+ agreement badge) ·
  wind · best-runway crosswind · approach type · vs-dest Δ tags incl. **"Better"**.
  Warns when `approach_filter_relaxed`.
- **Plain-text digest**: `digest/text.py:_format_route_alternates` →
  `--- Weather Alternates ---` block with the nearest-improving picks (labelled
  via `ALT_AXIS_LABELS`) + ranked table (top 8). When the post-step populated
  `alternate_requirement`, it also prints the destination "Alternate required?
  FAA/EASA" line and per-candidate FAA/EASA tags (owned by alternate-requirement.md).
- **LLM digest**: intentionally **NOT** fed to the prompt yet — the block in
  `digest/prompt_builder.py` is commented out (`#210`). Deferred to avoid prompt
  bloat until the feature settles.

## Gotchas
- Always `mode="worst"` for the headline category — matches the map default. A
  different mode silently desyncs alternates from the forecast map.
- Fresh-fetch is *method-identical* to the map, not byte-identical (init time /
  hour can drift); the *recipe* matches, which is the guarantee we make.
- Doglegged routes: ambiguous CPA on sharp turns — `find_airports_near_route`
  returns the closest segment only; documented limitation.
- Near-departure "alternates" (`enroute_distance ≈ 0`) are really "turn back".
- No minima: approach presence + precision tier is advisory, never operational.

## References
- Geometry: `euro_aip` `find_airports_near_route`, `NavPoint.haversine_distance`;
  `analysis/route_geometry.py:compute_route_distances`
- Fetch reuse: `tasks/standalone_verification.py` (`_fetch_forecasts_for_model`,
  `_enrich_with_grib`, `_enrich_with_sounding`, `fetch_ecmwf_grib_snapshots`)
- Shared assembly: `analysis/airport_consensus.py`; consumed by
  `tasks/map_queries.py`
- Pipeline / prefs: `pipeline.py`, `api/profiles.py`, `api/packs.py`
- UI: `web/ts/managers/briefing-ui.ts:renderRouteAlternates`
- Regulatory post-step: `tasks/alternate_requirement.py`,
  `analysis/alternate_requirement.py` →
  [alternate-requirement.md](./alternate-requirement.md)
- Related: [standalone-verification-plan.md](./future/standalone-verification-plan.md),
  [advisories.md](./advisories.md)
