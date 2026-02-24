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
└── run_observation_comparison() ← compare obs vs model predictions
      _interpolate_airport_time()← per-airport time for model lookup
      classify_flight_category() from analysis/airport_conditions.py

models/observations.py
├── AirportObservation     ← per-airport METAR/TAF data (flat, serializable)
├── ObservationComparison  ← obs-vs-model comparison result
└── RouteObservations      ← collection + summary stats
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

**Model ceiling**: When `route_analyses` are provided, model flight category uses `sounding_ceiling_ft` from the nearest `RoutePointAnalysis` (via `classify_flight_category(ceiling, visibility)`). This allows ceiling-driven IFR comparisons. Falls back to visibility-only when route analyses are unavailable.

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
| `WeatherAnalyzer.find_applicable_taf()` | `euro_aip.briefing.weather.analysis` |
| `DatabaseStorage.load_model()` | `euro_aip.storage.database_storage` |
| `classify_flight_category()` | `weatherbrief.analysis.airport_conditions` |

## Pipeline Options

```python
# BriefingOptions
metar_taf_corridor_nm: float = 30  # corridor half-width in NM

# BriefingUsage
metar_taf_fetched: bool = False
metar_taf_airports: int = 0
```

## Gotchas

- **`datetime.utcnow()`** is used for `fetch_time` — matches the codebase's naive-UTC convention from Open-Meteo
- **First model forecast** is used as reference for comparison (`wp_forecasts[0]`) — this is typically `best_match` or the first model in the list
- **aviationweather.gov** has a 400-ICAO batch limit — handled by `AvWxSource` internally
- **Not all airports report METARs** — small GA fields found by spatial query may have no data; the summary notes coverage

## Future Extensions

- **METAR/TAF-specific advisories**: ceiling check, visibility check, obs-model conflict, TAF deterioration alerts
- **D-1 TAF fetch**: If TAF validity periods are detected to cover the next day, fetch on D-1 too

## References

- Key code: `src/weatherbrief/tasks/route_weather.py`, `src/weatherbrief/models/observations.py`
- Pipeline integration: `src/weatherbrief/pipeline.py` (step 3.5)
- Digest: `src/weatherbrief/digest/prompt_builder.py`, `src/weatherbrief/digest/text.py`
- Report: `src/weatherbrief/report/templates/briefing.html`, `src/weatherbrief/report/render.py`
- Tests: `tests/test_route_weather.py` (21 tests)
- euro_aip weather module: [briefing_weather.md](rzflight design doc)
