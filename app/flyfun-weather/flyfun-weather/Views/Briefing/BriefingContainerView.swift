import CoreLocation
import SwiftUI

/// Tab-based briefing viewer for a single flight.
struct BriefingContainerView: View {
    let flight: FlightResponse
    @Environment(AppState.self) private var appState
    @State private var viewModel: BriefingViewModel?
    @State private var trackingService = FlightTrackingService()
    @State private var showingPirepSheet = false

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
                VStack(spacing: 0) {
                    // Connective header (§4.10) — persists above all 4 tabs.
                    BriefingHeaderView(viewModel: viewModel, flight: flight)
                    RefreshBannerView(state: viewModel.refreshState)
                    DownloadBannerView(state: viewModel.downloadState)
                    BriefingContentView(viewModel: viewModel, trackingService: trackingService)
                }
            } else {
                ProgressView("Loading briefing...")
            }
        }
        .navigationTitle(flight.shortTitle)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let viewModel {
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
                    Task { await viewModel.downloadCurrentPack() }
                } label: {
                    Image(systemName: "arrow.down.circle")
                }
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
                Task { await viewModel.refresh() }
            } label: {
                if viewModel.refreshState.isRefreshing {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Image(systemName: "arrow.clockwise")
                }
            }
            .disabled(viewModel.refreshState.isRefreshing)
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

/// Inner content once the view model is ready.
private struct BriefingContentView: View {
    @Bindable var viewModel: BriefingViewModel
    var trackingService: FlightTrackingService
    @Environment(AppState.self) private var appState

    var body: some View {
        // iPhone = 4 tabs: Brief · Cross-section · Skew-T · Map (§4.1).
        // PIREPs stays as a gated extra tab so offline reporting doesn't regress.
        TabView(selection: $viewModel.selectedTab) {
            Tab("Brief", systemImage: "doc.text.image", value: BriefingTab.brief) {
                BriefView(viewModel: viewModel)
            }

            Tab("Cross-Section", systemImage: "chart.xyaxis.line", value: BriefingTab.crossSection) {
                CrossSectionView(viewModel: viewModel, trackingService: trackingService)
            }

            Tab("Skew-T", systemImage: "chart.dots.scatter", value: BriefingTab.skewT) {
                SkewTTabView(viewModel: viewModel)
            }

            Tab("Map", systemImage: "map", value: BriefingTab.map) {
                RouteMapView(viewModel: viewModel, trackingService: trackingService)
            }

            if appState.userPreferences.preferences.pirepCanView {
                Tab("PIREPs", systemImage: "cloud.sun", value: BriefingTab.pireps) {
                    PirepListView(pirepsState: viewModel.pirepsState) {
                        await viewModel.loadPireps()
                    }
                }
            }
        }
    }
}
