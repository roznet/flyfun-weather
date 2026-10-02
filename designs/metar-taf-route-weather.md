# METAR/TAF Route Weather

> Real-time METAR observations and TAF forecasts from airports along the route, compared against NWP model predictions, surfaced in digest and HTML report.

## Intent

On the day of flight (D-0), NWP model output alone is insufficient — actual METAR observations and TAF forecasts provide ground truth that validates or contradicts model predictions, reports phenomena (TS, FG, FZRA) that models estimate coarsely, and gives the LLM digest concrete observations to reference.

**Timing rule**: Only fetched when `days_out == 0`. European TAFs rarely cover the next day, so D-1+ is skipped.

**What should NOT change**: This is purely additive — the NWP pipeline runs identically whether or not observations are fetched. Observations are optional context, not a replacement for model data.

## Architecture

```
tasks/route_weather.py
├── run_route_weather()          ← fetch METAR/TAF via euro_aip
│     _interpolate_airport_time()← per-airport ETA from enroute distance
│     RouteWeatherService → AvWxSource → aviationweather.gov
│     compute_wind_advisory()    ← runway crosswind assessment per airport
│     _applicable_taf_lines()    ← TAF line indices for UI highlighting
├── run_observation_comparison() ← compare obs vs model predictions
│     _interpolate_airport_time()← per-airport time for model lookup
│     classify_flight_category() from analysis/airport_conditions.py
└── run_realtime_refresh()       ← cheap real-time refresh seam (issue #167)
      load_briefing/forecasts/route_analyses from pack_dir
      run_route_weather() + run_observation_comparison()
      commit into the per-flight live layer (#637) — the pack is NOT patched

models/observations.py
├── AirportObservation     ← per-airport METAR/TAF data (flat, serializable)
├── ObservationComparison  ← obs-vs-model comparison result
├── RouteObservations      ← collection + summary stats
├── SigmetAlongRoute       ← per-SIGMET record (issue #168)
├── RouteSigmets           ← SIGMET collection + computed count/hazards/has_severe
├── RefreshDelta           ← worsening-only view of the live changes (kept for old clients)
└── RealtimeRefreshResult  ← {observations, sigmets, delta, observed, live_updated_at, changes}

tasks/live_significance.py   (#637, replaces tasks/refresh_delta.py)
└── classify_changes()      ← changes since the *briefing*, both directions → LiveChanges
```

### Pipeline Position (step 3.5)

```
1. run_fetch()         → NWP model data
2. run_analysis()      → sounding, wind, model comparison
3. run_advisories()    → route hazard assessment
3.5 run_route_weather() + run_observation_comparison() + run_route_sigmets()  ← D-0 only
4. Build snapshot      → route_observations / route_sigmets on ForecastSnapshot
5-8. GRAMET, Skew-T, LLM digest, text digest
```

Gated by `days_out == 0 and options.airports_db_path and not options.historical_mode`
— a historical replay must not splice live observations into a past briefing.
METAR/TAF and SIGMET each have their own try/except (SIGMETs are fetched even if
the METAR/TAF fetch raised), so the pipeline continues if aviationweather.gov is down.
The `run_route_sigmets` import sits *outside* its try so a genuine ImportError
surfaces rather than being swallowed as a fetch failure.

## Key Components

### Data Models (`models/observations.py`)

`AirportObservation` stores flat, serializable METAR/TAF fields (no euro_aip `WeatherReport` objects):
- METAR: raw text, flight category, ceiling, visibility, wind (dir/speed/gust), weather phenomena, temp, dewpoint, QNH
- TAF: raw text, validity window + `taf_valid_at_eta` (False for an expired / not-yet-valid TAF: raw kept, every at-ETA field empty; None on pre-#610 packs), flight category at ETA (worse of prevailing and temporary), `taf_prevailing_category_at_eta`, `taf_temporary_category_at_eta` + `taf_temporary_type` (only when the TEMPO/PROB group is worse), `taf_significant_weather`, trend type behind the category, wind (strongest of prevailing/temporary)
- `AirportObservation.taf_at_eta_line()` — the one shared TAF line for both digests: `TAF at ETA [VFR], TEMPO [IFR] (CB)`, or `TAF: none valid at ETA (latest TAF valid 11/15Z-11/17Z)`; pre-#610 packs keep the legacy `TAF at ETA [IFR] (TEMPO)`
- Wind advisories: `metar_wind_advisory`, `taf_wind_advisory` — lowercase `green`/`amber`/`red` (from `_wind_advisory_status()`) — with best runway and crosswind values
- TAF highlighting: `taf_applicable_lines: list[int]` — line indices for base + applicable BECMG/TEMPO groups (empty when the TAF is not valid at ETA)
- ETA: `eta_hour_offset: int | None` — rounded hours after departure (from enroute distance interpolation)
- Metadata: ICAO, distance from route, enroute distance, nearest waypoint

`ObservationComparison` stores the obs-vs-model result:
- `category_match`: `CONFIRMING` / `SIGNIFICANT` / `CONFLICTING`
- Visibility and wind deltas, detail string
- `model_wind_advisory`, `model_best_runway_id`, `model_crosswind_kt` — model-derived wind assessment
- `wind_advisory_match`: `CONFIRMING` / `SIGNIFICANT` / `CONFLICTING` — compares METAR vs model wind advisory

`RouteObservations` aggregates everything:
- Airport list, comparison list
- Summary: worst categories, phenomena union, has_conflicts flag

Added to `ForecastSnapshot` as `route_observations: RouteObservations | None`.

### Fetch (`run_route_weather`)

1. Load `EuroAipModel` from SQLite via `DatabaseStorage` (same pattern as `airports.py`)
2. Call `RouteWeatherService().fetch_route_weather(route_icaos, corridor_nm, model)`
3. For each `RouteAirportWeather`, compute per-airport ETA via `_interpolate_airport_time(departure, duration, enroute_dist, total_dist)` — uses `enroute_distance_nm` from euro_aip spatial query and `flight_duration_hours` from `RouteConfig`
4. Extract structured fields into `AirportObservation`, including `eta_hour_offset` (rounded hours)
5. For TAFs, `_apply_taf_at_eta` reads the TAF at the interpolated ETA (not departure) through `analysis/taf_reading.read_taf_at` (euro_aip `WeatherAnalyzer.taf_conditions_at`; shared with the historical map, #629, so both read a TAF identically): nothing when the TAF's validity does not contain the ETA; otherwise prevailing (main body + completed BECMG / started FM, worse-of while a BECMG is in transition), the worst TEMPO/PROB group laid over prevailing, and significant weather. Reasoning in [meteorology-decisions.md §32](meteorology-decisions.md). `airports_with_taf` / `worst_taf_category` count only TAFs valid at ETA
6. Map each airport to nearest waypoint via cumulative great-circle distance

### Comparison (`run_observation_comparison`)

For each airport with a METAR:
1. Compute per-airport interpolated time, then find model forecast at nearest waypoint via `WaypointForecast.at_time(airport_time)`
2. Derive model flight category from `HourlyForecast.visibility_m` via `classify_flight_category()`
3. Classify discrepancy by flight category distance:
   - `CONFIRMING`: same category (diff=0)
   - `SIGNIFICANT`: adjacent categories (diff=1, e.g., VFR↔MVFR)
   - `CONFLICTING`: 2+ categories apart (e.g., VFR↔IFR)
4. Compute visibility and wind deltas for detail annotation

**Model ceiling**: When `route_analyses` are provided, the model ceiling is derived via `reconcile_ceiling(sounding, hourly, field_elevation_ft=..., model=...)` (same path the advisory system uses — sounding ceiling reconciled against NWP cloud diagnostics) on the nearest `RoutePointAnalysis`'s per-model sounding, then fed to `classify_flight_category(ceiling_ft, visibility_sm)`. This allows ceiling-driven IFR comparisons. Falls back to visibility-only when route analyses are unavailable.

**Ceiling datum (#441 finding #3)**: the reconciled ceiling is asked for in **AGL**, because the METAR flight category it is compared against is AGL. That needs a field elevation, so the pipeline passes `airport_elevations` (from `airports.get_airport_elevations`) alongside `runway_data` into `run_observation_comparison`. The elevation is keyed on the **observed airport** (`obs.icao`), not the nearest waypoint — the METAR reports above its own field. A missing elevation degrades to the MSL/unknown-datum behaviour of `reconcile_ceiling`, it does not raise.

### Real-time refresh seam (`run_realtime_refresh`)

`run_realtime_refresh(pack_dir, db_path, *, persist=True, …)` is the **cheap** refresh path (issue #167 Part A): re-fetch METAR/TAF (and route SIGMETs, see below), recompute the comparison from a pack's **stored** forecasts, re-sample observed conditions, and commit the result into the flight's **live layer** ([live-observation-layer.md](live-observation-layer.md), #637). **No** model fetch, **no** GRIB, **no** LLM, and since #637 **no write to the pack**: `briefing.json` keeps the observations the assessment saw, which is the significance baseline. It reads `briefing.json` (route + stored `corridor_nm` + target time via `parse_target_time`), `forecasts.json`, and `route_analyses.json` off disk, calls `run_route_weather()` + `run_observation_comparison()` + `run_route_sigmets()`, and returns a `RealtimeRefreshResult{observations, sigmets, delta, observed, live_updated_at, changes}`. `persist=False` (an older pack) classifies against the baseline but writes nothing. `report_source` / `sigmet_source` let the live tick serve all flights from one shared fetch. Raises `FileNotFoundError` if the pack has no briefing data.

Three callers share this seam:
- `POST .../observations/refresh` — the standalone METAR/TAF refresh button (a thin endpoint wrapper that adds auth + the D-0 400 guard; persists only for the flight's latest pack).
- The tiered refresh gate's `realtime` mode (`api/packs.decide_refresh`) — when a D-0 manual refresh isn't worth a full pipeline run, both refresh-button endpoints invoke `run_realtime_refresh` instead so a D-0 press is always at least cheap-useful. See [freshness-markers.md](freshness-markers.md) for the gate.
- The server live-window tick (`tasks/live_tick.py`), every 10 min from departure − 3 h to arrival + 1 h.

`run_route_weather` also records, per airport, the latest report's type (`METAR`/`SPECI`) and the previous report's category and time from the 3 h fetch window, plus the TAF issue time (the hysteresis inputs before §35; the type still labels a SPECI change).

### Digest Integration

**LLM prompt** (`digest/prompt_builder.py`): `=== METAR/TAF OBSERVATIONS ===` section between MODEL COMPARISON and TEXT FORECASTS. Includes per-airport METAR raw + category, the `taf_at_eta_line()` reading, and comparison annotations for non-confirming airports. No raw TAF text reaches the LLM — the line is the whole TAF signal, which is why expiry and group selection are decided in code.

**Text digest** (`digest/text.py`): `--- METAR/TAF Observations ---` section with summary stats, per-airport METAR lines + `taf_at_eta_line()`, and conflict flags.

### Web UI (`briefing-ui.ts`)

Observations section on the briefing page with:
- **Summary bar**: airport count, worst flight category, phenomena list, refresh button (D-0 only)
- **Two-row grouped table headers**: ICAO, Dist, ETA (+0h/+1h/etc.), Conditions group (METAR/TAF/Model categories + agreement) and Wind group (METAR/TAF/Model wind + advisory match)
- **Info button**: ⓘ in ICAO cell opens detailed airport popup
- **TAF highlighting**: `taf_applicable_lines` indices highlight the base forecast + applicable BECMG/TEMPO lines in the TAF raw text
- **TAF at ETA** (#613): the TAF condition cell shows the prevailing category, the worse TEMPO/PROB group beside it (compact label, e.g. `PROB30`), and significant weather; a TAF not valid at ETA shows "No TAF" (tooltip carries the latest validity) instead of a bare dash. The popup adds an "At ETA" line under the raw TAF with the full temporary label, or "No TAF valid at ETA (latest valid 11/15Z-11/17Z)". The rules live in `helpers/taf-at-eta.ts::readTafAtEta` (unit-tested), mirrored on iOS by `AirportObservation.tafAtEta`
- **Wind advisory icons**: `green`/`amber`/`red` badges (rendered G/A/R) with crosswind values per source
- **Agreement column**: CONFIRMING/SIGNIFICANT/CONFLICTING badges for both conditions and wind
- **Refresh button**: re-fetches METAR/TAF via `POST .../observations/refresh` endpoint, updates snapshot in place

### iOS UI (`Views/Briefing/RouteObservationsView.swift`)

The iOS port (#492) sits in the Advisory tab between Conditions and Alternates —
the same slot as on web — with a scroll-spy anchor (`observations`). It reads
`snapshot.route_observations` from the standard snapshot endpoint (no iOS-specific
endpoint; `GET .../snapshot` returns raw `briefing.json`, so the full shape
including `comparisons` is already on the wire).

**Responsive two-axis table.** The web table is 11 columns, which does not fit a
phone. The two comparison groups therefore become a switchable axis:

| Width | Behaviour |
|---|---|
| Compact (iPhone) | A segmented Condition / Wind picker above the table; one 7-column group at a time (default Condition) |
| Regular (iPad) | Both groups side-by-side with the web's two-row grouped header; picker hidden |

Keyed off `horizontalSizeClass`, matching `RouteMapView`'s dual-metric split.

**Phone row cap (deliberate divergence from web).** A 30 nm corridor on a long
route routinely reports 30+ fields — the EDDC→EGTF verification run returned 29
with a METAR. Web renders them all, which is fine in a browser window but a very
long scroll inside the iOS Advisory tab. In **compact** width the table therefore
shows the **10 nearest** airports (by `distance_from_route_nm`) with a
"Show all N airports" toggle; regular width always shows everything.

Two details that matter:
- Selection is by distance but **rendering stays in route order**, so the table
  still reads departure→destination rather than jumping around the route.
- Any airport whose comparison is `CONFLICTING` (category **or** wind) is
  **pinned into the capped set** regardless of distance — otherwise the
  conflict banner could point at a row the cap had hidden. This means the
  collapsed view can exceed 10 rows, which is why the collapse affordance reads
  "Show fewer" rather than naming a number.

Logic lives on the DTO (`RouteObservations.nearestReportingAirports(limit:)`) so
it is unit-testable rather than buried in the view.

Other parity notes:
- **Wind cells** show the *crosswind in kt* coloured by advisory status, rather
  than the web's bare G/A/R letter — touch has no hover tooltip, so the value
  itself has to be the label. A legend under the table states the unit, the
  colour meaning, and the ✓/⚠/✗ agreement key.
- **Detail sheet** replaces the web's ⓘ popup: raw METAR, raw TAF with
  `taf_applicable_lines` emphasised, the "At ETA" reading (or "No TAF valid at
  ETA" with the latest validity), and the per-source runway-wind breakdown.
- **TAF cell** (#613) follows the web: prevailing badge, `TEMPO`/`PROB30` + the
  worse temporary badge, significant weather in amber, "No TAF" for a TAF not
  valid at ETA. Driven by `AirportObservation.tafAtEta` (SYNC with
  `web/ts/helpers/taf-at-eta.ts`).
- **Table cells** run at `Grid(horizontalSpacing: 0)` with the gutter inside each
  cell, so a CONFLICTING row's per-cell shading tiles into one continuous band
  (Grid applies a row background per cell, not across the row).
- **No in-section refresh button.** Web has one; on iOS the toolbar Refresh
  already routes through the tiered `decide_refresh` gate, which at D-0 performs
  exactly this cheap realtime refresh.
- **Route SIGMETs** are the sibling section, ported in #493 — see
  [iOS UI](#ios-ui-viewsbriefingroutesigmetsviewswift) below.

Fixture-backed (`FixtureBriefingData.route_observations` carries comparisons +
wind advisories), so mock mode renders it; covered by
`RouteObservationsTests.swift` (decode/join/filter) and the XCUI journeys
`testRouteObservationsSectionRenders` / `testRouteObservationsDetailSheet`, which
assert the compact-vs-regular contract.

### HTML Report

Table after airport conditions with columns: ICAO, Distance, ETA, METAR Cat, TAF Cat, Model Cat, Match, METAR raw text. Flight categories are color-coded. Conflicting rows are highlighted amber.

## Key Choices

| Decision | Rationale |
|----------|-----------|
| D-0 only (not D-1/D-2) | European TAFs rarely cover next day; METARs are stale for planning |
| Flat Pydantic models, not euro_aip dataclasses | Serializable to JSON for snapshot storage and template rendering |
| Category-based comparison (not raw values) | Flight category is the operationally meaningful unit; raw value comparison is noisy |
| Three-tier classification (no MINOR_DELTA) | Implemented as CONFIRMING/SIGNIFICANT/CONFLICTING; MINOR_DELTA was dropped for simplicity |
| Sounding ceiling for model category | `reconcile_ceiling()` on the route analyses' per-model sounding when available (AGL, via the airport's field elevation), falling back to visibility-only |
| Runway crosswind advisory | `compute_wind_advisory()` evaluates all runway ends, picks best runway; `green`/`amber`/`red` thresholds |
| Clients read structured TAF-at-ETA fields, not `taf_at_eta_line()` (#613) | The web UI is localised (en/fr/de/es) and renders categories as badges; a server-built English string would bypass both. `taf_at_eta_line()` stays the single text form for the LLM and text digests, and `readTafAtEta` / `tafAtEta` mirror its rules (SYNC comments on all three) |
| TAF line highlighting | `_applicable_taf_lines()` identifies base + BECMG/TEMPO groups active at target time for UI highlighting |
| Per-airport time interpolation | TAF matching and model comparison use `enroute_distance / total_distance * flight_duration` to estimate when the flight passes each airport; falls back to departure time when `flight_duration_hours == 0` |
| Graceful failure (try/except in pipeline) | Network failures shouldn't block the NWP-based briefing |

## Euro_aip Dependencies

| What | Import Path |
|------|-------------|
| `RouteWeatherService` | `euro_aip.briefing.weather.route_weather` |
| `RouteSigmetService` | `euro_aip.briefing.weather.route_sigmet` |
| `WeatherAnalyzer.find_applicable_taf()` / `applicable_trends()` | `euro_aip.briefing.weather.analysis` |
| `DatabaseStorage.load_model()` (via `weatherbrief.airports._load_airport_model`, cached) | `euro_aip.storage.database_storage` |
| `classify_flight_category()`, `reconcile_ceiling()`, `compute_runway_winds()` | `weatherbrief.analysis.airport_conditions` |
| `get_runway_ends()`, `get_airport_elevations()` (comparison inputs) | `weatherbrief.airports` |

## Pipeline Options

```python
# BriefingOptions
metar_taf_corridor_nm: float = 30  # corridor half-width in NM
sigmet_corridor_nm: float = 50     # wider corridor for SIGMET intersection

# BriefingUsage
metar_taf_fetched: bool = False
metar_taf_airports: int = 0
sigmet_fetched: bool = False
sigmet_count: int = 0
```

## Gotchas

- **`datetime.now(timezone.utc)`** is used for `fetch_time` (aware UTC)
- **First model forecast** is used as reference for comparison (`wp_forecasts[0]`) — this is typically `best_match` or the first model in the list
- **aviationweather.gov** has a 400-ICAO batch limit — handled by `AvWxSource` internally
- **Not all airports report METARs** — small GA fields found by spatial query may have no data; the summary notes coverage

## Route SIGMETs (issue #168)

Route SIGMETs are a **sibling** D-0 real-time integration that mirrors METAR/TAF
at every touch point, but for **area hazards** rather than airport-keyed points.
A SIGMET warns of a weather hazard (TURB/ICE/TS/MTW/VA...) over a FIR, bounded by
a polygon and a vertical band. There is **no model-comparison analog** — SIGMETs
are fetched and presented, not reconciled against NWP.

### Data shape (`models/observations.py`)

`SigmetAlongRoute` is the flat, serializable per-SIGMET record:
- Hazard fields: `fir_id`, `fir_name`, `hazard`, `qualifier` (SEV/EMBD/...), `base_ft`, `top_ft`, `valid_from/to`, `direction`, `speed_kt`, `raw_text`
- Route-intersection metadata: `matched_firs`, `min_distance_nm`, `enroute_distance_from_nm`, `enroute_distance_to_nm`
- **`coords`** — the polygon outline as `(lon, lat)` vertices

`RouteSigmets` aggregates: `corridor_nm`, `fetch_time`, `altitude_low_ft`/`altitude_high_ft`,
`time_window_from`/`to`, `route_firs`, `sigmets`, `hazards` (union), `has_severe`, `count`.
Surfaced on `ForecastSnapshot.route_sigmets`.

### Fetch (`run_route_sigmets`)

Calls euro_aip `RouteSigmetService().fetch_route_sigmets(route_icaos, corridor_nm,
model, altitude_band_ft, from_datetime, to_datetime)` and maps `RouteSigmetResult` →
`RouteSigmets`. Two derived inputs:
- **Altitude band** = `(0, cruise_altitude_ft + 5000)` (`_sigmet_altitude_band`) — surface
  to cruise plus a climb/descent buffer; high-FL-only hazards irrelevant to a GA route
  are dropped. SIGMETs with unknown bounds always surface (`overlaps_altitude` is permissive).
- **Time window** = `(now, max(end of departure day UTC, arrival + 3 h))`
  (`_departure_day_window`) — wider than the flight window so SIGMETs issued/expiring
  around the flight still surface. The arrival bound covers flights that cross 00:00Z:
  the departure day alone dropped SIGMETs for the airborne part after midnight, and
  the window collapsed while the live tick still ran (to arrival + 1 h), so every
  briefing SIGMET read as "no longer active".

Default corridor is `BriefingOptions.sigmet_corridor_nm = 50` (wider than METAR/TAF's 30,
since SIGMET areas are large).

### Real-time refresh seam

`run_realtime_refresh` fetches SIGMETs **alongside** METAR/TAF and returns a
`RealtimeRefreshResult{observations, sigmets, delta, …}`, committing them to the flight's
live layer (the pack is not patched since #637; a `None` SIGMET block keeps the stored one).
The SIGMET fetch is wrapped in try/except so a SIGMET source failure never blocks the cheap
METAR/TAF refresh. Both
refresh-button endpoints (`refresh_briefing`, `refresh_briefing_stream`) and the standalone
`observations/refresh` endpoint carry `sigmets` in their responses (`RefreshAccepted.sigmets`,
SSE `complete` event, and the endpoint's `{observations, sigmets}` body respectively).

### Changes since the briefing (`tasks/live_significance.py`, #637)

The cheap refresh does **not** regenerate the LLM digest, so a freshly-appeared
hazard would otherwise show in the tables while the AI assessment stays silent.
Until #637 `compute_refresh_delta` closed that gap by diffing each refresh against
the *previous* one and reporting only worsening. It is replaced by
`classify_changes`, which diffs against the **briefing's own observations**, in
**both directions**: METAR category crossings (no hysteresis since §35), TAF-at-ETA
changes, SIGMET issued/escalated/no longer active (merged across FIRs, all alert
tier), and heavy radar echo / lightning on the route ahead — see
[meteorology-decisions.md §34–35](meteorology-decisions.md)
and [live-observation-layer.md](live-observation-layer.md). Messages stay
deterministic, language-neutral shorthand (no tokens, no per-locale text).
`last_refresh_delta` is still produced (the worsening half, `worsening_delta`) and
overlaid on the snapshot, so a client that only knows the old banner keeps
working. SIGMET identity is unchanged: FIR + parsed sequence id, falling back to
FIR + hazard + validity.

### Digest / Report / Web UI

- **Text digest** (`digest/text.py`): `--- SIGMETs Along Route ---` section.
- **LLM prompt** (`digest/prompt_builder.py`): `=== SIGMETs ALONG ROUTE ===` section.
- **HTML report** (`briefing.html`): SIGMET table (Hazard, FIR, Levels, Enroute, Move, raw).
- **Web UI** (`briefing-ui.ts:renderRouteSigmets`): SIGMET table with per-row info popup;
  `sigmets-section` in `web/briefing.html` (the app page — distinct from the
  same-named report template under `report/templates/`). Read-only — the observations Refresh button refreshes
  SIGMETs too (combined seam).

### iOS UI (`Views/Briefing/RouteSigmetsView.swift`)

Ported in #493, one release after the observations section it sits under. Slot:
the Advisory tab, after Observations and before Alternates, with a scroll-spy
anchor (`sigmets`, pill label "Hazards"). Reads `snapshot.route_sigmets` from the
standard snapshot endpoint — no iOS-specific endpoint and no backend change, since
`GET .../snapshot` already returns raw `briefing.json`. Before the port the block
was simply dropped at decode: `SnapshotResponse` had no field for it.

Simpler than its sibling: one row per bulletin, no comparison join, no axis
switcher — SIGMETs are presented, not reconciled.

| Width | Behaviour |
|---|---|
| Compact (iPhone) | 4 columns: Hazard, FIR, Levels, Enroute. Movement folds into the detail sheet |
| Regular (iPad) | The web's 5th column, Move (`STNR` when stationary), renders inline |

Movement is the column that gives way because the vertical band decides whether a
GA route is in the hazard at all, while "MOV NE 30kt" only refines the timing.

Parity notes:
- **Formatters mirror the web helpers exactly** (`sigmetLevel`/`sigmetBand`/
  `sigmetEnroute`), including floor-division flight levels (`FL097`, not `FL098`)
  and `?` for an unknown bound — an omitted `base_ft` is genuinely unknown, and
  rendering it as `SFC` would overstate the bulletin. They live on the DTO
  (`SigmetAlongRoute.levelBand` / `.enrouteLabel` / `.movementLabel` /
  `.headline`), so they are unit-testable outside SwiftUI.
- **Severe row highlight** is red rather than the observations table's amber, and
  `has_severe` raises a banner — the counterpart of the obs conflict banner.
- **Detail sheet** replaces the web's ⓘ popup: level band, movement, validity
  (aviation `DDHHMMZ`), route intersection, then the raw bulletin (selectable
  monospace) — which is the authoritative text.
- **Server computed fields** (`count`, `hazards`, `has_severe`) are decoded as
  optionals with local fallbacks, so a payload without them can't render a
  "0 SIGMETs" summary above a populated table.
- **Row identity** is `fir_id` + `raw_text`: one FIR routinely carries several
  concurrent SIGMETs, so keying on the FIR alone would collapse them into one row.
- **Polygon `coords` is decoded but unused** — kept on the DTO so the future
  map/cross-section overlay below doesn't need a second wire change.

Fixture-backed (`FixtureBriefingData.snapshot` carries two SIGMETs, one `SEV`), so
mock mode renders it; covered by `RouteSigmetsTests.swift` (decode/fallbacks/
formatters) and the XCUI journeys `testRouteSigmetsSectionRenders` /
`testRouteSigmetDetailSheet`, which assert the compact-vs-regular contract.

**Verification note**: D-0 only, like observations, so on-device verification needs
a same-day flight with a live SIGMET along the route. Fixture + XCUI coverage is
the practical check.

### Future cross-section / map overlay (not yet built)

The model deliberately retains the polygon `coords`, the `enroute_distance_from/to_nm`
span, and the `base_ft`/`top_ft` band so a later feature can highlight the impacted area:
- **Cross-section**: the enroute span maps to the X axis and the vertical band to the Y
  axis → draw the SIGMET as a rectangle/region on the cross-section.
- **Route map**: `coords` is a ready-to-render `(lon, lat)` polygon.

No re-fetch needed — everything required is already serialized on the snapshot.

### Gotchas

- **FIR data in the airports DB**: the FIR prefilter (`firs_along_route`) only catches
  polygon-less SIGMETs; SIGMETs *with* polygons match by geometry regardless, so a DB
  without FIR boundaries degrades gracefully (just drops the rare polygon-less SIGMET).
- **euro_aip dependency**: SIGMET support (`RouteSigmetService`) requires euro_aip
  `>=0.10.2`; the current pyproject pin is `>=0.15.1`. Earlier `0.9.x` PyPI releases
  lacked it. Dev installs euro_aip editable from `~/Developer/public/rzflight/euro_aip`.

## Future Extensions

- **METAR/TAF-specific advisories**: ceiling check, visibility check, obs-model conflict, TAF deterioration alerts
- **D-1 TAF fetch**: If TAF validity periods are detected to cover the next day, fetch on D-1 too
- **SIGMET cross-section/map overlay**: render the affected area using the retained polygon + enroute span + vertical band (see above)

## References

- Key code: `src/weatherbrief/tasks/route_weather.py` (incl. `run_realtime_refresh`, `run_route_sigmets`), `src/weatherbrief/models/observations.py`
- Changes since the briefing: `src/weatherbrief/tasks/live_significance.py:classify_changes`; live store `tasks/live_layer.py`
- Pipeline integration: `src/weatherbrief/pipeline.py` (step 3.5)
- Realtime seam + tiered gate: `tasks/route_weather.py:run_realtime_refresh`, `api/packs.py:refresh_observations` (thin wrapper), `api/packs.py:decide_refresh`; shared helper `tasks/artifacts.py:parse_target_time`
- Digest: `src/weatherbrief/digest/prompt_builder.py`, `src/weatherbrief/digest/text.py`
- Report: `src/weatherbrief/report/templates/briefing.html`, `src/weatherbrief/report/render.py`
- Web UI: `web/ts/managers/briefing-ui.ts` (`renderRouteSigmets`, `renderRefreshDelta`)
- iOS UI: `app/flyfun-weather/flyfun-weather/Views/Briefing/RouteObservationsView.swift`, `RouteSigmetsView.swift`; DTOs in `Models/API/SnapshotResponse.swift`; tests `flyfun-weatherTests/RouteObservationsTests.swift`, `RouteSigmetsTests.swift`
- Tests: `tests/test_route_weather.py` (incl. `TestRunRealtimeRefresh`), `tests/test_live_significance.py`, `tests/test_live_layer.py`, `tests/test_packs.py::TestDecideRefresh`
- euro_aip weather module: [briefing_weather.md](rzflight design doc)
