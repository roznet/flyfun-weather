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
    let altDepartureTime: String?
    let targetDate: String
    let targetTimeUtc: Int
    let cruiseAltitudeFt: Int
    let flightCeilingFt: Int
    let flightDurationHours: Double
    let `private`: Bool
    let autoRefresh: Bool
    let autoRefreshHour: Int?
    let createdAt: String
    let latestBriefing: BriefingStatusInfo?
    let role: FlightRole?
    let ownerDisplayName: String?
    let isSubscribed: Bool?
    let debrief: DebriefResponse?
    let section: FlightListSection?
    let rawRoute: String?
    let parserVersion: String?
    let shareCode: String?

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

    /// Backend section with a local fallback for older cached flight lists.
    var displaySection: FlightListSection {
        if let section { return section }
        if let departureDate, departureDate >= Date() {
            return .future
        }
        return .past
    }

    /// Changes when the same flight row was edited or rebriefed, forcing detail reloads.
    var briefingIdentity: String {
        [
            id,
            departureTime,
            waypoints.joined(separator: "-"),
            String(cruiseAltitudeFt),
            String(flightDurationHours),
            latestBriefing?.fetchTimestamp ?? "",
        ].joined(separator: "|")
    }
}

enum FlightListSection: String, Codable, CaseIterable, Sendable {
    case future
    case recent
    case past

    var title: String {
        switch self {
        case .future: "Future"
        case .recent: "Recent"
        case .past: "Past"
        }
    }

    var systemImage: String {
        switch self {
        case .future: "calendar.badge.clock"
        case .recent: "clock.badge.exclamationmark"
        case .past: "archivebox"
        }
    }
}

enum FlightRole: String, Codable, Sendable {
    case owner
    case subscriber
}

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

struct BriefingStatusInfo: Codable, Sendable {
    let assessment: String?
    let assessmentReason: String?
    let outlook: String?
    let outlookReason: String?
    let hasDigest: Bool?
    let daysOut: Int?
    let fetchTimestamp: String?
    let hasAdvisories: Bool?
    let advisorySummary: AdvisorySummaryInfo?

    var displayAssessment: String? {
        assessment ?? outlook
    }
}

struct AdvisorySummaryInfo: Codable, Sendable {
    let red: Int?
    let amber: Int?
    let top: [AdvisoryChipInfo]?
}

struct AdvisoryChipInfo: Codable, Sendable {
    let status: String
    let name: String
}

struct DebriefResponse: Codable, Sendable {
    let flightId: String
    let decision: String
    let reasons: [String]
    let outcomes: [String: String]
    let note: String?
    let createdAt: String
    let updatedAt: String
}
