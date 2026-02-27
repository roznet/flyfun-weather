# Architecture

> System overview, data pipeline, API, web app, storage layout, and phase roadmap

## Intent

WeatherBrief produces daily aviation weather assessments for a planned European GA cross-country flight, tracking conditions from D-7 through D-0. It fetches quantitative data from multiple NWP models, performs aviation-specific analysis, and generates both human-readable text digests and LLM-powered briefings. A web UI and API serve the briefings with history, PDF reports, and email delivery.

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
[optional] enrich_forecasts()  (GRIB2 CLWMR/ICMR from GFS S3)
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
└→ compare_models()  (15 metrics)
    ↓
analyze_all_route_points()  → RouteAnalysesManifest (full route analysis)
    ↓
evaluate_all(RouteContext)  → RouteAdvisoriesManifest (13 hazard evaluators)
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

## Package Layout

```
src/weatherbrief/
├── models/            # Pydantic v2 data models (split into submodules)
│   ├── __init__.py    # Re-exports all models
│   ├── analysis.py    # Route, forecast, analysis models (RouteConfig, ForecastSnapshot, etc.)
│   ├── observations.py # METAR/TAF models (AirportObservation, ObservationComparison, RouteObservations)
│   └── storage.py     # Flight, BriefingPackMeta
├── airports.py        # ICAO → lat/lon via euro_aip
├── pipeline.py        # Thin orchestrator: calls tasks/ modules in sequence
├── scheduler.py       # Background auto-refresh: polls every 10min, freshness check, email notification
├── tasks/             # Independent pipeline stages (extracted from pipeline.py)
│   ├── __init__.py    # Re-exports: run_fetch, run_analysis, run_advisories, etc.
│   ├── fetch.py       # run_fetch() → FetchResult (route interpolation, Open-Meteo, GRIB)
│   ├── analyze.py     # run_analysis() → AnalysisResult (waypoint + route point analysis)
│   ├── advise.py      # run_advisories() → AdvisoryResult (evaluator + airport conditions)
│   ├── route_weather.py # run_route_weather() + run_observation_comparison() (D-0 METAR/TAF)
│   ├── artifacts.py   # Save/load helpers for pack_dir I/O (JSON serialization)
│   └── outputs.py     # run_gramet(), run_skewt(), run_llm_digest() → independent results
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
│   ├── gramet.py      # Autorouter GRAMET
│   └── grib/          # GFS GRIB2 enrichment (CLWMR/ICMR)
│       ├── __init__.py     # enrich_forecasts() public API
│       ├── gfs_idx.py      # .idx parser + byte-range planner
│       ├── grib_fetch.py   # HTTP Range downloads from S3
│       ├── decode.py       # cfgrib decode + spatial interpolation
│       └── cache.py        # Disk cache (48h TTL)
├── analysis/
│   ├── wind.py        # Headwind/crosswind decomposition
│   ├── comparison.py  # Multi-model divergence scoring (15 thresholds)
│   ├── advisories/    # Route advisory evaluators (13 hazard types)
│   │   ├── __init__.py      # evaluate_all(), get_catalog()
│   │   ├── registry.py      # @register decorator, auto-discovery
│   │   ├── _helpers.py      # Shared: format_extent, pct_above_threshold, terrain lookup
│   │   ├── cloud_top.py     # Cloud top vs ceiling
│   │   ├── convective.py    # Convective risk along route
│   │   ├── fiki_icing.py    # FIKI icing layer thickness
│   │   ├── flight_category.py # Airport ceiling/visibility (tunable MVFR/IFR thresholds)
│   │   ├── airport_wind.py  # Airport crosswind + gust assessment
│   │   ├── freezing_level.py # Freezing level vs terrain
│   │   ├── icing_escape.py  # Non-FIKI icing escape viability
│   │   ├── ifr_feasibility.py # Composite IFR go/no-go (airport + icing + convective)
│   │   ├── model_agreement.py # Cross-model divergence
│   │   ├── mountain_wind.py # Orographic/rotor risk
│   │   ├── turbulence.py    # CAT + vertical motion
│   │   ├── vfr_feasibility.py # Composite VFR go/no-go (airport + cloud + VMC)
│   │   └── vmc_cruise.py    # Cloud coverage at cruise
│   └── sounding/      # MetPy-based sounding analysis subpackage
│       ├── __init__.py     # analyze_sounding() entry point
│       ├── prepare.py      # Pint boundary: PressureLevelData → PreparedProfile
│       ├── thermodynamics.py  # MetPy indices + derived levels
│       ├── clouds.py       # Cloud layers from dewpoint depression
│       ├── icing.py        # Icing zones from wet-bulb + Ogimet index
│       ├── inversions.py   # Temperature inversion detection
│       ├── convective.py   # Convective risk from indices
│       ├── vertical_motion.py  # Vertical motion classification + CAT risk
│       └── advisories.py   # Dynamic vertical regimes + altitude advisories
├── digest/
│   ├── text.py        # Plain-text digest formatter
│   ├── skewt.py       # Skew-T diagram generation
│   ├── llm_config.py  # LLM config schema + factory
│   ├── llm_digest.py  # LangGraph digest pipeline
│   └── prompt_builder.py  # Context assembly for LLM
├── db/
│   ├── __init__.py    # Package exports (Base, SessionLocal, get_engine, init_db)
│   ├── models.py      # SQLAlchemy ORM models (User, Flight, BriefingPack, etc.)
│   ├── engine.py      # Singleton engine, init_db(), ensure_dev_user()
│   └── deps.py        # FastAPI deps: get_db() session, current_user_id()
├── storage/
│   ├── snapshots.py   # Snapshot + cross-section save/load/list (file-based)
│   └── flights.py     # Flight + BriefingPack CRUD (DB-backed)
├── api/
│   ├── app.py         # FastAPI app, lifespan (DB init), static files, CORS
│   ├── auth.py        # OAuth login/callback/logout, JWT cookie
│   ├── auth_config.py # JWT secret, dev mode detection, admin emails
│   ├── jwt_utils.py   # JWT encode/decode helpers
│   ├── encryption.py  # Fernet encrypt/decrypt for credentials
│   ├── flights.py     # CRUD /api/flights (DB sessions via Depends)
│   ├── packs.py       # Packs: history, artifacts, refresh (with RefreshRegistry), report, email
│   ├── profiles.py    # Flight parameter profiles CRUD (per-user named templates)
│   ├── preferences.py # User preferences + autorouter credentials CRUD
│   ├── throttle.py    # Concurrency limiters: generation_slot (5), pdf_limiter (3), plot_limiter (2)
│   ├── usage.py       # Usage summary + daily rate limits
│   ├── credits.py     # Credit balance, charge, admin cost config, transparency endpoint
│   ├── feedback.py    # User feedback submission + admin listing
│   └── admin.py       # Admin: user list, approval, usage overview, per-user costs, API agent/token mgmt
├── costs.py           # Pure cost computation (no DB/IO): CostConfig, CostBreakdown, compute_cost()
├── report/
│   ├── render.py      # render_html(), render_pdf() via Jinja2 + WeasyPrint
│   └── templates/     # Jinja2 template for self-contained HTML report
└── notify/
    ├── email.py       # SMTP email with HTML body + PDF attachment
    └── admin_email.py # Admin notifications: new user signup, feedback submission, welcome email
```

## Web Frontend (`web/`)

Vanilla TypeScript + Zustand (no React), bundled by esbuild.

```
web/
├── index.html         # Flights list page
├── briefing.html      # Briefing report page (collapsible sections)
├── login.html         # OAuth login page
├── settings.html      # User preferences + usage dashboard
├── admin.html         # Admin: user approval + usage overview
├── user-costs.html    # Admin: per-user cost attribution dashboard
├── help.html          # Help & documentation
├── css/style.css      # Shared styles
├── ts/
│   ├── store/         # Zustand vanilla stores + shared types
│   ├── managers/      # DOM rendering functions (briefing-ui, advisories-ui, etc.)
│   ├── adapters/      # API communication layer (api, auth, preferences, profiles, admin, credits)
│   ├── components/    # Reusable UI components (info-popup, welcome-wizard)
│   ├── helpers/       # Metric lookup, threshold rendering
│   ├── types/         # Shared TypeScript type definitions (metrics, advisories)
│   ├── data/          # Static data (metrics-catalog.json, metrics-display.json)
│   ├── visualization/ # Interactive visualizations (cross-section + route graph + route map)
│   │   ├── cross-section/
│   │   │   ├── renderer.ts      # Main canvas engine + coordinate transforms
│   │   │   ├── axes.ts          # Distance/altitude axis rendering
│   │   │   ├── interaction.ts   # Hover crosshair + click-to-select
│   │   │   ├── layer-registry.ts # Central layer toggle registry
│   │   │   └── layers/          # Individual layers (terrain, clouds, icing, etc.)
│   │   ├── route-graph/
│   │   │   ├── renderer.ts      # Scalar metric chart below cross-section
│   │   │   ├── axes.ts          # Dual Y-axis rendering
│   │   │   ├── interaction.ts   # Hover + selection sync with cross-section
│   │   │   ├── metrics.ts       # Metric registry (wind, temp, precip, CAPE, etc.)
│   │   │   └── constants.ts     # Shared layout constants
│   │   ├── route-map/           # Leaflet geographic route visualization
│   │   │   ├── renderer.ts      # Leaflet map lifecycle, segment polylines, waypoints
│   │   │   ├── metrics.ts       # 14-metric registry (MapMetric objects)
│   │   │   ├── segment-style.ts # Pure: computeSegmentStyles() → {color, weight}[]
│   │   │   ├── interaction.ts   # Hover, click, tooltip, sync callbacks
│   │   │   ├── altitude-slider.ts # Range input for level-dependent metrics
│   │   │   └── legend.ts        # DOM gradient bar with color stops
│   │   ├── interaction-utils.ts # Shared: tooltip, nearest point, axis ticks
│   │   ├── controls/panel.ts    # Layer toggles, model selector, layout mode, map metric selectors
│   │   ├── data-extract.ts      # Transform API data → VizRouteData
│   │   ├── scales.ts            # Shared color/opacity scales for all three renderers
│   │   ├── layer-legends.ts     # Legend entries for all layers
│   │   └── types.ts             # Shared viz type definitions
│   ├── utils.ts       # Shared utilities (API base, auth checks, centralized nav banner)
│   ├── theme.ts       # Dark/light/system theme: persistence, toggle UI, matchMedia listener
│   ├── flights-main.ts    # Flights page entry
│   ├── briefing-main.ts   # Briefing page entry
│   ├── settings-main.ts   # Settings page entry
│   ├── admin-main.ts      # Admin page entry
│   ├── user-costs-main.ts # Admin: per-user cost attribution page
│   └── help-main.ts       # Help page entry
└── dist/              # esbuild output (committed)
```

## Storage

### Database (SQLAlchemy — SQLite dev / MySQL prod)

Flight and pack metadata are stored in a relational database via SQLAlchemy ORM. The `db/` package manages engine, models, and FastAPI session dependency.

- **Dev mode** (`ENVIRONMENT=development`): SQLite at `data/weatherbrief.db`, tables auto-created on startup, dev user auto-inserted.
- **Production** (`ENVIRONMENT=production`): MySQL via `DATABASE_URL` env var, schema managed by Alembic migrations.

Tables: `users`, `user_preferences`, `flight_profiles`, `flights`, `briefing_packs`, `briefing_usage`, `api_tokens`, `feedback`, `cost_config`, `credit_ledger`. See [multi-user-deployment.md](./multi-user-deployment.md) for user/flight schema, [cost-attribution-design.md](./cost-attribution-design.md) for cost/credit schema.

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
            ├── route_analyses.json    # RouteAnalysesManifest
            ├── elevation_profile.json # ElevationProfile (SRTM terrain)
            ├── gramet.pdf             # GRAMET cross-section (PDF)
            ├── skewt/
            │   ├── EGTK_gfs.png
            │   └── ...
            ├── route_advisories.json  # RouteAdvisoriesManifest
            ├── digest.md
            └── digest.json
```

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

Authentication supports both JWT cookies (browser sessions) and API tokens (`Authorization: Bearer wb_...`) for bot/agent users. See [multi-user-deployment.md](./multi-user-deployment.md) for API token details.
| `/api/flights` | GET/POST | List/create flights |
| `/api/flights/{id}` | GET/DELETE | Get/delete (any user can view, only owner can delete) |
| `/api/flights/{id}/packs` | GET | List pack history |
| `/api/flights/{id}/packs/latest` | GET | Most recent pack |
| `/api/flights/{id}/packs/refresh` | POST | Trigger new briefing fetch (owner only) |
| `/api/flights/{id}/packs/refresh/stream` | POST | SSE streaming refresh with progress |
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
| `/api/flights/{id}/packs/{ts}/observations/refresh` | POST | Re-fetch METAR/TAF for D-0 packs (owner only) |
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
| `/api/feedback/admin` | GET | List all feedback (admin only) |
| `/api/refresh/active` | GET | List all currently active refreshes |
| `/api/flights/{id}/packs/refresh/status` | GET | Refresh status for a specific flight |
| `/api/admin/metrics` | GET | Queue depth, active refreshes, timing stats (admin) |

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
| `pydantic>=2.0` | Data models |
| `sqlalchemy>=2.0` | ORM (SQLite + MySQL) |
| `alembic>=1.13` | Database migrations |
| `pymysql>=1.1` | Pure-Python MySQL driver |
| `cryptography>=42.0` | MySQL auth + Fernet credential encryption |
| `authlib>=1.3` | Google OAuth OIDC flow |
| `python-jose[cryptography]` | JWT encode/decode |
| `requests` | HTTP API calls |
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
| `euro-aip` (local / GitHub) | Airport DB, Autorouter credentials |

## Phase Roadmap

| Phase | Status | Summary |
|-------|--------|---------|
| 1 | Done | Open-Meteo fetch, wind/icing/cloud analysis, JSON snapshots, text digest |
| 2 | Done | Route rework (YAML, per-waypoint track), GRAMET, Skew-T plots |
| 3 | Done | DWD text forecasts, LLM digest (LangGraph + structured output) |
| 4a | Done | MetPy sounding analysis: thermodynamic indices, enhanced clouds/icing/convective, altitude band comparison |
| 4b | Done | Vertical motion + CAT turbulence: omega profiles, Richardson number, Brunt-Vaisala frequency |
| 4c | Planned | Ensemble & remaining model comparison refinement |
| 5 | Done | Web UI, API, PDF report, email delivery |
| 6.1 | Done | Docker + DB + Deploy: SQLAlchemy storage, Alembic migrations, Docker packaging |
| 6.2 | Done | Auth: Google OAuth, JWT sessions, user-scoped data, approval workflow |
| 6.3 | Done | Preferences: per-user settings, Fernet-encrypted autorouter credentials |
| 6.4 | Done | Usage tracking, daily rate limits, admin page, shareable briefing links |
| 7.1 | Done | Interactive cross-section visualization: canvas renderer, 8 layer types, hover/click interaction |
| 7.2 | Done | SRTM terrain elevation profile along route (90m resolution, 0.5nm spacing) |
| 7.3 | Done | Model freshness checking: smart refresh skips unchanged models |
| 7.4 | Done | Enhanced Skew-T: CAPE/CIN shading, hodograph, indices panel, overlays |
| 7.5 | Done | Metric explanations UI: catalog-driven info popups, tiered display, threshold scales |
| 7.6 | Done | Inversion detection, Ogimet icing index, GRAMET PDF, convective risk visualization |
| 7.7 | Done | Legacy routes.yaml removal, collapsible sections, admin force refresh |
| 7.8 | Done | NWP cloud bands, terrain draw-order fix, layer legends, "Discuss with AI" buttons |
| 8.1 | Done | Route advisory system: 13 evaluators, registry, user-tunable parameters, recalculation, frontend dashboard |
| 8.2 | Done | Extended pressure levels (25 for GFS/ECMWF) + GRIB2 enrichment (CLWMR/ICMR from GFS S3), LWC-based icing |
| 9.1 | Done | Flight parameter profiles: named templates for altitude/models/advisories, profile CRUD API, settings UI |
| 9.2 | Done | Unified atmospheric profile table, icing severity toggle, Windy meteogram links |
| 10.1 | Done | Route graph: canvas chart below cross-section for scalar metrics (wind, temp, precip, CAPE, freezing level) |
| 10.2 | Done | Route-aware text forecasts: NWS AFD (US) and DWD (Europe) integrated into LLM digest |
| 10.3 | Done | METAR/TAF route weather: D-0 observations, obs-vs-model comparison, TAF highlighting, wind advisories |
| 10.4 | Done | API token authentication for bot/agent users (admin-managed, `wb_` prefix, SHA-256 hashed) |
| 11.1 | Done | Cost attribution: per-briefing cost computation, credit balance, auto-reload, ledger, admin config, transparency endpoint |
| 11.2 | Done | User feedback: submission with categories, admin email notifications, admin feedback listing |
| 11.3 | Done | Refresh registry: prevent duplicate refreshes, per-flight status tracking, SSE progress streaming |
| 11.4 | Done | Route map visualization: Leaflet geographic view with 14-metric coloring, altitude slider, hover sync |
| 11.5 | Done | Admin user costs page: per-user cost attribution dashboard, cost distribution chart, transaction ledger |
| 11.6 | Done | UX: centralized navigation banner, consistent nav across all pages |
| 11.7 | Done | First-login workflow: welcome wizard (intro, aircraft defaults, guided tour), setup_completed tracking |
| 11.8 | Done | Dark/light/system theme: CSS custom properties, FOUC prevention, theme-aware canvases, map tile switching, image inversion |
| 11.9 | Done | Auto-refresh scheduler: background polling, freshness check, pre-flight lead time, email notification |
| 11.10 | Done | Flight privacy: private flag hides flights from shared briefing links |
| 11.11 | Done | Compact/full display mode: compact hides sounding analysis, model comparison, secondary advisories |

## Docker

The app is packaged as a Docker image (`python:3.13-slim`) with:
- System deps for WeasyPrint (libpango, libcairo, etc.)
- `euro-aip` installed from GitHub (not local path)
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
