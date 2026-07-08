import AppIntents
import Foundation

/// Resolves `FlightEntity` for Siri / Shortcuts / Spotlight over the user's tiny,
/// closed set of upcoming flights.
///
/// The heavy lifting — deterministic place↔ICAO + relative-date matching, with an
/// on-device Foundation Models fallback — lives in `FlightResolver`. This type is
/// just the App Intents surface: load the flights (cache-first, so it works
/// offline) and hand them to the resolver.
struct FlightEntityQuery: EntityStringQuery {
    /// Natural-language match: "the flight tomorrow to Fairoaks", "my Cannes
    /// trip". Siri disambiguates when more than one survives.
    func entities(matching string: String) async throws -> [FlightEntity] {
        let flights = try await Self.loadFlights()
        let matched = await FlightResolver.resolve(string, in: flights)
        return matched.map(FlightEntity.init)
    }

    /// Resolve concrete ids (Shortcuts stores a chosen entity by id).
    func entities(for identifiers: [FlightEntity.ID]) async throws -> [FlightEntity] {
        let wanted = Set(identifiers)
        let flights = try await Self.loadFlights()
        return flights.filter { wanted.contains($0.id) }.map(FlightEntity.init)
    }

    /// Suggestions shown in the Shortcuts parameter picker — upcoming flights
    /// first (soonest at top), then the rest most-recent-first.
    func suggestedEntities() async throws -> [FlightEntity] {
        let flights = try await Self.loadFlights()
        return FlightResolver.orderedForSuggestions(flights).map(FlightEntity.init)
    }

    /// Cache-first flight load, shared by every query path. Throws
    /// `IntentAuthError.signedOut` when there's no JWT so the calling intent can
    /// surface the sign-in prompt (Decision 4).
    static func loadFlights() async throws -> [FlightResponse] {
        guard await IntentSupport.isSignedIn else { throw IntentAuthError.signedOut }
        let repo = await IntentSupport.makeRepository()
        return try await repo.flights()
    }
}
