import Foundation
import OSLog

/// Briefing-internal tabs (#310). Four tabs: Advisory · Discussion ·
/// Cross-Section · Map, plus a gated PIREPs extra (kept reachable so offline
/// reporting doesn't regress). On iPad they render as a top pill band; on
/// iPhone they collapse to a native bottom tab bar. The old single "Brief"
/// mega-scroll is split into Advisory (hero + grid + conditions + alternates)
/// and Discussion (synopsis); the standalone Skew-T tab folds under
/// Cross-Section (scrolled to, not a separate tab).
enum BriefingTab: String, Hashable, CaseIterable {
    case advisory
    case discussion
    case crossSection
    case map
    case pireps

    /// Tabs shown in the main band (PIREPs is appended only when permitted).
    static let core: [BriefingTab] = [.advisory, .discussion, .crossSection, .map]

    var title: String {
        switch self {
        case .advisory: "Advisory"
        case .discussion: "Discussion"
        case .crossSection: "Cross-Section"
        case .map: "Map"
        case .pireps: "PIREPs"
        }
    }

    var systemImage: String {
        switch self {
        case .advisory: "exclamationmark.triangle"
        case .discussion: "text.alignleft"
        case .crossSection: "chart.xyaxis.line"
        case .map: "map"
        case .pireps: "cloud.sun"
        }
    }
}

/// Shared deep-link payload (§4.6/§4.7/§4.9): one `focusIntent` consumed by the
/// target tab to open already-scrubbed to the right model / layer / point.
struct FocusIntent: Equatable {
    /// Which instrument tab should consume this intent.
    enum Target: Equatable { case crossSection, skewT, map }
    var target: Target = .crossSection
    var model: String?
    /// Cross-section layer id to enable (e.g. a convective layer).
    var layerId: String?
    /// Map metric id to select.
    var mapMetricId: String?
    /// Advisory lens id to apply on the cross-section (e.g. "icing"); see
    /// `CrossSectionPresets`.
    var advisoryPresetId: String?
    var pointIndex: Int?
    var distanceNm: Double?
    var altitudeFt: Double?
    var validTime: String?
}

/// Refresh pipeline state.
enum RefreshState: Equatable {
    case idle
    case refreshing(stage: String, detail: String?, progress: Double)
    case completed(elapsedSeconds: Double)
    /// The server's refresh gate decided a full refresh wasn't warranted yet
    /// (e.g. not enough covering model runs updated for the lead time). Carries
    /// the human-readable reason to show the user — same idea as the web's
    /// "Up to date / waiting on <models>" freshness message.
    case noRefresh(message: String)
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
    var selectedTab: BriefingTab = .advisory
    var selectedModel: String = "gfs"
    var availableModels: [String] = []

    /// Shared active route point (§4.7) — the scrub/selected point on the
    /// cross-section, reflected by the Skew-T tab and (later) the map. nil = no
    /// point selected yet.
    var activePointIndex: Int?

    /// Pending deep-link payload; the target tab applies and clears it (§4.10).
    var focusIntent: FocusIntent?
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

    // MARK: - Deep-link focus (§4.6/§4.7/§4.9)

    /// Apply a deep-link intent: select the model, resolve and set the shared
    /// active point, and switch to the target instrument tab. The target tab's
    /// view consumes `focusIntent` (layer/metric to enable) and clears it.
    func setFocusIntent(_ intent: FocusIntent?) {
        focusIntent = intent
        guard let intent else { return }

        if let model = intent.model, availableModels.contains(model) {
            selectedModel = model
        }

        if case .loaded(let analyses) = routeAnalysesState {
            if let idx = intent.pointIndex,
               analyses.analyses.contains(where: { $0.pointIndex == idx }) {
                activePointIndex = idx
            } else if let dist = intent.distanceNm,
                      let nearest = analyses.analyses.min(by: {
                          abs($0.distanceFromOriginNm - dist) < abs($1.distanceFromOriginNm - dist)
                      }) {
                activePointIndex = nearest.pointIndex
            }
        }

        switch intent.target {
        // Skew-T is no longer a top-level tab (#310) — it scrolls within the
        // Cross-Section tab. The cross-section view reads `target == .skewT`
        // from the still-pending intent and scrolls to the sounding anchor.
        case .crossSection, .skewT: selectedTab = .crossSection
        case .map: selectedTab = .map
        }
    }

    /// The target tab calls this once it has applied the intent's layer/metric.
    func clearFocusIntent() { focusIntent = nil }

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
        } catch APIError.notFound {
            // No briefing pack exists yet — this is the normal state right after a
            // flight is created (POST /api/flights only saves the flight; it does
            // not generate a briefing). Rather than dead-ending on a "not found"
            // error, kick off the first briefing and stream its progress through
            // the existing refresh banner (stage/detail/percent).
            await generateFirstBriefing()
        } catch {
            Self.logger.error("Failed to load pack meta: \(error)")
            advisoriesState = .error(error)
            digestState = .error(error)
            snapshotState = .error(error)
        }
    }

    /// Generate the briefing for a flight that has no pack yet (e.g. just created).
    /// If the server already has a refresh in flight (started from the web or
    /// another device), adopt and poll it; otherwise start a fresh streamed run.
    /// Either way the refresh UI reports progress and the data loads on completion.
    private func generateFirstBriefing() async {
        let active = (try? await repository.refreshStatus(flightId: flight.id))?.active ?? false
        if active {
            await checkActiveRefresh()
        } else {
            await refresh()
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

    /// Compact day label for a pack ("D-1" / "D+2"). Shared so the toolbar chip
    /// and the history-menu rows can't drift.
    func packDayLabel(for pack: PackMetaResponse) -> String {
        let daysOut = pack.daysOut
        return daysOut >= 0 ? "D-\(daysOut)" : "D+\(abs(daysOut))"
    }

    /// Label for a pack in the history picker.
    func packLabel(for pack: PackMetaResponse) -> String {
        let prefix = packDayLabel(for: pack)
        // Extract date/time from fetchTimestamp
        if let date = Self.parseISO(pack.fetchTimestamp) {
            return "\(prefix) · \(Self.dayTimeUTC.string(from: date)) UTC"
        }
        return prefix
    }

    /// Compact toolbar-chip label. Normally just the day code ("D-0"), but when
    /// another pack in history shares the same UTC day it appends the time
    /// ("D-0 · 14:05") so same-day runs are distinguishable at a glance — like
    /// the web history dropdown.
    func packChipLabel(for pack: PackMetaResponse) -> String {
        let day = packDayLabel(for: pack)
        guard hasSameDaySibling(pack),
              let date = Self.parseISO(pack.fetchTimestamp) else { return day }
        return "\(day) · \(Self.timeUTC.string(from: date))"
    }

    /// Whether another pack in history was fetched on the same UTC calendar day.
    private func hasSameDaySibling(_ pack: PackMetaResponse) -> Bool {
        guard let day = Self.utcDayKey(pack.fetchTimestamp) else { return false }
        return packHistory.filter { Self.utcDayKey($0.fetchTimestamp) == day }.count > 1
    }

    private static func utcDayKey(_ timestamp: String) -> String? {
        guard let date = parseISO(timestamp) else { return nil }
        return dayKeyUTC.string(from: date)
    }

    /// Parse an ISO-8601 timestamp. The server sends `datetime.isoformat()`,
    /// which includes microseconds (e.g. `2026-06-28T08:46:00.123456+00:00`),
    /// so try the fractional-seconds parser first, then plain. (Default
    /// `ISO8601DateFormatter` rejects fractional seconds — that was why the time
    /// never appeared.)
    private static func parseISO(_ s: String) -> Date? {
        isoParserFrac.date(from: s) ?? isoParserPlain.date(from: s)
    }

    private static let isoParserFrac: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let isoParserPlain = ISO8601DateFormatter()
    private static let dayTimeUTC: DateFormatter = utcFormatter("MMM d HH:mm")
    private static let timeUTC: DateFormatter = utcFormatter("HH:mm")
    private static let dayKeyUTC: DateFormatter = utcFormatter("yyyy-MM-dd")
    private static func utcFormatter(_ format: String) -> DateFormatter {
        let fmt = DateFormatter()
        fmt.dateFormat = format
        fmt.timeZone = TimeZone(identifier: "UTC")
        return fmt
    }

    // MARK: - Advisory detail

    /// Fetch the "why it's RED" drill-down for one advisory (§4.6) — per-model
    /// detail, fired parameters, and (for convective) the CAPE-vs-cover
    /// reconciliation. Backed by the Phase 5 REST endpoint.
    func fetchAdvisoryDetail(advisoryId: String) async throws -> AdvisoryDetailResponse {
        guard let pack else { throw APIError.notFound }
        return try await repository.advisoryDetail(
            flightId: flight.id,
            timestamp: pack.fetchTimestamp,
            advisoryId: advisoryId
        )
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
        downloadState = .downloading(progress: 0, receivedBytes: 0, totalBytes: -1)
        do {
            try await caching.downloadPack(
                flightId: flight.id,
                timestamp: pack.fetchTimestamp,
                flightTitle: flight.shortTitle,
                assessment: pack.assessment,
                packMeta: pack
            ) { [weak self] fraction, received, total in
                Task { @MainActor in
                    self?.downloadState = .downloading(progress: fraction, receivedBytes: received, totalBytes: total)
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
                    // The tiered gate may decide no full refresh is warranted yet
                    // (`mode == "none"`): the stream sends only this single event,
                    // carrying the reason. Surface it instead of a misleading
                    // "Refreshed in Xs", but still adopt the (unchanged) pack.
                    if let decision = event.refreshDecision, decision.mode == "none" {
                        refreshState = .noRefresh(message: Self.noRefreshMessage(decision))
                    } else {
                        refreshState = .completed(elapsedSeconds: event.elapsedSeconds ?? 0)
                    }
                    if let newPack = event.pack {
                        pack = newPack
                        selectedPackTimestamp = newPack.fetchTimestamp
                        updateModels(from: newPack)
                        await loadPackHistory()
                        await loadPackData(timestamp: newPack.fetchTimestamp)
                    }
                    // Clear the transient banner after a delay.
                    try? await Task.sleep(for: .seconds(10))
                    switch refreshState {
                    case .completed, .noRefresh: refreshState = .idle
                    default: break
                    }
                case "error":
                    refreshState = .error(event.message ?? "Refresh failed")
                default:
                    // e.g. "briefing_ready" — provisional pack; no UI change needed.
                    break
                }
            }
            // The stream ended without a terminal `complete`/`error` event (the
            // server closed it, or a frame was dropped). Never leave the spinner
            // running forever — fall back to idle and let the user retry.
            if case .refreshing = refreshState {
                Self.logger.warning("Refresh stream ended without a terminal event — resetting to idle")
                refreshState = .idle
            }
        } catch {
            refreshState = .error(error.localizedDescription)
            Self.logger.error("Refresh stream error: \(error)")
        }
    }

    /// Build the user-facing message for a gated no-op refresh. Prefer the
    /// server's reason (a complete sentence like "Only 1 of 3 covering model(s)
    /// updated for a D-3 flight — no refresh needed"); fall back to a generic
    /// line if it's ever absent.
    private static func noRefreshMessage(_ decision: RefreshDecision) -> String {
        if let reason = decision.reason, !reason.isEmpty {
            return reason
        }
        return "Already up to date — no refresh needed."
    }

    /// Check if a refresh is already running (started from web or another device).
    func checkActiveRefresh() async {
        do {
            let status = try await repository.refreshStatus(flightId: flight.id)
            if status.active {
                refreshState = .refreshing(
                    stage: status.label ?? status.stage ?? "In progress",
                    detail: status.detail,
                    progress: status.progress ?? 0
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
                    progress: status.progress ?? 0
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
