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
    ///
    /// `parser` is injectable so the on-device selection tier is unit-testable
    /// with a fake; production uses the Foundation Models resolver.
    @MainActor
    static func resolve(
        _ raw: String,
        in flights: [FlightResponse],
        now: Date = Date(),
        parser: FlightPhraseResolving = FoundationModelsPhraseResolver()
    ) async -> [FlightResponse] {
        await IntentSupport.ensureAirportDatabase()
        let calendar = Calendar.current
        let query = raw.lowercased()

        // Resolution is scoped to today-and-future flights — the design's "closed
        // set of *upcoming* flights". Both tiers match over this set, so a
        // long-past flown trip never resolves a voice request (no stale briefing,
        // no confusing old-vs-new disambiguation). Explicit id lookups
        // (`entities(for:)`) and the Shortcuts picker (`suggestedEntities`) still
        // see history — only free-phrase resolution is scoped here.
        let candidates = upcoming(flights, now: now, calendar: calendar)

        // Tier 1 — deterministic. Prefer flights matching BOTH a place and a date
        // (narrows "the flight tomorrow to Fairoaks"); else either signal.
        let both = candidates.filter {
            matchesPlace($0, query: query) && matchesRelativeDate($0, query: query, now: now, calendar: calendar)
        }
        if !both.isEmpty { return both }
        let either = candidates.filter {
            matchesPlace($0, query: query) || matchesRelativeDate($0, query: query, now: now, calendar: calendar)
        }
        if !either.isEmpty { return either }

        // Tier 2 — grounded on-device selection over the same upcoming set. The
        // model sees the real candidates (route + airport names + date) and picks
        // one; we validate the returned id against the set, so it can never
        // surface a flight that doesn't exist. Skipped (nil) when the model is
        // unavailable.
        let modelCandidates = candidates.map {
            FlightCandidate(id: $0.id, line: candidateLine($0))
        }
        if !modelCandidates.isEmpty {
            let pickedId = await parser.pick(phrase: raw, today: IntentSupport.mediumDate(now), candidates: modelCandidates)
            if let pickedId, let picked = candidates.first(where: { $0.id == pickedId }) {
                return [picked]
            }
        }

        // Tier 3 — nothing matched; let the intent handle the empty result.
        return []
    }

    // MARK: - Grounded-selection candidates

    /// Today-or-later flights, soonest first — the closed set handed to the
    /// on-device model (tier 2). "Today" is by calendar day, so a flight earlier
    /// today is still offered.
    nonisolated static func upcoming(
        _ flights: [FlightResponse], now: Date = Date(), calendar: Calendar = .current
    ) -> [FlightResponse] {
        let todayStart = calendar.startOfDay(for: now)
        return flights
            .filter { ($0.departureDate ?? .distantPast) >= todayStart }
            .sorted { ($0.departureDate ?? .distantFuture) < ($1.departureDate ?? .distantFuture) }
    }

    /// One model-facing candidate line, e.g.
    /// "EGKB (London Biggin Hill, London, GB) → EGTF (Fairoaks, GB), 9 Jul 2026".
    /// The airport name + city + ISO country give the on-device model enough to
    /// resolve world-knowledge references ("my France trip", "my beach flight").
    @MainActor
    static func candidateLine(_ flight: FlightResponse) -> String {
        let origin = flight.waypoints.first?.uppercased() ?? "?"
        let dest = flight.waypoints.last?.uppercased() ?? "?"
        let route = "\(airportLabel(origin)) → \(airportLabel(dest))"
        if let date = flight.departureDate { return "\(route), \(IntentSupport.mediumDate(date))" }
        return route
    }

    /// "ICAO (Name, City, Country)" from the local DB. The DB read is `@MainActor`;
    /// the formatting rules are the pure overload below (so they're unit-testable
    /// without the `AirportDatabase.shared` singleton).
    @MainActor
    private static func airportLabel(_ icao: String) -> String {
        guard let airport = AirportDatabase.shared.airport(icao: icao) else { return icao }
        return airportLabel(icao: icao, name: airport.name, city: airport.city, country: airport.country)
    }

    /// Pure "ICAO (Name, City, Country)" formatter — skips any empty component
    /// (the slim DB may not carry every column) and a city that just repeats the
    /// name. Falls back to the bare ICAO when nothing else is known.
    nonisolated static func airportLabel(icao: String, name: String, city: String, country: String) -> String {
        var parts: [String] = []
        if !name.isEmpty { parts.append(name) }
        if !city.isEmpty, city != name { parts.append(city) }
        if !country.isEmpty { parts.append(country) }
        return parts.isEmpty ? icao : "\(icao) (\(parts.joined(separator: ", ")))"
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
