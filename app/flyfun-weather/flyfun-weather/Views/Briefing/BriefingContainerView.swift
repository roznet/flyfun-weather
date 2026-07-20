import CoreLocation
import SwiftUI
import TipKit

/// Tab-based briefing viewer for a single flight.
struct BriefingContainerView: View {
    let flight: FlightResponse
    @Environment(AppState.self) private var appState
    @Environment(\.scenePhase) private var scenePhase
    @State private var viewModel: BriefingViewModel?
    @State private var trackingService = FlightTrackingService()
    @State private var showingPirepSheet = false
    /// Per-flight notification override (bell), tracked locally since `flight`
    /// is immutable. Seeded from the flight in `.task`; updated optimistically.
    @State private var notifyOverride: FlightNotifyOverride = .default
    @State private var notifyOverrideBusy = false

    private static let dayTimeUTC: DateFormatter = {
        let fmt = DateFormatter()
        fmt.dateFormat = "d MMM HH:mm"
        fmt.timeZone = TimeZone(identifier: "UTC")
        return fmt
    }()

    /// Nav-bar subtitle: "15 Mar 06:00 UTC · FL090" (#310 — was the header band's
    /// identity line).
    private var identitySubtitle: String {
        var parts: [String] = []
        if let date = flight.departureDate {
            parts.append("\(Self.dayTimeUTC.string(from: date)) UTC")
        }
        parts.append("FL\(flight.cruiseAltitudeFt / 100)")
        return parts.joined(separator: " · ")
    }

    /// Whether the current time is within the flight tracking window (departure - 2h to departure + duration + 2h).
    private var isInFlightWindow: Bool {
        guard let departure = flight.departureDate else { return false }
        let windowStart = departure.addingTimeInterval(-2 * 3600)
        let windowEnd = departure.addingTimeInterval((flight.flightDurationHours + 2) * 3600)
        let now = Date()
        return now >= windowStart && now <= windowEnd
    }

    var body: some View {
        Group {
            // Saved beyond the forecast horizon → no pack to load. The pending
            // card is a pure function of `flight.coverage`, so render it up front
            // without waiting on the briefing view model. Otherwise a freshly
            // created future flight sticks on the "Loading briefing…" spinner
            // while `.task` spins up a view model it never needs (the create→open
            // transition can leave that task uncompleted). Mirrors the web, which
            // shows the pending banner straight from the flight, no briefing load.
            if let coverage = flight.coverage {
                PendingCoverageView(flight: flight, coverage: coverage)
            } else if let viewModel {
                // #310: the standalone header band is gone — identity moved into
                // the nav bar (title + subtitle), freshness + pack picker into
                // the toolbar. Tabs render at the top (iPad) / bottom (iPhone).
                VStack(spacing: 0) {
                    RefreshBannerView(state: viewModel.refreshState)
                    DownloadBannerView(state: viewModel.downloadState)
                    BriefingContentView(viewModel: viewModel, trackingService: trackingService,
                                        onAddPirep: { showingPirepSheet = true })
                }
            } else {
                ProgressView("Loading briefing...")
            }
        }
        .navigationTitle(flight.shortTitle)
        .navigationSubtitle(identitySubtitle)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            // A pending-coverage flight has no pack: hide the pack picker and the
            // refresh/download/track actions — none apply until it's in range.
            if let viewModel, flight.coverage == nil {
                ToolbarItem(placement: .topBarLeading) {
                    BriefingPackToolbar(viewModel: viewModel)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    HStack(spacing: 12) {
                        // Owner sharing (#446): a ShareLink to the short /s/{code}
                        // URL. Only the owner sees it, and only for a non-private
                        // flight with a minted code (isShareable) — a private link
                        // would 404 for the recipient, matching the web gate.
                        if flight.isShareable, let code = flight.shareCode {
                            ShareLink(item: AppState.shareURL(forShareCode: code)) {
                                Label("Share", systemImage: "square.and.arrow.up")
                                    .font(.caption)
                            }
                        }
                        // No in-flight-window gate: a PIREP is a position report,
                        // and the reporting form grabs its own GPS fix. Show it
                        // whenever publishing is enabled; being in the window just
                        // means the fix pre-fills a cruise altitude rather than a
                        // ground one.
                        if appState.userPreferences.preferences.pirepCanPublish {
                            Button {
                                showingPirepSheet = true
                            } label: {
                                Label("Report PIREP", systemImage: "square.and.pencil")
                                    .font(.caption)
                            }
                        }
                        // Hide the notify bell for flown flights (matches web's
                        // renderNotifyOverrideBar isPast guard) — an override on a
                        // past flight is a no-op.
                        if flight.isEditable && !flight.isPast {
                            notifyBellMenu
                        }
                        BriefingToolbarView(viewModel: viewModel, trackingService: trackingService,
                                            isInFlightWindow: isInFlightWindow, startTracking: startTracking)
                    }
                }
            }
        }
        .sheet(isPresented: $showingPirepSheet) {
            if let viewModel, let repo = appState.repository {
                PirepReportingView(viewModel: PirepViewModel(flight: flight, repository: repo,
                                                                      offlineStore: appState.pirepOfflineStore),
                                   trackingService: trackingService)
            }
        }
        .task {
            guard let repo = appState.repository else { return }
            notifyOverride = flight.notifyOverrideMode
            // Pick up server-side flag changes (e.g. pirep_can_publish, and the
            // account "Briefing updates" setting the bell's Default hint reflects)
            // before the toolbar decides whether to show the PIREP button.
            await appState.refreshUserPreferences()
            // Cache flight data for offline recovery
            if let caching = repo as? CachingBriefingRepository {
                await caching.cacheFlightData(flight)
            }
            let vm = BriefingViewModel(
                flight: flight,
                repository: repo,
                settings: appState.settings,
                networkMonitor: appState.networkMonitor
            )
            viewModel = vm
            await vm.loadBriefing()
            await vm.checkActiveRefresh()
            // Only load PIREPs when the user may view them — the query 403s for a
            // publish-only account, which would strand the PIREPs tab on an error
            // screen and hide its "Report a PIREP" action.
            if appState.userPreferences.preferences.pirepCanView {
                await vm.loadPireps()
            }
            // Opening the briefing clears this flight from the badge (web + app),
            // and fires a silent badge-sync push to the user's other devices.
            await appState.markBriefingSeen(flightId: flight.id)
        }
        .onChange(of: scenePhase) {
            // Returning to the foreground: seamlessly sync to the newest online
            // pack (no-op if unchanged). Mirrors the flight list's foreground
            // reload — the open briefing shouldn't stay frozen on a stale pack.
            if scenePhase == .active {
                Task { await viewModel?.syncLatestPack() }
            }
        }
        .onChange(of: appState.externalSync) {
            // A push (or any external "data changed" nudge) is just another sync
            // trigger — adopt the newest online pack seamlessly.
            Task { await viewModel?.syncLatestPack() }
        }
        .onDisappear {
            // Stop the timing-scenario poll when the briefing leaves the screen —
            // it runs in a detached Task (not the `.task` above), so it would
            // otherwise keep the view model alive and polling in the background
            // until the scan reaches a terminal state. Re-entry recreates the VM
            // via `.task` and restarts polling.
            viewModel?.stopTimeOptionsPolling()
        }
    }

    /// Per-flight notification bell — Default / Always / Mute. Delivery still
    /// uses the account channels; this controls "whether", not "how". The footer
    /// shows what Default currently resolves to (and adapts when account = Off).
    private var notifyBellMenu: some View {
        Menu {
            Picker("Notifications for this flight", selection: Binding(
                get: { notifyOverride },
                set: { setNotifyOverride($0) }
            )) {
                ForEach(FlightNotifyOverride.allCases) { mode in
                    Label(mode.label, systemImage: mode.systemImage).tag(mode)
                }
            }
            Section { Text(notifyOverrideHint) }
        } label: {
            Label("Notifications", systemImage: notifyOverride.systemImage)
                .font(.caption)
        }
        .disabled(notifyOverrideBusy || appState.apiClient == nil)
    }

    /// Adaptive hint describing the current override / what Default resolves to.
    private var notifyOverrideHint: String {
        let account = appState.userPreferences.preferences.briefingUpdates
        switch notifyOverride {
        case .mute:
            return "Muted — never notify for this flight"
        case .notify:
            return account == .off ? "Always — overriding account Off" : "Always notify for this flight"
        case .default:
            switch account {
            case .off: return "Following account: Off"
            case .changes: return "Following account: Assessment changes"
            case .every: return "Following account: Every update"
            }
        }
    }

    /// Persist the per-flight override (optimistic; reverts on failure).
    private func setNotifyOverride(_ value: FlightNotifyOverride) {
        guard let repo = appState.repository, value != notifyOverride else { return }
        let previous = notifyOverride
        notifyOverride = value
        notifyOverrideBusy = true
        Task {
            do {
                _ = try await repo.updateFlight(
                    flightId: flight.id,
                    request: UpdateFlightRequest(notifyOverride: value.rawValue)
                )
            } catch {
                notifyOverride = previous
            }
            notifyOverrideBusy = false
        }
    }

    private func startTracking() {
        guard let viewModel,
              case .loaded(let analyses) = viewModel.routeAnalysesState else { return }
        guard let departure = flight.departureDate else { return }

        let routePoints = analyses.analyses.map { rpa in
            TrackingRoutePoint(
                coordinate: .init(latitude: rpa.lat, longitude: rpa.lon),
                distanceFromOriginNm: rpa.distanceFromOriginNm
            )
        }
        let flightEndTime = departure.addingTimeInterval((flight.flightDurationHours + 2) * 3600)
        trackingService.start(routePoints: routePoints, flightEndTime: flightEndTime)
    }
}

/// Toolbar with pack history picker, flight tracking, and refresh button.
private struct BriefingToolbarView: View {
    @Bindable var viewModel: BriefingViewModel
    var trackingService: FlightTrackingService
    var isInFlightWindow: Bool
    var startTracking: () -> Void

    // Contextual tips (#312): the refresh/download pair reads side by side, so
    // the download tip is sequenced after the refresh tip donates its event.
    private let downloadTip = DownloadBriefingTip()
    private let refreshTip = RefreshBriefingTip()

    var body: some View {
        HStack(spacing: 12) {
            // Start / Stop Flight button
            if isInFlightWindow {
                Button {
                    if trackingService.isTracking {
                        trackingService.stop()
                    } else {
                        startTracking()
                    }
                } label: {
                    Label(trackingService.isTracking ? "Stop" : "Start",
                          systemImage: trackingService.isTracking ? "location.fill" : "location")
                        .font(.caption)
                        .foregroundStyle(trackingService.isTracking ? .red : .accentColor)
                }
            }

            // Pack history picker now lives in the connective header (§4.10).

            // Download / cache button
            switch viewModel.downloadState {
            case .notDownloaded:
                Button {
                    Task {
                        await viewModel.downloadCurrentPack()
                        // Retire the download tip once its pack is saved.
                        if case .downloaded = viewModel.downloadState {
                            downloadTip.invalidate(reason: .actionPerformed)
                        }
                    }
                } label: {
                    Image(systemName: "arrow.down.circle")
                }
                .popoverTip(downloadTip)
            case .downloading(let progress, _, _):
                ProgressView(value: progress)
                    .progressViewStyle(.circular)
                    .controlSize(.small)
                    .frame(width: 20, height: 20)
            case .downloaded:
                Menu {
                    Button(role: .destructive) {
                        Task { await viewModel.deleteCurrentPack() }
                    } label: {
                        Label("Remove Download", systemImage: "trash")
                    }
                } label: {
                    Image(systemName: "arrow.down.circle.fill")
                        .foregroundStyle(.green)
                }
                // Auto-download means the briefing is almost always already
                // `.downloaded` by the time the toolbar renders, so the tip must
                // anchor here too — otherwise its only anchor (`.notDownloaded`)
                // is never in the hierarchy and the coachmark can never show
                // (nor can "Show All Tips" render it). Reworded as an offline-
                // availability *indicator* (#312), it explains this green icon.
                .popoverTip(downloadTip)
            case .error:
                Button {
                    Task { await viewModel.downloadCurrentPack() }
                } label: {
                    Image(systemName: "exclamationmark.arrow.circlepath")
                        .foregroundStyle(.red)
                }
            }

            // Refresh button
            Button {
                Task {
                    await viewModel.refresh()
                    // Don't burn the coaching tip on a failed refresh — keep it
                    // visible so the user can retry with the coaching present
                    // (mirrors the download-tip guard). `.completed`/`.noRefresh`
                    // are the successful terminal states; `.error` is failure.
                    if case .error = viewModel.refreshState { return }
                    refreshTip.invalidate(reason: .actionPerformed)
                }
            } label: {
                if viewModel.refreshState.isRefreshing {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Image(systemName: "arrow.clockwise")
                }
            }
            .disabled(viewModel.refreshState.isRefreshing)
            .popoverTip(refreshTip)
        }
        // Sequence the download tip after the refresh tip: when the refresh tip
        // is dismissed — closed via its × OR acted on by refreshing — donate the
        // event that makes the download tip eligible, so the fetch-new /
        // offline-save pair reads in order (#312).
        .task {
            for await status in refreshTip.statusUpdates {
                if case .invalidated = status {
                    await BriefingTipEvents.refreshTipSeen.donate()
                    break
                }
            }
        }
        .onAppear {
            // Recovery for the narrow window where the app is killed between the
            // refresh tip invalidating and the donate() above completing:
            // `statusUpdates` is a change stream, so it won't replay
            // `.invalidated` next launch. If the refresh tip is already
            // dismissed but the event never landed, donate proactively so the
            // download tip can't get stuck permanently ineligible.
            if case .invalidated = refreshTip.status,
               BriefingTipEvents.refreshTipSeen.donations.isEmpty {
                Task { await BriefingTipEvents.refreshTipSeen.donate() }
            }
        }
    }
}

/// Banner showing refresh progress or completion status.
private struct RefreshBannerView: View {
    let state: RefreshState

    var body: some View {
        switch state {
        case .idle:
            EmptyView()
        case .refreshing(let stage, let detail, let progress):
            VStack(spacing: 4) {
                HStack(spacing: 8) {
                    ProgressView()
                        .controlSize(.small)
                    Text(stage)
                        .font(.caption.bold())
                    if let detail {
                        Text(detail)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text("\(Int(progress * 100))%")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                ProgressView(value: progress)
                    .tint(.accentColor)
            }
            .padding(.horizontal)
            .padding(.vertical, 6)
            .background(.regularMaterial)
        case .completed(let elapsed):
            HStack {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                Text("Refreshed in \(Int(elapsed))s")
                    .font(.caption.bold())
                Spacer()
            }
            .padding(.horizontal)
            .padding(.vertical, 6)
            .background(.green.opacity(0.1))
        case .noRefresh(let message):
            HStack(spacing: 8) {
                Image(systemName: "checkmark.seal")
                    .foregroundStyle(.secondary)
                Text(message)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
            }
            .padding(.horizontal)
            .padding(.vertical, 6)
            .background(.regularMaterial)
        case .error(let message):
            HStack {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                Text(message)
                    .font(.caption)
                Spacer()
            }
            .padding(.horizontal)
            .padding(.vertical, 6)
            .background(.red.opacity(0.1))
        }
    }
}

/// Banner showing pack download progress (size + percentage) while downloading.
private struct DownloadBannerView: View {
    let state: DownloadState

    private static let byteFormatter: ByteCountFormatter = {
        let f = ByteCountFormatter()
        f.countStyle = .file
        f.allowedUnits = [.useKB, .useMB]
        f.allowsNonnumericFormatting = false  // "0 KB", not "Zero KB"
        return f
    }()

    var body: some View {
        if case .downloading(let progress, let received, let total) = state {
            // Before any bytes arrive the server is still building the bundle, so
            // there's nothing to count yet — label that phase "Preparing…".
            let isPreparing = total <= 0 && received == 0
            VStack(spacing: 4) {
                HStack(spacing: 8) {
                    // Spinner only while there's no determinate bar to convey activity.
                    if total <= 0 {
                        ProgressView()
                            .controlSize(.small)
                    }
                    Text(isPreparing ? "Preparing…" : "Downloading…")
                        .font(.caption.bold())
                    if let detail = sizeText(received: received, total: total) {
                        Text(detail)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    if total > 0 {
                        Text("\(Int(progress * 100))%")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                if total > 0 {
                    ProgressView(value: progress)
                        .tint(.accentColor)
                } else {
                    // .linear is required: a bare ProgressView() renders as a circular
                    // spinner; we want an indeterminate bar while the total is unknown.
                    ProgressView()
                        .progressViewStyle(.linear)
                }
            }
            .padding(.horizontal)
            .padding(.vertical, 6)
            .background(.regularMaterial)
        }
    }

    private func sizeText(received: Int64, total: Int64) -> String? {
        let f = Self.byteFormatter
        if total > 0 {
            return "\(f.string(fromByteCount: received)) / \(f.string(fromByteCount: total))"
        }
        if received > 0 {
            return f.string(fromByteCount: received)
        }
        return nil
    }
}

/// Inner content once the view model is ready (#310). Tabs: Advisory ·
/// Discussion · Cross-Section · Map (+ gated PIREPs), rendered by a native
/// `TabView` on both idioms — a bottom tab bar on iPhone (compact), a top tab
/// bar on iPad (regular). Both drive `viewModel.selectedTab`, so deep-links
/// behave identically. (The custom iPad pill band this replaced hit the same
/// NavigationSplitView-reuse compositing bug as the section spy bar — #437.)
private struct BriefingContentView: View {
    @Bindable var viewModel: BriefingViewModel
    var trackingService: FlightTrackingService
    /// Opens the PIREP reporting sheet (owned by the container's toolbar state),
    /// so the PIREPs tab can carry its own permanent "+".
    var onAddPirep: () -> Void
    @Environment(AppState.self) private var appState

    private var canPublishPireps: Bool {
        appState.userPreferences.preferences.pirepCanPublish
    }

    private var tabs: [BriefingTab] {
        var tabs = BriefingTab.core
        // A publisher can reach the tab even without view permission — that's
        // where the permanent "Report a PIREP" action lives.
        if appState.userPreferences.preferences.pirepCanView || canPublishPireps {
            tabs.append(.pireps)
        }
        return tabs
    }

    var body: some View {
        content
            // Pull-to-refresh = seamless *sync* to the newest online pack (NOT the
            // ↻ button's full regeneration). The action propagates via the
            // environment to whichever tab's vertical ScrollView is visible
            // (Advisory / Discussion); the canvas tabs simply don't offer a pull.
            .refreshable { await viewModel.syncLatestPack() }
    }

    private var content: some View {
        // Native TabView on BOTH idioms: iPhone renders a bottom tab bar, iPad a
        // top tab bar. This replaced a custom `BriefingTabBand` (a horizontally-
        // scrolling pill bar pinned as a sibling above the tab content on regular
        // width) which — like the section spy bar (#436) — composited zero pixels
        // when the reused NavigationSplitView detail column re-presented on iPad,
        // leaving the briefing with no way to switch tabs. The system-hosted tab
        // bar isn't a custom sibling of the scroll content, so it is immune.
        TabView(selection: $viewModel.selectedTab) {
            ForEach(tabs, id: \.self) { tab in
                Tab(tab.title, systemImage: tab.systemImage, value: tab) {
                    tabContent(tab)
                }
            }
        }
    }

    @ViewBuilder
    private func tabContent(_ tab: BriefingTab) -> some View {
        switch tab {
        case .advisory:
            AdvisoryTabView(viewModel: viewModel)
        case .discussion:
            DiscussionTabView(viewModel: viewModel)
        case .crossSection:
            CrossSectionView(viewModel: viewModel, trackingService: trackingService)
        case .map:
            RouteMapView(viewModel: viewModel, trackingService: trackingService)
        case .pireps:
            PirepListView(
                pirepsState: viewModel.pirepsState,
                canPublish: canPublishPireps,
                canView: appState.userPreferences.preferences.pirepCanView,
                onAdd: onAddPirep,
                retryAction: { await viewModel.loadPireps() }
            )
        }
    }
}

/// Toolbar control merging freshness + pack (D-N) history into one menu (#310):
/// a freshness dot + current pack label opens the pack-history picker, with the
/// detailed freshness line as a non-interactive header.
private struct BriefingPackToolbar: View {
    @Bindable var viewModel: BriefingViewModel

    var body: some View {
        if viewModel.packHistory.count > 1 {
            Menu {
                Section(freshnessText) {
                    ForEach(viewModel.packHistory, id: \.fetchTimestamp) { pack in
                        Button {
                            viewModel.selectedPackTimestamp = pack.fetchTimestamp
                        } label: {
                            HStack {
                                Text(viewModel.packLabel(for: pack))
                                if viewModel.packCacheStatus[pack.fetchTimestamp] == true {
                                    Image(systemName: "arrow.down.circle.fill")
                                        .foregroundStyle(Theme.green)
                                }
                                if pack.fetchTimestamp == viewModel.selectedPackTimestamp {
                                    Image(systemName: "checkmark")
                                }
                            }
                        }
                    }
                }
            } label: {
                label
            }
        } else if viewModel.pack != nil {
            label
        }
    }

    private var label: some View {
        HStack(spacing: Theme.spacingXS) {
            Circle()
                .fill(freshnessFresh ? Theme.green : Theme.amber)
                .frame(width: 7, height: 7)
            Text(currentPackLabel)
                .font(.caption.weight(.medium))
        }
    }

    private var currentPackLabel: String {
        if let pack = viewModel.pack {
            return viewModel.packChipLabel(for: pack)
        }
        return "History"
    }

    private var freshnessFresh: Bool {
        viewModel.pack?.dataStatus?.fresh ?? true
    }

    private var freshnessText: String {
        guard let status = viewModel.pack?.dataStatus else {
            return initTimesText(viewModel.pack?.modelInitTimes ?? [:])
        }
        if status.fresh {
            return initTimesText(status.modelInitTimes.isEmpty ? (viewModel.pack?.modelInitTimes ?? [:]) : status.modelInitTimes)
        }
        let stale = status.staleModels.map { $0.uppercased() }.joined(separator: ", ")
        return stale.isEmpty ? "Updating…" : "Updating: \(stale)"
    }

    private func initTimesText(_ times: [String: Int]) -> String {
        let parts = times
            .sorted { $0.key < $1.key }
            .prefix(3)
            .map { "\($0.key.uppercased()) \(String(format: "%02d", $0.value))Z" }
        return parts.isEmpty ? "Forecast" : parts.joined(separator: " · ")
    }
}
