# Native Android App: Scope, Architecture, UX & Full-Parity Plan

> **Status: design & roadmap. Updated 2026-10-18.**
>
> Full-parity plan for replicating the iOS companion app (`app/flyfun-weather/`) as a native Android client (`app/android/`), covering the complete Oct 2026 feature set, the multi-language strategy for first-party libraries (`flyfun-common`, `rzflight`, `rzskewt`), native Android / Material 3 cockpit UX, verification oracles, and a 4-phase execution roadmap.

## Related Docs

- [iOS Overview](../ios-app-overview.md) — as-built feature baseline
- [iOS Architecture](../ios-app-architecture.md) — MVVM + Repository, auth flow, library reuse
- [iOS Data Models](../ios-app-data-models.md) — API DTOs, `Viz*` domain structs, JSON-on-disk caches
- [iOS UI](../ios-app-ui.md) — cockpit constraints, 5-tab layout, cross-section layer bar
- [iOS Server API](../ios-app-server-api.md) — REST + SSE contract consumed by mobile clients
- [Briefing Charts on iOS](../briefing-charts-ios.md) — surface charts manifest + GRAMET caching (#799)
- [Live Observation Layer](../live-observation-layer.md) — flight-day `/live`, nutshell, ribbon, runway/wind dials, GPS progress (#637–#790)

---

## 1. Current iOS Scope (Oct 2026 Baseline)

The iOS app is a full-featured, offline-first native client—not a thin web wrapper.

- **Main app codebase**: **~51,500 lines of Swift** across 228 files (`app/flyfun-weather/flyfun-weather/`)
- **Test suites**: **~17,300 lines of Swift** across 56 unit test files (`flyfun-weatherTests/`) and 6 UI test suites (`flyfun-weatherUITests/`)

### 1.1 Module Breakdown

| Area | iOS Size | Scope & Features |
| :--- | :---: | :--- |
| **Views — Briefing & Observed** (`Views/Briefing/`) | ~8.8k LOC | 5-tab briefing viewer (`Advisory`, `Discussion`, `Cross-Section`, `Map`, plus flight-day `Observed`), live changes trail, route ribbon, METAR/TAF, runway/wind dials, SIGMETs, surface charts + route overlay, PDF GRAMET viewer, weather alternates, timing scenarios, post-flight debrief, PIREPs. |
| **Views — Cross-Section & Route Graph** (`Views/CrossSection/`, `Views/RouteGraph/`) | ~7.7k LOC | Immediate-mode 2D Canvas cross-section renderer (soft/natural clouds, Ogimet/SFIP icing, CAT, inversions, convective towers, terrain, live METAR/SIGMET columns, advisory highlight scrims), 3-mode responsive layer family/lens/emulation bar, synchronized dual-axis route graph, embedded Skew-T. |
| **Views — Maps** (`Views/Map/`, `Views/ForecastMap/`) | ~5.2k LOC | Briefing route map + pan-European Forecast & Historical maps (~620 airport markers), custom raster tile overlays (OPERA radar over EUMETView IR satellite), age-faded lightning bolt glyphs, radar cell contours + motion vectors, live aircraft marker. |
| **Views — Flights, Trips, Aircraft, Auth, Shared** (`Views/Flights/`, `Trips/`, `Aircraft/`, `Auth/`, `Help/`, `Shared/`) | ~6.0k LOC | Flight CRUD, multi-leg Trips feasibility chains, ICAO route autocomplete, Autorouter in-app OAuth linking, aircraft registry CRUD, multi-select bulk delete, shared-flight preview, What's New stream, `MarkdownLite`, scroll-spy bars. |
| **Models & Pure Rules** (`Models/API/`, `Models/Domain/`) | ~6.9k LOC | 1:1 API DTOs, `Viz*` domain rendering structs, pure mirrored rule engines (`RouteRibbonRules`, `RunwayWindRules`, `CellsOverlay`, `LivePositionRules`). |
| **ViewModels** (`ViewModels/`) | ~6.8k LOC | Screen state, SSE refresh progress, D-0 live-layer polling, cross-tab `FocusIntent` deep-linking, route autocomplete. |
| **Services & Offline Cache** (`Services/`) | ~6.1k LOC | `BriefingRepository` (Online / Caching / Fixture), gzipped `/bundle` streaming download, LRU + mark-and-sweep chart cache, `FlightTrackingService` (GPS), `LivePositionService`, `PirepOfflineStore`, `AirportDatabase`. |
| **System Surfaces** (`App/`, `AppIntents/`, `Tips/`) | ~2.5k LOC | Siri / App Intents, Spotlight indexing, APNs push + server-derived badge sync, Universal Links. |

---

## 2. First-Party Library Strategy (`flyfun-common`, `rzflight`, `rzskewt`)

The iOS app links three first-party Swift packages (`RZUtils` is linked in `Package.resolved` but unused in source). Instead of rewriting everything monolithic inside `flyfun-weather`, we should add a **`kotlin/` directory** to each of those three repositories, matching how `rzflight` and `flyfun-common` already house **Python + Swift** side by side.

### 2.1 Actual Surface Used by `flyfun-weather`

| Repository | Current Languages | Exact Surface Consumed by `flyfun-weather` | Kotlin Port Scope |
| :--- | :--- | :--- | :--- |
| **`flyfun-common`** (`roznet/flyfun-common`) | Python + Swift | • `FlyFunAuthService` (Google OAuth state/code flow via `/auth/exchange`, email magic-link)<br>• `RollingBearerSession` (attaches `Bearer` JWT, updates from `X-Renewed-Token` response header, triggers `onUnauthorized` on `401`)<br>• `KeychainBearerTokenStore` | **Small (~500 LOC)**.<br>OkHttp `Interceptor` for rolling bearer tokens, Jetpack `EncryptedSharedPreferences` / Tink + Android Keystore for storage, Chrome Custom Tabs (`androidx.browser`) + App Links for `/auth/exchange`. |
| **`rzflight`** (`roznet/rzflight`) | Python (`euro_aip`) + Swift (`RZFlight`) | • `AirportDatabase.swift`: `KnownAirports(db:)` (`rankedSearch(needle:limit:)`, `airport(icao:ensureRunway: false)`) over the downloaded `airports.db` SQLite<br>• `FlightTrackingService.swift`: `RouteGeometry.directDistanceNm` and `RouteGeometry.perpendicularDistanceAndRatio` | **Very small slice (~400 LOC)**.<br>Only needs `Airport`, `KnownAirports` (reading the slim `airports.db` via Android `SQLiteDatabase`), and `RouteGeometry` (pure spherical trig). Can expand later if other FlyFun apps need full AIP/runway hydration. |
| **`rzskewt`** (`roznet/rzskewt`) | Swift (`RZSkewT`) | • `SkewTDetailView.swift` & `SkewTVariableCatalog.swift`: `SoundingProfile`, `SoundingLevel`, `SkewTIndices`, `SkewTOverlays`, `SkewTConfiguration`, `Thermodynamics`, `SkewTView`, `SkewTVariablePanel` | **Medium (~1,800 LOC)**.<br>Pure thermodynamic math + Jetpack Compose `Canvas` plot and side-panel renderer, verified against the existing 47 unit tests. |

### 2.2 How Kotlin Packages Fit into Multi-Language Repos

While Swift Package Manager requires `Package.swift` at the repository root, Gradle works cleanly from a `kotlin/` subdirectory (with an optional root `jitpack.yml` pointing into `kotlin/`).

```text
rzflight/
├── Package.swift                 # Root manifest for Xcode / SPM
├── pyproject.toml                # Python package (euro_aip)
├── python/                       # Python sources & tests
├── Sources/RZFlight/             # Swift sources
├── Tests/RZFlightTests/          # Swift tests
├── shared-fixtures/              # Shared test fixtures (sample airports.db, geometry JSON cases)
└── kotlin/                       # Kotlin / Android library project
    ├── settings.gradle.kts       # rootProject.name = "rzflight"
    ├── build.gradle.kts          # com.android.library / kotlin.jvm + maven-publish
    └── rzflight/
        └── src/
            ├── main/kotlin/aero/flyfun/rzflight/
            │   ├── Airport.kt
            │   ├── KnownAirports.kt
            │   └── RouteGeometry.kt
            └── test/kotlin/aero/flyfun/rzflight/
                ├── KnownAirportsTest.kt
                └── RouteGeometryTest.kt
```

### 2.3 Consuming First-Party Kotlin Libraries (The SPM Equivalents)

1. **Remote consumption via JitPack or GitHub Packages**:
   - With the Gradle `maven-publish` plugin, JitPack (`https://jitpack.io`) builds public GitHub repos on demand from Git tags or commit hashes (`implementation("com.github.roznet:rzflight:1.0.0")`)—the closest 1:1 equivalent to SPM Git URLs in `Package.resolved`.
2. **Local side-by-side development via Gradle Composite Builds**:
   - In `app/android/settings.gradle.kts`, adding `includeBuild("../../../rzflight/kotlin")` (gated on directory existence or a local gradle property) substitutes the remote Maven coordinates with the local source folder automatically—identical to dragging a local Swift package into Xcode.
3. **Shared Cross-Language Test Fixtures**:
   - Python (`pytest`), Swift (`swift test`), and Kotlin (`./gradlew test`) can all read the same JSON/SQLite test fixtures inside each library repo, guaranteeing mathematical and schema parity across backend, iOS, and Android.

---

## 3. Technical Difficulty & Stack Mapping

**Overall difficulty: Medium-High, but roughly 60–70% of the effort of the original iOS build** because the server API, offline caching model, edge cases, and pure domain rules are already designed and tested.

### 3.1 Stack Mapping (iOS → Android)

| Layer | iOS (`app/flyfun-weather/`) | Android (`app/android/`) | Parity Notes |
| :--- | :--- | :--- | :--- |
| **Min OS** | iOS 26.2 | **API 31 (Android 12+)** | Avoids legacy compat shims; modern Compose, runtime permissions, and native splash/blur APIs. |
| **UI Framework** | SwiftUI | **Jetpack Compose + Material 3** | Direct declarative 1:1 counterpart (`View` ↔ `@Composable`). |
| **State & MVVM** | `@Observable` + `@MainActor` | **AndroidX `ViewModel` + `StateFlow`** | Coroutines `viewModelScope` replaces Swift `Task`. |
| **Networking & SSE** | `URLSession` + `URLSession.bytes` | **OkHttp + `okhttp-sse` (or Ktor)** | Handles REST, streaming byte progress for `/bundle`, and SSE for `/packs/refresh`. |
| **JSON Serialization** | `Codable` (`Models/API/*.swift`) | **`kotlinx.serialization` (`@Serializable`)** | Compile-time codegen, no reflection overhead. |
| **On-Disk Cache** | `BriefingCacheStore` (`actor`, JSON files) | **Coroutine `Mutex` / IO dispatcher + JSON files** | The iOS app avoided SwiftData/CoreData and uses a file tree (`BriefingCache/users/<scope>/<flightId>/<ts>/<endpoint>.json`). This ports 1:1. |
| **Preferences** | `UserDefaults` | **Jetpack DataStore (Preferences)** | Typed, coroutine-safe key-value persistence. |
| **Cross-Section & Skew-T** | SwiftUI `Canvas` (`GraphicsContext`) | **Compose `Canvas` (`DrawScope`)** | Both are immediate-mode 2D canvases; paths, gradients, clipping, and text measurement map directly. |
| **Route Graph** | `Swift Charts` | **Custom Compose `Canvas`** | Only ~770 lines on iOS. Drawing directly on Compose `Canvas` (instead of a 3rd-party chart library) shares the exact X-axis transform with the Cross-Section above it and guarantees lag-free scrub sync. |
| **Maps** | `MKMapView` (`UIViewRepresentable`) | **Google Maps SDK (`maps-compose`) or MapLibre** | Must use bitmap caching for the ~620 forecast airport markers, `TileOverlay` for OPERA radar/IR, and custom overlays for lightning & storm cells. |
| **PDF GRAMET** | `PDFKit` | **`android.graphics.pdf.PdfRenderer` / `androidx.pdf`** | Renders single-page GRAMET PDFs with pinch-to-zoom, falling back to `gramet.png` for legacy packs. |
| **Push Notifications** | APNs (`UNUserNotificationCenter`) | **Firebase Cloud Messaging (FCM)** | Requires server-side FCM sender alongside APNs (§5). |
| **Voice / Shortcuts** | `AppIntents` + `FoundationModels` | **Android App Actions (`shortcuts.xml`)** | Android lacks an exact on-device equivalent to iOS 26 `FoundationModels` intent phrase resolution; scope to standard shortcuts or an in-app voice action. |

### 3.2 Where the Real Engineering Effort Sits

1. **🔴 Tier 1 — High Effort (Custom Visuals & Maps)**
   - **Cross-Section Canvas (`~7k LOC`) & `RZSkewT`**: Translating the 10+ atmospheric layers (soft/natural clouds, SFIP/Ogimet icing, CAT, inversions, convective towers, terrain, live METAR/SIGMET columns, advisory highlight scrims) and theme/emulation resolution.
   - **Map Overlays (`~5.2k LOC`)**: Keeping 60fps with ~620 custom-drawn airport forecast markers, live OPERA radar + EUMETView satellite IR raster tiles, age-faded lightning bolts, and storm cell contours/vectors.
   - **Scrub vs. Scroll Gesture Disambiguation**: Replicating `ScrubPanGesture` in Compose `pointerInput` so horizontal drags scrub the cross-section cursor, hold-then-drag scrubs in any direction, and vertical drags pass through cleanly to the parent vertical scroll.

2. **🟡 Tier 2 — Medium Effort (Platform & OS Integration)**
   - **OAuth & Deep Links**: Chrome Custom Tabs + Android App Links (`assetlinks.json`) adhering strictly to the `state` nonce + `/auth/exchange` flow and App Store/Play Store reviewer token rules.
   - **In-Flight GPS Tracking**: Android aggressively kills background location unless backed by a **Foreground Service (`foregroundServiceType="location"`)** with a persistent notification.
   - **FCM Push & Badge Reconciliation**: Wiring Firebase Messaging, foreground suppression, and deep-linking on notification tap.

3. **🟢 Tier 3 — Straightforward (Mechanical 1:1 Port)**
   - **DTOs, Pure Rules & Repositories (`~20k LOC`)**: `Models/API/`, `RouteRibbonRules`, `RunwayWindRules`, `CellsOverlay`, `LivePositionRules`, `BriefingCacheStore`, `PirepOfflineStore`, `HelpCatalogStore`, and `WhatsNewStore` have zero UI/OS coupling and port almost mechanically from Swift to Kotlin with their existing unit test suites.

---

## 4. Native Android UX & Material 3 Cockpit Design

To feel like a first-class Android app across phones, foldables (Pixel Fold / Galaxy Z Fold), and cockpit tablets (Galaxy Tab Active), the UI should adopt Material 3 Adaptive conventions while preserving cockpit safety constraints:

### 4.1 Adaptive Layouts (`ListDetailPaneScaffold` & `NavigationSuiteScaffold`)
- **Flight List + Briefing Split View**: Replace iOS `NavigationSplitView` with Material 3 `ListDetailPaneScaffold`. On compact phones it pushes full-screen; on tablets and unfolded foldables it renders a two-pane list-detail layout automatically.
- **5-Tab Briefing Navigation**:
  - Keep the exact tab order and flight-day rules from iOS (`Advisory`, `Discussion`, `Cross-Section`, `Map`, with `Observed` appended at index 4 on flight day and auto-selected once when entering the live window).
  - On phones in portrait, render a bottom Material 3 `NavigationBar` (5 items is the M3 maximum for bottom bars—matching iOS's 5-tab limit before "More").
  - On tablets / landscape, use `PrimaryScrollableTabRow` at the top of the detail pane or a compact side `NavigationRail`.

### 4.2 Predictive Back Gesture (`BackHandler`)
- Android users rely heavily on the system back swipe (left/right edge).
- Wire `BackHandler` across multi-state screens so swiping back:
  1. Closes an expanded cross-section family detail row or active `FocusIntent` lens before leaving the tab.
  2. Exits fullscreen chart/GRAMET zoom before popping the briefing.
  3. Exits multi-select bulk-delete mode on the flight list before closing the app.

### 4.3 Cockpit-Safe Controls & Replacing iOS Idioms
- **Flight Row Actions & Bulk Delete**: Avoid hair-trigger swipe-to-delete on flight rows (error-prone in turbulence). Use a trailing `⋮` icon button (`DropdownMenu`: Edit, Share, Unsubscribe, Delete) plus standard Android **long-press multi-select** with a contextual TopAppBar ("3 selected · Delete") instead of iOS's separate modal selection sheet.
- **Intra-Tab Scroll-Spy (`SectionSpyBar`)**: Implement as a Compose `LazyColumn` `stickyHeader` containing horizontally scrollable `FilterChip` pills synced with `LazyListState.firstVisibleItemIndex`.
- **Disable Material You Dynamic Color on Weather Semantics**: Do **not** let Android 12+ wallpaper-based `dynamicDarkColorScheme` / `dynamicLightColorScheme` alter safety-critical colors. Lock `Assessment` (`GREEN` / `AMBER` / `RED`), VIP radar reflectivity ramps, and cross-section theme palettes to the exact hex values in `cross-section-themes.json` and `metrics-catalog.json`.
- **Foreground Service Notification for "Start Flight"**: When the pilot taps "Start Flight", start a location Foreground Service showing an ongoing compact notification (*"Tracking LFAT → LFMD · 85 NM along · FL065"* with a *"Stop"* action button). This prevents Android OEM battery savers from dropping GPS fixes when the pilot switches to SkyDemon/Garmin Pilot and gives instant glanceability from the lock screen or notification shade.

---

## 5. Server & Repository Changes Required

### 5.1 Backend Additions (`src/weatherbrief/`)
95% of the FastAPI server works out of the box. Only four small additions are needed:
1. **Device Tokens Migration (`api/devices.py` + Alembic)**:
   - Add a `platform` column (`"ios"` | `"android"`, default `"ios"`) to `device_tokens` using `batch_alter_table` (per `designs/migrations.md`).
2. **FCM Push Transport (`notify/push.py` & `notify/dispatch.py`)**:
   - Add an async FCM HTTP v1 sender (using service-account JWT via `httpx`, matching the lightweight dependency-free style of the existing APNs sender) and route tokens in `dispatch.py` by `platform`.
3. **OAuth Platform Handling (`flyfun-common` / auth routes)**:
   - Support `?platform=android` on `/auth/login/google` and `/autorouter/link-ticket`, redirecting to `flyfunweather://auth/callback` / `flyfunweather://autorouter/callback` (or verified HTTPS App Links) with the `state` + `code` exchange flow.
4. **Android Digital Asset Links (`deploy/weather.flyfun.aero.caddy`)**:
   - Serve `/.well-known/assetlinks.json` alongside `/.well-known/apple-app-site-association` so `/briefing.html`, `/maps.html`, `/s/{code}`, and `/t/{code}` open directly in the Android app.

### 5.2 CI & Repo Hygiene (Do Before Adding `app/android/`)
- **Narrow `.github/workflows/ios.yml` and `ios-ui-nightly.yml` path filters**:
  - Currently both trigger on `app/**`. Narrow them to `app/flyfun-weather/**` before creating `app/android/` so Android commits don't spin up expensive `macos-26` Xcode runners.
- **Extend `sync-ios-web` into a 3-way parity check**:
  - Add `app/android/app/src/main/assets/metrics-catalog.json` and `cross-section-themes.json` to the parity check so catalog updates never leave Android behind.

---

## 6. Verification & Testing Strategy ("Build the Oracle First")

An agentic workflow moves fast when it has a fast, truthful automated oracle. Unlike iOS (which requires `macos` runners and `xcodebuild` simulator boot), **Android unit and screenshot tests run hermetically on standard `ubuntu-latest` JVM in seconds**.

1. **JVM Unit Tests (`./gradlew testDebugUnitTest`) + Vacuous-Pass Guard**:
   - Runs all DTO serialization tests (against captured server JSON fixtures), `BriefingCacheStore` tests, and pure rule tests (`RouteRibbonRulesTest`, `RunwayWindRulesTest`, `CellsOverlayTest`, `LivePositionRulesTest`, `FlightGroupingTest`).
2. **`FixtureBriefingRepository` (`FLYFUN_MOCK=1` Equivalent)**:
   - Port the iOS fixture repository using the **exact same JSON fixture files** so every UI state (D-0 live observed, multi-leg trips, stale packs, offline read-only banner) can be rendered deterministically without a backend.
3. **JVM Screenshot Testing via Roborazzi (`@GraphicsMode(Mode.NATIVE)`)**:
   - Renders Jetpack Compose screens **and custom Compose `Canvas` views (Cross-Section, Route Graph, Skew-T, Runway Wind Dials)** directly on the JVM into PNGs.
   - Gives an immediate visual oracle for the Cross-Section and Skew-T renderers that can be compared side-by-side against the iOS/Web reference renders during development.
4. **Nightly Instrumented Emulator Journeys (`android-ui-nightly.yml`)**:
   - Mirrors `ios-ui-nightly.yml`: runs Compose UI end-to-end tests (`src/androidTest/`) against `FixtureBriefingRepository` on a nightly schedule rather than blocking PR merges on emulator boot flake.

---

## 7. Phased Execution Plan

### Phase 0: Repo Prep, Server Plumbing & First-Party Kotlin Slices
- [ ] Narrow `ios.yml` and `ios-ui-nightly.yml` triggers from `app/**` to `app/flyfun-weather/**`.
- [ ] Server: Add `platform` to `device_tokens` (Alembic migration with `batch_alter_table`), FCM HTTP v1 sender in `notify/push.py`, `?platform=android` auth callback support, and `/.well-known/assetlinks.json`.
- [ ] Create `kotlin/` library module in **`flyfun-common`** (`FlyFunAuthService`, `RollingBearerInterceptor`, `TokenStore`).
- [ ] Create `kotlin/` library module in **`rzflight`** (`Airport`, `KnownAirports` SQLite reader, `RouteGeometry` + shared unit tests).
- [ ] Bootstrap `app/android/` Gradle project (`minSdk = 31`, Kotlin 2.x, Compose BOM, `kotlinx.serialization`, OkHttp, Roborazzi) and `.github/workflows/android.yml` on `ubuntu-latest`.

### Phase 1: Core Data Layer, Auth, Flights, Trips & Text Briefing Tabs
- [ ] Port `Models/API/*.kt` DTOs and round-trip serialization tests against captured server JSONs.
- [ ] Implement `BriefingRepository` (`OnlineBriefingRepository`, `CachingBriefingRepository` with `BriefingCacheStore` JSON file tree, `/bundle` streaming download, and `FixtureBriefingRepository`).
- [ ] Implement `HelpCatalogStore`, `WhatsNewStore`, `AirportDatabase` (ETag download + `rzflight` `KnownAirports`), and DataStore preferences.
- [ ] Build Auth screens (Google Sign-In / Chrome Custom Tabs OAuth exchange, email magic-link, dev login for emulator).
- [ ] Build Adaptive Flight List (`ListDetailPaneScaffold`), Trips chain view (`Views/Trips`), Add/Edit Flight sheet (with ICAO autocomplete, Autorouter OAuth link, Move vs. Duplicate, aircraft & profile pickers), Aircraft Registry CRUD (`Settings → My Aircraft`), Shared Flight preview (`/s/{code}`), and bulk delete.
- [ ] Build `BriefingContainerScreen` with `AdvisoryTab` (verdict hero, advisory grid, green strip, airport conditions, alternates, timing scenarios, debrief card, scroll-spy) and `DiscussionTab` (LLM digest sections, Surface Charts manifest + route Canvas overlay + zoom viewer, PDF GRAMET viewer, chart LRU/mark-and-sweep cache).

### Phase 2: Custom Canvas Visualizations (`Cross-Section`, `Route Graph`, `RZSkewT`)
- [ ] Port `Models/Domain/VizData.kt`, `extractVizData`, `CrossSectionThemes`, and `CrossSectionPresets` (Focus lenses × Emulate GRAMET/Windy/ForeFlight/FlyFun).
- [ ] Build Compose `Canvas` **Cross-Section Renderer** and all layer renderers (soft/natural clouds, NWP/Ogimet/SFIP icing, CAT, inversions, thermo/NWP convective towers, terrain, reference/temperature lines, `CurrentConditionsLayer`, and `HighlightLayer` scrims).
- [ ] Build the 3-mode responsive layer family bar (phone portrait chips below chart, tablet full bar above chart, phone landscape immersive mode) and directional scrub-vs-scroll gesture handler.
- [ ] Build Compose `Canvas` **Route Graph** (`RouteGraphView` + 14 scalar metrics registry) with synchronized scrub cursor.
- [ ] Port **`rzskewt` (`kotlin/`)**: atmospheric thermodynamics, `SkewTView` Compose Canvas, `SkewTVariablePanel`, and `SkewTDetailView` folded below the cross-section. Verify with the 47 thermodynamic unit tests + Roborazzi golden PNGs.

### Phase 3: Maps, Flight-Day `Observed` Tab, Live Tracking & PIREPs
- [ ] Build **Briefing Route Map**, **Pan-European Forecast Map**, and **Historical Map** (`maps-compose` / MapLibre) with bitmap-cached ~620 airport markers, metric pickers, altitude slider, and day/hour/lead selectors.
- [ ] Port pure rule engines (`RouteRibbonRules`, `RunwayWindRules`, `CellsOverlay`, `LivePositionRules`) with 1:1 unit tests matching Swift and TypeScript.
- [ ] Add Map Observed overlays: OPERA radar + EUMETView IR satellite `TileOverlay`, age-faded lightning bolt glyphs, and radar cell contours + motion arrows (`RouteCellsModel`).
- [ ] Build the flight-day **`ObservedTab`**: Haiku highlight + 👍/👎 rating, nutshell glance card, interactive route ribbon, departure/destination runway + wind dials, *"Since this briefing"* live changes + history trails, radar storms list, METAR/TAF with observation proxies, and route SIGMETs.
- [ ] Implement **In-Flight GPS Tracking (`FlightTrackingService`)** as an Android Foreground Service (`foregroundServiceType="location"`) with ongoing notification, route projection, live aircraft icon on Map & Cross-Section, and sparse GPS fix reporting (`LivePositionService` → `POST /flights/{id}/live/position`).
- [ ] Port `PirepOfflineStore` and `PirepReportingSheet` (behind `PirepFeature.isEnabled`).

### Phase 4: Push Notifications, Deep Links, Polish & Play Store Release
- [ ] Wire `FirebaseMessagingService` for briefing-refresh and flight-day pushes, foreground suppression, server badge sync (`/flights/badge`, `/flights/{id}/seen`), and cold-start `PendingNavigation` routing.
- [ ] Verify Android App Links (`weather.flyfun.aero/briefing.html`, `/maps.html`, `/s/*`, `/t/*`) and Predictive Back transitions across all drill-downs.
- [ ] Add Android App Actions / pinned shortcuts for quick flight access.
- [ ] Set up Play Store signing, release build pipeline (`bundleRelease`), and nightly Compose UI emulator workflow (`android-ui-nightly.yml`).
