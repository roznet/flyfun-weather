# Data Models

> Pydantic v2 models for routes, forecasts, analysis results, and snapshots

All models live in `src/weatherbrief/models.py`.

## Intent

Single source of truth for all data structures. Pydantic v2 gives us validation on construction, JSON serialization for snapshots, and `Optional` fields to handle the variability of weather API responses.

## Core Models

### RouteConfig

Flight route with 2+ waypoints, cruise altitude, duration.

```python
route = RouteConfig(
    name="Oxford to Sion",
    waypoints=[wp_egtk, wp_lfpb, wp_lsgs],
    cruise_altitude_ft=8000,
    flight_duration_hours=4.5,
)
route.origin              # → wp_egtk
route.destination         # → wp_lsgs
route.cruise_pressure_hpa # → 752 (standard atmosphere)
route.leg_bearing(0)      # → bearing EGTK→LFPB
route.waypoint_track("LFPB")  # → circular mean of incoming/outgoing legs
```

- `cruise_pressure_hpa` computed via barometric formula (troposphere only)
- `waypoint_track()` returns circular mean of adjacent leg bearings — used for wind component analysis

### Waypoint

Simple `(icao, name, lat, lon)`. Resolved from ICAO codes via `airports.resolve_waypoints()` using euro_aip database.

### WaypointForecast

One model's forecast for one waypoint: `(waypoint, model, fetched_at, hourly: list[HourlyForecast])`. Use `at_time(datetime)` to find closest hour.

### HourlyForecast

Single timestep with 17 optional surface fields + `pressure_levels: list[PressureLevelData]`. Use `level_at(pressure_hpa)` for quick lookup.

### ForecastSnapshot

Root object for one fetch run: `(route, target_date, fetch_date, days_out, forecasts, analyses)`. Serialized to JSON for persistence.

## Analysis Models

| Model | Purpose | Key fields |
|-------|---------|------------|
| `WindComponent` | Headwind/crosswind decomposition | `headwind_kt` (+HW/-TW), `crosswind_kt` (+right/-left) |
| `IcingBand` | Icing at one pressure level | `risk` (NONE/LIGHT/MODERATE/SEVERE), `altitude_ft`, `temperature_c` |
| `CloudLayer` | Estimated cloud layer | `base_ft`, `top_ft` |
| `ModelDivergence` | Cross-model comparison | `variable`, `spread`, `agreement` (GOOD/MODERATE/POOR) |
| `WaypointAnalysis` | All analysis for one waypoint | dicts of model→WindComponent, model→IcingBands, model→CloudLayers, plus divergence list |

## API / Web Models

### Flight

A saved briefing target — route + date/time specifics. ID is `{route_name}-{target_date}` (one flight per route per day, by design).

```python
Flight(
    id="egtk_lsgs-2026-02-21",
    route_name="egtk_lsgs",
    waypoints=["EGTK", "LFPB", "LSGS"],  # ICAO codes
    target_date="2026-02-21",
    target_time_utc=9,
    cruise_altitude_ft=8000,
    flight_duration_hours=4.5,
    created_at=datetime(...),
)
```

### BriefingPackMeta

Lightweight metadata for one fetch — used for history listing without loading full snapshot.

```python
BriefingPackMeta(
    flight_id="egtk_lsgs-2026-02-21",
    fetch_timestamp="2026-02-19T18:00:00+00:00",
    days_out=2,
    has_gramet=True, has_skewt=True, has_digest=True,
    assessment="GREEN",
    assessment_reason="Conditions favorable",
)
```

Stored in `pack.json` alongside artifacts. `assessment` and `assessment_reason` are denormalized from the digest for quick display.

## Enums

- `ModelSource`: `BEST_MATCH`, `GFS`, `ECMWF`, `ICON`, `UKMO`, `METEOFRANCE`
- `IcingRisk`: `NONE`, `LIGHT`, `MODERATE`, `SEVERE`
- `AgreementLevel`: `GOOD`, `MODERATE`, `POOR`

## Patterns

- All Optional fields default to `None` — weather APIs have variable coverage per model
- Analysis results keyed by model name string (e.g., `wind_components["gfs"]`)
- `Field(default_factory=list)` for all collection fields
- `bearing_between()` is a module-level function (not on Waypoint) since it takes two waypoints

## Gotchas

- `headwind_kt` positive = headwind, negative = tailwind (not intuitive for display)
- `crosswind_kt` positive = from right, negative = from left
- Pressure level data ordered surface→altitude (1000→300 hPa) but not guaranteed by API
- `at_time()` returns closest hour by absolute time difference — no interpolation

## References

- Route loading: `config.py`, `airports.py`
- Analysis consumers: [analysis.md](./analysis.md)
- Snapshot persistence: `storage/snapshots.py`
- Flight/pack storage: `storage/flights.py`
- API response models: `api/flights.py`, `api/packs.py`
