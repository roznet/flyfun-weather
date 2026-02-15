# Architecture

> System overview, data pipeline, API, web app, storage layout, and phase roadmap

## Intent

WeatherBrief produces daily aviation weather assessments for a planned European GA cross-country flight, tracking conditions from D-7 through D-0. It fetches quantitative data from multiple NWP models, performs aviation-specific analysis, and generates both human-readable text digests and LLM-powered briefings. A web UI and API serve the briefings with history, PDF reports, and email delivery.

## Pipeline (`pipeline.py`)

```
RouteConfig + target_date + options
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
ForecastSnapshot  (root object, saved as JSON)
    ↓
Optional outputs:
├→ GRAMET cross-section (Autorouter API → PDF, rendered as PNG via PyMuPDF)
├→ Skew-T plots (MetPy → PNG with CAPE/CIN shading, hodograph, indices panel)
├→ LLM digest (LangGraph: DWD text + quant → WeatherDigest → Markdown + JSON)
```

**Entry point:** `execute_briefing(route, target_date, target_hour, options)` — shared by CLI and API. Returns `BriefingResult` with all paths and structured results. Never prints or exits.

`BriefingOptions` controls what gets generated (models, gramet, skewt, llm_digest, output_dir). `BriefingResult` carries snapshot, paths, digest object, and error list.

## Package Layout

```
src/weatherbrief/
├── models/            # Pydantic v2 data models (split into submodules)
│   ├── __init__.py    # Re-exports all models
│   ├── analysis.py    # Route, forecast, analysis models (RouteConfig, ForecastSnapshot, etc.)
│   └── storage.py     # Flight, BriefingPackMeta
├── airports.py        # ICAO → lat/lon via euro_aip
├── cli.py             # CLI entry point (delegates to pipeline)
├── pipeline.py        # Core pipeline: fetch → analyze → outputs
├── fetch/
│   ├── variables.py   # Model endpoints, API parameters
│   ├── open_meteo.py  # Open-Meteo client (single + multi-point)
│   ├── route_points.py # Route interpolation (every ~20nm)
│   ├── route_walk.py  # Common route walking utility (great-circle)
│   ├── elevation.py   # SRTM terrain elevation profile
│   ├── model_status.py # NWP model freshness checking
│   ├── dwd_text.py    # DWD synoptic text forecasts
│   └── gramet.py      # Autorouter GRAMET
├── analysis/
│   ├── wind.py        # Headwind/crosswind decomposition
│   ├── comparison.py  # Multi-model divergence scoring (15 thresholds)
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
│   ├── packs.py       # Packs: history, artifacts, refresh, report, email
│   ├── preferences.py # User preferences + autorouter credentials CRUD
│   ├── usage.py       # Usage summary + daily rate limits
│   └── admin.py       # Admin: user list, approval, usage overview
├── report/
│   ├── render.py      # render_html(), render_pdf() via Jinja2 + WeasyPrint
│   └── templates/     # Jinja2 template for self-contained HTML report
└── notify/
    ├── email.py       # SMTP email with HTML body + PDF attachment
    └── admin_email.py # Admin notification on new user signup (HMAC-signed approval links)
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
├── css/style.css      # Shared styles
├── ts/
│   ├── store/         # Zustand vanilla stores + shared types
│   ├── managers/      # DOM rendering functions
│   ├── adapters/      # API communication layer (api, auth, preferences, admin)
│   ├── components/    # Reusable UI components (info-popup)
│   ├── helpers/       # Metric lookup, threshold rendering
│   ├── types/         # Shared TypeScript type definitions (metrics)
│   ├── data/          # Static data (metrics-catalog.json, metrics-display.json)
│   ├── visualization/ # Canvas-rendered cross-section visualization
│   │   ├── cross-section/
│   │   │   ├── renderer.ts      # Main canvas engine + coordinate transforms
│   │   │   ├── axes.ts          # Distance/altitude axis rendering
│   │   │   ├── interaction.ts   # Hover crosshair + click-to-select
│   │   │   ├── layer-registry.ts # Central layer toggle registry
│   │   │   └── layers/          # Individual layers (terrain, clouds, icing, etc.)
│   │   ├── controls/panel.ts    # Layer toggles + model selector
│   │   ├── data-extract.ts      # Transform API data → VizRouteData
│   │   ├── scales.ts            # Color/opacity scales for risk levels
│   │   └── types.ts             # Shared viz type definitions
│   ├── utils.ts       # Shared utilities (API base, auth checks, nav)
│   ├── flights-main.ts    # Flights page entry
│   ├── briefing-main.ts   # Briefing page entry
│   ├── settings-main.ts   # Settings page entry
│   └── admin-main.ts      # Admin page entry
└── dist/              # esbuild output (committed)
```

## Storage

### Database (SQLAlchemy — SQLite dev / MySQL prod)

Flight and pack metadata are stored in a relational database via SQLAlchemy ORM. The `db/` package manages engine, models, and FastAPI session dependency.

- **Dev mode** (`ENVIRONMENT=development`): SQLite at `data/weatherbrief.db`, tables auto-created on startup, dev user auto-inserted.
- **Production** (`ENVIRONMENT=production`): MySQL via `DATABASE_URL` env var, schema managed by Alembic migrations.

Tables: `users`, `user_preferences`, `flights`, `briefing_packs`, `briefing_usage`. See [multi-user-deployment.md](./multi-user-deployment.md) for full schema.

### File artifacts (disk)

Large artifacts (snapshots, images, digests) stay on disk, user-scoped:

```
data/packs/
└── {user_id}/
    └── {flight_id}/
        └── {safe_timestamp}/   # ISO timestamp (: → -, + → p)
            ├── snapshot.json
            ├── cross_section.json
            ├── route_analyses.json    # RouteAnalysesManifest
            ├── elevation_profile.json # ElevationProfile (SRTM terrain)
            ├── gramet.pdf             # GRAMET cross-section (PDF)
            ├── skewt/
            │   ├── EGTK_gfs.png
            │   └── ...
            ├── digest.md
            └── digest.json
```

Path components are sanitized via `safe_path_component()` to prevent traversal attacks.

In Docker, `data/` is a volume mount (`./data:/app/data`). Legacy CLI storage (`data/forecasts/`, etc.) still works for CLI-only usage.

## API

FastAPI app at `api/app.py`, served by uvicorn.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/login/google` | GET | Redirect to Google OAuth |
| `/auth/callback/google` | GET | OAuth callback → JWT cookie |
| `/auth/logout` | POST | Clear JWT cookie |
| `/auth/me` | GET | Current user info (incl. is_admin) |
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
| `/api/flights/{id}/packs/{ts}/email` | POST | Send email to logged-in user |
| `/api/user/preferences` | GET/PUT | User preferences (defaults, digest config) |
| `/api/user/preferences/autorouter` | DELETE | Clear autorouter credentials |
| `/api/user/usage` | GET | Today/month usage summary with quotas |
| `/api/admin/users` | GET | All users with usage (admin only) |
| `/api/admin/users/{id}/approve` | POST | Approve user (admin only) |
| `/api/admin/approve/{id}` | GET | One-click HMAC-signed approval link |

**Shareable briefing links**: any authenticated user can view any flight's briefings via direct URL. Only the flight owner can refresh, delete, or trigger email. The frontend conditionally shows action buttons based on ownership.

Static files served from `web/` at root.

## Key Choices

- **Pydantic v2 throughout** — validation, serialization, JSON round-trip all free.
- **Multi-point fetch** — 1 API call per model with all route points (not per-waypoint); 24h time window.
- **Graceful degradation** — GRAMET/Skew-T/LLM/DWD failures logged but don't halt pipeline.
- **Pipeline extracted from CLI** — `pipeline.py` is the single entry point for both CLI and API.
- **DB-backed metadata, file-based artifacts** — flight/pack metadata in SQLAlchemy (SQLite dev, MySQL prod); large files (snapshots, images) on disk in user-scoped directories.
- **Flight ID = route + date + params hash** — allows same route+date with different time/altitude.
- **Naive datetimes in pipeline** — matches Open-Meteo's naive UTC timestamps.
- **Vanilla TS + Zustand** — no framework; esbuild for fast bundling.
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

- Spec: [flight-weather-tracker-spec.md](./flight-weather-tracker-spec.md)
- Data models: [data-models.md](./data-models.md)
- Fetch: [fetch.md](./fetch.md)
- Analysis: [analysis.md](./analysis.md)
- Digest: [digest.md](./digest.md)
- API & web plan: [plan-briefing-architecture.md](./plan-briefing-architecture.md)
- Sounding analysis plan: [sounding_analysis_plan.md](./sounding_analysis_plan.md)
- Multi-user deployment: [multi-user-deployment.md](./multi-user-deployment.md)
- Cross-section visualization: [visualization.md](./visualization.md)
