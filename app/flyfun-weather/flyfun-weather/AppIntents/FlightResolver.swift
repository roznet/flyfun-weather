import Foundation

/// Tiered natural-language resolution of a flight from the user's small, closed
/// set of flights (see `designs/ios-app-intents.md` → Resolver Design):
///
/// 1. **Deterministic** — place↔ICAO (via `AirportDatabase`) + relative-date
///    keywords. Free, instant, offline, every device. The mandatory floor.
/// 2. **Foundation Models** — on-device `{place, when}` extraction for loose
///    phrasing, then re-run tier 1. Gated on model availability.
/// 3. **Siri disambiguation** — whatever survives (≥2 matches) is returned and
///    Siri asks the user.
///
/// Division of authority: the LLM only flattens *language* into `{place, when}`;
/// the deterministic matcher against real flights stays the authority, so the
/// model can never surface a flight that doesn't exist.
enum FlightResolver {
    // MARK: - Ordering helpers (pure)
    //
    // These are `nonisolated` (the app target defaults to MainActor isolation) so
    // the nonisolated test target — and any actor context — can call them
    // synchronously. They touch no shared state, only their arguments.

    /// The soonest upcoming flight (departure ≥ now), or nil if none upcoming.
    nonisolated static func nextFlight(in flights: [FlightResponse], now: Date = Date()) -> FlightResponse? {
        flights
            .filter { ($0.departureDate ?? .distantPast) >= now }
            .min(by: { ($0.departureDate ?? .distantFuture) < ($1.departureDate ?? .distantFuture) })
    }

    /// Upcoming flights soonest-first, then past flights most-recent-first — the
    /// order used for Shortcuts suggestions and for the "overview" intent.
    nonisolated static func orderedForSuggestions(_ flights: [FlightResponse], now: Date = Date()) -> [FlightResponse] {
        let upcoming = flights
            .filter { ($0.departureDate ?? .distantPast) >= now }
            .sorted { ($0.departureDate ?? .distantFuture) < ($1.departureDate ?? .distantFuture) }
        let past = flights
            .filter { ($0.departureDate ?? .distantPast) < now }
            .sorted { ($0.departureDate ?? .distantPast) > ($1.departureDate ?? .distantPast) }
        return upcoming + past
    }

    // MARK: - Resolution

    /// Resolve a free string to the matching flights. Empty result → the intent
    /// speaks its "couldn't find a flight to X" line; multiple → Siri
    /// disambiguates.
    @MainActor
    static func resolve(_ raw: String, in flights: [FlightResponse], now: Date = Date()) async -> [FlightResponse] {
        IntentSupport.ensureAirportDatabase()
        let calendar = Calendar.current
        let query = raw.lowercased()

        // Tier 1 — deterministic. Prefer flights matching BOTH a place and a date
        // (narrows "the flight tomorrow to Fairoaks"); else either signal.
        let both = flights.filter {
            matchesPlace($0, query: query) && matchesRelativeDate($0, query: query, now: now, calendar: calendar)
        }
        if !both.isEmpty { return both }
        let either = flights.filter {
            matchesPlace($0, query: query) || matchesRelativeDate($0, query: query, now: now, calendar: calendar)
        }
        if !either.isEmpty { return either }

        // Tier 2 — Foundation Models fallback (only when tier 1 found nothing and
        // an on-device model is available), then re-run the deterministic match.
        if let parsed = await FlightQueryModel.parse(raw) {
            let placed = parsed.place.map { place in
                flights.filter { matchesPlace($0, query: place.lowercased()) }
            } ?? []
            let timed = parsed.when.map { when in
                flights.filter { matchesRelativeDate($0, query: when.lowercased(), now: now, calendar: calendar) }
            } ?? []
            // Intersection when both are present; otherwise whichever signal fired.
            if !placed.isEmpty && !timed.isEmpty {
                let timedIds = Set(timed.map(\.id))
                let intersection = placed.filter { timedIds.contains($0.id) }
                if !intersection.isEmpty { return intersection }
            }
            let merged = placed + timed.filter { t in !placed.contains(where: { $0.id == t.id }) }
            if !merged.isEmpty { return merged }
        }

        // Tier 3 — nothing matched; let the intent handle the empty result.
        return []
    }

    // MARK: - Place matching (needs AirportDatabase, so MainActor)

    /// True when the query references either endpoint of the flight — by ICAO
    /// token or by (a significant word of) the airport name.
    @MainActor
    static func matchesPlace(_ flight: FlightResponse, query: String) -> Bool {
        for icao in [flight.waypoints.first, flight.waypoints.last].compactMap({ $0 }) {
            if icaoMatchesQuery(icao.uppercased(), query: query) { return true }
        }
        return false
    }

    @MainActor
    private static func icaoMatchesQuery(_ icao: String, query: String) -> Bool {
        if query.contains(icao.lowercased()) { return true }
        guard let name = AirportDatabase.shared.airport(icao: icao)?.name.lowercased() else { return false }
        return nameMatches(name: name, query: query)
    }

    /// Match on a significant (≥4-char, non-stopword) word shared between the
    /// airport name and the query. Catches "Fairoaks", "Le Touquet", "Cannes".
    nonisolated static func nameMatches(name: String, query: String) -> Bool {
        let queryWords = Set(tokenize(query))
        for word in tokenize(name) where word.count >= 4 && !placeStopwords.contains(word) {
            if queryWords.contains(word) || query.contains(word) { return true }
        }
        return false
    }

    nonisolated private static func tokenize(_ s: String) -> [String] {
        s.lowercased()
            .components(separatedBy: CharacterSet.letters.inverted)
            .filter { !$0.isEmpty }
    }

    nonisolated private static let placeStopwords: Set<String> = [
        "airport", "international", "intl", "aerodrome", "airfield", "field",
        "base", "municipal", "regional", "city", "the", "flight", "flying",
    ]

    // MARK: - Relative-date matching (pure, testable)

    nonisolated static func matchesRelativeDate(_ flight: FlightResponse, query: String, now: Date, calendar: Calendar) -> Bool {
        guard let departure = flight.departureDate else { return false }
        return relativeDateMatches(departure: departure, query: query, now: now, calendar: calendar)
    }

    /// Pure keyword→date matcher, extracted so it's unit-testable off any actor.
    nonisolated static func relativeDateMatches(departure: Date, query: String, now: Date, calendar: Calendar) -> Bool {
        let depDay = calendar.startOfDay(for: departure)
        let today = calendar.startOfDay(for: now)
        let dayDiff = calendar.dateComponents([.day], from: today, to: depDay).day ?? Int.max
        guard dayDiff >= 0 else { return false }   // only ever resolve to future/today flights

        if query.contains("today") || query.contains("tonight"), dayDiff == 0 { return true }
        if query.contains("tomorrow"), dayDiff == 1 { return true }
        if query.contains("weekend") {
            let wd = calendar.component(.weekday, from: departure)   // 1=Sun, 7=Sat
            if (wd == 1 || wd == 7) && dayDiff <= 8 { return true }
        }
        if query.contains("next week"), (7...13).contains(dayDiff) { return true }
        // Weekday names → the soonest upcoming instance (within a week).
        for (name, weekday) in weekdayNumbers where query.contains(name) {
            if calendar.component(.weekday, from: departure) == weekday && dayDiff <= 7 { return true }
        }
        return false
    }

    nonisolated private static let weekdayNumbers: [(String, Int)] = [
        ("sunday", 1), ("monday", 2), ("tuesday", 3), ("wednesday", 4),
        ("thursday", 5), ("friday", 6), ("saturday", 7),
    ]
}
