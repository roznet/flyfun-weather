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
| `/api/flights/bulk-delete` | POST | done | Delete many owned flights at once (`{ids: […≤200]}` → `{deleted, not_found}`); backs the app's multi-select sheet. Owner-scoped — other users' ids come back in `not_found`, not as an error |
| `/api/flights/{id}/packs/latest` | GET | 1 | Latest pack metadata |
| `/api/flights/{id}/packs/{ts}/snapshot` | GET | 1 | Full briefing data |
| `/api/flights/{id}/packs/{ts}/advisories` | GET | 1 | Route advisories |
| `/api/flights/{id}/packs/{ts}/elevation` | GET | 1 | Elevation profile |
| `/api/flights/{id}/packs/{ts}/skewt/{icao}/{model}` | GET | 1 | Skew-T image (PNG; no `.png` in route) |
| `/api/flights/{id}/packs/{ts}/gramet.png` | GET | 1 | GRAMET image |
| `/api/flights/{id}/packs` | GET | done | List all packs (history) |
| `/api/flights/{id}/packs/freshness` | GET | 2 | Data freshness check |
| `/api/flights/{id}/packs/refresh/stream` | POST | done | SSE streaming refresh with progress |
| `/api/flights/{id}/packs/refresh/status` | GET | done | Check active refresh status |
| `/api/flights/{id}/packs/{ts}/sounding-profile/{point_index}/{model}` | GET | done | Raw sounding profile for client Skew-T |
| `/api/flights/{id}/packs/{ts}/bundle` | GET | done | Gzipped single-JSON offline bundle (see Phase 2) |
| `/api/flights/{id}/move` | POST | done | Structural edit — see below |
| `/api/flights/{id}/packs/refresh` | POST | done | Queue a refresh (202) without holding a stream — see below |
| `/auth/apple/token` | POST | done | Native Apple Sign In token exchange |

### Editing a flight: PATCH vs move (#552)

The flight ID is derived from route + date + altitude/ceiling/duration
(`_compute_flight_id`, `api/flights.py`), so **the date and the origin/destination
are part of the flight's identity**. `PATCH /api/flights/{id}` rejects both with a
422 (`"Cannot change the flight date. Create a new flight instead."`); a client
that only knows PATCH therefore cannot express a date change at all, which is
exactly the bug in #544.

- **Non-structural** (time-of-day inside the same UTC day, mid-route waypoints,
  altitude, ceiling, duration, aircraft, profile, Flexibility) → `PATCH`.
- **Structural** (different UTC day, different first/last waypoint) → either
  `POST /{id}/move` (replaces the flight, discards its packs, keeps the share
  code) or `POST /api/flights` with the merged values (keeps both flights). The
  client asks the pilot which.

The structural test must compare **UTC calendar days**, not the picker's local
day: `target_date` is derived from the UTC instant, so 00:30 in `Europe/Paris` is
the previous UTC day. iOS does this in `AddFlightViewModel.departureDayChanged`.

`MoveFlightRequest` has no `profile_id` / `aircraft_id` / `flexibility` field —
those are carried over from the source flight. A client whose form can change them
in the same edit (iOS has one Save button) follows the move with a PATCH on the
new flight; see `AddFlightViewModel.applyResidualEdits(to:)`.

### Queueing a refresh without watching it

`POST /api/flights/{id}/packs/refresh?source=user` returns **202** and runs the
pipeline in a server-side executor that is independent of any client stream
(`api/packs.py`, `loop.run_in_executor(_refresh_executor, run_pipeline)`).
Prefer it over `/packs/refresh/stream` whenever the UI that triggered the refresh
is about to go away — the editor sheet after a save, for instance. Progress then
comes from the flight list's `/api/refresh/active` poll and the APNs push, not
from a stream nobody is looking at. (It can also answer **200 `already_fresh`**;
the pack-params gate that stops that from happening right after a parameter edit
is described in `refresh-durability.md`.)

## Phase 1 — Auth Extension (DONE)

| Endpoint | Change |
|----------|--------|
| `/auth/login/google?platform=ios` | Callback redirects to `<scheme>://auth/callback?code=<code>&state=<state>` (auth-code flow, H8 hardening; `?token=<jwt>` is the legacy fallback). Scheme comes from `?scheme=` (stored in session as `oauth_scheme`), defaulting to `flyfun`; `platform` stored as `oauth_platform`. Scheme allowlist is now exact-match (no loose `flyfun*` regex). |
| `/auth/apple/token` | Native Apple identity token → flyfun JWT |

Lives in `flyfun-common` (`flyfun_common/auth/router.py`), wired via `create_auth_router(...)` in `weatherbrief/api/app.py`.

## Phase 2 — Offline Bundle Endpoint (DONE)

Shipped as `/bundle` (not `/companion` as originally planned).

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/flights/{id}/packs/{ts}/bundle` | GET | Single gzipped JSON object keyed by cache-endpoint name (`advisories`, `snapshot`, `route-analyses`, `elevation`, `digest`, `altitude-table`) plus pre-computed sounding profiles for every (point, model). For full offline display + client Skew-T. |

Implementation notes (`packs.py::get_bundle`):
- Response is `gzip`-compressed with `Content-Encoding: gzip`; an `X-Uncompressed-Length` header lets the client show accurate download progress (URLSession transparently decompresses).
- Sounding profiles come from a gzipped sidecar written at refresh time (`read_sounding_sidecar`); falls back to `build_sounding_sidecar` for older packs.
- Unlike the originally-envisioned curated companion, the bundle is the *full* pack snapshot, not a derived-analysis-only subset.

## Phase 3 — PIREPs (DONE, reduced scope)

Shipped as **PIREPs** at `/api/pireps` (`api/pireps.py`), NOT the `/api/observations` + sessions + WebSocket design originally sketched. PIREPs are top-level entities, optionally linked to a flight via `pack_id`. There are **no flight sessions, no `/sessions` endpoints, no WebSocket live-push, and no server-side `/verification` endpoint** in this module — that part of the vision was descoped. (Verification has a separate, unrelated archive in `db/models.py` — `VerificationObservationRow` etc. — not exposed here.)

Visibility is gated by per-user prefs: `can_publish_pireps` / `can_view_pireps` (preferences.py). Submission is rate-limited (`pirep_burst_limiter`, `pirep_daily_limiter`; 50/day, batch ≤ 50) and bounds-checked to Europe (`validate_european_bounds`).

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/pireps` | POST | Submit one PIREP. 409 on duplicate `client_uuid`. |
| `/api/pireps/batch` | POST | Offline sync: array of ≤50 PIREPs. Idempotent — duplicate `client_uuid` is silently skipped and the stored row returned. Out-of-bounds / bad aircraft/pack refs skipped silently. |
| `/api/pireps` | GET | Query with flexible filters (see below). Returns `{items, count}`. |

`GET /api/pireps` filters (mutually-exclusive scope picks first match): `flight_id`, `pack_id`, `airport` (ICAO → lat/lon via euro_aip), `bounds` (`sw_lat,sw_lon,ne_lat,ne_lon`). Time: `from`/`to` (ISO) or `hours` (default 6, 1–48). Optional: `hazard` (icing|turbulence|cloud), `min_severity`, `altitude_min`, `altitude_max`, `aircraft_type`.

PIREP *content* is community-global (airport/bounds queries see everyone's). Only flight-keyed scoping (`flight_id`/`pack_id`) is gated by flight visibility; for community results filed against a flight the viewer can't see, the `pack_id` linkage is stripped but the report stays visible (`_visible_linked_pack_ids`).

## Server Data Model

`PirepRow` (`db/models.py`, table `pireps`). Note the field names differ from the original `Observation` sketch:

```python
class PirepRow(Base):
    id: int                                # server PK (autoincrement), NOT a UUID
    client_uuid: str | None                # unique; enables idempotent offline sync
    submitted_at: datetime                 # UTC
    observed_at: datetime                  # UTC
    latitude: float
    longitude: float
    gps_altitude_ft: int | None
    reported_altitude_ft: int | None       # pressure/indicated altitude reported by pilot
    in_cloud: bool | None
    icing_intensity: str | None            # none/trace/light/moderate/severe
    icing_type: str | None                 # rime/clear/mixed
    turbulence_intensity: str | None       # none/light/moderate/severe
    ceiling_msl_ft: int | None
    tops_msl_ft: int | None
    tops_basis: str | None                 # crossed/estimated_above/below_min
    temp_c: float | None
    wind_dir: int | None
    wind_speed_kt: int | None
    remarks: str | None
    aircraft_id: int | None                # FK user_aircraft (ICAO type resolved on response)
    pack_id: int | None                    # FK briefing_packs — flight linkage
    source: str                            # manual/inflight/postflight
    user_id: str | None                    # FK users
```

Enum tuples live alongside the model: `ICING_INTENSITIES`, `ICING_TYPES`, `TURBULENCE_INTENSITIES`, `TOPS_BASES`, `PIREP_SOURCES`.

## Spatial Query Design

Only the community-feed use case shipped. Queries use a great-circle / bounding-box filter in `storage/pireps.py` (`list_pireps`, `validate_european_bounds`); `pack_id` is indexed. There is no active-session registry and no spatial broadcast — those depended on the descoped sessions/WebSocket work. If live in-flight push is revived later, see git history for the original R-tree/PostGIS scaling sketch.

## References

- [Data Models](./ios-app-data-models.md) — matching Swift `@Model` types
- [Sync & Prompting](./ios-app-sync-prompting.md) — client-side sync behavior
