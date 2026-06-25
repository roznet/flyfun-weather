import Foundation

struct FlightResponse: Codable, Identifiable, Sendable {
    let id: String
    let userId: String
    let profileId: Int?
    let aircraftId: Int?
    let aircraft: AircraftInfo?
    let routeName: String
    let waypoints: [String]
    let departureTime: String
    let targetDate: String
    let targetTimeUtc: Int
    let cruiseAltitudeFt: Int
    let flightCeilingFt: Int
    let flightDurationHours: Double
    let `private`: Bool
    let autoRefresh: Bool
    let autoRefreshHour: Int?
    let createdAt: String
    /// Owner vs subscriber. Absent on older servers — treated as owner.
    let role: FlightRole?

    /// Parsed departure date for display.
    var departureDate: Date? {
        ISO8601DateFormatter().date(from: departureTime)
    }

    /// Short title: "ORIGIN → DEST" from waypoints.
    var shortTitle: String {
        guard let origin = waypoints.first, let dest = waypoints.last else {
            return routeName
        }
        return "\(origin.uppercased()) → \(dest.uppercased())"
    }

    /// Whether the current user may edit this flight. Subscribers (shared flights)
    /// are read-only; owners and legacy flights without a role are editable.
    var isEditable: Bool {
        role != .subscriber
    }
}

/// Whether the signed-in user owns this flight or is a read-only subscriber.
enum FlightRole: String, Codable, Sendable {
    case owner
    case subscriber
}

/// Aircraft summary embedded in a flight, used for the list card label.
struct AircraftInfo: Codable, Hashable, Sendable {
    let id: Int
    let icaoType: String
    let typeName: String
    let tailNumber: String?
    let nickname: String?

    var displayName: String {
        if let nickname, !nickname.isEmpty { return nickname }
        if let tailNumber, !tailNumber.isEmpty { return tailNumber }
        return typeName
    }
}
