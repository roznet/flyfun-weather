import Foundation

/// Request body for creating a new flight via POST /api/flights.
struct CreateFlightRequest: Encodable {
    let waypoints: [String]
    let departureTime: String
    var routeName: String = ""
    var rawRoute: String? = nil
    var cruiseAltitudeFt: Int? = nil
    var flightCeilingFt: Int? = nil
    var flightDurationHours: Double? = nil
    var profileId: Int? = nil
    var aircraftId: Int? = nil
}

/// Request body for PATCH /api/flights/{id}.
struct UpdateFlightRequest: Encodable {
    var profileId: Int? = nil
    var aircraftId: Int? = nil
    var departureTime: String? = nil
    var cruiseAltitudeFt: Int? = nil
    var flightCeilingFt: Int? = nil
    var flightDurationHours: Double? = nil
    var waypoints: [String]? = nil
    var rawRoute: String? = nil
}

enum FlightInvalidation: String, Codable, Sendable {
    case none
    case advisoriesOnly = "advisories_only"
    case refetchNeeded = "refetch_needed"

    var needsRegeneration: Bool {
        self != .none
    }
}

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

/// Aircraft type suggestion from GET /api/aircraft/types.
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

/// Aircraft entry from GET /api/aircraft for create/edit pickers.
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
        return parts.joined(separator: " · ")
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
