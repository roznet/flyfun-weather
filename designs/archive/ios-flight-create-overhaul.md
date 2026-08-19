# iOS Flight-Create Overhaul — Implementation Plan

> Status: SHIPPED — merged to `main` (the `ios-flight-create-revamp` worktree is gone).
> All six items are live. This file is now a historical build record and a candidate for
> archive/. Goal was: bring the iOS flight-creation flow to parity with the web
> flight-create page, fix the stuck "Loading Briefing" screen, add dynamic ICAO autocomplete.
>
> The durable knowledge is already folded into the real iOS design docs — the downloaded
> (not bundled) `airports.db` decision in `ios-app-architecture.md` + `ios-app-data-models.md`,
> the form shape in `ios-app-overview.md`, `/api/nav/airports-db` in `ios-app-server-api.md`,
> and the `AirportDatabase`/`KnownAirports` reuse in `ios-app-intents.md`. Read those first;
> nothing below is load-bearing any more.
>
> As-built code paths (verified in `main`):
> - Item 1: `BriefingViewModel.loadBriefing()` catches `APIError.notFound` and calls
>   `generateFirstBriefing()` (streams the refresh banner).
> - Item 6: server endpoint in `src/weatherbrief/api/nav.py` (`GET /api/nav/airports-db`,
>   ETag), iOS `Services/AirportDatabase.swift` + `ViewModels/RouteAutocompleteController.swift`.
> - Item 3: `Models/API/ProfileResponse.swift`, `profileId` on `CreateFlightRequest`/`FlightResponse`.
> - Item 2: `autorouterRoutes(limit:)` repo method + `AutorouterRoute`; recent routes derived client-side.
> - Items 4/5: TZ lives in `ViewModels/DepartureTimeModel.swift` (NOT `Utils/Timezone.swift`);
>   interpret popup is `Views/Flights/RouteInterpretSheet.swift` (NOT `RouteInterpretView.swift`);
>   repo methods `interpretRoute(rawRoute:)` + `routeDistance(waypoints:)`; logic unit-tested
>   in `flyfun-weatherTests/RouteCreateLogicTests.swift`.

## As-built deviations from the original plan

- **#6 airports DB is downloaded, not bundled.** A bundled SQLite churns every AIRAC
  cycle and would bloat the repo, so instead a new server endpoint
  `GET /api/nav/airports-db` serves a slim (~3.5 MB, airports+runways only) SQLite
  built from `nav.db` and cached server-side, with an `ETag`/`If-None-Match` refresh.
  The iOS `AirportDatabase` caches it in Application Support and refreshes on launch.
  With no cached copy (first run / offline) autocomplete is simply empty — never broken.
  The DB is gitignored, never committed.
- **#4 TZ math uses Swift `TimeZone.secondsFromGMT(for:)`** (DST-correct natively)
  rather than re-implementing the web's Intl-based offset computation. The single
  source of truth is the absolute instant; switching zones preserves it and re-displays
  (web behaviour). `DepartureTimeModel` carries this, unit-tested for summer/winter DST.
- **Sharing:** all new code is app-local in flyfun-weather (reusing RZFlight's existing
  `KnownAirports.rankedSearch`). Promoting the pure TZ helpers + a `KnownAirports.bundled`
  loader into RZFlight remains a deliberate follow-up (both apps track rzflight `main`).

---

> Original plan retained below for reference.

## Guiding insight

The web backend already exposes every endpoint we need. This is almost entirely
**iOS client-side work** — wire up SwiftUI to APIs the web already calls and iOS
currently ignores. Server changes: none expected (verify only).

Reference files:
- iOS create: `app/flyfun-weather/flyfun-weather/Views/Flights/AddFlightView.swift`,
  `ViewModels/AddFlightViewModel.swift`, `Models/API/CreateFlightRequest.swift`
- iOS briefing/SSE: `Views/Briefing/BriefingContainerView.swift`,
  `ViewModels/BriefingViewModel.swift` (`refresh()` already streams progress)
- Web reference: `web/ts/flights-main.ts`, `web/ts/components/route-interpret.ts`,
  `web/ts/utils/timezone.ts`
- Proven autocomplete pattern: `flyfun-forms/.../Services/AirportDatabase.swift`

---

## Item 1 — Fix the stuck "Loading Briefing" (the real bug)

**Root cause.** `POST /api/flights` only *saves* the flight; it does not generate a
briefing. After create, `FlightListView` sets `selectedFlight = flight`, the briefing
container loads, calls `latestPack()`, gets 404 (no pack yet), and sits on
`ProgressView("Loading briefing...")` / `.error`. The web kicks off a refresh after
create; iOS never does. All progress machinery already exists in
`BriefingViewModel.refresh()` (streams `/packs/refresh/stream` with stage/detail/%).

**Plan.**
- In `BriefingViewModel.loadBriefing()`: when `latestPack()` indicates *no pack exists*
  (distinguish 404 / empty from a real network error), call `refresh()` instead of
  setting `.error`. This reuses the existing progress-bar UI end to end.
- Distinguish "no pack yet" from transient errors so we don't auto-refresh-loop on a
  genuine network failure. Add a `noPackYet` path (check the `APIError`/status).
- `BriefingContainerView` already shows the refresh progress overlay during
  `refreshState == .refreshing`; confirm it renders during the initial auto-refresh
  (it should, since it's the same state).
- Optional nicety: pass a hint from the create completion so the container knows this
  is a brand-new flight and shows "Generating briefing…" immediately rather than
  "Loading…".

**Files:** `BriefingViewModel.swift`, `BriefingContainerView.swift` (minor),
possibly `FlightListView.swift` (pass new-flight hint).
**Effort:** small. **Risk:** low. Ship-first candidate.

---

## Item 6 — Dynamic ICAO autocomplete (infrastructure first; enables local validation)

**Feasibility.** Proven. RZFlight ships `KnownAirports.rankedSearch(needle:limit:)`
(in-memory, tiered: ICAO prefix → ICAO contains → name prefix → name contains).
flyfun-forms already wraps it in `AirportDatabase` (singleton, loads a bundled
`airports.db` on a `Task.detached`). No network → typing never blocks.

**Plan.**
- **Bundle the DB.** Add an `airports.db` to the iOS app bundle. forms ships a 10 MB
  airports-only DB (vs the 33 MB full `nav.db`). Decision: reuse forms' airports-only
  DB build so we add ~10 MB, not 33 MB. (Confirm the build step that produces forms'
  `airports.db`; replicate it from `data/nav.db` if needed.)
- **Copy the singleton.** Port `AirportDatabase.swift` verbatim (it's generic RZFlight
  glue): `load()` on app launch, `ready()`, `search(needle:limit:)`,
  `airport(icao:)`.
- **Suggestions UI.** A `RouteAutocompleteController` (small `@Observable`) that:
  - watches the waypoints text field,
  - extracts the **current last token** (after the last space/dash/comma),
  - debounces ~150 ms, runs `search()` on a background queue,
  - publishes results *only if the last token is still the same one searched*
    (drop stale results — this is exactly the "if the user already typed the next
    word, autocomplete that one instead" behaviour),
  - renders a horizontal suggestions bar above the keyboard; tapping a suggestion
    replaces the last token with its ICAO + a trailing space.
- Min token length 1–2 chars before searching to avoid huge result sets.

**Why early in the sequence:** the bundled DB also gives us local ICAO validation,
which strengthens the interpret feedback (#5) and lets us pre-validate before calling
the server.

**Files (new):** `Services/AirportDatabase.swift`, `Views/Flights/RouteAutocomplete*`,
bundle resource `airports.db`. **Effort:** medium. **Risk:** bundle-size; otherwise low.

---

## Item 3 — Profile selector

**Backend:** `GET /user/profiles` → `[ProfileResponse]`. A profile presets
`cruise_altitude_ft`, `flight_ceiling_ft`, `speed_kt`, plus model/method choices.
On create, web sends `profile_id`; server fills unspecified fields from the profile.

**Plan.**
- New iOS models: `ProfileResponse` + `ProfileSettings` (Decodable) mirroring
  `profiles.py`. Repository: `fetchProfiles() -> [ProfileResponse]`.
- Add `profileId: Int?` to `CreateFlightRequest` (and `UpdateFlightRequest` if edit
  should carry it). **Coding key:** `profile_id`.
- `AddFlightView`: a `Picker` for profile (default-first). On selection, override the
  altitude/ceiling fields with the profile's values (matching web behaviour), and feed
  `speed_kt` into the duration auto-calc.
- Load profiles in `AddFlightViewModel` alongside aircraft.

**Files:** `CreateFlightRequest.swift`, new `Models/API/ProfileResponse.swift`,
`BriefingRepository.swift`, `AddFlightView.swift`, `AddFlightViewModel.swift`.
**Effort:** small–medium.

---

## Item 2 — Autorouter import + recent-route dropdown (+ FPL already exists)

iOS already has FPL paste import (parses via `POST /flights/parse-fpl`). Two additions:

**2a. Autorouter import.**
- Backend: `GET /flights/autorouter-routes?limit=25` → `{ routes: [...] }`. Gated by
  linked autorouter credentials (`hasAutorouterCreds` in `/user/preferences`).
- Plan: repository `fetchAutorouterRoutes(limit:)`; a picker sheet listing routes
  (dep→dest, date, distance, aircraft). On select, run the route's `fplan` through the
  **existing** FPL parse → apply path (reuse current `applyParsedFpl` equivalent).
- If not linked: show a sheet pointing to settings/web to link autorouter (iOS does not
  own the OAuth link flow — same as today's credential model).

**2b. Recent-route dropdown.**
- **No API.** Web builds this client-side from the flight list: sort by created, take up
  to 8 distinct waypoint sequences. iOS already loads the flight list — derive the same
  list in `AddFlightViewModel`, expose a `Menu`/`Picker` near the waypoints field that
  fills the field on selection. Hide when <2 distinct routes.

**Files:** `BriefingRepository.swift`, new picker view for autorouter,
`AddFlightView.swift`, `AddFlightViewModel.swift`. **Effort:** medium (autorouter),
small (recent routes).

---

## Items 4 + 5 — Timezone handling and "Interpret" feedback (share one backend call)

Both rely on `POST /flights/interpret-route` (returns `interpreted` / `skipped` /
`off_route` + per-waypoint `{icao,name,lat,lon,timezone}`). `POST /flights/route-distance`
returns the same waypoint+timezone shape plus `total_distance_nm` for duration calc.

### Item 5 — Interpret popup
- After the route field settles (debounced) or on an explicit **Interpret** button,
  call `interpret-route`.
- Show a popup/sheet: the understood chain (`interpreted`), `skipped` chips (amber,
  "not recognized"), `off_route` chips (muted, "too far"), and a **MapKit** map drawing
  the resolved polyline + waypoint pins (iOS already uses MapKit in `RouteMapView`).
- On save, mirror web: if nothing skipped/off-route, accept silently; otherwise show the
  confirmation popup with Accept/Cancel.
- Local pre-filter: with the bundled DB (#6) we can flag obviously-unknown tokens before
  the server call, but the server stays the source of truth (handles airways, coords,
  detour geometry).

### Item 4 — Timezone (port the web logic; do NOT copy flyfun-forms' buggy version)
The web approach is correct and DST-safe; flyfun-forms' `TimeEntryView` has DST-naive
labels and date-wrap bugs — port the web, not forms.

- Keep weatherbrief's existing nicer time-entry UX; **add a timezone dropdown**.
- Populate the dropdown from the route's waypoint timezones (the `timezone` fields from
  `interpret-route`/`route-distance`), plus UTC always. De-dup; label with the DST-correct
  offset **for the selected date**.
- Port `web/ts/utils/timezone.ts`:
  - `getUtcOffsetMinutes(tz, refDate)` → Swift `TimeZone(identifier:)?.secondsFromGMT(for: refDate)`
    (DST-aware by date — this is the key correctness win over forms).
  - `buildTimezoneOptions`, `localToUtc`, `utcToLocal`, `nearestMinuteOption`,
    `formatUtcOffset` → small Swift helpers.
- Model: keep a single **internal UTC** instant; display it in the selected zone;
  re-render on zone change (preserve the instant). On submit, send UTC ISO-8601
  (fixes today's bug where the iOS `DatePicker` local time is force-stamped `Z`).
- Duration auto-calc: use `total_distance_nm` (route-distance) + profile/aircraft
  `speed_kt` (IAS→TAS at altitude), unless the user manually edited duration (lock flag),
  matching web.

**Files:** new `Utils/Timezone.swift`, new `Views/Flights/RouteInterpretView.swift`
(map popup), changes to `AddFlightView.swift` / `AddFlightViewModel.swift`, repository
methods `interpretRoute` + `routeDistance`. **Effort:** medium each.

---

## Recommended build sequence

1. **#1** — fix stuck Loading Briefing (smallest, biggest UX win).
2. **#6 infra** — bundle `airports.db` + `AirportDatabase` + autocomplete bar.
3. **#3** — profile selector.
4. **#2** — autorouter import + recent-route dropdown.
5. **#4 + #5** — timezone port + interpret popup (shared `interpret-route` call).

## Open decisions / confirmations before coding
- **Bundle size:** OK to add ~10 MB `airports.db`? (forms already does.) Confirm the
  build step that produces the airports-only DB from `nav.db`.
- **Profiles on iOS:** new models needed (no `ProfileResponse` exists on iOS today).
- **Edit parity:** apply the same TZ/interpret/profile UX to the *edit* flight sheet
  (same `AddFlightView`), or create-only for v1?
- **Server:** confirm `/flights/interpret-route`, `/flights/route-distance`,
  `/flights/autorouter-routes`, `/user/profiles` are all reachable by the iOS auth
  context (token vs cookie) — expected yes, verify.
