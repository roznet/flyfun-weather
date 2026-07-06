import Foundation

/// A repository that can report offline / cache status to the flight list.
///
/// Extracted so `FlightListViewModel` reads offline state through a protocol
/// instead of down-casting to the concrete `CachingBriefingRepository` — the
/// same decoupling the #359 online-layer refactor applied inside the caching
/// repo. It lets the DEBUG `FixtureBriefingRepository` present a deterministic
/// offline scenario for the XCUITest offline journey (#318) without a real
/// on-disk cache or network fault.
protocol CacheStatusReporting {
    /// Whether the last `flights()` was served from an offline cache (server
    /// unreachable). Drives the flight list's offline banner + read-only mode.
    var isServingCachedFlights: Bool { get }

    /// Flight IDs that have a full pack available offline, so their row stays
    /// tappable (and un-dimmed) while offline.
    func offlineReadyFlightIds() async -> Set<String>
}
