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
      patch route_observations back into briefing.json

models/observations.py
├── AirportObservation     ← per-airport METAR/TAF data (flat, serializable)
├── ObservationComparison  ← obs-vs-model comparison result
├── RouteObservations      ← collection + summary stats
├── SigmetAlongRoute       ← per-SIGMET record (issue #168)
├── RouteSigmets           ← SIGMET collection + computed count/hazards/has_severe
├── RefreshDelta           ← deterministic "conditions worsened" diff (no LLM)
└── RealtimeRefreshResult  ← {observations, sigmets, delta} from the refresh seam

tasks/refresh_delta.py
└── compute_refresh_delta() ← diff old vs new obs+sigmets → RefreshDelta (worsening only)
```

### Pipeline Position (step 3.5)

```
1. run_fetch()         → NWP model data
2. run_analysis()      → sounding, wind, model comparison
3. run_advisories()    → route hazard assessment
3.5 run_route_weather() + run_observation_comparison()  ← D-0 only
4. Build snapshot      → route_observations stored on ForecastSnapshot
5-8. GRAMET, Skew-T, LLM digest, text digest
```

Gated by `days_out == 0 and options.airports_db_path`. Wrapped in try/except so pipeline continues if aviationweather.gov is down.

## Key Components

### Data Models (`models/observations.py`)

`AirportObservation` stores flat, serializable METAR/TAF fields (no euro_aip `WeatherReport` objects):
- METAR: raw text, flight category, ceiling, visibility, wind (dir/speed/gust), weather phenomena, temp, dewpoint, QNH
- TAF: raw text, flight category at ETA, applicable trend type, wind (dir/speed/gust)
- Wind advisories: `metar_wind_advisory`, `taf_wind_advisory` (OK/CAUTION/WARNING) with best runway and crosswind values
- TAF highlighting: `taf_applicable_lines: list[int]` — line indices for base + applicable BECMG/TEMPO groups
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
5. For TAFs, use `WeatherAnalyzer.find_applicable_taf(taf, airport_time)` to get active group at the interpolated ETA (not departure)
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

**Model ceiling**: When `route_analyses` are provided, the model ceiling is derived via `reconcile_ceiling(sounding, hourly)` (same path the advisory system uses — sounding ceiling reconciled against NWP cloud diagnostics) on the nearest `RoutePointAnalysis`'s per-model sounding, then fed to `classify_flight_category(ceiling_ft, visibility_sm)`. This allows ceiling-driven IFR comparisons. Falls back to visibility-only when route analyses are unavailable.

### Real-time refresh seam (`run_realtime_refresh`)

`run_realtime_refresh(pack_dir, db_path)` is the **cheap** refresh path (issue #167 Part A): re-fetch METAR/TAF (and route SIGMETs, see below), recompute the comparison from a pack's **stored** forecasts, then patch `route_observations`, `route_sigmets`, and `last_refresh_delta` back into `briefing.json`. **No** model fetch, **no** GRIB, **no** LLM. It reads `briefing.json` (route + stored `corridor_nm` + target time via `parse_target_time`), `forecasts.json`, and `route_analyses.json` off disk, calls `run_route_weather()` + `run_observation_comparison()` + `run_route_sigmets()`, computes a `RefreshDelta` (see below), and writes the result back. Returns a `RealtimeRefreshResult{observations, sigmets, delta}`. Raises `FileNotFoundError` if the pack has no briefing data.

Two callers share this seam:
- `POST .../observations/refresh` — the standalone METAR/TAF refresh button (a thin endpoint wrapper that adds auth + the D-0 400 guard).
- The tiered refresh gate's `realtime` mode (`api/packs.decide_refresh`) — when a D-0 manual refresh isn't worth a full pipeline run, both refresh-button endpoints invoke `run_realtime_refresh` instead so a D-0 press is always at least cheap-useful. See [freshness-markers.md](freshness-markers.md) for the gate.

### Digest Integration

**LLM prompt** (`digest/prompt_builder.py`): `=== METAR/TAF OBSERVATIONS ===` section between MODEL COMPARISON and TEXT FORECASTS. Includes per-airport METAR raw + category, TAF at ETA, and comparison annotations for non-confirming airports.

**Text digest** (`digest/text.py`): `--- METAR/TAF Observations ---` section with summary stats, per-airport METAR/TAF lines, and conflict flags.

### Web UI (`briefing-ui.ts`)

Observations section on the briefing page with:
- **Summary bar**: airport count, worst flight category, phenomena list, refresh button (D-0 only)
- **Two-row grouped table headers**: ICAO, Dist, ETA (+0h/+1h/etc.), Conditions group (METAR/TAF/Model categories + agreement) and Wind group (METAR/TAF/Model wind + advisory match)
- **Info button**: ⓘ in ICAO cell opens detailed airport popup
- **TAF highlighting**: `taf_applicable_lines` indices highlight the base forecast + applicable BECMG/TEMPO lines in the TAF raw text
- **Wind advisory icons**: OK/CAUTION/WARNING badges with crosswind values per source
- **Agreement column**: CONFIRMING/SIGNIFICANT/CONFLICTING badges for both conditions and wind
- **Refresh button**: re-fetches METAR/TAF via `POST .../observations/refresh` endpoint, updates snapshot in place

### HTML Report

Table after airport conditions with columns: ICAO, Distance, ETA, METAR Cat, TAF Cat, Model Cat, Match, METAR raw text. Flight categories are color-coded. Conflicting rows are highlighted amber.

## Key Choices

| Decision | Rationale |
|----------|-----------|
| D-0 only (not D-1/D-2) | European TAFs rarely cover next day; METARs are stale for planning |
| Flat Pydantic models, not euro_aip dataclasses | Serializable to JSON for snapshot storage and template rendering |
| Category-based comparison (not raw values) | Flight category is the operationally meaningful unit; raw value comparison is noisy |
| Three-tier classification (no MINOR_DELTA) | Implemented as CONFIRMING/SIGNIFICANT/CONFLICTING; MINOR_DELTA was dropped for simplicity |
| Sounding ceiling for model category | Uses `sounding_ceiling_ft` from route analyses when available, falling back to visibility-only |
| Runway crosswind advisory | `compute_wind_advisory()` evaluates all runway ends, picks best runway; OK/CAUTION/WARNING thresholds |
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
| `classify_flight_category()`, `reconcile_ceiling()` | `weatherbrief.analysis.airport_conditions` |

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
- **Time window** = `(now, end of departure day UTC)` (`_departure_day_window`) — the
  departure-day window; wider than the flight window so SIGMETs issued/expiring around
  the flight still surface.

Default corridor is `BriefingOptions.sigmet_corridor_nm = 50` (wider than METAR/TAF's 30,
since SIGMET areas are large).

### Real-time refresh seam

`run_realtime_refresh` fetches SIGMETs **alongside** METAR/TAF and returns a
`RealtimeRefreshResult{observations, sigmets, delta}`, patching `route_observations`,
`route_sigmets`, and `last_refresh_delta` into `briefing.json`. The SIGMET fetch is wrapped
in try/except so a SIGMET source failure never blocks the cheap METAR/TAF refresh. Both
refresh-button endpoints (`refresh_briefing`, `refresh_briefing_stream`) and the standalone
`observations/refresh` endpoint carry `sigmets` in their responses (`RefreshAccepted.sigmets`,
SSE `complete` event, and the endpoint's `{observations, sigmets}` body respectively).

### Refresh worsening delta (`tasks/refresh_delta.py`)

The cheap refresh re-fetches obs+SIGMETs but does **not** regenerate the LLM digest, so a
freshly-appeared hazard would otherwise show in the tables while the AI assessment stays
silent. `compute_refresh_delta(old_obs, new_obs, old_sigmets, new_sigmets)` closes that gap
deterministically (no tokens): it diffs the previous on-disk state against the new one and
reports **only what got worse** — degraded flight category, new/escalated SIGMETs — as a
`RefreshDelta{worsened, messages, computed_at}`. Improvements are intentionally not reported
(the banner only warns). `messages` use language-neutral aviation shorthand (ICAO, flight
categories, FIR/SIGMET ids) so they need no per-locale translation. SIGMET identity across
refreshes is keyed by FIR + parsed sequence id (falling back to FIR + hazard + validity) so a
re-issued SIGMET isn't mistaken for new. The delta is **always** persisted as
`last_refresh_delta` (even when nothing worsened) so a stale banner from a prior refresh
clears on the next load. The web UI (`briefing-ui.ts:renderRefreshDelta` →
`refresh-delta-banner`) shows the banner when `worsened` and there are messages.

### Digest / Report / Web UI

- **Text digest** (`digest/text.py`): `--- SIGMETs Along Route ---` section.
- **LLM prompt** (`digest/prompt_builder.py`): `=== SIGMETs ALONG ROUTE ===` section.
- **HTML report** (`briefing.html`): SIGMET table (Hazard, FIR, Levels, Enroute, Move, raw).
- **Web UI** (`briefing-ui.ts:renderRouteSigmets`): SIGMET table with per-row info popup;
  `sigmets-section` in `briefing.html`. Read-only — the observations Refresh button refreshes
  SIGMETs too (combined seam).

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
  `>=0.10.2` (the current pyproject pin). Earlier `0.9.x` PyPI releases lacked it. Dev
  installs euro_aip editable from `~/Developer/public/rzflight/euro_aip`.

## Future Extensions

- **METAR/TAF-specific advisories**: ceiling check, visibility check, obs-model conflict, TAF deterioration alerts
- **D-1 TAF fetch**: If TAF validity periods are detected to cover the next day, fetch on D-1 too
- **SIGMET cross-section/map overlay**: render the affected area using the retained polygon + enroute span + vertical band (see above)

## References

- Key code: `src/weatherbrief/tasks/route_weather.py` (incl. `run_realtime_refresh`, `run_route_sigmets`), `src/weatherbrief/models/observations.py`
- Worsening delta: `src/weatherbrief/tasks/refresh_delta.py:compute_refresh_delta`
- Pipeline integration: `src/weatherbrief/pipeline.py` (step 3.5)
- Realtime seam + tiered gate: `tasks/route_weather.py:run_realtime_refresh`, `api/packs.py:refresh_observations` (thin wrapper), `api/packs.py:decide_refresh`; shared helper `tasks/artifacts.py:parse_target_time`
- Digest: `src/weatherbrief/digest/prompt_builder.py`, `src/weatherbrief/digest/text.py`
- Report: `src/weatherbrief/report/templates/briefing.html`, `src/weatherbrief/report/render.py`
- Web UI: `web/ts/managers/briefing-ui.ts` (`renderRouteSigmets`, `renderRefreshDelta`)
- Tests: `tests/test_route_weather.py` (incl. `TestRunRealtimeRefresh`), `tests/test_refresh_delta.py`, `tests/test_packs.py::TestDecideRefresh`
- euro_aip weather module: [briefing_weather.md](rzflight design doc)
