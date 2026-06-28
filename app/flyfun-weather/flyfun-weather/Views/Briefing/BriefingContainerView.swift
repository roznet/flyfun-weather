import CoreLocation
import SwiftUI
import TipKit

/// Tab-based briefing viewer for a single flight.
struct BriefingContainerView: View {
    let flight: FlightResponse
    @Environment(AppState.self) private var appState
    @State private var viewModel: BriefingViewModel?
    @State private var trackingService = FlightTrackingService()
    @State private var showingPirepSheet = false

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
            if let viewModel {
                // #310: the standalone header band is gone — identity moved into
                // the nav bar (title + subtitle), freshness + pack picker into
                // the toolbar. Tabs render at the top (iPad) / bottom (iPhone).
                VStack(spacing: 0) {
                    RefreshBannerView(state: viewModel.refreshState)
                    DownloadBannerView(state: viewModel.downloadState)
                    BriefingContentView(viewModel: viewModel, trackingService: trackingService)
                }
            } else {
                ProgressView("Loading briefing...")
            }
        }
        .navigationTitle(flight.shortTitle)
        .navigationSubtitle(identitySubtitle)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let viewModel {
                ToolbarItem(placement: .topBarLeading) {
                    BriefingPackToolbar(viewModel: viewModel)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    HStack(spacing: 12) {
                        if isInFlightWindow && appState.userPreferences.preferences.pirepCanPublish {
                            Button {
                                showingPirepSheet = true
                            } label: {
                                Label("Report PIREP", systemImage: "square.and.pencil")
                                    .font(.caption)
                            }
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
            // Pick up server-side flag changes (e.g. pirep_can_publish)
            // before the toolbar decides whether to show the PIREP button.
            await appState.refreshUserPreferences()
            // Cache flight data for offline recovery
            if let caching = repo as? CachingBriefingRepository {
                await caching.cacheFlightData(flight)
            }
            let vm = BriefingViewModel(flight: flight, repository: repo)
            viewModel = vm
            await vm.loadBriefing()
            await vm.checkActiveRefresh()
            await vm.loadPireps()
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

    // Contextual tips (#312): the download/refresh pair reads side by side, so
    // the refresh tip is sequenced after the download tip donates its event.
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
                        // Retire the tip on a successful download. The refresh
                        // tip's sequencing is driven by the dismissal watcher
                        // below, so it fires whether the user downloads or just
                        // closes the tip.
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
        // Sequence the refresh tip after the download tip: when the download tip
        // is dismissed — closed via its × OR acted on by downloading — donate the
        // event that makes the refresh tip eligible, so the offline-save /
        // fetch-new pair reads in order (#312).
        .task {
            for await status in downloadTip.statusUpdates {
                if case .invalidated = status {
                    await BriefingTipEvents.downloadTipSeen.donate()
                    break
                }
            }
        }
        .onAppear {
            // Recovery for the narrow window where the app is killed between the
            // download tip invalidating and the donate() above completing:
            // `statusUpdates` is a change stream, so it won't replay
            // `.invalidated` next launch. If the download tip is already
            // dismissed but the event never landed, donate proactively so the
            // refresh tip can't get stuck permanently ineligible.
            if case .invalidated = downloadTip.status,
               BriefingTipEvents.downloadTipSeen.donations.isEmpty {
                Task { await BriefingTipEvents.downloadTipSeen.donate() }
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
/// Discussion · Cross-Section · Map (+ gated PIREPs). On regular width (iPad)
/// they render as a custom top pill band with switched content; on compact
/// width (iPhone) they collapse to a native bottom tab bar. Both drive
/// `viewModel.selectedTab`, so deep-links behave identically.
private struct BriefingContentView: View {
    @Bindable var viewModel: BriefingViewModel
    var trackingService: FlightTrackingService
    @Environment(AppState.self) private var appState
    @Environment(\.horizontalSizeClass) private var sizeClass

    private var tabs: [BriefingTab] {
        var tabs = BriefingTab.core
        if appState.userPreferences.preferences.pirepCanView { tabs.append(.pireps) }
        return tabs
    }

    var body: some View {
        if sizeClass == .regular {
            VStack(spacing: 0) {
                BriefingTabBand(tabs: tabs, selection: $viewModel.selectedTab)
                tabContent(viewModel.selectedTab)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        } else {
            TabView(selection: $viewModel.selectedTab) {
                ForEach(tabs, id: \.self) { tab in
                    Tab(tab.title, systemImage: tab.systemImage, value: tab) {
                        tabContent(tab)
                    }
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
            PirepListView(pirepsState: viewModel.pirepsState) {
                await viewModel.loadPireps()
            }
        }
    }
}

/// Custom top tab band (iPad / regular width, #310): pill-styled tabs driving
/// the selection, behaving identically nested in the split-view detail.
private struct BriefingTabBand: View {
    let tabs: [BriefingTab]
    @Binding var selection: BriefingTab

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: Theme.spacingS) {
                    ForEach(tabs, id: \.self) { tab in
                        let active = tab == selection
                        Button { selection = tab } label: {
                            Label(tab.title, systemImage: tab.systemImage)
                                .font(.subheadline.weight(active ? .semibold : .regular))
                                .foregroundStyle(active ? Theme.primary : Theme.textMuted)
                                .padding(.horizontal, 14).padding(.vertical, 8)
                                .background(active ? Theme.primary.opacity(0.12) : Color.clear, in: Capsule())
                        }
                        .buttonStyle(.plain)
                        .id(tab)
                    }
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, Theme.cardPadding)
                .padding(.vertical, Theme.spacingS)
            }
            // Keep the active tab visible if a deep-link switches tabs while the
            // band is scrolled (robust if the tab set ever overflows).
            .onChange(of: selection) { _, newValue in
                withAnimation(.easeInOut(duration: 0.2)) { proxy.scrollTo(newValue, anchor: .center) }
            }
        }
        .background(Theme.surface)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Theme.border).frame(height: 0.5)
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
