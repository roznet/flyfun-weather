import Foundation

/// Request body for creating a new flight via POST /api/flights.
struct CreateFlightRequest: Encodable {
    let waypoints: [String]
    let departureTime: String
    var routeName: String = ""
    var cruiseAltitudeFt: Int?
    var flightCeilingFt: Int?
    var flightDurationHours: Double?
}

/// Request body for editing an existing flight via PATCH /api/flights/{id}.
/// Date and origin/destination cannot change server-side; only forecast-
/// affecting parameters (time-of-day, FL, ceiling, duration, route) are sent.
struct UpdateFlightRequest: Encodable {
    var waypoints: [String]?
    var departureTime: String?
    var cruiseAltitudeFt: Int?
    var flightCeilingFt: Int?
    var flightDurationHours: Double?
}

/// Request body for parsing an ICAO flight plan string.
struct ParseFplRequest: Encodable {
    let fplText: String
}

/// Parsed ICAO flight plan fields returned by the server.
struct ParseFplResponse: Decodable {
    let waypoints: [String]
    let date: String?       // YYYY-MM-DD
    let timeUtc: String?    // HH:MM
    let altitudeFt: Int?
    let durationHours: Double?
    let flightRules: String?
    let aircraftType: String?
    let rawRoute: String?
    let error: String?
}
