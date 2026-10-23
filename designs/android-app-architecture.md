# Android App Architecture

> Multi-module Gradle structure, MVVM + Repository layers, Compose Canvas engines, server DTO conventions, and local HTTPS dev setup

## Tech Stack

| Choice | Decision | Rationale |
|--------|----------|-----------|
| **Min / Target / Compile SDK** | **minSdk 31 (Android 12) / targetSdk 36 / compileSdk 37** | Modern Android phone/tablet/foldable coverage with Android 12+ Keystore, Foreground Service location types, and Compose APIs. |
| **Language & Build** | **Kotlin 2.4.20 (JVM 17) / AGP 9.4.1 / Gradle 9.4.1** | Coroutines, `StateFlow`, `kotlinx.serialization` (`1.8.0`), strong null-safety. |
| **UI** | **Jetpack Compose (BOM `2026.09.00`) + Material 3 + Coil (`2.7.0`)** | Declarative UI, adaptive phone/tablet layouts (`BoxWithConstraints` >= `840.dp` split view), AndroidX `viewModel(viewModelStoreOwner, key)` lifecycle scoping (see §4), immediate-mode `Canvas` for all meteorological plots. Never uses Android dynamic wallpaper colors (`dynamicColor = false`) so semantic weather colors stay unambiguous. |
| **Networking** | **OkHttp 4.12 + `okhttp-sse` + `kotlinx.serialization`** | Built-in connection pooling, host-scoped `RollingBearerInterceptor` for `X-Renewed-Token` rotation, streaming gzipped `/bundle` downloads, and SSE `/packs/refresh` progress streams. |
| **Persistence** | **File-based JSON (`BriefingCache`, `PirepOfflineStore`) + `AndroidTokenStore` (Android Keystore AES-GCM) + SQLite (`airports.db`)** | Matches iOS cache-the-pack architecture (`filesDir/BriefingCache/users/<scope>/<flightId>/<timestamp>/<endpoint>.json` + shared `charts/<cacheKey>.<ext>` with 50 MB LRU cap). |
| **Maps** | **Custom Web Mercator `SlippyMapCanvas`** | Pure Compose `Canvas` tile + vector renderer with in-memory `LruCache<String, ImageBitmap>` and OkHttp 30 MB disk cache. Matches web/iOS tile convention (`web/ts/visualization/map-tiles.ts`): Stadia `alidade_smooth_dark` in dark mode, Stadia `alidade_smooth` in muted light mode (when radar/satellite overlay is active), and OSM in standard light mode (with `Referer: https://weather.flyfun.aero/`). |
| **Project Location** | `app/android/` in `flyfun-weather` repo | Monorepo alongside backend (`src/weatherbrief/`), web (`web/`), and iOS (`app/flyfun-weather/`). |

## Multi-Module Gradle Structure (`app/android/`)

```
app/android/
├── settings.gradle.kts        # Includes :app, :flyfun-common, :rzflight, :rzskewt
├── gradle/libs.versions.toml  # Version catalog (AGP 9.4.1, Kotlin 2.4.20, Compose BOM 2026.09.00, Coil 2.7.0)
├── flyfun-common/             # App-local pure Kotlin JVM port (aero.flyfun.common)
│   └── TokenStore, RollingBearerInterceptor (host-scoped), FlyFunAuthService, JwtClaims
├── rzflight/                  # App-local pure Kotlin JVM port (aero.flyfun.rzflight)
│   └── Coord, Airport, RouteGeometry (Haversine + cross-track projection), KnownAirports (SQLite search)
├── rzskewt/                   # App-local pure Kotlin JVM port (aero.flyfun.rzskewt)
│   └── Thermodynamics (ISA, Bolton, LCL, dry/moist adiabats, HW/XW), SkewTTransform (log-P + skewed-T)
└── app/                       # Android application module (applicationId + namespace aero.flyfun.weather)
    └── src/main/kotlin/aero/flyfun/weather/
        ├── model/             # @Serializable API DTOs, VizData, Assessment, rules/
        ├── data/              # ApiClient, AndroidTokenStore, BriefingRepository, BriefingCache, PirepOfflineStore
        ├── viewmodel/         # AppStateViewModel, FlightListViewModel, BriefingViewModel, CrossSectionViewModel, MapViewModels
        ├── ui/                # theme/, auth/, flights/, trips/, aircraft/, briefing/, crosssection/, skewt/, map/, observed/, pirep/, help/
        └── service/           # FlightTrackingService, PushNotificationManager
```

### Why `:flyfun-common`, `:rzflight`, and `:rzskewt` are app-local pure JVM modules
The upstream `flyfun-common`, `RZFlight`, and `RZSkewT` repositories are Swift-only packages used by iOS. On Android, `:flyfun-common`, `:rzflight`, and `:rzskewt` are self-contained, app-local Kotlin ports (`kotlin("jvm")`, no Android framework dependencies) of the subset needed by WeatherBrief. Keeping them as pure JVM modules means their unit tests (`MockWebServer`, `sqlite-jdbc`, thermodynamics & coordinate math) run in sub-second JVM tasks (`./gradlew :flyfun-common:test :rzflight:test :rzskewt:test`) without an emulator or Robolectric.

## Security, Auth & Lifecycle Architecture

1. **Hardware-Backed Token Encryption (`AndroidTokenStore`)**:
   - `AndroidTokenStore` (`data/AndroidTokenStore.kt`) generates a non-exportable 256-bit AES key in Android's `AndroidKeyStore` (`alias = "aero.flyfun.weather.jwt_key_v1"`) and encrypts the session JWT with `AES/GCM/NoPadding` (12-byte random IV prefixed to the ciphertext) at `filesDir/flyfun_auth_token.enc`.
   - Automatically migrates and deletes any legacy plaintext `filesDir/flyfun_auth_token.jwt` file on first read.
   - On a device, an encryption failure never writes plaintext and never signs the pilot out: `writeToken` keeps the previously stored token (the next `X-Renewed-Token` retries) and logs a warning. The plaintext `FileTokenStore` path runs only when the `AndroidKeyStore` provider is absent (plain JVM unit tests).
2. **Host-Scoped Bearer Token & Renewal (`RollingBearerInterceptor`)**:
   - `ApiClient` passes `allowedHostProvider = { runCatching { this.baseUrl.toHttpUrl().host }.getOrNull() }` into `RollingBearerInterceptor`.
   - Even if `ApiClient.buildUrl` is called with an absolute URL, `Authorization: Bearer <jwt>`, `X-Renewed-Token` acceptance, and HTTP 401 token clearing / logout only execute when `request.url.host` matches the configured API host.
3. **Dynamic Per-User Cache Scoping & Persistent Storage (`AppStateViewModel`, `BriefingCache`)**:
   - Offline packs are stored under `File(context.filesDir, "BriefingCache")` (never `cacheDir`, so the OS will not evict downloaded cockpit packs under storage pressure; any legacy `cacheDir/briefing_cache` directory is cleaned up automatically on startup).
   - `AppStateViewModel.rebuildUserScopedRepositories(token)` derives the user directory (`users/<16-hex-sha256-of-sub>/`) from the active JWT and rebuilds `BriefingCache`, `CachingBriefingRepository`, and `OnlineTripRepository` on `init`, `completeLogin(jwt)`, and `logout()`, exposing `cacheScope: StateFlow<String>`. Signing out and signing in as another account in the same session immediately switches the cache directory and ViewModel keys.
   - **Retention policy**: Explicitly downloaded offline packs under `users/<scope>/<flightId>/<timestamp>/` are kept on disk until deleted by the user (or when the user clears cache in Settings), while shared surface/GRAMET chart images under `charts/` are bounded by a 50 MB mark-and-sweep LRU cap (matching iOS).
4. **ViewModel Lifecycle & Configuration Survival (`MainActivity.kt`)**:
   - Screen ViewModels do not sit directly in the activity `ViewModelStore`. `viewmodel/ViewModelScopes` (itself an activity-scoped ViewModel) holds named `ViewModelStoreOwner`s: `session:<sessionKey>` (`FlightListViewModel`, `TripsViewModel`, `AircraftListViewModel`, forecast / historical map VMs) and `briefing:<sessionKey>:<flightId>` (`BriefingViewModel`, plus the briefing's nested `viewModel()` calls such as `CrossSectionViewModel`, via `LocalViewModelStoreOwner`). `sessionKey` is `"$cacheScope:$isMockMode:$isGuestBrowsing"`.
   - A `LaunchedEffect(sessionKey, activeFlightId)` calls `retainOnly({session, active briefing})`, so closing a briefing (phone Back, tablet deselect), selecting another flight, or changing `sessionKey` (logout, account switch, demo / guest toggle) clears the dropped owners: `onCleared` runs and `viewModelScope` work (pack loads, refresh polling) is cancelled. The login screen clears everything.
   - Why owners and not `NavHost`: a back-stack entry would give the same clear-on-pop, but the two-pane tablet layout and saveable route state predate it; the owner map gets the lifecycle without that rewrite (#824). Why a ViewModel and not a saveable map: a `ViewModelStore` can't be saved; held in an activity-scoped ViewModel it survives rotation, and after process death a fresh VM is what we want anyway.
   - A reopened briefing is always a fresh `BriefingViewModel`, so there is no reopen-detection in `applyLaunchOptions`. Tapping the already-selected flight from another screen (tablet: back from Settings, a trip leg) is treated as a fresh open: `openBriefing` clears that briefing's owner first.
   - Navigation state (`currentScreen`, `selectedFlightId`, `selectedPackTimestamp`, `openObservedOnLaunch`, `selectedTripId`) uses `rememberSaveable` so tablet rotation, foldable posture changes, and dark-mode toggles preserve both the active screen and in-flight ViewModel coroutines (the owner keys don't change, so nothing is cleared).
   - Root `BackHandler` pops back from secondary screens (`My Aircraft`, `Explore Maps`, `What's New`, `Settings`, `Feedback`, `Trip Detail`) and, on single-pane phones (`maxWidth < 840.dp`), pops from an open briefing back to the Flight List instead of exiting the activity.

## Repository Pattern & Offline Cache

All ViewModels depend on `BriefingRepository` (`data/BriefingRepository.kt`) and `TripRepository` (`data/TripRepository.kt`):

```kotlin
interface BriefingRepository { ... }

class OnlineBriefingRepository(private val apiClient: ApiClient) : BriefingRepository
class CachingBriefingRepository(
    private val online: OnlineBriefingRepository,
    val cache: BriefingCache,
    private val isOnlineProvider: () -> Boolean = { true },
) : BriefingRepository
class FixtureBriefingRepository : BriefingRepository // Used when isMockMode / FLYFUN_MOCK=1
```

- **Concurrent pack loading**: `BriefingViewModel.loadInternal` and `loadPackContents` fetch all 9 pack sub-endpoints (`advisories`, `digest`, `snapshot`, `routeAnalyses`, `elevation`, `alternates`, `timeOptions`, `debrief`, `liveLayer`) in parallel using `coroutineScope { async { ... } }`.
- **Bundle unpacking**: `CachingBriefingRepository.downloadPackBundle` streams `GET /api/flights/{id}/packs/{ts}/bundle`, unpacks the top-level JSON keys (`advisories`, `digest`, `snapshot`, `route_analyses`, `elevation`, `alternates`, `time_options`, and `sounding_profiles` → `sounding-{pointIndex}-{model}.json`), and marks the pack complete once all 5 required endpoints exist on disk.
- **Chart cache**: `BriefingCache` stores surface charts and GRAMET images under `charts/<cacheKey>.<ext>` with mark-and-sweep (`pruneCharts`) and a 50 MB LRU cap.

## Canvas Coordinate & DPI Conventions (Gotchas)

1. **Always convert `dp` margins to `px` on Android `Canvas`**:
   - Unlike iOS SwiftUI `Canvas` (which draws in logical points), Jetpack Compose `Canvas` draws in **raw device pixels (`size.width`, `size.height`)**.
   - In `CrossSectionCanvas.kt`, `RouteGraphCanvas.kt`, and `SkewTCanvas.kt`, always scale layout margins with `density`:
     ```kotlin
     val marginLeftPx = 52.dp.toPx()
     val marginRightPx = 44.dp.toPx()
     ```
   - `CrossSectionCanvas` and `RouteGraphCanvas` share identical `52.dp` left and `44.dp` right margins so route distance $X$ coordinates stay pixel-aligned vertically between the cross-section and the route graph.
2. **Directional Scrub-vs-Scroll Gesture**:
   - `CrossSectionCanvas` sits inside a vertically scrollable screen. Its `pointerInput` gesture locks horizontal drags ($\lvert\Delta x\rvert > \lvert\Delta y\rvert$) to `onScrubDistanceChanged(distanceNm)` while allowing vertical drags to scroll the parent column.

## Server DTO Alignment Gotchas (`kotlinx.serialization`)

`ApiClient` configures `Json { ignoreUnknownKeys = true; isLenient = true; coerceInputValues = true; explicitNulls = false }`. When adding or modifying DTOs in `model/`, preserve these server shapes:
- **`AirportConditions`** (`advisories.airport_conditions`): server returns `{ "departure": AirportConditionsSummary, "arrival": AirportConditionsSummary }`, where `AirportConditionsSummary.conditions` is a `Map<String, AirportModelCondition>` keyed by model name (`"gfs"`, `"icon"`, `"ecmwf"`).
- **`RouteObservations.airports`**: server returns `List<AirportObservation>`, not a map; `hasMetar` is derived (`hasMetarFlag ?: (metarRaw != null || flightCategory != null)`), and wind fields are `metarWindDir`, `metarWindSpeedKt`, `metarWindGustKt`, `tafWindDir`, `tafWindSpeedKt`.
- **`AlternatesResponse`**: `alternates` is a plain `List<AlternateAirport>` where each item carries `icao`, `name`, `distanceToDestNm`, `detourNm`, `summary: AirportConditionsSummary?`, and `operationalFlags: List<AlternateFlag>`.
- **`LiveLayerResponse`** (`GET /api/flights/{id}/live`): `glance.lines` is a `Map<String, LiveGlanceLine>` keyed by phase (`"departure"`, `"en_route"`, `"arrival"`, `"comparison"`); `ribbon` uses `bins: List<LiveRibbonBin>`, `stations: List<LiveRibbonStation>` (at `routeNm`), `sigmets: List<LiveRibbonSigmet>` (`fromNm`..`toNm`), and `routeLengthNm`; `storms.storms` (`models/live.py::LiveStorm`) carries `intensity`, `trend`, `ahead`, `side`, `offtrack_nm`, `along_nm`, `closing_kt`, `history[].offtrack_nm` and **no** `severity` / `disposition` (the Kotlin defaults "developing" / "vicinity" used to label every card; #883 stopped reading them in `LiveStormsSection`); `progress` uses `distanceFlownNm`, `totalDistanceNm`, `fractionFlown` (`flownFraction` helper falls back to `distanceFlownNm / totalDistanceNm`).
- **Airport cards (#883)**: `glance.departure` / `glance.destination` both decode to `LiveGlanceTerminal` (`model/AirportCardModels.kt`), with the card fields (#880) on `AirportObservation` and the server's `runway_wind` as `RunwayWindWire` (named apart from the client-built `RunwayWindPicture`). `ui/observed/AirportCard.kt` only lays them out; the choices (slot order, header boxes, tones, offline text) are in `model/rules/AirportCardRules.kt`, unit-tested and checked against the shared `web/tests/fixtures/airport_cards.json`. The corner dial reuses `RunwayCompassCanvas(compact = true)` with the server's `best_end`. A failed `/live` read keeps the previous layer (`BriefingViewModel.liveFetchFailed`) and shows the offline banner.
- **`TripResponse`** (`GET /api/trips`): list items contain `legs: List<TripLegResponse>` without an inline `summary` object; `TripResponse.resolvedSummary` synthesizes a fallback `TripSummaryResponse` from `legs` when `summary == null`.

## Local HTTPS Dev Server & Emulator Networking

`ApiClient.DEFAULT_BASE_URL` selects `https://localhost.ro-z.me:8443` when `BuildConfig.DEBUG && ApiClient.isRunningOnEmulator()`, and `https://weather.flyfun.aero` on physical devices and release builds (mirroring iOS `#if targetEnvironment(simulator)`).

1. **Debug-only hostname rewrite + TLS trust (`ApiClient.kt`, `FlyFunAuthService.kt`)**:
   - On an Android emulator without `adb reverse`, `localhost.ro-z.me` resolves in public DNS to `127.0.0.1` (the emulator's own loopback).
   - Either run `adb reverse tcp:8443 tcp:8443` (recommended, so `127.0.0.1:8443` inside the emulator tunnels to host `127.0.0.1:8443`), or let `LocalhostRozMeDns` resolve `localhost.ro-z.me` to `10.0.2.2` when running on an emulator (`FINGERPRINT.contains("generic") || MODEL.contains("sdk_gphone")`).
   - `LocalhostRozMeDns` and `configureLocalDevTlsIfNeeded` are strictly gated to debug builds (`BuildConfig.DEBUG`) or explicit `localhost.ro-z.me` base URLs so release builds exclusively use standard system DNS and CA validation.
2. **IPv4 → IPv6 loopback relay (`scripts/ops/devserver.py`)**:
   - `wb-https` runs `uvicorn --host ::1 --port 8443` (IPv6 loopback only), while `adb reverse` and emulator `10.0.2.2` connect to IPv4 `127.0.0.1:8443`.
   - `scripts/ops/devserver.py` starts `run_ipv4_loopback_relay(8443)` alongside `uvicorn`, forwarding `127.0.0.1:8443` → `[::1]:8443` while keeping `wb-https` strictly on loopback.

## References

- [Android Overview](./android-app-overview.md) — parent doc, feature summary
- [iOS App Architecture](./ios-app-architecture.md) — sibling iOS architecture
- [Briefing Refresh Notifications](./ios-app-briefing-notifications.md) — APNs + FCM push architecture
