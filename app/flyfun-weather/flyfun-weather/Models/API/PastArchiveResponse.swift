import Foundation

/// Response of `GET /api/flights/archive?view=list` (#1031): the owner's Past
/// flights, filtered, paged and counted by the server. The app renders rows,
/// chips and counts as given and computes none of them (#1032).
///
/// Decoded with `JSONDecoder.weatherBrief` (snake→camel). The dynamic-key
/// dictionaries (`forecastTags`, `facets.tags`, `facets.outcomes`) are keyed by
/// tag / outcome ids with no underscore (`ICE`, `flown`, `differs`), so the key
/// conversion leaves them unchanged.
nonisolated struct PastArchiveResponse: Decodable, Sendable {
    let view: String
    let total: Int
    let limit: Int
    let offset: Int
    let facets: PastArchiveFacets
    let rows: [PastArchiveRow]
}

nonisolated struct PastArchiveFacets: Decodable, Sendable, Equatable {
    let source: String
    /// Matches per tag for the current filter without the tag chips.
    let tags: [String: Int]
    /// Matches per outcome for the current filter without the outcome chips.
    let outcomes: [String: Int]
    let notRecorded: Int
}

nonisolated struct PastArchiveTrip: Decodable, Sendable, Hashable {
    let id: String
    let name: String
}

/// One Past flight. `tagsRecorded == false`: the briefing was gone before it
/// was recorded, shown as "Hazards not recorded", never as "none".
nonisolated struct PastArchiveRow: Decodable, Sendable, Identifiable, Hashable {
    let id: String
    /// ISO 8601, kept as text like `FlightResponse.departureTime`.
    let departureTime: String
    let departure: String?
    let destination: String?
    let routeName: String
    let trip: PastArchiveTrip?
    /// Worst status per tag, amber or worse only: `["ICE": "red"]`.
    let forecastTags: [String: String]
    let forecastWorst: String?
    let tagsRecorded: Bool
    let reportedTags: [String]
    let decision: String?
    let debriefed: Bool
    let differsFromForecast: Bool
    let distanceNm: Double?

    var departureDate: Date? { ISO8601DateFormatter().date(from: departureTime) }

    var title: String {
        let ends = [departure, destination].compactMap { $0 }
        return ends.isEmpty ? routeName : ends.joined(separator: " → ")
    }
}

/// The finder's chips, in the order every client shows them (web
/// `helpers/past-archive.ts`, Android `PastChips`): change one, change all.
nonisolated enum PastChips {
    struct Chip: Hashable, Sendable {
        /// Archive tag (`ICE`) or outcome key (`flown`) the server takes.
        let id: String
        let label: String
    }

    static let hazards: [Chip] = [
        Chip(id: "ICE", label: "Icing"), Chip(id: "TS", label: "Convection"),
        Chip(id: "IMC", label: "Low cloud"), Chip(id: "WIND", label: "Wind"),
        Chip(id: "TURB", label: "Turbulence"), Chip(id: "FRZ", label: "Freezing precip"),
        Chip(id: "VIS", label: "Visibility"),
    ]
    static let outcomes: [Chip] = [
        Chip(id: "flown", label: "Flown"), Chip(id: "cancelled", label: "Cancelled"),
        Chip(id: "monitoring", label: "Monitor only"), Chip(id: "debriefed", label: "Debriefed"),
        Chip(id: "differs", label: "Differed from forecast"),
    ]
    static let hideInactive = ["cancelled", "monitoring"]

    static func label(forTag tag: String) -> String {
        hazards.first { $0.id == tag }?.label ?? tag
    }

    static func label(forOutcome key: String) -> String {
        outcomes.first { $0.id == key }?.label ?? key
    }
}

/// The finder's filter state; `queryItems` is the whole client-side logic.
nonisolated struct PastFilter: Equatable, Hashable, Sendable {
    var query: String = ""
    var tags: [String] = []
    var reported = false
    var outcomes: [String] = []
    var hideInactive = false

    var isActive: Bool {
        !query.trimmingCharacters(in: .whitespaces).isEmpty || !tags.isEmpty || !outcomes.isEmpty || hideInactive
    }

    func queryItems(limit: Int, offset: Int) -> [URLQueryItem] {
        var items: [URLQueryItem] = []
        let q = query.trimmingCharacters(in: .whitespaces)
        if !q.isEmpty { items.append(URLQueryItem(name: "q", value: q)) }
        if !tags.isEmpty { items.append(URLQueryItem(name: "tags", value: tags.joined(separator: ","))) }
        if reported { items.append(URLQueryItem(name: "source", value: "reported")) }
        if !outcomes.isEmpty { items.append(URLQueryItem(name: "outcome", value: outcomes.joined(separator: ","))) }
        if hideInactive { items.append(URLQueryItem(name: "hide", value: PastChips.hideInactive.joined(separator: ","))) }
        items.append(URLQueryItem(name: "limit", value: String(limit)))
        if offset > 0 { items.append(URLQueryItem(name: "offset", value: String(offset))) }
        return items
    }

    /// The iPad sidebar's saved filters (#1030).
    static let all = PastFilter()
    static let withIcing = PastFilter(tags: ["ICE"])
    static let withConvection = PastFilter(tags: ["TS"])
    static let cancelled = PastFilter(outcomes: ["cancelled"])
    static let debriefed = PastFilter(outcomes: ["debriefed"])
}
