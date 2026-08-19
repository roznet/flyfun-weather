# iOS App — Implementation Roadmap

> 3-phase roadmap, decisions made, open questions

The app is built in three major phases, each delivering standalone value. The architecture (MVVM + Repository) is designed so each phase extends the previous without rewrites.

> Status note: this is the original forward-looking roadmap. Several details below describe *intended* design that the shipped code diverged from — corrections are inlined. Notably the app does **not** use SwiftData (caching is file-based via `BriefingCacheStore` + `FileManager`); the `/companion` payload endpoint of Phase 2 was **never built** (the app caches the normal API responses instead); the shipped iOS PIREP feature is **flight-linked only** (no standalone PIREP, no community feed/map) and is gated by two server-side per-user flags, `pirep_can_view` / `pirep_can_publish`; PIREP `source` is the string `"inflight"` (not the planned `.standalone`/`.manual` enum); and PIREP server routes live at `/api/pireps` (batch at `/api/pireps/batch`), not `/api/observations`. APNs push has since shipped, but for *refresh-complete* deep-links (`Services/PushNotifications.swift`), not for PIREPs. App Intents / Siri Shortcuts also shipped (`AppIntents/`) — but note that is a briefing-navigation surface, NOT the voice-PIREP filing envisioned in 3a below (still unbuilt). The richer community/standalone PIREP surface (query by airport/bounds/radius, hazard + severity + altitude filters) currently lives on the **web/server** side (`api/pireps.py`, `web/pireps.html`), not in the app. See [Overview](./ios-app-overview.md) for the authoritative current status.

The ultimate goal is the PIREP system (Phase 3) — the two-way weather conversation that makes this app uniquely valuable. Phases 1 and 2 build the foundation and deliver value along the way.

## Phase 1 — Online Briefing Viewer ✅

**Goal**: Native mobile briefing viewer that replaces checking the web on a phone. Pilot opens app, sees flights, taps one, views full briefing natively.

**Server-side**: Add `?platform=ios` to OAuth callback (redirect to custom URL scheme).

**App work**:

- **Foundation**
  - Xcode project with SwiftUI App lifecycle (no SwiftData — see Decisions Made)
  - SPM deps: RZFlight (airport DB, autocomplete, tracking geometry) + RZSkewT (Skew-T rendering). RZUtils was planned but never imported
  - API client (URLSession + async/await, JWT auth header) — `APIClient`
  - Repository layer (`BriefingRepository` protocol, `OnlineBriefingRepository`, interface designed for caching)
  - JSON `Codable` API models under `Models/API`; cached as raw JSON files (prepares Phase 2)
  - Error handling and loading states (`LoadingState`)

- **Authentication**
  - Google OAuth via `ASWebAuthenticationSession` **plus** native Sign in with Apple (`SignInWithAppleButton` → credential exchange), both in `LoginView`
  - JWT in Keychain via `KeychainBearerTokenStore` (service `aero.flyfun.weather`)
  - `flyfunweather://auth?token=…` deep-link handling survives only as the **App Store reviewer** path — the normal sign-in completes inside `ASWebAuthenticationSession` and never reaches `onOpenURL`. `AppState.shouldAcceptDeepLinkToken` accepts an inbound bare token only when signed out AND the (unverified) `scope` claim is `"review"`; see the comment block on `handleAuthCallback` for the threat model
  - Session renewal is **server-driven** (`X-Renewed-Token` response header rolls the stored token forward), not a client-side expiry check; 401 clears the keychain and swaps back to `LoginView`

- **Flight list**
  - List user's flights from API
  - Flight card: route name, waypoints, departure time, assessment badge
  - Pull-to-refresh, navigate to briefing on tap

- **Briefing display** (read-only, all data from existing endpoints)
  - Advisory dashboard — severity badges per evaluator per model, expandable details
  - Digest summary — synopsis, synoptic, trend
  - Airport conditions — departure/arrival category, wind, visibility
  - Route map — MapKit SwiftUI Map, route line with waypoints, advisory coloring
  - Cross-section renderer — SwiftUI Canvas, incremental layer wave
  - Route graph — Swift Charts, scalar metrics along route
  - On-demand artifacts — Skew-T and GRAMET loaded as images (tap-to-load)

- **Layout** — iPhone + iPad adaptive. Shipped as `NavigationSplitView` (sidebar + detail on iPad, pushed stack on iPhone) with per-view `horizontalSizeClass` branches; the planned generic `DynamicStack` wrapper was never written.

### Cross-Section Renderer (Phase 1 — Incremental)

Was built incrementally; cross-section parity with web has largely shipped. Renderer uses the same cross-section data structure as web (TypeScript `extractVizData` ported to Swift; see `Models/Domain/VizData.swift`). Each layer conforms to the `CrossSectionLayerProtocol` and draws on a SwiftUI `Canvas`.

Shipped `CrossSectionLayerProtocol` conformers (`Views/CrossSection/Layers/`, 15):
- `TerrainLayer`, `TemperatureLinesLayer`, `StabilityLinesLayer`, `ReferenceLinesLayer`
- `NaturalCloudBandsLayer`, `SquareCloudBandsLayer`, `SoftCloudBandsLayer` (cloud struct names live in `CloudBandsNatural.swift`, not one-file-per-struct)
- `IcingBandsLayer`, `IcingOgimetNwpBandsLayer`, `SfipBandsLayer`
- `CATBandsLayer`, `InversionBandsLayer`
- `NwpConvectiveBgLayer`, `ThermoConvectiveBgLayer`
- `HighlightLayer` — not on the original plan; draws an advisory's highlight geometry (scrim + verdict ribbon) when a deep link arrives with a preset

`CrossSectionPresets` supplies the advisory "lenses" (which layers/model a given advisory opens with). Supporting render helpers: `BandRendering`, `ConvectiveTowerRendering`, and — one level up in `Views/CrossSection/` — `ColorScales`, `CoordTransform`. Skew-T detail is a separate on-demand view (`SkewTDetailView`, rendering RZSkewT's `SkewTView`).

## Phase 2 — Offline Briefing Viewer ✅ (offline resilience + refresh-complete push shipped; SwiftData never adopted)

**Goal**: Pilots sync briefing data before departure and view full briefing in flight without connectivity. Push notifications alert when briefings refresh.

**Server-side**: Build `/companion` lightweight payload endpoint. ❌ **Never built** — no `companion` route exists in `src/weatherbrief/api/`. The app consumes the ordinary briefing endpoints and caches their JSON, so the "companion payload" concept has no counterpart in shipped code. Don't go looking for it.

**App work**:

- **Companion sync consumption** ❌ — folded into plain repository calls (see above)
- **Offline storage** ✅ (shipped, not with SwiftData) — file-based `BriefingCacheStore` (JSON under Documents, scoped by the JWT's `sub`); `CachingBriefingRepository` wraps `OnlineBriefingRepository` (cache-first, fallback to API, cache on success). On-demand caching of Skew-T and GRAMET once viewed. Stale packs evicted on launch/foreground (`AppState`, ~1 week after departure)
- **Pre-flight sync** — landed *differently*: there is no per-flight "Sync for offline" button. Downloads are automatic (the old manual control was removed; its status now rides on the pack chip). `BriefingViewModel.syncLatestPack()` is the cheap seamless adopt-a-newer-pack path, distinct from a full user-triggered refresh
- **Push notifications** ✅ (shipped — `Services/PushNotifications.swift`, `AppDelegate`) — remote-notification registration, device-token upload to `api/devices.py`, refresh-complete push deep-links into the flight via `PushSupport.pendingNavigation(from:)`, silent content-available badge-sync. Server-side push off by default (`APNS_*` env + migration). See [Briefing Refresh Notifications](./ios-app-briefing-notifications.md)
- **Offline map tiles** ❌ **not built** — no MapKit tile caching anywhere in the app. The route map degrades to whatever iOS happens to have cached when offline. Still a live gap if in-flight map use matters

## Phase 3 — In-Flight PIREP System 🚧 (3a partially shipped in app)

**Goal**: The killer feature — pilots report weather during flight, two-way conversation. PIREPs stored locally, synced when online, shared in real-time with connectivity.

Three sub-phases, each delivering incremental value.

> Shipped in the app so far (3a subset): GPS tracking (`FlightTrackingService`, projects position onto the route), the report card (`PirepReportingView` + `PirepViewModel`, one-tap severity, no pre-selected values to avoid confirmation bias), an offline queue (`PirepOfflineStore`, per-user JSON file `pending_pireps.json`) that batch-syncs via the server's `POST /api/pireps/batch` with client UUIDs for dedup, and a per-flight PIREP tab (`PirepListView`). All shipped reports are flight-linked with `source = "inflight"`. Standalone filing, community feed/map, voice/Siri, prompting engine (3b), and live sharing (3c) are NOT in the app.
>
> Two gates to know before touching this: **filing is no longer restricted to the in-flight window** (a PIREP is a position report — the reporting sheet is reachable from the briefing at any time), and both filing and viewing are **server-side per-user permissions** (`pirep_can_publish` / `pirep_can_view`, mirrored onto `UserPreferences`). A publish-only account 403s on the list query, so the container skips `loadPireps()` rather than stranding the tab on an error.

### Phase 3a — PIREP Filing + Offline Sync

Pilots can file PIREPs in two contexts: during an active flight session (linked to a flight) or standalone (no flight required). Both share the same observation model, report UI, and sync engine.

**Server-side**: top-level observation endpoints (`POST/GET /api/observations`), flight session endpoints, observation DB storage.

**App work**:

- **Standalone PIREP filing** (no flight required)
  - "File PIREP" button on main screen — always available
  - Location: airport ICAO picker (RZFlight's `KnownAirports` for search/autocomplete) or current GPS
  - Simplified report sheet — flight rules, cloud base/tops, icing, turbulence, visibility, precipitation, free text
  - No forecast pre-population (no flight context) — fields start blank or at common defaults
  - Observation saved with `source = .standalone`, `flightId = nil`, `session = nil`
  - Syncs via same offline queue as in-flight observations

- **Flight session lifecycle** (partly shipped — as *tracking*, not as a session entity)
  - "Start Flight" is a briefing-toolbar action gated on `isInFlightWindow`; it calls `FlightTrackingService.start(routePoints:flightEndTime:)`. Stop is manual, plus auto-stop when landing is detected (low GS near destination) and a hard stop at `flightEndTime`
  - ❌ **No persisted session object** — there is no `session_id` anywhere in the app or on a PIREP. Tracking state is in-memory on the view's `@State` service. If a session entity is ever wanted, it's a genuinely new concept, not a rename of what exists

- **GPS tracking** (shipped — `FlightTrackingService`)
  - Core Location with `kCLLocationAccuracyBest` + `distanceFilter = 200` m (the planned `kCLLocationAccuracyKilometer` was dropped in favor of best accuracy throttled at the GPS level)
  - Background location mode (justified: PIREP location reporting)
  - Current position, altitude, GS, track
  - Route progress: projects GPS position onto the route (`ProjectedPosition`: along-route distance, cross-track distance, on-route within 10nm)

- **In-flight report UI** (shipped — `PirepReportingView` / `PirepViewModel`)
  - Report sheet reached from the briefing toolbar, the flight-list context menu, or the PIREPs tab's persistent "Report a PIREP" bar — **no flight-session or in-flight-window requirement** (deliberate: see the gates note above)
  - One-tap severity buttons, fields start **unset** (no forecast pre-population — deliberately avoids confirmation bias); GPS altitude auto-filled when vertical accuracy is valid
  - Smart field ordering (`PirepField`), optional free-text remarks
  - Saved with `source = "inflight"` (string, not the planned `.manual` enum)

- **Observation timeline** (partly shipped as the PIREPs tab)
  - `PirepListView` — per-flight list, expandable rows with severity bars, hazard icons, own-badge
  - ❌ No amend flow, and ❌ PIREPs are **not** drawn on the route map

- **Offline sync engine** (shipped — `PirepOfflineStore`, simpler than originally specced)
  - Unsent reports persisted to a JSON file (`pending_pireps.json`) via an `actor` queue
  - Flushed by `sync(using:)` after a submit (`PirepViewModel`) and on app foreground (`AppState.syncPendingPireps`, triggered from `WeatherBriefApp` scenePhase) — no `syncStatus` enum, no exponential backoff, and no 50-chunk batching yet. The app *does* have an `NWPathMonitor` (`Services/NetworkMonitor.swift`, used by the flight-list/briefing VMs), but the PIREP queue is **not** wired to it, so there is no auto-flush on connectivity restore
  - Idempotent: client UUIDs (`client_uuid`), server dedupes; entire queue POSTed via `submitPirepsBatch` → `POST /api/pireps/batch`, cleared on success

- **Voice PIREP (Siri shortcut)**
  - Register "FlyFun PIREP" App Shortcut via `AppShortcutsProvider` + `AppIntent`
  - `SFSpeechRecognizer` for on-device transcription (offline)
  - `PIREPParser` — regex/keyword extraction for altitude, icing, turbulence, cloud, visibility, precipitation, flight rules
  - Voice-extracted values populate same report card — highlighted to show voice-parsed vs forecast default
  - Saved with `source = .manual` (same as tap-based manual)

- **Community PIREP feed**
  - "PIREPs" tab on main screen (no flight required)
  - Map with recent shared PIREPs as severity-colored markers
  - Filter: airport ICAO, radius from current location, recency
  - Tap marker for details
  - When viewing a briefing, route-nearby PIREPs shown alongside route map
  - Data: `GET /api/observations?lat=&lon=&radius_nm=&since=`

- **Passive data collection** (in-flight only)
  - Track log — GPS breadcrumbs at 30–60s intervals
  - Route deviation detection — flag significant deviations from planned route

### Phase 3b — Proactive Prompting Engine

Watches forecast, tracks position, prompts at transition points with pre-populated observations. See [Sync & Prompting](./ios-app-sync-prompting.md) for the detailed engine spec.

**App work**:

- **Route progress tracker** — match GPS to nearest route point, look-ahead 5–15 min, transition detection
- **Trigger rules engine** — icing/IMC/convective/turbulence/cloud base/wind shear/periodic, with entry/exit/cooldown. Priority queue. Rate limiting (max one per 5 min)
- **Prompted report card UI** — compact non-modal card (slides in from side). Confirm (1 tap) / Edit (2 taps) / Deny (1 tap) / Dismiss (swipe). Auto-dismiss after 30s if no interaction
- **Smart suppression** — departure/arrival quiet zone (15nm), duplicate suppression, higher-priority trigger subsumes lower
- **Audio/haptic cue** — optional gentle chime/haptic on prompt appearance

### Phase 3c — Live PIREP Sharing

When online (Starlink, cellular), observations stream real-time; pilots receive nearby PIREPs from other flights.

**Server-side**: WebSocket endpoint, active session registry, spatial broadcast.

**App work**:

- **WebSocket connection**
  - Established when flight session starts and connectivity is available
  - Outbound: push observations on creation
  - Inbound: nearby PIREPs from other active flights
  - Graceful reconnection on connectivity changes
  - Fallback to REST polling if WebSocket unavailable

- **Nearby PIREP display**
  - Other pilots' PIREPs as markers on route map
  - Severity color (same scale as advisories)
  - Tap for details
  - Filter by recency and distance

- **Privacy controls**
  - Opt-in sharing: `is_shared` per observation
  - Default: shared (configurable in settings)
  - Position broadcasting consent at session start

## Future Phases (Not Scoped Yet)

Ideas from the original brainstorm, to be designed when Phase 3 is complete:

- **Forecast verification** — server-side comparison engine (forecast vs observations), per-model accuracy scoring, verification dashboard
- **Apple Watch** — quick-report complication (good/marginal/bad severity) for single-pilot ops
- **Turbulence from accelerometer** — passive detection via iPad accelerometer (academic precedent exists)
- **ForeFlight / SkyDemon integration** — URL scheme or share sheet for route import/export
- **PIREP format compliance** — standard PIREP string formatting for submission to official channels (FAA/EUROCONTROL)

## Decisions Made

- **Native SwiftUI** — no WKWebView for viz. Cross-section native via SwiftUI Canvas, route graph via Swift Charts. Modern SwiftUI only (iOS 18+).
- **No third-party heavyweights** — Apple-native SDKs only (URLSession, MapKit, Core Location), plus the author's own RZFlight and RZSkewT via SPM (RZUtils planned, never imported). Note: SwiftData was the original caching plan but was dropped for file-based caching (`BriefingCacheStore` + `FileManager`).
- **Google OAuth natively** — `ASWebAuthenticationSession`, no token-paste friction. Same flow as web.
- **Repository pattern from day one** — even Phase 1 uses repos, so Phase 2 adds caching without touching UI.
- **Subdirectory** — iOS project lives in flyfun-weather repo alongside server code.
- **3-phase roadmap** — (1) Online viewer → (2) Offline viewer → (3) PIREP system (3a manual + sync, 3b prompting, 3c live sharing).
- **Real-time architecture** — WebSocket during active flight sessions for bidirectional PIREP flow. APNS for background notifications. Simple in-memory spatial matching on server, upgrade to PostGIS/R-tree if scale demands.
- **Privacy** — PIREP sharing is opt-in (`is_shared`). Position broadcasting requires consent at session start.
- **PIREPs are first-class** — observations exist independently of flights. `flight_id` and `session_id` are optional. Pilots can file standalone PIREPs from the main screen. All shared PIREPs feed a community map.

## Open Questions

- ~~**Cross-section data documentation**~~ — **closed by the port itself**: `Models/Domain/VizData.swift` is now the Swift mirror of the web `extractVizData` output, and [visualization.md](./visualization.md) covers the shared data shape. The live risk has shifted from "undocumented" to "two hand-copied implementations drift" — run the `sync-ios-web` skill after touching either side.
- **Avionics integration** — G1000, Avidyne expose data ports (OAT, pressure altitude, winds aloft). Worth investigating for Phase 3+ but not essential.
- **iPhone vs iPad UX split** — app works on both, but in-flight PIREP UI is primarily for iPad. iPhone experience should focus on planning and notifications. Need to define which Phase 3 features are iPad-only vs universal.
- ~~**Flight creation in app**~~ — **answered yes**. `AddFlightView` / `AddFlightViewModel` do full create *and* edit, with route autocomplete, autorouter interpretation, aircraft + profile pickers, and a Move-vs-Duplicate choice for structural edits.
- ~~**Briefing refresh from app**~~ — **answered yes**. The app triggers refreshes (`POST /api/flights/{id}/packs/refresh?source=…`), streams progress over SSE, and exposes it via the briefing toolbar and `RefreshBriefingIntent`. Refresh availability is gated (cost confirm, subscriber rows).
- **Track log upload** — Phase 3a collects track logs. Upload for post-flight analysis, or local-only? Upload enables server-side route deviation analysis and verification.
- **PIREP format compliance** — format observations as standard PIREP strings for official channels? Deferred post-Phase 3.

## References

- [Overview](./ios-app-overview.md) — current implementation status
- [Architecture](./ios-app-architecture.md) — tech stack and layers
- [Features](./ios-app-features.md) — end-state feature set
- [Sync & Prompting](./ios-app-sync-prompting.md) — detailed engine specs
