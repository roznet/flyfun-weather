# iOS App — Server API Contract

> Endpoints consumed and added, server-side data model, spatial queries

Server-side work was phased to match the app roadmap; **all three phases below have
shipped**, so the tables are now a contract description, not a plan. The phased
framing is kept because Phase 2/3 landed *smaller* than sketched and the deltas
are the load-bearing part.

## Core Endpoints (Used As-Is)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/login/google` | GET | OAuth login (`?platform=ios&scheme=…`, see Phase 1) |
| `/auth/apple/token` | POST | Native Apple Sign In token exchange |
| `/auth/me` | GET | Current user info |
| `/api/flights` | GET/POST | List / create flights |
| `/api/flights/{id}` | GET/PATCH/DELETE | Flight details and non-structural edit — see below |
| `/api/flights/{id}/move` | POST | Structural edit — see below |
| `/api/flights/bulk-delete` | POST | Delete many owned flights at once (`{ids: […≤200]}` → `{deleted, not_found}`); backs the app's multi-select sheet. Owner-scoped — other users' ids come back in `not_found`, not as an error |
| `/api/flights/{id}/packs` | GET | List all packs (history) |
| `/api/flights/{id}/packs/latest` | GET | Latest pack metadata |
| `/api/flights/{id}/packs/freshness` | GET | Data freshness check |
| `/api/flights/{id}/packs/refresh` | POST | Queue a refresh (202) without holding a stream — see below |
| `/api/flights/{id}/packs/refresh/stream` | POST | SSE streaming refresh with progress |
| `/api/flights/{id}/packs/refresh/status` | GET | Check active refresh status for this flight |
| `/api/refresh/active` | GET | All active refreshes (flight-list poll; separate `refresh_router`, prefix `/api/refresh`) |
| `/api/flights/{id}/packs/{ts}/snapshot` | GET | Full briefing data |
| `/api/flights/{id}/packs/{ts}/advisories` | GET | Route advisories |
| `/api/flights/{id}/packs/{ts}/advisories/{advisory_id}/detail` | GET | Per-model split, cross-check, mitigations |
| `/api/flights/{id}/packs/{ts}/elevation` | GET | Elevation profile |
| `/api/flights/{id}/packs/{ts}/skewt/{icao}/{model}` | GET | Skew-T image (PNG; no `.png` in route) |
| `/api/flights/{id}/packs/{ts}/sounding-profile/{point_index}/{model}` | GET | Raw sounding profile for client Skew-T |
| `/api/flights/{id}/packs/{ts}/gramet.png` | GET | GRAMET image |
| `/api/flights/{id}/packs/{ts}/bundle` | GET | Gzipped single-JSON offline bundle (see Phase 2) |

Also consumed by the app, documented with their own subsystems rather than here:
`/api/flights/interpret-route`, `/api/flights/parse-fpl`, `/api/flights/badge`,
`/api/flights/frequent-airports`, `/api/flights/route-distance`,
`/api/flights/{id}/debrief` (GET/PUT/DELETE), `/api/devices` (APNs registration),
`/api/messages`, `/api/help/catalog`, `/api/maps/forecast[/days]`,
`/api/nav/airports-db`, `/api/aircraft`, `/api/user/{preferences,profiles,usage}`,
`/api/feedback`, `/api/pireps`.

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

### Two flight fields that punish a client for guessing

- **`raw_route`** is three-way, on `create`, `move` *and* `PATCH`: present →
  store and stamp `parser_version` with the current euro_aip release; absent
  with a changed route → **clear** (the old string would now lie); absent with
  an unchanged route → leave alone. So a client must send it **only when the
  pilot actually edited the route input**, and must send what they *typed*, not
  the resolved waypoints — resending a stored value re-stamps `parser_version`
  and destroys its meaning as a re-derive marker, while omitting it on an edited
  route silently drops the annotation. iOS captures it in
  `AddFlightViewModel.editedRawRoute`, before `applyInterpretedRoute()`
  normalises the field.
- **`alt_departure_time`** must be on the same day as the primary departure and
  must differ from it (`update_flight`), compared as stored — i.e. in **UTC**. A
  free alternate-date control therefore offers days the server rejects; both
  clients bind the day to the departure's instead (web pins
  `flight.target_date`, iOS uses `AddFlightViewModel.alignedAltDepartureInstant`).

### Queueing a refresh without watching it

`POST /api/flights/{id}/packs/refresh?source=user` returns **202** and runs the
pipeline in a server-side executor that is independent of any client stream
(`api/packs.py`, `loop.run_in_executor(_refresh_executor, run_pipeline)`).
Prefer it over `/packs/refresh/stream` whenever the UI that triggered the refresh
is about to go away — the editor sheet after a save, for instance. Progress then
comes from the flight list's `/api/refresh/active` poll and the APNs push, not
from a stream nobody is looking at. (It can also answer **200 `already_fresh`**;
the pack-params gate that stops that from happening right after a parameter edit
is described in `freshness-markers.md`, under "Tiered Refresh Gate".)

### Timing scenarios: a separate, poll-until-terminal endpoint

The "is there a better departure time?" scan is **not** part of the pack payload —
it is its own job with its own status ladder, so a client has to poll it rather
than read it. Three routes, all under `/api/flights/{id}/packs/{ts}/`
(`api/packs.py`, grep `time-options`):

| Method | Path | Semantics |
|---|---|---|
| `GET`  | `…/time-options` | Poll status + scan result. **404** when Flexibility is `none` and no scan ever ran — a normal "no data" answer, not an error. Owner-only: lazily schedules a scan if Flexibility is set but no status exists. |
| `POST` | `…/time-options/rescan` | Re-queue the scan (owner-only). **202**; **409** if Flexibility is `none`. |
| `POST` | `…/time-options/confirm` | On-tap multi-model check of one candidate. **202**; **429** if a confirm is already running *for this pack*; **409** for unknown/already-confirmed. |

Client contract (web and iOS implement the same one — `web/ts/store/briefing-store.ts`,
`BriefingViewModel.pollTimeOptions`): poll with backoff **3 s → ×1.5 → cap 15 s**
until the job reaches a terminal status (`done|failed|skipped`), treat 404 as
no-data, tolerate ~3 transient errors, and carry a hard iteration cap as a
backstop against a job that never terminates. Two gotchas worth keeping:

- **Gate on the pack, not the flight.** The start condition reads
  `pack?.flexibility ?? flight.effectiveFlexibility` — the pack meta carries
  Flexibility too, and the client's `Flight` object goes stale if the user edited
  Flexibility after the briefing was opened.
- **Online-only by design.** Timing data is deliberately excluded from the
  offline `/bundle`; the panel shows a placeholder when offline rather than a
  stale scan.

The `429` on confirm is the *only* throttle — there is no per-user rate limit on
scans yet (`count_user_time_scans` exists but is not enforced).

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

Visibility is gated by per-user prefs: `can_publish_pireps` / `can_view_pireps` (preferences.py). Submission is rate-limited (`throttle.py`: `pirep_burst_limiter` = 1 per 120 s, single submits only; `pirep_daily_limiter` = 50/day, and the batch route charges it `len(items)` so 50/day means 50 PIREPs, not 50 calls) and bounds-checked to Europe (`validate_european_bounds`).

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

`submitted_at` / `observed_at` are still declared `DateTime(timezone=True)`, which is
a no-op on MySQL — they have not yet been converted to `TZDateTime` (#520). Treat
them as UTC by convention, and pass aware datetimes; a naive one will not be caught
on write here the way it is on converted columns.

## Spatial Query Design

Only the community-feed use case shipped, and it is deliberately unsophisticated —
`list_pireps` (`storage/pireps.py`) filters on **plain lat/lon inequality boxes**,
no great-circle distance and no spatial index. `airport=` is a ±`airport_radius_deg`
(default 0.3°) square around the field, not a radius, so hits near the corners are
further away than the name suggests. Results are capped at `limit=500` with no
pagination. Only `pack_id` and `user_id` are indexed.

One non-obvious scope rule: `flight_id` matches PIREPs linked to the flight's packs
**OR** PIREPs by the flight's owner with no `pack_id` inside the flight window
(departure −2 h to departure + duration +2 h) — that second arm exists so the iOS
offline queue, which files without a pack linkage, still shows up on its flight.

There is no active-session registry and no spatial broadcast — those depended on the
descoped sessions/WebSocket work. If live in-flight push is revived later, see git
history for the original R-tree/PostGIS scaling sketch.

## References

- [Data Models](./ios-app-data-models.md) — matching Swift `@Model` types
- [Sync & Prompting](./ios-app-sync-prompting.md) — client-side sync behavior
