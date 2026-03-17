import Foundation

struct FlightResponse: Codable, Identifiable, Sendable {
    let id: String
    let userId: String
    let profileId: Int?
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

    /// Parsed departure date for display.
    var departureDate: Date? {
        ISO8601DateFormatter().date(from: departureTime)
    }

    /// Short title: "ORIGIN → DEST" from waypoints.
    var shortTitle: String {
        guard let origin = waypoints.first, let dest = waypoints.last, origin != dest else {
            return routeName
        }
        return "\(origin.uppercased()) → \(dest.uppercased())"
    }
}
