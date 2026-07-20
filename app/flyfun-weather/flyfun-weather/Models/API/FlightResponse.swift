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

    /// Server-assigned logbook bucket ("future" | "recent" | "past"). "recent" is
    /// the debrief-nudge window (recent past flights not yet debriefed). Absent on
    /// older servers → nil; the flight list then falls back to client-side date
    /// bucketing. `var … = nil` (not `let`) so it decodes AND the synthesized
    /// memberwise init keeps existing call sites unchanged.
    var section: String? = nil
    /// The pilot's stored debrief for this flight, inlined by `/api/flights` for
    /// owned past flights only (nil otherwise / older servers). Lets the list show
    /// a "debriefed ✓" glyph and the briefing seed its debrief card without a
    /// per-flight round-trip.
    var debrief: DebriefResponse? = nil

    /// Short base62 token for the flight's share link (`/s/{shareCode}`). The
    /// server emits `share_code` on every flight created after migration 049; the
    /// app uses it to build the ShareLink URL (owner) and it round-trips through
    /// the by-share resolver (subscriber preview). `var … = nil` so it decodes
    /// AND the synthesized memberwise init keeps existing call sites unchanged;
    /// absent on very old rows → nil (no share affordance).
    var shareCode: String? = nil

    /// The flight owner's display name — set by the server only on the subscriber
    /// view (`role == .subscriber`). Drives the "Shared by …" preview banner; nil
    /// falls back to a generic "Shared flight" label (server never sends the
    /// owner's email). `var … = nil` so it decodes AND keeps memberwise-init call
    /// sites unchanged; absent on older servers / the owner's own view → nil.
    var ownerDisplayName: String? = nil

    /// Whether the viewer has already subscribed to this (someone else's) flight.
    /// Flips the shared-flight preview banner button Subscribe ↔ Unsubscribe.
    /// `var … = nil` so it decodes AND keeps memberwise-init call sites unchanged;
    /// absent on older servers → treated as not-subscribed via `hasSubscribed`.
    var isSubscribed: Bool? = nil

    /// `isSubscribed` folded to a plain Bool so callers never juggle the optional.
    var hasSubscribed: Bool { isSubscribed ?? false }

    /// Whether this flight can be shared: only the owner, and only when it isn't
    /// private (recipients of a private link would 404), and only once the server
    /// has minted a share code. Mirrors the web share-affordance gate.
    var isShareable: Bool {
        isEditable && !`private` && !(shareCode ?? "").isEmpty
    }

    /// Per-flight override folded to an enum, tolerant of unknown/absent values.
    var notifyOverrideMode: FlightNotifyOverride {
        FlightNotifyOverride(rawValue: notifyOverride ?? "default") ?? .default
    }

    /// Logbook section folded to an enum. On a legacy server that omits
    /// `section`, mirror `FlightListView.groupedFlights`' date bucketing exactly
    /// (Recent = departed within 7 days) so a card's Recent-debrief nudge matches
    /// the list's Recent group instead of never appearing.
    var flightSection: FlightSection {
        if let section, let parsed = FlightSection(rawValue: section) { return parsed }
        guard let dep = departureDate else { return .future }
        let now = Date()
        if dep >= now { return .future }
        if dep >= now.addingTimeInterval(-7 * 24 * 3600) { return .recent }
        return .past
    }

    /// Whether this flight already has a stored debrief.
    var hasDebrief: Bool { debrief != nil }

    /// Parsed departure date for display.
    var departureDate: Date? {
        ISO8601DateFormatter().date(from: departureTime)
    }

    /// Whether the flight has already ended (departure + duration in the past).
    /// Mirrors the web `isFlightPast` so past-flight UI gating matches across
    /// clients (e.g. the per-flight notify bell is hidden for flown flights).
    var isPast: Bool {
        guard let departure = departureDate else { return false }
        return Date() > departure.addingTimeInterval(flightDurationHours * 3600)
    }

    /// Whether `now` falls in the flight's tracking window: departure −2h to
    /// departure + duration + 2h. This is the single source of truth for that
    /// window — the briefing view (Start/Stop, PIREP button) and the flight list
    /// ("Add PIREP" gate) both use it instead of reimplementing the formula, and
    /// it mirrors the server's PIREP-linkage window in `storage/pireps.py`.
    /// `now` is a parameter for deterministic testing.
    func isInTrackingWindow(now: Date = Date()) -> Bool {
        guard let departure = departureDate else { return false }
        let start = departure.addingTimeInterval(-2 * 3600)
        let end = departure.addingTimeInterval((flightDurationHours + 2) * 3600)
        return now >= start && now <= end
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

/// Server-assigned logbook bucket for the flight list (mirrors the web
/// `section`). `recent` is the debrief-nudge window: recent past flights not yet
/// debriefed (bounded server-side), so a `recent` flight is the one to debrief.
enum FlightSection: String, Codable, Sendable {
    case future
    case recent
    case past
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
/// `Equatable` so `FlightCardView` can diff on it (see its `==`).
struct CoveragePending: Codable, Sendable, Equatable {
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
/// `Equatable` so `FlightCardView` diffs on the briefing content (the card's
/// only source of truth) rather than `FlightResponse`'s id-only identity, which
/// would otherwise make a same-id/new-briefing update look unchanged (#426).
struct BriefingStatusInfo: Codable, Sendable, Equatable {
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
    /// The latest pack's fetch timestamp (ISO 8601), as the server sends it in
    /// `latest_briefing.fetch_timestamp`. Used by the flight list's
    /// level-triggered reconcile to detect that a briefing advanced even when
    /// the active-refresh poll never observed the refresh (#426). Optional (no
    /// default — a `let` with a default would be dropped from the synthesized
    /// `Codable`, never decoding the field); absent on older servers → nil.
    let fetchTimestamp: String?
}

/// Compact RED/AMBER advisory breakdown for the flights-list card chips.
struct AdvisorySummary: Codable, Sendable, Equatable {
    let red: Int
    let amber: Int
    /// Severity-ordered named concerns, capped at 3 server-side.
    let top: [AdvisoryChip]
}

/// One named advisory concern for the summary chips.
struct AdvisoryChip: Codable, Sendable, Equatable {
    let status: String  // "RED" | "AMBER"
    let name: String
}
