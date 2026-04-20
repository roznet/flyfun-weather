# iOS App Data Models

> Swift `@Model` classes for SwiftData persistence + `Codable` structs for API serialization

## Implementation Note

Models shown as `struct` for readability. In practice, persisted models use SwiftData `@Model class`, with separate `Codable` structs for API serialization. `CLLocationCoordinate2D` is not `Codable` — persisted models store `latitude: Double` and `longitude: Double` separately, with a computed `coordinate` property.

## Phase 1 — Briefing Viewer

```swift
/// A flight from the WeatherBrief server
@Model
class Flight {
    let id: String                        // slug (e.g. "lfat-lfmd-20260315")
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

    var packs: [PackMeta]
}

/// Metadata for a briefing pack (one refresh = one pack)
@Model
class PackMeta {
    let flightId: String
    let fetchTimestamp: String             // ISO 8601 — unique id
    var daysOut: Int
    var isHistorical: Bool
    var hasGramet: Bool
    var hasSkewt: Bool
    var hasDigest: Bool
    var hasAdvisories: Bool
    var assessment: String?
    var assessmentReason: String?
    var modelInitTimes: [String: Double]   // model_key → epoch seconds

    // Phase 1: fetched from /snapshot on demand
    // Phase 2: pre-synced from /companion for offline
    var cachedPayload: BriefingPayload?
}

/// Everything needed to display the full briefing
@Model
class BriefingPayload {
    var crossSections: [String: ModelCrossSection]  // model_key → cross-section
    var routeAnalyses: Data?              // JSON: per-point analysis
    var advisories: Data?                 // JSON: per-evaluator per-model severity
    var elevationProfile: Data?           // JSON: terrain
    var digestSummary: String?
    var digestSynoptic: String?
    var digestTrend: String?
    var routePoints: Data?                // JSON: [{lat, lon, distance_nm}]
    var departureConditions: Data?        // JSON
    var arrivalConditions: Data?
}

/// Input to the Canvas renderer
struct ModelCrossSection: Codable {
    var modelKey: String
    var routePoints: [CrossSectionPoint]
    var pressureLevels: [Int]             // hPa (e.g. [1000, 925, 850, ...])
}

struct CrossSectionPoint: Codable {
    var index: Int
    var latitude: Double
    var longitude: Double
    var distanceNm: Double
    var levels: [String: LevelData]       // pressure_hPa (string) → data
}

struct LevelData: Codable {
    var temperatureC: Double?
    var dewpointC: Double?
    var relativeHumidity: Double?
    var windSpeedKt: Double?
    var windDirectionDeg: Double?
    var cloudCoverPercent: Double?
    var icingIndex: Double?
    var verticalVelocity: Double?
}

struct AdvisoryResult: Codable {
    var evaluatorId: String
    var evaluatorName: String
    var category: String                  // icing/cloud/turbulence/airport/feasibility
    var modelKey: String
    var severity: String                  // GREEN/AMBER/RED
    var title: String
    var detail: String
}
```

## Phase 3 — Observations & Flight Sessions

PIREPs are **first-class entities**. An observation can exist standalone (no flight, no session) or be linked to a flight session for forecast verification.

```swift
/// A single weather observation (PIREP). Standalone or flight-linked.
@Model
class Observation {
    let id: UUID
    let timestamp: Date
    var latitude: Double
    var longitude: Double
    var coordinate: CLLocationCoordinate2D {  // computed
        CLLocationCoordinate2D(latitude: latitude, longitude: longitude)
    }
    let gpsAltitudeFt: Double?            // nil for ground-based
    let pressureAltitudeFt: Double?
    let groundSpeedKt: Double?
    let track: Double?

    var airportIcao: String?              // for ground/airport-referenced PIREPs

    var source: ObservationSource          // .prompted/.manual/.passive/.standalone
    var promptTrigger: PromptTrigger?      // if .prompted
    var forecastAtPoint: ForecastSummary?  // nil for standalone

    // Reported conditions (all optional — only notable items)
    var flightRules: FlightRules?         // VMC/marginal/IMC
    var icing: IcingSeverity?             // PIREP scale none→severe
    var turbulence: TurbulenceSeverity?   // PIREP scale none→extreme
    var cloudCoverage: CloudCoverage?     // CLR/SCT/BKN/OVC
    var cloudBaseFt: Int?
    var cloudTopFt: Int?
    var precipitation: PrecipitationType?
    var visibility: VisibilityRange?
    var windComparison: WindComparison?
    var oatCelsius: Double?
    var notes: String?

    var response: ObservationResponse?    // nil for standalone

    var syncStatus: SyncStatus            // .local/.synced/.failed
    var isShared: Bool

    var session: FlightSession?           // nil for standalone
    var flightId: String?                 // nil for standalone
    var routePointIndex: Int?             // nearest route point at report time
}

enum ObservationSource: String, Codable {
    case prompted, manual, passive, standalone
}

enum ObservationResponse: String, Codable {
    case confirmed, edited, denied, dismissed
}

enum PromptTrigger: String, Codable {
    case icingZone, imcZone, convectiveArea, turbulenceZone
    case cloudBaseTransition, windShear, periodic
}

/// Snapshot of forecast at observation point — stored for verification
struct ForecastSummary: Codable {
    var predictedFlightRules: FlightRules?
    var predictedIcing: IcingSeverity?
    var predictedTurbulence: TurbulenceSeverity?
    var predictedCloudCoverage: CloudCoverage?
    var predictedCloudBaseFt: Int?
    var predictedWindDir: Int?
    var predictedWindSpeedKt: Int?
    var model: String
}

/// Groups observations from engine start to shutdown
@Model
class FlightSession {
    let id: UUID
    let flightId: String
    var briefingTimestamp: String?         // which pack was synced
    let startTime: Date
    var endTime: Date?

    var observations: [Observation]       // inverse
    var trackPoints: [TrackPoint]
}

@Model
class TrackPoint {
    let timestamp: Date
    var latitude: Double
    var longitude: Double
    let altitudeFt: Double
    let groundSpeedKt: Double

    var session: FlightSession?
}
```

## Key Choices

- **First-class PIREPs** — `flightId` and `session` are optional; standalone observations work without any flight context
- **Client UUIDs** — `Observation.id: UUID` is client-generated so offline sync is idempotent
- **Forecast snapshot embedded** — `forecastAtPoint` stored with the observation so verification survives later model refreshes
- **Append-only** — observations are immutable once created; amendments are new observations

## References

- [Server API](./ios-app-server-api.md) — matching server-side `Observation`, `FlightSession` tables
- [Sync & Prompting](./ios-app-sync-prompting.md) — how `syncStatus` transitions
