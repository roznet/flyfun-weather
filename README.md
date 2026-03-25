# WeatherBrief

**Medium-range aviation weather assessment for cross-country GA flights in Europe**

WeatherBrief fetches forecast data from multiple numerical weather prediction (NWP) models, performs aviation-specific analysis (icing, turbulence, convection, clouds), and presents everything side-by-side so pilots can compare models and start forming an early view of what conditions will look like from D-7 through D-0.

> **Disclaimer** — This is an early-stage exploratory tool, not built by a meteorologist. It is meant to help pilots explore the wealth of NWP data available and see how models converge (or diverge) on metrics that matter for flying. It is **not** a substitute for official weather briefings, MET reports, or professional meteorological advice. Always consult official sources before flying.

## What it does

- **Multi-model forecasting** — Fetches from 6 NWP models via Open-Meteo (GFS, ECMWF IFS, DWD ICON, UKMO, Meteo-France, Best Match) at 8 pressure levels along your route
- **~40 derived metrics** — From raw NWP data, derives thermodynamic indices (CAPE, CIN, Lifted Index, K-Index, etc.), cloud layers, icing zones, CAT turbulence risk, convective potential, wind shear, and more using MetPy
- **13 route advisories** — Deterministic hazard evaluators (icing escape, FIKI icing, freezing level, cloud top, VMC cruise, VFR/IFR feasibility, flight category, turbulence, mountain wind, airport wind, convective, model agreement) with per-model GREEN/AMBER/RED severity grading
- **Model comparison** — Side-by-side divergence scoring across 15 metrics so you can see where models agree and where they don't
- **Interactive cross-section** — Canvas-rendered visualization with 17 toggleable layers (terrain, clouds, icing, SFIP, CAT, inversions, convective, temperature lines, stability levels, NWP cloud bands) and hover/click interaction
- **Skew-T soundings** — Per-waypoint, per-model diagrams with CAPE/CIN shading, hodograph, and indices panel
- **GRAMET cross-section** — From the Autorouter API (requires credentials)
- **LLM-powered synopsis** — Optional AI-generated weather narrative via Claude or ChatGPT, combining DWD synoptic text with quantitative analysis
- **PDF/HTML reports & email** — Self-contained briefing reports you can download or email to yourself
- **Terrain-aware** — SRTM 90m elevation profiles for mountain crossing risk assessment

## Weather Models

| Model | Source | Forecast range |
|-------|--------|---------------|
| **Best Match** | Open-Meteo auto-select | 16 days |
| **ECMWF IFS** | European Centre | 10 days |
| **GFS** | NOAA (US) | 16 days |
| **DWD ICON** | German Weather Service | 7 days |
| **UKMO** | UK Met Office | 7 days |
| **Meteo-France** | Meteo-France Arpege | 6 days |

Not all variables are available from all models (e.g., omega/vertical velocity is missing from ICON and Meteo-France, visibility from ECMWF). WeatherBrief derives fallbacks where possible and clearly indicates when data is unavailable.

## Route Advisories

Each advisory evaluates a specific weather hazard along your route, per model:

| Advisory | What it checks |
|----------|---------------|
| Icing Escape | Can you descend below freezing to escape icing? (terrain clearance) |
| FIKI Icing | Icing layer thickness and severity for FIKI-equipped aircraft |
| Freezing Level | Freezing level vs terrain (mountain icing risk) |
| Cloud Top | Can you fly above the clouds? (cloud top vs flight ceiling) |
| VMC Cruise | Cloud coverage at cruise altitude (VFR viability) |
| VFR Feasibility | Overall VFR viability considering ceiling, visibility, and cloud layers |
| IFR Feasibility | IFR flight viability considering ceiling and visibility minimums |
| Flight Category | VFR/MVFR/IFR/LIFR classification at departure, en-route, and arrival |
| Turbulence | Clear Air Turbulence + strong vertical motion at cruise |
| Mountain Wind | Orographic/rotor wind risk near significant terrain |
| Airport Wind | Surface wind and crosswind at departure and arrival airports |
| Convective | Thunderstorm development risk from CAPE and instability indices |
| Model Agreement | How much do the models agree with each other? |

Advisory parameters are user-tunable (terrain margins, percentage thresholds, etc.) and can be recalculated without re-fetching weather data.

## Tech Stack

**Backend:** Python 3.12+ / FastAPI / Pydantic v2 / SQLAlchemy / MetPy / Matplotlib / LangChain+LangGraph

**Frontend:** TypeScript / Vanilla DOM (no framework) / Zustand / Canvas API / esbuild

**Infrastructure:** Docker / SQLite (dev) / MySQL (prod) / Multi-provider OAuth (Google, Apple)

## Project Structure

```
src/weatherbrief/
├── models/           # Pydantic v2 data models
├── pipeline.py       # Core engine: fetch → analyze → outputs
├── fetch/            # Data retrieval (Open-Meteo, SRTM elevation, GRAMET, DWD text)
├── analysis/
│   ├── wind.py       # Headwind/crosswind decomposition
│   ├── comparison.py # Multi-model divergence scoring
│   ├── advisories/   # 13 route hazard evaluators (registry pattern)
│   └── sounding/     # MetPy thermodynamic analysis (clouds, icing, CAT, convective)
├── digest/           # Text digest, Skew-T plots, LLM briefing (LangGraph)
├── api/              # FastAPI app (auth, flights, packs, preferences, admin)
├── db/               # SQLAlchemy ORM + Alembic migrations
├── storage/          # File-based artifact persistence
├── report/           # HTML/PDF rendering (Jinja2 + WeasyPrint)
└── notify/           # Email delivery

web/
├── ts/
│   ├── visualization/  # Canvas cross-section renderer (17 layers)
│   ├── store/          # Zustand state management
│   ├── managers/       # DOM rendering (briefing, advisories, flights)
│   ├── adapters/       # API communication
│   └── data/           # Metrics catalog, display config
├── css/style.css
├── index.html          # Flights list
├── briefing.html       # Briefing report (10 collapsible sections)
├── flight.html         # Single flight detail / edit
├── settings.html       # User preferences + advisory tuning
├── admin.html          # User approval + usage tracking
├── login.html          # Authentication page
├── help.html           # User help / FAQ
└── user-costs.html     # Personal usage & cost tracking

configs/              # LLM digest configuration and prompts
designs/              # Design documentation (21 docs)
tests/                # pytest test suite
```

## Getting Started

### Prerequisites

- Python 3.12+
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

Create a `.env` file in the project root:

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

**CLI (single briefing):**

```bash
weatherbrief EGTK LFQA LSGS --date 2026-03-15 --time 9 --alt 8000
```

### Docker

```bash
docker build -t weatherbrief .
docker compose up -d
```

The Docker image runs as a non-root user (UID 2000). Data is persisted via volume mount at `/app/data`.

## Data Sources

- **NWP forecasts** — [Open-Meteo](https://open-meteo.com/) (free, open-source weather API)
- **Terrain elevation** — [SRTM](https://www.usgs.gov/centers/eros/science/usgs-eros-archive-digital-elevation-shuttle-radar-topography-mission-srtm-1) 90m resolution via srtm.py
- **Airport database** — [euro-aip](https://github.com/roznet/rzflight) (European AIP data)
- **GRAMET cross-sections** — [Autorouter](https://www.autorouter.aero/) (requires free account)
- **Synoptic text forecasts** — DWD (German Weather Service) open data

## How the Analysis Works

The pipeline fetches NWP data for ~20 interpolated points along your route (every ~20nm), plus your departure/arrival airports. For each point and model, it runs a MetPy-based sounding analysis that computes:

- **Thermodynamic indices** — CAPE (surface, most-unstable, mixed-layer), CIN, Lifted Index, K-Index, Total Totals, Showalter Index
- **Cloud layers** — detected from dewpoint depression at pressure levels, classified by coverage (SCT/BKN/OVC)
- **Icing zones** — using the Ogimet continuous icing index (physically peaks at -7C matching observed supercooled liquid water), with type classification (clear/mixed/rime) from temperature bands
- **CAT turbulence** — Richardson number from Brunt-Vaisala frequency and wind shear, classified NONE/LIGHT/MODERATE/SEVERE
- **Convective risk** — from CAPE thresholds with CIN modulation and severe weather modifiers (shear, hail indicators)
- **Vertical motion** — omega profiles classified as quiescent, synoptic ascent/subsidence, oscillating, or convective

These per-point results feed into the 13 route-level advisory evaluators that produce the GREEN/AMBER/RED hazard assessment.

## Known Limitations

- **Not a certified weather product** — this is an exploratory tool for understanding NWP data
- **Coarse vertical resolution** — 8 pressure levels (1000-300 hPa) means thin cloud layers can be missed
- **No ensemble data** — currently uses deterministic runs only (except precipitation probability from GFS/ECMWF)
- **European focus** — airport database and some features (DWD text, Autorouter GRAMET) are Europe-centric
- **NWP cloud vs sounding cloud** — two independent cloud detection methods can disagree (see design docs for details)

## Design Documentation

The `designs/` directory contains 21 detailed design documents covering architecture, data models, analysis methods, metrics catalog, and implementation plans. Start with `designs/architecture.md` for the system overview.

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
