# Data Models

> Pydantic v2 models for routes, forecasts, analysis results, and snapshots

Models are organized in `src/weatherbrief/models/` package:
- `analysis.py` — route, forecast, and weather analysis models
- `storage.py` — `Flight`, `BriefingPackMeta`
- `__init__.py` — re-exports everything for backward-compatible imports

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

### RoutePoint

A point along a route — either a named waypoint or an interpolated point. Used by `fetch_multi_point()` and stored in `RouteCrossSection`.

```python
RoutePoint(lat=51.836, lon=-1.32, distance_from_origin_nm=0.0,
           waypoint_icao="EGTK", waypoint_name="Oxford Kidlington")
RoutePoint(lat=50.4, lon=0.5, distance_from_origin_nm=100.0)  # interpolated
```

- `waypoint_icao` / `waypoint_name` are set only for named waypoints; `None` for interpolated points
- `distance_from_origin_nm` is cumulative distance along the route

### RouteCrossSection

Cross-section forecast data along the full route for one model: `(model, route_points, fetched_at, point_forecasts)`. One `WaypointForecast` per route point, in the same order as `route_points`.

### ForecastSnapshot

Root object for one fetch run: `(route, target_date, fetch_date, days_out, forecasts, analyses, cross_sections)`. Serialized to JSON for persistence.

- `forecasts` contains only waypoint forecasts (used by analysis)
- `cross_sections` contains full route data per model (used for cross-section visualization)
- **Storage split**: `snapshot.json` excludes `cross_sections`; saved separately as `cross_section.json` to keep the snapshot lean for existing consumers
- `cross_sections` defaults to empty list for backward compatibility with old snapshots

## Analysis Models

### Wind & Comparison

| Model | Purpose | Key fields |
|-------|---------|------------|
| `WindComponent` | Headwind/crosswind decomposition | `headwind_kt` (+HW/-TW), `crosswind_kt` (+right/-left) |
| `ModelDivergence` | Cross-model comparison | `variable`, `spread`, `agreement` (GOOD/MODERATE/POOR) |

### Sounding Analysis Models

Full MetPy-based atmospheric analysis, computed per model per waypoint.

| Model | Purpose | Key fields |
|-------|---------|------------|
| `ThermodynamicIndices` | Profile-level indices | LCL/LFC/EL (pressure + altitude), CAPE (surface/MU/ML), CIN, lifted index, showalter, K-index, total totals, precipitable water, freezing/-10C/-20C levels, bulk shear 0-6km/0-1km |
| `DerivedLevel` | Per-pressure-level derived values | altitude_ft, temperature_c, dewpoint_c, wet_bulb_c, dewpoint_depression_c, theta_e_k, lapse_rate_c_per_km, relative_humidity_pct, omega_pa_s, w_fpm, richardson_number, bv_freq_squared_per_s2 |
| `EnhancedCloudLayer` | Cloud layer from dewpoint depression | base/top (ft + hPa), thickness, mean_temperature_c, coverage (SCT/BKN/OVC) |
| `IcingZone` | Grouped icing zone from wet-bulb | base/top (ft + hPa), risk, icing_type (RIME/MIXED/CLEAR), sld_risk, mean_wet_bulb_c |
| `ConvectiveAssessment` | Convective risk from indices | risk_level (NONE→EXTREME), CAPE/CIN, LCL/LFC/EL, bulk shear, severe_modifiers list |
| `VerticalMotionClass` | Enum: vertical motion profile type | QUIESCENT, SYNOPTIC_ASCENT, SYNOPTIC_SUBSIDENCE, CONVECTIVE, OSCILLATING, UNAVAILABLE |
| `CATRiskLevel` | Enum: clear-air turbulence risk | NONE, LIGHT, MODERATE, SEVERE |
| `CATRiskLayer` | CAT risk identified by Richardson number | base_ft, top_ft, base/top_pressure_hpa, richardson_number, risk |
| `VerticalMotionAssessment` | Vertical motion + turbulence | classification, max_omega_pa_s, max_w_fpm, max_w_level_ft, cat_risk_layers, convective_contamination |
| `SoundingAnalysis` | Container per model | indices, derived_levels, cloud_layers, icing_zones, convective, vertical_motion, cloud_cover_{low,mid,high}_pct |

### Altitude Advisories

| Model | Purpose | Key fields |
|-------|---------|------------|
| `VerticalRegime` | A vertical slice with uniform conditions | floor_ft, ceiling_ft, in_cloud, icing_risk, icing_type, cloud_cover_pct, cat_risk, strong_vertical_motion, label |
| `AltitudeAdvisory` | Actionable altitude recommendation | advisory_type, altitude_ft, feasible, reason, per_model_ft |
| `AltitudeAdvisories` | Complete altitude picture for a waypoint | regimes (per-model), advisories, cruise_in_icing, cruise_icing_risk |

### WaypointAnalysis

All analysis for one waypoint. Contains:
- `wind_components: dict[str, WindComponent]` — model → wind decomposition
- `sounding: dict[str, SoundingAnalysis]` — model → full sounding analysis
- `altitude_advisories: AltitudeAdvisories | None` — dynamic vertical regimes and altitude advisories
- `model_divergence: list[ModelDivergence]` — 15 metrics compared across models

### RoutePointAnalysis

Analysis for one route point (waypoint or interpolated). Same analysis data as `WaypointAnalysis` but keyed by point index along the route, with interpolated time based on distance/duration.

Fields: `point_index`, `lat`, `lon`, `distance_from_origin_nm`, `waypoint_icao`, `waypoint_name`, `interpolated_time`, `forecast_hour`, `track_deg`, `wind_components`, `sounding`, `altitude_advisories`, `model_divergence`.

### RouteAnalysesManifest

Container for all route point analyses, saved as `route_analyses.json` in the pack directory.

Fields: `route_name`, `target_date`, `departure_time`, `flight_duration_hours`, `total_distance_nm`, `cruise_altitude_ft`, `models`, `analyses: list[RoutePointAnalysis]`.

## API / Web Models

### Flight

A saved briefing target — route + date/time specifics. ID is `{route_name}-{target_date}-{hash}` where hash encodes time/altitude/duration to allow same route+date with different params.

```python
Flight(
    id="egtk_lsgs-2026-02-21-a1b2c3",
    user_id="dev-user-001",
    route_name="egtk_lsgs",
    waypoints=["EGTK", "LFPB", "LSGS"],  # ICAO codes
    target_date="2026-02-21",
    target_time_utc=9,
    cruise_altitude_ft=8000,
    flight_ceiling_ft=18000,
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
- `IcingType`: `NONE`, `RIME`, `MIXED`, `CLEAR`
- `CloudCoverage`: `SCT`, `BKN`, `OVC`
- `ConvectiveRisk`: `NONE`, `LOW`, `MODERATE`, `HIGH`, `EXTREME`
- `AgreementLevel`: `GOOD`, `MODERATE`, `POOR`
- `VerticalMotionClass`: `QUIESCENT`, `SYNOPTIC_ASCENT`, `SYNOPTIC_SUBSIDENCE`, `CONVECTIVE`, `OSCILLATING`, `UNAVAILABLE`
- `CATRiskLevel`: `NONE`, `LIGHT`, `MODERATE`, `SEVERE`

## Patterns

- All Optional fields default to `None` — weather APIs have variable coverage per model
- Analysis results keyed by model name string (e.g., `sounding["gfs"]`)
- `Field(default_factory=list)` for all collection fields
- `bearing_between()` is a module-level function (not on Waypoint) since it takes two waypoints
- Sounding models use `Optional[float]` throughout — MetPy computations may fail for individual fields

## Gotchas

- `headwind_kt` positive = headwind, negative = tailwind (not intuitive for display)
- `crosswind_kt` positive = from right, negative = from left
- Pressure level data ordered surface→altitude (1000→300 hPa) but not guaranteed by API
- `at_time()` returns closest hour by absolute time difference — no interpolation
- Pint units must not leak beyond `analysis/sounding/` subpackage — causes Pydantic serialization issues

## References

- Route loading: `config.py`, `airports.py`
- Analysis consumers: [analysis.md](./analysis.md)
- Snapshot persistence: `storage/snapshots.py`
- Flight/pack storage: `storage/flights.py`
- API response models: `api/flights.py`, `api/packs.py`
