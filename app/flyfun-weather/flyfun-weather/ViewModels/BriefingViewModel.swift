import Foundation
import OSLog

enum BriefingTab: String, Hashable {
    case advisories
    case crossSection
    case map
    case digest
    case pireps
}

/// Refresh pipeline state.
enum RefreshState: Equatable {
    case idle
    case refreshing(stage: String, detail: String?, progress: Double)
    case completed(elapsedSeconds: Double)
    case error(String)

    var isRefreshing: Bool {
        if case .refreshing = self { return true }
        return false
    }
}

/// View model for the full briefing viewer.
@Observable
@MainActor
final class BriefingViewModel {
    let flight: FlightResponse
    private let repository: any BriefingRepository

    // Pack metadata
    private(set) var pack: PackMetaResponse?
    private(set) var packHistory: [PackMetaResponse] = []

    // Section states
    private(set) var advisoriesState: LoadingState<AdvisoriesResponse> = .idle
    private(set) var digestState: LoadingState<DigestResponse> = .idle
    private(set) var snapshotState: LoadingState<SnapshotResponse> = .idle
    private(set) var routeAnalysesState: LoadingState<RouteAnalysesResponse> = .idle
    private(set) var elevationState: LoadingState<ElevationResponse> = .idle
    private(set) var pirepsState: LoadingState<[PirepResponse]> = .idle

    // Refresh state
    private(set) var refreshState: RefreshState = .idle

    // Download/cache state
    private(set) var downloadState: DownloadState = .notDownloaded
    private(set) var packCacheStatus: [String: Bool] = [:] // timestamp -> isCached

    // UI state
    var selectedTab: BriefingTab = .advisories
    var selectedModel: String = "gfs"
    var availableModels: [String] = []
    var selectedPackTimestamp: String = "" {
        didSet {
            guard oldValue != selectedPackTimestamp, !selectedPackTimestamp.isEmpty else { return }
            if let newPack = packHistory.first(where: { $0.fetchTimestamp == selectedPackTimestamp }) {
                pack = newPack
                downloadState = (packCacheStatus[selectedPackTimestamp] == true) ? .downloaded : .notDownloaded
                Task { await loadPackData(timestamp: selectedPackTimestamp) }
            }
        }
    }

    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "Briefing")

    init(flight: FlightResponse, repository: any BriefingRepository) {
        self.flight = flight
        self.repository = repository
    }

    // MARK: - Initial load

    func loadBriefing() async {
        do {
            let pack = try await repository.latestPack(flightId: flight.id)
            self.pack = pack
            self.selectedPackTimestamp = pack.fetchTimestamp
            updateModels(from: pack)

            // Load pack history in parallel with data
            async let historyTask: () = loadPackHistory()
            async let dataTask: () = loadPackData(timestamp: pack.fetchTimestamp)
            _ = await (historyTask, dataTask)
            await checkCacheStatus()
        } catch {
            Self.logger.error("Failed to load pack meta: \(error)")
            advisoriesState = .error(error)
            digestState = .error(error)
            snapshotState = .error(error)
        }
    }

    // MARK: - Pack history

    private func loadPackHistory() async {
        do {
            packHistory = try await repository.packs(flightId: flight.id)
        } catch {
            Self.logger.error("Failed to load pack history: \(error)")
        }
    }

    /// Label for a pack in the history picker.
    func packLabel(for pack: PackMetaResponse) -> String {
        let daysOut = pack.daysOut
        let prefix = daysOut >= 0 ? "D-\(daysOut)" : "D+\(abs(daysOut))"
        // Extract date/time from fetchTimestamp
        if let date = ISO8601DateFormatter().date(from: pack.fetchTimestamp) {
            let fmt = DateFormatter()
            fmt.dateFormat = "MMM d HH:mm"
            fmt.timeZone = TimeZone(identifier: "UTC")
            return "\(prefix) · \(fmt.string(from: date)) UTC"
        }
        return prefix
    }

    // MARK: - Sounding profile

    /// Fetch raw sounding profile for a route point (for Skew-T rendering).
    func fetchSoundingProfile(pointIndex: Int) async throws -> SoundingProfileResponse {
        guard let pack else { throw APIError.notFound }
        return try await repository.soundingProfile(
            flightId: flight.id,
            timestamp: pack.fetchTimestamp,
            pointIndex: pointIndex,
            model: selectedModel
        )
    }

    // MARK: - Download / Cache

    /// Check which packs in history are cached.
    func checkCacheStatus() async {
        guard let caching = repository as? CachingBriefingRepository else { return }
        var status: [String: Bool] = [:]
        for pack in packHistory {
            status[pack.fetchTimestamp] = await caching.isPackCached(flightId: flight.id, timestamp: pack.fetchTimestamp)
        }
        packCacheStatus = status
        // Update download state for current pack
        if let ts = pack?.fetchTimestamp {
            downloadState = (packCacheStatus[ts] == true) ? .downloaded : .notDownloaded
        }
    }

    /// Download the current pack for offline access.
    func downloadCurrentPack() async {
        guard let pack, let caching = repository as? CachingBriefingRepository else { return }
        downloadState = .downloading(progress: 0)
        do {
            try await caching.downloadPack(
                flightId: flight.id,
                timestamp: pack.fetchTimestamp,
                flightTitle: flight.shortTitle,
                assessment: pack.assessment,
                packMeta: pack
            ) { [weak self] progress in
                Task { @MainActor in
                    self?.downloadState = .downloading(progress: progress)
                }
            }
            downloadState = .downloaded
            packCacheStatus[pack.fetchTimestamp] = true
        } catch {
            downloadState = .error(error.localizedDescription)
            Self.logger.error("Download failed: \(error)")
        }
    }

    /// Delete the current pack from the cache.
    func deleteCurrentPack() async {
        guard let pack, let caching = repository as? CachingBriefingRepository else { return }
        await caching.deletePack(flightId: flight.id, timestamp: pack.fetchTimestamp)
        downloadState = .notDownloaded
        packCacheStatus[pack.fetchTimestamp] = false
    }

    // MARK: - Refresh

    func refresh() async {
        guard !refreshState.isRefreshing else { return }
        refreshState = .refreshing(stage: "Starting", detail: nil, progress: 0)

        do {
            let stream = await repository.refreshStream(flightId: flight.id)
            for try await event in stream {
                switch event.type {
                case "progress":
                    refreshState = .refreshing(
                        stage: event.label ?? event.stage ?? "Working",
                        detail: event.detail,
                        progress: event.progress ?? 0
                    )
                case "complete":
                    refreshState = .completed(elapsedSeconds: event.elapsedSeconds ?? 0)
                    if let newPack = event.pack {
                        pack = newPack
                        selectedPackTimestamp = newPack.fetchTimestamp
                        updateModels(from: newPack)
                        await loadPackHistory()
                        await loadPackData(timestamp: newPack.fetchTimestamp)
                    }
                    // Clear completed state after a delay
                    try? await Task.sleep(for: .seconds(10))
                    if case .completed = refreshState {
                        refreshState = .idle
                    }
                case "error":
                    refreshState = .error(event.message ?? "Refresh failed")
                default:
                    break
                }
            }
        } catch {
            refreshState = .error(error.localizedDescription)
            Self.logger.error("Refresh stream error: \(error)")
        }
    }

    /// Check if a refresh is already running (started from web or another device).
    func checkActiveRefresh() async {
        do {
            let status = try await repository.refreshStatus(flightId: flight.id)
            if status.active {
                refreshState = .refreshing(
                    stage: status.label ?? status.stage ?? "In progress",
                    detail: status.detail,
                    progress: 0
                )
                // Poll until complete
                await pollRefreshStatus()
            }
        } catch {
            // Status check failed — not critical
            Self.logger.debug("Refresh status check failed: \(error)")
        }
    }

    private func pollRefreshStatus() async {
        for _ in 0..<100 {
            try? await Task.sleep(for: .seconds(3))
            do {
                let status = try await repository.refreshStatus(flightId: flight.id)
                if !status.active {
                    refreshState = .completed(elapsedSeconds: 0)
                    // Reload data
                    let pack = try await repository.latestPack(flightId: flight.id)
                    self.pack = pack
                    selectedPackTimestamp = pack.fetchTimestamp
                    updateModels(from: pack)
                    await loadPackHistory()
                    await loadPackData(timestamp: pack.fetchTimestamp)
                    try? await Task.sleep(for: .seconds(10))
                    if case .completed = refreshState {
                        refreshState = .idle
                    }
                    return
                }
                refreshState = .refreshing(
                    stage: status.label ?? status.stage ?? "In progress",
                    detail: status.detail,
                    progress: 0
                )
            } catch {
                break
            }
        }
    }

    // MARK: - Data loading

    private func loadPackData(timestamp: String) async {
        await withTaskGroup(of: Void.self) { group in
            group.addTask { await self.loadAdvisories(timestamp: timestamp) }
            group.addTask { await self.loadDigest(timestamp: timestamp) }
            group.addTask { await self.loadSnapshot(timestamp: timestamp) }
            group.addTask { await self.loadRouteAnalyses(timestamp: timestamp) }
            group.addTask { await self.loadElevation(timestamp: timestamp) }
        }
    }

    private func updateModels(from pack: PackMetaResponse) {
        let models = Array(pack.modelInitTimes.keys).sorted()
        if !models.isEmpty {
            availableModels = models
            if !models.contains(selectedModel) {
                selectedModel = models.first ?? "gfs"
            }
        }
    }

    // MARK: - Section loaders

    private func loadAdvisories(timestamp: String) async {
        advisoriesState = .loading
        do {
            advisoriesState = .loaded(try await repository.advisories(flightId: flight.id, timestamp: timestamp))
        } catch {
            advisoriesState = .error(error)
            Self.logger.error("Failed to load advisories: \(error)")
        }
    }

    private func loadDigest(timestamp: String) async {
        digestState = .loading
        do {
            digestState = .loaded(try await repository.digest(flightId: flight.id, timestamp: timestamp))
        } catch {
            digestState = .error(error)
            Self.logger.error("Failed to load digest: \(error)")
        }
    }

    private func loadSnapshot(timestamp: String) async {
        snapshotState = .loading
        do {
            snapshotState = .loaded(try await repository.snapshot(flightId: flight.id, timestamp: timestamp))
        } catch {
            snapshotState = .error(error)
            Self.logger.error("Failed to load snapshot: \(error)")
        }
    }

    private func loadRouteAnalyses(timestamp: String) async {
        routeAnalysesState = .loading
        do {
            let response = try await repository.routeAnalyses(flightId: flight.id, timestamp: timestamp)
            routeAnalysesState = .loaded(response)
            if !response.models.isEmpty {
                let raModels = response.models.sorted()
                availableModels = raModels
                if !raModels.contains(selectedModel) {
                    selectedModel = raModels.first ?? selectedModel
                    Self.logger.info("Switched model to \(self.selectedModel) (previous not in route analyses)")
                }
            }
        } catch {
            routeAnalysesState = .error(error)
            Self.logger.error("Failed to load route analyses: \(error)")
        }
    }

    private func loadElevation(timestamp: String) async {
        elevationState = .loading
        do {
            elevationState = .loaded(try await repository.elevation(flightId: flight.id, timestamp: timestamp))
        } catch {
            elevationState = .error(error)
            Self.logger.error("Failed to load elevation: \(error)")
        }
    }

    func loadPireps() async {
        pirepsState = .loading
        do {
            let response = try await repository.fetchPireps(flightId: flight.id)
            pirepsState = .loaded(response.items)
        } catch {
            pirepsState = .error(error)
            Self.logger.error("Failed to load PIREPs: \(error)")
        }
    }
}
