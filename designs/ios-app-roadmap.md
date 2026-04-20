# iOS App — Implementation Roadmap

> 3-phase roadmap, decisions made, open questions

The app is built in three major phases, each delivering standalone value. The architecture (MVVM + Repository, SwiftData from day one) is designed so each phase extends the previous without rewrites.

The ultimate goal is the PIREP system (Phase 3) — the two-way weather conversation that makes this app uniquely valuable. Phases 1 and 2 build the foundation and deliver value along the way.

## Phase 1 — Online Briefing Viewer ✅

**Goal**: Native mobile briefing viewer that replaces checking the web on a phone. Pilot opens app, sees flights, taps one, views full briefing natively.

**Server-side**: Add `?platform=ios` to OAuth callback (redirect to custom URL scheme).

**App work**:

- **Foundation**
  - Xcode project with SwiftUI App lifecycle, SwiftData
  - SPM deps: RZFlight, RZUtils
  - API client (URLSession + async/await, JWT auth header)
  - Repository layer (online-only initially, interface designed for caching)
  - SwiftData models for flights and briefing data (cache, prepares Phase 2)
  - Error handling and loading states

- **Authentication**
  - Google OAuth via `ASWebAuthenticationSession`
  - `flyfunweather://auth/callback` URL scheme handling
  - JWT in Keychain (`CodableSecureStorage`)
  - Auto-refresh near expiry, re-auth on 401

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

- **Layout** — iPhone + iPad adaptive (DynamicStack). iPad: side-by-side. iPhone: stacked/tabbed.

### Cross-Section Renderer (Phase 1 — Incremental)

Web has ~16 layers across 8 groups. Native built incrementally starting with most valuable:

**Wave 1** (core — ship with these):
- Cloud coverage (filled regions by altitude)
- Icing severity (color-coded zones)
- Wind barbs (direction + speed at altitude)
- Temperature (isotherms or color fill)
- Terrain profile (elevation along route)

**Wave 2** (add iteratively):
- Humidity / relative humidity
- Vertical motion / CAT turbulence
- Convective indices

**Wave 3** (full parity):
- Pressure levels
- Visibility
- Remaining specialized layers

Renderer uses same cross-section data structure as web — TypeScript `extractVizData` ported to Swift. Each layer is a composable `CrossSectionLayer` protocol conformance on Canvas.

## Phase 2 — Offline Briefing Viewer ✅ (offline resilience done; push + SwiftData pending)

**Goal**: Pilots sync briefing data before departure and view full briefing in flight without connectivity. Push notifications alert when briefings refresh.

**Server-side**: Build `/companion` lightweight payload endpoint.

**App work**:

- **Companion sync consumption** — fetch lightweight payload, parse into models
- **Offline storage** — SwiftData persistence; `CachingBriefingRepository` wraps online repo (cache-first, fallback to API, cache on success). On-demand caching of Skew-T and GRAMET once viewed. Auto-expire old briefings, manual clear
- **Pre-flight sync** — per-flight "Sync for offline" button (downloads payload + map tiles). Sync status indicator. Background sync on Wi-Fi when departure is within configurable lead time
- **Push notifications** — APNS registration. Server-side push on auto-refresh with new pack. Notification tap opens updated briefing. Badge count for unread updates
- **Offline map tiles** — cache MapKit tiles along route corridor (±30nm, low-to-medium zoom) as part of pre-flight sync

## Phase 3 — In-Flight PIREP System 🚧 (M0+M1 done)

**Goal**: The killer feature — pilots report weather during flight, two-way conversation. PIREPs stored locally, synced when online, shared in real-time with connectivity.

Three sub-phases, each delivering incremental value.

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

- **Flight session lifecycle**
  - "Start Flight" transitions app from planning to flight mode
  - "End Flight" on landing (or auto-detect via prolonged GS = 0)
  - Session persisted with start/end times

- **GPS tracking**
  - Core Location with `kCLLocationAccuracyKilometer` (battery-friendly, sufficient for NWP grids)
  - Background location mode (justified: PIREP location reporting)
  - Current position, altitude, GS, track
  - Route progress: snap GPS position to nearest route point

- **In-flight report UI**
  - Persistent "Report" button during flight session
  - Full report sheet (all fields pre-populated from forecast)
  - "All correct" one-tap confirmation
  - Voice-to-text for optional free-text notes
  - Saved with `source = .manual`

- **Observation timeline**
  - Scrollable list of observations during the flight
  - Ability to amend (creates new linked observation)
  - Shown as pins on the route map

- **Offline sync engine**
  - Observations saved with `syncStatus = .local`
  - On connectivity (`NWPathMonitor`), batch POST queued to server
  - Idempotent: client UUIDs, server dedupes
  - Retry with exponential backoff
  - Batch in chunks of 50 if queue large
  - Mark `.synced` on success

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
- **No third-party heavyweights** — Apple-native SDKs only (URLSession, MapKit, SwiftData, Core Location). RZFlight + RZUtils via SPM.
- **Google OAuth natively** — `ASWebAuthenticationSession`, no token-paste friction. Same flow as web.
- **Repository pattern from day one** — even Phase 1 uses repos, so Phase 2 adds caching without touching UI.
- **Subdirectory** — iOS project lives in flyfun-weather repo alongside server code.
- **3-phase roadmap** — (1) Online viewer → (2) Offline viewer → (3) PIREP system (3a manual + sync, 3b prompting, 3c live sharing).
- **Real-time architecture** — WebSocket during active flight sessions for bidirectional PIREP flow. APNS for background notifications. Simple in-memory spatial matching on server, upgrade to PostGIS/R-tree if scale demands.
- **Privacy** — PIREP sharing is opt-in (`is_shared`). Position broadcasting requires consent at session start.
- **PIREPs are first-class** — observations exist independently of flights. `flight_id` and `session_id` are optional. Pilots can file standalone PIREPs from the main screen. All shared PIREPs feed a community map.

## Open Questions

- **Cross-section data documentation** — snapshot data format feeding the web cross-section renderer needs docs so the Swift extraction logic can be ported accurately. `extractVizData` TypeScript + I/O shapes are the key reference.
- **Avionics integration** — G1000, Avidyne expose data ports (OAT, pressure altitude, winds aloft). Worth investigating for Phase 3+ but not essential.
- **iPhone vs iPad UX split** — app works on both, but in-flight PIREP UI is primarily for iPad. iPhone experience should focus on planning and notifications. Need to define which Phase 3 features are iPad-only vs universal.
- **Flight creation in app** — Phase 1 is read-only. Should app allow creating flights directly? Route input UI complexity vs self-sufficiency.
- **Briefing refresh from app** — should app trigger refreshes, or only consume server-triggered? Endpoint exists (`POST /packs/refresh`); question is whether mobile UX should include this.
- **Track log upload** — Phase 3a collects track logs. Upload for post-flight analysis, or local-only? Upload enables server-side route deviation analysis and verification.
- **PIREP format compliance** — format observations as standard PIREP strings for official channels? Deferred post-Phase 3.

## References

- [Overview](./ios-app-overview.md) — current implementation status
- [Architecture](./ios-app-architecture.md) — tech stack and layers
- [Features](./ios-app-features.md) — end-state feature set
- [Sync & Prompting](./ios-app-sync-prompting.md) — detailed engine specs
