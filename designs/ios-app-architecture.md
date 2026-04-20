# iOS App Architecture

> Tech stack, MVVM+Repository structure, layer responsibilities, auth flow

## Tech Stack

| Choice | Decision | Rationale |
|--------|----------|-----------|
| **Min iOS** | **18** | Latest SwiftUI, mature SwiftData, new MapKit SwiftUI APIs. No legacy burden. |
| **UI** | **SwiftUI** | Native, declarative. No UIKit wrappers unless absolutely necessary. |
| **Persistence** | **SwiftData** | SwiftUI-native, simpler than Core Data, sufficient for our model. |
| **Networking** | **URLSession + async/await** | Built-in, no third-party dep. |
| **Maps** | **MapKit (SwiftUI Map)** | iOS 17+ API, offline tiles, sufficient. |
| **Architecture** | **MVVM + Repository** | Natural fit for SwiftUI. Repos abstract API vs cache — offline-ready from day one. |
| **Cross-section** | **SwiftUI Canvas** | Immediate-mode 2D, equivalent to HTML Canvas. No WKWebView. |
| **Route graph** | **Swift Charts** | 2D charts, dual axes, extensible. |
| **Auth** | **ASWebAuthenticationSession** | Native Google OAuth, no token-paste friction. |
| **Deps** | **RZFlight + RZUtils** via SPM | Airport data, wind models, storage, logging. |
| **Project location** | Subdirectory in flyfun-weather repo | Keep API contracts in sync. |

## High-Level Components

```
┌─────────────────────────────────┐
│   WeatherBrief Companion        │  SwiftUI, iOS 18+
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
│  │ SwiftData│ │ API Client  │   │
│  │  Store   │ │  + Sync     │   │
│  └──────────┘ └──────┬──────┘   │
│                      │          │
│  ┌──────────┐ ┌──────┴──────┐   │
│  │   GPS    │ │  WebSocket  │   │  (Phase 3)
│  │ Manager  │ │   Client    │   │
│  └──────────┘ └──────┬──────┘   │
└──────────────────────┼──────────┘
                       │  HTTPS / WebSocket
               ┌───────┴────────┐
               │  WeatherBrief  │
               │    Server      │
               │   (FastAPI)    │
               └────────────────┘
```

## Repository Pattern (Offline-Ready from Phase 1)

All data flows through repositories that abstract over network vs cache. Even in Phase 1 the layer exists — Phase 2 adds caching without touching UI or ViewModels.

```swift
protocol BriefingRepository {
    func flights() async throws -> [Flight]
    func briefing(flightId: String, timestamp: String) async throws -> BriefingPayload
    func latestPack(flightId: String) async throws -> PackMeta
}

// Phase 1: always hits the API
class OnlineBriefingRepository: BriefingRepository { ... }

// Phase 2: checks cache first, falls back to API, caches results
class CachingBriefingRepository: BriefingRepository { ... }
```

## iOS App Layers

| Layer | Responsibility | Key Components |
|-------|---------------|----------------|
| **UI** | SwiftUI views, cockpit-optimized | BriefingViewer, ReportSheet, Timeline, RouteMap |
| **ViewModels** | State management, business logic | FlightListVM, BriefingVM, FlightSessionVM, PirepVM |
| **Repositories** | Data access (API vs cache) | BriefingRepository, ObservationRepository |
| **Domain** | Observation model, report builder | Observation, ObservationBuilder, FlightSession |
| **Location** | GPS tracking, altitude, route progress. Coarse accuracy (NWP grids ~10nm). `kCLLocationAccuracyKilometer` to save battery. | LocationManager (Core Location), RouteTracker |
| **Storage** | Offline-first persistence | SwiftData models, briefing cache, observation queue |
| **Sync** | Server communication, queue management | SyncEngine, APIClient, WebSocketClient |

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
     │  //auth/callback?token=...  │                       │
     │◀───────────────────────────┤                       │
     │  Store JWT in Keychain     │                       │
     │  (CodableSecureStorage)    │                       │
     │                            │                       │
     │  All API calls:            │                       │
     │  Authorization: Bearer ... │                       │
     ├───────────────────────────▶│                       │
```

**Server-side change for iOS**: `?platform=ios` param on `/auth/login/google` — callback redirects to `flyfunweather://auth/callback?token=<jwt>` instead of the web UI. Same JWT; just delivered via URL scheme instead of cookie.

**Token refresh**: JWT has 7-day expiry. App stores expiry and proactively re-auths when nearing expiration. On 401, shows login screen.

## Existing Library Reuse

| Library | What to Reuse |
|---------|---------------|
| **RZFlight** | `KnownAirports` spatial queries, `RunwayWindModel` wind display, `Briefing` NOTAM models, airport data |
| **RZUtilsSwift** | `UserStorage` / `CodableSecureStorage` (prefs + tokens), `RZSLog`, custom `Dimension` types (fpm/kt) |
| **RZUtilsSwiftUI** | `DynamicStack` adaptive layout, `Color` hex extensions |
| **RZData** | `DataFrame` for local analysis (observation stats) |
| **RZSkewT** | `SkewTView`, `SkewTRenderer` — see rzskewt entry in INDEX |

## References

- [Data Models](./ios-app-data-models.md)
- [Server API](./ios-app-server-api.md)
- [Sync & Prompting](./ios-app-sync-prompting.md)
