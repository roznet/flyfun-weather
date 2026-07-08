import Foundation

/// Spoken-line builders shared by the background (voice) intents. Kept pure and
/// `FlightResponse`-driven so the phrasing is one place and unit-testable.
enum IntentDialogs {
    /// One flight's assessment + top concerns, e.g.
    /// "Your LFMD → LFML flight is graded amber. Top concerns: convective and icing."
    static func checkSummary(_ flight: FlightResponse) -> String {
        let route = flight.shortTitle
        guard let briefing = flight.latestBriefing else {
            if let coverage = flight.coverage, let day = coverage.availableDay {
                return "Your \(route) flight isn't in forecast range yet — a briefing is expected \(IntentSupport.mediumDate(day))."
            }
            return "Your \(route) flight hasn't been briefed yet."
        }
        if let assessment = briefing.assessment?.uppercased() {
            var line = "Your \(route) flight is graded \(assessment.lowercased())."
            if let top = briefing.advisorySummary?.top, !top.isEmpty {
                let names = top.prefix(2).map(\.name).joined(separator: " and ")
                line += " Top concern\(top.count > 1 ? "s" : ""): \(names)."
            }
            return line
        }
        if let outlook = briefing.outlook {
            return "Your \(route) flight has a long-range outlook of \(humanize(outlook))."
        }
        return "Your \(route) flight has no assessment yet."
    }

    /// One-line-per-flight traffic-light overview of upcoming flights.
    static func overviewSummary(_ flights: [FlightResponse], now: Date = Date()) -> String {
        let upcoming = FlightResolver.orderedForSuggestions(flights, now: now)
            .filter { ($0.departureDate ?? .distantPast) >= now }
        guard !upcoming.isEmpty else { return "You have no upcoming flights." }
        let lines = upcoming.prefix(5).map { flight -> String in
            let status = flight.latestBriefing?.assessment?.lowercased()
                ?? flight.latestBriefing?.outlook.map(humanize)
                ?? "not yet briefed"
            return "\(flight.shortTitle), \(status)"
        }
        let joined = lines.joined(separator: "; ")
        let count = upcoming.count
        return "You have \(count) upcoming flight\(count == 1 ? "" : "s"): \(joined)."
    }

    /// Consensus category + wind for an airport, plus a resolution note when the
    /// requested airport was snapped to the nearest monitored one and the latest
    /// observation for D-0.
    ///
    /// The main sentence keys off `entry.icao` — the airport the forecast data is
    /// actually *for* — not the originally-requested ICAO, so a snapped result
    /// never attributes the nearest airport's numbers back to the unmonitored one.
    static func airportWeather(_ entry: AirportWeatherEntry, dayLabel: String) -> String {
        let location = entry.icao.uppercased()
        let category = entry.consensus?.flightCategory ?? "unavailable"
        var sentence = "\(dayLabel), \(location) is forecast \(category)"
        if let wind = windPhrase(speed: entry.consensus?.windSpeedKt, direction: entry.consensus?.windDirDeg) {
            sentence += " with \(wind)"
        }
        sentence += "."
        if let requested = entry.requestedIcao,
           requested.uppercased() != location {
            sentence = "\(requested.uppercased()) isn't monitored, so here's the nearest, \(location). " + sentence
        }
        if let observation = entry.observation, let observed = observation.flightCategory {
            sentence += " The latest report is \(observed)."
        }
        return sentence
    }

    private static func windPhrase(speed: Double?, direction: Double?) -> String? {
        guard let speed else { return nil }
        var phrase = "wind \(Int(speed.rounded())) knots"
        if let direction {
            phrase += " from \(Int(direction.rounded())) degrees"
        }
        return phrase
    }

    // MARK: - Formatting

    private static func humanize(_ raw: String) -> String {
        raw.replacingOccurrences(of: "_", with: " ").lowercased()
    }

}
