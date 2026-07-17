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
    /// Concrete advisory instance behind the lens (e.g. "vmc_cruise"), set only
    /// by advisory-card actions. Activates that advisory's cross-section
    /// highlight (scrim + verdict ribbon, #374) when the pack carries highlight
    /// geometry; a re-tap of the same advisory toggles the highlight off.
    var advisoryId: String?
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
    /// Local settings (offline auto-download mode) + connectivity. Optional so
    /// fixtures/tests can construct a view model without the full app graph;
    /// auto-download is simply skipped when either is absent.
    private let settings: AppSettingsStore?
    private let networkMonitor: NetworkMonitor?

    // Pack metadata
    private(set) var pack: PackMetaResponse?
    private(set) var packHistory: [PackMetaResponse] = []

    /// The pilot's post-flight debrief (past owned flights). Seeded from the
    /// inlined `flight.debrief` and updated in place when the debrief sheet saves
    /// or deletes, so the Advisory-tab card reflects the change without a list
    /// reload and survives tab switches (this view model outlives the tab views).
    private(set) var debrief: DebriefResponse?

    // Section states
    private(set) var advisoriesState: LoadingState<AdvisoriesResponse> = .idle
    private(set) var digestState: LoadingState<DigestResponse> = .idle
    private(set) var snapshotState: LoadingState<SnapshotResponse> = .idle
    private(set) var routeAnalysesState: LoadingState<RouteAnalysesResponse> = .idle
    private(set) var elevationState: LoadingState<ElevationResponse> = .idle
    private(set) var pirepsState: LoadingState<[PirepResponse]> = .idle

    // Timing scenarios (#357) — the latest poll result, or nil when the flight
    // has Flexibility `none` / the pack predates the feature (404). Online-only:
    // `timeOptionsOffline` is set instead when there's no connectivity.
    private(set) var timeOptions: TimeOptionsResponse?
    private(set) var timeOptionsOffline = false
    @ObservationIgnored private var timeOptionsPollTask: Task<Void, Never>?

    // Refresh state
    private(set) var refreshState: RefreshState = .idle
    /// Guards against overlapping `syncLatestPack()` runs when several triggers
    /// fire close together (e.g. foreground + push). Non-observed — internal only.
    @ObservationIgnored private var isSyncing = false

    // Download/cache state
    private(set) var downloadState: DownloadState = .notDownloaded
    private(set) var packCacheStatus: [String: Bool] = [:] // timestamp -> isCached

    // UI state
    var selectedTab: BriefingTab = .advisory
    /// Default to ECMWF (#8, iOS feedback). The effective choice is reconciled
    /// against the models a given flight actually carries via `preferredModel`;
    /// a user's explicit pick is remembered across flights/launches (#9).
    var selectedModel: String = BriefingViewModel.storedPreferredModel ?? "ecmwf"
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

    init(
        flight: FlightResponse,
        repository: any BriefingRepository,
        settings: AppSettingsStore? = nil,
        networkMonitor: NetworkMonitor? = nil
    ) {
        self.flight = flight
        self.repository = repository
        self.settings = settings
        self.networkMonitor = networkMonitor
        self.debrief = flight.debrief
    }

    /// Adopt the saved (or deleted → nil) debrief so the Advisory card updates.
    func setDebrief(_ debrief: DebriefResponse?) {
        self.debrief = debrief
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
        // Saved beyond the forecast horizon — no model reaches the date yet.
        // Don't fetch or auto-generate a briefing; the container shows the
        // pending-coverage card. It briefs automatically once it crosses into
        // range (a fresh flight fetch then carries coverage == nil).
        if flight.coverage != nil {
            return
        }
        do {
            let latest = try await repository.latestPack(flightId: flight.id)
            await applyPack(latest)
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

    /// Seamlessly adopt the newest *already-generated* pack from the server
    /// **without** starting a new briefing generation — the cheap "sync" that
    /// mirrors what's live on the website. This is distinct from `refresh()`,
    /// which runs the full pipeline to produce a *new* pack.
    ///
    /// A single `latestPack()` request that **no-ops when the timestamp is
    /// unchanged** (the common case), so it's safe — and intended — to call from
    /// many triggers: opening the briefing, pull-to-refresh, returning to the
    /// foreground, and a refresh push. Best-effort: on any failure the current
    /// pack stays on screen. The reload runs `quiet` so an update swaps in place
    /// instead of flashing the sections back to spinners.
    func syncLatestPack() async {
        // Nothing to sync for a pending-coverage flight (no pack yet). While a
        // generation is in flight, its completion installs the newer pack itself.
        // `isSyncing` collapses overlapping triggers (e.g. foreground + push).
        guard flight.coverage == nil, !refreshState.isRefreshing, !isSyncing else { return }
        // Don't yank the pilot off a pack they deliberately picked from the history
        // menu: only auto-sync when the pack on screen is the latest one we know
        // about. A brand-new server pack isn't in `packHistory` yet, so a viewer on
        // the current latest still passes and the newer pack is allowed to adopt.
        guard Self.isViewingLatestPack(current: pack?.fetchTimestamp, history: packHistory) else {
            Self.logger.debug("syncLatestPack: viewing a historical pack — skipping auto-sync")
            return
        }
        isSyncing = true
        defer { isSyncing = false }
        let latest: PackMetaResponse
        do {
            latest = try await repository.latestPack(flightId: flight.id)
        } catch {
            Self.logger.debug("syncLatestPack: latestPack fetch failed, keeping current pack: \(error)")
            return
        }
        // Already showing the newest pack — seamless no-op (the frequent case).
        guard latest.fetchTimestamp != pack?.fetchTimestamp else { return }
        Self.logger.info("syncLatestPack: newer pack \(latest.fetchTimestamp) available — adopting")
        await applyPack(latest, quiet: true)
    }

    /// Whether the pack on screen is the newest one the app knows about — the
    /// gate for an automatic sync. False when the pilot has deliberately selected
    /// an older pack from history (auto-sync must not replace it). Pure + static
    /// so the timestamp comparison is unit-testable. A brand-new server pack isn't
    /// in `history` yet, so a viewer on the current latest still counts as
    /// "latest" and the newer pack is allowed to adopt. Parses to `Date` rather
    /// than comparing ISO strings so fractional seconds can't misorder.
    static func isViewingLatestPack(current: String?, history: [PackMetaResponse]) -> Bool {
        guard let current, let currentDate = Date.parseISO8601(current) else { return true }
        guard let newest = history.compactMap({ Date.parseISO8601($0.fetchTimestamp) }).max() else {
            return true   // history not loaded yet — don't block the first sync
        }
        return currentDate >= newest
    }

    /// Adopt `newPack` as the active pack and (re)load its data + history. The
    /// single swap path shared by the initial load, refresh/poll completion, and
    /// the seamless `syncLatestPack()`, so the logic can't drift across them.
    ///
    /// `quiet` keeps already-loaded sections on screen while the replacement
    /// streams in (sync uses it, so picking up a newer online pack never flashes
    /// the briefing back to spinners). A first load — with no data yet — still
    /// shows spinners even when `quiet`, because the section loaders only suppress
    /// the spinner when they already hold data.
    private func applyPack(_ newPack: PackMetaResponse, quiet: Bool = false) async {
        pack = newPack
        // `packHistory` is still the previous pack's list here, so the
        // `selectedPackTimestamp` didSet can't find `newPack` and won't kick a
        // duplicate load — we load explicitly below, and refresh history alongside.
        selectedPackTimestamp = newPack.fetchTimestamp
        updateModels(from: newPack)
        async let historyTask: () = loadPackHistory()
        async let dataTask: () = loadPackData(timestamp: newPack.fetchTimestamp, quiet: quiet)
        _ = await (historyTask, dataTask)
        await checkCacheStatus()
        await maybeAutoDownloadLatest()
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

    /// Parse an ISO-8601 timestamp, tolerating the server's fractional seconds.
    /// Delegates to the shared `Date.parseISO8601` helper (was a bespoke
    /// fractional-then-plain parser duplicated here and in the cache layer).
    private static func parseISO(_ s: String) -> Date? {
        Date.parseISO8601(s)
    }

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

    /// Auto-download the latest pack for offline use when the pilot has opted in
    /// (Settings → Offline Auto-Download, default Wi-Fi only) and the flight is
    /// today or in the future. No-op if the pack is already cached, a download is
    /// already running, or the current connectivity doesn't match the chosen
    /// mode. Past flights are intentionally skipped — they age out via
    /// `pruneStalePacks`. Reuses `downloadCurrentPack`, so the download banner
    /// gives the same visible progress as a manual download.
    private func maybeAutoDownloadLatest() async {
        guard let settings, let networkMonitor,
              repository is CachingBriefingRepository,
              let pack else { return }

        guard Self.isTodayOrFuture(flight.departureDate) else { return }
        if packCacheStatus[pack.fetchTimestamp] == true { return }
        if case .downloading = downloadState { return }

        guard settings.autoDownloadMode.allows(
            isOnWiFi: networkMonitor.isOnWiFi,
            isConnected: networkMonitor.isConnected
        ) else { return }

        Self.logger.info("Auto-downloading pack \(pack.fetchTimestamp) for offline use")
        await downloadCurrentPack()
    }

    /// Whether a departure is today or later (local calendar). A nil departure is
    /// treated as eligible — better to cache than to silently skip.
    private static func isTodayOrFuture(_ date: Date?) -> Bool {
        guard let date else { return true }
        return date >= Calendar.current.startOfDay(for: Date())
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
                packMeta: pack,
                departureTime: flight.departureTime
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
                        await applyPack(newPack)
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
                    // Reload data. A refresh started elsewhere (web/another device)
                    // still produces a new pack here — `applyPack` mirrors
                    // refresh()'s completion path, so an opted-in pilot gets it
                    // auto-downloaded for offline use, not only on the next entry.
                    let latest = try await repository.latestPack(flightId: flight.id)
                    await applyPack(latest)
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

    private func loadPackData(timestamp: String, quiet: Bool = false) async {
        await withTaskGroup(of: Void.self) { group in
            group.addTask { await self.loadAdvisories(timestamp: timestamp, quiet: quiet) }
            group.addTask { await self.loadDigest(timestamp: timestamp, quiet: quiet) }
            group.addTask { await self.loadSnapshot(timestamp: timestamp, quiet: quiet) }
            group.addTask { await self.loadRouteAnalyses(timestamp: timestamp, quiet: quiet) }
            group.addTask { await self.loadElevation(timestamp: timestamp, quiet: quiet) }
        }
        // Kick the timing-scenario poll for this pack (no-op when Flexibility is
        // `none`). Runs after the briefing loads — the scan is a background job
        // reached only by polling, not the refresh SSE stream. `resetExisting`
        // drops any prior pack's scan so a pack switch can't briefly render
        // another run's candidates until the new poll lands.
        startTimeOptionsPolling(timestamp: timestamp, resetExisting: true)
    }

    // MARK: - Timing scenarios (#357)

    /// Flexibility mode that gates timing scenarios. Prefers the pack's value —
    /// injected fresh from the flight row at serve time — over the `flight`
    /// object, which is seeded once at open and goes stale if flexibility was
    /// changed on another device after this flights list was last fetched.
    /// Falls back to the flight object for older packs missing the field.
    var effectiveFlexibility: FlexibilityMode {
        pack?.flexibility ?? flight.effectiveFlexibility
    }

    /// Whether the Timing Scenarios panel should be shown at all — the flight has
    /// a Flexibility mode set (the panel then renders its own state ladder).
    var showsTimingScenarios: Bool {
        effectiveFlexibility != .none
    }

    /// (Re)start the poll loop for a pack. Cancels any in-flight poll first, so a
    /// pack switch or a post-confirm re-poll never runs two loops at once.
    ///
    /// Pass `resetExisting: true` when the pack changed (a fresh load / switch /
    /// refresh) so the previous pack's scan is dropped up front — mirroring the
    /// web, which nulls `timeOptions` as it installs a new pack. Same-pack
    /// re-polls (confirm / set-as-alternate) keep the current panel so its
    /// pending state doesn't flash away.
    func startTimeOptionsPolling(timestamp: String, resetExisting: Bool = false) {
        timeOptionsPollTask?.cancel()
        if resetExisting {
            timeOptions = nil
            timeOptionsOffline = false
        }
        guard effectiveFlexibility != .none else {
            timeOptions = nil
            timeOptionsOffline = false
            return
        }
        timeOptionsPollTask = Task { [weak self] in
            await self?.pollTimeOptions(timestamp: timestamp)
        }
    }

    /// Cancel the timing-scenario poll loop. Called from the briefing view's
    /// `.onDisappear` so a still-`running` scan can't keep the view model alive
    /// and hitting the network after the pilot has left the briefing. (The loop
    /// also has its own iteration cap as a backstop.)
    func stopTimeOptionsPolling() {
        timeOptionsPollTask?.cancel()
        timeOptionsPollTask = nil
    }

    /// Hard cap on `pollTimeOptions` iterations — a safety backstop so the loop
    /// is bounded even if the server never reports a terminal status. With the
    /// 3s→×1.5→15s backoff this is ~18 min worst case; a real scan reaches a
    /// terminal state well before then. Mirrors `pollRefreshStatus`'s 100-iter
    /// bound. Terminal status remains the primary exit.
    private static let maxTimeOptionsPollIterations = 80

    /// Poll `GET …/time-options` until a terminal status, mirroring the web
    /// backoff (3s → ×1.5 → cap 15s): keep polling while `pending`/`running` or
    /// any candidate is `confirm_pending`; stop on `done`/`failed`/`skipped`. A
    /// 404 means "no data" (Flexibility none / legacy pack) — hide the panel. Up
    /// to 3 transient errors are tolerated so a single blip can't blank a live
    /// "Scenarios running…". Online-only: bail to a placeholder when offline.
    private func pollTimeOptions(timestamp: String) async {
        if let networkMonitor, !networkMonitor.isConnected {
            timeOptionsOffline = true
            return
        }
        timeOptionsOffline = false
        var delay: Double = 3
        var errorStreak = 0
        var iterations = 0
        while !Task.isCancelled {
            iterations += 1
            if iterations > Self.maxTimeOptionsPollIterations { return }
            guard pack?.fetchTimestamp == timestamp else { return }
            do {
                let resp = try await repository.timeOptions(flightId: flight.id, timestamp: timestamp)
                guard !Task.isCancelled, pack?.fetchTimestamp == timestamp else { return }
                timeOptions = resp
                timeOptionsOffline = false
                errorStreak = 0
                let status = resp.status?.status
                let confirmPending = resp.scan?.candidates.contains { $0.confirmPending } ?? false
                let nonTerminal = status == .pending || status == .running || confirmPending
                    || (status == nil && resp.scan == nil)
                if !nonTerminal { return }
                delay = min(delay * 1.5, 15)
            } catch let error as APIError {
                guard !Task.isCancelled, pack?.fetchTimestamp == timestamp else { return }
                if error.isCancellation { return }
                if case .notFound = error {
                    timeOptions = nil   // Flexibility none / legacy pack — hide it.
                    return
                }
                errorStreak += 1
                if errorStreak > 3 { abandonTimeOptionsPolling(); return }
                delay = min(delay * 1.5, 15)
            } catch {
                errorStreak += 1
                if errorStreak > 3 { abandonTimeOptionsPolling(); return }
                delay = min(delay * 1.5, 15)
            }
            try? await Task.sleep(for: .seconds(delay))
        }
    }

    /// Give up polling after the transient-error budget is exhausted. Mirror the
    /// web (`briefing-store.ts`): clear the scan so a stale "this window looks
    /// smoother" panel can't linger with no signal that it stopped refreshing —
    /// a real hazard for an attention-director feature. If the failures are a
    /// mid-poll connectivity drop (the initial online check only runs once,
    /// before the loop), show the offline placeholder instead of hiding outright.
    private func abandonTimeOptionsPolling() {
        if let networkMonitor, !networkMonitor.isConnected {
            timeOptionsOffline = true
        } else {
            timeOptions = nil
        }
    }

    /// Whether any candidate currently has a multi-model confirm in flight. The
    /// server allows only one confirm at a time per pack (429s the rest), so the
    /// panel disables every "Check all models" button while this is true.
    var anyConfirmPending: Bool {
        timeOptions?.scan?.candidates.contains { $0.confirmPending } ?? false
    }

    /// Queue the on-tap multi-model check of one provisional candidate. Mirrors
    /// the server's one-at-a-time rule client-side (skip if another confirm is
    /// already running), then restarts the poll at a fresh cadence so the
    /// "checking all models…" state appears promptly. A `429` (a confirm slipped
    /// in) is swallowed — the poll surfaces the real state.
    func confirmTimeOption(departureTime: String) async {
        guard let pack, !anyConfirmPending else { return }
        do {
            try await repository.confirmTimeOption(
                flightId: flight.id,
                timestamp: pack.fetchTimestamp,
                departureTime: departureTime
            )
        } catch let APIError.serverError(code, _) where code == 429 {
            // Another confirm is already running — the poll will show it.
        } catch {
            Self.logger.error("Confirm time option failed: \(error)")
            return
        }
        startTimeOptionsPolling(timestamp: pack.fetchTimestamp)
    }

    /// Pin a scenario as the flight's alternate departure: PATCH the alt time
    /// (keeping the current day-scan Flexibility), re-queue the scan so the
    /// pinned row re-grades and the alt artifacts persist, then re-poll. Only the
    /// `alt_departure_time` is sent — the day mode is preserved (a day scan can
    /// carry a pinned alternate).
    func setScenarioAsAlternate(departureTime: String) async {
        guard let pack else { return }
        do {
            _ = try await repository.updateFlight(
                flightId: flight.id,
                request: UpdateFlightRequest(altDepartureTime: departureTime)
            )
            try await repository.rescanTimeOptions(flightId: flight.id, timestamp: pack.fetchTimestamp)
        } catch {
            Self.logger.error("Set-as-alternate failed: \(error)")
            return
        }
        startTimeOptionsPolling(timestamp: pack.fetchTimestamp)
    }

    private func updateModels(from pack: PackMetaResponse) {
        let models = Array(pack.modelInitTimes.keys).sorted()
        if !models.isEmpty {
            availableModels = models
            if !models.contains(selectedModel) {
                selectedModel = preferredModel(from: models)
            }
        }
    }

    /// `UserDefaults` key for the sticky model preference.
    private static let preferredModelKey = "preferredCrossSectionModel"

    /// The user's last explicit model pick, if any.
    static var storedPreferredModel: String? {
        UserDefaults.standard.string(forKey: preferredModelKey)
    }

    /// Choose which model to show for a flight whose current selection isn't
    /// available: the user's sticky preference when this flight carries it, else
    /// ECMWF (the #8 default), else GFS, else whatever's first. Never persists —
    /// a fallback for an ECMWF-less flight must not clobber the saved preference.
    private func preferredModel(from available: [String]) -> String {
        if let pref = Self.storedPreferredModel, available.contains(pref) { return pref }
        if available.contains("ecmwf") { return "ecmwf" }
        if available.contains("gfs") { return "gfs" }
        return available.first ?? selectedModel
    }

    /// Record an explicit user model choice so it sticks across flights and
    /// launches (#8/#9, iOS feedback). Programmatic fallbacks must NOT call this.
    func selectModel(_ model: String) {
        selectedModel = model
        UserDefaults.standard.set(model, forKey: Self.preferredModelKey)
    }

    // MARK: - Section loaders

    // Section loaders share two conventions:
    //  - `quiet` (seamless sync): keep current data on screen — only show the
    //    spinner when nothing is loaded yet, and keep old data (not an error wall)
    //    if a quiet fetch fails. A normal load shows spinner/error as before.
    //  - stale-guard: after the await, only apply the result if `pack` still points
    //    at `timestamp`. A concurrent sync / refresh / history-pick may have swapped
    //    the active pack while this load was in flight; without the guard a late
    //    result would paint one pack's data under another pack's hero/toolbar.
    //    Mirrors `pollTimeOptions`' `pack?.fetchTimestamp == timestamp` check.

    private func loadAdvisories(timestamp: String, quiet: Bool = false) async {
        if !quiet || !advisoriesState.hasData { advisoriesState = .loading }
        do {
            let result = try await repository.advisories(flightId: flight.id, timestamp: timestamp)
            guard pack?.fetchTimestamp == timestamp else { return }
            advisoriesState = .loaded(result)
        } catch {
            Self.logger.error("Failed to load advisories: \(error)")
            guard pack?.fetchTimestamp == timestamp else { return }
            if !quiet || !advisoriesState.hasData { advisoriesState = .error(error) }
        }
    }

    private func loadDigest(timestamp: String, quiet: Bool = false) async {
        if !quiet || !digestState.hasData { digestState = .loading }
        do {
            let result = try await repository.digest(flightId: flight.id, timestamp: timestamp)
            guard pack?.fetchTimestamp == timestamp else { return }
            digestState = .loaded(result)
        } catch {
            Self.logger.error("Failed to load digest: \(error)")
            guard pack?.fetchTimestamp == timestamp else { return }
            if !quiet || !digestState.hasData { digestState = .error(error) }
        }
    }

    private func loadSnapshot(timestamp: String, quiet: Bool = false) async {
        if !quiet || !snapshotState.hasData { snapshotState = .loading }
        do {
            let result = try await repository.snapshot(flightId: flight.id, timestamp: timestamp)
            guard pack?.fetchTimestamp == timestamp else { return }
            snapshotState = .loaded(result)
        } catch {
            Self.logger.error("Failed to load snapshot: \(error)")
            guard pack?.fetchTimestamp == timestamp else { return }
            if !quiet || !snapshotState.hasData { snapshotState = .error(error) }
        }
    }

    private func loadRouteAnalyses(timestamp: String, quiet: Bool = false) async {
        if !quiet || !routeAnalysesState.hasData { routeAnalysesState = .loading }
        do {
            let response = try await repository.routeAnalyses(flightId: flight.id, timestamp: timestamp)
            guard pack?.fetchTimestamp == timestamp else { return }
            routeAnalysesState = .loaded(response)
            if !response.models.isEmpty {
                let raModels = response.models.sorted()
                availableModels = raModels
                if !raModels.contains(selectedModel) {
                    selectedModel = preferredModel(from: raModels)
                    Self.logger.info("Switched model to \(self.selectedModel) (previous not in route analyses)")
                }
            }
        } catch {
            Self.logger.error("Failed to load route analyses: \(error)")
            guard pack?.fetchTimestamp == timestamp else { return }
            if !quiet || !routeAnalysesState.hasData { routeAnalysesState = .error(error) }
        }
    }

    private func loadElevation(timestamp: String, quiet: Bool = false) async {
        if !quiet || !elevationState.hasData { elevationState = .loading }
        do {
            let result = try await repository.elevation(flightId: flight.id, timestamp: timestamp)
            guard pack?.fetchTimestamp == timestamp else { return }
            elevationState = .loaded(result)
        } catch {
            Self.logger.error("Failed to load elevation: \(error)")
            guard pack?.fetchTimestamp == timestamp else { return }
            if !quiet || !elevationState.hasData { elevationState = .error(error) }
        }
    }

    func loadPireps() async {
        pirepsState = .loading
        do {
            let response = try await repository.fetchPireps(flightId: flight.id)
            pirepsState = .loaded(response.items)
        } catch let apiError as APIError where apiError.isCancellation {
            // Task was cancelled (view disappeared / superseded) — benign, not a
            // failure. Leave state as .loading so the next .task run reloads.
            Self.logger.debug("PIREP load cancelled — ignoring")
        } catch is CancellationError {
            Self.logger.debug("PIREP load cancelled — ignoring")
        } catch {
            pirepsState = .error(error)
            Self.logger.error("Failed to load PIREPs: \(error)")
        }
    }
}
