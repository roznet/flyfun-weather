import SwiftUI

/// Tab-based briefing viewer for a single flight.
struct BriefingContainerView: View {
    let flight: FlightResponse
    @Environment(AppState.self) private var appState
    @State private var viewModel: BriefingViewModel?

    var body: some View {
        Group {
            if let viewModel {
                VStack(spacing: 0) {
                    RefreshBannerView(state: viewModel.refreshState)
                    BriefingContentView(viewModel: viewModel)
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
                    BriefingToolbarView(viewModel: viewModel)
                }
            }
        }
        .task {
            guard let repo = appState.repository else { return }
            let vm = BriefingViewModel(flight: flight, repository: repo)
            viewModel = vm
            await vm.loadBriefing()
            await vm.checkActiveRefresh()
        }
    }
}

/// Toolbar with pack history picker and refresh button.
private struct BriefingToolbarView: View {
    @Bindable var viewModel: BriefingViewModel

    var body: some View {
        HStack(spacing: 12) {
            // Pack history picker
            if viewModel.packHistory.count > 1 {
                Menu {
                    ForEach(viewModel.packHistory, id: \.fetchTimestamp) { pack in
                        Button {
                            viewModel.selectedPackTimestamp = pack.fetchTimestamp
                        } label: {
                            HStack {
                                Text(viewModel.packLabel(for: pack))
                                if pack.fetchTimestamp == viewModel.selectedPackTimestamp {
                                    Image(systemName: "checkmark")
                                }
                                if let assessment = pack.assessment {
                                    AssessmentStringBadge(status: assessment)
                                }
                            }
                        }
                    }
                } label: {
                    Label(currentPackLabel, systemImage: "clock.arrow.circlepath")
                        .font(.caption)
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

/// Inner content once the view model is ready.
private struct BriefingContentView: View {
    @Bindable var viewModel: BriefingViewModel

    var body: some View {
        TabView(selection: $viewModel.selectedTab) {
            Tab("Advisories", systemImage: "exclamationmark.shield", value: BriefingTab.advisories) {
                AdvisoryDashboardView(viewModel: viewModel)
            }

            Tab("Cross-Section", systemImage: "chart.xyaxis.line", value: BriefingTab.crossSection) {
                CrossSectionView(viewModel: viewModel)
            }

            Tab("Map", systemImage: "map", value: BriefingTab.map) {
                RouteMapView(viewModel: viewModel)
            }

            Tab("Digest", systemImage: "doc.text", value: BriefingTab.digest) {
                DigestView(viewModel: viewModel)
            }
        }
    }
}
