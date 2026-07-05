import Foundation

/// Request body for creating a new flight via POST /api/flights.
struct CreateFlightRequest: Encodable {
    let waypoints: [String]
    let departureTime: String
    var routeName: String = ""
    var cruiseAltitudeFt: Int?
    var flightCeilingFt: Int?
    var flightDurationHours: Double?
    var aircraftId: Int? = nil
    /// Flight profile to associate; the server fills any unspecified flight
    /// fields (ceiling, speed, model choices) from this profile's settings.
    var profileId: Int? = nil
    /// Timing-scenario Flexibility mode. `.alternate` is rejected on create
    /// (it needs `altDepartureTime`, set via PATCH after creation — mirrors the
    /// web create-then-patch flow), so the create picker only offers the day
    /// modes. Omit for no scan.
    var flexibility: FlexibilityMode? = nil
}

/// Request body for editing an existing flight via PATCH /api/flights/{id}.
/// Only the keys we set are sent; the server diffs them and returns a
/// `FlightInvalidation` hint describing how much of the briefing is now stale.
/// `aircraftId == 0` is the server's "detach aircraft" sentinel.
struct UpdateFlightRequest: Encodable {
    var aircraftId: Int? = nil
    var waypoints: [String]? = nil
    var departureTime: String? = nil
    var cruiseAltitudeFt: Int? = nil
    var flightCeilingFt: Int? = nil
    var flightDurationHours: Double? = nil
    var profileId: Int? = nil
    /// Timing-scenario Flexibility mode; omit for no change. `.alternate`
    /// requires `altDepartureTime` (server 422s otherwise).
    var flexibility: FlexibilityMode? = nil
    /// Pinned alternate departure (ISO 8601), or `""` to clear it. Omit for no
    /// change. Paired with `flexibility == .alternate`.
    var altDepartureTime: String? = nil
}

/// How much of the briefing an edit invalidated, returned alongside the updated
/// flight from PATCH /api/flights/{id}.
enum FlightInvalidation: String, Codable, Sendable {
    case none
    case advisoriesOnly = "advisories_only"
    case refetchNeeded = "refetch_needed"

    var needsRegeneration: Bool {
        self != .none
    }
}

/// PATCH /api/flights/{id} response: the updated flight plus the invalidation hint.
/// The flight fields decode at the top level (same shape as `FlightResponse`); the
/// extra `invalidation` key is decoded separately so we keep a single source of
/// truth for the flight model.
struct UpdateFlightResponse: Decodable, Sendable {
    let flight: FlightResponse
    let invalidation: FlightInvalidation

    private enum CodingKeys: String, CodingKey {
        case invalidation
    }

    init(from decoder: Decoder) throws {
        flight = try FlightResponse(from: decoder)
        let container = try decoder.container(keyedBy: CodingKeys.self)
        invalidation = try container.decode(FlightInvalidation.self, forKey: .invalidation)
    }
}

/// Aircraft type suggestion from GET /api/aircraft/types?q=…
struct AircraftTypeResponse: Codable, Identifiable, Hashable, Sendable {
    var id: String { icao }

    let icao: String
    let manufacturer: String
    let model: String
    let category: String?

    var displayName: String {
        let title = [manufacturer, model]
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: " ")
        return title.isEmpty ? icao : "\(icao) - \(title)"
    }
}

/// Saved aircraft from GET /api/aircraft, used to populate the create/edit picker.
struct AircraftResponse: Codable, Identifiable, Hashable, Sendable {
    let id: Int
    let icaoType: String
    let typeName: String
    let tailNumber: String?
    let nickname: String?
    let isIfr: Bool
    let isFiki: Bool
    let cruiseSpeedKt: Int?
    let ceilingFt: Int?
    let isDefault: Bool
    let createdAt: String

    var displayName: String {
        if let nickname, !nickname.isEmpty { return nickname }
        if let tailNumber, !tailNumber.isEmpty { return tailNumber }
        return typeName
    }

    var detailText: String {
        var parts = [icaoType]
        if let cruiseSpeedKt { parts.append("\(cruiseSpeedKt) kt") }
        if let ceilingFt { parts.append("FL\(ceilingFt / 100)") }
        return parts.joined(separator: " \u{00b7} ")
    }

    var pickerTitle: String {
        displayName == typeName ? displayName : "\(displayName) - \(typeName)"
    }
}

/// Request body for POST /api/aircraft.
struct CreateAircraftRequest: Encodable {
    let icaoType: String
    var tailNumber: String? = nil
    var nickname: String? = nil
    var isIfr: Bool = false
    var isFiki: Bool = false
    var cruiseSpeedKt: Int? = nil
    var ceilingFt: Int? = nil
    var isDefault: Bool = false
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
