# Data Models

> Pydantic v2 models for routes, forecasts, analysis results, and snapshots

Models are organized in `src/weatherbrief/models/` package:
- `analysis.py` — route, forecast, and weather analysis models
- `storage.py` — `Flight`, `FlightProfile`, `BriefingPackMeta`
- `advisories.py` — route advisory models (status, results, catalog, manifest)
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

### PressureLevelData

Per-level NWP data. Core fields: `pressure_hpa`, `temperature_c`, `dewpoint_c`, `relative_humidity_pct`, `wind_speed_kt`, `wind_direction_deg`, `geopotential_height_m`. Optional GRIB2-enriched fields: `cloud_liquid_water_kg_kg` (CLWMR), `ice_mixing_ratio_kg_kg` (ICMR) — populated by GRIB2 enrichment when enabled.

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
- **Storage split**: Snapshot is saved as two files plus a separate cross-section file:
  - `briefing.json` — everything *except* `forecasts` and `cross_sections` (route, analyses, observations, metadata). This is what the `/snapshot` API endpoint serves.
  - `forecasts.json` — `route` + `target_date` + `fetch_date` + `days_out` + `forecasts` only. Large file (~5-10 MB with all pressure levels), loaded only when raw forecasts are needed (e.g. Skew-T generation, on-demand analysis).
  - `cross_section.json` — `cross_sections` only (full route, all models). Loaded for cross-section visualization.
- `cross_sections` defaults to empty list for backward compatibility with old snapshots
- **Legacy fallback**: Old packs may have a single `snapshot.json` — load helpers (`load_briefing()`, `load_forecasts()`) fall back to it automatically

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
| `ThermodynamicIndices` | Profile-level indices | LCL/LFC/EL (pressure + altitude), CAPE (surface/MU/ML), CIN, lifted index, showalter, K-index, total totals, precipitable water, freezing/-10C/-20C levels, bulk shear 0-6km/0-1km. **Raw NWP values:** nwp_cape_jkg, nwp_cape_type (sb/ml/mu/unknown), nwp_cin_jkg, nwp_lifted_index, nwp_freezing_level_ft, cape_raw_vs_calc_divergent |
| `DerivedLevel` | Per-pressure-level derived values | altitude_ft, temperature_c, dewpoint_c, wet_bulb_c, dewpoint_depression_c, theta_e_k, lapse_rate_c_per_km, relative_humidity_pct, omega_pa_s, w_fpm, richardson_number, bv_freq_squared_per_s2, cloud_liquid_water_g_m3, cloud_liquid_water_g_kg, ice_mixing_ratio_g_kg, icing_index (Ogimet-DD 0–100), icing_index_nwp (Ogimet-NWP 0–100), sfip_raw, sfip_100, sfip_severity, sfip_variant, clw_interpolated |
| `EnhancedCloudLayer` | Cloud layer from dewpoint depression or NWP diagnostics | base/top (ft + hPa), thickness, mean_temperature_c, coverage (SCT/BKN/OVC), source ("dd"/"grib"/"synthesized") |
| `InversionLayer` | Temperature inversion from lapse rate | base/top (ft + hPa), strength_c, surface_based |
| `IcingZone` | Grouped icing zone (Ogimet-DD or Ogimet-NWP) | base/top (ft + hPa), risk, icing_type (RIME/MIXED/CLEAR), sld_risk, mean_temperature_c, mean_wet_bulb_c, mean_rh_pct, mean_icing_index |
| `SfipZone` | Grouped SFIP icing zone | base/top (ft + hPa), risk, icing_type, mean_sfip_100, mean_temperature_c, mean_rh_pct, variant ("full"/"full_no_vv"/"interp"/"interp_no_vv"/"proxy"/"proxy_no_vv") |
| `ConvectiveAssessment` | Convective risk from indices | risk_level (NONE→EXTREME), CAPE/CIN, LCL/LFC/EL, bulk shear, severe_modifiers list, base_ft/top_ft (unified bounds), cover_pct (NWP only), method ("thermo"/"nwp"/"nwp_hybrid") |
| `VerticalMotionClass` | Enum: vertical motion profile type | QUIESCENT, SYNOPTIC_ASCENT, SYNOPTIC_SUBSIDENCE, CONVECTIVE, OSCILLATING, UNAVAILABLE |
| `CATRiskLevel` | Enum: clear-air turbulence risk | NONE, LIGHT, MODERATE, SEVERE |
| `CATRiskLayer` | CAT risk identified by Richardson number | base_ft, top_ft, base/top_pressure_hpa, richardson_number, risk |
| `VerticalMotionAssessment` | Vertical motion + turbulence | classification, max_omega_pa_s, max_w_fpm, max_w_level_ft, cat_risk_layers, convective_contamination |
| `SoundingAnalysis` | Container per model | indices, derived_levels, cloud_layers, dd_cloud_layers, nwp_cloud_layers, icing_zones, icing_ogimet_dd_zones, icing_ogimet_nwp_zones, sfip_zones, inversion_layers, convective, vertical_motion, cloud_cover_{low,mid,high}_pct, cloud_method_effective. **Field semantics:** `cloud_layers`/`icing_zones` are the "active" slots (resolved per user preference by `_resolve_analyses()`). `dd_cloud_layers`/`icing_ogimet_dd_zones` are immutable DD sources (excluded from JSON, reconstructed by validator on load). `cloud_method_effective` records which method was actually applied: "dd", "nwp" (GRIB), or "nwp_synthesized" (Open-Meteo + DD heuristics). All downstream consumers read only the active slots. |

### Altitude Advisories

| Model | Purpose | Key fields |
|-------|---------|------------|
| `VerticalRegime` | A vertical slice with uniform conditions | floor_ft, ceiling_ft, in_cloud, icing_risk, icing_type, cloud_coverage, cat_risk, strong_vertical_motion, label + diagnostic fields (mean_temperature_c, mean_dewpoint_depression_c, mean_wet_bulb_c, mean_rh_pct, mean_icing_index, sld_risk, inversion_strength_c, inversion_surface_based) |
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

### Elevation Profile

| Model | Purpose | Key fields |
|-------|---------|------------|
| `ElevationPoint` | Single terrain sample along route | distance_nm, elevation_ft, lat, lon |
| `ElevationProfile` | High-resolution terrain profile | route_name, points, max_elevation_ft, total_distance_nm, spacing_nm |

Saved as `elevation_profile.json` in the pack directory. ~800 points for a 400nm route at 0.5nm spacing.

## API / Web Models

### FlightProfile

Named parameter template for flights. Stores all flight + advisory settings as flexible JSON.

```python
FlightProfile(
    id=1,
    user_id="dev-user-001",
    name="VFR Training",
    is_default=True,
    settings={
        "cruise_altitude_ft": 8000,
        "flight_ceiling_ft": 18000,
        "speed_kt": 120,
        "models": ["gfs", "ecmwf", "icon"],
        "advisory_models": ["gfs", "ecmwf"],
        "gramet_enabled": True,
        "llm_digest_enabled": False,
        "icing_severity_enhance": False,
        "icing_method": "ogimet_dd",       # "ogimet_dd" | "ogimet_nwp" | "sfip_nwp"
        "cloud_method": "dd",              # "dd" | "nwp"
        "advisories": {
            "enabled": {"icing_escape": True},
            "params": {"icing_escape": {"terrain_margin_ft": 1000}},
            "aggregation": "majority"      # "worst" | "majority"
        }
    },
)
```

- One default profile per user (auto-created on first access, migrates legacy `defaults_json`)
- Settings applied dynamically at briefing refresh time — not stored on Flight
- Flexible JSON allows adding new settings without migrations

### Flight

A saved briefing target — route + departure time specifics. ID is `{route_name}-{target_date}-{hash}` where hash encodes time/altitude/duration to allow same route+date with different params.

```python
Flight(
    id="egtk_lsgs-2026-02-21-a1b2c3",
    user_id="dev-user-001",
    route_name="egtk_lsgs",
    waypoints=["EGTK", "LFPB", "LSGS"],  # ICAO codes
    departure_time=datetime(2026, 2, 21, 9, tzinfo=timezone.utc),
    cruise_altitude_ft=8000,
    flight_ceiling_ft=18000,
    flight_duration_hours=4.5,
    profile_id=1,  # optional FK → FlightProfile, SET NULL on delete
    aircraft_id=3,  # optional FK → UserAircraftRow, SET NULL on delete
    created_at=datetime(...),
)
# Backward-compat computed fields (auto-derived from departure_time):
flight.target_date      # → "2026-02-21"
flight.target_time_utc  # → 9
```

- `departure_time` is the canonical field — a single aware-UTC datetime
- `target_date` and `target_time_utc` are `@computed_field` properties for backward compatibility (used by email, admin, digest, and frontend display code)
- `aircraft_id` links to a user's aircraft (see `multi-user-deployment.md` for schema). Independent from `profile_id` — aircraft provides physical defaults (speed, ceiling), profile provides mission preferences.

### BriefingPackMeta

Lightweight metadata for one fetch — used for history listing without loading full snapshot.

```python
BriefingPackMeta(
    flight_id="egtk_lsgs-2026-02-21",
    fetch_timestamp=datetime(2026, 2, 19, 18, 0, 0, tzinfo=timezone.utc),
    days_out=2,
    has_gramet=True, has_skewt=True, has_digest=True,
    assessment="GREEN",
    assessment_reason="Conditions favorable",
    model_init_times={"gfs": 1708300800, "ecmwf": 1708300800},
    grib_init_times={"gfs": 1708344000},
)
```

Stored in `pack.json` alongside artifacts. `fetch_timestamp` is a timezone-aware UTC datetime (stored as `DATETIME(6)` in MySQL, text in SQLite). `assessment` and `assessment_reason` are denormalized from the digest for quick display. `model_init_times` records the NWP model initialization timestamps at fetch time — used by the freshness check to determine if new model runs are available. `grib_init_times` records the initialization timestamps of GRIB2 data sources (GFS, ICON-EU) when they differ from the Open-Meteo init times — displayed in the freshness bar as "GFS 12Z (GRIB 18Z)".

## Route Advisory Models (`models/advisories.py`)

| Model | Purpose | Key fields |
|-------|---------|------------|
| `AdvisoryStatus` | Enum: GREEN, AMBER, RED, UNAVAILABLE | `worst()` classmethod for aggregation |
| `AdvisoryParameterDef` | Tunable parameter metadata | key, label, type (number/percent/altitude/speed/boolean), unit, default, min, max, step |
| `AdvisoryCatalogEntry` | Evaluator metadata for UI | id, name, short_description, description, category (icing/cloud/turbulence/convective/model), default_enabled, parameters |
| `ModelAdvisoryResult` | One advisory, one model | status, detail, affected_count, total_count, affected_pct, affected_nm. `build()` classmethod computes pct/nm from counts |
| `RouteAdvisoryResult` | Aggregate across models | advisory_id, aggregate_status, aggregate_detail, per_model list, parameters_used. `from_per_model()` classmethod aggregates (worst status, detail from worst model) |
| `RouteAdvisoriesManifest` | Top-level container | advisories, catalog, cruise_altitude_ft, flight_ceiling_ft, total_distance_nm, models |

See [advisories.md](./advisories.md) for the evaluator framework.

## Enums

- `ModelSource`: `GFS`, `ECMWF`, `ICON`, `UKMO`, `METEOFRANCE` (note: `BEST_MATCH` retained in enum for backward-compat deserialization but removed from `MODEL_ENDPOINTS`)
- `IcingRisk`: `NONE`, `LIGHT`, `MODERATE`, `SEVERE`
- `IcingType`: `NONE`, `RIME`, `MIXED`, `CLEAR`
- `CloudCoverage`: `SCT`, `BKN`, `OVC`
- `ConvectiveRisk`: `NONE`, `MARGINAL`, `LOW`, `MODERATE`, `HIGH`, `EXTREME`
- `AgreementLevel`: `GOOD`, `MODERATE`, `POOR`
- `VerticalMotionClass`: `QUIESCENT`, `SYNOPTIC_ASCENT`, `SYNOPTIC_SUBSIDENCE`, `CONVECTIVE`, `OSCILLATING`, `UNAVAILABLE`
- `CATRiskLevel`: `NONE`, `LIGHT`, `MODERATE`, `SEVERE`
- `AdvisoryStatus`: `GREEN`, `AMBER`, `RED`, `UNAVAILABLE`

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
