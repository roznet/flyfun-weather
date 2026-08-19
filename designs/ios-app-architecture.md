# iOS App Architecture

> Tech stack, MVVM+Repository structure, layer responsibilities, auth flow

## Tech Stack

| Choice | Decision | Rationale |
|--------|----------|-----------|
| **Min iOS** | **26.2** (`IPHONEOS_DEPLOYMENT_TARGET`) | Latest SwiftUI, `@Observable`, new MapKit SwiftUI APIs. No legacy burden. |
| **UI** | **SwiftUI** | Native, declarative. No UIKit wrappers unless absolutely necessary. |
| **Persistence** | **File-based JSON + UserDefaults** | No SwiftData. `BriefingCacheStore` (actor, on-disk pack cache under Application Support), `PirepOfflineStore` (JSON queue in Documents), `UserPreferencesStore`/`AppSettingsStore`/`WhatsNewStore` (UserDefaults). Simpler than a DB for our cache-the-pack model. The one SQLite is `AirportDatabase` — a slim `airports.db` **downloaded** from `GET /api/nav/airports-db` (ETag-refreshed, not bundled: it churns per AIRAC), read through RZFlight's `KnownAirports`/FMDB for offline ICAO autocomplete. |
| **Networking** | **URLSession + async/await** | Built-in, no third-party dep. SSE refresh via `URLSession.bytes`. |
| **Maps** | **MapKit (`MKMapView` via `UIViewRepresentable`)** | Both the briefing route map and the forecast map are `MKMapView`-backed (converged in #428) so the airport-forecast marker layer is shared, not duplicated per map — the SwiftUI `Map` API janks at the forecast map's ~620-annotation scale. |
| **Architecture** | **MVVM + Repository** | Natural fit for SwiftUI. Repos abstract API vs cache — offline-ready from day one. |
| **Cross-section** | **SwiftUI Canvas** | Immediate-mode 2D, equivalent to HTML Canvas. No WKWebView. |
| **Route graph** | **Swift Charts** | 2D charts, dual axes, extensible. |
| **Auth** | **ASWebAuthenticationSession + Sign in with Apple**, driven by `FlyFunAuthService` (FlyFunCommon) | Native OAuth, no token-paste friction. Shared auth/session code across flyfun apps. |
| **Deps** | **FlyFunCommon + RZFlight + RZSkewT + RZUtils(Swift/SwiftUI/Universal)** via SPM | FlyFunCommon = shared auth/session/keychain (`github.com/roznet/flyfun-common`); RZ* = airport data, Skew-T rendering, storage, logging. |
| **Project location** | `app/flyfun-weather/` in flyfun-weather repo | Keep API contracts in sync. |

## High-Level Components

```
┌─────────────────────────────────┐
│   WeatherBrief Companion        │  SwiftUI, iOS 26.2+
│                                 │
│  ┌──────────┐ ┌──────────────┐  │
│  │ Briefing │ │  PIREP       │  │
│  │ Viewer   │ │  Reporter    │  │
│  └────┬─────┘ └──────┬───────┘  │
│       │              │          │
│  ┌────┴──────────────┴──────┐   │
│  │      View Models         │   │  MVVM
│  └────┬──────────────┬──────┘   │
│       │              │          │
│  ┌────┴──────────────┴──────┐   │
│  │      Repositories        │   │  API vs cache
│  └────┬──────────────┬──────┘   │
│       │              │          │
│  ┌────┴─────┐ ┌──────┴──────┐   │
│  │  Cache   │ │ API Client  │   │
│  │  Stores  │ │  (APIClient)│   │
│  └──────────┘ └──────┬──────┘   │
│                      │          │
│  ┌──────────────────┴──────┐   │
│  │  FlightTrackingService   │   │  Core Location (live position)
│  │  (CLLocationManager)     │   │
│  └──────────────────┬──────┘   │
└──────────────────────┼──────────┘
                       │  HTTPS (REST + SSE)
               ┌───────┴────────┐
               │  WeatherBrief  │
               │    Server      │
               │   (FastAPI)    │
               └────────────────┘
```

No WebSocket client. Live refresh progress streams over **Server-Sent Events** (`APIClient.streamSSE` → `AsyncThrowingStream<RefreshEvent, Error>` via `URLSession.bytes`); everything else is plain REST.

## Repository Pattern (Offline-Ready)

All data flows through `BriefingRepository` (`Services/BriefingRepository.swift`), which abstracts over network vs cache. UI and ViewModels depend only on the protocol.

The protocol is one per-endpoint async method (returning the `*Response` Codable types from `Models/API/`), not a single `BriefingPayload`. It has grown well past the briefing itself — roughly: flights CRUD (`flights`, `flight`, `createFlight`, `updateFlight`, `moveFlight`, `deleteFlight`, `bulkDeleteFlights`), route entry (`parseFpl`, `interpretRoute`, `routeDistance`, `autorouterRoutes`), aircraft/profiles/`usageSummary`, sharing (`flightByShareCode`, `subscribeFlight`, `unsubscribeFlight`), standalone weather (`airportWeather`, `forecastMap`, `forecastDays`, `frequentAirports`), packs (`packs`/`latestPack`), pack-data fetchers (`advisories`, `advisoryDetail`, `recalculateAdvisories`, `digest`, `snapshot`, `routeAnalyses`, `elevation`, `soundingProfile`, `timeOptions`/`confirmTimeOption`/`rescanTimeOptions`), image fetchers (`skewtImage`, `grametImage`), refresh (`refreshStream` SSE, `refreshStatus`, `triggerRefresh`, `activeRefreshes`), PIREP (`submitPirep`, `submitPirepsBatch`, `fetchPireps`), debrief (`fetchDebrief`, `upsertDebrief`, `deleteDebrief`) and feedback. Adding an endpoint means touching every conformer — keep that in mind before widening the protocol.

```swift
protocol BriefingRepository: Sendable { ... }

// Always hits the API
final class OnlineBriefingRepository: BriefingRepository { ... }

// Checks on-disk cache first, falls back to API, caches results
final class CachingBriefingRepository: BriefingRepository { ... }

// DEBUG-only: canned fixtures for XCUITest (FLYFUN_MOCK=1), no backend/network
final class FixtureBriefingRepository: BriefingRepository { ... }
```

`AppState` owns the active `repository` (and exposes `cachingRepository` when caching is enabled). The fixture repo is what makes the XCUI journeys run without a server — `FLYFUN_MOCK_OFFLINE=1` additionally makes it report cached-list status so the offline banner/read-only paths are testable; endpoints no journey needs throw `notProvided` rather than being faked.

## iOS App Layers

| Layer | Responsibility | Key Components |
|-------|---------------|----------------|
| **UI** | SwiftUI views, cockpit-optimized | `Views/` — Briefing (container, advisory dashboard + detail, digest, alternates, timing scenarios, route observations/SIGMETs, debrief, PIREP list/reporting, shared-flight preview), CrossSection (Canvas renderer + `Layers/` stack + Skew-T), RouteGraph (Swift Charts), Map + ForecastMap (both MapKit), Flights, Help/What's-New, Auth, Shared (theme, markdown-lite, marker sizing) |
| **ViewModels** | State management (`@Observable`) | `FlightListViewModel`, `BriefingViewModel`, `CrossSectionViewModel`, `RouteMapViewModel`, `ForecastMapViewModel`, `RouteForecastOverlayModel`, `PirepViewModel`, `DebriefViewModel`, `AddFlightViewModel`, `DepartureTimeModel`, `RouteAutocompleteController` |
| **Repositories** | Data access (API vs cache) | `BriefingRepository` (Online/Caching/Fixture impls) |
| **Domain** | Forecast/assessment view models | `Assessment`, `VizData`, `FlightDuration` (`Models/Domain/`); API DTOs in `Models/API/` |
| **Location** | Live aircraft position projected onto route. `FlightTrackingService` wraps `CLLocationManager` at `kCLLocationAccuracyBest`. | `FlightTrackingService` (Core Location) |
| **Storage** | Offline-first persistence | `BriefingCacheStore` (actor, on-disk pack cache), `PirepOfflineStore` (queued PIREPs), `UserPreferencesStore`/`AppSettingsStore`/`WhatsNewStore` (UserDefaults), `HelpCatalogStore`, `AirportDatabase` (downloaded SQLite) |
| **Sync** | Server communication, auth, queue flush | `APIClient` (wraps `RollingBearerSession` from FlyFunCommon), `KeychainBearerTokenStore` (FlyFunCommon), `NetworkMonitor`, SSE refresh, PIREP batch flush |
| **System surfaces** | Siri/Shortcuts/Spotlight + APNs | `AppIntents/` (see [App Intents](./ios-app-intents.md)), `Services/PushNotifications.swift` (see [Briefing notifications](./ios-app-briefing-notifications.md)). Both foreground the app through the same `PendingNavigation` seam deep links use. |

## Authentication Flow

App-side auth is driven by `FlyFunAuthService` (FlyFunCommon), constructed in `LoginView` with `callbackScheme: "flyfunweather"`. The view calls `authService.exchangeAppleCredential(...)` / `authService.signIn(provider: "google")`, gets a JWT back, and hands it to `appState.signIn(token:)`. Two methods:
1. **Sign in with Apple** — native `SignInWithAppleButton` → identity token exchanged with server via `POST /auth/apple/token` (flyfun-common). Bundle ID must be in `APPLE_APP_IDS` env var.
2. **Google OAuth** — `ASWebAuthenticationSession` → **native authorization-code flow** (H8 hardening, see `flyfun-common/designs/oauth-deeplink-hardening.md`): the server callback returns a short-TTL one-time **`code`** bound to a client-generated **`state`** nonce (never the JWT), which `FlyFunAuthService` verifies (`state` match) and exchanges for the session JWT over `POST /auth/exchange`. The bearer token is never in a URL.

(There is also a debug-only `/auth/dev-token` path in `LoginView` for simulator/dev sign-in.)

```
┌─────────┐                ┌─────────────┐            ┌────────┐
│  iOS    │                │ WeatherBrief│            │ Google │
│  App    │                │   Server    │            │ OAuth  │
└────┬────┘                └──────┬──────┘            └───┬────┘
     │  ASWebAuthSession          │                       │
     │  /auth/login/google        │                       │
     │  ?platform=ios&state=<n>   │                       │
     ├───────────────────────────▶│                       │
     │                            │ redirect to Google    │
     │                            ├──────────────────────▶│
     │     Google consent screen (in-app browser)         │
     │◀──────────────────────────────────────────────────▶│
     │                            │ auth code callback    │
     │                            │◀──────────────────────┤
     │  redirect flyfunweather:// │                       │
     │  auth/callback?code=&state=│                       │
     │◀───────────────────────────┤                       │
     │  verify state, then        │                       │
     │  POST /auth/exchange       │                       │
     │  {code,state} ───────────▶ │ → { token } (body)    │
     │◀───────────────────────────┤                       │
     │  Store JWT in Keychain     │                       │
     │  All API calls: Bearer ... │                       │
     ├───────────────────────────▶│                       │
```

**Server-side for iOS**: `?platform=ios` + `state` on `/auth/login/google`. The callback emits `code`+`state` for state-sending (migrated) clients, falling back to the legacy `?token=` param for not-yet-updated clients (shared multi-app compat). The app also accepts the `https://weather.flyfun.aero/auth/callback?...` universal-link form.

**`onOpenURL` deep links**: the normal Google/Apple sign-in is captured *inside* `ASWebAuthenticationSession` and never reaches `onOpenURL`. `AppState.handleAuthCallback` therefore accepts a bare-token deep link **only** for the App Store reviewer link, gated by `shouldAcceptDeepLinkToken` (signed-out **and** the token carries `scope:"review"`). Any other inbound token is ignored — this is the H8 login-CSRF carve-out. The `scope` claim is read unverified for *routing only*; server signature verification is still the actual authorization.

**Universal-Link deep links**: `onOpenURL` first tries `AppState.handleUniversalLink`; non-matches fall through to `handleAuthCallback`. Parsing lives in the pure, `nonisolated` `AppState.navigationTarget(for:)` (unit-tested in `UniversalLinkRoutingTests`), which maps host `weather.flyfun.aero` plus:

| Path | Target |
|------|--------|
| `/briefing.html?flight=<id>` | `.briefing(flightId:)` — the web **Smart App Banner** target (`<meta name="apple-itunes-app" app-id=6760951972>` on `web/index.html` + `web/briefing.html`, `app-argument` = current URL) |
| `/maps.html?fc.*` | `.forecastMap(MapDeepLink)` (#420) — day/hour/model/metric/apt carried over, so a desktop-shared map link opens the phone in the same state; a bare `/maps.html` opens at the cold-open default |
| `/s/{code}` | `.share(code:)` (#446) — shared-flight preview with a Subscribe banner; the code shape is validated against the server's `_SHARE_CODE_RE` (`^[0-9A-Za-z]{4,16}$`) so a stray `/s/` can't route to an empty preview |

All of them route through the same cold-launch-safe `PendingNavigation` seam App Intents and push taps use. The domain's AASA (`deploy/weather.flyfun.aero.caddy`, served at `/.well-known/apple-app-site-association`, team `M7QSSF3624`) whitelists `paths: ["/auth/callback","/briefing.html","/maps.html","/s/*"]`. Requires the AASA change to be deployed **before** a new app build ships (iOS caches AASA per-install).

**Token refresh**: rolling session (flyfun-common `SlidingSessionMiddleware`) — JWT default 30-day expiry, refreshed when within the threshold; native Bearer clients pick up the renewed token from the `X-Renewed-Token` response header. On 401, shows the login screen.

## Existing Library Reuse

SPM-linked: **FlyFunCommon, RZFlight, RZSkewT, RZUtils, RZUtilsSwift, RZUtilsSwiftUI, RZUtilsUniversal**. Directly imported in app sources today:

| Library | What's Reused |
|---------|---------------|
| **FlyFunCommon** | `FlyFunAuthService` (Apple/Google sign-in, `LoginView`), `RollingBearerSession` (token-refresh URLSession, 401 → `onUnauthorized`; `AppState` + `APIClient`), `KeychainBearerTokenStore`, plus `IntentSupport`. Shared across flyfun apps; repo `github.com/roznet/flyfun-common`. |
| **RZSkewT** | `SkewTView` in `SkewTDetailView` (+ `SkewTVariableCatalog`) — see rzskewt entry in INDEX |
| **RZFlight** | `RouteGeometry` (a `public enum` in `Route+Geometry.swift` — `directDistanceNm`, `perpendicularDistanceAndRatio`) for projecting live position onto the route in `FlightTrackingService`; `KnownAirports` for offline ICAO search in `AirportDatabase`, consumed by `RouteAutocompleteController` and the `AirportEntity`/`FlightResolver` App Intents. FMDB arrives transitively through it — there is no direct SQLite dependency of our own. |

The RZUtils* modules are linked (logging, storage, SwiftUI helpers) but not yet directly imported in these app files. (RZData is not a dependency.) `FoundationModels` is used behind `#if canImport` in `FlightPhraseResolver` for on-device intent phrase parsing, with a deterministic fallback when the model is unavailable.

## References

- [Overview](./ios-app-overview.md) — parent doc, phase status
- [Data Models](./ios-app-data-models.md)
- [Server API](./ios-app-server-api.md)
- [Features](./ios-app-features.md) · [UI](./ios-app-ui.md)
- [App Intents](./ios-app-intents.md) · [Briefing notifications](./ios-app-briefing-notifications.md)
- [Sync & Prompting](./ios-app-sync-prompting.md)
