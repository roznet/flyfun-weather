import Foundation
import OSLog

/// Loading state for async data.
enum LoadingState<T> {
    case idle
    case loading
    case loaded(T)
    case error(Error)
}

extension LoadingState {
    /// Whether we currently hold loaded data. Used by a *quiet* reload to keep the
    /// existing content on screen (and swap in place) instead of flashing back to a
    /// spinner — the seamless-sync path.
    var hasData: Bool {
        if case .loaded = self { return true }
        return false
    }
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
    /// Flight IDs whose briefing is currently queued/refreshing server-side —
    /// drives the live "Updating…" row indicator. Fed by `pollActiveRefreshes`.
    private(set) var refreshingFlightIds: Set<String> = []

    private let repository: any BriefingRepository
    /// Live reachability — gates the active-refresh poll so it doesn't hammer the
    /// radio while offline. Optional so tests/fixtures can omit it (poll then
    /// always attempts, relying on error backoff).
    private let networkMonitor: NetworkMonitor?
    private var isLoading = false
    /// Per-flight latest-pack `fetchTimestamp`, snapshotted on every successful
    /// load. The level-triggered reconcile compares against this to tell whether
    /// a briefing actually advanced (#426).
    private var lastPackTimestamps: [String: String] = [:]
    /// Monotonic counter bumped on every authoritative list commit (a completed
    /// `loadFlights()` or a `reconcileLatestPacks()` write). The reconcile — which
    /// runs on `@MainActor` but suspends across its `repository.flights()` fetch —
    /// captures this before awaiting and drops its result if it changed, so a
    /// slow background reconcile can never clobber a fresher `loadFlights()` that
    /// completed during that suspension (#427 review).
    private var loadGeneration = 0
    /// The active-refresh poll loop (one at a time). Detached from any `.task`, so
    /// the view must start/stop it explicitly around visibility.
    @ObservationIgnored private var refreshPollTask: Task<Void, Never>?
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "FlightList")

    init(repository: any BriefingRepository, networkMonitor: NetworkMonitor? = nil) {
        self.repository = repository
        self.networkMonitor = networkMonitor
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
            let generation = loadGeneration
            let flights = try await repository.flights()
            // Mirror `reconcileLatestPacks()`'s guard in the other direction: if a
            // reconcile (or any authoritative commit) landed a strictly newer list
            // while our fetch was suspended, its data is at least as fresh as ours —
            // drop our write rather than reverting the row to older data (#427
            // review). Request ordering doesn't guarantee a load is fresher than a
            // concurrent reconcile, so both sides need the drop. No `await` sits
            // between this guard and the `loadGeneration` bump below, so the
            // compare-and-commit is race-free.
            guard generation == loadGeneration else {
                Self.logger.info("Dropping stale flight-list load; a newer commit won during fetch")
                return
            }
            // Check offline/cache status (via `CacheStatusReporting` so the DEBUG
            // fixture repo can present offline without a real cache, #318). The
            // sync `isServingCachedFlights` read stays inside the race-free window;
            // the async badge-set refresh runs *after* the commit, as in reconcile.
            let reporter = repository as? CacheStatusReporting
            if let reporter { isOffline = reporter.isServingCachedFlights }
            state = .loaded(flights)
            lastPackTimestamps = Self.packTimestampMap(flights)
            loadGeneration &+= 1   // authoritative commit — see reconcileLatestPacks
            if let reporter { await refreshCachedFlightIds(reporter) }
            // Keep Spotlight / Siri's flight index in sync with the server truth.
            // Fire-and-forget — never block the list on indexing (Decision 8).
            // Only when online: an offline cache snapshot can be stale/partial
            // (cold start before first sync), and reindex does delete-all-then-add,
            // which would transiently drop entries for flights not yet cached. The
            // next online load reconciles.
            if !isOffline {
                Task { await SpotlightDonator.reindex(flights) }
            }
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

    // MARK: - Active-refresh polling (live "Updating…" row indicator)

    /// Poll `GET /api/refresh/active` every 5s while the list is visible so a row
    /// shows a live "Updating…" state whenever its briefing is refreshing —
    /// including refreshes started elsewhere (web, auto-refresh, another device).
    /// iOS has no server push for progress, so this mirrors the web's flat 5s poll
    /// (a single user-scoped request per tick, independent of flight count).
    ///
    /// Bounded + idempotent: one loop at a time. When a flight *leaves* the active
    /// set — its refresh just finished — the list is re-synced so that row's
    /// summary/assessment updates immediately, making the same loop double as the
    /// completion trigger. Call `stopActiveRefreshPolling()` when the list is
    /// backgrounded or torn down.
    func startActiveRefreshPolling() {
        guard refreshPollTask == nil else { return }
        // `weak self` only guards the initial hop; once `pollActiveRefreshesLoop`
        // is running it holds a strong `self` for the loop's lifetime. That's
        // intentional — the loop is bounded by `stopActiveRefreshPolling()`
        // (`.onDisappear` / backgrounding), so the view model is released promptly
        // once polling stops; it just can't be freed mid-iteration.
        refreshPollTask = Task { [weak self] in
            await self?.pollActiveRefreshesLoop()
        }
    }

    /// Stop the active-refresh poll loop (list backgrounded / disappeared).
    func stopActiveRefreshPolling() {
        refreshPollTask?.cancel()
        refreshPollTask = nil
    }

    /// Healthy poll cadence (matches the web's flat 5s).
    private static let refreshPollBaseDelay: Double = 5
    /// Full-reconcile cadence, in healthy poll ticks (6 × 5s ≈ 30s). A level-
    /// triggered safety net for the active-set edge below: a refresh that begins
    /// *and* finishes between two polls — a fast refresh, or one started on the
    /// web / another device / by auto-refresh — never enters `refreshingFlightIds`,
    /// so the `finished` edge never fires and the row would stay stale until the
    /// next foreground / pull-to-refresh. Reconciling on this cadence catches it.
    private static let reconcileEveryTicks = 6

    private func pollActiveRefreshesLoop() async {
        var delay = Self.refreshPollBaseDelay
        var ticksSinceReconcile = 0
        while !Task.isCancelled {
            if networkMonitor?.isConnected == false {
                // Offline: don't even attempt the round-trip — no point waking the
                // radio every 5s. Clear stale indicators (we can't know a refresh is
                // still running) and back off; the next online tick repopulates.
                if !refreshingFlightIds.isEmpty { refreshingFlightIds = [] }
                delay = min(delay * 2, 30)
            } else {
                do {
                    let entries = try await repository.activeRefreshes()
                    guard !Task.isCancelled else { return }
                    let newIds = Set(entries.map(\.flightId))
                    // A flight dropping out of the set means its refresh just
                    // completed — pull fresh summaries so the row updates at once.
                    let finished = refreshingFlightIds.subtracting(newIds)
                    refreshingFlightIds = newIds
                    if !finished.isEmpty {
                        let genBefore = loadGeneration
                        await loadFlights()          // edge-triggered completion
                        // Only reset the level-triggered counter if that load
                        // actually committed (generation advanced). A no-op load
                        // (a concurrent manual refresh already held `isLoading`, or
                        // the load dropped as stale) reconciled nothing, so don't
                        // push the catch-up reconcile out a full 30s cycle for it.
                        if loadGeneration != genBefore { ticksSinceReconcile = 0 }
                    } else {
                        ticksSinceReconcile += 1
                        if ticksSinceReconcile >= Self.reconcileEveryTicks {
                            ticksSinceReconcile = 0
                            await reconcileLatestPacks()   // level-triggered catch-up
                        }
                    }
                    delay = Self.refreshPollBaseDelay   // healthy — reset cadence
                } catch {
                    // Offline blip / 5xx — back off (cap 60s) instead of hammering,
                    // and keep the last known set for one cycle.
                    delay = min(delay * 2, 60)
                    Self.logger.debug("activeRefreshes poll failed (retry in \(Int(delay))s): \(error)")
                }
            }
            try? await Task.sleep(for: .seconds(delay))
        }
    }

    /// Level-triggered reconcile: re-fetch the flights list and repaint only when
    /// a flight's latest-pack `fetchTimestamp` has advanced since the last load.
    /// This is the completion signal the active-refresh edge can miss (#426) — a
    /// refresh whose whole active lifetime fell between two polls still lands here
    /// once its new pack timestamp shows up. Quiet by design: it never flips the
    /// full-screen spinner or the `isRefreshing` indicator, and does nothing
    /// (beyond one cheap list fetch) when no briefing changed. Returns whether it
    /// repainted, for tests. Failures are swallowed — the next tick retries.
    @discardableResult
    func reconcileLatestPacks() async -> Bool {
        let generation = loadGeneration
        guard let flights = try? await repository.flights() else { return false }
        // An authoritative load (pull-to-refresh, foreground, external sync, the
        // completion edge, or a prior reconcile) committed while our fetch was in
        // flight — its result is at least as fresh as ours, so drop instead of
        // clobbering it back to stale data (#427 review). `loadGeneration` and
        // `state` only change on the main actor between our suspension points, so
        // this comparison is race-free.
        guard generation == loadGeneration else { return false }
        let fresh = Self.packTimestampMap(flights)
        guard fresh != lastPackTimestamps else { return false }
        loadGeneration &+= 1
        state = .loaded(flights)
        lastPackTimestamps = fresh
        // Keep the offline/cached-badge state consistent with the fresh list, the
        // same way loadFlights does (disk-only, no extra network).
        if let reporter = repository as? CacheStatusReporting {
            isOffline = reporter.isServingCachedFlights
            await refreshCachedFlightIds(reporter)
        }
        Self.logger.info("Reconcile repainted flight list (pack timestamps advanced)")
        return true
    }

    /// Per-flight latest-pack `fetchTimestamp`, keyed by flight id. Flights whose
    /// latest briefing carries no timestamp (never briefed, or a legacy server
    /// that omits the field) are absent from the map.
    static func packTimestampMap(_ flights: [FlightResponse]) -> [String: String] {
        var map: [String: String] = [:]
        for flight in flights {
            if let ts = flight.latestBriefing?.fetchTimestamp {
                map[flight.id] = ts
            }
        }
        return map
    }

    /// Refresh the "available offline" badge set from the offline-ready flights.
    /// Disk-only (no network) for the caching repo; shared by the cold-start seed
    /// and the post-fetch update so both paint a consistent set of offline badges.
    private func refreshCachedFlightIds(_ reporter: any CacheStatusReporting) async {
        cachedFlightIds = await reporter.offlineReadyFlightIds()
    }
}
