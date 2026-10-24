import Foundation

/// Spoken-line builders shared by the background (voice) intents. Kept pure and
/// `FlightResponse`-driven so the phrasing is one place and unit-testable.
enum IntentDialogs {
    /// One flight's assessment + top concerns, e.g.
    /// "Your LFMD → LFML flight is graded amber. Top concerns: convective and icing."
    ///
    /// With a `live` layer that carries data (#365), the live lines lead and the
    /// grade follows, labelled as the briefing's: see `liveCheckSummary`.
    /// Without one (outside the window, fetch failed, no data yet) the line is
    /// exactly the briefing-only one.
    static func checkSummary(_ flight: FlightResponse, live: LiveLayerResponse? = nil) -> String {
        if let lines = liveCheckLines(live) {
            return liveCheckSummary(flight, lines: lines)
        }
        let route = flight.shortTitle
        switch briefingState(flight.latestBriefing) {
        case .notBriefed:
            if let coverage = flight.coverage, let day = coverage.availableDay {
                return "Your \(route) flight isn't in forecast range yet — a briefing is expected \(IntentSupport.mediumDate(day))."
            }
            return "Your \(route) flight hasn't been briefed yet."
        case .unavailable:
            // #392: "graded unavailable" reads as a verdict. It is the absence of
            // one — say so, in the same register as the web/iOS copy.
            return "I couldn't assess your \(route) flight — there's no usable model data for that route yet."
        case .graded(let grade, let top):
            var line = "Your \(route) flight is graded \(grade)."
            if !top.isEmpty {
                line += " Top \(concerns(top))."
            }
            return line
        case .outlook(let outlook):
            return "Your \(route) flight has a long-range outlook of \(outlook)."
        case .noAssessment:
            return "Your \(route) flight has no assessment yet."
        }
    }

    /// What the latest briefing says, before it is worded. The one decision
    /// ladder behind `checkSummary` (briefing-only) and `briefingGradeLine`
    /// (closing a live check), which phrase it differently.
    private enum BriefingState {
        case notBriefed
        /// `UNAVAILABLE`: the absence of a grade, never said as one (#392).
        case unavailable
        /// Lowercased grade and the server's top concerns (possibly empty).
        case graded(String, top: [AdvisoryChip])
        /// Humanized long-range outlook.
        case outlook(String)
        case noAssessment
    }

    private static func briefingState(_ briefing: BriefingStatusInfo?) -> BriefingState {
        guard let briefing else { return .notBriefed }
        if let assessment = briefing.assessment?.uppercased() {
            if assessment == "UNAVAILABLE" { return .unavailable }
            return .graded(assessment.lowercased(), top: briefing.advisorySummary?.top ?? [])
        }
        if let outlook = briefing.outlook { return .outlook(humanize(outlook)) }
        return .noAssessment
    }

    /// The card's note for a flight with no traffic-light grade to show (not
    /// briefed, out of range, `UNAVAILABLE`, outlook only): the same words the
    /// dialog speaks. nil when there is a GREEN / AMBER / RED grade, which the
    /// card draws as a badge instead.
    static func briefingNote(_ flight: FlightResponse) -> String? {
        switch flight.latestBriefing?.assessment?.uppercased() {
        case "GREEN", "AMBER", "RED": return nil
        default: return checkSummary(flight)
        }
    }

    // MARK: - Live layer (#365)

    /// What a flight-day check takes from the live layer, in the order it is
    /// said: alert-tier lines, then the highlight (with when it was written) or
    /// the headline. Server text verbatim: clients render, they don't derive
    /// (the `LiveGlance` SYNC contract).
    nonisolated struct LiveCheckLines: Equatable, Sendable {
        /// `glance.alertItems` texts: never folded, always first.
        let alerts: [String]
        /// The highlight, or the headline when there is no highlight.
        let summary: String?
        /// "14:29Z" when `summary` is the highlight. nil for the headline,
        /// which carries its own "Observed 14:29Z".
        let summaryTime: String?
    }

    /// The live lines to lead a check with, or nil when the layer has nothing
    /// to say (absent, `!hasData`, no glance, or a glance with neither alerts
    /// nor a highlight / headline). nil means "behave exactly as without live".
    static func liveCheckLines(_ layer: LiveLayerResponse?) -> LiveCheckLines? {
        guard let layer, layer.hasData, let glance = layer.glance else { return nil }
        let alerts = glance.alertItems.compactMap { nonEmpty($0.text) }
        let summary: String?
        let summaryTime: String?
        if let highlight = glance.highlight, let text = nonEmpty(highlight.text) {
            summary = text
            summaryTime = highlight.generatedAt.flatMap(Date.parseISO8601).map(LiveTime.zulu)
        } else {
            summary = nonEmpty(glance.headline)
            summaryTime = nil
        }
        guard !alerts.isEmpty || summary != nil else { return nil }
        return LiveCheckLines(alerts: alerts, summary: summary, summaryTime: summaryTime)
    }

    /// "SIGMET 6 EMBD TS now covers EGTF. As of 14:29Z: Departure and route
    /// as briefed. The 07:12Z briefing graded it amber; top concern: convective."
    ///
    /// The grade is the briefing's, said as such and never reconciled with the
    /// live text: the live layer annotates, it never re-grades (same rule as
    /// the MCP `live` block).
    private static func liveCheckSummary(_ flight: FlightResponse, lines: LiveCheckLines) -> String {
        var parts = lines.alerts.map(sentence)
        if let summary = lines.summary {
            if let time = lines.summaryTime {
                parts.append("As of \(time): \(sentence(summary))")
            } else {
                parts.append(sentence(summary))
            }
        }
        parts.append(briefingGradeLine(flight))
        return parts.joined(separator: " ")
    }

    /// The briefing's grade as the closing line of a live check, labelled with
    /// when the briefing was built ("The 07:12Z briefing graded it amber").
    static func briefingGradeLine(_ flight: FlightResponse) -> String {
        guard let briefing = flight.latestBriefing else {
            return "Your \(flight.shortTitle) flight hasn't been briefed yet."
        }
        let label = briefingTime(briefing).map { "The \($0) briefing" } ?? "The briefing"
        switch briefingState(briefing) {
        case .notBriefed, .noAssessment:
            return "\(label) has no assessment yet."
        case .unavailable:
            return "\(label) couldn't assess it: there was no usable model data for that route."
        case .graded(let grade, let top):
            var line = "\(label) graded it \(grade)"
            if !top.isEmpty {
                line += "; top \(concerns(top))"
            }
            return line + "."
        case .outlook(let outlook):
            return "\(label) gave a long-range outlook of \(outlook)."
        }
    }

    /// "07:12Z" for the briefing's pack, or nil when the server sent none.
    static func briefingTime(_ briefing: BriefingStatusInfo) -> String? {
        briefing.fetchTimestamp.flatMap(Date.parseISO8601).map(LiveTime.zulu)
    }

    /// "concern: convective" / "concerns: convective and icing" (first two).
    private nonisolated static func concerns(_ top: [AdvisoryChip]) -> String {
        "concern\(top.count > 1 ? "s" : ""): " + top.prefix(2).map(\.name).joined(separator: " and ")
    }

    /// One-line-per-flight traffic-light overview of upcoming flights, always
    /// soonest-first.
    ///
    /// Deliberately does NOT follow the `flight_order` display preference
    /// (#536), for the same reason `FlightResolver.nextFlight` doesn't: this is a
    /// spoken answer to "what's coming up", not a rendering of the list. It also
    /// truncates to `listLimit`, so following a furthest-first preference would
    /// read out the five most *distant* flights and silently omit the one
    /// departing tomorrow. Entity pickers follow the display preference; spoken
    /// "next/upcoming" answers stay chronological.
    static func overviewSummary(_ flights: [FlightResponse], now: Date = Date()) -> String {
        // The `.filter` is NOT redundant with the ordering call:
        // `orderedForSuggestions` returns `upcoming + past` (it is the entity
        // picker's list, which deliberately includes history so you can pick a
        // past flight). This strips that past suffix — without it the overview
        // would announce every past flight as upcoming.
        let upcoming = FlightResolver.orderedForSuggestions(flights, order: .soonestFirst, now: now)
            .filter { FlightResolver.isUpcoming($0, now: now) }
        guard !upcoming.isEmpty else { return "You have no upcoming flights." }
        let listLimit = 5
        let lines = upcoming.prefix(listLimit).map { flight -> String in
            let rawAssessment = flight.latestBriefing?.assessment?.uppercased()
            let status = (rawAssessment == "UNAVAILABLE" ? "couldn't be assessed" : nil)
                ?? flight.latestBriefing?.assessment?.lowercased()
                ?? flight.latestBriefing?.outlook.map(humanize)
                ?? "not yet briefed"
            return "\(flight.shortTitle), \(status)"
        }
        var joined = lines.joined(separator: "; ")
        let count = upcoming.count
        let remaining = count - lines.count
        if remaining > 0 {
            joined += "; and \(remaining) more"
        }
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

    private nonisolated static func humanize(_ raw: String) -> String {
        raw.replacingOccurrences(of: "_", with: " ").lowercased()
    }

    private nonisolated static func nonEmpty(_ text: String?) -> String? {
        guard let trimmed = text?.trimmingCharacters(in: .whitespacesAndNewlines), !trimmed.isEmpty else { return nil }
        return trimmed
    }

    /// Server text as a spoken sentence: verbatim, with a full stop added only
    /// when it has no closing punctuation, so consecutive lines don't run on.
    private nonisolated static func sentence(_ text: String) -> String {
        guard let last = text.last, !".!?".contains(last) else { return text }
        return text + "."
    }

}
