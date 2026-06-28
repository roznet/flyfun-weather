import Foundation
import RZFlight

/// One ICAO autocomplete suggestion (Sendable projection of RZFlight's `Airport`).
struct AirportSuggestion: Identifiable, Hashable, Sendable {
    var id: String { icao }
    let icao: String
    let name: String
}

/// Drives dynamic ICAO autocomplete for the route field.
///
/// Design notes (matching the "fast to type" requirement):
/// - The *keystroke* never does work — the view debounces (`.task(id:)` cancels
///   the superseded run), so typing is never blocked.
/// - We only ever complete the **last** token in the field. If the user keeps
///   typing past the pause, the debounce fires again for the new last token, so
///   stale results can't appear for an already-finished waypoint.
/// - `rankedSearch` is an in-memory tiered ICAO/name lookup over ~8.5k airports
///   (sub-millisecond), so it runs on the main actor after the debounce — no
///   background hop needed, and no chance of a data race on the shared DB.
@Observable
@MainActor
final class RouteAutocompleteController {
    /// Suggestions for the current last token (empty when nothing matches, the
    /// token is too short, or the DB hasn't been downloaded yet).
    private(set) var suggestions: [AirportSuggestion] = []

    /// Token separators must match `AddFlightViewModel.waypoints` parsing.
    private static let separators = CharacterSet(charactersIn: " -,")

    /// The trailing, still-being-typed identifier (uppercased). Empty when the
    /// field is empty or ends with a separator (i.e. the previous token is done).
    static func lastToken(in text: String) -> String {
        text.uppercased().components(separatedBy: separators).last ?? ""
    }

    /// Recompute suggestions for the field's current last token. Cheap; intended
    /// to be called from a debounced `.task(id:)`.
    func update(for text: String) {
        let token = Self.lastToken(in: text)
        // Need at least 2 chars to keep the list tight (a single letter matches
        // hundreds of ICAOs and isn't useful).
        guard token.count >= 2 else {
            suggestions = []
            return
        }
        suggestions = AirportDatabase.shared.search(needle: token, limit: 8)
            .map { AirportSuggestion(icao: $0.icao, name: $0.name) }
    }

    func clear() {
        suggestions = []
    }
}
