# Architecture

> System overview, data pipeline, API, web app, storage layout, and phase roadmap

## Intent

Flyfun Weather (package: weatherbrief) produces daily aviation weather assessments for a planned GA cross-country flight, tracking conditions from D-7 through D-0. It fetches quantitative data from multiple NWP models, performs aviation-specific analysis, and generates both human-readable text digests and LLM-powered briefings. A web UI and API serve the briefings with history, PDF reports, and email delivery.

## Pipeline (`pipeline.py` + `tasks/`)

```
RouteConfig + departure_time + options
    ↓
interpolate_route()  → ~20 RoutePoint (airports + every 20nm)
    ↓
get_elevation_profile()  → ElevationProfile (SRTM 90m, 0.5nm spacing)
    ↓
OpenMeteoClient.fetch_multi_point()  (1 API call per model, all points)
    ↓
filter by waypoint_icao → list[WaypointForecast]  (for analysis)
+ list[RouteCrossSection]  (full route, saved separately)
    ↓
[optional] enrich_forecasts()  (GRIB2 cloud water/ice + diagnostics: GFS S3, ICON-EU DWD, ECMWF IFS ECPDS)
    ↓
analyze_waypoint()  (per waypoint)
├→ compute_wind_components()
├→ analyze_sounding()  (per model → SoundingAnalysis)
│   ├→ prepare_profile()
│   ├→ compute_indices() + compute_derived_levels()
│   ├→ detect_cloud_layers()
│   ├→ assess_icing_zones() + detect_inversions()
│   ├→ assess_convective()
│   └→ assess_vertical_motion()  (CAT risk, strong motion)
├→ compute_altitude_advisories()  (vertical regimes + advisories)
└→ compare_models()  (22 metrics)
    ↓
analyze_all_route_points()  → RouteAnalysesManifest (full route analysis)
    ↓
evaluate_all(RouteContext)  → RouteAdvisoriesManifest (22 hazard evaluators)
    ↓
ForecastSnapshot  (root object, saved as briefing.json + forecasts.json)
    ↓
Optional outputs:
├→ GRIB init times tracking (grib_init_times dict, when GRIB model run differs from Open-Meteo)
├→ Route weather observations (D-0 only: METAR/TAF via euro_aip, obs-vs-model comparison)
├→ GRAMET cross-section (Autorouter API → PDF, rendered as PNG via PyMuPDF)
├→ Skew-T plots (MetPy → PNG with CAPE/CIN shading, hodograph, indices panel)
├→ LLM digest (LangGraph: text forecasts + quant → WeatherDigest → Markdown + JSON)
```

**Entry point:** `execute_briefing(route, departure_time, options)` — called by the API. Derives `target_date` and `target_hour` internally for downstream task modules. Returns `BriefingResult` with all paths and structured results. Never prints or exits.

`BriefingOptions` controls what gets generated (models, gramet, skewt, llm_digest, enrich_grib, output_dir). `BriefingResult` carries snapshot, paths, digest object, and error list.

**Task modules** (`tasks/`): Pipeline stages extracted into independent modules for testability and incremental re-runs. `pipeline.py` is now a thin orchestrator calling `run_fetch()` → `run_analysis()` → `run_advisories()` → `run_route_weather()` (D-0) → optional outputs. Each task module can also run standalone from saved artifacts (e.g. `run_advisories_from_pack()`).

**Progressive render via SSE** (`/refresh/stream`, PR #118): the pipeline emits a `briefing_ready` event after the snapshot is saved (post-advisories, post-GRAMET) but **before** the LLM digest runs. The SSE handler persists a *provisional* `BriefingPackRow` at this milestone and pushes the event with the pack timestamp. The frontend shows the briefing immediately while `digestPending` is true, then patches in the digest when the `complete` event lands ~20-30s later. Total perceived latency drops from ~80s to ~55s. The provisional pack is rewritten with `digest_path` on completion — same row, no second insert.

## Package Layout

```
src/weatherbrief/
├── models/            # Pydantic v2 data models (split into submodules)
│   ├── __init__.py    # Re-exports all models
│   ├── analysis.py    # Route, forecast, analysis models (RouteConfig, ForecastSnapshot, etc.)
│   ├── advisories.py  # Route advisory + manifest models
│   ├── airport_conditions.py # Airport flight-category/wind condition models
│   ├── observations.py # METAR/TAF + SIGMET models (AirportObservation, RouteObservations, RouteSigmets)
│   ├── alternates.py / alternate_requirement.py # Alternate selection + EASA requirement models
│   ├── fronts.py       # Hewson front-crossing route models
│   ├── diagnostic.py / diagnostic_codes.py # Per-point diagnostic flags + code catalog
│   ├── verification.py # METAR/TAF verification + digest models
│   └── storage.py     # Flight, BriefingPackMeta
├── airports.py        # ICAO → lat/lon via euro_aip
├── pipeline.py        # Thin orchestrator: calls tasks/ modules in sequence
├── scheduler.py       # Background loops: auto-refresh (10min) + retention, verification scoring/digest/rollup, METAR ingest, ECMWF watcher (5min), GRIB pre-cache, analytics rollup
├── tasks/             # Independent pipeline stages (extracted from pipeline.py)
│   ├── __init__.py    # Re-exports: run_fetch, run_analysis, run_advisories, etc.
│   ├── fetch.py       # run_fetch() → FetchResult (route interpolation, Open-Meteo, GRIB)
│   ├── analyze.py     # run_analysis() → AnalysisResult (waypoint + route point analysis)
│   ├── advise.py      # run_advisories() → AdvisoryResult (evaluator + airport conditions)
│   ├── route_weather.py # run_route_weather() + run_observation_comparison() (D-0 METAR/TAF)
│   ├── artifacts.py   # Save/load helpers for pack_dir I/O (JSON serialization)
│   ├── outputs.py     # run_gramet(), run_skewt(), run_llm_digest() → independent results
│   ├── retention.py / refresh_delta.py # T1/T2 pack retention + delta refresh
│   ├── verification*.py / scoring.py / standalone_verification.py # METAR/TAF verification, scoring, rollups (see metar-taf-accuracy.md)
│   ├── airport_summary.py / airport_watchlist.py / standalone_grib.py # Standalone pan-European monitoring cycle
│   ├── map_queries.py / cache_builder.py / climatology_queries.py # Forecast-map + climatology query/cache builders
│   ├── dwd_charts.py / metoffice_charts.py # Front-chart fetch/georeference tasks
│   ├── alternates.py / alternate_requirement.py # EASA alternate requirement + selection (TAF/minima)
│   ├── fronts.py # Hewson front-crossing computation for the route
│   ├── edr_calibration.py # EDR turbulence calibration task (inert, see project_edr_calibration_inert)
│   └── admin_digest_stats.py # Admin digest/usage stats
├── fetch/
│   ├── variables.py   # Model endpoints, API parameters
│   ├── open_meteo.py  # Open-Meteo client (single + multi-point)
│   ├── route_points.py # Route interpolation (every ~20nm)
│   ├── route_walk.py  # Common route walking utility (great-circle)
│   ├── elevation.py   # SRTM terrain elevation profile
│   ├── model_status.py # NWP model freshness checking
│   ├── nws_text.py    # NWS Area Forecast Discussions (US routes)
│   ├── text_forecasts.py # Route-aware text forecast dispatcher (NWS or DWD by region)
│   ├── dwd_text.py    # DWD synoptic text forecasts (European routes)
│   ├── dwd_charts.py  # DWD surface-front chart download + georeferencing
│   ├── metoffice_charts.py # Met Office surface-front charts (gated source)
│   ├── freshness/     # Marker-based per-(model, source) staleness (see freshness-markers.md)
│   └── grib/          # GRIB2 enrichment (GFS S3, ICON-EU DWD, ECMWF IFS ECPDS)
│       ├── __init__.py     # enrich_forecasts() public API
│       ├── gfs_idx.py      # .idx parser + byte-range planner
│       ├── grib_fetch.py   # HTTP Range downloads from S3
│       ├── icon_eu_fetch.py # ICON-EU GRIB2 from DWD opendata
│       ├── icon_eu_levels.py # Log-pressure interpolation from model levels
│       ├── ecmwf_fetch.py  # ECMWF IFS GRIB read from ECPDS-delivered files
│       ├── ecmwf_watcher.py # Watch/ingest ECPDS-delivered ECMWF runs
│       ├── decode.py       # cfgrib decode + spatial interpolation (chunked for memory)
│       ├── decode_worker.py # Subprocess decode entry for the priority process pool
│       ├── precache.py     # Pre-fetch GRIB runs off freshness markers
│       ├── fill.py         # Forward-fill GRIB fields across time axis
│       └── cache.py        # Disk cache per model (48h TTL)
├── analysis/
│   ├── wind.py        # Headwind/crosswind decomposition
│   ├── comparison.py  # Multi-model divergence scoring (22 thresholds)
│   ├── airport_conditions.py / airport_consensus.py # Airport flight-category/wind + multi-model consensus
│   ├── route_geometry.py / spatial_interpolation.py # Route geometry + spatial field interpolation helpers
│   ├── alternate_requirement.py / sun.py # EASA alternate requirement logic, sun position/glare
│   ├── advisories/    # Route advisory evaluators (22 registered hazard types — see advisories.md)
│   │   ├── __init__.py      # evaluate_all(), get_catalog()
│   │   ├── registry.py      # @register decorator, auto-discovery
│   │   ├── _helpers.py      # Shared: format_extent, pct_above_threshold, terrain lookup
│   │   ├── cloud_top.py     # Cloud top vs ceiling
│   │   ├── convective.py    # Convective risk along route
│   │   ├── dd_nwp_agreement.py # DD-vs-NWP within-model cloud/icing agreement
│   │   ├── fiki_icing.py / icing_escape.py / freezing_precip.py # FIKI icing, non-FIKI escape, freezing precip
│   │   ├── flight_category.py # Airport ceiling/visibility (tunable MVFR/IFR thresholds)
│   │   ├── airport_wind.py / density_altitude.py / llws.py # Airport crosswind+gust, density altitude, low-level wind shear
│   │   ├── enroute_precip.py / headwind.py # En-route precip/visibility, winds-aloft trip impact
│   │   ├── ifr_feasibility.py / vfr_feasibility.py # Composite IFR/VFR go/no-go
│   │   ├── model_agreement.py # Cross-model divergence
│   │   ├── mountain_wind.py / turbulence.py # Orographic/rotor risk, CAT + vertical motion
│   │   ├── fronts.py / sun.py # Front crossings (Hewson), sun glare/position
│   │   └── vmc_cruise.py    # Cloud coverage at cruise
│   └── sounding/      # MetPy-based sounding analysis subpackage
│       ├── __init__.py     # analyze_sounding() entry point
│       ├── prepare.py      # Pint boundary: PressureLevelData → PreparedProfile
│       ├── thermodynamics.py  # MetPy indices + derived levels
│       ├── clouds.py       # Cloud layers from dewpoint depression
│       ├── icing.py        # Icing zones from wet-bulb + Ogimet index
│       ├── wet_bulb.py     # Wet-bulb temperature computation
│       ├── edr.py          # EDR turbulence estimate (calibration inert, see project_edr_calibration_inert)
│       ├── icing_common.py # Shared icing helpers across methods
│       ├── sfip.py         # SFIP NWP-based icing potential index
│       ├── sld.py          # Supercooled large droplet risk
│       ├── e_shear.py      # Effective bulk shear
│       ├── precipitation.py # Precipitation type/intensity classification
│       ├── inversions.py   # Temperature inversion detection
│       ├── convective.py   # Convective risk from indices
│       ├── vertical_motion.py  # Vertical motion classification + CAT risk
│       └── advisories.py   # Dynamic vertical regimes + altitude advisories
├── digest/
│   ├── text.py        # Plain-text digest formatter
│   ├── skewt.py       # Skew-T diagram generation
│   ├── llm_config.py  # LLM config schema + factory
│   ├── llm_digest.py  # LangGraph digest pipeline
│   ├── prompt_builder.py  # Context assembly for LLM
│   ├── outlook.py      # Long-range outlook haiku digest (see project_longrange_outlook)
│   ├── guardrails.py   # Digest output guardrail checks
│   ├── langsmith_feedback.py # LangSmith eval feedback hooks
│   ├── dwd_translate.py # DE→EN translation for DWD synoptic text
│   └── format_utils.py / exceptions.py # Shared formatting helpers + digest exceptions
├── db/
│   ├── __init__.py    # Re-exports from flyfun-common (Base, SessionLocal, get_engine, etc.)
│   └── models.py      # Re-exports shared models (UserRow, ApiTokenRow, etc.) + app-specific tables
├── storage/
│   ├── snapshots.py   # Snapshot + cross-section save/load/list (file-based)
│   ├── sounding_profiles.py # sounding_profiles.json.gz sidecar I/O
│   ├── flights.py     # Flight + BriefingPack CRUD (DB-backed)
│   ├── aircraft.py / aircraft_types.py # iOS aircraft registry CRUD + type catalog
│   ├── pireps.py / debriefs.py # PIREP + post-flight debrief CRUD
│   └── system_profiles.py # System/admin profile storage
├── api/
│   ├── app.py         # FastAPI app, lifespan (DB init), mounts flyfun-common auth router
│   ├── flights.py     # CRUD /api/flights (DB sessions via Depends)
│   ├── packs.py       # Packs: history, artifacts, refresh (with RefreshRegistry), report, email
│   ├── profiles.py    # Flight parameter profiles CRUD (per-user named templates)
│   ├── preferences.py # User preferences + autorouter credentials CRUD
│   ├── throttle.py    # Server-wide generation_slot semaphore (3) + per-key sliding-window rate limiters (pdf, plot, pirep, feedback, analytics)
│   ├── usage.py       # Usage summary + daily rate limits
│   ├── credits.py     # Cost summary, charge, admin cost config, transparency endpoint
│   ├── feedback.py    # User feedback submission, admin workflow (status/reply/send/notes)
│   ├── security.py    # Audit logging (admin actions, pack access), HMAC integrity helpers
│   ├── admin.py       # Admin: user list, approval, usage overview, per-user costs, API agent/token mgmt
│   ├── deps.py        # Shared FastAPI dependencies (auth, DB session)
│   ├── tokens.py      # API token mgmt for the current user
│   ├── account_export.py # GDPR Art. 20 export: user downloads JSON of all their data
│   ├── donations.py   # Stripe donation checkout + webhook (see project_donations_live)
│   ├── aircraft.py / pireps.py / debriefs.py # iOS companion: aircraft registry, PIREPs, post-flight debrief
│   ├── maps.py / hewson_map.py / climatology.py / airport_profile.py # Forecast/front/climatology map + airport-profile endpoints
│   ├── synoptic_charts.py / _chart_serving.py # DWD/Met Office surface-front chart serving
│   ├── eval_workbench.py # Digest eval workbench (admin/dev, gated; see digest-eval-workbench.md)
│   ├── data_sources.py / models.py / messages.py / validation.py / user_migrations.py # Source registry, model metadata, in-app messages, input validation, per-user migrations
├── costs.py           # Pure cost computation (no DB/IO): CostConfig, CostBreakdown, compute_cost()
├── report/
│   ├── render.py      # render_html(), render_pdf() via Jinja2 + WeasyPrint
│   └── templates/     # Jinja2 template for self-contained HTML report
├── notify/
│   ├── email.py       # Email delivery: Resend API (primary) with SMTP fallback
│   ├── admin_email.py # Notifications: signup, feedback, welcome, feedback reply to user
│   ├── magic_link_email.py # Magic-link / passwordless sign-in email
│   ├── verification_email.py # Daily verification digest email
│   └── admin_digest_email.py # Admin digest stats email
├── triage/
│   ├── __main__.py    # CLI entry point for feedback triage
│   ├── process.py     # AI triage via Claude CLI: classify, analyze, suggest reply
│   ├── prompt.py      # Triage prompt assembly
│   └── security.py    # Triage input sanitization
├── analytics/         # Client-event ingest + admin analytics (events, enrich, digest, API)
├── mcp/               # fastmcp server + client (AI flight-planning tool surface, see project_mcp_server)
├── hewson/            # Hewson frontal-detection precompute + CLI (ERA5 cases)
├── frontal/           # Frontal-zone detection (contour fronts, TFP/θe) + CLI
├── era5/              # ERA5 reanalysis loader (front-calibration reference data)
├── release/           # Release-stream / What's New CLI + models
├── verify/            # Standalone verification CLI entry
├── debriefs/          # Post-flight debrief stats + taxonomy
├── scenario/          # Scenario measurement helpers
├── eval_workbench/    # Digest eval replay harness (see digest-eval-workbench.md)
├── atmo.py / units.py # Atmospheric constants + region-aware unit conversion
├── impact.py          # Shared route-impact summarization helpers
├── privacy.py         # PII-safe logging helpers (mask_email for ops logs)
└── process_memory_sampler.py / process_rss.py # Memory instrumentation
```

## Web Frontend (`web/`)

Vanilla TypeScript + Zustand (no React), bundled by esbuild.

```
web/
├── index.html         # Flights list page
├── flight.html        # Single-flight overview page
├── briefing.html      # Briefing report page (collapsible sections)
├── login.html / auth-verify.html # OAuth + magic-link login/verify
├── settings.html      # User preferences + usage dashboard
├── admin.html         # Admin: user approval + usage overview
├── user-costs.html / cost-summary.html # Admin per-user costs + cost summary
├── maps.html          # Pan-European forecast/front maps
├── verification.html  # METAR/TAF verification dashboard
├── pireps.html        # PIREP feed
├── donate.html / donate-thanks.html / donate-cancel.html # Stripe donation flow pages
├── eval.html          # Digest eval workbench page (admin/dev)
├── help.html / privacy.html # Help & documentation, privacy policy
├── css/style.css      # Shared styles
├── ts/
│   ├── store/         # Zustand vanilla stores + shared types
│   ├── managers/      # DOM rendering functions (briefing-ui, advisories-ui, etc.)
│   ├── adapters/      # API communication layer (api, auth, preferences, profiles, admin, credits)
│   ├── components/    # Reusable UI components (info-popup, welcome-wizard)
│   ├── helpers/       # Metric lookup, threshold rendering
│   ├── types/         # Shared TypeScript type definitions (metrics, advisories)
│   ├── data/          # Static data (metrics-catalog.json, metrics-display.json)
│   ├── analytics/     # Client-side analytics event tracking
│   ├── i18n/          # Localization strings
│   ├── tour/          # Guided product tour
│   ├── visualization/ # Interactive visualizations (cross-section + route graph + route map)
│   │   ├── cross-section/
│   │   │   ├── renderer.ts      # Main canvas engine + coordinate transforms
│   │   │   ├── axes.ts          # Distance/altitude axis rendering
│   │   │   ├── interaction.ts   # Hover crosshair + click-to-select
│   │   │   ├── layer-registry.ts # Central layer toggle registry
│   │   │   ├── theme.ts         # Switchable cross-section themes (standard, high-contrast)
│   │   │   ├── theme-preview.ts  # Theme preview popup canvas
│   │   │   ├── compare-renderer.ts # Compare mode with theme support
│   │   │   └── layers/          # Individual layers (terrain, clouds, icing, etc.)
│   │   ├── route-graph/
│   │   │   ├── renderer.ts      # Scalar metric chart below cross-section
│   │   │   ├── axes.ts          # Dual Y-axis rendering
│   │   │   ├── interaction.ts   # Hover + selection sync with cross-section
│   │   │   ├── metrics.ts       # Metric registry (wind, temp, precip, CAPE, etc.)
│   │   │   └── constants.ts     # Shared layout constants
│   │   ├── route-map/           # Leaflet geographic route visualization
│   │   │   ├── renderer.ts      # Leaflet map lifecycle, segment polylines, waypoints
│   │   │   ├── metrics.ts       # 19-metric registry (MapMetric objects)
│   │   │   ├── segment-style.ts # Pure: computeSegmentStyles() → {color, weight}[]
│   │   │   ├── interaction.ts   # Hover, click, tooltip, sync callbacks
│   │   │   ├── altitude-slider.ts # Range input for level-dependent metrics
│   │   │   └── legend.ts        # DOM gradient bar with color stops
│   │   ├── interaction-utils.ts # Shared: tooltip, nearest point, axis ticks
│   │   ├── controls/panel.ts    # Layer toggles, model selector, layout mode, map metric selectors
│   │   ├── data-extract.ts      # Transform API data → VizRouteData
│   │   ├── scales.ts            # Shared color/opacity scales for all three renderers
│   │   ├── layer-legends.ts     # Legend entries for all layers
│   │   ├── skewt/               # Canvas Skew-T renderer (see skewt-canvas.md)
│   │   ├── weather-map.ts / synoptic-map.ts / weather-map-consensus.ts # Pan-European forecast + synoptic/front overlay maps
│   │   ├── climatology-map.ts / climatology-tab.ts / climatology-datasets.ts # Climatology map dataset views
│   │   ├── hewson-grid-layer.ts / hewson-colormaps.ts / front-style.ts # Hewson front grid overlay + styling
│   │   ├── airport-profile-panel.ts / pirep-map.ts / surface-obscuration.ts # Airport profile panel, PIREP map, surface obscuration
│   │   └── types.ts             # Shared viz type definitions
│   ├── utils.ts       # Shared utilities (API base, auth checks, centralized nav banner)
│   ├── theme.ts       # Dark/light/system theme: persistence, toggle UI, matchMedia listener
│   ├── flights-main.ts / flight-main.ts # Flights list + single-flight page entries
│   ├── briefing-main.ts   # Briefing page entry
│   ├── settings-main.ts   # Settings page entry
│   ├── admin-main.ts      # Admin page entry
│   ├── user-costs-main.ts / cost-summary-main.ts # Admin per-user costs + cost summary entries
│   ├── maps-main.ts       # Forecast/front maps page entry
│   ├── verification-main.ts # Verification dashboard entry
│   ├── pireps-main.ts     # PIREP feed entry
│   ├── donate-main.ts     # Donation page entry
│   ├── help-main.ts       # Help page entry
│   └── eval/              # Digest eval workbench UI (admin/dev)
└── dist/              # esbuild output (committed)
```

## Storage

### Database (SQLAlchemy — SQLite dev / MySQL prod)

Flight and pack metadata are stored in a relational database via SQLAlchemy ORM. Shared tables (users, user_preferences, api_tokens, cost_ledger) are defined in `flyfun-common`; app-specific tables (flights, briefing_packs, etc.) are in `weatherbrief.db.models`. All share the same `Base` from flyfun-common.

- **Dev mode** (`ENVIRONMENT=development`): SQLite at `data/weatherbrief.db`, tables auto-created on startup, dev user auto-inserted.
- **Production** (`ENVIRONMENT=production`): MySQL via `DATABASE_URL` env var, schema managed by Alembic migrations.

Tables: `users`, `user_preferences`, `api_tokens`, `cost_ledger` (from flyfun-common) + app-specific tables, grouped by feature:
- Core: `flight_profiles`, `flights`, `briefing_packs`, `briefing_usage`, `api_usage_log`, `feedback`, `cost_config`, `system_messages`, `device_tokens`, `flight_subscriptions`
- iOS companion: `user_aircraft`, `pireps`, `flight_debriefs`
- METAR/TAF verification (see metar-taf-accuracy.md): `verification_observations`, `verification_scores`, `taf_verification_scores`, `verification_cycles`, `verification_cache`, `verification_daily_stats`, `verification_monthly_stats`, `flight_verification_map`, `airport_forecast_snapshots`, `airport_daily_summary`, `airport_monthly_summary`

See [multi-user-deployment.md](./multi-user-deployment.md) for user/flight schema, [cost-attribution-design.md](./cost-attribution-design.md) for cost schema.

### File artifacts (disk)

Large artifacts (snapshots, images, digests) stay on disk, user-scoped:

```
data/packs/
└── {user_id}/
    └── {flight_id}/
        └── {safe_timestamp}/   # ISO timestamp (: → -, + → p)
            ├── briefing.json           # ForecastSnapshot minus forecasts & cross_sections
            ├── forecasts.json          # Route + metadata + raw forecasts only
            ├── cross_section.json
            ├── route_analyses.json    # RouteAnalysesManifest (derived_levels stripped)
            ├── sounding_profiles.json.gz # Sidecar (see note below)
            ├── elevation_profile.json # ElevationProfile (SRTM terrain)
            ├── gramet.pdf             # GRAMET cross-section (PDF)
            ├── skewt/
            │   ├── EGTK_gfs.png
            │   └── ...
            ├── route_advisories.json  # RouteAdvisoriesManifest
            ├── digest.md
            └── digest.json
```

`sounding_profiles.json.gz` holds shaped sounding profiles per (point, model), written at refresh time from the in-memory manifest so the bundle and Skew-T endpoints skip the MetPy recompute. The online viewer never loads it, and it is stripped at T1 retention alongside `cross_section.json`.

Path components are sanitized via `safe_path_component()` to prevent traversal attacks.

In Docker, `data/` is a volume mount (`./data:/app/data`). Legacy storage (`data/forecasts/`) still exists for backward compatibility.

## API

FastAPI app at `api/app.py`, served by uvicorn.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/login/google` | GET | Redirect to Google OAuth |
| `/auth/callback/google` | GET | OAuth callback → JWT cookie |
| `/auth/logout` | POST | Clear JWT cookie |
| `/auth/me` | GET | Current user info (incl. is_admin) |

Authentication (from flyfun-common) supports both JWT cookies (`flyfun_auth`, cross-subdomain on `.flyfun.aero`) and API tokens (`Authorization: Bearer ff_...`, legacy `wb_` accepted). See [multi-user-deployment.md](./multi-user-deployment.md) for details.
| `/api/flights` | GET/POST | List/create flights |
| `/api/flights/{id}` | GET/DELETE | Get/delete (any user can view, only owner can delete) |
| `/api/flights/{id}/packs` | GET | List pack history |
| `/api/flights/{id}/packs/latest` | GET | Most recent pack |
| `/api/flights/{id}/packs/refresh` | POST | Trigger new briefing fetch (owner only) |
| `/api/flights/{id}/packs/refresh/stream` | POST | SSE streaming refresh with progress (emits `progress`, `briefing_ready`, `complete`, `error`) |
| `/api/flights/{id}/packs/{ts}` | GET | Pack metadata |
| `/api/flights/{id}/packs/{ts}/snapshot` | GET | Raw forecast JSON |
| `/api/flights/{id}/packs/{ts}/gramet` | GET | GRAMET PDF (or PNG fallback) |
| `/api/flights/{id}/packs/{ts}/skewt/{icao}/{model}` | GET | Skew-T PNG (by waypoint) |
| `/api/flights/{id}/packs/{ts}/skewt/route/{idx}/{model}` | GET | Skew-T PNG (by route point) |
| `/api/flights/{id}/packs/{ts}/route-analyses` | GET | Route point analyses JSON |
| `/api/flights/{id}/packs/{ts}/elevation` | GET | Elevation profile JSON |
| `/api/flights/{id}/packs/freshness` | GET | Model freshness + staleness check |
| `/api/flights/{id}/packs/{ts}/digest/json` | GET | Structured digest |
| `/api/flights/{id}/packs/{ts}/report.html` | GET | Self-contained HTML report |
| `/api/flights/{id}/packs/{ts}/report.pdf` | GET | PDF download |
| `/api/flights/{id}/packs/{ts}/advisories` | GET | Route advisories JSON |
| `/api/flights/{id}/packs/{ts}/advisories/recalculate` | POST | Re-evaluate advisories with user prefs |
| `/api/flights/{id}/packs/{ts}/observations/refresh` | POST | Re-fetch METAR/TAF + route SIGMETs for D-0 packs; returns `{observations, sigmets}` (owner only) |
| `/api/flights/{id}/packs/{ts}/email` | POST | Send email to logged-in user |
| `/api/user/profiles` | GET/POST | List/create flight parameter profiles |
| `/api/user/profiles/{id}` | GET/PUT/DELETE | Get/update/delete profile |
| `/api/user/profiles/{id}/duplicate` | POST | Clone profile with new name |
| `/api/user/preferences` | GET/PUT | User preferences (autorouter creds) |
| `/api/user/preferences/autorouter` | DELETE | Clear autorouter credentials |
| `/api/user/usage` | GET | Today/month usage summary with quotas |
| `/api/admin/users` | GET | All users with monthly summaries (admin only) |
| `/api/admin/users/{id}/costs` | GET | Per-user cost detail: balance, transactions, breakdown (admin) |
| `/api/admin/users/{id}/approve` | POST | Approve user (admin only) |
| `/api/admin/approve/{id}` | GET | One-click HMAC-signed approval link |
| `/api/admin/agents` | POST | Create bot/agent user with initial API token |
| `/api/admin/agents/{uid}/tokens` | POST | Create additional API token for agent |
| `/api/admin/agents/{uid}/tokens/{tid}` | DELETE | Revoke API token |
| `/api/user/credits` | GET | Credit balance, recent transactions, daily/monthly usage |
| `/api/admin/cost-config` | GET/PUT | View/update cost configuration (admin) |
| `/api/admin/cost-config/history` | GET | Cost config version history (admin) |
| `/api/transparency` | GET | Public pricing structure (no auth) |
| `/api/feedback` | POST | Submit user feedback on a briefing |
| `/api/feedback/admin` | GET | List all feedback with optional status filter (admin only) |
| `/api/feedback/admin/{id}/status` | PUT | Update feedback workflow status (admin) |
| `/api/feedback/admin/{id}/reply` | PUT | Save draft reply text (admin) |
| `/api/feedback/admin/{id}/send` | POST | Send reply email to user + set status=replied (admin) |
| `/api/feedback/admin/{id}/notes` | PUT | Save internal admin notes (admin) |
| `/auth/me/account` | DELETE | Delete own account (cascades flights, profiles, artifacts) |
| `/api/account/export` | GET | GDPR Art. 20: download JSON of all own data (read-only mirror of account delete) |
| `/api/admin/hub/*` | various | Cross-app admin hub (users, costs) via flyfun-common |
| `/api/refresh/active` | GET | List all currently active refreshes |
| `/api/refresh/stats` | GET | Average refresh time (7-day window, for progress hint) |
| `/api/flights/{id}/packs/refresh/status` | GET | Refresh status for a specific flight |
| `/api/admin/metrics` | GET | Queue depth, active refreshes, timing stats (admin) |

The table above covers the core flight/pack/admin surface. Additional feature areas mount their own routers (see linked docs): iOS companion (`/api/aircraft`, `/api/pireps`, `/api/observations`, `/api/companion`, flight debriefs), forecast/front/climatology maps (`/api/maps`, `/api/hewson`, `/api/climatology`, `/api/airport-profile`), synoptic/front chart serving (`/api/charts`), METAR/TAF verification (`/api/verification`), Stripe donations (`/api/donations`, see project_donations_live), in-app messages, model/data-source metadata, and per-user API token management. The MCP server (fastmcp) exposes a separate tool surface.

**Shareable briefing links**: any authenticated user can view any flight's briefings via direct URL. Only the flight owner can refresh, delete, or trigger email. The frontend conditionally shows action buttons based on ownership.

Static files served from `web/` at root.

## Key Choices

- **Pydantic v2 throughout** — validation, serialization, JSON round-trip all free.
- **Multi-point fetch** — 1 API call per model with all route points (not per-waypoint); 24h time window.
- **Graceful degradation** — GRAMET/Skew-T/LLM/DWD failures logged but don't halt pipeline.
- **Pipeline as API entry point** — `pipeline.py` is the single entry point for the API. Stages extracted into `tasks/` modules for independent testability and re-run from artifacts.
- **DB-backed metadata, file-based artifacts** — flight/pack metadata in SQLAlchemy (SQLite dev, MySQL prod); large files (snapshots, images) on disk in user-scoped directories. Snapshots are split into `briefing.json` (analyses, observations, metadata) and `forecasts.json` (raw forecasts) — see [data-models.md](./data-models.md) for details.
- **Flight ID = route + date + params hash** — allows same route+date with different time/altitude.
- **Timezone-aware UTC datetimes** — all datetimes are `datetime(..., tzinfo=timezone.utc)`. SQLite loses tzinfo on round-trip; `_ensure_utc()` in storage layer promotes naive datetimes back to UTC on read.
- **Vanilla TS + Zustand** — no framework; esbuild for fast bundling.
- **CSS custom property theming** — dark/light/system mode via `[data-theme]` on `<html>`, FOUC-prevention inline script, `theme-changed` custom event for canvas/map reactivity.
- **ECMWF-only Skew-T in PDF/email** — PDF is concise; web UI allows model toggling.

## Dependencies

| Package | Purpose |
|---------|---------|
| `flyfun-common` | Shared auth (OAuth, JWT, API tokens), DB engine, user models, encryption (from GitHub) |
| `pydantic>=2.0` | Data models |
| `sqlalchemy>=2.0` | ORM (SQLite + MySQL) |
| `alembic>=1.13` | Database migrations |
| `pymysql>=1.1` | Pure-Python MySQL driver |
| `cryptography>=42.0` | MySQL auth + Fernet credential encryption |
| `authlib>=1.3` | Google OAuth OIDC flow |
| `pyjwt>=2.8` | JWT encode/decode |
| `fastmcp>=2.13` | MCP server (AI flight-planning tools) |
| `timezonefinder>=6.2` | Lat/lon → timezone for local-time display |
| `requests`, `httpx` | HTTP API calls |
| `pyyaml` | Route config |
| `fastapi`, `uvicorn` | API server |
| `metpy`, `matplotlib`, `numpy` | Sounding analysis + Skew-T plots |
| `cfgrib`, `xarray`, `eccodes` | GRIB2 decoding for GFS enrichment (system dep: libeccodes-dev) |
| `langchain`, `langgraph` | LLM digest orchestration |
| `langchain-anthropic`, `langchain-openai` | LLM providers |
| `python-dotenv` | Environment loading |
| `jinja2`, `weasyprint` | PDF/HTML report rendering |
| `srtm.py` | SRTM terrain elevation data (90m resolution) |
| `PyMuPDF` (`fitz`) | GRAMET PDF → PNG conversion for reports |
| `euro-aip` (PyPI) | Airport DB, Autorouter credentials |

## Phase Roadmap

Completed-phase development changelog moved to [archive/phase-roadmap.md](./archive/phase-roadmap.md) to keep this doc focused on current architecture. The only still-open item is **Phase 4c** (ensemble & remaining model-comparison refinement). Shipped subsystems each have their own design doc — see the index.

## Docker

The app is packaged as a Docker image (`python:3.13-slim`) with:
- System deps for WeasyPrint (libpango, libcairo, etc.) + eccodes for GRIB2 decode
- `weatherbrief` installed editable (`pip install -e .`); `euro-aip` pulled from PyPI per the pyproject pin (`euro-aip>=0.12.0`), not a local path
- Non-root user (UID 2000)
- Exposed on port 8020

```bash
# Build
docker build -t weatherbrief .

# Run with docker-compose (joins shared-services network)
docker-compose up -d
```

`docker-compose.yml` mounts `./data:/app/data` for artifact persistence and reads env vars from `.env`.

## References

- Original spec: [archive/flight-weather-tracker-spec.md](./archive/flight-weather-tracker-spec.md)
- Data models: [data-models.md](./data-models.md)
- Fetch: [fetch.md](./fetch.md)
- Analysis: [analysis.md](./analysis.md)
- Digest: [digest.md](./digest.md)
- Multi-user deployment: [multi-user-deployment.md](./multi-user-deployment.md)
- Route advisories: [advisories.md](./advisories.md)
- Cross-section & route graph visualization: [visualization.md](./visualization.md)
- Route graph: [route-graph.md](./route-graph.md)
- METAR/TAF route weather: [metar-taf-route-weather.md](./metar-taf-route-weather.md)
- Cost attribution & credits: [cost-attribution-design.md](./cost-attribution-design.md)
