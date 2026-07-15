import Foundation

/// Response of `GET /api/flights/frequent-airports` — the user's top-5 departure
/// and top-5 destination airports, derived from flight history (#419, B3). There
/// is no `home_base` concept in the app; the forecast map uses this to centre on
/// cold open on the pilot's usual departure area. Either array may be empty.
///
/// Decoded with `JSONDecoder.weatherBrief` (snake→camel); no dynamic-key dicts.
struct FrequentAirportsResponse: Decodable, Sendable {
    let departures: [FrequentAirport]
    let destinations: [FrequentAirport]
}

/// One ranked airport: ICAO plus how many of the user's flights used it.
struct FrequentAirport: Decodable, Sendable, Identifiable {
    let icao: String
    let count: Int

    var id: String { icao }
}
