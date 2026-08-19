# PIREP Collection & Model Validation — Implementation Plan

## Build Status (checked 2026-08-15)

Partly built. **M0 and M1 are shipped — code is the source of truth for them; the
sections below only point at it.** M2 is *unbuilt as a PIREP feature*, but its
hardest dependency (APNs) now exists for another feature. M3 is unbuilt.

- **M0 Aircraft Registry — DONE.** `user_aircraft` + ICAO type reference,
  migration `031_user_aircraft.py`, `storage/aircraft.py` + `storage/aircraft_types.py`,
  CRUD/search in `api/aircraft.py` (note: type search is `GET /api/aircraft/types?q=`,
  not the `/api/aircraft-types` this plan originally proposed).
- **M1 PIREP Publish & View — DONE.** `pireps` + `device_tokens` tables
  (`PirepRow`, `DeviceTokenRow`), migration `032_pireps_and_device_tokens.py`,
  `storage/pireps.py`, `api/pireps.py` (submit, `/batch` with `client_uuid` 409
  dedup, query by flight/pack/airport/bounds/time plus `hazard` / `min_severity` /
  `altitude_min` / `altitude_max` / `aircraft_type`). Rate limits in
  `api/throttle.py` (`pirep_burst_limiter` 1/2min, `pirep_daily_limiter` 50/day).
  European-bounds rejection via `validate_european_bounds`. Permissions
  `can_view_pireps` / `can_publish_pireps` in `api/preferences.py` (stored in
  `app_prefs_json`) + admin single/bulk toggles in `api/admin.py`. Retention
  exemption in `tasks/retention.py` (packs whose id appears in
  `select(PirepRow.pack_id)` skip **all** retention, T1 and T2).
  - Web viewer: `web/pireps.html` + `web/ts/pireps-main.ts`,
    `web/ts/visualization/pirep-map.ts` (severity colour, hazard icon,
    age-based opacity), `web/ts/managers/pirep-ui.ts`, plus the briefing PIREPs
    tab. The **fog-of-war overlay described below was not built** — the map is a
    plain marker map. Still a live idea if the viewer is revisited.
  - iOS: `PirepViewModel`, `PirepOfflineStore`, `PirepReportingView`,
    `PirepListView` — a single manual, briefing-anchored filing path. Covered by
    the `ios-app-*` design docs; `designs/ios-app-features.md` carries the
    honest shipped-vs-intended split (no standalone filing, no PIREP map, app
    sends no `pack_id` — the server infers the flight link from `observed_at`).
- **M2 Notifications — NOT built as a PIREP feature, but APNs is no longer a
  greenfield task.** Missing: `route_watches` / `airport_watches` /
  `notification_log` tables, `pirep/matching.py` (Shapely), and the
  PIREP-triggered notification flow. Already built by the briefing-refresh
  notification work (#366, migration `075_briefing_notifications.py`) and reusable:
  - `notify/push.py` — token-based APNs sender (httpx HTTP/2 + PyJWT ES256,
    ~50 min cached provider token), per-token sandbox/production routing,
    dead-token pruning on 410 / BadDeviceToken.
  - `notify/dispatch.py` — the notification gate + channel dispatch (email/push),
    driven by `app_prefs_json` notify flags.
  - `api/devices.py` — `POST /api/devices` upsert + `DELETE /api/devices/{token}`.
    This is the device-token endpoint M2 asked for; the `PUT /api/device-token`
    shape and the raw-SQL upsert sketched in the old plan are superseded, and
    `device_tokens` is no longer an unused table.
  - See `designs/ios-app-briefing-notifications.md` before writing any push code.
- **M3 Post-Flight & Validation — NOT built.** No cross-section-tap post-flight
  filing (`PIREP_SOURCES` accepts `"postflight"` but nothing emits it), no
  validation reconstruction tooling.

> When M2/M3 land, fold their durable design into the API / iOS design docs and
> move this plan to `archive/`. Separately: the shipped M0/M1 server side has no
> design-doc home of its own (only the iOS docs describe it) — worth promoting.

> **As-built M0/M1 now lives in [../pireps.md](../pireps.md)** (2026-08-17) — data
> model, permissions, rate limits, European-bounds gate, retention exemption and
> GDPR posture. This file is kept for the **unbuilt** milestones: M2 (watches &
> notifications) and M3 (post-flight & model validation).

## Overview

Crowdsourced PIREP (pilot weather report) collection, with two goals:

1. **Near-real-time condition sharing** — pilots share observed conditions along their route
2. **Longitudinal model validation** — compare NWP forecasts against what pilots actually encountered

Open-source, community-driven, no commercial entity. Data is sparse by design
initially — the value builds over time.

-----

## Design Principles

- **Zero friction in the cockpit** — no pop-ups; all fields visible with smart ordering, one-tap severity buttons, skip always available
- **Pilot opens app → report is already there** — contextual, not interruptive
- **Avoid confirmation bias** — all fields shown (no hiding implies irrelevance), no values pre-selected; predictions only influence field ordering
- **Staleness is explicit** — stale reports are flagged aggressively, never displayed as current
- **Open source, MIT licensed** — no corporation, community-owned data

-----

## Permissions & Rate Limiting (as built)

Both flags live in `app_prefs_json` and default FALSE: `pirep_can_view` gates
viewing during beta, `pirep_can_publish` gates submitting. Admin enables per user
or in bulk.

**Post-rollout intent:** `pirep_can_view` becomes ignored — all authenticated
users see PIREPs. `pirep_can_publish` stays, so admin can disable an abusive user.
Trust model: everyone with the publish flag is trusted; no pre-moderation queue.

Rate limits (server-side, client should debounce too): 1 PIREP / 2 min burst,
50 / 24h. Inflight reports are periodic, not continuous.

-----

## Milestone 2 — Watches & Notifications (design intent, unbuilt)

### Schema

```sql
route_watches:    id, user_id, flight_id, route_waypoints JSON, corridor_km=50,
                  active_from, active_to, min_severity ENUM(any|light|moderate|severe)='light',
                  created_at
airport_watches:  id, user_id, airport_icao, radius_km=30,
                  min_severity='moderate', quiet_hours_start, quiet_hours_end, created_at
notification_log: id, user_id, pirep_id, watch_type ENUM(route|airport), watch_id,
                  sent_at, apns_status
```

Use `TZDateTime` for the datetime columns (see project CLAUDE.md), not the bare
`DATETIME UTC` this plan originally wrote.

### Spatial matching — Shapely, no PostGIS

Expected volumes are tiny (<<100 active watches). New `pirep/matching.py`:

- `find_matching_route_watches(lat, lon, active_watches)` — `LineString` of the
  cached waypoints, `.buffer(deg)` , `.contains(Point(lon, lat))`
- `find_matching_airport_watches(lat, lon, watches, resolve_icao)` — same with a
  point buffer around the resolved airport

The buffer radius must be converted from km to degrees at the PIREP's latitude
(`1 / (111.32 * cos(lat))`, clamped near the poles). **Gotcha:** that only
corrects the longitude axis, so Shapely's isotropic buffer yields a slightly
elliptical corridor in real-world distance. Fine at mid-latitudes and ≤50 km;
needs a geodesic buffer (pyproj) for polar or very wide corridors.

### Endpoints

```
POST   /api/watches/route           # Register route watch
DELETE /api/watches/route/{id}      # Cancel route watch
POST   /api/watches/airport         # Register airport watch
```

Device tokens: use the existing `POST /api/devices` — do not add a second endpoint.

### Notification trigger flow (async, post-store)

```
PIREP stored
  → sync_delay = submitted_at - observed_at
  → if sync_delay > 30 min: skip notifications (data retained, alerts suppressed)
  → find_matching_route_watches() — active flight windows only
  → find_matching_airport_watches()
  → for each match:
      severity >= watch.min_severity
      not in quiet hours (airport watches)
      not already sent for this watch in the last 30 min
      coalesce with other pending PIREPs for the same watch (5-min window)
      send via notify/push.py (routes per-token sandbox/production)
      log to notification_log
```

**Why the sync_delay gate:** late-synced PIREPs (phone reconnects after landing)
are still valuable *data* but misleading as *alerts* — the weather has moved on.
Store always, notify only when fresh.

**Coalescing:** group by `(watch_type, watch_id)`; a batch that lands together
becomes one notification ("3 new PIREPs — EGTF→LSGS route", body listing the
hazards), keyed on the highest severity in the batch for the threshold check.

### Community PIREPs in the briefing view

The briefing PIREPs tab shows own PIREPs today (linked via `pack_id`). M2 adds
community PIREPs: reports from other pilots inside the route corridor and flight
time window, using the same corridor logic as route watches.

-----

## Milestone 3 — Post-Flight & Validation (design intent, unbuilt)

### Post-flight debrief

After landing, prompt once. The pilot taps a position on the cross-section, which
interpolates lat/lon/altitude from the route; that opens the standard reporting
card pre-filled, stored with `source = 'postflight'` (position approximate rather
than live GPS). Multiple PIREPs per route allowed; the cross-section overlays the
inflight PIREPs already filed so the pilot doesn't duplicate. More considered
answers than inflight — the pilot is relaxed and can review the whole route.

Note the separate, *shipped* flight-debrief feature (`designs/debrief.md`) — it is
a different thing (subjective flight rating for digest calibration) and its doc
explicitly leaves PIREP retention out of scope. Don't conflate them.

### Model validation dataset

PIREPs store **observations only** — no forecast snapshot on the row. Each links
to its `pack_id`, and PIREP-linked packs are exempt from retention, so validation
reconstructs the prediction on demand: load the pack, interpolate its forecasts to
the PIREP's lat/lon/altitude/time, compare per model (ECMWF vs GFS vs …) against
`icing_intensity` / `turbulence_intensity` / cloud fields.

Reconstructing rather than snapshotting means validation can be **re-run as
analysis methods improve** — that is the whole reason for the retention exemption.
Over time this builds a record of NWP accuracy at GA altitudes in European
airspace, which met offices don't track.

### Data retention

PIREPs are **never deleted** — permanent observation dataset. Packs with a linked
PIREP skip both T1 (strip heavy artifacts) and T2 (delete pack): the full
`forecasts.json` / `cross_section.json` is exactly what the comparison needs.
Already enforced in `tasks/retention.py`; don't "optimise" that exemption away.

-----

## PIREP Data Format

US PIREP structure plus ceiling/tops additions: location+time (UL/TM, from GPS),
altitude (FL, GPS-prefilled and editable), aircraft (TP, from the user_aircraft
registry), cloud/ceiling (SK + ceiling MSL), cloud tops (SK + `tops_basis`
crossed / estimated_above / below_min), icing intensity+type (IC), turbulence
(TB), temperature (TA), wind (WV), remarks (RM).

`tops_basis` is the non-obvious one: `crossed` = exact altimeter reading through
the top, `estimated_above` = visual estimate from above, `below_min` = the value
is a *lower bound* only.

-----

## Geographic Scope

Targets **European airspace**, where no equivalent to the US PIREP system exists.

- **Europe:** full feature — community PIREPs fill a real gap
- **US:** do NOT show community PIREPs. Official AWC/ADDS PIREPs are authoritative;
  mixing community reports in would muddy provenance. Any US support should ingest
  official PIREPs only.

Enforced server-side by `validate_european_bounds` (submissions outside the
configured box are rejected).

-----

## Liability, Privacy & GDPR

- MIT licensed, no warranty
- Disclaimer prominent in the UI, not buried in ToS: "For situational awareness
  only — not a substitute for official weather briefings"
- Stale reports (>90 min) flagged visually, never quietly hidden
- Sparse coverage must be explicit — an empty map ≠ clear skies
- Consult an aviation law specialist before public launch (AOPA Legal Services
  Plan as a start, but check it covers product liability, not just certificate defence)
- **Account deletion anonymizes, does not delete:** `user_id` and `aircraft_id`
  set to NULL, observation data retained as anonymous records. This is what keeps
  the validation dataset intact while honouring erasure — say so at submission
  time ("stored permanently for weather research; deleting your account
  anonymizes your reports")
- No GPS tracks stored — PIREPs are discrete point observations

-----

## Remaining Implementation Sequence

### Milestone 2 — Notifications
1. Alembic migration — `route_watches`, `airport_watches`, `notification_log`
2. Shapely spatial matching in `pirep/matching.py` — standalone, easily testable
3. Watch CRUD endpoints (device tokens already handled by `api/devices.py`)
4. PIREP notification trigger — matching, severity/quiet-hours/rate-limit checks,
   coalescing, flight-window checks; send through `notify/dispatch.py` +
   `notify/push.py` rather than a new APNs client
5. Community PIREPs in the briefing view (reuses the corridor matcher)

### Milestone 3 — Post-Flight & Validation
1. Post-flight debrief — cross-section tap → PIREP with `source = 'postflight'`
2. Model validation tooling — prediction reconstruction, comparison queries

### Environment

APNs env vars are already live (`APNS_KEY_ID`, `APNS_TEAM_ID`, `APNS_BUNDLE_ID`,
and `APNS_KEY_P8` — base64 or PEM — with `APNS_KEY_P8_PATH` as a local-dev
alternative). M2 needs no new env vars.
