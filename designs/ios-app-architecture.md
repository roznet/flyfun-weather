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
| **Auth** | **ASWebAuthenticationSession + Sign in with Apple** | Native OAuth, no token-paste friction. |
| **Deps** | **RZFlight + RZSkewT + RZUtils(Swift/SwiftUI/Universal)** via SPM | Airport data, Skew-T rendering, storage, logging. |
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
| **Sync** | Server communication, auth, queue flush | `APIClient` (`RollingBearerSession`), `KeychainBearerTokenStore`, SSE refresh, PIREP batch flush |

## Authentication Flow

Two auth methods:
1. **Sign in with Apple** — native `SignInWithAppleButton` → identity token exchanged with server via `POST /auth/apple/token` (flyfun-common). Bundle ID must be in `APPLE_APP_IDS` env var.
2. **Google OAuth** — `ASWebAuthenticationSession` → server redirects to `flyfunweather://auth/callback?token=<jwt>`.

```
┌─────────┐                ┌─────────────┐            ┌────────┐
│  iOS    │                │ WeatherBrief│            │ Google │
│  App    │                │   Server    │            │ OAuth  │
└────┬────┘                └──────┬──────┘            └───┬────┘
     │  ASWebAuthSession          │                       │
     │  opens /auth/login/google  │                       │
     │  ?platform=ios             │                       │
     ├───────────────────────────▶│                       │
     │                            │ redirect to Google    │
     │                            ├──────────────────────▶│
     │     Google consent screen (in-app browser)         │
     │◀──────────────────────────────────────────────────▶│
     │                            │ auth code callback    │
     │                            │◀──────────────────────┤
     │                            │ exchange code → JWT   │
     │  redirect to flyfunweather:│                       │
     │  //auth?token=...           │                       │
     │◀───────────────────────────┤                       │
     │  Store JWT in Keychain     │                       │
     │  (KeychainBearerTokenStore)│                       │
     │                            │                       │
     │  All API calls:            │                       │
     │  Authorization: Bearer ... │                       │
     ├───────────────────────────▶│                       │
```

**Server-side change for iOS**: `?platform=ios` param on `/auth/login/google` — callback redirects to `flyfunweather://auth?token=<jwt>` instead of the web UI. Same JWT; just delivered via URL scheme instead of cookie. The app's callback parser also accepts the `https://weather.flyfun.aero/auth/callback?token=…` form.

**Token refresh**: JWT has 7-day expiry. App stores expiry and proactively re-auths when nearing expiration. On 401, shows login screen.

## Existing Library Reuse

SPM-linked: **RZFlight, RZSkewT, RZUtils, RZUtilsSwift, RZUtilsSwiftUI, RZUtilsUniversal**. Directly imported in app sources today:

| Library | What's Reused |
|---------|---------------|
| **RZSkewT** | `SkewTView` in `SkewTDetailView` — see rzskewt entry in INDEX |
| **RZFlight** | imported by `FlightTrackingService` (airport/aviation types); route projection geometry is done locally in `RouteGeometry`, not from the library |

The RZUtils* modules are linked (logging, storage, SwiftUI helpers) but not yet directly imported in these app files. (RZData is not a dependency.)

## References

- [Data Models](./ios-app-data-models.md)
- [Server API](./ios-app-server-api.md)
- [Sync & Prompting](./ios-app-sync-prompting.md)
