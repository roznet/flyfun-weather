import CoreSpotlight
import Foundation
import OSLog

/// Keeps the Spotlight index in sync with the user's flights so they're
/// searchable and Siri resolves "my flight to Cannes" better (via the semantic
/// index behind `FlightEntity: IndexedEntity`).
///
/// We reconcile the whole set on each flight-list load (which also runs right
/// after a create or delete): delete-all then re-index the current flights. The
/// default `CSSearchableIndex` is app-scoped, so delete-all only touches our
/// items — this purges server-deleted flights without needing a per-id delete,
/// and keeps donations from going stale (Decision 8).
enum SpotlightDonator {
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "Spotlight")

    static func reindex(_ flights: [FlightResponse]) async {
        #if DEBUG
        // Never donate UI-test fixtures to the real device index.
        if AppState.isUITesting { return }
        #endif
        let index = CSSearchableIndex.default()
        do {
            try await index.deleteAllSearchableItems()
            let entities = flights.map(FlightEntity.init)
            if !entities.isEmpty {
                try await index.indexAppEntities(entities)
            }
            logger.debug("Reindexed \(entities.count) flight(s) for Spotlight")
        } catch {
            // Non-fatal — Spotlight is a nicety, never block the list on it.
            logger.debug("Spotlight reindex skipped: \(error.localizedDescription)")
        }
    }
}
