import Foundation

/// Wire types for flight trips (#602, iOS port #607).
///
/// A trip is a **conjunctive feasibility chain with a shrinking scope**: it
/// happens only if all its *remaining* legs work, and the set that must work
/// shrinks as legs are flown. Two rules from `designs/flight-trips.md` constrain
/// everything that reads these types:
///
/// 1. **The trip never gets a verdict.** `chainStatus` is the worst light among
///    *gradeable remaining* legs — it is a input to attention, not a colour for
///    the trip. Nothing in the app may render a trip-level traffic light.
/// 2. **The binding leg is computed once, on the server.** Read
///    ``TripSummary/bindingLegId`` and ``TripSummary/headline``; never re-derive
///    them by ranking legs client-side. A long-range `outlook` and a traffic
///    light are separate ladders and must never be folded together.
///
/// Datetimes are `String` on the wire, matching every other DTO here: the shared
/// decoder uses `.iso8601`, which rejects both fractional seconds and the bare
/// `YYYY-MM-DD` that `decidable_from` carries. Parsing is done at the point of
/// display via the helpers below.

// MARK: - Enumerations

/// Where a leg sits relative to now. Clock time is primary; a debrief refines it
/// (a cancelled leg is not "remaining" even when it is still in the future).
enum TripLegState: String, Codable, Sendable {
    case flown
    case cancelled
    case monitoring
    case remaining
    /// A value this build doesn't know. Decodes rather than throwing, so a
    /// server that adds a state can't blank the whole trip screen.
    case unknown

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = TripLegState(rawValue: raw) ?? .unknown
    }

    /// Whether this leg still has to work for the trip to happen. Only these can
    /// bind — which is why the AI-summary cache key includes the derived state.
    ///
    /// `monitoring` is deliberately **not** remaining: it is the debrief value for
    /// a flight created to watch the weather, never intended to fly, and the
    /// server (`trips.py::_leg_state`) excludes it from both the binding-leg pick
    /// and the `remaining_legs` count for the same reason as `cancelled`. Counting
    /// it here would pull it under the trip header while the header's own
    /// "n of m legs ahead" (the server's count) left it out.
    var isRemaining: Bool { self == .remaining }
}

/// What kind of claim a leg carries. **Four non-gradeable states, not three** —
/// `unavailable` (briefed, but the pack could not be graded) and `needsBriefing`
/// (never briefed) are different facts implying different actions, and telling a
/// pilot a leg has no briefing when it has one that failed to grade is false.
enum TripGradeKind: String, Codable, Sendable {
    /// A real GREEN/AMBER/RED traffic light.
    case assessment
    /// Beyond the GRIB horizon: a tendency, never a verdict.
    case outlook
    /// No weather model reaches this leg's date yet.
    case pendingCoverage = "pending_coverage"
    /// Never briefed.
    case needsBriefing = "needs_briefing"
    /// Briefed, but the pack came back ungradeable.
    case unavailable
    case unknown

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = TripGradeKind(rawValue: raw) ?? .unknown
    }
}

/// What the server picked the binding leg on. `outlook` means *no* remaining leg
/// was gradeable, so there is deliberately no `chainStatus` alongside it.
enum TripBindingBasis: String, Codable, Sendable {
    case assessment
    case outlook
    case unknown

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = TripBindingBasis(rawValue: raw) ?? .unknown
    }
}

// MARK: - Summary

/// Leg *k*'s destination is not leg *k+1*'s origin. Soft by design — pilots
/// reposition, and a move is allowed to break the chain — but usually a mistake
/// worth seeing.
struct ContinuityWarning: Codable, Sendable, Equatable, Identifiable {
    let afterFlightId: String
    let beforeFlightId: String
    let arrives: String
    let departs: String

    var id: String { "\(afterFlightId)->\(beforeFlightId)" }
}

/// One leg as the trip view renders it.
struct TripLeg: Codable, Sendable, Equatable, Identifiable {
    let flightId: String
    /// "EGTF → LSGS", built server-side from the waypoints.
    let label: String
    var origin: String? = nil
    var destination: String? = nil
    let departureTime: String
    var durationHours: Double = 0
    let state: TripLegState
    let gradeKind: TripGradeKind
    var assessment: String? = nil
    var assessmentReason: String? = nil
    var outlook: String? = nil
    var outlookReason: String? = nil
    var daysOut: Int? = nil
    var advisorySummary: AdvisorySummary? = nil
    var fetchTimestamp: String? = nil
    /// Ground time since the previous leg landed, in hours (nil on leg 1).
    /// Negative is possible when a pilot schedules overlapping legs; the server
    /// reports it as-is rather than clamping, since it is a data problem worth
    /// seeing, so do not assume this is positive.
    var gapHoursBefore: Double? = nil
    /// This leg and the previous one are one *sortie* — a fuel or customs stop,
    /// not a decision point. Server threshold is `SORTIE_GAP_HOURS` (4 h).
    var sameSortieAsPrevious: Bool = false

    var id: String { flightId }

    var departureDate: Date? { ISO8601DateFormatter().date(from: departureTime) }
    var fetchDate: Date? { fetchTimestamp.flatMap { ISO8601DateFormatter().date(from: $0) } }
}

/// The deterministic trip picture. Computed per read on the server and **never
/// persisted** — it is stale the moment any leg refreshes.
struct TripSummary: Codable, Sendable, Equatable {
    let tripId: String
    var name: String = ""
    var legs: [TripLeg] = []
    var totalLegs: Int = 0
    var remainingLegs: Int = 0

    /// Worst traffic light among the *gradeable remaining* legs — nil when no
    /// remaining leg carries one. Never render this as a badge for the trip;
    /// see the type-level note.
    var chainStatus: String? = nil
    var bindingLegId: String? = nil
    var bindingBasis: TripBindingBasis? = nil

    /// The four non-gradeable buckets, reported separately and never folded into
    /// `chainStatus`.
    var beyondHorizonLegIds: [String] = []
    var pendingCoverageLegIds: [String] = []
    var needsBriefingLegIds: [String] = []
    var unavailableLegIds: [String] = []

    /// `daysOut` of the binding leg, and the date its first GRIB-backed briefing
    /// becomes available. `decidableFrom` is a bare `YYYY-MM-DD`, not a datetime.
    var decisionRipenessDays: Int? = nil
    var decidableFrom: String? = nil

    var isRoundTrip: Bool = false
    /// "EGTF → LSGS → LFAT → EGTF".
    var chainLabel: String = ""
    var continuityWarnings: [ContinuityWarning] = []

    /// One deterministic sentence — the thing the eye should land on. Render it
    /// verbatim; the AI paragraph is secondary to it and can only make it nicer
    /// to read, never different.
    var headline: String = ""

    /// The leg the server says decides this trip.
    var bindingLeg: TripLeg? {
        guard let bindingLegId else { return nil }
        return legs.first { $0.flightId == bindingLegId }
    }

    /// Legs that still have to work. Used for the flight-list rows and the trip
    /// context menu — a flown leg is precisely the one that stopped mattering.
    var remainingLegsList: [TripLeg] { legs.filter { $0.state.isRemaining } }

    var decidableFromDate: Date? {
        decidableFrom.flatMap { DateFormatter.tripDay.date(from: $0) }
    }
}

// MARK: - Trip

/// Progress of the server's serial trip-refresh driver. One leg is in flight at
/// a time; the chain survives the app being backgrounded because the server
/// drives it, so this is purely a progress readout.
struct TripRefreshStatus: Codable, Sendable, Equatable {
    let tripId: String
    var refreshId: String? = nil
    var active: Bool = false
    var total: Int = 0
    var completed: Int = 0
    var currentFlightId: String? = nil
    /// flight id → "succeeded" | "skipped" | "failed" | "busy".
    var results: [String: String] = [:]
    /// Human line for the "2 of 3 legs had new data" readout. Show it verbatim:
    /// without it, a trip refresh that legitimately did almost nothing (every
    /// leg skipped for want of a new model run) reads as one that failed.
    var message: String = ""
    /// When the last run closed (ISO, UTC); nil before any has. The server keeps
    /// a finished run's `results`/`message` indefinitely, so this is what lets the
    /// screen drop that readout once a leg has been refreshed on its own since.
    var finishedAt: String? = nil

    /// The run readout to show, or "" for none — a port of the web's
    /// `helpers/trip-leg-refresh.ts::tripRunMessage`.
    ///
    /// Always while a run is live. After one, only until any leg has a pack newer
    /// than the finish: a single-leg refresh since then makes the line describe a
    /// run the legs have moved past (it would call a just-re-briefed leg "already
    /// current"). No finish time means the age is unknown, so say nothing rather
    /// than risk a stale claim.
    func runMessage(legs: [TripLeg]) -> String {
        guard !message.isEmpty else { return "" }
        if active { return message }
        guard let finished = finishedAt.flatMap(Self.parseDate) else { return "" }
        let legMovedOn = legs.contains { leg in
            guard let fetched = leg.fetchTimestamp.flatMap(Self.parseDate) else { return false }
            return fetched > finished
        }
        return legMovedOn ? "" : message
    }

    /// Server timestamps come from Python `isoformat()`, which carries fractional
    /// seconds whenever the microseconds are non-zero — try both shapes.
    private static func parseDate(_ raw: String) -> Date? {
        let plain = ISO8601DateFormatter()
        if let date = plain.date(from: raw) { return date }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: raw)
    }
}

/// A trip container plus its derived summary.
struct TripResponse: Codable, Sendable, Equatable, Identifiable {
    let id: String
    let userId: String
    var name: String = ""
    var notes: String? = nil
    var autoRefresh: Bool = false
    var autoRefreshHour: Int? = nil
    var notifyOverride: String = "default"
    var createdAt: String = ""
    var flightIds: [String] = []
    let summary: TripSummary
    /// The persisted Haiku paragraph, when one exists and is still keyed to the
    /// current member packs. The server re-checks the inherited AI consent gate
    /// on *read*, so a nil here can mean "AI is off on a member leg" as well as
    /// "never generated" — ask `/ai-summary` for the reason.
    var aiSummary: String? = nil
    var aiSummaryAt: String? = nil
    /// The stored paragraph no longer matches the member packs. **This is the
    /// only trigger for `POST /ai-summary`**: a key hit costs nothing, but the
    /// GET already carries the text, so there is no reason to post without it.
    var aiSummaryStale: Bool = false
    var refresh: TripRefreshStatus? = nil

    /// Display title: the trip's name, falling back to the chain when a trip was
    /// created before its default name was derived.
    var displayName: String { name.isEmpty ? summary.chainLabel : name }

    var notifyOverrideMode: FlightNotifyOverride {
        FlightNotifyOverride(rawValue: notifyOverride) ?? .default
    }
}

/// Response of `POST /api/trips/{id}/ai-summary`.
struct TripAiSummaryResponse: Codable, Sendable, Equatable {
    let tripId: String
    var text: String? = nil
    var generatedAt: String? = nil
    /// Why there is no paragraph. `ai_disabled` is the inherited-gate case: any
    /// member leg with the AI digest off means the whole trip gets the
    /// deterministic sentence only, and that is worth saying out loud rather
    /// than silently omitting the section.
    var unavailableReason: String? = nil

    var isAiDisabled: Bool { unavailableReason == "ai_disabled" }
}

// MARK: - Requests

struct CreateTripRequest: Encodable, Sendable {
    var name: String? = nil
    var flightIds: [String] = []
}

struct AddTripLegsRequest: Encodable, Sendable {
    var flightIds: [String] = []
}

/// PATCH body. Every field is optional — the server merges only what is sent, so
/// never populate a field you are not deliberately changing.
struct UpdateTripRequest: Encodable, Sendable {
    var name: String? = nil
    var notes: String? = nil
    var autoRefresh: Bool? = nil
    var autoRefreshHour: Int? = nil
    var notifyOverride: String? = nil
}

// MARK: - Formatting

extension DateFormatter {
    /// Parses the bare `YYYY-MM-DD` the server sends for `decidable_from`. Fixed
    /// POSIX locale so a user's regional calendar can't reinterpret the digits.
    static let tripDay: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()
}

// MARK: - Trip-claimed refresh conflict

/// Recognising the server's "this leg belongs to a running trip refresh" refusal.
///
/// The trip driver owns each leg while its chain runs, and both per-flight
/// refresh paths refuse a claimed leg — the queued endpoint with a **409** and
/// the streaming one with an SSE `error` event. Both carry the same sentence
/// (`api/packs.py`), which is already good user-facing copy; what the app needs
/// is to *recognise* it, because the two things it must do differ from an
/// ordinary refresh failure:
///
/// * **Don't retry it as a busy-flight conflict.** `AddFlightViewModel`'s
///   post-edit re-queue treats a 409 as "a refresh is already running, wait a few
///   seconds and try again" — true for the single-flight case, false here: a trip
///   chain runs for minutes per leg, so every retry is spent for nothing.
/// * **Offer the trip.** A refusal a pilot can act on beats one they can only
///   read.
///
/// Matched on the message rather than on 409 alone, since 409 is also the
/// ordinary already-refreshing conflict. Substring matching is deliberately
/// loose: if the server rewords the sentence this degrades to treating it as an
/// ordinary conflict, which is the pre-existing behaviour, not a new failure.
enum TripRefreshConflict {
    private static let marker = "part of a trip refresh"

    /// Whether a message is the claimed-leg refusal.
    static func matches(message: String?) -> Bool {
        guard let message else { return false }
        return message.localizedCaseInsensitiveContains(marker)
    }

    /// Whether an error from a per-flight refresh is the claimed-leg refusal.
    static func matches(error: Error) -> Bool {
        guard case APIError.serverError(409, let detail) = error else { return false }
        return matches(message: detail)
    }
}

// MARK: - Tolerant decoding

/// Swift's synthesized `Decodable` **ignores default values**: a non-optional
/// property with `= 0` still throws `keyNotFound` when the key is absent, because
/// only `Optional` gets `decodeIfPresent`. The defaults above exist for the
/// memberwise initializer, and would do nothing on the wire.
///
/// That matters here even though the server (Pydantic) serialises every field
/// today: one missing key would fail the whole trip payload rather than one
/// value, blanking the screen. Each `init(from:)` below lives in an **extension**
/// so the memberwise initializer is preserved, and each declares `CodingKeys`
/// explicitly rather than relying on synthesis that a hand-written initializer
/// makes conditional. Key *names* stay camelCase: the shared decoder's
/// `.convertFromSnakeCase` strategy transforms the incoming JSON key before it is
/// matched, so `pending_coverage_leg_ids` arrives as `pendingCoverageLegIds`.

extension TripLeg {
    enum CodingKeys: String, CodingKey {
        case flightId, label, origin, destination, departureTime, durationHours
        case state, gradeKind, assessment, assessmentReason, outlook, outlookReason
        case daysOut, advisorySummary, fetchTimestamp, gapHoursBefore
        case sameSortieAsPrevious
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        flightId = try c.decode(String.self, forKey: .flightId)
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? flightId
        origin = try c.decodeIfPresent(String.self, forKey: .origin)
        destination = try c.decodeIfPresent(String.self, forKey: .destination)
        departureTime = try c.decode(String.self, forKey: .departureTime)
        durationHours = try c.decodeIfPresent(Double.self, forKey: .durationHours) ?? 0
        // An absent state is *not* assumed remaining: counting an unclassifiable
        // leg as one that still has to work would overstate what is left of a
        // trip. `.unknown` keeps it out of the remaining set.
        state = try c.decodeIfPresent(TripLegState.self, forKey: .state) ?? .unknown
        gradeKind = try c.decodeIfPresent(TripGradeKind.self, forKey: .gradeKind) ?? .unknown
        assessment = try c.decodeIfPresent(String.self, forKey: .assessment)
        assessmentReason = try c.decodeIfPresent(String.self, forKey: .assessmentReason)
        outlook = try c.decodeIfPresent(String.self, forKey: .outlook)
        outlookReason = try c.decodeIfPresent(String.self, forKey: .outlookReason)
        daysOut = try c.decodeIfPresent(Int.self, forKey: .daysOut)
        advisorySummary = try c.decodeIfPresent(AdvisorySummary.self, forKey: .advisorySummary)
        fetchTimestamp = try c.decodeIfPresent(String.self, forKey: .fetchTimestamp)
        gapHoursBefore = try c.decodeIfPresent(Double.self, forKey: .gapHoursBefore)
        sameSortieAsPrevious =
            try c.decodeIfPresent(Bool.self, forKey: .sameSortieAsPrevious) ?? false
    }
}

extension TripSummary {
    enum CodingKeys: String, CodingKey {
        case tripId, name, legs, totalLegs, remainingLegs
        case chainStatus, bindingLegId, bindingBasis
        case beyondHorizonLegIds, pendingCoverageLegIds, needsBriefingLegIds
        case unavailableLegIds, decisionRipenessDays, decidableFrom
        case isRoundTrip, chainLabel, continuityWarnings, headline
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tripId = try c.decode(String.self, forKey: .tripId)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        legs = try c.decodeIfPresent([TripLeg].self, forKey: .legs) ?? []
        totalLegs = try c.decodeIfPresent(Int.self, forKey: .totalLegs) ?? legs.count
        remainingLegs = try c.decodeIfPresent(Int.self, forKey: .remainingLegs)
            ?? legs.filter { $0.state.isRemaining }.count
        chainStatus = try c.decodeIfPresent(String.self, forKey: .chainStatus)
        bindingLegId = try c.decodeIfPresent(String.self, forKey: .bindingLegId)
        bindingBasis = try c.decodeIfPresent(TripBindingBasis.self, forKey: .bindingBasis)
        beyondHorizonLegIds =
            try c.decodeIfPresent([String].self, forKey: .beyondHorizonLegIds) ?? []
        pendingCoverageLegIds =
            try c.decodeIfPresent([String].self, forKey: .pendingCoverageLegIds) ?? []
        needsBriefingLegIds =
            try c.decodeIfPresent([String].self, forKey: .needsBriefingLegIds) ?? []
        unavailableLegIds =
            try c.decodeIfPresent([String].self, forKey: .unavailableLegIds) ?? []
        decisionRipenessDays = try c.decodeIfPresent(Int.self, forKey: .decisionRipenessDays)
        decidableFrom = try c.decodeIfPresent(String.self, forKey: .decidableFrom)
        isRoundTrip = try c.decodeIfPresent(Bool.self, forKey: .isRoundTrip) ?? false
        chainLabel = try c.decodeIfPresent(String.self, forKey: .chainLabel) ?? ""
        continuityWarnings =
            try c.decodeIfPresent([ContinuityWarning].self, forKey: .continuityWarnings) ?? []
        headline = try c.decodeIfPresent(String.self, forKey: .headline) ?? ""
    }
}

extension TripRefreshStatus {
    enum CodingKeys: String, CodingKey {
        case tripId, refreshId, active, total, completed, currentFlightId, results, message
        case finishedAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tripId = try c.decode(String.self, forKey: .tripId)
        refreshId = try c.decodeIfPresent(String.self, forKey: .refreshId)
        active = try c.decodeIfPresent(Bool.self, forKey: .active) ?? false
        total = try c.decodeIfPresent(Int.self, forKey: .total) ?? 0
        completed = try c.decodeIfPresent(Int.self, forKey: .completed) ?? 0
        currentFlightId = try c.decodeIfPresent(String.self, forKey: .currentFlightId)
        results = try c.decodeIfPresent([String: String].self, forKey: .results) ?? [:]
        message = try c.decodeIfPresent(String.self, forKey: .message) ?? ""
        finishedAt = try c.decodeIfPresent(String.self, forKey: .finishedAt)
    }
}

extension TripResponse {
    enum CodingKeys: String, CodingKey {
        case id, userId, name, notes, autoRefresh, autoRefreshHour, notifyOverride
        case createdAt, flightIds, summary, aiSummary, aiSummaryAt, aiSummaryStale, refresh
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        userId = try c.decodeIfPresent(String.self, forKey: .userId) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        notes = try c.decodeIfPresent(String.self, forKey: .notes)
        autoRefresh = try c.decodeIfPresent(Bool.self, forKey: .autoRefresh) ?? false
        autoRefreshHour = try c.decodeIfPresent(Int.self, forKey: .autoRefreshHour)
        notifyOverride =
            try c.decodeIfPresent(String.self, forKey: .notifyOverride) ?? "default"
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
        flightIds = try c.decodeIfPresent([String].self, forKey: .flightIds) ?? []
        summary = try c.decode(TripSummary.self, forKey: .summary)
        aiSummary = try c.decodeIfPresent(String.self, forKey: .aiSummary)
        aiSummaryAt = try c.decodeIfPresent(String.self, forKey: .aiSummaryAt)
        aiSummaryStale = try c.decodeIfPresent(Bool.self, forKey: .aiSummaryStale) ?? false
        refresh = try c.decodeIfPresent(TripRefreshStatus.self, forKey: .refresh)
    }
}

extension TripAiSummaryResponse {
    enum CodingKeys: String, CodingKey {
        case tripId, text, generatedAt, unavailableReason
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tripId = try c.decode(String.self, forKey: .tripId)
        text = try c.decodeIfPresent(String.self, forKey: .text)
        generatedAt = try c.decodeIfPresent(String.self, forKey: .generatedAt)
        unavailableReason = try c.decodeIfPresent(String.self, forKey: .unavailableReason)
    }
}
