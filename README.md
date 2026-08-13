# Flyfun Weather

[![Tests](https://github.com/roznet/flyfun-weather/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/roznet/flyfun-weather/actions/workflows/tests.yml)
[![iOS](https://github.com/roznet/flyfun-weather/actions/workflows/ios.yml/badge.svg?branch=main)](https://github.com/roznet/flyfun-weather/actions/workflows/ios.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**Medium-range aviation weather assessment for cross-country GA flights in Europe**

🌍 **[weather.flyfun.aero](https://weather.flyfun.aero)** — live web app &nbsp;·&nbsp; 📱 **[iOS / iPadOS app](https://apps.apple.com/app/id6760951972)**

> The Python package and module are named `weatherbrief`; the product is **Flyfun Weather**.

Flyfun Weather fetches forecast data from multiple numerical weather prediction (NWP) models, performs aviation-specific analysis (icing, turbulence, convection, clouds), and presents everything side-by-side so pilots can compare models and start forming an early view of what conditions will look like from D-7 through D-0.

> **Disclaimer** — Flyfun Weather is built to help and support flight planning and decision-making, giving pilots an early, multi-model picture of the weather and surfacing the factors that matter for a route. It relies on automated analysis and AI, which can make mistakes or miss things, and it has not been reviewed by a professional meteorologist. It is **not** a substitute for official weather briefings, MET reports, or professional meteorological advice. Always consult official sources before flying — the pilot in command remains the sole decision-maker.

## What it does

- **Multi-model forecasting** — Fetches 6 NWP models via Open-Meteo (GFS, ECMWF IFS, DWD ICON, UKMO, Meteo-France, Best Match) at up to 28 pressure levels along your route, with high-resolution **GRIB2 enrichment** (upper-air soundings, cloud microphysics) direct from ECMWF IFS, DWD ICON-EU, and NOAA GFS where coverage allows
- **~85 derived metrics** — From raw NWP data, derives thermodynamic indices (CAPE, CIN, Lifted Index, K-Index, etc.), cloud layers, icing zones (multiple methods), CAT turbulence risk, convective potential, wind shear, and more using MetPy
- **20+ route advisories across 11 categories** — Deterministic hazard evaluators (icing incl. freezing precipitation, cloud, en-route visibility/precipitation, turbulence incl. wave-corroborated mountain wind, convective incl. terminal convective & LLWS, winds-aloft trip impact, airport conditions incl. density altitude, feasibility, model quality, fronts, sun/daylight) with per-model GREEN/AMBER/RED severity grading and worst/majority aggregation
- **D-0 observations** — METAR/TAF for departure/arrival airports plus route **SIGMETs** (area hazards), with a deterministic banner flagging conditions that have worsened since the last refresh
- **Weather-based alternates** — When a destination is marginal (D-2 inward), surfaces nearby divert candidates that fix the deficient axis (category/wind/crosswind), classified before/after along the route, plus a regulatory **"is an alternate required?"** estimate (FAA 14 CFR 91.169 + EASA Part-NCO)
- **Model comparison** — Side-by-side divergence scoring so you can see where models agree and where they don't
- **Interactive cross-section** — Canvas-rendered visualization with ~25 toggleable layers (terrain, clouds, icing, SFIP, CAT, inversions, convective, temperature lines, stability levels, NWP cloud bands), switchable themes, a compare-across-models mode, and hover/click interaction
- **Route map & route graph** — Leaflet route map with an altitude slider and metric-colored segments (incl. an "alternate required?" mode), plus a scalar route graph sharing the cross-section's x-axis
- **Skew-T soundings** — Per-waypoint, per-model diagrams: a dynamic canvas Skew-T with a multi-variable side panel and overlay bands, a multi-model compare mode, and the classic MetPy PNG (CAPE/CIN shading, hodograph, indices panel)
- **GRAMET cross-section** — From the Autorouter API (requires credentials)
- **LLM-powered synopsis** — Optional AI-generated weather narrative via Claude or ChatGPT, combining DWD synoptic text with quantitative analysis; beyond the high-resolution horizon it switches to a cheaper, confidence-led **long-range early outlook**
- **Navigation database** — Resolves non-airport waypoints (5-letter fixes, navaids, free-route points) from ~95,900 deduplicated points built from four free public sources, refreshed on the 28-day AIRAC/NASR cycle
- **PDF/HTML reports & email** — Self-contained briefing reports you can download or email to yourself
- **Terrain-aware** — SRTM 90m elevation profiles for mountain crossing risk assessment
- **Pan-European forecast map** — Standalone map of the same derived metrics across Europe, independent of any single route
- **Native iOS / iPadOS app** — SwiftUI client sharing the same API: flights, briefing, cross-section, map, Skew-T, push notifications when a briefing is ready
- **PIREPs** — File your own pilot reports and read others' (permission-gated per user)
- **Post-flight debrief** — Rate a briefing after you fly, so assessments can be checked against what actually happened
- **Forecast verification** — Model forecasts scored against later observations and analyses, feeding a skill view per model and metric (admin-facing while it settles)
- **MCP server** — Exposes flights, briefings, advisory detail and airport weather as tools for AI assistants (Claude, ChatGPT), with OAuth 2.1

## Weather Models

| Model | Source | Forecast range |
|-------|--------|---------------|
| **Best Match** | Open-Meteo auto-select | 16 days |
| **ECMWF IFS** | European Centre | 10 days |
| **GFS** | NOAA (US) | 16 days |
| **DWD ICON** | German Weather Service | 7 days |
| **UKMO** | UK Met Office | 7 days |
| **Meteo-France** | Meteo-France Arpege | 6 days |

Not all variables are available from all models (e.g., omega/vertical velocity is missing from ICON and Meteo-France, visibility from ECMWF). Flyfun Weather derives fallbacks where possible and clearly indicates when data is unavailable.

## Route Advisories

Each advisory evaluates a specific weather hazard along your route, per model. The set has grown to 20+ evaluators across 11 categories; a representative selection:

| Advisory | What it checks |
|----------|---------------|
| Icing Escape | Can you descend below freezing to escape icing? (terrain clearance) |
| FIKI Icing | Icing layer thickness and severity for FIKI-equipped aircraft |
| Freezing Level | Freezing level vs terrain (mountain icing risk) |
| Freezing Precipitation | Freezing rain / ice pellets in the column |
| Cloud Top | Can you fly above the clouds? (cloud top vs flight ceiling) |
| VMC Cruise | Cloud coverage at cruise altitude (VFR viability) |
| En-route Visibility | Visibility and precipitation along the route |
| VFR / IFR Feasibility | Overall VFR/IFR viability vs ceiling, visibility, and cloud layers |
| Flight Category | VFR/MVFR/IFR/LIFR classification at departure, en-route, and arrival |
| Turbulence | Clear Air Turbulence + strong vertical motion at cruise |
| Mountain Wind | Orographic/rotor wind risk near significant terrain (wave-corroborated) |
| Winds Aloft | Headwind/tailwind trip impact along the route |
| Airport Conditions | Surface wind, gusts, crosswind, and density altitude at departure/arrival |
| Terminal Convective / LLWS | Low-level wind shear and convective risk near the airports |
| Convective | Thunderstorm development risk from CAPE and instability indices |
| Fronts | Frontal passages crossing the route |
| Sun | Daylight / sun position relative to the flight window |
| Model Agreement | How much do the models agree with each other? |

Advisory parameters are user-tunable (terrain margins, percentage thresholds, etc.) and can be recalculated without re-fetching weather data.

## Tech Stack

**Backend:** Python 3.11+ / FastAPI / Pydantic v2 / SQLAlchemy + Alembic / MetPy / Matplotlib / LangChain+LangGraph / FastMCP

**Frontend:** TypeScript / Vanilla DOM (no framework) / Zustand / Canvas API / Leaflet / esbuild

**iOS / iPadOS:** Swift / SwiftUI / Xcode

**Infrastructure:** Docker / SQLite (dev) / MySQL (prod) / Multi-provider OAuth (Google, Apple)

**Testing:** pytest (~5,000 tests) / vitest / Playwright / Swift Testing (397 iOS unit tests) — all gated by CI on every push and PR

## Project Structure

```
src/weatherbrief/
├── models/           # Pydantic v2 data models
├── pipeline.py       # Core engine: fetch → analyze → outputs
├── tasks/            # Independently runnable pipeline stages (fetch, analyze, advise, …)
├── fetch/            # Data retrieval (Open-Meteo, GRIB2 enrichment, SRTM, GRAMET, DWD text)
├── analysis/
│   ├── wind.py       # Headwind/crosswind decomposition
│   ├── comparison.py # Multi-model divergence scoring
│   ├── advisories/   # 20+ route hazard evaluators (registry pattern)
│   └── sounding/     # MetPy thermodynamic analysis (clouds, icing, CAT, convective)
├── frontal/          # Frontal zone detection
├── hewson/           # Hewson θe diagnostic field precompute (NPZ snapshots)
├── era5/             # ERA5 reanalysis loaders for retrospective calibration
├── digest/           # Text digest, Skew-T plots, LLM briefing (LangGraph)
├── api/              # FastAPI app (auth, flights, packs, preferences, admin)
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
├── maps.html           # Pan-European forecast map
├── verification.html   # Forecast verification / model skill (admin)
├── pireps.html         # Pilot reports
├── settings.html       # User preferences + advisory tuning
├── admin.html          # User approval + usage tracking
├── login.html          # Authentication page
├── help.html           # User help / FAQ + What's New
├── donate.html         # Optional support / donations (Stripe)
├── privacy.html        # Privacy policy
├── eval.html           # LLM digest eval workbench (dev/admin)
├── cost-summary.html   # Service cost transparency (admin)
└── user-costs.html     # Personal usage & cost tracking

app/flyfun-weather/   # Native iOS / iPadOS app (SwiftUI, Xcode project)
configs/              # LLM digest configuration and prompts
designs/              # Design documentation (46 docs)
tests/                # pytest test suite (~200 modules, ~5,000 tests)
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

# Optional — for GRAMET cross-sections (Autorouter account)
AUTOROUTER_USERNAME=...
AUTOROUTER_PASSWORD=...

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
# Python — ~5,000 tests. `-m 'not slow'` is applied by default via pyproject
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
# iOS — 397 unit tests, run from the repo root (the block above leaves you in web/).
# The UI tests are excluded here, as they are in CI: they drive a simulator and are
# run manually.
xcodebuild test \
  -project app/flyfun-weather/flyfun-weather.xcodeproj \
  -scheme flyfun-weather \
  -destination 'platform=iOS Simulator,name=iPhone 17,OS=latest' \
  -only-testing:flyfun-weatherTests
```

CI is split across two workflows, path-filtered so they don't overlap.
[`.github/workflows/tests.yml`](.github/workflows/tests.yml) runs three parallel jobs —
`pytest`, `typecheck + build + vitest`, and `playwright` — and
[`.github/workflows/ios.yml`](.github/workflows/ios.yml) runs the iOS unit tests on a
macOS runner. A commit touching `app/` runs the iOS job, anything else runs the
server/web jobs, and a commit spanning both runs both. Commits touching only
`designs/`, `.claude/` or Markdown skip CI entirely.

### Docker

```bash
docker build -t weatherbrief .
docker compose up -d
```

The Docker image runs as a non-root user (UID 2000). Data is persisted via volume mount at `/app/data`.

## Data Sources

- **NWP forecasts** — [Open-Meteo](https://open-meteo.com/) (free, open-source weather API), plus high-resolution GRIB2 enrichment direct from ECMWF IFS, DWD ICON-EU, and NOAA GFS where available
- **METAR / TAF & SIGMETs** — [NOAA Aviation Weather Center](https://aviationweather.gov/data/api/) for day-of observations, terminal forecasts, and route SIGMETs
- **Terrain elevation** — [SRTM](https://www.usgs.gov/centers/eros/science/usgs-eros-archive-digital-elevation-shuttle-radar-topography-mission-srtm-1) 90m resolution via srtm.py
- **Airport database** — [euro-aip](https://github.com/roznet/rzflight) (European AIP data)
- **Navigation waypoints** — Eurocontrol FRA, OpenNav, OurAirports NAVAIDs, and FAA NASR (free public sources; ~95,900 deduplicated points on the 28-day AIRAC/NASR cycle)
- **GRAMET cross-sections** — [Autorouter](https://www.autorouter.aero/) (requires free account)
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

- **Not a certified weather product** — this is an exploratory tool for understanding NWP data
- **Vertical resolution varies by model** — GFS resolves 28 pressure levels (~25 hPa spacing in the lower atmosphere), but ECMWF offers only 13 via Open-Meteo, so thin cloud layers can still be missed on the coarser models
- **No ensemble data** — currently uses deterministic runs only (except precipitation probability from GFS/ECMWF)
- **European focus** — airport database and some features (DWD text, Autorouter GRAMET) are Europe-centric
- **NWP cloud vs sounding cloud** — two independent cloud detection methods can disagree (see design docs for details)

## Design Documentation

The `designs/` directory contains 40+ detailed design documents covering architecture, data models, analysis methods, metrics catalog, and implementation plans. Start with `designs/architecture.md` for the system overview, or `designs/INDEX.md` for the module map.

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

This is an early-stage personal project. Issues and discussions are welcome. If you're a meteorologist or aviation weather professional and spot something wrong, please open an issue — corrections are very much appreciated.

If you're sending a pull request, the [test suite](#testing) gates every PR — running
`pytest` and the web checks locally first will save you a round trip. Migrations must
work on both SQLite (dev) and MySQL (prod); use `batch_alter_table` for any `ALTER`.
