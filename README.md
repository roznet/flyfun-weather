# Flyfun Weather

[![Tests](https://github.com/roznet/flyfun-weather/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/roznet/flyfun-weather/actions/workflows/tests.yml)
[![iOS](https://github.com/roznet/flyfun-weather/actions/workflows/ios.yml/badge.svg?branch=main)](https://github.com/roznet/flyfun-weather/actions/workflows/ios.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**Medium-range aviation weather assessment for cross-country GA flights in Europe**

🌍 **[weather.flyfun.aero](https://weather.flyfun.aero)** — live web app &nbsp;·&nbsp; 📱 **[iOS / iPadOS app](https://apps.apple.com/app/id6760951972)** &nbsp;·&nbsp; 🤖 **[Android app](https://play.google.com/store/apps/details?id=aero.flyfun.weather)** &nbsp;·&nbsp; 🎬 **[talks.flyfun.aero](https://talks.flyfun.aero)** — see it in action ([PPL/IR Europe video](https://youtu.be/Sa5YkQ71WHs))

> The Python package and module are named `weatherbrief`; the product is **Flyfun Weather**.

Flyfun Weather fetches forecast data from multiple numerical weather prediction (NWP) models, performs aviation-specific analysis (icing, turbulence, convection, clouds), and presents everything side-by-side so pilots can compare models and start forming an early view of what conditions will look like from D-7 through D-0 — and then follows the flight on the day with live observations along the route.

> **Responsibility** — Flyfun Weather supports flight planning and decision-making with automated analysis and AI, which can occasionally be wrong or miss something; use it alongside the other information you would normally check (METAR, TAF, SIGMET and the rest). The pilot in command remains responsible for the flight.

## What it does

- **Multi-model forecasting** — Up to six NWP models per route, fetched where possible as GRIB direct from the forecast centres (ECMWF IFS, DWD ICON-EU/D2, NOAA GFS with HRRR over the US, Météo-France AROME, Met Office UKV), with [Open-Meteo](https://open-meteo.com/) filling the gaps and providing Best Match
- **~85 derived metrics** — From raw NWP data, derives thermodynamic indices (CAPE, CIN, Lifted Index, K-Index, etc.), cloud layers, icing zones (multiple methods), CAT turbulence risk, convective potential, wind shear, and more using MetPy
- **20+ route advisories across 11 categories** — Deterministic hazard evaluators (airport weather, wind, density altitude, LLWS and frost; VFR/IFR/approach/departure feasibility; winds aloft; icing graded for the aircraft's FIKI equipage, freezing precipitation, icing escape; cloud tops and VMC at cruise; convective activity and character; en-route precipitation; turbulence incl. wave-corroborated mountain wind; sun; fronts; model quality) with per-model GREEN/AMBER/RED grading, majority (default) or worst-model aggregation, an altitude slider/table, and advice-only **options to improve** that never change the grade
- **Flight-day live layer** — From about three hours before departure, a 10-minute tick follows the newest METAR/TAF, route **SIGMETs**, OPERA radar, MTG lightning and satellite cloud tops along the route: an at-a-glance nutshell with an AI highlight, a route ribbon with ETAs, storm cells placed by side and off-track distance, and a "since this briefing" change list. It annotates the briefing and never re-grades it. The mobile apps' **Start Flight** places the aircraft on the route from GPS; after the flight the view becomes a **recap** of what was seen where you actually were
- **Weather-based alternates** — When a destination is marginal (D-2 inward), surfaces nearby divert candidates that fix the deficient axis (category/wind/crosswind), classified before/after along the route, plus a regulatory **"is an alternate required?"** estimate (FAA 14 CFR 91.169 + EASA Part-NCO)
- **Model comparison** — Side-by-side divergence scoring so you can see where models agree and where they don't
- **Interactive cross-section** — Canvas-rendered visualization with ~25 toggleable layers (terrain, clouds, icing, SFIP, CAT, inversions, convective, temperature lines, stability levels, NWP cloud bands), switchable themes, a compare-across-models mode, and hover/click interaction
- **Route map & route graph** — Leaflet route map with an altitude slider and metric-colored segments (incl. an "alternate required?" mode), plus a scalar route graph sharing the cross-section's x-axis
- **Skew-T soundings** — Per-waypoint, per-model diagrams: a dynamic canvas Skew-T with a multi-variable side panel and overlay bands, a multi-model compare mode, and the classic MetPy PNG (CAPE/CIN shading, hodograph, indices panel)
- **LLM-powered synopsis** — Optional AI-generated weather narrative via Claude (default) or OpenAI, combining DWD synoptic text with quantitative analysis; beyond the high-resolution horizon it switches to a cheaper, confidence-led **long-range early outlook**
- **Navigation database** — Resolves non-airport waypoints (5-letter fixes, navaids, free-route points) from ~95,900 deduplicated points built from four free public sources, refreshed on the 28-day AIRAC/NASR cycle
- **PDF/HTML reports & email** — Self-contained briefing reports you can download or email to yourself
- **Terrain-aware** — SRTM 90m elevation profiles for mountain crossing risk assessment
- **Maps** — Pan-European views independent of any single route: forecast overview per airport (per model, worst or majority), Now (latest METARs, radar, satellite IR, lightning), Synoptic forecast (fronts and air-mass boundaries), Historical replay, Climatology, and forecast accuracy stats
- **Trips & timing** — Group legs into a trip whose viability is that of its remaining legs, and let a per-flight Flexibility setting scan other departure times in the background
- **Native iOS / iPadOS app** — SwiftUI client sharing the same API: flights, briefing, cross-section, map, Skew-T, offline briefings, the Observed tab and Start Flight on flight day, push notifications, Siri shortcuts
- **Native Android app** — Kotlin / Jetpack Compose client on [Google Play](https://play.google.com/store/apps/details?id=aero.flyfun.weather) sharing the same API: flights, briefing, cross-section, map, Skew-T, offline briefings, the Observed tab and Start Flight on flight day, push notifications
- **Post-flight debrief** — Record whether you flew or cancelled and what weather mattered, so assessments can be checked against what actually happened
- **Forecast verification** — Model forecasts scored against later observations and analyses, feeding a skill view per model and metric (admin-facing while it settles)
- **MCP server** — Exposes flights, briefings, advisory detail, airport weather and a multi-day area outlook ("which day this weekend?") as tools for AI assistants, with OAuth 2.1; listed in Claude's connectors directory, plus a ChatGPT custom GPT over the same core

## Weather Models

| Slot | Model | Source |
|------|-------|--------|
| **ECMWF** | ECMWF IFS | ECMWF GRIB direct, Open-Meteo fallback |
| **ICON** | DWD ICON-EU / ICON-D2 | DWD GRIB direct, Open-Meteo fallback |
| **GFS** | NOAA GFS (HRRR over the continental US) | NOAA GRIB direct, Open-Meteo fallback |
| **Météo-France** | Météo-France AROME | Météo-France GRIB (AROME domain only) |
| **UKMO** | Met Office UKV | Met Office NetCDF on AWS (UKV domain only) |
| **Best Match** | Open-Meteo auto-select | Open-Meteo |

Horizons differ by model, from about two days for the high-resolution regional models (AROME, UKV, ICON-D2) to 10+ days for the global ones; the live list of runs, resolutions and horizons is on the [Data Sources tab](https://weather.flyfun.aero/help.html?tab=data-sources) of the help page. Not all variables are available from all models; Flyfun Weather derives fallbacks where possible and clearly indicates when data is unavailable.

## Route Advisories

Each advisory evaluates a specific weather hazard along your route, per model. The summary rating per advisory is the majority of the models by default (ties go to the worse grade), or the worst model for a conservative profile.

| Category | Advisories |
|----------|-----------|
| Airport | Airport weather (flight category), surface wind and crosswind, density altitude, low-level wind shear, frost at departure (informational) |
| Flight rules | VFR feasibility, IFR feasibility, approach feasibility, departure feasibility |
| Wind | Winds aloft — headwind/tailwind trip impact |
| Icing | Icing graded for the aircraft's FIKI equipage, freezing precipitation, icing escape (descend below freezing with terrain clearance), FIKI icing (opt-in) |
| Cloud | Cloud tops vs ceiling, VMC at cruise |
| Convective | Convective activity, convective character (isolated → organised / embedded) |
| Precipitation | En-route precipitation and visibility |
| Turbulence | Turbulence (CAT + vertical motion), mountain wind (wave-corroborated) |
| Sun | Low-sun glare on the runway, day-VFR arrival near sunset |
| Fronts | Air-mass boundaries crossing the route (experimental, opt-in) |
| Model (diagnostics) | Forecast confidence, DD vs NWP agreement (off by default) |

Advisory parameters are user-tunable (terrain margins, percentage thresholds, etc.) and can be recalculated without re-fetching weather data.

## Tech Stack

**Backend:** Python 3.11+ / FastAPI / Pydantic v2 / SQLAlchemy + Alembic / MetPy / Matplotlib / LangChain+LangGraph / FastMCP

**Frontend:** TypeScript / Vanilla DOM (no framework) / Zustand / Canvas API / Leaflet / esbuild

**iOS / iPadOS:** Swift / SwiftUI / Xcode

**Infrastructure:** Docker / SQLite (dev) / MySQL (prod) / Multi-provider sign-in (Google, Apple, email link)

**Android:** Kotlin / Jetpack Compose (Material 3) / Gradle

**Testing:** pytest (~8,000 tests) / vitest / Playwright / Swift Testing (iOS) / JUnit (Android) — gated by CI on every push and PR; the iOS and Android UI journeys run nightly

## Project Structure

```
src/weatherbrief/
├── models/           # Pydantic v2 data models
├── pipeline.py       # Core engine: fetch → analyze → outputs
├── tasks/            # Independently runnable pipeline stages (fetch, analyze, advise, …)
├── fetch/            # Data retrieval (GRIB2 direct, Open-Meteo, SRTM, DWD/NWS text, front charts)
├── observed/         # Radar, lightning and satellite ingest sampled along the route
├── analysis/
│   ├── wind.py       # Headwind/crosswind decomposition
│   ├── comparison.py # Multi-model divergence scoring
│   ├── advisories/   # 20+ route hazard evaluators (registry pattern)
│   └── sounding/     # MetPy thermodynamic analysis (clouds, icing, CAT, convective)
├── frontal/          # Frontal zone detection
├── hewson/           # Hewson θe diagnostic field precompute (NPZ snapshots)
├── era5/             # ERA5 reanalysis loaders for retrospective calibration
├── digest/           # Text digest, Skew-T plots, LLM briefing (LangGraph)
├── api/              # FastAPI app (auth, flights, packs, live layer, preferences, admin)
├── mcp/              # MCP server exposing briefings as AI-assistant tools
├── connectors/       # Shared agent-facing response shaping (MCP + GPT/OpenAPI)
├── db/               # SQLAlchemy ORM + Alembic migrations
├── storage/          # File-based artifact persistence
├── report/           # HTML/PDF rendering (Jinja2 + WeasyPrint)
├── notify/           # Email delivery + push notifications
├── verify/           # Forecast verification vs METAR/TAF (collect, stats, digest, archive)
├── debriefs/         # Post-flight pilot judgement capture
├── analytics/        # Privacy-first usage analytics
├── costs.py          # Cost ledger / spend tracking
├── eval_workbench/   # Dev-only golden-labelling workbench for the LLM digest eval
├── scenario/         # Scenario previews ("where/when could I fly") — measurement harness
├── triage/           # AI-assisted feedback triage CLI
└── release/          # Release-notes / What's New CLI

web/
├── ts/
│   ├── visualization/  # Canvas cross-section renderer (~25 layers)
│   ├── store/          # Zustand state management
│   ├── managers/       # DOM rendering (briefing, advisories, flights)
│   ├── adapters/       # API communication
│   ├── i18n/           # Locale bundles (en, fr, de, es)
│   └── data/           # Metrics catalog, display config
├── tests/              # Playwright e2e specs + vitest unit tests
├── css/style.css
├── index.html          # Flights list
├── briefing.html       # Briefing report (collapsible sections)
├── flight.html         # Single flight detail / edit
├── trip.html           # Trip (several legs) view
├── maps.html           # Maps: forecast, now, synoptic, historical, climatology, accuracy
├── verification.html   # Forecast verification / model skill (admin)
├── settings.html       # User preferences + advisory tuning
├── admin.html          # Users, usage, costs and performance (admin)
├── login.html          # Authentication page
├── help.html           # User help / FAQ + What's New
├── donate.html         # Optional support / donations (Stripe)
├── privacy.html        # Privacy policy
├── eval.html           # LLM digest eval workbench (dev/admin)
├── embed.html          # Frameable cross-section (used by talks.flyfun.aero)
├── cost-summary.html   # Service cost transparency (admin)
└── user-costs.html     # Personal usage & cost tracking

app/flyfun-weather/   # Native iOS / iPadOS app (SwiftUI, Xcode project)
app/android/          # Native Android app (Kotlin, Jetpack Compose)
configs/              # LLM digest configuration and prompts
designs/              # Design documentation (see designs/INDEX.md)
tests/                # pytest test suite (~8,000 tests)
alembic/versions/     # Database migrations
```

## Getting Started

### Prerequisites

- Python 3.11+ (CI runs 3.13)
- Node.js 22+ (for frontend build)
- SQLite (development) or MySQL (production)

### Installation

```bash
# Clone the repository
git clone https://github.com/roznet/flyfun-weather.git
cd flyfun-weather

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install -e ".[dev]"

# Install frontend dependencies and build
cd web
npm install
npm run build
cd ..
```

### Environment Variables

Copy [`.env.sample`](.env.sample) to `.env` in the project root and fill in what you need.
The essentials:

```env
ENVIRONMENT=development

# Optional — for LLM-powered digest
ANTHROPIC_API_KEY=sk-...
OPENAI_API_KEY=sk-...


# Required for production multi-user deployment
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
JWT_SECRET=...
CREDENTIAL_ENCRYPTION_KEY=...
DATABASE_URL=mysql+pymysql://...
```

In development mode, the app uses SQLite and auto-creates a dev user — no OAuth setup needed.

### Running

**Web app (development):**

```bash
# Start the API server
uvicorn weatherbrief.api.app:app --reload --port 8000

# In another terminal — watch and rebuild frontend
cd web && npm run dev
```

Then open http://localhost:8000

The repo also wraps this for day-to-day work: `scripts/ops/devserver.py` starts uvicorn and the esbuild watch in a tmux session (port 8000 for `main`, 8001+ for worktrees), and `scripts/ops/worktree_init.py <branch>` creates a sibling worktree with its own venv, npm install and `.env`.

**MCP server (AI-assistant tools):**

```bash
python -m weatherbrief.mcp
```

Other operational entry points follow the same pattern — `python -m weatherbrief.verify`
(forecast verification), `python -m weatherbrief.hewson` (frontal diagnostic precompute),
`python -m weatherbrief.release` (What's New stream). Each prints its own usage.

### Testing

The full suite runs in CI on every push and PR (see the badge above); to run it locally:

```bash
# Python — ~8,000 tests. `-m 'not slow'` is applied by default via pyproject
source venv/bin/activate
pytest

# Frontend typecheck, build and unit tests
cd web
npx tsc --noEmit
npm run build
npx vitest run

# End-to-end (boots the API server itself; needs the venv at the repo root)
npx playwright install --with-deps chromium
npx playwright test
```

```bash
# iOS unit tests, run from the repo root (the block above leaves you in web/).
# The UI tests are excluded here, as they are in CI: they drive a simulator and are
# run manually.
xcodebuild test \
  -project app/flyfun-weather/flyfun-weather.xcodeproj \
  -scheme flyfun-weather \
  -destination 'platform=iOS Simulator,name=iPhone 17,OS=latest' \
  -only-testing:flyfun-weatherTests
```

CI is path-filtered so the jobs don't overlap.
[`.github/workflows/tests.yml`](.github/workflows/tests.yml) runs three parallel jobs —
`pytest`, `typecheck + build + vitest`, and `playwright`;
[`.github/workflows/ios.yml`](.github/workflows/ios.yml) and
[`.github/workflows/android.yml`](.github/workflows/android.yml) run the app unit tests;
[`.github/workflows/alembic-check.yml`](.github/workflows/alembic-check.yml) checks the
migrations. A commit touching `app/` runs the app jobs, anything else runs the
server/web jobs, and a commit spanning both runs both. Commits touching only
`designs/`, `.claude/` or Markdown skip CI entirely. The iOS and Android UI journeys
are not gated: they run nightly on `main` (`ios-ui-nightly.yml`, `android-ui-nightly.yml`).

### Docker

```bash
docker build -t weatherbrief .
docker compose up -d
```

The Docker image runs as a non-root user (UID 2000). Data is persisted via volume mount at `/app/data`.

## Data Sources

- **NWP forecasts** — GRIB direct from ECMWF, DWD, NOAA, Météo-France and the Met Office (UKV on AWS), with [Open-Meteo](https://open-meteo.com/) as fallback and for Best Match
- **METAR / TAF & SIGMETs** — [NOAA Aviation Weather Center](https://aviationweather.gov/data/api/) for day-of observations, terminal forecasts, and route SIGMETs
- **Radar** — [EUMETNET OPERA](https://www.eumetnet.eu/activities/observations-programme/current-activities/opera/) pan-European composite, with the Italian Civil Protection (Radar-DPC) mosaic filling OPERA's Italy gap
- **Lightning & satellite** — [EUMETSAT](https://www.eumetsat.int/) MTG Lightning Imager, cloud-top height/temperature, and EUMETView infrared imagery
- **Surface pressure & front charts** — DWD, Met Office, and Météo-France (incl. TEMSI)
- **Terrain elevation** — [SRTM](https://www.usgs.gov/centers/eros/science/usgs-eros-archive-digital-elevation-shuttle-radar-topography-mission-srtm-1) 90m resolution via srtm.py
- **Airport database** — [euro-aip](https://github.com/roznet/rzflight) (European AIP data)
- **Navigation waypoints** — Eurocontrol FRA, OpenNav, OurAirports NAVAIDs, and FAA NASR (free public sources; ~95,900 deduplicated points on the 28-day AIRAC/NASR cycle)
- **Synoptic text forecasts** — DWD (German Weather Service) open data; NWS Area Forecast Discussions for US routes

## How the Analysis Works

The pipeline fetches NWP data for ~20 interpolated points along your route (every ~20nm), plus your departure/arrival airports. For each point and model, it runs a MetPy-based sounding analysis that computes:

- **Thermodynamic indices** — CAPE (surface, most-unstable, mixed-layer), CIN, Lifted Index, K-Index, Total Totals, Showalter Index
- **Cloud layers** — detected from dewpoint depression at pressure levels, classified by coverage (SCT/BKN/OVC)
- **Icing zones** — using the Ogimet continuous icing index (physically peaks at -7C matching observed supercooled liquid water), with type classification (clear/mixed/rime) from temperature bands
- **CAT turbulence** — Richardson number from Brunt-Vaisala frequency and wind shear, classified NONE/LIGHT/MODERATE/SEVERE
- **Convective risk** — from CAPE thresholds with CIN modulation and severe weather modifiers (shear, hail indicators)
- **Vertical motion** — omega profiles classified as quiescent, synoptic ascent/subsidence, oscillating, or convective

These per-point results feed into the 20+ route-level advisory evaluators that produce the GREEN/AMBER/RED hazard assessment.

## Known Limitations

- **Not a certified weather product**
- **Vertical resolution varies by model** — where a model falls back to Open-Meteo its pressure levels are coarser than the direct GRIB, so thin cloud layers can still be missed
- **No ensemble data** — currently uses deterministic runs only (except precipitation probability from GFS/ECMWF)
- **European focus** — airport database and some features (DWD text, observed radar/lightning, regional models) are Europe-centric
- **NWP cloud vs sounding cloud** — two independent cloud detection methods can disagree (see design docs for details)

## Design Documentation

The `designs/` directory contains detailed design documents covering architecture, data models, analysis methods, metrics catalog, and implementation plans. Start with [`designs/architecture.md`](designs/architecture.md) for the system overview, or [`designs/INDEX.md`](designs/INDEX.md) for the full module map.

## AI Acknowledgment

This project was built extensively with AI assistance. [Claude](https://claude.ai) (Anthropic) was used throughout development for:

- Architecture design and implementation planning
- Weather analysis algorithms and metric derivations
- Aviation-specific threshold selection and advisory logic
- Frontend visualization (canvas rendering, interaction patterns)
- Code implementation across the full stack

The meteorological explanations, threshold values, and aviation interpretations in the codebase were developed collaboratively with AI. While cross-referenced against published references (MetPy documentation, Ogimet/Autorouter formulas, standard aviation weather texts), they have not been reviewed by a professional meteorologist.

The optional LLM-powered weather digest feature uses Claude or ChatGPT to generate narrative briefings from the quantitative analysis.

## License

MIT

## Contributing

Issues and discussions are welcome. If you're a meteorologist or aviation weather professional and spot something wrong, please open an issue — corrections are very much appreciated.

If you're sending a pull request, the [test suite](#testing) gates every PR — running
`pytest` and the web checks locally first will save you a round trip. Migrations must
work on both SQLite (dev) and MySQL (prod); use `batch_alter_table` for any `ALTER`.
