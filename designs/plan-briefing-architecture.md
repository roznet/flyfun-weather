# Plan: Briefing Page & API Architecture

> API-first multi-client architecture for flight briefing configuration, retrieval, history, and a web-based briefing report page.

## Intent

WeatherBrief currently operates as a CLI tool. This plan introduces:

1. **A "Flight" concept** — a saved, refreshable briefing target (route + date + time + duration).
2. **Bundled briefing packs** — each fetch produces a pack containing snapshot, GRAMET, Skew-Ts, and LLM digest, stored together for full history.
3. **An API layer** — so any client (web app, iOS app, CLI-to-email, cron automation) can trigger fetches, retrieve history, and access all artifacts.
4. **A web app** — starting with a briefing report page: refresh, history, synopsis, GRAMET, model comparison, and Skew-T route view.

This builds on Phases 1–3 (done) and runs in parallel with Phase 4 (model comparison refinement). It represents the "multi-client + web UI" ambition from Phase 5.

---

## Key Concepts

### Flight

A **Flight** is a saved briefing target that persists across refreshes:

```
Flight:
  id: str                     # slug, e.g. "egtk-lsgs-2026-02-21"
  route_name: str             # references routes.yaml key, e.g. "egtk_lsgs"
  target_date: str            # YYYY-MM-DD
  target_time_utc: int        # departure hour UTC (e.g. 9)
  cruise_altitude_ft: int     # override or from route default
  flight_duration_hours: float
  created_at: datetime
```

A Flight is the unit people interact with: "my Oxford-to-Sion trip on Feb 21st." It references a route (reusable) and adds the date/time specifics. Refreshing a Flight triggers a new fetch, producing a new BriefingPack in the history.

### BriefingPack

A **BriefingPack** is the complete output of one fetch for a Flight:

```
BriefingPack:
  flight_id: str
  fetch_timestamp: datetime   # when this fetch happened
  days_out: int               # D-N
  snapshot_path: str          # snapshot.json
  gramet_path: str | None     # gramet.png (if fetched)
  skewt_paths: list[str]      # [{ICAO}_{model}.png, ...]
  digest_path: str | None     # digest.md
  digest_data: WeatherDigest | None  # structured LLM output
```

Each BriefingPack lives in a directory. History is the ordered list of all packs for a Flight.

---

## Storage Layout Evolution

Extend the current `data/` layout to group all artifacts per fetch under one directory, and add a flight-level index:

```
data/
├── flights.json                    # Flight registry (list of Flight configs)
└── flights/
    └── {flight_id}/
        ├── flight.json             # Flight config
        └── packs/
            └── {fetch_timestamp}/  # ISO format, e.g. 2026-02-19T18-00-00Z
                ├── snapshot.json   # ForecastSnapshot (existing model)
                ├── gramet.png      # GRAMET cross-section (if available)
                ├── skewt/
                │   ├── EGTK_gfs.png
                │   ├── EGTK_ecmwf.png
                │   ├── LFPB_gfs.png
                │   └── ...
                ├── digest.md       # Formatted LLM digest
                └── pack.json       # BriefingPack metadata (paths, digest_data)
```

**Why this layout:**
- All artifacts for one fetch are co-located (easy to zip, serve, compare, delete).
- Flight-level grouping makes history enumeration trivial (list dirs in `packs/`).
- `pack.json` is a lightweight index so we don't need to load the full snapshot just to show the history dropdown.
- Backward-compatible: the existing `data/forecasts/` layout continues to work for CLI usage. The new layout is additive — API/web uses `data/flights/`.

### Migration from current layout

The current storage (`data/forecasts/{target_date}/d-{N}_{fetch_date}/`) is keyed by target date, not by flight. The new layout keys by flight ID. Options:

- **Option A: Clean break** — new API/web uses `data/flights/` exclusively. Existing CLI snapshots stay where they are.
- **Option B: Symlink migration** — write a one-time migration that detects route from existing snapshots and creates flight entries.

**Recommendation: Option A** — clean break. The existing CLI still works as-is. New flights created via API get the new layout. Future CLI enhancement could optionally adopt the new layout too.

---

## Data Models (new)

Add to `models.py` or new `models_api.py`:

```python
class Flight(BaseModel):
    """A saved briefing target — route + date/time specifics."""
    id: str                          # slug: "{route_name}-{target_date}"
    route_name: str                  # key in routes.yaml
    target_date: str                 # YYYY-MM-DD
    target_time_utc: int = 9         # departure hour
    cruise_altitude_ft: int = 8000
    flight_duration_hours: float = 0.0
    created_at: datetime

class BriefingPackMeta(BaseModel):
    """Metadata for one fetch — lightweight index for history listing."""
    flight_id: str
    fetch_timestamp: str             # ISO datetime
    days_out: int
    has_gramet: bool = False
    has_skewt: bool = False
    has_digest: bool = False
    assessment: str | None = None    # GREEN/AMBER/RED from digest
    assessment_reason: str | None = None
```

These models keep the history dropdown fast: load `pack.json` (small) rather than the full `snapshot.json` (potentially large with all pressure-level data).

---

## API Design

### Framework Choice: FastAPI

**Why FastAPI:**
- Native Pydantic v2 integration (our models work as request/response schemas directly).
- Auto-generated OpenAPI docs (immediate Swagger UI for testing).
- Async support for concurrent fetches.
- Lightweight — no ORM needed (we're file-based).
- Well-supported in the Python ecosystem.

### Endpoints

#### Routes (reusable route definitions)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/routes` | List all named routes from routes.yaml |
| `GET` | `/api/routes/{name}` | Get route details (waypoints, defaults) |
| `POST` | `/api/routes` | Create new named route |

#### Flights (briefing targets)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/flights` | List all saved flights |
| `POST` | `/api/flights` | Create a new flight (route + date + time) |
| `GET` | `/api/flights/{id}` | Get flight details + latest pack summary |
| `DELETE` | `/api/flights/{id}` | Delete flight and all its packs |

#### Briefing Packs (fetch history)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/flights/{id}/refresh` | Trigger a new fetch → returns new pack |
| `GET` | `/api/flights/{id}/packs` | List all packs (history dropdown data) |
| `GET` | `/api/flights/{id}/packs/{timestamp}` | Get full pack detail |
| `GET` | `/api/flights/{id}/packs/{timestamp}/snapshot` | Raw ForecastSnapshot JSON |
| `GET` | `/api/flights/{id}/packs/{timestamp}/gramet` | GRAMET image (PNG) |
| `GET` | `/api/flights/{id}/packs/{timestamp}/skewt/{icao}/{model}` | Specific Skew-T (PNG) |
| `GET` | `/api/flights/{id}/packs/{timestamp}/digest` | LLM digest (markdown + structured) |
| `GET` | `/api/flights/{id}/packs/latest` | Redirect/alias to most recent pack |

#### Utility

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/models` | List available NWP models |
| `GET` | `/health` | Health check |

### Request/Response examples

**Create flight:**
```
POST /api/flights
{
  "route_name": "egtk_lsgs",
  "target_date": "2026-02-21",
  "target_time_utc": 9,
  "cruise_altitude_ft": 8000,
  "flight_duration_hours": 4.5
}
→ 201: { "id": "egtk_lsgs-2026-02-21", "route_name": "egtk_lsgs", ... }
```

**Refresh (trigger new fetch):**
```
POST /api/flights/egtk_lsgs-2026-02-21/refresh
→ 202: { "fetch_timestamp": "2026-02-19T18:00:00Z", "days_out": 2, "status": "fetching" }
```

The refresh endpoint runs the existing `run_fetch` pipeline (refactored to return results instead of printing), saving all artifacts into a new pack directory.

**History listing:**
```
GET /api/flights/egtk_lsgs-2026-02-21/packs
→ 200: [
    { "fetch_timestamp": "2026-02-19T18:00:00Z", "days_out": 2,
      "assessment": "GREEN", "assessment_reason": "Ridge established...",
      "has_gramet": true, "has_skewt": true, "has_digest": true },
    { "fetch_timestamp": "2026-02-18T08:00:00Z", "days_out": 3,
      "assessment": "AMBER", ... },
    ...
  ]
```

### Refactoring `run_fetch` for API use

The current `cli.py:run_fetch()` is monolithic — it prints to console and calls `sys.exit()`. To support API use:

1. **Extract core pipeline** into a new function (e.g., `pipeline.py:execute_briefing()`) that:
   - Takes a `Flight` (or route + date + time params).
   - Returns a `BriefingPackResult` (all paths + structured digest data).
   - Does not print or exit — raises exceptions.
   - Saves artifacts to the flight's pack directory.

2. **CLI calls the pipeline** — `cli.py` becomes a thin wrapper that calls `execute_briefing()` and prints results.

3. **API calls the same pipeline** — FastAPI endpoint calls `execute_briefing()` and returns JSON.

This is the critical refactor: **one pipeline, multiple frontends**.

```
                ┌──────────────┐
                │  CLI (cli.py) │──── prints text ──→ terminal
                └──────┬───────┘
                       │
                       ▼
              ┌─────────────────┐
              │  execute_briefing │ ←── core pipeline
              │  (pipeline.py)   │
              └──────┬──────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   ┌─────────┐ ┌──────────┐ ┌──────────┐
   │ fetch/  │ │ analysis/│ │ digest/  │
   │ modules │ │ modules  │ │ modules  │
   └─────────┘ └──────────┘ └──────────┘
                       │
                       ▼
              ┌─────────────────┐
              │  BriefingPack    │ ←── saved to disk
              │  (storage)       │
              └──────┬──────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   ┌─────────┐ ┌──────────┐ ┌──────────┐
   │ CLI     │ │ API      │ │ Cron/    │
   │ output  │ │ response │ │ Email    │
   └─────────┘ └──────────┘ └──────────┘
```

---

## Web App Architecture

### Technology choice

| Option | Pros | Cons |
|--------|------|------|
| **React SPA + FastAPI backend** | Rich interactivity, large ecosystem, familiar | Build toolchain, separate deploy |
| **FastAPI + HTMX + Jinja** | Minimal JS, fast to build, single deploy | Less interactive, harder for complex state |
| **Next.js** | SSR, great DX, API routes built in | Node.js dependency, separate from Python |

**Recommendation: React SPA (Vite) + FastAPI backend.**

Rationale: The briefing page has real interactivity needs (model toggle for Skew-T, history dropdown, tabbed sections). React handles this well. Vite keeps the dev experience fast. The API-first design means the frontend is fully decoupled — an iOS app or CLI email sender hits the same API.

### Package structure

```
src/
├── weatherbrief/          # existing Python package
│   ├── api/               # NEW: FastAPI app
│   │   ├── __init__.py
│   │   ├── app.py         # FastAPI app factory, CORS, static mount
│   │   ├── routes.py      # /api/routes endpoints
│   │   ├── flights.py     # /api/flights endpoints
│   │   └── packs.py       # /api/flights/{id}/packs endpoints
│   ├── pipeline.py        # NEW: core briefing pipeline (extracted from cli.py)
│   └── ...existing modules...
└── web/                   # NEW: React frontend
    ├── package.json
    ├── vite.config.ts
    └── src/
        ├── App.tsx
        ├── api/            # API client hooks
        ├── pages/
        │   ├── FlightsPage.tsx    # flight list + create
        │   └── BriefingPage.tsx   # the main briefing report
        └── components/
            ├── Synopsis.tsx
            ├── GrametViewer.tsx
            ├── ModelComparison.tsx
            ├── SkewTRoute.tsx
            └── HistoryDropdown.tsx
```

### Development mode

- `npm run dev` (Vite) proxies `/api/*` to FastAPI backend.
- `uvicorn weatherbrief.api.app:app --reload` runs the API.
- In production: FastAPI serves the built React assets as static files, or reverse proxy via nginx.

---

## Briefing Page Design

The briefing page is the core user experience. It displays the complete weather briefing for a flight, with refresh and history navigation.

### Layout (top to bottom)

```
┌─────────────────────────────────────────────────────────┐
│  EGTK → LFPB → LSGS  |  Sat 21 Feb 2026 0900Z  | FL080│
│  [⟳ Refresh]  [History: D-2 (19 Feb 18:00Z) ▾]        │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ██ ASSESSMENT: GREEN — Ridge established, models agree ██
│                                                         │
├──── Synopsis ───────────────────────────────────────────┤
│                                                         │
│  SYNOPTIC: High pressure centered over Bay of Biscay...│
│  WINDS: Light and variable at FL080...                  │
│  CLOUD & VISIBILITY: Scattered CI above FL150...        │
│  PRECIPITATION: None expected along route...            │
│  ICING: Freezing level ~6500ft, no cloud in band...     │
│  SPECIFIC CONCERNS: Sion valley fog possible before...  │
│                                                         │
├──── GRAMET ─────────────────────────────────────────────┤
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │                                                 │    │
│  │          [GRAMET cross-section image]           │    │
│  │                                                 │    │
│  └─────────────────────────────────────────────────┘    │
│                                                         │
├──── Model Comparison ───────────────────────────────────┤
│                                                         │
│  Variable        │ GFS   │ ECMWF │ ICON  │ Spread │Agr │
│  ────────────────┼───────┼───────┼───────┼────────┼────│
│  Temp (°C)       │  3.2  │  3.5  │  3.1  │  0.4   │ ✓  │
│  Wind (kt)       │  12   │  10   │  14   │  4     │ ✓  │
│  Wind dir (°)    │  270  │  265  │  275  │  10    │ ✓  │
│  Cloud (%)       │  15   │  20   │  10   │  10    │ ✓  │
│  Precip (mm)     │  0.0  │  0.0  │  0.0  │  0.0   │ ✓  │
│  Freezing (m)    │ 1980  │ 2050  │ 1950  │  100   │ ✓  │
│                                                         │
│  MODEL AGREEMENT: GFS and ECMWF in strong agreement... │
│  TREND: Improving since D-5...                          │
│  WATCH ITEMS: Sion valley fog — check 0600Z TAF...      │
│                                                         │
├──── Skew-T Soundings ───────────────────────────────────┤
│                                                         │
│  Model: [GFS ▾]  (toggle between models)                │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │   EGTK   │  │   LFPB   │  │   LSGS   │              │
│  │          │  │          │  │          │              │
│  │  [Skew-T │  │  [Skew-T │  │  [Skew-T │              │
│  │  diagram] │  │  diagram] │  │  diagram] │              │
│  │          │  │          │  │          │              │
│  └──────────┘  └──────────┘  └──────────┘              │
│                                                         │
│  Waypoints displayed left-to-right matching route order │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### Component Breakdown

#### Header Bar
- Route summary (origin → waypoints → destination).
- Target date/time and altitude.
- **Refresh button**: triggers `POST /api/flights/{id}/refresh`. Shows loading spinner during fetch. On completion, reloads the page with the new pack.
- **History dropdown**: populated from `GET /api/flights/{id}/packs`. Shows `D-{N} (fetch_date fetch_time)` plus assessment color dot. Selecting an entry loads that historical pack.

#### Assessment Banner
- Full-width colored banner: green/amber/red background.
- Assessment text from `WeatherDigest.assessment_reason`.
- Immediately communicates the go/no-go picture.

#### Synopsis Section
- Renders the LLM digest fields as labeled subsections:
  - Synoptic situation
  - Winds
  - Cloud & Visibility
  - Precipitation & Convection
  - Icing
  - Specific concerns (Alpine, foehn, valley fog, etc.)
- If no LLM digest available (e.g., API keys not configured), falls back to the plain-text digest sections from `format_digest()`.

#### GRAMET Section
- Displays the GRAMET cross-section PNG.
- Full-width, zoomable (click to enlarge).
- Shows placeholder/message if GRAMET not available for this pack.

#### Model Comparison Section
- Table view: one row per compared variable, columns for each model + spread + agreement indicator.
- Data comes from `WaypointAnalysis.model_divergence` in the snapshot.
- Below the table: the LLM digest's `model_agreement`, `trend`, and `watch_items` fields as narrative text.

#### Skew-T Route View
- Displays Skew-T diagrams horizontally, left-to-right matching route order (origin → waypoints → destination).
- **Model toggle dropdown**: switches which model's Skew-T to display at all waypoints simultaneously. Default: first available model.
- Each Skew-T is a clickable thumbnail that expands to full size.
- If a specific waypoint/model combo is missing, shows a "not available" placeholder.
- Future enhancement: overlay two models on the same Skew-T for visual comparison (already described in the spec).

---

## Flights Page Design

Simple list/create page for managing flights:

```
┌─────────────────────────────────────────────────────────┐
│  My Flights                                  [+ New]    │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  EGTK → LFPB → LSGS   21 Feb 2026 0900Z   FL080       │
│  Last briefing: D-2 (19 Feb 18:00Z) ██ GREEN           │
│  [View Briefing →]                                      │
│                                                         │
│  ─────────────────────────────────────────────────────  │
│                                                         │
│  EGTK → LFAT           28 Feb 2026 1000Z   FL060       │
│  Last briefing: D-5 (23 Feb 09:00Z) ██ AMBER           │
│  [View Briefing →]                                      │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Create Flight dialog:**
- Select from existing routes (dropdown from `GET /api/routes`) or enter inline ICAO codes.
- Target date (date picker), time (hour selector), altitude, duration.
- On create: `POST /api/flights` → redirects to briefing page → auto-triggers first refresh.

---

## Implementation Plan

### Step 1: Data models & storage for Flights and BriefingPacks

- Add `Flight` and `BriefingPackMeta` Pydantic models.
- Implement `storage/flights.py`: create/list/get/delete flights, save/load/list packs.
- Write tests for the new storage layer.

### Step 2: Extract pipeline from CLI

- Create `pipeline.py` with `execute_briefing(flight, db_path, options) → BriefingPackResult`.
- Refactor `cli.py` to call `execute_briefing()` internally.
- Verify CLI still works identically after refactor (existing tests pass).

### Step 3: FastAPI app + route/flight/pack endpoints

- Add `fastapi` + `uvicorn` dependencies.
- Implement `api/app.py` (app factory, CORS config, static mount).
- Implement `api/routes.py` — thin wrappers around `config.py`.
- Implement `api/flights.py` — CRUD backed by `storage/flights.py`.
- Implement `api/packs.py` — refresh triggers pipeline, list/get serve pack data and artifacts.
- Write API tests.

### Step 4: Web app scaffold + Flights page

- Scaffold React app with Vite + TypeScript in `web/`.
- Set up API client with typed hooks.
- Build Flights page: list flights, create flight dialog.
- Proxy config for development.

### Step 5: Briefing page — core layout

- Build `BriefingPage` with header bar, history dropdown, refresh button.
- Assessment banner component.
- Synopsis section (renders digest fields).

### Step 6: Briefing page — GRAMET + Model Comparison

- GRAMET viewer component (image display, zoom, fallback).
- Model comparison table from divergence data.
- Narrative sections (model agreement, trend, watch items).

### Step 7: Briefing page — Skew-T route view

- Skew-T gallery component: horizontal layout, route order.
- Model toggle dropdown (shared state across all waypoints).
- Thumbnail → full-size expand.

### Step 8: Polish & integration

- Error states, loading states, empty states throughout.
- Production build: FastAPI serves React static build.
- Verify full flow: create flight → refresh → view briefing → switch history → toggle models.

---

## Alternatives Considered

### Database (SQLite/Postgres) vs. file-based storage

The current system is file-based and it works well. Adding a database introduces complexity (migrations, connection management) for what is currently a single-user tool. The file-based approach:
- Makes packs portable (zip and share).
- Simplifies deployment (no DB server).
- Works with the existing snapshot JSON approach.

If multi-user or concurrent access becomes important, SQLite is a natural evolution: add a `flights.db` for metadata/indexing while keeping artifact files on disk. But for now, file-based is the right call.

### Server-sent events for refresh progress

A fetch can involve multiple API calls (Open-Meteo per waypoint per model + GRAMET + LLM digest). Options for communicating progress:

- **Option A: Simple request/response** — refresh endpoint blocks until complete. Simple but can take 30-60s.
- **Option B: Background task + polling** — refresh returns immediately, client polls for status.
- **Option C: Server-sent events** — push progress updates to client.

**Recommendation: Option B for initial implementation.** Return a 202 with a task ID, client polls `GET /api/tasks/{id}` until completion. SSE can be added later. The frontend shows a progress indicator during polling.

### Route creation: API vs. YAML only

Currently routes live in `routes.yaml`. Options:
- **Keep YAML-only**: API reads from YAML. Creating routes requires file editing.
- **API-managed routes**: API reads/writes a `routes.json` (or extends YAML writing).

**Recommendation: API-managed with JSON.** Add a `data/routes.json` that the API manages, while continuing to read `config/routes.yaml` as read-only defaults. API-created routes go to `data/routes.json`. Both sources are merged when listing routes. This keeps backward compatibility and allows the web app to create routes without manual YAML editing.

---

## Dependencies (new)

| Package | Purpose |
|---------|---------|
| `fastapi>=0.115` | API framework |
| `uvicorn[standard]>=0.30` | ASGI server |
| `python-multipart` | File upload support (future) |

Frontend (in `web/package.json`):
| Package | Purpose |
|---------|---------|
| `react`, `react-dom` | UI framework |
| `typescript` | Type safety |
| `vite` | Build tool |
| `@tanstack/react-query` | API state management |

---

## Open Questions

1. **Authentication**: For now, this is single-user local. When/if we add multi-user, do we want simple API key auth, OAuth, or defer to a reverse proxy?

2. **Background task runner**: For refresh, do we need a proper task queue (e.g., `arq`, `celery`) or is `asyncio.create_task` with in-memory state sufficient for single-user use?

3. **Image optimization**: Skew-T PNGs can be large (~200KB each). Should we generate thumbnails on save, or resize on the fly via the API?

4. **Offline / PWA**: Should the web app cache the latest briefing pack for offline viewing (useful if checking weather at an airfield with poor connectivity)?

---

## References

- Current architecture: [architecture.md](./architecture.md)
- Data models: [data-models.md](./data-models.md)
- Digest pipeline: [digest.md](./digest.md)
- Original spec: [flight-weather-tracker-spec.md](./flight-weather-tracker-spec.md)
