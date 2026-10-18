# WeatherBrief Android Companion App — Overview

> Native Android phone/tablet/foldable companion app (`app/android/`) for flight weather briefings, cross-section & Skew-T visualization, flight-day live observations, and in-flight GPS tracking

## Related Docs

- [Android Architecture](./android-app-architecture.md) — multi-module Gradle structure (`:app`, `:flyfun-common`, `:rzflight`, `:rzskewt`), MVVM + Repository, Compose Canvas engines, DTO conventions, emulator HTTPS setup
- [Server API](./ios-app-server-api.md) — shared REST + SSE contract consumed by both iOS and Android
- [Surface Charts & GRAMET](./briefing-charts-ios.md) — shared `PackChartsManifest` and 50 MB LRU chart cache
- [Live Observation Layer](./live-observation-layer.md) — flight-day `/live` overlay, nutshell, route ribbon, storms, and progress fixes
- [Cells Overlay](./cells-overlay.md) — radar cell contours, 30-min motion vectors, and suspect-echo filtering
- [Briefing Refresh Notifications](./ios-app-briefing-notifications.md) — APNs + FCM (`FcmPushSender`, migration `110`) push pipeline
- [Cross-Section Visualization](./visualization.md) · [Route Graph](./route-graph.md) · [Skew-T Canvas](./skewt-canvas.md)

## Implementation Status

All four implementation phases are built and verified against both offline fixtures (`FLYFUN_MOCK=1`) and the live local HTTPS backend (`https://localhost.ro-z.me:8443`):

- **Phase 0 (Server & CI)**: `device_tokens.platform` (`"ios"` | `"android"`, migration `110`), `FcmPushSender` in `src/weatherbrief/notify/push.py`, `/.well-known/assetlinks.json` in `deploy/weather.flyfun.aero.caddy`, and `.github/workflows/android.yml` + `android-ui-nightly.yml`.
- **Phase 1 (Core App & First-Party Libraries)**: `:flyfun-common`, `:rzflight`, Auth, Adaptive Flight List & Trips, Add/Edit Flight, Aircraft CRUD, Advisory & Discussion tabs (with Surface Charts + GRAMET PDF/PNG), Offline `/bundle` & 50 MB Chart Cache, What's New, and Feedback.
- **Phase 2 (Cross-Section, Route Graph & Skew-T)**: `:rzskewt` thermodynamics & transform library, 15+ layer `CrossSectionCanvas`, 3-mode `LayerBarView`, pixel-aligned `RouteGraphCanvas`, and interactive `SkewTCanvas` with dual-axis side panel and Compare Models mode.
- **Phase 3 & 4 (Maps, Observed Tab, In-Flight GPS, PIREPs & Push)**: Web Mercator `SlippyMapCanvas`, `RouteMapTabScreen`, `PanEuropeanMapScreen` (Forecast + Historical Replay), flight-day `ObservedTabScreen` with `RouteRibbonCanvas` and `RunwayWindDialView`, `FlightTrackingService` (foreground location service), `PirepOfflineStore`, and `PushNotificationManager`.

## What's Built

### Authentication (`ui/auth/LoginScreen.kt`, `:flyfun-common`)
- Driven by `FlyFunAuthService` (`aero.flyfun.common`):
  - **Sign in with Google**: Chrome Custom Tabs OAuth (`/auth/login/google?platform=android&state=<nonce>`) → `flyfunweather://auth/callback?code=...&state=...` → `POST /auth/exchange`.
  - **Sign in with Email**: magic-link request (`POST /auth/magic-link`) + code verification sheet.
  - **Dev Login**: `GET /auth/dev-token` in debug builds on the Android Emulator (`BuildConfig.DEBUG && ApiClient.isRunningOnEmulator()`), connected to `https://localhost.ro-z.me:8443` (real devices connect to `https://weather.flyfun.aero`).
  - **Demo Mode (`Try Demo Mode`)**: switches `AppStateViewModel` to `FixtureBriefingRepository` + `FixtureTripRepository` so the full app can be explored offline without a server.
- Session JWT is stored via `EncryptedSharedPreferences` (`AndroidTokenStore` in `MainActivity.kt`) and rotated automatically via `RollingBearerInterceptor` whenever the server returns `X-Renewed-Token`.
- Reviewer deep-link carve-out (`JwtClaims.shouldAcceptDeepLinkToken`) accepts a bare token in a deep link only when signed out and the token payload carries `"scope": "review"`.

### Flight List & Trips (`ui/flights/`, `ui/trips/`)
- **Adaptive two-pane layout** (`FlightListScreen.kt`): on tablets/foldables (`maxWidth >= 600.dp`), renders a persistent 340dp flight list pane alongside the detail `BriefingContainerScreen`; on phones, uses single-pane stack navigation with predictive back (`BackHandler`).
- **Flights vs. Trips toggle**: switches between duration-aware collapsible sections (`Future`, `Recent`, `Past`) and multi-leg `Trips` (`TripDetailScreen.kt`: conjunctive chain status, binding leg callout, Haiku trip summary, per-leg cards, and serial trip refresh).
- **Multi-select bulk delete**: long-pressing a flight row or choosing `Select & Delete Flights` from the top bar enters contextual multi-select mode (`POST /api/flights/bulk-delete`).
- **Top bar actions**: quick `Explore Maps` button (opens `PanEuropeanMapScreen`), `+` Add Flight FAB, and `More` menu (`My Aircraft`, `What's New` with unseen badge dot, `Settings`, `Send Feedback`, `Sign Out`).

### Add / Edit Flight & Aircraft (`ui/flights/AddFlightSheet.kt`, `ui/aircraft/`)
- Offline ICAO route autocomplete backed by `AirportDatabaseService` (`GET /api/nav/airports-db` with ETag caching) and `:rzflight`'s `KnownAirports.rankedSearch`.
- Quarter-hour `FlightDuration` picker, cruise altitude & ceiling inputs with live aircraft service-ceiling warning (`AddFlightViewModel.ceilingWarning`, #777), Autorouter route import, and structural edit detection (prompting **Move** vs. **Duplicate** when route or date changes).
- Full aircraft registry CRUD (`AircraftListScreen.kt`, `AircraftFormSheet.kt`) with Make Default and 409 last-aircraft deletion guard.

### Briefing Viewer (`ui/briefing/BriefingContainerScreen.kt`)
- **Leading Pack Toolbar (`BriefingPackToolbar.kt`)**: displays freshness dot, `D-N` label, assessment badge, and offline cache icon; dropdown switches historical packs, triggers `Download for Offline` (`/bundle`), or starts an SSE streaming `Refresh Briefing` (`/packs/refresh`).
- **5-Tab Navigation (`BriefingTab.tabs(showsObserved)`)**:
  1. **Advisory (`AdvisoryTabScreen.kt`)**: sticky `SectionSpyBar`, accented Verdict Hero card, flight-day `ObservedTeaserRow`, digest altitude warning, digest 👍/👎 feedback, post-flight `DebriefCard`, responsive `AMBER`/`RED` `AdvisoryCard`s with `FocusIntent` deep links (`View on Cross-Section ›`, `View on Map ›`), collapsible `GreenAdvisoryStrip`, departure/destination `AirportConditionsSection`, EASA/FAA `AlternatesSection`, and `TimingScenariosSection`.
  2. **Discussion (`DiscussionTabScreen.kt`)**: sticky `SectionSpyBar` (`Synoptic`, `Charts`, `Concerns`, `Trend`, `Watch`, `GRAMET`), `MarkdownLiteText` sections, `SurfaceChartsSection.kt` (DWD / Met Office / TEMSI source & time chips + Compose `Canvas` route overlay + pinch-to-zoom dialog), and `GrametSection.kt` (renders PDF via Android's native `PdfRenderer` or PNG via `BitmapFactory`).
  3. **Cross-Section (`ui/crosssection/CrossSectionTabScreen.kt`)**: model selector chips, 3-mode `LayerBarView.kt` (`Focus` presets × `Emulate` modes × 4 themes + 5 expandable `LayerFamily` pills), cockpit `CrossSectionReadoutBar.kt`, 15+ layer `CrossSectionCanvas.kt` with directional scrub-vs-scroll gesture lock, pixel-aligned `RouteGraphCanvas.kt` (14 metrics, dual Y-axes, `AboveScale` chevrons), and inline `SkewTDetailSection.kt` (`SkewTCanvas.kt` with CAPE/CIN fill, parcel path, multi-model compare mode, 14 side-panel variables, and bidirectional altitude hover sync).
  4. **Map (`ui/map/RouteMapTabScreen.kt`)**: interactive Web Mercator `SlippyMapCanvas.kt` with OpenStreetMap raster tiles, 13 route segment metrics, altitude slider, wind barbs, Hewson fronts, SIGMET polygons, OPERA radar / IR satellite tile overlays, storm cell contours (`20/35/41 dBZ` + 30-min motion vectors), age-faded lightning bolts (`⚡`), alternate airport detour lines, and tap inspector with `Open Skew-T at this point ›`.
  5. **Observed (`ui/observed/ObservedTabScreen.kt`)**: appended on the right on flight day (`hasObservedContent`) and auto-selected once when opening a flight inside the live window (`departure − 3h … arrival + 1h`). Renders the live status & GPS tracking bar, `ObservedHighlightCard` (Haiku alert lines + 👍/👎 rating), `ObservedNutshellCard` + interactive 4-lane `RouteRibbonCanvas` (Radar VIP, Lightning, SIGMETs, Stations + aircraft progress marker), `LiveChangesSection` (with expandable history trails), `LiveStormsSection` (with 30-min off-track distance sparkline), `RouteObservationsSection` (with `obs_proxy` badges and Compose `Canvas` `RunwayWindDialView` compass/runway dials), and `RouteSigmetsSection`.

### Pan-European Forecast & Historical Maps (`ui/map/PanEuropeanMapScreen.kt`)
- Accessible from the Flight List top bar (`Explore Maps`) or `/maps.html` deep links.
- Switches between **Forecast Map** (`D-0`..`D-6`, UTC hours, model selector, 10 airport metrics from `HelpCatalogStore`, tappable `ForecastAirportCard` bottom sheet) and **Historical Replay** (past UTC date + 30-min slider, lead selector, `HistoricalAirportCard` comparing actual METAR vs. TAF vs. archived model forecast).

### In-Flight GPS Service, Offline PIREPs & Push (`service/`, `data/PirepOfflineStore.kt`)
- **`FlightTrackingService.kt`**: Android `foregroundServiceType="location"` service (`START_STICKY`) with compact ongoing notification, projecting GPS fixes onto the active route via `LivePositionRules.projectOntoRoute` and posting sparse updates (`POST /api/flights/{id}/live/position` when $\Delta t \ge 3\text{ min}$, $\Delta d \ge 8\text{ NM}$, or $\Delta alt \ge 800\text{ ft}$).
- **`PirepOfflineStore.kt` & `PirepReportingSheet.kt`**: coroutine-safe JSON queue (`pending_pireps.json`) with European airspace bounds validation and idempotent `client_uuid` flush (`PirepFeature.isEnabled = false` by default, matching iOS #661).
- **`PushNotificationManager.kt`**: Android 13+ `POST_NOTIFICATIONS` permission handling, `POST /api/devices` registration with `platform = "android"`, foreground banner suppression when watching the refreshed flight, and tap deep-linking via `PendingNavigation`.

## References

- Architecture & module details: [android-app-architecture.md](./android-app-architecture.md)
- Key code paths: `app/android/app/`, `app/android/flyfun-common/`, `app/android/rzflight/`, `app/android/rzskewt/`
- Implementation plan: [plans/android-app.md](../plans/android-app.md)
