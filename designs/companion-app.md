# WeatherBrief Companion App

> iOS/iPad app for in-flight condition reporting, offline briefing access, and collaborative PIREPs

## Vision

The WeatherBrief Companion App turns every flight into a two-way weather conversation: before departure, you sync your briefing and carry it offline; in flight, you mark actual conditions with a few taps; after landing, those observations feed back into forecast verification and (if online) stream as PIREPs to other pilots.

The app is designed for the cockpit: one-handed operation, large tap targets, GPS-prepopulated fields, and a UI that assumes the pilot is busy. It works fully offline (most GA pilots have no connectivity) but lights up with real-time sharing when a Starlink or cellular connection is available.

On the ground, the app doubles as a primary briefing viewer — with push notifications when briefings auto-refresh, native cross-section rendering, and a mobile-optimized UI that's better than the web on a phone or tablet.

Long-term, the observation data creates a feedback loop: compare what was forecast against what actually happened, learn which models are most reliable for which conditions, and build a community-sourced real-time weather picture that complements official PIREPs.

## Core Features

### 1. Briefing Sync & Offline Access

The briefing data splits into two tiers: a **lightweight payload** synced for offline use, and **heavy artifacts** available on-demand when online.

#### Offline payload (always synced)

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

## Architecture

### High-Level Components

```
┌─────────────────────────────┐
│   WeatherBrief Companion    │  SwiftUI, iOS 17+
│                             │
│  ┌──────────┐ ┌──────────┐  │
│  │ Briefing │ │ Reporter │  │
│  │ Viewer   │ │   UI     │  │
│  └────┬─────┘ └────┬─────┘  │
│       │             │        │
│  ┌────┴─────────────┴─────┐  │
│  │    Local Data Store    │  │  Core Data / SwiftData
│  │  (briefings + obs)     │  │
│  └────┬─────────────┬─────┘  │
│       │             │        │
│  ┌────┴─────┐ ┌─────┴─────┐  │
│  │   GPS    │ │   Sync    │  │
│  │ Manager  │ │  Engine   │  │
│  └──────────┘ └─────┬─────┘  │
└─────────────────────┼────────┘
                      │  HTTPS / WebSocket
              ┌───────┴────────┐
              │  WeatherBrief  │
              │    Server      │
              │  (FastAPI)     │
              └────────────────┘
```

### iOS App Layers

| Layer | Responsibility | Key Components |
|-------|---------------|----------------|
| **UI** | SwiftUI views, cockpit-optimized | BriefingViewer, ReportSheet, Timeline, RouteMap |
| **Domain** | Observation model, report builder | Observation, ObservationBuilder, FlightSession |
| **Location** | GPS tracking, altitude, route progress. Coarse accuracy is fine — NWP model grids are ~10nm, so sub-nm precision is wasted. Uses `kCLLocationAccuracyKilometer` or reduced-accuracy mode to save battery. | LocationManager (Core Location), RouteTracker |
| **Storage** | Offline-first persistence | SwiftData models, briefing cache, observation queue |
| **Sync** | Server communication, conflict resolution | SyncEngine, APIClient, WebSocket (optional) |

### Data Models (iOS)

```swift
/// A single weather observation made by the pilot
struct Observation: Codable, Identifiable {
    let id: UUID
    let timestamp: Date
    let coordinate: CLLocationCoordinate2D
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
    var flightSessionId: UUID
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
struct FlightSession: Codable, Identifiable {
    let id: UUID
    let flightId: String                  // WeatherBrief flight ID
    var briefingTimestamp: String?         // which pack was synced
    let startTime: Date
    var endTime: Date?
    var observations: [Observation]

    // Route tracking
    var trackLog: [TrackPoint]            // periodic GPS breadcrumbs
}

struct TrackPoint: Codable {
    let timestamp: Date
    let coordinate: CLLocationCoordinate2D
    let altitudeFt: Double
    let groundSpeedKt: Double
}
```

### Server-Side Extensions (WeatherBrief API)

New endpoints needed on the FastAPI backend:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/flights/{id}/packs/{ts}/companion` | GET | Offline payload: cross-sections, route analyses, advisories, elevation, digest summary, route geometry, airport conditions — everything needed for full offline display + prompting |
| `/api/flights/{id}/observations` | GET | List observations for a flight |
| `/api/flights/{id}/observations` | POST | Submit observations (batch) |
| `/api/flights/{id}/observations/stream` | WebSocket | Real-time observation push/pull |
| `/api/flights/{id}/observations/nearby` | GET | Other pilots' observations near this route |
| `/api/flights/{id}/verification` | GET | Forecast vs observation comparison |

New data model on the server:

```python
class FlightObservation(Base):
    """Pilot weather observation during flight."""
    id: UUID
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
    created_at: datetime
```

### Sync Engine

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

## Prompting Engine

The prompting engine is the intelligence layer that makes the app "smart" — it watches the aircraft's progress along the route, reads ahead in the forecast, and decides when and what to ask the pilot.

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

### Screen Layout (iPad Landscape)

```
┌──────────────────────────────────────────────────────────┐
│ [Flight: LFAT→LFMD]  [▲ 6500ft]  [GS: 120kt]  [⏱ 1:23] │  Status bar
├────────────────────────────────┬─────────────────────────┤
│                                │                         │
│                                │   Last report: 3min ago │
│        Route Map               │   ✅ VMC               │
│    (current position,          │   ✅ No icing           │
│     observations as pins,      │   ✅ Light turb         │
│     other pilots' reports)     │                         │
│                                │   ┌─────────────────┐   │
│                                │   │   NEW REPORT ▶  │   │
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

### In-Flight Map

The map is the primary view while flying — it should dominate the screen. The pilot needs to see where they are relative to the route and to weather. The cross-section and briefing tabs are secondary in-flight (but primary on the ground during planning).

**Offline map tiles**: The app should cache map tiles along the route corridor before departure. MapKit supports offline map data via `MKLocalSearch` and tile pre-fetching. At a minimum, cache tiles at zoom levels covering the route ± 30nm at low-to-medium resolution. This ensures the pilot always sees a real map, not a blank grid, even without connectivity. The tile cache can be prepared as part of the pre-flight sync.

**Map layers in flight:**
- Route line with advisory coloring (same as web route map — color-coded by metric)
- Current position (prominent aircraft icon with heading)
- Observation pins (own reports + other pilots' if online)
- Forecast hazard zones (icing, convective) as shaded regions derived from cross-section data
- Waypoints with ETA and key conditions (e.g., "LFMD: MVFR, 15kt XW")

### Prompted Report Card (slides in from side, non-modal)

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

### Full Report Sheet (manual, slides up from bottom)

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

### Phase 1 — Offline Briefing Viewer + Planning Tool
- Auth: login with WeatherBrief account (reuse Google OAuth token or API token)
- Flight list sync from server
- Server-side `/companion` endpoint for lightweight payload
- Briefing payload download and offline cache
- Native cross-section renderer (Core Graphics)
- Read-only briefing display (cross-section, advisories, route graph, digest summary)
- On-demand heavy artifact loading (Skew-T, sounding details) when online
- Route map with MapKit, offline tile caching along route corridor
- Push notifications when server auto-refreshes a briefing
- Foundation: SwiftData models, API client, background sync
- iPhone + iPad adaptive layout

### Phase 2 — In-Flight Reporting + Prompting Engine
- "Start Flight" button to enter flight session mode
- GPS tracking with Core Location (coarse accuracy, background mode)
- Route progress tracker (position → route point matching)
- Flight session lifecycle (start / stop)
- Prompting engine: trigger rules, forecast lookup, priority queue, departure/arrival quiet zones
- Prompted report card UI (compact, non-modal, confirm/deny/edit)
- Full manual report sheet (all fields, pre-populated from forecast)
- "All correct" one-tap confirmation
- Local observation storage with source/response metadata
- Observation timeline view
- Passive data collection: track log, altitude profile, route deviation detection

### Phase 3 — Online Sync
- Observation upload to server (batch POST)
- Offline queue with retry
- Server-side observation storage and API
- Basic observation display on web briefing

### Phase 4 — Real-Time Sharing
- WebSocket connection for live observation relay
- Display other pilots' observations on the route map
- Nearby observation queries
- Push notification for significant reports near your route

### Phase 5 — Forecast Verification
- Server-side comparison engine (forecast vs observations)
- Per-model accuracy scoring
- Verification dashboard (web + app)
- Historical accuracy trends

## Open Questions

- **PIREP format compliance**: Should observations be formatted as standard PIREP strings for potential submission to official channels (FAA/EUROCONTROL)? This would increase the value but adds format constraints.
- **Avionics integration**: Some panels (G1000, Avidyne) expose data ports — OAT, pressure altitude, winds aloft. Worth investigating for Phase 2+ but not essential for MVP.
- **Track log granularity**: With coarse GPS, every 30–60 seconds is likely sufficient for route progress tracking. Finer for the map display can use the reduced-accuracy updates iOS provides.
- **Privacy**: Observation sharing should be opt-in. Some pilots may want to log conditions privately without broadcasting position. Need clear consent model.
- **Apple Watch**: A Watch complication for quick reports (just severity: good/marginal/bad) could be useful for single-pilot operations where reaching the iPad is awkward.
- **Turbulence from accelerometer**: The iPad accelerometer can detect turbulence passively — no pilot input needed. Academic precedent exists for smartphone-based turbulence detection. Even a simple variance-over-30-seconds metric could auto-generate turbulence observations. Worth prototyping.
- **ForeFlight / SkyDemon integration**: Many GA pilots already have an EFB running. URL scheme or share sheet integration to import/export routes could make adoption easier — complement rather than compete.
- **iPhone vs iPad**: The app should work on both. On iPhone it becomes the primary planning/notification viewer (check briefing on the go, get refresh notifications). On iPad it's the in-flight companion. UI should adapt — phone gets a more compact single-column layout.

## References

- WeatherBrief architecture: [architecture.md](./architecture.md)
- Data models: [data-models.md](./data-models.md)
- Advisory system: [advisories.md](./advisories.md)
- RZFlight Swift: see `list_libraries` → rzflight
- RZUtils Swift: see `list_libraries` → rzutils
