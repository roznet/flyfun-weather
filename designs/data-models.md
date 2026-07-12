# Data Models

> Pydantic v2 models for routes, forecasts, analysis results, and snapshots

Models are organized in `src/weatherbrief/models/` package:
- `analysis.py` — route, forecast, weather analysis, and route-solar (`RouteSunAnalysis` and friends) models
- `storage.py` — `Flight`, `FlightProfile`, `BriefingPackMeta`, `FlightDebrief`
- `advisories.py` — route advisory models (status, results, catalog, manifest, altitude table)
- `observations.py` — METAR/TAF/SIGMET route models, `RefreshDelta`, `RealtimeRefreshResult`
- `airport_conditions.py` — derived airport flight-category conditions (see advisories.md)
- `alternates.py` / `alternate_requirement.py` — weather-based divert candidates + regulatory "alternate required?" assessment (see alternates.md, alternate-requirement.md)
- `fronts.py` — per-briefing front-detection artifact (`route_fronts.json`; see frontal-detection.md)
- `diagnostic.py` / `diagnostic_codes.py` — structured pipeline events + stable codes
- `verification.py` — forecast-vs-observation verification records
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

Per-level NWP data. Core fields: `pressure_hpa`, `temperature_c`, `dewpoint_c`, `relative_humidity_pct`, `wind_speed_kt`, `wind_direction_deg`, `geopotential_height_m`, `vertical_velocity_pa_s` (omega). Optional GRIB2-enriched fields: `cloud_liquid_water_kg_kg` (CLWMR), `ice_mixing_ratio_kg_kg` (ICMR), `cloud_area_fraction_pct` (CLC from ICON-EU, 0–100), `clw_interpolated` (True when CLW filled by spatial interpolation) — populated by GRIB2 enrichment when enabled.

### HourlyForecast

Single timestep with ~22 optional surface fields + optional `nwp_cloud_diagnostics: NWPCloudDiagnostics` (GFS GRIB2 cloud layers) + `pressure_levels: list[PressureLevelData]`. Use `level_at(pressure_hpa)` for quick lookup.

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

Root object for one fetch run: `(route, target_date, fetch_date, days_out, departure_time, forecasts, analyses, cross_sections, route_observations, route_sigmets, alternates, last_refresh_delta)`. Serialized to JSON for persistence.

- `departure_time` is the aware-UTC departure (None for old packs)
- `route_observations` / `route_sigmets` carry METAR/TAF/SIGMET (`models/observations.py`)
- `alternates: RouteAlternates | None` — weather-based divert candidates, opt-in (D-2 inward); None outside that window (`models/alternates.py`, see alternates.md)
- `last_refresh_delta` holds the worsening summary from the last cheap real-time refresh; None after a full pipeline run

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
| `ModelDivergence` | Cross-model comparison | `variable`, nullable per-model `model_values`, nullable `mean`, `spread`, `agreement` (GOOD/MODERATE/POOR). `mean=None` means the metric is absent even when the compatibility agreement value is GOOD |

### Sounding Analysis Models

Full MetPy-based atmospheric analysis, computed per model per waypoint.

| Model | Purpose | Key fields |
|-------|---------|------------|
| `ThermodynamicIndices` | Profile-level indices | LCL/LFC/EL (pressure + altitude), CAPE (surface/MU/ML), CIN, lifted index, showalter, K-index, total totals, precipitable water, freezing/-10C/-20C levels, bulk shear 0-6km/0-1km. **Raw NWP values:** nwp_cape_jkg, nwp_cape_type (sb/ml/mu/unknown), nwp_cin_jkg, nwp_lifted_index, nwp_freezing_level_ft, cape_raw_vs_calc_divergent |
| `DerivedLevel` | Per-pressure-level derived values | altitude_ft, temperature_c, dewpoint_c, wet_bulb_c, dewpoint_depression_c, theta_e_k, lapse_rate_c_per_km, relative_humidity_pct, omega_pa_s, w_fpm, richardson_number, bv_freq_squared_per_s2, cloud_liquid_water_g_m3, cloud_liquid_water_g_kg, ice_mixing_ratio_g_kg, icing_index (Ogimet-DD 0–100), icing_index_nwp (Ogimet-NWP 0–100), sfip_raw, sfip_100, sfip_severity, sfip_variant ("full"/"proxy"/"interp"), clw_interpolated, precip_phase |
| `EnhancedCloudLayer` | Cloud layer from dewpoint depression or NWP diagnostics | base/top (ft + hPa), thickness, mean_temperature_c, coverage (FEW/SCT/BKN/OVC), mean_dewpoint_depression_c, mean_cloud_cover_pct, theoretical_max_top_ft, source (`"dd"`/`"grib"`/`"nwp_3d"`; legacy-compatible `"synthesized"` only) |
| `NWPCloudDiagnostics` | NWP-surface diagnostics (GRIB) | low/mid/high (NWPCloudLayerDiag each), convective_cover_pct, convective_base_ft, convective_top_ft, total_cover_pct, boundary_cover_pct, ceiling_ft, freezing_level_ft (ECMWF `deg0l`) |
| `InversionLayer` | Temperature inversion from lapse rate | base/top (ft + hPa), strength_c, surface_based |
| `IcingZone` | Grouped icing zone (Ogimet-DD or Ogimet-NWP) | base/top (ft + hPa), risk, icing_type (RIME/MIXED/CLEAR), sld_risk, mean_temperature_c, mean_wet_bulb_c, mean_rh_pct, mean_icing_index |
| `SfipZone` | Grouped SFIP icing zone | base/top (ft + hPa), risk, icing_type, mean_sfip_100, mean_temperature_c, mean_rh_pct, variant (`"full"`/`"full_no_vv"`/`"interp"`/`"interp_no_vv"`/`"proxy"`/`"proxy_no_vv"`) |
| `SldZone` | Supercooled Large Droplet zone | base/top (ft + hPa), risk, mechanism ("warm_nose"/"coalescence"), mean_temperature_c |
| `ConvectiveAssessment` | Convective risk from indices | risk_level (NONE→EXTREME), CAPE/CIN, LCL/LFC/EL, bulk shear, k_index, total_totals, severe_modifiers, regime (`ConvectiveRegime`), drivers/suppressors lists, elevated_convection, base_ft/top_ft (unified bounds), cover_pct (NWP only), method ("thermo"/"nwp"/"nwp_hybrid") |
| `ConvectiveRegime` | Enum: dominant convective regime | THERMAL, WEAK_INSTABILITY, LOADED_GUN, ACTIVE (`.label` → title-case) |
| `ParcelPathPoint` | Lifted parcel temp profile point | pressure_hpa, temperature_c |
| `PrecipitationAssessment` | Precip type + intensity for a sounding | surface_phase, surface_intensity, precipitation_zones, freezing_rain_risk, warm_nose_base/top_ft, rain_mm, snow_cm, total_mm |
| `PrecipitationZone` | Vertical zone of uniform precip phase | base/top (ft + hPa), phase (`PrecipPhase`), mean_wet_bulb_c, ice_fraction |
| `VerticalMotionClass` | Enum: vertical motion profile type | QUIESCENT, SYNOPTIC_ASCENT, SYNOPTIC_SUBSIDENCE, CONVECTIVE, OSCILLATING, UNAVAILABLE |
| `CATRiskLevel` | Enum: clear-air turbulence risk | NONE, LIGHT, MODERATE, SEVERE |
| `CATRiskLayer` | CAT risk identified by Richardson number | base_ft, top_ft, base/top_pressure_hpa, richardson_number, risk |
| `VerticalMotionAssessment` | Vertical motion + turbulence | classification, max_omega_pa_s, max_w_fpm, max_w_level_ft, cat_risk_layers, e_shear_layers, convective_contamination |
| `SoundingAnalysis` | Container per model | indices, parcel_path, derived_levels, cloud_layers, nwp_cloud_layers, dd_cloud_layers, icing_zones, icing_ogimet_dd_zones, icing_ogimet_nwp_zones, sfip_zones, ieng_icing_zones, sld_zones, inversion_layers, convective, convective_thermo, convective_nwp, precipitation, vertical_motion, cloud_cover_{low,mid,high}_pct, surface visibility_m/temperature_2m_c/dewpoint_2m_c, nwp_cloud_diagnostics, cloud_method_effective. **Field semantics:** `cloud_layers`/`icing_zones` are the "active" slots (resolved per user preference by `_resolve_analyses()`). `dd_cloud_layers`/`icing_ogimet_dd_zones` are immutable DD sources, reconstructed by the `_sync_dd_sources` validator on load. `nwp_cloud_layers=None` means no native NWP envelope is available; `[]` means the native source was assessed and found clear. `convective` is the active slot; `convective_thermo`/`convective_nwp` retain both derivations. `cloud_method_effective` is `"dd"` for DD or an NWP-request fallback to DD, `"nwp"` for native `grib`/`nwp_3d` geometry (including an available empty list), `"nwp_synthesized"` only for legacy synthesized geometry, and `None` for mixed/unknown provenance. All downstream consumers read only the active slots. |

### Airport condition availability

`AirportModelCondition.ceiling_evaluated` distinguishes an assessed clear sky
from a missing ceiling source. `ceiling_evaluated=True` with `ceiling_ft=None`
means the sounding or native NWP ceiling diagnostic ran and found no ceiling;
`ceiling_evaluated=False` with `ceiling_ft=None` means no ceiling source was
available. Airport and VFR evaluators use this flag together with visibility,
terminal-convection, wind, runway-component, and sounding availability so a
known hazard can survive partial input while an incomplete clear result becomes
`UNAVAILABLE`.

The frontend preserves the same distinction: an evaluated null ceiling renders
as `CLR`, an unevaluated null ceiling renders as `N/A`, and a row with no
ceiling/visibility evidence gets a muted `N/A` category rather than a VFR vote.
Independent wind evidence may still be displayed and reduced.

### Altitude Advisories

| Model | Purpose | Key fields |
|-------|---------|------------|
| `VerticalRegime` | A vertical slice with uniform conditions | floor_ft, ceiling_ft, in_cloud, icing_risk, icing_type, cloud_coverage, cat_risk, strong_vertical_motion, label + diagnostic fields (mean_temperature_c, mean_dewpoint_depression_c, mean_wet_bulb_c, mean_rh_pct, mean_icing_index, sld_risk, inversion_strength_c, inversion_surface_based) |
| `AltitudeAdvisory` | Actionable altitude recommendation | advisory_type, altitude_ft, feasible, reason, per_model_ft. For `descend_below_icing`, an icing-bearing freezing-rain model makes the aggregate altitude null/infeasible; an ordinary no-icing model's null does not veto another model's finite escape |
| `AltitudeAdvisories` | Complete altitude picture for a waypoint | regimes (per-model), advisories, cruise_in_icing, cruise_icing_risk |

### WaypointAnalysis

All analysis for one waypoint. Contains:
- `wind_components: dict[str, WindComponent]` — model → wind decomposition
- `sounding: dict[str, SoundingAnalysis]` — model → full sounding analysis
- `altitude_advisories: AltitudeAdvisories | None` — dynamic vertical regimes and altitude advisories
- `model_divergence: list[ModelDivergence]` — metrics compared across the model map. Individual values may be null and an all-null entry has `mean=None`; consumers must not treat its compatibility `agreement=GOOD` as assessed agreement

### RoutePointAnalysis

Analysis for one route point (waypoint or interpolated). Same analysis data as `WaypointAnalysis` but keyed by point index along the route, with interpolated time based on distance/duration.

Fields: `point_index`, `lat`, `lon`, `distance_from_origin_nm`, `waypoint_icao`, `waypoint_name`, `interpolated_time`, `forecast_hour`, `track_deg`, `wind_components`, `sounding`, `altitude_advisories`, `model_divergence`.

### RouteAnalysesManifest

Container for all route point analyses, saved as `route_analyses.json` in the pack directory.

Fields: `route_name`, `target_date`, `departure_time`, `flight_duration_hours`, `total_distance_nm`, `cruise_altitude_ft`, `models`, `analyses: list[RoutePointAnalysis]`, `sun: RouteSunAnalysis | None` (optional, old packs deserialize fine without it).

### Route Solar Analysis

`RouteSunAnalysis` (in `analysis.py`, issue #227) is precomputed solar readouts hung off `RouteAnalysesManifest.sun`. Fields:
- `night_intervals: list[NightInterval]` — twilight (civil, 0..−6°) vs night (<−6°) runs for cross-section shading
- `sun_side: SunSideSummary` — dominant sector (`left`/`right` for seating, `ahead`/`behind` for into-sun/sun-behind) + `dominant_side_pct` and per-stretch `segments` (`SunSideSegment`)
- `points: list[SunPoint]` — per-route-point sun elevation/azimuth/relative-bearing for the cross-section hover readout
- `takeoff` / `landing: GlareAssessment | None` — sun-vs-runway glare on the wind-best departure/arrival runway (`into_sun`, `is_dark`)

### RouteWindOverlay

`RouteWindOverlay` (`cruise_altitude_ft`, `points: list[RoutePointWindOverlay]`) carries per-point wind components recomputed at an override altitude, so route-graph/route-map headwind can be refreshed without regenerating the full analysis manifest. `RoutePointWindOverlay` is just `(point_index, wind_components)`.

### Elevation Profile

| Model | Purpose | Key fields |
|-------|---------|------------|
| `ElevationPoint` | Single terrain sample along route | distance_nm, elevation_ft, lat, lon |
| `ElevationProfile` | High-resolution terrain profile | route_name, points, max_elevation_ft, total_distance_nm |

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

- One default profile per user (auto-created on first access, migrating legacy settings out of `UserPreferencesRow.app_prefs_json`)
- Settings applied dynamically at briefing refresh time — not stored on Flight
- Flexible JSON allows adding new settings without migrations
- `system_template_key` (optional) marks a profile cloned from a built-in system template; `created_at`/`updated_at` are aware-UTC timestamps

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
- Additional fields: `private` (sharing opt-out), `alt_departure_time` (optional same-day alternate departure), `auto_refresh` / `auto_refresh_hour` / `last_auto_refresh_at` (scheduled refresh), `raw_route` + `parser_version` (original Field-15 string the pilot typed and the euro_aip version that derived `waypoints` from it; both NULL for iOS/MCP-created flights), `share_code` (short token for `/s/{code}` redirect, minted at save time)

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

Persisted as a `BriefingPackRow` in the DB (`_meta_to_row`/`_apply_meta_to_row` in `storage/flights.py`), not a `pack.json` file. `id` is the DB primary key. `fetch_timestamp` is a timezone-aware UTC datetime (stored as `DATETIME(6)` in MySQL, text in SQLite). `assessment` and `assessment_reason` are denormalized from the digest for quick display (`outlook`/`outlook_reason` replace them for long-range packs beyond the GRIB horizon — a soft TRENDING_SETTLED/MIXED_SIGNALS/TRENDING_UNSETTLED outlook, mutually exclusive with the traffic-light `assessment`). `model_init_times` records the NWP model initialization timestamps at fetch time — used by the freshness check to determine if new model runs are available. `grib_init_times` records the initialization timestamps of GRIB2 data sources (GFS, ICON-EU) when they differ from the Open-Meteo init times — displayed in the freshness bar as "GFS 12Z (GRIB 18Z)". `model_sources` maps each model to its freshness source key (e.g. `ecmwf:direct`).

Other fields: `artifact_path` (pack directory), `models_skipped_region` (models out of coverage), `llm_digest_requested` (whether the AI digest was requested for this pack; defaults True so legacy packs read as "still generating" not "off"), `digest_trace_id` (LangSmith root run id of the digest LLM call, #244; used by the feedback endpoint), `alt_assessment`/`alt_assessment_reason`/`has_alt_advisories` (optional same-day alternate departure), and DWD + Met Office surface-chart references (`{dwd,metoffice}_charts_run_cycle`/`_default_id`/`_in_coverage`/`_within_horizon`). `is_historical` is a `@computed_field` (true when `days_out < 0`).

`advisory_summary: AdvisorySummary | None` (#276) is a compact RED/AMBER breakdown (`red`/`amber` counts + a severity-ordered `top: list[AdvisoryChip]` capped at 3, each a `status`+`name`) denormalized at briefing-build time via `tasks/advise.py:summarize_advisories` and persisted to the `advisory_summary_json` column. It lets the flights-list card render per-flight summary chips straight from the DB — never re-parsing `route_advisories.json` per flight. NULL for old packs (set on next refresh); `storage/flights.py:parse_advisory_summary` tolerates malformed/legacy values by degrading to None.

`BriefingPackMeta.diagnostics: list[Diagnostic]` carries structured pipeline events from every stage (fetch, analyze, advisories, gramet, skewt, digest). See the **Diagnostic** section below.

## Diagnostic (`models/diagnostic.py`)

One structured event from the briefing pipeline — warning, info, or error. Collected per-pipeline-run, persisted both into the pack on disk (`fetch_meta.json`) and into the DB (`diagnostics_json` column on `BriefingPackRow`).

```python
Diagnostic(
    level="warn",
    stage="digest",
    code="anthropic_internal_error",  # StrEnum value, see DigestCode/FetchCode/...
    message="AI weather digest unavailable — Anthropic API error. Try refreshing again in a few minutes.",
    detail="Traceback (most recent call last):\n  ...",   # capped + redacted, debug-only
    request_id="req_011Caf...",                            # upstream Anthropic id
    error_id=UUID("a4f9..."),                              # user-quotable support id
    occurred_at=datetime(2026, 5, 3, 5, 18, 37, tzinfo=timezone.utc),
)
```

### Level convention

`level` doubles as severity AND audience. Pick based on what the user can do about it:

| Level | Banner? | Use for |
|-------|---------|---------|
| `info` | no | persisted for debugging only — internal config the user can't fix, normal pipeline events ("ICON skipped, out of range") |
| `warn` | yes | retryable issues — "GFS fetch failed", "Anthropic API overloaded — try again" |
| `error` | yes | irrecoverable failures the user should know about even though they can't fix them — bad request, auth failure |

Reach for `warn` first; `error` is for the rare case where retrying genuinely won't help.

### Construction split (preserves backward compat)

- `Diagnostic.create(...)` — for **new** entries. Mints `error_id` (UUID) and `occurred_at`.
- `Diagnostic(...)` / `Diagnostic.model_validate(...)` — for **round-tripping** persisted records. Leaves `error_id` and `occurred_at` as `None`.

Why: legacy DB rows (pre-typed model) only have `{level, message}`. If `error_id` used `Field(default_factory=uuid4)`, every read of the same legacy row would mint a fresh UUID, making the same diagnostic look "new" on every parse. The split-constructor pattern avoids that.

`model_config = {"extra": "ignore"}` lets future field additions land without breaking older readers.

### Wire-safe projection: `DiagnosticPublic`

`DiagnosticPublic` strips `detail` and `request_id` before crossing the API boundary. The frontend doesn't render `detail` today, but it's trivially visible via devtools — and `detail` contains stack traces, file paths, library versions. `PackMetaResponse.diagnostics: list[DiagnosticPublic]`; `Diagnostic.to_public()` does the projection.

`error_id` IS exposed on the public schema — it's a per-entry UUID a user can quote back to support, with no information value beyond that.

### Stable codes

Codes live in `models/diagnostic_codes.py`, one `StrEnum` per stage: `FetchCode`, `DigestCode`, `GrametCode`, `SkewtCode`, `AdvisoryCode`. **Never rename existing values** — add new ones. Telemetry, log filters, and any future i18n key off these strings.

### Persistence

- **DB**: `BriefingPackRow.diagnostics_json` (`Text` column). `_meta_to_row` serializes via `model_dump(mode="json")`; `_parse_diagnostics` validates per-item and skips malformed entries (logs at debug) so one bad row doesn't break a whole flight listing.
- **Disk**: `fetch_meta.json` in the pack directory. Written once by `save_fetch_artifacts` (fetch-stage subset), then rewritten at end of `execute_briefing` with the full merged set across all stages — preserving the original `fetched_at`.
- **Detail cap**: 4 KB per entry, with light redaction for Bearer tokens and `sk-`/`sk-ant-` API key prefixes. Applied in a `field_validator(mode="before")` so it fires on both fresh construction and round-trip.

See [digest.md](./digest.md) for the digest-stage classifier (`classify_llm_exception`) that maps Anthropic exceptions to typed `DigestCode` values.

## Route Advisory Models (`models/advisories.py`)

| Model | Purpose | Key fields |
|-------|---------|------------|
| `AdvisoryStatus` | Enum: GREEN, AMBER, RED, UNAVAILABLE | `worst()` + `majority()` classmethods for aggregation |
| `AdvisoryAggregation` | Enum: WORST, MAJORITY | how per-model statuses combine |
| `AdvisoryParameterDef` | Tunable parameter metadata | key, label, description, type (number/percent/altitude/speed/boolean), unit, default, min, max, step |
| `AdvisoryCatalogEntry` | Evaluator metadata for UI | id, name, short_description, description, category (icing/cloud/turbulence/convective/model), default_enabled, altitude_dependent, parameters |
| `AdvisoryEvidenceRegion` | One validated spatial evidence region for one model | inclusive `start_point_index`/`end_point_index`; optional paired lower/upper altitude bounds; GREEN/AMBER/RED local severity; stable `reason_code`; optional `metric_id` and `method_id` |
| `ModelAdvisoryResult` | One advisory, one model | status, detail, affected/total point and nautical-mile extents, `cross_check`, additive `data_state`, `primary_method_id`, `evidence_regions`, and mitigations |
| `RouteAdvisoryResult` | Aggregate across models | advisory_id, aggregate_status/detail, `representative_model`, per_model list, parameters_used, aggregate_mitigations. `from_per_model()` aggregates (default MAJORITY, ties→worst) and sources detail/mitigations/model from one representative result |
| `RouteAdvisoriesManifest` | Top-level container | advisories, catalog, route_name, cruise_altitude_ft, flight_ceiling_ft, total_distance_nm, models, aggregation, airport_conditions |
| `AltitudeAdvisoryRow` | One row of the altitude table | altitude_ft, statuses (advisory_id→status), red/amber/green_count |
| `AltitudeTableResult` | Altitude-dependent advisory sweep | rows (desc by altitude), advisory_ids, advisory_names, cruise/ceiling/step_ft, best_below_cruise, best_above_cruise |

See [advisories.md](./advisories.md) for the evaluator framework.

### Advisory evidence compatibility and geometry

`ModelAdvisoryResult.data_state` is `"complete"`, `"partial"`,
`"unavailable"`, or `None`. `None` is the additive legacy default: it means the
pack/evaluator did not provide this metadata and must not be interpreted as
complete. `primary_method_id` is stable provenance for the method that controlled
the model grade. A region-level `method_id` overrides it only for that region.

Evidence regions inherit the containing model; they never duplicate or merge
model attribution. Their altitude bounds must be both present or both absent,
their indices and bounds must be ordered, their reason code must be non-empty,
and their severity cannot be UNAVAILABLE. Empty regions mean assessed-clear only
when a migrated spatial result explicitly says `data_state="complete"`; an empty
list is also normal for non-spatial results.

For migrated spatial results, `total_points` is the number of evaluated
assessments, not the full expected route count. Missing expected points are
represented by `data_state` and never enter a percentage denominator as clear.
`affected_points` is the unique union of controlling evidence domains; for
example FIKI cruise and terminal concern at the same point count once, while
each evidence region retains its own local severity.

Migrated spatial evaluators do not derive nautical miles from
`affected_points / total_points`. `analysis/advisories/evidence.py` assigns each
stable route point the cell bounded by neighbouring midpoints (clipped to route
start/end) and sums the union of qualifying cells. Legacy/unmigrated callers of
`ModelAdvisoryResult.build()` retain the older count-based estimate for backward
compatibility.

`RouteAdvisoryResult.representative_model` is the same per-model result used for
`aggregate_detail` and `aggregate_mitigations`. Empty or all-unavailable
per-model inputs aggregate to UNAVAILABLE; the frontend does not repeat this
selection or merge evidence across forecast models.

## Enums

- `ModelSource`: `GFS`, `ECMWF`, `ICON`, `UKMO`, `METEOFRANCE`, `GEM` (note: `BEST_MATCH` retained in enum for backward-compat deserialization but not in `MODEL_ENDPOINTS`). `GEM` is North-America-region; `ICON`/`UKMO`/`METEOFRANCE` are Europe-region.
- `IcingRisk`: `NONE`, `LIGHT`, `MODERATE`, `SEVERE`
- `IcingType`: `NONE`, `RIME`, `MIXED`, `CLEAR`
- `CloudCoverage`: `FEW`, `SCT`, `BKN`, `OVC`
- `ConvectiveRisk`: `NONE`, `MARGINAL`, `LOW`, `MODERATE`, `HIGH`, `EXTREME`
- `ConvectiveRegime`: `THERMAL`, `WEAK_INSTABILITY`, `LOADED_GUN`, `ACTIVE`
- `PrecipPhase`: `SNOW`, `MIXED`, `RAIN`, `FREEZING_RAIN`, `ICE_PELLETS`, `DRY`
- `PrecipIntensity`: `NONE`, `LIGHT`, `MODERATE`, `HEAVY`
- `AgreementLevel`: `GOOD`, `MODERATE`, `POOR`
- `VerticalMotionClass`: `QUIESCENT`, `SYNOPTIC_ASCENT`, `SYNOPTIC_SUBSIDENCE`, `CONVECTIVE`, `OSCILLATING`, `UNAVAILABLE`
- `CATRiskLevel`: `NONE`, `LIGHT`, `MODERATE`, `SEVERE`
- `AdvisoryStatus`: `GREEN`, `AMBER`, `RED`, `UNAVAILABLE`
- `AdvisoryAggregation`: `WORST`, `MAJORITY`

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

- Waypoint resolution / route building: `airports.py` (`resolve_waypoints()`)
- Analysis consumers: [analysis.md](./analysis.md)
- Snapshot persistence: `storage/snapshots.py` (`save_snapshot`/`save_cross_section`/`load_snapshot`); split-file loaders `load_briefing()`/`load_forecasts()`/`load_cross_sections()` live in `tasks/artifacts.py`
- Flight/pack storage: `storage/flights.py`
- API response models: `api/flights.py`, `api/packs.py`
- Weather-based alternates (`alternates.py`) + regulatory trigger (`alternate_requirement.py`): [alternates.md](./alternates.md), [alternate-requirement.md](./alternate-requirement.md)
- Front-detection artifact (`fronts.py` → `route_fronts.json`): [frontal-detection.md](./frontal-detection.md)
