import Foundation

/// The words a pilot can say for each upcoming flight inside a Siri phrase:
/// "Read my FlyFun briefing for **Le Touquet**", "Brief me on my FlyFun flight
/// **from Le Touquet**". Siri matches the spoken value against these synonyms
/// (registered via `FlyFunShortcuts.updateAppShortcutParameters()`).
///
/// One rule keeps them unambiguous: a bare place or ICAO, "to X" and "from X"
/// belong to the **soonest** upcoming flight they describe. So with an
/// out-and-back tomorrow, "Le Touquet" is the outbound (it goes *to* Le
/// Touquet), "from Le Touquet" and "Fairoaks" are the return, and next week's
/// Le Touquet trip answers only to its full "Fairoaks to Le Touquet on Friday"
/// form. Every flight also answers to "Origin to Destination".
///
/// Pure (names injected) so it is unit-tested without the airport database.
nonisolated enum FlightSpokenNames {
    /// Synonyms by flight id. `flights` is the upcoming set; it is sorted
    /// soonest-first here, so callers needn't. `names` maps ICAO → speakable
    /// name (`SpokenText.airportNames`); unknown codes fall back to the ICAO.
    static func synonyms(
        for flights: [FlightResponse], names: [String: String], now: Date = Date()
    ) -> [String: [String]] {
        let ordered = flights.sorted { ($0.departureDate ?? .distantFuture) < ($1.departureDate ?? .distantFuture) }
        var claimed = Set<String>()
        var result: [String: [String]] = [:]

        for flight in ordered {
            guard let originIcao = flight.waypoints.first?.uppercased(),
                  let destinationIcao = flight.waypoints.last?.uppercased() else { continue }
            let origin = names[originIcao] ?? originIcao
            let destination = names[destinationIcao] ?? destinationIcao
            var words: [String] = []

            /// Adds `phrase` only if no sooner flight already owns it.
            func claim(_ phrase: String) {
                let key = phrase.lowercased()
                guard !claimed.contains(key) else { return }
                claimed.insert(key)
                words.append(phrase)
            }

            claim("\(origin) to \(destination)")
            claim("from \(origin) to \(destination)")
            claim(destination)
            claim("to \(destination)")
            claim(destinationIcao)
            claim(spelled(destinationIcao))
            claim("from \(origin)")
            claim("from \(spelled(originIcao))")
            if let departure = flight.departureDate {
                // "Fairoaks to Le Touquet tomorrow" / "… on Friday": the
                // date-qualified form is what a later same-route flight answers to.
                let when = IntentDialogs.whenPhrase(departure, now: now)
                    .components(separatedBy: " at ").first ?? ""
                if !when.isEmpty {
                    claim("\(origin) to \(destination) \(when)")
                    claim("to \(destination) \(when)")
                }
            }
            result[flight.id] = words
        }
        return result
    }

    /// "LFAT" → "L F A T", how Siri transcribes a code read letter by letter.
    static func spelled(_ icao: String) -> String {
        icao.map(String.init).joined(separator: " ")
    }
}
