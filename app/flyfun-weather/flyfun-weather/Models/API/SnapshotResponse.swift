import Foundation

/// Briefing snapshot — route info, waypoint analyses, observations.
/// We decode only the fields needed for display.
struct SnapshotResponse: Codable, Sendable {
    let route: RouteConfig
    let targetDate: String
    let daysOut: Int
    let departureTime: String?
    let analyses: [WaypointAnalysis]?
    let routeObservations: RouteObservations?
    /// Weather-based divert candidates (D-2 inward, `compute_alternates` pref).
    /// Present only on marginal D-0/D-1/D-2 packs; nil otherwise. Mirrors
    /// `models/alternates.py` / `designs/future/alternates.md`.
    let alternates: RouteAlternates?
}

struct RouteConfig: Codable, Sendable {
    let name: String
    let waypoints: [Waypoint]
    let cruiseAltitudeFt: Int
    let flightCeilingFt: Int
    let flightDurationHours: Double
}

struct Waypoint: Codable, Sendable, Identifiable {
    let icao: String
    let name: String
    let lat: Double
    let lon: Double

    var id: String { icao }
}

struct WaypointAnalysis: Codable, Sendable {
    let waypoint: Waypoint
    let targetTime: String?
    let windComponents: [String: WindComponent]?
    let sounding: [String: SoundingAnalysisSummary]?
}

struct WindComponent: Codable, Sendable {
    let windSpeedKt: Double
    let windDirectionDeg: Double
    let trackDeg: Double
    let headwindKt: Double
    let crosswindKt: Double
}

/// Minimal sounding fields needed for airport conditions display.
struct SoundingAnalysisSummary: Codable, Sendable {
    let indices: ThermodynamicIndicesSummary?
    let cloudCoverLowPct: Double?
    let cloudCoverMidPct: Double?
    let cloudCoverHighPct: Double?
}

struct ThermodynamicIndicesSummary: Codable, Sendable {
    let freezingLevelFt: Double?
    let capeSurfaceJkg: Double?
    let soundingCeilingFt: Double?
    let nwpCeilingFt: Double?
}

struct RouteObservations: Codable, Sendable {
    let corridorNm: Double?
    let fetchTime: String?
    let airports: [AirportObservation]?
    let worstMetarCategory: String?
    let worstTafCategory: String?
}

struct AirportObservation: Codable, Identifiable, Sendable {
    let icao: String
    let name: String?
    let distanceFromRouteNm: Double?
    let metarRaw: String?
    let metarFlightCategory: String?
    let metarCeilingFt: Int?
    let metarWindSpeedKt: Int?
    let metarWindGustKt: Int?
    let metarWeather: [String]?
    let metarTemperatureC: Int?
    let metarDewpointC: Int?
    let tafRaw: String?
    let tafFlightCategoryAtEta: String?

    var id: String { icao }
}
