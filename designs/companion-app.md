# WeatherBrief Companion App

> iOS/iPad app for in-flight condition reporting, offline briefing access, and collaborative PIREPs

## Vision

The WeatherBrief Companion App turns every flight into a two-way weather conversation: before departure, you sync your briefing and carry it offline; in flight, you mark actual conditions with a few taps; after landing, those observations feed back into forecast verification and (if online) stream as PIREPs to other pilots.

The app is designed for the cockpit: one-handed operation, large tap targets, GPS-prepopulated fields, and a UI that assumes the pilot is busy. It works fully offline (most GA pilots have no connectivity) but lights up with real-time sharing when a Starlink or cellular connection is available.

On the ground, the app doubles as a primary briefing viewer — with push notifications when briefings auto-refresh, native cross-section rendering, and a mobile-optimized UI that's better than the web on a phone or tablet.

Long-term, the observation data creates a feedback loop: compare what was forecast against what actually happened, learn which models are most reliable for which conditions, and build a community-sourced real-time weather picture that complements official PIREPs.

## Core Features

> **Note**: This section describes the complete end-state feature set. Features are built incrementally — see **Implementation Phases** for the build order (Phase 1: online viewer → Phase 2: offline viewer → Phase 3: PIREP system).

### 1. Briefing Sync & Offline Access

The briefing data splits into two tiers: a **lightweight payload** synced for offline use (Phase 2+), and **heavy artifacts** available on-demand when online.

#### Offline payload (synced before departure — Phase 2+)

Synced before departure and cached locally. This is everything the app needs to display the full briefing and power the prompting engine without any connectivity:

- **Cross-section data**: Per-model forecast grids at each route point and pressure level — the core dataset for cross-section visualization and the prompting engine
- **Route analyses**: Per-point analysis results (cloud layers, icing zones, wind components, convective risk, etc.)
- **Route advisories**: Evaluated advisory results (severity + detail per evaluator per model)
- **Elevation profile**: Terrain along the route
- **Digest summary**: Synopsis text (synoptic + trend)
- **Route geometry**: Waypoints, route points with coordinates, distances
- **Airport conditions**: Departure/arrival weather (flight category, wind, visibility)

Everything the app can render — cross-section, route graph, advisory dashboard, digest — comes from this payload. The app shows the full briefing experience offline.

#### On-demand via API (online only)

Heavier artifacts that are derived from the full forecast data and don't need to live on the device. The app fetches these from the server when the pilot requests them and connectivity is available:

- **Skew-T plots**: Generated per-waypoint per-model (PNG from server, ~100KB each)
- **Full sounding analysis**: Detailed thermodynamic indices, derived levels per waypoint
- **Model comparison details**: Full 15-metric divergence table
- **LLM digest full text**: The complete AI-generated briefing narrative
- **GRAMET cross-section**: The Autorouter PDF/PNG

These are "tap to load" in the UI — the app shows a placeholder with a download button. If offline, the placeholder says "available when online." If the pilot viewed them while connected (e.g., during pre-flight on Wi-Fi), they get cached locally.

#### Payload size

The offline payload is lightweight — it contains derived analysis results, not raw forecast arrays. Cross-sections, route analyses, advisories, elevation, and digest are all on the order of a few hundred KB total. The multi-MB full sounding data and raw forecasts stay on the server. This makes sync fast even on cellular.

#### Sync behavior

- **Pre-flight sync**: Pull latest offline payload for selected flights (Wi-Fi or cellular, before engine start)
- **Offline storage**: Payload cached locally, full briefing viewable without connectivity
- **On-demand caching**: Heavy artifacts fetched once and cached — subsequent views are local
- **Auto-sync**: Background refresh when on Wi-Fi if departure is within configurable lead time
- **Push notifications**: When the server auto-refreshes a briefing (scheduler detects new model data), send a push notification to the app so the pilot knows updated data is available. This makes the app a natural primary viewer for flight planning on the phone — check your briefing, get notified when it updates, review changes.
- **Briefing viewer**: Native cross-section, advisory dashboard, route graph, and digest — optimized for tablet and phone display

### 2. In-Flight Condition Reporting

The design goal is to **maximize data collected while minimizing pilot effort**. There are two complementary modes:

1. **Proactive prompting**: The app watches the forecast, tracks position along the route, and prompts at transition points with pre-populated observations. The pilot's job reduces to: confirm, edit, or dismiss.
2. **Pilot-initiated PIREPs**: The pilot can file a report at any time — to flag something unexpected, report conditions the app didn't prompt about, or simply because they want to share. The manual report is always one tap away and still comes pre-populated from the forecast for speed.

#### Proactive Forecast-Driven Prompts

The app knows what weather is predicted at each route point (from the synced cross-section data). As the aircraft progresses along the route, the app compares the current position/altitude against the forecast and triggers prompts when conditions are notable:

| Trigger | Prompt | Example |
|---------|--------|---------|
| Entering predicted icing zone | "Forecast shows light icing here at FL065. Confirm?" | Pre-selected: Light icing. Pilot taps ✓ or adjusts to None/Moderate |
| Entering predicted IMC | "Forecast shows BKN at 5500ft. Are you in cloud?" | Pre-selected: IMC, BKN. Pilot taps ✓ or VMC |
| Predicted convective area ahead | "Convective activity forecast 15nm ahead. Seeing anything?" | Pre-selected: TS nearby. Pilot confirms, edits, or "clear skies" |
| Significant altitude change near predicted cloud base | "Climbing through predicted cloud base (6200ft)" | Pre-selected: entering cloud. Pilot confirms or "still VMC" |
| Entering predicted turbulence zone | "Moderate turbulence forecast this segment" | Pre-selected: Moderate. Pilot confirms or adjusts |
| Periodic (no hazard predicted) | "Conditions at FL065 near LFMD?" | Pre-selected: VMC, no icing, smooth. Pilot taps ✓ (1 second) |
| Significant wind shear predicted | "Wind shift forecast: 240/25 → 310/15" | Pre-selected: wind different. Pilot confirms or "as forecast" |

**Key principle: the prompt comes pre-populated from the forecast.** When the forecast says "light icing", the report card appears with "Light icing" already selected. The pilot either:
- **Confirms** (single tap — the common case if the forecast is right)
- **Edits** (tap a different severity — 2 taps)
- **Denies** ("Not present" — single tap, equally valuable data for verification)
- **Dismisses** (swipe away — no observation recorded, pilot is busy)

This means even a "confirm" generates a useful observation, and a "not present" is a high-value negative observation for forecast verification.

#### Flight Session and Prompt Timing

The pilot explicitly starts a flight session via a **"Start Flight"** button. Until the session is started, no prompts fire and no GPS tracking runs (the app is in planning/viewer mode).

Once the session is active, prompts are governed by:

- **Departure/arrival quiet zone**: No prompts while within a configurable radius of the departure or arrival airport (default: 15nm). The pilot is busy with ATC, checklists, and maneuvering. Prompting resumes once en-route. This is computed from the route geometry — the app knows which points are near origin/destination.
- **Non-intrusive**: Prompts appear as a banner at the top or a card sliding in from the side — never a modal, never blocks the view
- **Rate-limited**: No more than one prompt every 5 minutes (configurable). If multiple triggers fire, prioritize by severity (icing > IMC > turbulence > routine)
- **Smart suppression**: If the pilot just reported icing 3 minutes ago, don't prompt again for icing unless conditions changed significantly
- **Dismissal is OK**: A dismissed prompt is not a negative report — it just means the pilot was busy. The app records "prompt dismissed" (useful for UX tuning, not sent as observation)
- **Audio/haptic cue**: Optional gentle chime or haptic when a prompt appears, so the pilot notices without looking

#### Pilot-Initiated PIREPs (Always Available)

The pilot can always file a report proactively via the persistent "Report" button — to flag something the app didn't prompt about, report unexpected conditions, or simply share what they're seeing. The full report card opens with all fields pre-populated from the forecast at the current position, so even a proactive report takes minimal effort. This is recorded with `source = .manual` to distinguish from app-prompted observations.

**Auto-populated from device sensors:**
- Position (lat/lon) → nearest waypoint / route segment
- GPS altitude (with pressure altitude correction option)
- Timestamp
- Ground speed, track

**All fields pre-populated from the forecast at current position:**

| Category | Input | UI |
|----------|-------|----|
| **Flight rules** | VMC / IMC / marginal | 3-button toggle, color-coded |
| **Icing** | None / Trace / Light / Moderate / Severe | 5-button strip (matches PIREP scale) |
| **Turbulence** | None / Light / Moderate / Severe / Extreme | 5-button strip (matches PIREP scale) |
| **Cloud** | Clear / SCT / BKN / OVC | 4-button toggle |
| **Cloud base** | Estimated base in 100s of ft | Scroll wheel, pre-filled from forecast |
| **Precipitation** | None / Rain / Snow / Mixed / TS | Icon buttons |
| **Visibility** | >10km / 5–10km / 1–5km / <1km | 4-button range selector |
| **Wind** | As forecast / Stronger / Weaker / Different direction | Quick comparison against briefing |
| **Temperature** | OAT if available from avionics | Numeric input, optional |
| **Free text** | Short note | Voice-to-text or keyboard, optional |

#### Voice PIREP via Siri Shortcut (Phase 3a)

The pilot can file a PIREP entirely by voice — hands-free, eyes on the sky. Aviation PIREP language is a near-ideal candidate for speech-to-structured-data because the vocabulary is small, standardized, and unambiguous.

**Trigger**: "Hey Siri, FlyFun PIREP" (registered via App Shortcuts / `AppShortcutsProvider`)

**Flow**:
1. Siri activates the app in recording mode
2. App uses `SFSpeechRecognizer` (on-device, works offline since iOS 17) for real-time transcription
3. A pattern-based parser extracts structured fields from the transcript
4. The report card appears pre-filled with voice-extracted values (highlighted to show what was parsed)
5. Pilot confirms with one tap — or edits any field before saving

**Example utterances and parsing**:

| Spoken | Parsed |
|--------|--------|
| "flight level 120" / "FL120" | Altitude: FL120 |
| "eight thousand feet" / "8000'" | Altitude: 8000 ft |
| "IMC" / "in cloud" / "in the clouds" | Flight rules: IMC |
| "light icing" / "trace icing" / "no icing" | Icing: light / trace / none |
| "moderate turbulence" / "light chop" / "smooth" | Turbulence: moderate / light / none |
| "tops at 8000" / "cloud tops eight thousand" | Cloud top: 8000 ft |
| "broken" / "overcast" / "scattered" | Cloud coverage |
| "visibility 3 miles" | Visibility: 1-5km |
| "moderate rain" / "snow" | Precipitation |

**Parser approach**: Keyword/regex matching, not ML. The aviation vocabulary is finite — all valid icing severities, cloud coverages, and altitude formats can be enumerated. Pattern examples:

```swift
// Altitude: "flight level 120", "FL065", "8000 feet"
/(?:flight level|FL)\s*(\d{2,3})/i
/(\d{3,5})\s*(?:feet|ft|foot|')/i

// Icing: "light icing", "no icing"
/(no|none|trace|light|moderate|severe)\s*icing/i

// Cloud tops/bases: "tops at 8000", "bases 4500"
/(tops?|bases?)\s*(?:at\s*)?(\d{3,5})/i
```

**Graceful fallback**: Anything the parser doesn't extract stays at the forecast-prepopulated default. The pilot always sees the result before confirming — voice is an input method, not an auto-submit path. This layers on top of the existing report card UI; it's just a different way to populate the same fields.

#### Passive Data Collection

Beyond explicit observations (prompted or manual), the app silently records data that requires no pilot input at all:

- **Track log**: GPS breadcrumbs at regular intervals (position, altitude, speed, track)
- **Altitude profile**: Continuous — useful for detecting holds, diversions, altitude changes that might indicate weather avoidance
- **Route deviation**: If the pilot deviates significantly from the planned route, that itself is a weather signal (likely circumnavigating something)
- **Timing**: Actual vs planned departure, arrival, and segment times

This passive data is low-cost to collect and valuable for analysis — a route deviation around a convective area is a strong signal even without an explicit report.

### 3. Observation Timeline

A scrollable timeline of all observations made during the flight, shown on the route. Serves as:
- In-flight: a log of what you've reported, ability to amend
- Post-flight: review of the entire flight's weather experience
- Data source for forecast verification

### 4. Online Sharing (Starlink / Cellular)

When connectivity is available, observations stream in real-time:

- **Outbound**: Your observations are pushed to the WeatherBrief server as they're created
- **Inbound**: You receive other pilots' observations along or near your route
- **Display**: Other pilots' reports appear as markers on your route view, color-coded by severity
- **Graceful degradation**: If connection drops, queue locally and sync when reconnected. The app never blocks on network.

### 5. Post-Flight: Forecast Verification

After landing, the app (or web UI) can compare the briefing forecast against actual observations:

- Side-by-side: what was predicted at each point vs what you reported
- Per-model accuracy: which model was closest to reality for each metric
- Aggregate scoring over multiple flights: build a personal track record of model reliability
- Highlight surprises: where forecast and reality diverged significantly

This is a future analysis layer — the companion app's job is to **collect the data**; verification can happen server-side.

## Tech Stack

| Choice | Decision | Rationale |
|--------|----------|-----------|
| **Min iOS** | **18** | Latest SwiftUI, mature SwiftData, new MapKit SwiftUI APIs. No legacy support burden. |
| **UI framework** | **SwiftUI** | Native, modern, declarative. No UIKit wrappers unless absolutely necessary. |
| **Persistence** | **SwiftData** | SwiftUI-native, simpler than Core Data, sufficient for our data model. |
| **Networking** | **URLSession + async/await** | Built-in, no external dependency needed. |
| **Maps** | **MapKit (SwiftUI Map)** | New SwiftUI Map API (iOS 17+), offline tile support, MapKit is sufficient. |
| **Architecture** | **MVVM + Repository pattern** | Natural fit for SwiftUI. Repositories abstract API vs cache — designed for offline from day one. |
| **Cross-section** | **SwiftUI Canvas** | Immediate-mode 2D drawing, equivalent to HTML Canvas. Native rendering, no WKWebView. |
| **Route graph** | **Swift Charts** | Purpose-built for 2D charts, dual axes, extensible. |
| **Auth** | **ASWebAuthenticationSession** | Native Google OAuth, no token-paste friction. |
| **Dependencies** | **RZFlight + RZUtils** via SPM | Airport data, wind models, storage, logging. No third-party heavyweights. |
| **Project location** | **Subdirectory** in flyfun-weather repo | Keep API contracts in sync, single repo for server + client. |

## Architecture

### High-Level Components

```
┌─────────────────────────────────┐
│   WeatherBrief Companion        │  SwiftUI, iOS 18+
│                                 │
│  ┌──────────┐ ┌──────────────┐  │
│  │ Briefing │ │  PIREP       │  │
│  │ Viewer   │ │  Reporter    │  │
│  └────┬─────┘ └──────┬───────┘  │
│       │               │         │
│  ┌────┴───────────────┴───────┐ │
│  │      View Models           │ │  MVVM
│  └────┬───────────────┬───────┘ │
│       │               │         │
│  ┌────┴───────────────┴───────┐ │
│  │      Repositories          │ │  Abstract API vs cache
│  └────┬───────────────┬───────┘ │
│       │               │         │
│  ┌────┴─────┐  ┌──────┴──────┐  │
│  │ SwiftData│  │  API Client │  │
│  │  Store   │  │  + Sync     │  │
│  └──────────┘  └──────┬──────┘  │
│                       │         │
│  ┌──────────┐  ┌──────┴──────┐  │
│  │   GPS    │  │  WebSocket  │  │  (Phase 3)
│  │ Manager  │  │  Client     │  │
│  └──────────┘  └──────┬──────┘  │
└───────────────────────┼─────────┘
                        │  HTTPS / WebSocket
                ┌───────┴────────┐
                │  WeatherBrief  │
                │    Server      │
                │  (FastAPI)     │
                └────────────────┘
```

### Repository Pattern (Offline-Ready from Phase 1)

All data access flows through repositories that abstract over network vs cache. Even in Phase 1 (online), the repository layer exists — Phase 2 adds caching logic without touching UI or ViewModels.

```swift
protocol BriefingRepository {
    func flights() async throws -> [Flight]
    func briefing(flightId: String, timestamp: String) async throws -> BriefingPayload
    func latestPack(flightId: String) async throws -> PackMeta
}

// Phase 1: always hits the API
class OnlineBriefingRepository: BriefingRepository { ... }

// Phase 2: checks SwiftData cache first, falls back to API, caches results
class CachingBriefingRepository: BriefingRepository { ... }
```

### iOS App Layers

| Layer | Responsibility | Key Components |
|-------|---------------|----------------|
| **UI** | SwiftUI views, cockpit-optimized | BriefingViewer, ReportSheet, Timeline, RouteMap |
| **ViewModels** | State management, business logic | FlightListVM, BriefingVM, FlightSessionVM |
| **Repositories** | Data access abstraction (API vs cache) | BriefingRepository, ObservationRepository |
| **Domain** | Observation model, report builder | Observation, ObservationBuilder, FlightSession |
| **Location** | GPS tracking, altitude, route progress. Coarse accuracy is fine — NWP model grids are ~10nm, so sub-nm precision is wasted. Uses `kCLLocationAccuracyKilometer` or reduced-accuracy mode to save battery. | LocationManager (Core Location), RouteTracker |
| **Storage** | Offline-first persistence | SwiftData models, briefing cache, observation queue |
| **Sync** | Server communication, queue management | SyncEngine, APIClient, WebSocketClient |

### Authentication Flow

The app uses native Google OAuth via `ASWebAuthenticationSession` — the same Google login as the web app, no token pasting.

```
┌─────────┐                    ┌─────────────┐                ┌────────┐
│  iOS    │                    │ WeatherBrief│                │ Google │
│  App    │                    │   Server    │                │ OAuth  │
└────┬────┘                    └──────┬──────┘                └───┬────┘
     │                                │                           │
     │  ASWebAuthenticationSession    │                           │
     │  opens /auth/login/google      │                           │
     │  ?platform=ios                 │                           │
     ├───────────────────────────────▶│                           │
     │                                │  redirect to Google       │
     │                                ├──────────────────────────▶│
     │                                │                           │
     │          Google consent screen (in-app browser)            │
     │◀──────────────────────────────────────────────────────────▶│
     │                                │                           │
     │                                │  auth code callback       │
     │                                │◀──────────────────────────┤
     │                                │                           │
     │                                │  exchange code → JWT      │
     │                                │  (same as web flow)       │
     │                                │                           │
     │  redirect to weatherbrief://   │                           │
     │  auth/callback?token=<jwt>     │                           │
     │◀───────────────────────────────┤                           │
     │                                │                           │
     │  Store JWT in Keychain         │                           │
     │  (CodableSecureStorage)        │                           │
     │                                │                           │
     │  All API calls:                │                           │
     │  Authorization: Bearer <jwt>   │                           │
     ├───────────────────────────────▶│                           │
```

**Server-side change required**: Add a `?platform=ios` parameter to `/auth/login/google`. When present, the callback redirects to `weatherbrief://auth/callback?token=<jwt>` instead of the web UI. The JWT is the same one the web uses — the app just receives it via URL scheme instead of a cookie.

**Token refresh**: The JWT has a 7-day expiry. The app stores the expiry and proactively re-authenticates when nearing expiration. On 401 responses, the app shows the login screen.

### Data Models (iOS)

> **Implementation note**: Models shown as `struct` for readability. In practice, persisted models use SwiftData `@Model class` for storage, with separate `Codable` structs for API serialization. `CLLocationCoordinate2D` is not `Codable` — persisted models store `latitude: Double` and `longitude: Double` separately, with a computed `coordinate` property.

#### Phase 1 Models — Briefing Viewer

```swift
/// A flight from the WeatherBrief server
@Model
class Flight {
    let id: String                        // flight slug (e.g. "lfat-lfmd-20260315")
    var routeName: String
    var waypoints: [String]               // ICAO codes
    var departureTime: Date
    var targetDate: String                // "YYYY-MM-DD"
    var targetTimeUTC: Int                // hour 0-23
    var cruiseAltitudeFt: Int
    var flightCeilingFt: Int
    var flightDurationHours: Double
    var isPrivate: Bool
    var autoRefresh: Bool
    var assessment: String?               // "GREEN" / "AMBER" / "RED"

    // Relationship to packs
    var packs: [PackMeta]
}

/// Metadata for a briefing pack (one refresh = one pack)
@Model
class PackMeta {
    let flightId: String
    let fetchTimestamp: String             // ISO 8601 — unique identifier for this pack
    var daysOut: Int
    var isHistorical: Bool
    var hasGramet: Bool
    var hasSkewt: Bool
    var hasDigest: Bool
    var hasAdvisories: Bool
    var assessment: String?
    var assessmentReason: String?
    var modelInitTimes: [String: Double]   // model_key → epoch seconds

    // Cached briefing data (populated on first view, or during offline sync in Phase 2)
    var cachedPayload: BriefingPayload?
}

/// The briefing payload — everything needed to display the full briefing
/// In Phase 1: fetched from /snapshot on demand
/// In Phase 2: pre-synced from /companion endpoint for offline access
@Model
class BriefingPayload {
    // Cross-section data: per-model forecast grids at route points × pressure levels
    // This is the core dataset for cross-section rendering and prompting
    var crossSections: [String: ModelCrossSection]  // model_key → cross-section data

    // Route analysis results
    var routeAnalyses: Data?              // JSON: per-point analysis (cloud, icing, wind, etc.)

    // Advisories
    var advisories: Data?                 // JSON: per-evaluator per-model severity + detail

    // Elevation profile
    var elevationProfile: Data?           // JSON: terrain along route

    // Digest
    var digestSummary: String?            // synopsis text
    var digestSynoptic: String?           // synoptic overview
    var digestTrend: String?              // trend summary

    // Route geometry
    var routePoints: Data?                // JSON: [{lat, lon, distance_nm, ...}]

    // Airport conditions
    var departureConditions: Data?        // JSON: flight category, wind, vis
    var arrivalConditions: Data?
}

/// Cross-section data for a single model — the input to the Canvas renderer
struct ModelCrossSection: Codable {
    var modelKey: String
    var routePoints: [CrossSectionPoint]
    var pressureLevels: [Int]             // hPa levels (e.g. [1000, 925, 850, ...])
}

/// Forecast data at a single route point across all pressure levels
struct CrossSectionPoint: Codable {
    var index: Int
    var latitude: Double
    var longitude: Double
    var distanceNm: Double
    var levels: [String: LevelData]       // pressure_hPa (as string) → data at that level
}

/// Forecast values at a single route point × pressure level
struct LevelData: Codable {
    var temperatureC: Double?
    var dewpointC: Double?
    var relativeHumidity: Double?
    var windSpeedKt: Double?
    var windDirectionDeg: Double?
    var cloudCoverPercent: Double?
    var icingIndex: Double?               // model-specific icing metric
    var verticalVelocity: Double?         // Pa/s or m/s depending on model
    // Additional fields as needed per cross-section layer
}

/// Advisory result for a single evaluator × model
struct AdvisoryResult: Codable {
    var evaluatorId: String
    var evaluatorName: String
    var category: String                  // icing / cloud / turbulence / airport / feasibility
    var modelKey: String
    var severity: String                  // GREEN / AMBER / RED
    var title: String
    var detail: String
}
```

#### Phase 3 Models — Observations & Flight Sessions

```swift
/// A single weather observation made by the pilot
@Model
class Observation {
    let id: UUID
    let timestamp: Date
    var latitude: Double
    var longitude: Double
    var coordinate: CLLocationCoordinate2D {  // computed, not stored
        CLLocationCoordinate2D(latitude: latitude, longitude: longitude)
    }
    let gpsAltitudeFt: Double
    let pressureAltitudeFt: Double?       // if pilot has set altimeter
    let groundSpeedKt: Double?
    let track: Double?

    // How this observation was created
    var source: ObservationSource          // .prompted / .manual / .passive
    var promptTrigger: PromptTrigger?      // what triggered the prompt (if .prompted)
    var forecastAtPoint: ForecastSummary?  // what the forecast predicted here (for verification)

    // Reported conditions (all optional — only notable items)
    var flightRules: FlightRules?         // VMC / marginal / IMC
    var icing: IcingSeverity?             // none → severe (PIREP scale)
    var turbulence: TurbulenceSeverity?   // none → extreme (PIREP scale)
    var cloudCoverage: CloudCoverage?     // CLR / SCT / BKN / OVC
    var cloudBaseFt: Int?                 // estimated
    var precipitation: PrecipitationType? // none / rain / snow / mixed / TS
    var visibility: VisibilityRange?      // >10km, 5–10, 1–5, <1
    var windComparison: WindComparison?   // as forecast / stronger / weaker / different
    var oatCelsius: Double?
    var notes: String?

    // Pilot response to the prompt
    var response: ObservationResponse     // .confirmed / .edited / .denied / .dismissed

    // Sync state
    var syncStatus: SyncStatus            // .local / .synced / .failed
    var isShared: Bool                    // whether pilot opted to share this PIREP

    // Relationships
    var session: FlightSession?           // SwiftData relationship
    var routePointIndex: Int?             // nearest route point at time of report
}

enum ObservationSource: String, Codable {
    case prompted   // app proactively asked based on forecast
    case manual     // pilot initiated via Report button
    case passive    // auto-recorded (track log, deviation, etc.)
}

enum ObservationResponse: String, Codable {
    case confirmed  // pilot agreed with pre-populated forecast
    case edited     // pilot changed one or more fields
    case denied     // pilot said "not present" — negative observation
    case dismissed  // pilot swiped away (busy) — not sent as PIREP
}

enum PromptTrigger: String, Codable {
    case icingZone, imcZone, convectiveArea, turbulenceZone
    case cloudBaseTransition, windShear, periodic
}

/// Snapshot of the forecast at this point — stored with observation for verification
struct ForecastSummary: Codable {
    var predictedFlightRules: FlightRules?
    var predictedIcing: IcingSeverity?
    var predictedTurbulence: TurbulenceSeverity?
    var predictedCloudCoverage: CloudCoverage?
    var predictedCloudBaseFt: Int?
    var predictedWindDir: Int?
    var predictedWindSpeedKt: Int?
    var model: String                      // which model this came from
}

/// A flight session groups observations from engine start to shutdown
@Model
class FlightSession {
    let id: UUID
    let flightId: String                  // WeatherBrief flight ID
    var briefingTimestamp: String?         // which pack was synced
    let startTime: Date
    var endTime: Date?

    // SwiftData relationships
    var observations: [Observation]       // inverse relationship
    var trackPoints: [TrackPoint]         // inverse relationship
}

@Model
class TrackPoint {
    let timestamp: Date
    var latitude: Double
    var longitude: Double
    let altitudeFt: Double
    let groundSpeedKt: Double

    var session: FlightSession?           // inverse relationship
}
```

### Server-Side Extensions (WeatherBrief API)

Server-side work is phased to match the app roadmap. Endpoints listed below are designed upfront to ensure the app architecture leads to the final vision, but implemented only when their phase arrives.

#### Existing Endpoints (Used As-Is)

These already exist and the companion app uses them directly:

| Endpoint | Method | Used In | Purpose |
|----------|--------|---------|---------|
| `/auth/login/google` | GET | Phase 1 | OAuth login (needs `?platform=ios` addition) |
| `/auth/me` | GET | Phase 1 | Current user info |
| `/api/flights` | GET | Phase 1 | List user's flights |
| `/api/flights/{id}` | GET | Phase 1 | Flight details |
| `/api/flights/{id}/packs/latest` | GET | Phase 1 | Latest pack metadata |
| `/api/flights/{id}/packs/{ts}/snapshot` | GET | Phase 1 | Full briefing data |
| `/api/flights/{id}/packs/{ts}/advisories` | GET | Phase 1 | Route advisories |
| `/api/flights/{id}/packs/{ts}/elevation` | GET | Phase 1 | Elevation profile |
| `/api/flights/{id}/packs/{ts}/skewt/{icao}/{model}.png` | GET | Phase 1 | Skew-T image |
| `/api/flights/{id}/packs/{ts}/gramet.png` | GET | Phase 1 | GRAMET image |
| `/api/flights/{id}/packs/freshness` | GET | Phase 2 | Data freshness check |
| `/api/flights/{id}/packs/refresh` | POST | Phase 2 | Trigger briefing refresh |

#### Phase 1 — Auth Extension

| Endpoint | Method | Change |
|----------|--------|--------|
| `/auth/login/google?platform=ios` | GET | When `platform=ios`, callback redirects to `weatherbrief://auth/callback?token=<jwt>` instead of web UI |

#### Phase 2 — Companion Sync Endpoint

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/flights/{id}/packs/{ts}/companion` | GET | Lightweight offline payload (see "Offline Payload" in Core Features) — cross-section data, route analyses, advisories, elevation, digest summary, route geometry, airport conditions. Everything needed for full display + prompting without raw forecasts. |

The companion endpoint is a curated subset of the snapshot — derived analysis results only, not raw forecast arrays. Target payload: a few hundred KB.

#### Phase 3 — Observation & Flight Session Endpoints

**Flight sessions:**

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/flights/{id}/sessions` | POST | Start a flight session (returns session_id) |
| `/api/flights/{id}/sessions/{sid}` | PATCH | End session (set end_time), update track summary |
| `/api/flights/{id}/sessions` | GET | List sessions for a flight |
| `/api/flights/{id}/sessions/{sid}` | GET | Session detail with observation summary |

**Observations (PIREPs):**

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/flights/{id}/observations` | POST | Submit observations (batch). Accepts array of observations with offline UUIDs. Idempotent — re-submitting the same UUID is a no-op. |
| `/api/flights/{id}/observations` | GET | List observations for a flight (own observations, all sessions) |
| `/api/flights/{id}/observations/nearby` | GET | Other pilots' observations near this route. Params: `radius_nm` (default 30), `since` (timestamp). Server performs spatial query against active sessions on overlapping routes. |

**Real-time (Phase 3c):**

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/flights/{id}/sessions/{sid}/live` | WebSocket | Bidirectional during active flight session. Outbound: push observations as created. Inbound: receive nearby PIREPs from other active flights. Server maintains a registry of active sessions and their route geometries for spatial matching. |

**Verification (future):**

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/flights/{id}/verification` | GET | Forecast vs observation comparison (server-side analysis) |

#### Server Data Model

```python
class FlightSession(Base):
    """A flight session from engine start to shutdown."""
    id: UUID
    flight_id: str
    user_id: int
    briefing_timestamp: str | None        # which pack was synced
    start_time: datetime                   # UTC
    end_time: datetime | None
    track_summary: dict | None             # JSON: simplified track for route display
    created_at: datetime

class FlightObservation(Base):
    """Pilot weather observation during flight."""
    id: UUID                               # client-generated, enables idempotent sync
    flight_id: str
    user_id: int
    session_id: UUID
    timestamp: datetime                   # UTC
    latitude: float
    longitude: float
    gps_altitude_ft: float
    pressure_altitude_ft: float | None

    # How this observation was created
    source: str                           # prompted / manual / passive
    prompt_trigger: str | None            # what triggered it (if prompted)
    response: str                         # confirmed / edited / denied / dismissed
    forecast_summary: dict | None         # JSON: what the forecast predicted here

    # Reported conditions
    flight_rules: str | None              # VMC / MVFR / IMC
    icing: str | None                     # none / trace / light / moderate / severe
    turbulence: str | None                # none / light / moderate / severe / extreme
    cloud_coverage: str | None            # CLR / SCT / BKN / OVC
    cloud_base_ft: int | None
    precipitation: str | None
    visibility: str | None
    wind_comparison: str | None
    oat_celsius: float | None
    notes: str | None

    route_point_index: int | None         # snapped to nearest route point
    is_shared: bool                       # whether pilot opted to share this PIREP
    created_at: datetime
```

#### Spatial Query Design (Phase 3c)

For "nearby PIREPs" the server needs to efficiently find observations near a route. At expected scale (hundreds of concurrent flights, not thousands), a simple approach works:

1. **Active session registry**: In-memory dict of `session_id → (flight_id, route_bbox, route_points)`, populated when sessions start, removed when they end
2. **Spatial filter**: For each new observation, iterate active sessions and check if the observation falls within `radius_nm` of any route point (great-circle distance). With hundreds of active sessions, this is sub-millisecond
3. **WebSocket broadcast**: Push matching observations to the relevant session's WebSocket connection
4. **Scaling**: If usage grows beyond ~1000 concurrent sessions, add a spatial index (R-tree via `shapely` or PostGIS). The in-memory approach avoids infrastructure dependencies initially

### Sync Engine (Phase 3a — Detailed Spec)

The sync engine handles the core offline-first challenge:

```
Observation created
       │
       ▼
  ┌─────────┐    yes    ┌──────────┐
  │ Online? ├──────────▶│ POST to  │──▶ Mark .synced
  └────┬────┘           │  server  │
       │ no             └──────────┘
       ▼
  Save locally
  (.local status)
       │
       ▼
  Queue for sync
       │
  ─ ─ ─ ─ ─ ─  (connectivity restored)
       │
       ▼
  Batch POST
  queued observations
       │
       ▼
  Mark .synced
```

- **Queue**: Observations persist in SwiftData with `syncStatus = .local`
- **Retry**: On connectivity change (NWPathMonitor), flush the queue
- **Conflict**: Server is append-only for observations — no conflict resolution needed (each observation is immutable once created, amendments are new observations linked to the original)
- **Backpressure**: If queue grows large (extended offline), batch in chunks of 50

For real-time sharing (Starlink connected pilots):
- **WebSocket** connection kept alive during flight session
- Outbound: observations pushed immediately on creation
- Inbound: server relays nearby observations from other active flights
- Fallback to polling if WebSocket unavailable

## Prompting Engine (Phase 3b — Detailed Spec)

The prompting engine is the intelligence layer that makes the app "smart" — it watches the aircraft's progress along the route, reads ahead in the forecast, and decides when and what to ask the pilot. This requires the offline briefing data from Phase 2 (synced cross-section data at each route point).

### Route Progress Tracker

The engine continuously matches the aircraft's GPS position to the nearest route point and tracks progress as a percentage / distance along the route. This gives it:

- **Current conditions**: What the forecast predicts at the current position and altitude
- **Look-ahead**: What's coming in the next 5–15 minutes of flight (configurable)
- **Transition detection**: When the aircraft crosses from one forecast regime to another (e.g., from "no icing" to "light icing" zone)

```
Route points:   [0] ---- [1] ---- [2] ---- [3] ---- [4] ---- [5]
                                     ✈ (current)     ↓ look-ahead
Forecast:       CLR  CLR  SCT  BKN  BKN  OVC
Icing:          ---  ---  ---  LGT  LGT  MOD
                                    ^^^
                            Trigger: entering icing zone
```

### Trigger Rules

Each trigger type has entry/exit logic and a cooldown:

| Trigger | Entry Condition | Exit / Reset | Cooldown |
|---------|----------------|--------------|----------|
| Icing zone | Forecast icing ≥ Trace at current point | Forecast icing = None for 2+ consecutive points | 10 min |
| IMC zone | Forecast cloud base ≤ cruise altitude, BKN/OVC | Forecast CLR/SCT or cloud base > cruise + 1000ft | 10 min |
| Convective | Convective risk ≥ MODERATE within look-ahead | Risk drops below MODERATE | 15 min |
| Turbulence | CAT or strong vertical motion at cruise | CAT clear, motion calm | 10 min |
| Cloud base transition | Altitude crosses predicted cloud base (±500ft) | Altitude moves >1000ft from cloud base | 10 min |
| Wind shear | Predicted wind change >30° or >15kt between current and next segment | Next segment reached | Once per segment |
| Periodic | No prompt fired in last N minutes and no hazard zones active | After prompt | Configurable (default 15 min) |

### Priority Queue

When multiple triggers fire simultaneously (or within the rate limit window), the engine queues them by priority:

1. **Convective** (safety-critical, time-sensitive)
2. **Icing zone entry** (safety-critical)
3. **IMC entry** (significant)
4. **Turbulence** (significant)
5. **Cloud base transition** (moderate)
6. **Wind shear** (moderate)
7. **Periodic check-in** (low — always deferred if anything else is pending)

Only the highest-priority pending trigger fires. Lower-priority triggers are suppressed if a higher one already covers the same conditions (e.g., entering IMC subsumes a cloud base transition prompt).

### Forecast Lookup

When a trigger fires, the engine reads the synced cross-section data at the current route point and altitude to pre-populate the observation card:

```swift
func forecastAtCurrentPosition() -> ForecastSummary {
    let routeIdx = routeTracker.nearestPointIndex
    let altFt = locationManager.currentAltitudeFt

    // Look up cross-section data at this point and altitude
    let cs = briefingPayload.crossSection(for: selectedModel)
    let icing = cs.icingAt(pointIndex: routeIdx, altitudeFt: altFt)
    let cloud = cs.cloudAt(pointIndex: routeIdx, altitudeFt: altFt)
    let wind = cs.windAt(pointIndex: routeIdx, altitudeFt: altFt)
    // ... etc

    return ForecastSummary(
        predictedIcing: icing,
        predictedCloudCoverage: cloud.coverage,
        predictedCloudBaseFt: cloud.baseFt,
        predictedWindDir: wind.direction,
        predictedWindSpeedKt: wind.speed,
        model: selectedModel
    )
}
```

This is why the cross-section data is part of the lightweight sync payload — it's the source of truth for both visualization and prompting.

## UI Design Principles

### Cockpit Constraints

- **One-handed operation**: All primary actions reachable with right thumb on iPad in landscape
- **Large tap targets**: Minimum 60pt buttons for condition reporting (FAA HIG recommends 44pt; we go larger for turbulence)
- **High contrast**: Support for both dark cockpit (night) and bright cockpit (day VFR) — always high contrast, no subtle grays
- **Minimal reading**: Icons > text. Color-coded severity. Numbers only where essential
- **No typing required**: All condition reports are tap-only. Free text is optional and supports voice-to-text
- **Non-blocking**: No modals that require dismissal. Report sheet slides in, auto-dismisses after save. Never steal focus from the map/briefing view
- **Glanceable**: Current conditions / last report always visible in a status bar without interaction

### Screen Layout — Briefing Viewer (Phase 1, iPad Landscape)

The planning/viewer mode is the default when not in an active flight session. This is what the pilot sees on the ground when reviewing a briefing.

```
┌──────────────────────────────────────────────────────────┐
│  ◀ Flights   LFAT → LFMD  ·  Mar 15  ·  06:00 UTC       │  Nav bar
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌─ Assessment ─────────────────────────────────────┐    │
│  │  🟡 AMBER — Moderate icing forecast FL060-FL080  │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  ┌─ Advisories ─────────────────────────────────────┐    │
│  │  ❄️ Icing         🟡 AMBER  GFS  │  🟡 AMBER  ICON │    │
│  │  ☁️ Cloud Base     🟢 GREEN  GFS  │  🟢 GREEN  ICON │    │
│  │  💨 Turbulence     🟢 GREEN  GFS  │  🟡 AMBER  ICON │    │
│  │  🛬 Crosswind      🟢 GREEN  GFS  │  🟢 GREEN  ICON │    │
│  │  ...expandable per evaluator...                   │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  ┌─ Cross-Section ──────────────────────────────────┐    │
│  │  [Model: GFS ▼]  [Layers: Cloud | Icing | Wind] │    │
│  │                                                   │    │
│  │  FL100 ─┬─────────────────────────────────────── │    │
│  │         │  ░░░░░░▓▓▓▓▓▓▓▓░░░░░░                 │    │
│  │  FL080 ─┤  ░░░▓▓▓▓▓▓▓▓▓▓▓▓░░░░░                │    │
│  │         │  ░▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░                 │    │
│  │  FL060 ─┤  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░                │    │
│  │         │  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░                 │    │
│  │  FL040 ─┤  ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒░░                │    │
│  │         │▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲ terrain        │    │
│  │  GND  ──┴────┬────┬────┬────┬──── distance ───── │    │
│  │           LFAT  LFBE  LSGG  LFMD                 │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  ┌─ Route Graph ────────────────────────────────────┐    │
│  │  Headwind/Tailwind · Temperature · Humidity       │    │
│  │  ┄┄┄╱╲┄┄┄╱╲╲┄┄┄┄┄╱╲┄┄┄┄┄┄┄  (Swift Charts)    │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
├──────────────────────────────────────────────────────────┤
│ [Advisories] [Cross-Section] [Map] [Digest]    [⚙ Settings]│  Tab bar
└──────────────────────────────────────────────────────────┘
```

On **iPhone**, this becomes a single-column scrollable view or swipeable tabs rather than the full dashboard layout.

### Screen Layout — In-Flight Mode (Phase 3, iPad Landscape)

When the pilot taps "Start Flight", the UI switches to flight mode: map-dominant, status bar with live data, and the report/timeline panel.

```
┌──────────────────────────────────────────────────────────┐
│ [Flight: LFAT→LFMD]  [▲ 6500ft]  [GS: 120kt]  [⏱ 1:23] │  Status bar
├────────────────────────────────┬─────────────────────────┤
│                                │                         │
│                                │   Last report: 3min ago │
│        Route Map               │   VMC                   │
│    (current position,          │   No icing              │
│     observations as pins,      │   Light turb            │
│     other pilots' reports)     │                         │
│                                │   ┌─────────────────┐   │
│                                │   │   NEW REPORT    │   │
│                                │   └─────────────────┘   │
│                                │                         │
│                                │   Timeline (scrollable) │
│                                │   12:34 VMC, no icing   │
│                                │   12:15 Light turb      │
│                                │   12:00 Session start   │
├────────────────────────────────┴─────────────────────────┤
│ [Briefing] [Map] [Timeline]                    [Settings]│  Tab bar
└──────────────────────────────────────────────────────────┘
```

### In-Flight Map (Phase 3)

The map is the primary view while flying — it should dominate the screen. The pilot needs to see where they are relative to the route and to weather. The cross-section and briefing tabs are secondary in-flight (but primary on the ground during planning). In Phase 1, the route map is a planning view (no live position); it becomes the live in-flight map in Phase 3.

**Offline map tiles** (Phase 2): The app should cache map tiles along the route corridor before departure. At a minimum, cache tiles at zoom levels covering the route ±30nm at low-to-medium resolution. This ensures the pilot always sees a real map, not a blank grid, even without connectivity. The tile cache can be prepared as part of the pre-flight sync. Implementation options: MapKit's `MKTileOverlay` with a custom local tile cache, or Apple's `DownloadedMap` API if available on iOS 18+.

**Map layers in flight:**
- Route line with advisory coloring (same as web route map — color-coded by metric)
- Current position (prominent aircraft icon with heading)
- Observation pins (own reports + other pilots' if online)
- Forecast hazard zones (icing, convective) as shaded regions derived from cross-section data
- Waypoints with ETA and key conditions (e.g., "LFMD: MVFR, 15kt XW")

### Prompted Report Card (Phase 3b — slides in from side, non-modal)

The prompted card is compact — it focuses on the specific trigger and doesn't show every field. The pilot sees what the forecast predicted, confirms or edits, done.

```
┌─────────────────────────────────────────────┐
│  ❄️ Icing forecast at FL065                  │
│  12:47 UTC · near LFMD · GFS model          │
│                                              │
│  Icing  [None] [Trace] [·Light·] [Mod] [Sev]│
│                          ^^^^^^^^             │
│                       (pre-selected from      │
│                        forecast — highlighted) │
│                                              │
│  [ ✓ Confirm ]   [ ✕ Not present ]   [swipe→]│
└─────────────────────────────────────────────┘
```

- **Confirm**: Saves observation with `response = .confirmed`, forecast was right
- **Not present**: Saves observation with `response = .denied` — equally valuable
- **Edit any button**: Tapping a different severity saves with `response = .edited`
- **Swipe to dismiss**: No observation recorded, `response = .dismissed`

### Full Report Sheet (Phase 3a — manual, slides up from bottom)

When the pilot taps "Report" manually, the full sheet appears with all fields pre-populated from the forecast at the current position:

```
┌──────────────────────────────────────────────────────────┐
│  Report at 12:47 UTC  ·  N45.12 E6.34  ·  FL065         │
│  Forecast source: GFS                                    │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Flight Rules    [·VMC·]  [ MVFR ]  [ IMC ]              │
│                                                          │
│  Icing     [None] [Trace] [·Light·] [Mod]  [Sev]        │
│                                                          │
│  Turbulence [·None·] [Light] [Mod]  [Sev]  [Extr]       │
│                                                          │
│  Cloud     [ CLR ]  [ SCT ]  [·BKN·]  [ OVC ]           │
│                  Base: [  ▼ 4500 ft ▲  ]                 │
│                                                          │
│  Visibility [·>10·]  [ 5-10 ]  [ 1-5 ]  [ <1 ]          │
│                                                          │
│  Precip    [·—·]  [ 🌧 ]  [ 🌨 ]  [ Mix ]  [ ⛈ ]       │
│                                                          │
│  Wind vs forecast  [·✓ OK·]  [ ↑ Stronger ]  [ ↻ Diff ] │
│                                                          │
│  [🎤 Add note...]                                        │
│                                                          │
│          [ ✕ Cancel ]    [ ✓ All correct ]    [ ✓ Save ] │
└──────────────────────────────────────────────────────────┘
```

Note the **"All correct"** button — if the forecast is spot-on, the pilot taps one button to confirm everything at once. This is the lowest-effort path for the most common case (forecast was right).

## Existing Library Reuse

| Library | What to Reuse |
|---------|---------------|
| **RZFlight** | `KnownAirports` for spatial queries, `RunwayWindModel` for wind display, `Briefing` models for NOTAM display, airport data |
| **RZUtilsSwift** | `UserStorage` / `CodableSecureStorage` for preferences and auth tokens, `RZSLog` for logging, custom `Dimension` types (UnitSpeed fpm/kt) |
| **RZUtilsSwiftUI** | `DynamicStack` for adaptive layout, `Color` extensions for hex themes |
| **RZData** | `DataFrame` if we need local data analysis (e.g., observation stats) |

## Implementation Phases

The app is built in three major phases, each delivering standalone value. The architecture (MVVM + Repository pattern, SwiftData from day one) is designed so that each phase extends the previous without rewrites.

The ultimate goal is the PIREP system (Phase 3) — the two-way weather conversation that makes this app uniquely valuable. Phases 1 and 2 build the foundation and deliver value along the way.

### Phase 1 — Online Briefing Viewer

**Goal**: A native mobile briefing viewer that replaces checking the web on a phone. The pilot opens the app, sees their flights, taps one, and views the full briefing natively.

**Server-side work**: Add `?platform=ios` to OAuth callback (redirect to custom URL scheme).

**App work**:

- **Foundation**
  - Xcode project with SwiftUI App lifecycle, SwiftData
  - SPM dependencies: RZFlight, RZUtils
  - API client (URLSession + async/await, JWT auth header)
  - Repository layer (online-only initially, but interface designed for caching)
  - SwiftData models for flights and briefing data (used as cache, prepares for Phase 2)
  - Error handling and loading states

- **Authentication**
  - Google OAuth via ASWebAuthenticationSession
  - `weatherbrief://auth/callback` URL scheme handling
  - JWT storage in Keychain (CodableSecureStorage)
  - Auto-refresh on approaching expiry, re-auth on 401

- **Flight list**
  - List user's flights from API
  - Flight card: route name, waypoints, departure time, assessment badge (GREEN/AMBER/RED)
  - Pull-to-refresh
  - Navigate to briefing on tap

- **Briefing display** (read-only, all data from existing API endpoints)
  - **Advisory dashboard**: severity badges per evaluator per model, expandable details. Text-based, straightforward to implement first.
  - **Digest summary**: synopsis text, synoptic + trend
  - **Airport conditions**: departure/arrival flight category, wind, visibility
  - **Route map**: MapKit SwiftUI Map, route line with waypoints, advisory coloring per segment
  - **Cross-section renderer** (SwiftUI Canvas): native rendering of forecast data. Built incrementally — start with cloud coverage + icing + wind barbs + temperature, add remaining layers (humidity, convective, visibility, pressure) iteratively. See "Cross-Section Renderer" section for details.
  - **Route graph** (Swift Charts): scalar metrics along route (wind components, temperature, humidity, etc.)
  - **On-demand artifacts**: Skew-T and GRAMET loaded as images from API (tap-to-load with placeholder)

- **Layout**
  - iPhone + iPad adaptive layout (DynamicStack from RZUtilsSwiftUI)
  - iPad: side-by-side briefing panels. iPhone: stacked/tabbed

#### Cross-Section Renderer (Phase 1 — Incremental)

The web cross-section renderer has ~16 layers across 8 groups. The native renderer is built incrementally within Phase 1, starting with the most valuable layers:

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

The renderer uses the same cross-section data structure as the web — the data extraction logic (TypeScript `extractVizData`) is ported to Swift. Each layer is a composable `CrossSectionLayer` protocol conformance drawn on the Canvas.

### Phase 2 — Offline Briefing Viewer

**Goal**: Pilots sync briefing data before departure and view the full briefing in flight without connectivity. Push notifications alert when briefings refresh.

**Server-side work**: Build the `/companion` lightweight payload endpoint.

**App work**:

- **Companion sync endpoint consumption**
  - Fetch lightweight payload (cross-section data, route analyses, advisories, elevation, digest, route geometry, airport conditions)
  - Parse into SwiftData models

- **Offline storage**
  - SwiftData persistence for all briefing data
  - `CachingBriefingRepository` replaces `OnlineBriefingRepository` — checks cache first, falls back to API, caches results
  - On-demand artifact caching: Skew-T and GRAMET images cached once viewed on Wi-Fi
  - Cache management: auto-expire old briefings, manual clear

- **Pre-flight sync**
  - "Sync for offline" button per flight (downloads companion payload + map tiles)
  - Sync status indicator (synced / needs update / syncing)
  - Background sync: auto-refresh on Wi-Fi when departure is within configurable lead time

- **Push notifications**
  - APNS registration
  - Server-side: push notification when auto-refresh produces new briefing pack
  - Notification taps open the updated briefing
  - Badge count for unread briefing updates

- **Offline map tiles**
  - Cache MapKit tiles along route corridor (±30nm, low-to-medium zoom) as part of pre-flight sync

### Phase 3 — In-Flight PIREP System

**Goal**: The killer feature — pilots report weather conditions during flight, building a two-way weather conversation. PIREPs are stored locally, synced when online, and shared with other pilots in real-time when connectivity allows.

This phase has three sub-phases, each delivering incremental value:

#### Phase 3a — Manual PIREP Filing + Offline Sync

The pilot can file PIREPs at any time during flight. Reports are stored locally and synced to the server when connectivity is available.

**Server-side work**: Build flight session and observation endpoints (POST/GET). Observation storage in DB.

**App work**:

- **Flight session lifecycle**
  - "Start Flight" button transitions the app from planning mode to flight mode
  - "End Flight" on landing (or auto-detect via prolonged ground speed = 0)
  - Session persisted in SwiftData with start/end times

- **GPS tracking**
  - Core Location with `kCLLocationAccuracyKilometer` (battery-friendly, sufficient for NWP grid resolution)
  - Background location mode (justified: PIREP location reporting)
  - Current position, altitude, ground speed, track
  - Route progress tracking: snap GPS position to nearest route point

- **Manual report UI**
  - Persistent "Report" button always visible during flight session
  - Full report sheet (all fields from "Full Report Sheet" UI mockup)
  - All fields pre-populated from forecast at current position (from synced cross-section data)
  - "All correct" one-tap confirmation
  - Voice-to-text for optional free-text notes
  - Observation saved to SwiftData with `source = .manual`

- **Observation timeline**
  - Scrollable list of all observations made during the flight
  - Ability to amend (creates a new linked observation)
  - Shown in the map view as pins on the route

- **Offline sync engine**
  - Observations saved locally with `syncStatus = .local`
  - On connectivity (NWPathMonitor), batch POST queued observations to server
  - Idempotent sync: client-generated UUIDs, server deduplicates
  - Retry with exponential backoff on failure
  - Batch in chunks of 50 if queue is large (extended offline)
  - Mark `.synced` on success

- **Voice PIREP (Siri shortcut)**
  - Register App Shortcut "FlyFun PIREP" via `AppShortcutsProvider` + `AppIntent`
  - `SFSpeechRecognizer` for on-device transcription (works offline)
  - `PIREPParser` module: regex/keyword extraction for altitude, icing, turbulence, cloud, visibility, precipitation, flight rules
  - Voice-extracted values populate the same report card UI — highlighted fields show what was parsed from voice vs forecast default
  - Observation saved with `source = .manual` (same as tap-based manual reports)

- **Passive data collection**
  - Track log: GPS breadcrumbs at 30–60 second intervals
  - Route deviation detection: flag significant deviations from planned route (weather avoidance signal)

#### Phase 3b — Proactive Prompting Engine

The app watches the forecast, tracks position along the route, and prompts at transition points with pre-populated observations.

**App work**:

- **Route progress tracker**
  - Continuously match GPS position to nearest route point
  - Look-ahead: forecast conditions 5–15 minutes ahead
  - Transition detection: entering/exiting forecast weather zones

- **Trigger rules engine**
  - Icing zone entry/exit, IMC entry, convective area, turbulence, cloud base transition, wind shear, periodic check-in
  - Each trigger with entry condition, exit/reset condition, cooldown timer
  - Priority queue: convective > icing > IMC > turbulence > cloud base > wind shear > periodic
  - Rate limiting: max one prompt per 5 minutes (configurable)

- **Prompted report card UI**
  - Compact, non-modal card (slides in from side)
  - Shows only the triggered condition, pre-selected from forecast
  - Confirm (1 tap) / Edit (2 taps) / Deny "Not present" (1 tap) / Dismiss (swipe)
  - Auto-dismiss after 30 seconds if no interaction

- **Smart suppression**
  - Departure/arrival quiet zone (no prompts within 15nm of origin/destination)
  - Duplicate suppression: no re-prompt for same condition if recently reported
  - Higher-priority trigger subsumes lower (e.g., IMC entry subsumes cloud base transition)

- **Audio/haptic cue**
  - Optional gentle chime or haptic when prompt appears

#### Phase 3c — Live PIREP Sharing

When online (Starlink, cellular), observations stream in real-time and pilots receive nearby PIREPs from other flights.

**Server-side work**: WebSocket endpoint, active session registry, spatial broadcast.

**App work**:

- **WebSocket connection**
  - Established when flight session starts and connectivity is available
  - Outbound: push observations immediately on creation
  - Inbound: receive nearby PIREPs from other active flights
  - Graceful reconnection on connectivity changes
  - Falls back to REST polling if WebSocket unavailable

- **Nearby PIREP display**
  - Other pilots' PIREPs shown as markers on the route map
  - Color-coded by severity (same color scale as advisories)
  - Tap for details: who reported what, when, where
  - Filter by recency and distance

- **Privacy controls**
  - Opt-in sharing: `is_shared` flag per observation
  - Default: shared (but configurable in settings)
  - Position broadcasting consent at session start

### Future Phases (Not Scoped Yet)

These are ideas from the original brainstorm, to be designed when Phase 3 is complete:

- **Forecast verification**: Server-side comparison engine (forecast vs observations), per-model accuracy scoring, verification dashboard
- **Apple Watch**: Quick-report complication (good/marginal/bad severity) for single-pilot operations
- **Turbulence from accelerometer**: Passive turbulence detection via iPad accelerometer (academic precedent exists)
- **ForeFlight / SkyDemon integration**: URL scheme or share sheet for route import/export
- **PIREP format compliance**: Standard PIREP string formatting for submission to official channels (FAA/EUROCONTROL)

## Decisions Made

- **Native SwiftUI**: No WKWebView for visualization. Cross-section rendered natively via SwiftUI Canvas, route graph via Swift Charts. Modern SwiftUI only (iOS 18+).
- **No third-party heavyweights**: Apple-native SDKs only (URLSession, MapKit, SwiftData, Core Location). RZFlight and RZUtils via SPM for aviation-specific and utility code.
- **Google OAuth natively**: ASWebAuthenticationSession, no API token paste friction. Same login flow as the web.
- **Repository pattern from day one**: Even Phase 1 (online-only) uses repositories, so Phase 2 adds caching without touching UI.
- **Subdirectory**: iOS project lives in the flyfun-weather repo alongside the server code.
- **3-phase roadmap**: (1) Online viewer → (2) Offline viewer → (3) PIREP system (3a manual + sync, 3b prompting, 3c live sharing).
- **Real-time architecture**: WebSocket during active flight sessions for bidirectional PIREP flow. APNS for background notifications (briefing refreshes). Simple in-memory spatial matching on server, upgrade to PostGIS/R-tree if scale demands.
- **Privacy**: PIREP sharing is opt-in with `is_shared` flag. Position broadcasting requires consent at session start.

## Open Questions

- **Cross-section data documentation**: The snapshot data format that feeds the web cross-section renderer needs to be documented so the Swift data extraction logic can be ported accurately. The `extractVizData` TypeScript function and its input/output shapes are the key reference.
- **Avionics integration**: Some panels (G1000, Avidyne) expose data ports — OAT, pressure altitude, winds aloft. Worth investigating for Phase 3+ but not essential.
- **iPhone vs iPad UX split**: The app works on both, but the in-flight PIREP UI is primarily designed for iPad. The iPhone experience should focus on planning and notifications. Need to define which Phase 3 features are iPad-only vs universal.
- **Flight creation in app**: Phase 1 is read-only (view existing flights created on the web). Should the app allow creating flights directly? This adds route input UI complexity but makes the app self-sufficient.
- **Briefing refresh from app**: Should the app trigger briefing refreshes, or only consume server-triggered refreshes? The API endpoint exists (`POST /packs/refresh`) — it's a question of whether the mobile UX should include this.
- **Track log upload**: Phase 3a collects track logs. Should these be uploaded to the server for post-flight analysis, or kept local-only? Upload enables route deviation analysis and forecast verification on the server side.
- **PIREP format compliance**: Should observations be formatted as standard PIREP strings for potential submission to official channels (FAA/EUROCONTROL)? Deferred to post-Phase 3.

## References

- WeatherBrief architecture: [architecture.md](./architecture.md)
- Data models: [data-models.md](./data-models.md)
- Advisory system: [advisories.md](./advisories.md)
- RZFlight Swift: see `list_libraries` → rzflight
- RZUtils Swift: see `list_libraries` → rzutils
