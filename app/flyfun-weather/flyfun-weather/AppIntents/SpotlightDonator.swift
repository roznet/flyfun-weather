import AppIntents
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

    /// The in-flight reindex, so overlapping calls (repeated `.active` reloads
    /// near launch) chain instead of interleaving a second delete-all with the
    /// first call's still-running insert — which would transiently empty or
    /// duplicate the index. `SpotlightDonator` is main-actor isolated, so the
    /// read-then-write of `pending` below is atomic (no `await` between them).
    private static var pending: Task<Void, Never>?

    static func reindex(_ flights: [FlightResponse]) async {
        #if DEBUG
        // Never donate UI-test fixtures to the real device index.
        if AppState.isUITesting { return }
        #endif
        let previous = pending
        let task = Task {
            await previous?.value
            await performReindex(flights)
        }
        pending = task
        await task.value
    }

    private static func performReindex(_ flights: [FlightResponse]) async {
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
