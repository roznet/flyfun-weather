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
- [App Intents](./ios-app-intents.md) — Siri / Shortcuts / Spotlight surface (shipped, `AppIntents/`)
- [Briefing Refresh Notifications](./ios-app-briefing-notifications.md) — APNs push on refresh-complete (shipped)

## Current Implementation Status (2026-07-11)

Phase 1 complete. Phase 2 complete (offline resilience hardening). Phase 3 M0 (aircraft registry) and M1 (PIREP submit/view) implemented with offline queue flush. Live in-flight tracking ("Start Flight") shipped, plus cross-section parity with web (GRAMET/NWP/SFIP/Ogimet layers). APNs push (refresh-complete) and the App Intents / Siri Shortcuts surface have since shipped.

### What's built

**Authentication**: adopts `FlyFunCommon` (login buttons aligned with flyfun-forms). Google OAuth + native Sign in with Apple (`SignInWithAppleButton`, token exchange with `/auth/apple/token` from flyfun-common). JWT in Keychain. Dev login for simulator (`/auth/dev-token`, `#if targetEnvironment(simulator)`). Dev server base URL points at the HTTPS dev instance (`https://localhost.ro-z.me:8443`).

**Flight list**: `NavigationSplitView` — sidebar + detail briefing pane on iPad (collapsible). "ORIGIN → DEST" titles. Pull-to-refresh. Aircraft dropdown (hidden when none). Ellipsis menu replaces logout button.

**Briefing viewer** (tabs: Advisories, Cross-Section, Map, Digest, PIREPs):
- Advisory dashboard — 3-column grid on iPad, model status badges as colored capsules, short names (MF for Météo-France)
- Airport conditions — departure/arrival side-by-side on iPad
- Cross-section — Canvas renderer aligned with the web (smooth/soft cloud bands). Layer stack under `Views/CrossSection/Layers/` includes cloud bands (soft + NWP variants), icing (model, Ogimet NWP, SFIP), CAT, inversions, convective towers/background (thermo + NWP), terrain, temperature lines, reference lines. Model selector. Layer toggle chips.
- Native Skew-T — tap cross-section point → `SkewTDetailView` (in `Views/CrossSection/`) renders `SkewTView` from the RZSkewT package below with thermodynamics, wind barbs, CAPE/CIN shading, LCL/LFC/EL markers, FL labels
- Route map, digest view
- PIREPs tab — list for the flight, expandable rows with severity bars, hazard icons, own-badge. Tab shown when `pirepCanView` **or** `pirepCanPublish` (a publisher reaches it even without view permission). When `pirepCanPublish`, the tab carries a permanent **"Report a PIREP"** action (a persistent bottom bar, present in every load state) that opens the reporting sheet — no in-flight-window gate. For a **publish-only** account (`pirepCanView == false`) the container skips `loadPireps()` (the query would 403) and the tab shows a "filing enabled, viewing not yet" message above the add bar rather than a generic error

**PIREP reporting (M1)**:
- Reporting sheet reachable three ways: the briefing toolbar "Report PIREP" button and the PIREPs-tab "Report a PIREP" action (both gated on `pirepCanPublish` only — permanent, no flight-window gate), and an "Add PIREP" flight-list context-menu item (gated on `pirepCanPublish` **and** the flight's tracking window). **Flight linkage caveat:** `submit` sends no `pack_id`, so the server links a report to a flight only when its `observed_at` falls in that flight's window (`storage/pireps.py`). The list entry is therefore window-gated so a list-filed report always lands in the flight's tab; a report filed from the two in-briefing entries well outside the flight's window is retained as a standalone community position report (visible in bounds/airport queries), not attached to that flight's tab
- `PirepViewModel` — GPS pre-fill altitude, all fields unselected (no confirmation bias), smart field ordering, location from `FlightTrackingService`
- **Smart pre-population without an active track**: the form calls `FlightTrackingService.requestOneShotLocation()` on appear — a single `CLLocationManager.requestLocation()` fix (no route projection) — so lat/lon/altitude pre-fill even when the pilot never tapped "Start". No-op while a live track is already feeding positions
- `PirepOfflineStore` actor — JSON-file queue in Documents, batch sync via `/api/pireps/batch`, dedup by `client_uuid`
- **Auto-flush on connectivity**: on successful online submission, `offlineStore.sync(using: repository)` drains the queue
- Network-error detection: `URLError` codes (`.notConnectedToInternet`, `.timedOut`, `.networkConnectionLost`, `.cannotConnectToHost`) via `underlyingError as? URLError` since `APIClient` wraps URL errors in `APIError` — the raw URL code is on the underlying error, not the top-level. Offline PIREPs enqueued with synthetic `PirepResponse.offline`
- `BriefingRepository` extended with `submitPirep`, `submitPirepsBatch`, `fetchPireps`

**Live in-flight tracking ("Start Flight")**:
- `FlightTrackingService` (`NSObject` + `CLLocationManagerDelegate`) — `start(routePoints:flightEndTime:)` / `stop()`, projects live GPS onto the route, publishes `isTracking`, `currentLocation`, `projectedPosition`, `locationUpdateCount`
- Start/Stop toolbar button in `BriefingContainerView` (only during the in-flight window)
- Live aircraft position drawn on the cross-section (`CrossSectionView`) and route map (`RouteMapView`); also used to pre-fill PIREP location/altitude. The lighter `requestOneShotLocation()` path (no route projection) backs PIREP pre-fill when tracking isn't running

**Pack management**: Pack history picker (toolbar dropdown with D-N labels + assessment badges). Server refresh with SSE streaming progress (stage, percentage). Active-refresh detection (polls for refreshes started elsewhere). Seamless pack sync — `BriefingViewModel.syncLatestPack()` (cheap, no-op if unchanged) fires on appear / pull-to-refresh / foreground / push, distinct from the ↻ full-refresh.

**Push notifications (shipped)**: `Services/PushNotifications.swift` — `AppDelegate` (`UIApplicationDelegate` + `UNUserNotificationCenterDelegate`) registers for remote notifications, hex-encodes the device token, and posts it to the server (`api/devices.py`). Refresh-complete pushes deep-link into the flight/briefing via `PushSupport.pendingNavigation(from:)` → `PendingNavigation`. Server-side push is off by default (migration required, `APNS_*` env). See [Briefing Refresh Notifications](./ios-app-briefing-notifications.md).

**App Intents / Siri Shortcuts (shipped)**: `AppIntents/` — flight/airport entities + queries, intents (CheckBriefing, OpenBriefing, OpenFlightList, RefreshBriefing, AirportWeather, FlightsOverview), `FlyFunShortcuts` provider, Spotlight donation. See [App Intents](./ios-app-intents.md).

**Offline caching & resilience**:
- `CachingBriefingRepository` wraps `OnlineBriefingRepository` with multi-tier fallback
- Flight list fallback: online API → cached `flights.json` → recover from per-flight cached pack data
- `isServingCachedFlights` flag exposed to ViewModels
- Cached-flight indicators — `FlightListViewModel.cachedFlightIds: Set<String>` from `caching.cachedPacks()`; green dot per card, non-cached disabled offline
- Resilient fallback — serves any matching cached timestamp, not just exact requested
- Explicit per-pack download button with real byte-level progress (`requestDataStreaming` + `StreamingDownloadDelegate`; a single bundled, server-gzipped request fetches all endpoints, then writes each per-endpoint payload to disk)
- Caches the 5 required endpoints (advisories, digest, snapshot, route-analyses, elevation) **plus every `(point, model)` sounding profile** (keyed `sounding-{pt}-{model}`, straight from the bundle) to `Application Support/BriefingCache/<flightId>/<timestamp>/<endpoint>.json` as plain JSON (decompressed on write). Because the sounding keys match what `soundingProfile()` reads, a downloaded pack renders Skew-Ts instantly and fully offline — no per-tap round-trip
- **Auto-download (opt-in, default Wi-Fi only)** — `AppSettingsStore.autoDownloadMode` (`off` / `wifiOnly` / `wifiAndCellular`, in Settings). When a briefing is displayed for a flight that is **today or later**, the latest pack auto-downloads in the background (gated by `NetworkMonitor` connectivity), reusing the same bundle + download banner. `BriefingViewModel.maybeAutoDownloadLatest()` fires from `loadBriefing` and after a refresh produces a new pack
- **Cache eviction** — `CachingBriefingRepository.pruneStalePacks(olderThanDays:)` drops packs whose flight departed > `AppState.cacheRetentionDays` (7) days ago; departure date comes from `CachedPackEntry.departureTime` (captured at download), falling back to the cached flight record. Runs on launch/foreground (`scenePhase == .active`). A **second sweep** then walks the on-disk flight directories (`BriefingCacheStore.flightDirectoryIDs()`) to reclaim flight-level sidecars (`flight.json`, `packs.json`, …) written just by *viewing* a flight — those never enter the index, so the pack loop can't reach them — aging them out on the same departure cutoff. Both sweeps keep any flight with live packs, and any flight whose departure can't be determined (conservative — never delete blind)
- `BriefingCacheStore` actor with JSON index (`index.json`)
- Sign-out protection when offline (would strand auth)

**Backend additions**:
- `GET /api/flights/{flight_id}/packs/{timestamp}/sounding-profile/{point_index}/{model}` (in `api/packs.py`) — raw T/Td/wind at pressure levels for client-side Skew-T. Served from the `sounding_profiles.json.gz` sidecar when present, else rebuilt from `cross_section.json`

### What's NOT built yet

- SwiftData persistence (current cache is file-based)
- True background refresh (auto-download runs only while the briefing screen is open / on foreground, not via `BGTaskScheduler` or a background `URLSession`)
- Phase 3 M2: route/airport watches, spatial matching (refresh-complete push itself has shipped; the watch/spatial-match layer has not)
- Phase 3 M3: post-flight debrief, validation tooling
- Phase 3a: voice PIREP (Siri shortcut), proactive prompting engine
- Phase 3c: live PIREP sharing (WebSocket)

## References

- Key code paths: `app/flyfun-weather/flyfun-weather/`
- Server architecture: [architecture.md](./architecture.md)
- Data models: [data-models.md](./data-models.md)
- Advisory system: [advisories.md](./advisories.md)
