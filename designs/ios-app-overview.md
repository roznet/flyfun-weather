# WeatherBrief iOS Companion App — Overview

> iOS/iPad app for in-flight condition reporting, offline briefing access, and collaborative PIREPs

## Related Docs

- [Architecture](./ios-app-architecture.md) — tech stack, MVVM+Repository, auth flow
- [Data Models](./ios-app-data-models.md) — Swift `@Model` classes + Codable structs
- [Server API](./ios-app-server-api.md) — endpoints consumed + added, server data model
- [Features](./ios-app-features.md) — vision, briefing sync, PIREP filing modes, community feed
- [UI](./ios-app-ui.md) — cockpit constraints, screen layouts, report cards
- [Sync & Prompting](./ios-app-sync-prompting.md) — offline queue, WebSocket, forecast-driven prompts
- [Roadmap](./ios-app-roadmap.md) — phases, decisions, open questions

## Current Implementation Status (2026-05-30)

Phase 1 complete. Phase 2 complete (offline resilience hardening). Phase 3 M0 (aircraft registry) and M1 (PIREP submit/view) implemented with offline queue flush. Live in-flight tracking ("Start Flight") shipped, plus cross-section parity with web (GRAMET/NWP/SFIP/Ogimet layers).

### What's built

**Authentication**: adopts `FlyFunCommon` (login buttons aligned with flyfun-forms). Google OAuth + native Sign in with Apple (`SignInWithAppleButton`, token exchange with `/auth/apple/token` from flyfun-common). JWT in Keychain. Dev login for simulator (`/auth/dev-token`, `#if targetEnvironment(simulator)`). Dev server base URL points at the HTTPS dev instance (`https://localhost.ro-z.me:8443`).

**Flight list**: `NavigationSplitView` — sidebar + detail briefing pane on iPad (collapsible). "ORIGIN → DEST" titles. Pull-to-refresh. Aircraft dropdown (hidden when none). Ellipsis menu replaces logout button.

**Briefing viewer** (tabs: Advisories, Cross-Section, Map, Digest, PIREPs):
- Advisory dashboard — 3-column grid on iPad, model status badges as colored capsules, short names (MF for Météo-France)
- Airport conditions — departure/arrival side-by-side on iPad
- Cross-section — Canvas renderer aligned with the web (smooth/soft cloud bands). Layer stack under `Views/CrossSection/Layers/` includes cloud bands (soft + NWP variants), icing (model, Ogimet NWP, SFIP), CAT, inversions, convective towers/background (thermo + NWP), terrain, temperature lines, reference lines. Model selector. Layer toggle chips.
- Native Skew-T — tap cross-section point → `SkewTView` (RZSkewT package) renders below with thermodynamics, wind barbs, CAPE/CIN shading, LCL/LFC/EL markers, FL labels
- Route map, digest view
- PIREPs tab — read-only list for the flight, expandable rows with severity bars, hazard icons, own-badge. Tab gated by `userPreferences.pirepCanView`; in-flight reporting gated by `pirepCanPublish` (server flags, refreshed when the briefing opens)

**PIREP reporting (M1)**:
- In-flight reporting sheet (toolbar button during flight window)
- `PirepViewModel` — GPS pre-fill altitude, all fields unselected (no confirmation bias), smart field ordering, location from `FlightTrackingService`
- `PirepOfflineStore` actor — JSON-file queue in Documents, batch sync via `/api/pireps/batch`, dedup by `client_uuid`
- **Auto-flush on connectivity**: on successful online submission, `offlineStore.sync(using: repository)` drains the queue
- Network-error detection: `URLError` codes (`.notConnectedToInternet`, `.timedOut`, `.networkConnectionLost`, `.cannotConnectToHost`) via `underlyingError as? URLError` since `APIClient` wraps URL errors in `APIError` — the raw URL code is on the underlying error, not the top-level. Offline PIREPs enqueued with synthetic `PirepResponse.offline`
- `BriefingRepository` extended with `submitPirep`, `submitPirepsBatch`, `fetchPireps`

**Live in-flight tracking ("Start Flight")**:
- `FlightTrackingService` (`NSObject` + `CLLocationManagerDelegate`) — `start(routePoints:flightEndTime:)` / `stop()`, projects live GPS onto the route, publishes `isTracking`, `currentLocation`, `projectedPosition`, `locationUpdateCount`
- Start/Stop toolbar button in `BriefingContainerView` (only during the in-flight window)
- Live aircraft position drawn on the cross-section (`CrossSectionView`) and route map (`RouteMapView`); also used to pre-fill PIREP location/altitude

**Pack management**: Pack history picker (toolbar dropdown with D-N labels + assessment badges). Server refresh with SSE streaming progress (stage, percentage). Active-refresh detection (polls for refreshes started elsewhere).

**Offline caching & resilience**:
- `CachingBriefingRepository` wraps `OnlineBriefingRepository` with multi-tier fallback
- Flight list fallback: online API → cached `flights.json` → recover from per-flight cached pack data
- `isServingCachedFlights` flag exposed to ViewModels
- Cached-flight indicators — `FlightListViewModel.cachedFlightIds: Set<String>` from `caching.cachedPacks()`; green dot per card, non-cached disabled offline
- Resilient fallback — serves any matching cached timestamp, not just exact requested
- Explicit per-pack download button with real byte-level progress (`requestDataStreaming` + `StreamingDownloadDelegate`; a single bundled, server-gzipped request fetches all endpoints, then writes each per-endpoint payload to disk)
- Caches 5 endpoints (advisories, digest, snapshot, route-analyses, elevation) to `Application Support/BriefingCache/<flightId>/<timestamp>/<endpoint>.json` as plain JSON (decompressed on write); sounding-profile stays online-only
- `BriefingCacheStore` actor with JSON index (`index.json`)
- Sign-out protection when offline (would strand auth)

**Backend additions**:
- `GET /api/flights/{flight_id}/packs/{timestamp}/sounding-profile/{point_index}/{model}` (in `api/packs.py`) — raw T/Td/wind at pressure levels for client-side Skew-T. Served from the `sounding_profiles.json.gz` sidecar when present, else rebuilt from `cross_section.json`

### What's NOT built yet

- Push notifications for briefing updates (Phase 2 M2)
- SwiftData persistence (current cache is file-based)
- Auto-sync / background refresh
- Sounding profiles in offline download (online-only for now)
- Phase 3 M2: route/airport watches, APNs notifications, spatial matching
- Phase 3 M3: post-flight debrief, validation tooling
- Phase 3a: voice PIREP (Siri shortcut), proactive prompting engine
- Phase 3c: live PIREP sharing (WebSocket)

## References

- Key code paths: `app/flyfun-weather/flyfun-weather/`
- Server architecture: [architecture.md](./architecture.md)
- Data models: [data-models.md](./data-models.md)
- Advisory system: [advisories.md](./advisories.md)
