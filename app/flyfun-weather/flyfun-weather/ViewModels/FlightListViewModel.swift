import Foundation
import OSLog

/// Loading state for async data.
enum LoadingState<T> {
    case idle
    case loading
    case loaded(T)
    case error(Error)
}

/// View model for the flight list screen.
@Observable
@MainActor
final class FlightListViewModel {
    private(set) var state: LoadingState<[FlightResponse]> = .idle
    /// True while a refresh runs *over an already-loaded list* — drives a subtle
    /// in-place indicator instead of replacing the list with a spinner (#1).
    private(set) var isRefreshing = false
    /// True when the flight list was served from cache (server unreachable).
    private(set) var isOffline = false
    /// Flight IDs that have downloaded pack data available offline.
    private(set) var cachedFlightIds: Set<String> = []

    private let repository: any BriefingRepository
    private var isLoading = false
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "FlightList")

    init(repository: any BriefingRepository) {
        self.repository = repository
    }

    func loadFlights() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        // Only show the full-screen spinner on the very first load. A refresh
        // (scene re-activation, pull-to-refresh, post-edit) keeps the current
        // list on screen and swaps in fresh data when it arrives, so reopening
        // the app no longer flashes the list to empty (#1, iOS feedback).
        let hadList: Bool
        if case .loaded = state {
            hadList = true
            isRefreshing = true
        } else {
            hadList = false
            state = .loading
        }
        defer { isRefreshing = false }
        do {
            let flights = try await repository.flights()
            // Check offline/cache status
            if let caching = repository as? CachingBriefingRepository {
                isOffline = caching.isServingCachedFlights
                let packs = await caching.cachedPacks()
                cachedFlightIds = Set(packs.map(\.flightId))
            }
            state = .loaded(flights)
            Self.logger.info("Loaded \(flights.count) flights (offline=\(self.isOffline), cached=\(self.cachedFlightIds.count))")
        } catch {
            // Keep an already-loaded list on screen if a refresh fails — the
            // stale list beats an error wall. Only surface the error when we
            // have nothing to show.
            if hadList {
                Self.logger.error("Flight-list refresh failed, keeping current list: \(error)")
            } else {
                state = .error(error)
                Self.logger.error("Failed to load flights: \(error)")
            }
        }
    }
}
