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
    /// Present only when the flight is saved beyond the forecast horizon (no
    /// model data yet). Drives the pending "available dd/mm" list chip and the
    /// pending-coverage card; nil once the flight is within range.
    let coverage: CoveragePending?
    /// Owner vs subscriber. Absent on older servers — treated as owner.
    let role: FlightRole?
    /// Timing-scenario Flexibility mode. Absent on older servers → treated as
    /// `.none`. `.alternate` grades the single `altDepartureTime`; the day modes
    /// run the departure-window scan. See `TimeOptionsResponse`. Placed last so
    /// the memberwise initializer's existing call sites only append two args.
    let flexibility: FlexibilityMode?
    /// The pinned alternate departure (ISO 8601), used when `flexibility` is
    /// `.alternate` or as the "★ your alternate" row of a day scan. nil = none set.
    let altDepartureTime: String?
    /// Per-flight briefing-notification override ("default" | "notify" | "mute").
    /// Stored as a tolerant string (see `notifyOverrideMode`) and defaulted so
    /// the synthesized memberwise init keeps existing call sites unchanged.
    /// Absent on older servers → treated as "default".
    var notifyOverride: String? = nil

    /// Per-flight override folded to an enum, tolerant of unknown/absent values.
    var notifyOverrideMode: FlightNotifyOverride {
        FlightNotifyOverride(rawValue: notifyOverride ?? "default") ?? .default
    }

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

    /// `flexibility` with the absent/legacy case folded to `.none`, so callers
    /// never juggle the optional.
    var effectiveFlexibility: FlexibilityMode {
        flexibility ?? .none
    }
}

/// Whether the signed-in user owns this flight or is a read-only subscriber.
enum FlightRole: String, Codable, Sendable {
    case owner
    case subscriber
}

/// Per-flight briefing-notification override (mirrors the server
/// `Flight.notify_override`). `default` follows the account "Briefing updates"
/// setting; `notify` fires on any update to this flight even if the account is
/// Off; `mute` never notifies. Delivery still uses the global channels.
enum FlightNotifyOverride: String, Codable, Sendable, CaseIterable, Identifiable {
    case `default`
    case notify
    case mute

    var id: String { rawValue }

    /// Bell menu label.
    var label: String {
        switch self {
        case .default: "Default"
        case .notify: "Always"
        case .mute: "Mute"
        }
    }

    /// SF Symbol reflecting the state.
    var systemImage: String {
        switch self {
        case .default: "bell"
        case .notify: "bell.fill"
        case .mute: "bell.slash"
        }
    }
}

/// Timing-scenario Flexibility mode (mirrors the server `Flight.flexibility` and
/// the web `FlexibilityMode`). Raw values match the wire format verbatim — they
/// decode from string *values*, which the decoder's snake-case key strategy does
/// not touch, so `sameDay` must carry the explicit `"same_day"` raw value.
enum FlexibilityMode: String, Codable, Sendable, CaseIterable {
    case none
    case alternate
    case sameDay = "same_day"
    case prevDay = "prev_day"
    case nextDay = "next_day"

    /// Human label for the flight-editor picker.
    var label: String {
        switch self {
        case .none: "None — just this flight"
        case .alternate: "Alternate time — grade one other departure"
        case .sameDay: "Same day — scan for better windows"
        case .prevDay: "Previous day"
        case .nextDay: "Next day"
        }
    }

    /// Tolerant decode: an unrecognized wire value (e.g. a mode added
    /// server-side before this client knows it) degrades to `.none` instead of
    /// throwing — matching the tolerant-decode contract the rest of the timing
    /// DTOs follow. `FlightResponse` relies on synthesized `Codable` and the
    /// live list decode (`APIClient.request`) is non-tolerant, so without this a
    /// single unknown `flexibility` value would fail the *entire* flights-list
    /// decode, not just that one field. (`effectiveFlexibility` already folds
    /// `.none` to "no scan", so this is a no-op for callers.)
    init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = FlexibilityMode(rawValue: raw) ?? .none
    }
}

/// Weather-coverage status for a flight saved beyond the forecast horizon.
/// Present on `FlightResponse.coverage` only while no model reaches the flight
/// date yet; the UI shows a neutral pending state instead of an assessment.
struct CoveragePending: Codable, Sendable {
    /// ISO date (yyyy-MM-dd) — first (early-outlook) briefing appears.
    let availableDate: String
    /// ISO date — full GRIB briefing, if resolved. nil when unresolved.
    let fullBriefingDate: String?
    /// Whole days from today until `availableDate`.
    let daysUntilAvailable: Int

    /// Parsed `availableDate` for display (date-only, UTC).
    var availableDay: Date? { Self.isoDate.date(from: availableDate) }

    /// Parsed `fullBriefingDate` for display (date-only, UTC); nil if unresolved.
    var fullBriefingDay: Date? {
        fullBriefingDate.flatMap { Self.isoDate.date(from: $0) }
    }

    private static let isoDate: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()
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
