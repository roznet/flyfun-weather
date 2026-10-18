# Android App Architecture

> Multi-module Gradle structure, MVVM + Repository layers, Compose Canvas engines, server DTO conventions, and local HTTPS dev setup

## Tech Stack

| Choice | Decision | Rationale |
|--------|----------|-----------|
| **Min / Target SDK** | **minSdk 28 (Android 9) / targetSdk 35 (Android 15)** | Broad tablet/phone cockpit coverage with modern Compose & Foreground Service APIs. |
| **Language** | **Kotlin 2.0.21 (JVM 17)** | Coroutines, `StateFlow`, `kotlinx.serialization`, strong null-safety. |
| **UI** | **Jetpack Compose + Material 3** | Declarative UI, adaptive phone/tablet layouts (`BoxWithConstraints` >= 600dp split view), immediate-mode `Canvas` for all meteorological plots. Never uses Android dynamic wallpaper colors (`dynamicColor = false`) so semantic weather colors stay unambiguous. |
| **Networking** | **OkHttp 4.12 + `okhttp-sse` + `kotlinx.serialization`** | Built-in connection pooling, `RollingBearerInterceptor` for `X-Renewed-Token` rotation, streaming gzipped `/bundle` downloads, and SSE `/packs/refresh` progress streams. |
| **Persistence** | **File-based JSON (`BriefingCacheStore`, `PirepOfflineStore`) + `EncryptedSharedPreferences` + SQLite (`airports.db`)** | Matches iOS cache-the-pack architecture (`BriefingCache/users/<scope>/<flightId>/<timestamp>/<endpoint>.json` + shared `charts/<cacheKey>.<ext>` with 50 MB LRU cap). |
| **Maps** | **Custom Web Mercator `SlippyMapCanvas`** | Pure Compose `Canvas` tile + vector renderer with in-memory `LruCache<String, ImageBitmap>` and OSM raster tiles (`https://tile.openstreetmap.org/{z}/{x}/{y}.png` with `User-Agent: FlyFunWeather-Android/1.5`). No Google Maps API key required; renders offline graticule when disconnected. |
| **Project Location** | `app/android/` in `flyfun-weather` repo | Monorepo alongside backend (`src/weatherbrief/`), web (`web/`), and iOS (`app/flyfun-weather/`). |

## Multi-Module Gradle Structure (`app/android/`)

```
app/android/
├── settings.gradle.kts        # Includes :app, :flyfun-common, :rzflight, :rzskewt
├── gradle/libs.versions.toml  # Version catalog (AGP 8.7.3, Kotlin 2.0.21, Compose BOM 2024.10.01)
├── flyfun-common/             # Pure Kotlin JVM library (aero.flyfun.common)
│   └── TokenStore, RollingBearerInterceptor, FlyFunAuthService, JwtClaims
├── rzflight/                  # Pure Kotlin JVM library (aero.flyfun.rzflight)
│   └── Coord, Airport, RouteGeometry (Haversine + cross-track projection), KnownAirports (SQLite search)
├── rzskewt/                   # Pure Kotlin JVM library (aero.flyfun.rzskewt)
│   └── Thermodynamics (ISA, Bolton, LCL, dry/moist adiabats, HW/XW), SkewTTransform (log-P + skewed-T)
└── app/                       # Android application module (net.ro_z.flyfun_weather / aero.flyfun.weather)
    └── src/main/kotlin/aero/flyfun/weather/
        ├── model/             # @Serializable API DTOs, VizData, Assessment, rules/
        ├── data/              # ApiClient, BriefingRepository, BriefingCacheStore, PirepOfflineStore
        ├── viewmodel/         # AppStateViewModel, FlightListViewModel, BriefingViewModel, CrossSectionViewModel, MapViewModels
        ├── ui/                # theme/, auth/, flights/, trips/, aircraft/, briefing/, crosssection/, skewt/, map/, observed/, pirep/, help/
        └── service/           # FlightTrackingService, PushNotificationManager
```

### Why `:flyfun-common`, `:rzflight`, and `:rzskewt` are pure JVM libraries
Keeping the three first-party libraries as pure Kotlin JVM modules (`kotlin("jvm")`, no Android framework dependencies) means their unit tests (`MockWebServer`, `sqlite-jdbc`, thermodynamics & coordinate math) run in sub-second JVM tasks (`./gradlew :flyfun-common:test :rzflight:test :rzskewt:test`) without an emulator or Robolectric.

## Repository Pattern & Offline Cache

All ViewModels depend on `BriefingRepository` (`data/BriefingRepository.kt`) and `TripRepository` (`data/TripRepository.kt`):

```kotlin
interface BriefingRepository { ... }

class OnlineBriefingRepository(private val apiClient: ApiClient) : BriefingRepository
class CachingBriefingRepository(
    private val online: OnlineBriefingRepository,
    val cacheStore: BriefingCacheStore,
) : BriefingRepository
class FixtureBriefingRepository : BriefingRepository // Used when isMockMode / FLYFUN_MOCK=1
```

- **User-scoped cache isolation**: `BriefingCacheStore` scopes all pack files under `filesDir/BriefingCache/users/<userScope>/<flightId>/<timestamp>/` so switching accounts never leaks cached flights or packs.
- **Bundle unpacking**: `CachingBriefingRepository.downloadPackBundle` streams `GET /api/flights/{id}/packs/{ts}/bundle`, unpacks the top-level JSON keys (`advisories`, `digest`, `snapshot`, `route_analyses`, `elevation`, `alternates`, `time_options`, and `sounding_profiles` → `sounding-{pointIndex}-{model}.json`), and marks the pack complete once all 5 required endpoints exist on disk.
- **Chart cache**: `BriefingCacheStore` stores surface charts and GRAMET images under `charts/<cacheKey>.<ext>` with mark-and-sweep (`pruneCharts`) and a 50 MB LRU cap.

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
- **`AlternatesResponse`**: `alternates` is a plain `List<AlternateAirport>` where each item carries `icao`, `name`, `distanceToDestNm`, `detourNm`, `Summary: AirportConditionsSummary?`, and ` operationalFlags: List<AlternateFlag>`.
- **`LiveLayerResponse`** (`GET /api/flights/{id}/live`): `glance.lines` is a `Map<String, LiveGlanceLine>` keyed by phase (`"departure"`, `"en_route"`, `"arrival"`, `"comparison"`); `ribbon` uses `bins: List<LiveRibbonBin>`, `stations: List<LiveRibbonStation>` (at `routeNm`), `sigmets: List<LiveRibbonSigmet>` (`fromNm`..`toNm`), and `routeLengthNm`; `storms.storms` uses `tier`, `side`, `offTrackNm`, `abeamEta`, `closing`, `history: List<LiveStormHistoryPoint>`; `progress` uses `distanceFlownNm`, `totalDistanceNm`, `fractionFlown` (`flownFraction` helper falls back to `distanceFlownNm / totalDistanceNm`).
- **`TripResponse`** (`GET /api/trips`): list items contain `legs: List<TripLegResponse>` without an inline `summary` object; `TripResponse.resolvedSummary` synthesizes a fallback `TripSummaryResponse` from `legs` when `summary == null`.

## Local HTTPS Dev Server & Emulator Networking

`ApiClient.DEFAULT_BASE_URL` selects `https://localhost.ro-z.me:8443` when `BuildConfig.DEBUG && ApiClient.isRunningOnEmulator()`, and `https://weather.flyfun.aero` on physical devices and release builds (mirroring iOS `#if targetEnvironment(simulator)`).

1. **Hostname rewrite + TLS trust (`ApiClient.kt`, `FlyFunAuthService.kt`)**:
   - On an Android emulator without `adb reverse`, `localhost.ro-z.me` resolves in public DNS to `127.0.0.1` (the emulator's own loopback).
   - Either run `adb reverse tcp:8443 tcp:8443` (recommended, so `127.0.0.1:8443` inside the emulator tunnels to host `127.0.0.1:8443`), or let `ApiClient.rewriteDevLoopbackUrl` rewrite `localhost.ro-z.me` to `10.0.2.2` when running on an emulator (`FINGERPRINT.contains("generic") || MODEL.contains("sdk_gphone")`).
   - In debug builds, `ApiClient.Builder.configureLocalDevTlsIfNeeded` trusts the local `mkcert` certificate on `localhost.ro-z.me` / `10.0.2.2`.
2. **IPv4 → IPv6 loopback relay (`scripts/ops/devserver.py`)**:
   - `wb-https` runs `uvicorn --host ::1 --port 8443` (IPv6 loopback only), while `adb reverse` and emulator `10.0.2.2` connect to IPv4 `127.0.0.1:8443`.
   - `scripts/ops/devserver.py` starts `run_ipv4_loopback_relay(8443)` alongside `uvicorn`, forwarding `127.0.0.1:8443` → `[::1]:8443` while keeping `wb-https` strictly on loopback.

## References

- [Android Overview](./android-app-overview.md) — parent doc, feature summary
- [iOS App Architecture](./ios-app-architecture.md) — sibling iOS architecture
- [Briefing Refresh Notifications](./ios-app-briefing-notifications.md) — APNs + FCM push architecture
