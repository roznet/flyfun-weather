# iOS App Architecture

> Tech stack, MVVM+Repository structure, layer responsibilities, auth flow

## Tech Stack

| Choice | Decision | Rationale |
|--------|----------|-----------|
| **Min iOS** | **26.2** (`IPHONEOS_DEPLOYMENT_TARGET`) | Latest SwiftUI, `@Observable`, new MapKit SwiftUI APIs. No legacy burden. |
| **UI** | **SwiftUI** | Native, declarative. No UIKit wrappers unless absolutely necessary. |
| **Persistence** | **File-based JSON + UserDefaults** | No SwiftData. `BriefingCacheStore` (actor, on-disk pack cache under Application Support), `PirepOfflineStore` (JSON queue in Documents), `UserPreferencesStore` (UserDefaults). Simpler than a DB for our cache-the-pack model. |
| **Networking** | **URLSession + async/await** | Built-in, no third-party dep. SSE refresh via `URLSession.bytes`. |
| **Maps** | **MapKit (SwiftUI Map)** | iOS 17+ API, offline tiles, sufficient. |
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

The protocol is one per-endpoint async method (returning the `*Response` Codable types from `Models/API/`), not a single `BriefingPayload`. Roughly: `flights`, `createFlight`, `parseFpl`, `packs`/`latestPack`, the pack-data fetchers (`advisories`, `digest`, `snapshot`, `routeAnalyses`, `elevation`, `soundingProfile`), image fetchers (`skewtImage`, `grametImage`), refresh (`refreshStream` SSE + `refreshStatus`), and PIREP (`submitPirep`, `submitPirepsBatch`, `fetchPireps`).

```swift
protocol BriefingRepository: Sendable { ... }

// Always hits the API
final class OnlineBriefingRepository: BriefingRepository { ... }

// Checks on-disk cache first, falls back to API, caches results
final class CachingBriefingRepository: BriefingRepository { ... }
```

`AppState` owns the active `repository` (and exposes `cachingRepository` when caching is enabled).

## iOS App Layers

| Layer | Responsibility | Key Components |
|-------|---------------|----------------|
| **UI** | SwiftUI views, cockpit-optimized | `Views/` — Briefing (container, advisory dashboard, digest, airport conditions, PIREP list/reporting), CrossSection (Canvas renderer + layer stack), RouteGraph (Swift Charts), Map (MapKit), Flights, Auth |
| **ViewModels** | State management (`@Observable`) | `FlightListViewModel`, `BriefingViewModel`, `CrossSectionViewModel`, `RouteMapViewModel`, `PirepViewModel`, `AddFlightViewModel` |
| **Repositories** | Data access (API vs cache) | `BriefingRepository` (Online/Caching impls) |
| **Domain** | Forecast/assessment view models | `Assessment`, `VizData` (`Models/Domain/`); API DTOs in `Models/API/` |
| **Location** | Live aircraft position projected onto route. `FlightTrackingService` wraps `CLLocationManager` at `kCLLocationAccuracyBest`. | `FlightTrackingService` (Core Location) |
| **Storage** | Offline-first persistence | `BriefingCacheStore` (actor, on-disk pack cache), `PirepOfflineStore` (queued PIREPs), `UserPreferencesStore` (UserDefaults) |
| **Sync** | Server communication, auth, queue flush | `APIClient` (wraps `RollingBearerSession` from FlyFunCommon), `KeychainBearerTokenStore` (FlyFunCommon), SSE refresh, PIREP batch flush |

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

**Token refresh**: rolling session (flyfun-common `SlidingSessionMiddleware`) — JWT default 30-day expiry, refreshed when within the threshold; native Bearer clients pick up the renewed token from the `X-Renewed-Token` response header. On 401, shows the login screen.

## Existing Library Reuse

SPM-linked: **FlyFunCommon, RZFlight, RZSkewT, RZUtils, RZUtilsSwift, RZUtilsSwiftUI, RZUtilsUniversal**. Directly imported in app sources today:

| Library | What's Reused |
|---------|---------------|
| **FlyFunCommon** | imported by `AppState` — `FlyFunAuthService` (Apple/Google sign-in), `RollingBearerSession` (token-refresh URLSession, 401 → `onUnauthorized`), `KeychainBearerTokenStore`. Shared across flyfun apps; repo `github.com/roznet/flyfun-common`. |
| **RZSkewT** | `SkewTView` in `SkewTDetailView` — see rzskewt entry in INDEX |
| **RZFlight** | imported by `FlightTrackingService` (airport/aviation types + `RouteGeometry`, a `public enum` in RZFlight's `Route+Geometry.swift` — `directDistanceNm`, `perpendicularDistanceAndRatio` for projecting live position onto the route) |

The RZUtils* modules are linked (logging, storage, SwiftUI helpers) but not yet directly imported in these app files. (RZData is not a dependency.)

## References

- [Data Models](./ios-app-data-models.md)
- [Server API](./ios-app-server-api.md)
- [Sync & Prompting](./ios-app-sync-prompting.md)
