# iOS App — Server API Contract

> Endpoints consumed and added, server-side data model, spatial queries

Server-side work is phased to match the app roadmap. Endpoints listed below are designed upfront to ensure the app architecture leads to the final vision, but implemented only when their phase arrives.

## Existing Endpoints (Used As-Is)

| Endpoint | Method | Phase | Purpose |
|----------|--------|-------|---------|
| `/auth/login/google` | GET | 1 | OAuth login (needs `?platform=ios` addition) |
| `/auth/me` | GET | 1 | Current user info |
| `/api/flights` | GET | 1 | List user's flights |
| `/api/flights/{id}` | GET | 1 | Flight details |
| `/api/flights/{id}/packs/latest` | GET | 1 | Latest pack metadata |
| `/api/flights/{id}/packs/{ts}/snapshot` | GET | 1 | Full briefing data |
| `/api/flights/{id}/packs/{ts}/advisories` | GET | 1 | Route advisories |
| `/api/flights/{id}/packs/{ts}/elevation` | GET | 1 | Elevation profile |
| `/api/flights/{id}/packs/{ts}/skewt/{icao}/{model}.png` | GET | 1 | Skew-T image |
| `/api/flights/{id}/packs/{ts}/gramet.png` | GET | 1 | GRAMET image |
| `/api/flights/{id}/packs` | GET | done | List all packs (history) |
| `/api/flights/{id}/packs/freshness` | GET | 2 | Data freshness check |
| `/api/flights/{id}/packs/refresh/stream` | POST | done | SSE streaming refresh with progress |
| `/api/flights/{id}/packs/refresh/status` | GET | done | Check active refresh status |
| `/api/flights/{id}/packs/{ts}/sounding-profile/{pt}/{model}` | GET | done | Raw sounding profile for client Skew-T |
| `/auth/apple/token` | POST | done | Native Apple Sign In token exchange |

## Phase 1 — Auth Extension (DONE)

| Endpoint | Change |
|----------|--------|
| `/auth/login/google?platform=ios` | Callback redirects to `flyfunweather://auth/callback?token=<jwt>` |
| `/auth/apple/token` | Native Apple identity token → flyfun JWT |

## Phase 2 — Companion Sync Endpoint

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/flights/{id}/packs/{ts}/companion` | GET | Lightweight offline payload — cross-section, route analyses, advisories, elevation, digest summary, route geometry, airport conditions. Everything needed for full display + prompting without raw forecasts. Target: a few hundred KB. |

The companion endpoint is a curated subset of `/snapshot` — derived analysis results only, not raw forecast arrays.

## Phase 3 — Observations & Sessions

Observations (PIREPs) are **top-level entities** — they can be filed with or without a flight. The API reflects this: primary endpoints at `/api/observations`, convenience accessors under flights.

### Observations (top-level)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/observations` | POST | Batch submit. Array of observations with offline UUIDs. Idempotent — re-submit is no-op. Each may include `flight_id`/`session_id`. |
| `/api/observations` | GET | Recent shared. Params: `lat`, `lon`, `radius_nm` (30), `since`, `airport_icao`. Community PIREP feed. |
| `/api/observations/nearby` | GET | Near point or route. Params: `lat`, `lon`, `radius_nm`, `since`, or `flight_id` (uses the flight's route geometry). |
| `/api/observations/mine` | GET | Current user's own (all, including unshared). |

### Flight-scoped accessors

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/flights/{id}/observations` | GET | Observations linked to this flight (own, all sessions). |

### Flight sessions

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/flights/{id}/sessions` | POST | Start session (returns `session_id`) |
| `/api/flights/{id}/sessions/{sid}` | PATCH | End session (`end_time`), update track summary |
| `/api/flights/{id}/sessions` | GET | List sessions for a flight |
| `/api/flights/{id}/sessions/{sid}` | GET | Session detail + observation summary |

### Real-time (Phase 3c)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/flights/{id}/sessions/{sid}/live` | WebSocket | Bidirectional during active session. Outbound: push observations as created. Inbound: nearby PIREPs from other active flights. Server maintains in-memory registry of active sessions + route geometries for spatial matching. |

### Verification (future)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/flights/{id}/verification` | GET | Forecast vs observation comparison (server-side) |

## Server Data Model

```python
class FlightSession(Base):
    """Flight session from engine start to shutdown."""
    id: UUID
    flight_id: str
    user_id: int
    briefing_timestamp: str | None        # which pack was synced
    start_time: datetime                   # UTC
    end_time: datetime | None
    track_summary: dict | None             # JSON: simplified track for route display
    created_at: datetime

class Observation(Base):
    """PIREP — first-class. Standalone or flight-linked."""
    id: UUID                               # client-generated, enables idempotent sync
    user_id: int
    timestamp: datetime                   # UTC
    latitude: float
    longitude: float
    gps_altitude_ft: float | None          # nil for ground-based
    pressure_altitude_ft: float | None

    airport_icao: str | None               # for ground/airport-referenced

    flight_id: str | None                  # optional linkage
    session_id: UUID | None

    source: str                           # prompted/manual/passive/standalone
    prompt_trigger: str | None            # if prompted
    response: str | None                  # confirmed/edited/denied/dismissed (nil for standalone)
    forecast_summary: dict | None         # nil for standalone

    flight_rules: str | None              # VMC/MVFR/IMC
    icing: str | None                     # none/trace/light/moderate/severe
    turbulence: str | None                # none/light/moderate/severe/extreme
    cloud_coverage: str | None            # CLR/SCT/BKN/OVC
    cloud_base_ft: int | None
    cloud_top_ft: int | None              # "tops around 1500'"
    precipitation: str | None
    visibility: str | None
    wind_comparison: str | None
    oat_celsius: float | None
    notes: str | None

    route_point_index: int | None         # snapped to nearest route point (flight-linked)
    is_shared: bool
    created_at: datetime
```

## Spatial Query Design (Phase 3)

Two use cases:

1. **Community PIREP feed** (Phase 3a) — "Recent PIREPs near EGTF" or "near lat/lon within 30nm". Simple DB query with great-circle distance filter. Index on `(timestamp, latitude, longitude)` is sufficient: `WHERE timestamp > since AND haversine(lat, lon, obs.lat, obs.lon) < radius`. Fast at expected volumes without specialized spatial indexing.

2. **Live in-flight push** (Phase 3c) — "Push new PIREPs to active flight sessions whose routes pass nearby". Requires:
   - **Active session registry** — in-memory `session_id → (flight_id, route_bbox, route_points)`, populated on session start, removed on end
   - **Spatial filter** — for each new observation (any source — in-flight or standalone), iterate active sessions and check if it falls within `radius_nm` of any route point (great-circle). Sub-ms for hundreds of sessions
   - **WebSocket broadcast** — push matching observations to the relevant session's WebSocket
   - **Scaling** — if usage grows beyond ~1000 concurrent sessions, add spatial index (R-tree via `shapely` or PostGIS). In-memory avoids infra deps initially

Standalone PIREPs filed on the ground also trigger the spatial broadcast — a pilot reporting "EGTF, base at 800'" immediately appears on any active session whose route passes near EGTF.

## References

- [Data Models](./ios-app-data-models.md) — matching Swift `@Model` types
- [Sync & Prompting](./ios-app-sync-prompting.md) — client-side sync behavior
