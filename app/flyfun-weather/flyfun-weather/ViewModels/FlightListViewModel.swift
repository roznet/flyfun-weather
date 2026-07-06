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
        // Cache-first cold start: if a cached list is on disk, paint it before
        // touching the network so a fresh open never blocks on `/api/flights`
        // behind a full-screen spinner (#359). Seeding from `.idle` turns the
        // load below into the #1 warm-refresh path — the seeded list stays on
        // screen behind the subtle indicator while fresh data swaps in. The
        // server stays authoritative: the network result always replaces the
        // seed (or the offline fallback keeps it, with `isOffline` set).
        var seededCaching: CachingBriefingRepository?
        if case .idle = state,
           let caching = repository as? CachingBriefingRepository,
           let cached = await caching.cachedFlights(), !cached.isEmpty {
            state = .loaded(cached)
            seededCaching = caching
        }
        // Only show the full-screen spinner on the very first load. A refresh
        // (scene re-activation, pull-to-refresh, post-edit) keeps the current
        // list on screen and swaps in fresh data when it arrives, so reopening
        // the app no longer flashes the list to empty (#1, iOS feedback).
        // NOTE: no `await` may sit between the seed paint above and setting
        // `isRefreshing` — a seeded list must always paint *with* the subtle
        // indicator, never briefly as a bare `.loaded`.
        let hadList: Bool
        if case .loaded = state {
            hadList = true
            isRefreshing = true
        } else {
            hadList = false
            state = .loading
        }
        defer { isRefreshing = false }
        // Now that the refresh indicator is set, seed the offline badges from
        // disk so cached flights don't briefly flash as not-available-offline
        // before the network fetch lands (#359 review). `cachedPacks()` is
        // disk-only (no network).
        if let seededCaching {
            await refreshCachedFlightIds(seededCaching)
        }
        do {
            let flights = try await repository.flights()
            // Check offline/cache status (via `CacheStatusReporting` so the DEBUG
            // fixture repo can present offline without a real cache, #318).
            if let reporter = repository as? CacheStatusReporting {
                isOffline = reporter.isServingCachedFlights
                await refreshCachedFlightIds(reporter)
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

    /// Refresh the "available offline" badge set from the offline-ready flights.
    /// Disk-only (no network) for the caching repo; shared by the cold-start seed
    /// and the post-fetch update so both paint a consistent set of offline badges.
    private func refreshCachedFlightIds(_ reporter: any CacheStatusReporting) async {
        cachedFlightIds = await reporter.offlineReadyFlightIds()
    }
}
