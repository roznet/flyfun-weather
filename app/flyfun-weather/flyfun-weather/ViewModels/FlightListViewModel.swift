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
        state = .loading
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
            state = .error(error)
            Self.logger.error("Failed to load flights: \(error)")
        }
    }
}
