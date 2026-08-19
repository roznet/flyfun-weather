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

## Current Implementation Status (2026-08-15, App Store 1.5 / build 11)

Phase 1 complete. Phase 2 complete (offline resilience hardening). Phase 3 M0 (aircraft registry) and M1 (PIREP submit/view) implemented with offline queue flush. Since then: live in-flight tracking ("Start Flight"), cross-section parity with web (GRAMET/NWP/SFIP/Ogimet layers), APNs push, App Intents / Siri Shortcuts, flight sharing, post-flight debrief, the forecast map, and What's New. The briefing screen was re-cut into four tabs by #310 — check `BriefingTab` before trusting any older tab list.

### What's built

**Authentication**: adopts `FlyFunCommon` (login buttons aligned with flyfun-forms). Google OAuth + native Sign in with Apple (`SignInWithAppleButton`, token exchange with `/auth/apple/token` from flyfun-common). JWT in Keychain. Dev login for simulator (`/auth/dev-token`, `#if targetEnvironment(simulator)`). Dev server base URL points at the HTTPS dev instance (`https://localhost.ro-z.me:8443`).

**Flight list**: `NavigationSplitView` — sidebar + detail briefing pane on iPad (collapsible). "ORIGIN → DEST" titles. Pull-to-refresh. Red unseen dot per card when `latestBriefing.unseen` (server-derived badge, cleared by opening the briefing). Coachmark tips (`Tips/FlightListTips.swift`). The ellipsis "More" menu replaces the logout button and carries: Add Flight, Forecast Map, Settings, Open Website, Help, What's New (unseen count in the title, red dot on the ellipsis), Send Feedback, Select & Delete Flights…, Sign Out. Per-row context menu: Edit Flight, Add PIREP, Share Flight, Unsubscribe (subscriber rows), Delete Flight.

Single-flight Delete (swipe / context menu, owner-only, behind a confirmation alert) and **multi-select bulk delete** — "Select & Delete Flights…" opens `FlightSelectionView`, a *separate* `List(selection:)` sheet rather than an edit mode on the sidebar list, so the second selection binding can never disturb the split view's `SidebarSelection`. Owned flights only (a subscriber's row isn't selectable; shared flights are dropped with Unsubscribe), online-only, chunked at the server's 200-id cap, and a partial `not_found` result is surfaced rather than swallowed (#553).

**Add / edit flight form** (`AddFlightView` + `AddFlightViewModel`): route entry with autocomplete (`RouteAutocompleteController`) and autorouter interpretation, departure time / duration (`DepartureTimeModel`, over-long durations clamped on load, not on the pilot's next edit), and the **aircraft picker** (with an inline `AircraftFormSheet` to create one) plus the profile picker — those live here, *not* as a flight-list dropdown. A structural edit (route/date) can't be applied in place, so the form asks Move (rewrite this flight) vs Duplicate as new flight — the iOS shape of the web's swap-Save toolbar. Edits that force a re-brief go through a cost confirm; aircraft-only edits save silently.

**Briefing viewer** — four core tabs (`BriefingTab`, #310): **Advisory · Discussion · Cross-Section · Map**, plus a gated **PIREPs** tab appended when permitted. Native `TabView` (top pill band on iPad, bottom bar on iPhone). There is no Digest tab (the digest *is* Discussion) and no standalone Skew-T tab (it folds under Cross-Section). Cross-tab deep links travel as a single `FocusIntent` (target tab + model / layer / map metric / advisory preset / point), so an advisory card can open the cross-section already scrubbed to the right point and lens.
- **Advisory** (`AdvisoryTabView`) — the read-me-first surface: accented hero (traffic light + reason), digest-altitude staleness warning, digest 👍/👎, debrief card, advisory grid (AMBER/RED as cards, GREEN collapsed into one all-clear strip), airport conditions side-by-side on iPad, and — on D-0 — the METAR/TAF observations comparison and route SIGMET area hazards; weather alternates and timing scenarios when the pack carries them; watch chips close it out. Sticky scroll-spy bar (`SectionSpyBar`) jumps between sections.
- **Discussion** (`DiscussionTabView`) — the LLM digest, section by section, with the same scroll-spy. (`Views/Briefing/DigestView.swift` is the pre-#310 view and is now unreferenced.)
- **Cross-Section** — Canvas renderer aligned with the web (smooth/soft cloud bands). Layer stack under `Views/CrossSection/Layers/`: cloud bands (soft + natural), icing (model, Ogimet NWP, SFIP), CAT, inversions, convective towers/background (thermo + NWP), terrain, temperature and stability lines, reference lines, plus a `HighlightLayer` that draws an advisory's highlight geometry (scrim + verdict ribbon, #374). `CrossSectionPresets` supplies the advisory "lenses". Model selector, layer toggle chips. Below the chart: `RouteGraphView` (per-metric route profile) and an embedded `SkewTTabView` → `SkewTDetailView`, which renders `SkewTView` from RZSkewT with thermodynamics, wind barbs, CAPE/CIN shading, LCL/LFC/EL markers, FL labels.
- **Map** (`RouteMapView`) — MapKit route + airport markers with metric selection (`MapMetrics`) and an airport **forecast overlay** (`RouteForecastOverlayModel`, day-slot slices resolved from the departure time).
- **PIREPs** — list for the flight, expandable rows with severity bars, hazard icons, own-badge. Tab shown when `pirepCanView` **or** `pirepCanPublish` (a publisher reaches it even without view permission). When `pirepCanPublish`, the tab carries a permanent **"Report a PIREP"** action (a persistent bottom bar, present in every load state) that opens the reporting sheet — no in-flight-window gate. For a **publish-only** account (`pirepCanView == false`) the container skips `loadPireps()` (the query would 403) and the tab shows a "filing enabled, viewing not yet" message above the add bar rather than a generic error.

**Forecast map** (`Views/ForecastMap/`, flight-list level not briefing level): the app's port of the web forecast map — MapKit, metric catalog from `Resources/metrics-catalog.json` (`MapMetricsCatalog`), tappable airport cards, and an iPad sidebar toggle so the sidebar can't strand the user. Reached from More → Forecast Map and from the `maps.html` universal link.

**Flight sharing**: owner shares a `/s/{code}` link via `ShareActivitySheet` (not `ShareLink` — that entry point needs the sheet's control). A recipient lands in `SharedFlightPreviewView` and subscribes. Subscriber rows are read-only: no delete, not bulk-selectable, Unsubscribe instead, and refresh is gated (see Pack management).

**Post-flight debrief**: `DebriefFormView` + `DebriefViewModel` over `/api/flights/{id}/debrief` (GET/PUT/DELETE), taxonomy from `DebriefTaxonomy` / the help catalog. Entry is a card on the Advisory tab; saving signals the flight list so the "Debriefed ✓" glyph updates without waiting for a foreground reload. Digest 👍/👎 (`DigestFeedbackView`) sits alongside.

**Help & feedback**: `HelpCatalogStore` mirrors the web help catalog to disk (raw bytes + `ETag` in `UserDefaults`, bundled `metrics-catalog.json` baseline for first run) so `HelpInfoButton` (i) popups render offline. The web is the single source of truth — no help text is hand-written in Swift. `FeedbackFormView` posts feedback; Settings deep-links to the web *settings* page (not the site root) for preferences/aircraft/profiles/services.

**PIREP reporting (M1)**:
- Reporting sheet reachable three ways: the briefing toolbar "Report PIREP" button and the PIREPs-tab "Report a PIREP" action (both gated on `pirepCanPublish` only — permanent, no flight-window gate), and an "Add PIREP" flight-list context-menu item (gated on `pirepCanPublish`, the flight's tracking window, **and** `isEditable`). **Flight linkage caveat:** `submit` sends no `pack_id`, so the server links a report to a flight only when its `observed_at` falls in the flight's window **and** the report's `user_id` equals the flight owner's (`storage/pireps.py:130-140`). The list entry is therefore gated on both window and ownership so a list-filed report always lands in the flight's tab. Reports from the two in-briefing entries are always accepted but stay standalone community position reports (visible in bounds/airport queries, not that flight's tab) when filed outside the window **or** by a subscriber on a shared flight (`user_id` ≠ owner) — a subscriber's report never links regardless of timing
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

**Pack management**: `BriefingPackToolbar` merges freshness and pack history into one **leading chip** (#310) — freshness dot + current D-N label opens the history picker (D-N labels + assessment badges) with the detailed freshness line as a non-interactive header. That chip also carries the offline-save badge (green saved / spinner saving / red failed) and the Download / Retry / Save-for-Offline actions: the standalone trailing download **button was retired** (downloads are automatic, so nobody needed to press it) and the menu now opens whenever a pack exists, not only when the history has more than one entry. Server refresh streams SSE progress (stage, percentage); active-refresh detection polls for refreshes started elsewhere. Refresh is never yanked out from under the user — it stays visible but renders as a *menu carrying the reason* when the server would refuse: subscriber (`_load_owned_flight` 404s) or past the live window, `departure + min(duration + 3h, 12h)`, mirroring the server's `_classify_refresh_time` (deliberately **not** `isPast`, which would kill refresh across the post-arrival grace window). Seamless pack sync — `BriefingViewModel.syncLatestPack()` (cheap, no-op if unchanged) fires on appear / pull-to-refresh / foreground / push, distinct from the ↻ full-refresh.

**Push notifications (shipped)**: `Services/PushNotifications.swift` — `AppDelegate` (`UIApplicationDelegate` + `UNUserNotificationCenterDelegate`) registers for remote notifications, hex-encodes the device token, and posts it to the server (`api/devices.py`). Refresh-complete pushes deep-link into the flight/briefing via `PushSupport.pendingNavigation(from:)` → `PendingNavigation`. Server-side push is off by default (migration required, `APNS_*` env). See [Briefing Refresh Notifications](./ios-app-briefing-notifications.md).

**App Intents / Siri Shortcuts (shipped)**: `AppIntents/` — flight/airport entities + queries, intents (CheckBriefing, OpenBriefing, OpenFlightList, RefreshBriefing, AirportWeather, FlightsOverview), `FlyFunShortcuts` provider, Spotlight donation. See [App Intents](./ios-app-intents.md).

**Offline caching & resilience**:
- `CachingBriefingRepository` wraps `OnlineBriefingRepository` with multi-tier fallback
- Flight list fallback: online API → cached `flights.json` → recover from per-flight cached pack data
- `isServingCachedFlights` flag exposed to ViewModels
- Cached-flight indicators — `FlightListViewModel.cachedFlightIds: Set<String>` from `caching.cachedPacks()`; green dot per card, non-cached disabled offline
- Resilient fallback — serves any matching cached timestamp, not just exact requested
- Per-pack download with real byte-level progress (`requestDataStreaming` + `StreamingDownloadDelegate`; a single bundled, server-gzipped `/packs/{ts}/bundle` request fetches all endpoints, then writes each per-endpoint payload to disk). Triggered by auto-download or the pack chip's menu — there is no longer a dedicated download button
- Caches the 5 required endpoints (advisories, digest, snapshot, route-analyses, elevation) **plus every `(point, model)` sounding profile** (keyed `sounding-{pt}-{model}`, straight from the bundle) to `Application Support/BriefingCache/<flightId>/<timestamp>/<endpoint>.json` as plain JSON (decompressed on write). Because the sounding keys match what `soundingProfile()` reads, a downloaded pack renders Skew-Ts instantly and fully offline — no per-tap round-trip
- **Auto-download (opt-in, default Wi-Fi only)** — `AppSettingsStore.autoDownloadMode` (`off` / `wifiOnly` / `wifiAndCellular`, in Settings). When a briefing is displayed for a flight that is **today or later**, the latest pack auto-downloads in the background (gated by `NetworkMonitor` connectivity), reusing the same bundle + download banner. `BriefingViewModel.maybeAutoDownloadLatest()` fires from `loadBriefing` and after a refresh produces a new pack
- **Cache eviction** — `CachingBriefingRepository.pruneStalePacks(olderThanDays:)` drops packs whose flight departed > `AppState.cacheRetentionDays` (7) days ago; departure date comes from `CachedPackEntry.departureTime` (captured at download), falling back to the cached flight record. Runs on launch/foreground (`scenePhase == .active`). A **second sweep** then walks the on-disk flight directories (`BriefingCacheStore.flightDirectoryIDs()`) to reclaim flight-level sidecars (`flight.json`, `packs.json`, …) written just by *viewing* a flight — those never enter the index, so the pack loop can't reach them — aging them out on the same departure cutoff. Both sweeps keep any flight with live packs, and any flight whose departure can't be determined (conservative — never delete blind)
- `BriefingCacheStore` actor with JSON index (`index.json`)
- Sign-out protection when offline (would strand auth)

**What's New (release stream, #550)**: More → What's New opens `Views/Help/WhatsNewView.swift`, a native list over the **same unified stream the web help page shows** (`GET /api/messages`) — not a platform-filtered feed, since most entries reach app users on the server deploy with no app release involved. `WhatsNewStore` (`Services/`) mirrors `HelpCatalogStore`: it persists the last-fetched stream to `Documents/whats-new.json` and seeds from it at init, so the view reads in the cockpit like every other screen. The stream is global, so nothing user-specific is written to disk; the unseen count *is* per-user and is deliberately never persisted (a stale dot is worse than no dot). Bodies render through `MarkdownLiteText` with `bulletLists: true` / `normalizeRunOnLists: false` — authored text already carries real newlines, and every body written so far is a `- ` list. Badge: `/api/messages/status` counts **highlighted** rows only, drawn as a red dot on the More ellipsis; opening the view POSTs `/messages/seen`. That pointer (`messages_last_seen_id`) is one value per user shared with the web, so marking seen here clears the web's nav dot too — same cross-surface behaviour as the briefing badge. The app deliberately omits the install call to action the web renders under `app_release` entries: a reader already inside the app has nothing to install. Entry authoring and the draft-at-archive / publish-at-approval split: [ios-release.md §A6](./references/ios-release.md).

**Backend additions** (both in `api/packs.py`):
- `GET /api/flights/{flight_id}/packs/{timestamp}/sounding-profile/{point_index}/{model}` — raw T/Td/wind at pressure levels for client-side Skew-T. Served from the `sounding_profiles.json.gz` sidecar when present, else rebuilt from `cross_section.json`
- `GET /api/flights/{flight_id}/packs/{timestamp}/bundle` — one gzipped response carrying every cacheable endpoint (the offline download path)

### What's NOT built yet

- SwiftData persistence (current cache is file-based)
- True background refresh (auto-download runs only while the briefing screen is open / on foreground, not via `BGTaskScheduler` or a background `URLSession`)
- Phase 3 M2: route/airport watches, spatial matching (refresh-complete push itself has shipped; the watch/spatial-match layer has not)
- Phase 3 M3: forecast-vs-reality validation tooling (the post-flight *debrief* half shipped — see above; the verification loop it feeds is still web/admin-side)
- Phase 3a: voice PIREP (no PIREP App Intent yet), proactive prompting engine
- Phase 3c: live PIREP sharing (WebSocket — no `URLSessionWebSocketTask` anywhere in the app)

## References

- Key code paths: `app/flyfun-weather/flyfun-weather/`
- Server architecture: [architecture.md](./architecture.md)
- Data models: [data-models.md](./data-models.md)
- Advisory system: [advisories.md](./advisories.md)
- Debrief (shared web + iOS contract): [debrief.md](./debrief.md)
- Forecast map (web original the iOS port follows): [forecast-page.md](./forecast-page.md)
- Release / App Store process, incl. the What's New entry: [references/ios-release.md](./references/ios-release.md)
- Web↔iOS parity audits (hand-copied catalogs, DTOs): the `sync-ios-web` skill
