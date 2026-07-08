import Foundation

/// Response of `GET /api/maps/airport-weather` — multi-model forecast + (for
/// D-0) observations for a set of airports. The App Intents `AirportWeatherIntent`
/// consumes only the consensus category + wind and the latest METAR, so this DTO
/// decodes just those fields (unknown keys, including the full per-model `models`
/// block, are ignored). Mirrors the MCP `get_airport_weather` tool.
struct AirportWeatherResponse: Decodable, Sendable {
    let day: Int?
    let hourUtc: Int?
    let airports: [AirportWeatherEntry]
    let unsupported: [String]?
}

/// One airport's forecast consensus + optional observation.
struct AirportWeatherEntry: Decodable, Sendable {
    let icao: String
    let consensus: AirportWeatherConsensus?
    /// Present for D-0 only (latest cached METAR/TAF, up to ~3h old).
    let observation: AirportWeatherObservation?
    /// Set when the requested airport isn't monitored and was snapped to the
    /// nearest one.
    let requestedIcao: String?
    let resolutionDistanceNm: Double?
}

/// Cross-model consensus (worst/majority computed server-side).
struct AirportWeatherConsensus: Decodable, Sendable {
    let flightCategory: String?
    let windSpeedKt: Double?
    let windDirDeg: Double?
    let ceilingFt: Double?
    let visibilityM: Double?
}

/// Latest METAR-derived observation (D-0).
struct AirportWeatherObservation: Decodable, Sendable {
    let metarRaw: String?
    let flightCategory: String?
    let windSpeedKt: Double?
    let windDirDeg: Double?
}
