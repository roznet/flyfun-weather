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
    /// Latest briefing pack summary (assessment / outlook / advisory chips),
    /// inlined by `/api/flights` so the list card shows a traffic-light tag
    /// without a per-flight round-trip — same payload the web card reads. nil
    /// for a flight that has never been briefed.
    let latestBriefing: BriefingStatusInfo?
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

/// Summary of a flight's latest briefing pack, inlined in the flights list so
/// each card can render a status tag from the single `/api/flights` response
/// (mirrors the web `BriefingStatusInfo`). Scalar fields are optional so a
/// partial/legacy payload decodes rather than failing the whole flight.
struct BriefingStatusInfo: Codable, Sendable {
    /// GREEN / AMBER / RED traffic-light verdict (short-range, within the GRIB
    /// horizon). nil when only a long-range `outlook` is available.
    let assessment: String?
    let assessmentReason: String?
    /// Long-range early outlook (beyond the GRIB horizon), e.g.
    /// TRENDING_SETTLED / MIXED_SIGNALS / TRENDING_UNSETTLED. Mutually
    /// exclusive with `assessment` — a tendency, not a verdict.
    let outlook: String?
    let outlookReason: String?
    let hasAdvisories: Bool?
    let advisorySummary: AdvisorySummary?
}

/// Compact RED/AMBER advisory breakdown for the flights-list card chips.
struct AdvisorySummary: Codable, Sendable {
    let red: Int
    let amber: Int
    /// Severity-ordered named concerns, capped at 3 server-side.
    let top: [AdvisoryChip]
}

/// One named advisory concern for the summary chips.
struct AdvisoryChip: Codable, Sendable {
    let status: String  // "RED" | "AMBER"
    let name: String
}
