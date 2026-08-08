import AppIntents
import Foundation

/// Resolves `FlightEntity` for Siri / Shortcuts / Spotlight over the user's tiny,
/// closed set of upcoming flights.
///
/// The heavy lifting — deterministic place↔ICAO + relative-date matching, with an
/// on-device Foundation Models fallback — lives in `FlightResolver`. This type is
/// just the App Intents surface: load the flights (cache-first, so it works
/// offline) and hand them to the resolver.
///
/// Resolution runs *before* an intent's `perform()`, so a load failure here must
/// degrade to an empty match (→ Siri's own "couldn't find it" / disambiguation),
/// never a thrown error — otherwise the intent's own signed-out / error dialog
/// (Decision 4) would be pre-empted by a raw resolution failure. When the user is
/// signed out but has a cached flight list, resolution still succeeds from cache,
/// so `perform()` runs and speaks the sign-in line.
struct FlightEntityQuery: EntityStringQuery {
    /// Natural-language match: "the flight tomorrow to Fairoaks", "my Cannes
    /// trip". Siri disambiguates when more than one survives.
    func entities(matching string: String) async throws -> [FlightEntity] {
        guard let flights = try? await Self.loadFlights() else { return [] }
        let matched = await FlightResolver.resolve(string, in: flights)
        return matched.map(FlightEntity.init)
    }

    /// Resolve concrete ids (Shortcuts stores a chosen entity by id).
    func entities(for identifiers: [FlightEntity.ID]) async throws -> [FlightEntity] {
        let wanted = Set(identifiers)
        guard let flights = try? await Self.loadFlights() else { return [] }
        return flights.filter { wanted.contains($0.id) }.map(FlightEntity.init)
    }

    /// Suggestions shown in the Shortcuts parameter picker — upcoming flights
    /// first (in the user's chosen order), then the rest most-recent-first.
    ///
    /// The preference is read from the cached-prefs blob via a `nonisolated`
    /// accessor rather than the `@MainActor` store, so this stays off the main
    /// actor. Shortcuts caches suggested entities, so a preference flip may not
    /// reorder its picker until iOS refreshes them; in-app order is immediate.
    func suggestedEntities() async throws -> [FlightEntity] {
        guard let flights = try? await Self.loadFlights() else { return [] }
        let order = UserPreferencesStore.cachedFlightOrder()
        return FlightResolver.orderedForSuggestions(flights, order: order).map(FlightEntity.init)
    }

    /// Cache-first flight load, shared by every query path. Does *not* gate on
    /// sign-in: a signed-out user with a cached list still resolves flights (so
    /// the intent's own Decision-4 guard can speak the sign-in line), and a hard
    /// failure (signed out, no cache) throws — callers translate that to an empty
    /// match rather than propagating it out of resolution.
    static func loadFlights() async throws -> [FlightResponse] {
        let repo = await IntentSupport.makeRepository()
        return try await repo.flights()
    }
}
