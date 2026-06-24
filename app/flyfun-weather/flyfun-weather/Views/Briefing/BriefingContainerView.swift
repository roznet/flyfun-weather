import CoreLocation
import SwiftUI

/// Tab-based briefing viewer for a single flight.
struct BriefingContainerView: View {
    let flight: FlightResponse
    @Environment(AppState.self) private var appState
    @Environment(\.colorScheme) private var colorScheme
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
                    BriefingHeaderView(
                        viewModel: viewModel,
                        trackingService: trackingService,
                        isInFlightWindow: isInFlightWindow,
                        canPublishPirep: appState.userPreferences.preferences.pirepCanPublish,
                        reportPirep: { showingPirepSheet = true },
                        startTracking: startTracking
                    )
                    RefreshBannerView(state: viewModel.refreshState)
                    DownloadBannerView(state: viewModel.downloadState)
                    BriefingContentView(viewModel: viewModel, trackingService: trackingService)
                }
                .background(WeatherTheme.background(colorScheme))
            } else {
                ProgressView("Loading briefing...")
            }
        }
        .navigationTitle(flight.shortTitle)
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $showingPirepSheet) {
            if let repo = appState.repository {
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

/// Persistent briefing chrome shared by all briefing tabs.
private struct BriefingHeaderView: View {
    @Bindable var viewModel: BriefingViewModel
    var trackingService: FlightTrackingService
    var isInFlightWindow: Bool
    var canPublishPirep: Bool
    var reportPirep: () -> Void
    var startTracking: () -> Void
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.sm) {
            HStack(alignment: .firstTextBaseline, spacing: WeatherTheme.Spacing.md) {
                VStack(alignment: .leading, spacing: WeatherTheme.Spacing.xs) {
                    Text(viewModel.flight.shortTitle)
                        .font(.headline)
                        .foregroundStyle(WeatherTheme.text(colorScheme))
                        .lineLimit(1)
                    Text(flightDetails)
                        .font(.caption)
                        .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                        .lineLimit(1)
                }

                Spacer(minLength: WeatherTheme.Spacing.sm)

                if let assessment = viewModel.pack?.assessment {
                    AssessmentStringBadge(status: assessment)
                }
            }

            HStack(spacing: WeatherTheme.Spacing.sm) {
                BriefingToolbarView(
                    viewModel: viewModel,
                    trackingService: trackingService,
                    isInFlightWindow: isInFlightWindow,
                    startTracking: startTracking
                )

                if isInFlightWindow && canPublishPirep {
                    Button(action: reportPirep) {
                        Image(systemName: "square.and.pencil")
                    }
                    .accessibilityLabel("Report PIREP")
                }
            }

            HStack(spacing: WeatherTheme.Spacing.sm) {
                statusPill(freshnessText, systemImage: freshnessIcon)
                statusPill(cacheText, systemImage: cacheIcon)
                if let activePointText {
                    statusPill(activePointText, systemImage: "scope")
                }
            }

            if let modelSummary {
                Text(modelSummary)
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(WeatherTheme.mutedText(colorScheme))
                    .lineLimit(1)
            }
        }
        .padding(.horizontal, WeatherTheme.Spacing.lg)
        .padding(.vertical, WeatherTheme.Spacing.md)
        .background(WeatherTheme.surface(colorScheme))
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(WeatherTheme.border(colorScheme))
                .frame(height: 0.5)
        }
    }

    private func statusPill(_ text: String, systemImage: String) -> some View {
        Label(text, systemImage: systemImage)
            .font(.caption2)
            .lineLimit(1)
            .foregroundStyle(WeatherTheme.mutedText(colorScheme))
            .padding(.horizontal, WeatherTheme.Spacing.sm)
            .padding(.vertical, WeatherTheme.Spacing.xs)
            .background(WeatherTheme.background(colorScheme), in: RoundedRectangle(cornerRadius: WeatherTheme.Radius.control))
            .overlay {
                RoundedRectangle(cornerRadius: WeatherTheme.Radius.control)
                    .stroke(WeatherTheme.border(colorScheme), lineWidth: 0.5)
            }
    }

    private var flightDetails: String {
        var parts: [String] = []
        if let date = viewModel.flight.departureDate {
            parts.append("\(DateFormatter.shortDate.string(from: date)) \(DateFormatter.utcTime.string(from: date))")
        }
        parts.append("FL\(viewModel.flight.cruiseAltitudeFt / 100)")
        parts.append(String(format: "%.1fh", viewModel.flight.flightDurationHours))
        return parts.joined(separator: " · ")
    }

    private var freshnessText: String {
        guard let status = viewModel.pack?.dataStatus else { return "Freshness pending" }
        if status.fresh { return "Fresh" }
        if status.staleModels.isEmpty { return "Stale data" }
        let models = status.staleModels.map(\.shortModelName).joined(separator: "/")
        return "Stale \(models)"
    }

    private var freshnessIcon: String {
        if viewModel.pack?.dataStatus?.fresh == true { return "checkmark.seal" }
        return "clock.badge.exclamationmark"
    }

    private var cacheText: String {
        switch viewModel.downloadState {
        case .downloaded:
            return "Downloaded"
        case .downloading:
            return "Downloading"
        case .error:
            return "Download issue"
        case .notDownloaded:
            return "Online pack"
        }
    }

    private var cacheIcon: String {
        switch viewModel.downloadState {
        case .downloaded:
            return "arrow.down.circle.fill"
        case .downloading:
            return "arrow.down.circle"
        case .error:
            return "exclamationmark.arrow.circlepath"
        case .notDownloaded:
            return "wifi"
        }
    }

    private var activePointText: String? {
        guard let activePoint = viewModel.activePoint else { return nil }
        let point = activePoint.waypointIcao ?? "Pt \(activePoint.pointIndex)"
        return "\(point) · \(Int(activePoint.distanceNm)) nm"
    }

    private var modelSummary: String? {
        guard let pack = viewModel.pack else { return nil }
        if pack.modelInitTimes.isEmpty {
            return viewModel.availableModels.isEmpty ? nil : "\(viewModel.availableModels.count) models available"
        }
        let summary = pack.modelInitTimes
            .sorted { $0.key < $1.key }
            .prefix(3)
            .map { "\($0.key.shortModelName) \(formatModelInit($0.value))" }
            .joined(separator: " · ")
        let overflow = pack.modelInitTimes.count > 3 ? " · +\(pack.modelInitTimes.count - 3)" : ""
        return summary + overflow
    }

    private func formatModelInit(_ value: Int) -> String {
        let date = Date(timeIntervalSince1970: TimeInterval(value))
        return DateFormatter.utcTime.string(from: date)
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

            // Pack history picker
            if viewModel.packHistory.count > 1 {
                Menu {
                    ForEach(viewModel.packHistory, id: \.fetchTimestamp) { pack in
                        Button {
                            viewModel.selectedPackTimestamp = pack.fetchTimestamp
                        } label: {
                            HStack {
                                Text(viewModel.packLabel(for: pack))
                                if viewModel.packCacheStatus[pack.fetchTimestamp] == true {
                                    Image(systemName: "arrow.down.circle.fill")
                                        .foregroundStyle(.green)
                                        .font(.caption2)
                                }
                                if pack.fetchTimestamp == viewModel.selectedPackTimestamp {
                                    Image(systemName: "checkmark")
                                }
                            }
                        }
                    }
                } label: {
                    Label(currentPackLabel, systemImage: "clock.arrow.circlepath")
                        .font(.caption)
                }
            }

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

    private var currentPackLabel: String {
        if let pack = viewModel.pack {
            return viewModel.packLabel(for: pack)
        }
        return "History"
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

    var body: some View {
        TabView(selection: $viewModel.selectedTab) {
            Tab("Brief", systemImage: "doc.text.magnifyingglass", value: BriefingTab.brief) {
                BriefTabView(viewModel: viewModel)
            }

            Tab("Cross-Section", systemImage: "chart.xyaxis.line", value: BriefingTab.crossSection) {
                CrossSectionView(viewModel: viewModel, trackingService: trackingService)
            }

            Tab("Skew-T", systemImage: "chart.line.uptrend.xyaxis", value: BriefingTab.skewT) {
                SkewTTabView(viewModel: viewModel)
            }

            Tab("Map", systemImage: "map", value: BriefingTab.map) {
                RouteMapView(viewModel: viewModel, trackingService: trackingService)
            }
        }
    }
}
