import SwiftUI

/// Tab-based briefing viewer for a single flight.
struct BriefingContainerView: View {
    let flight: FlightResponse
    @Environment(AppState.self) private var appState
    @State private var viewModel: BriefingViewModel?

    var body: some View {
        Group {
            if let viewModel {
                BriefingContentView(viewModel: viewModel)
            } else {
                ProgressView("Loading briefing...")
            }
        }
        .navigationTitle(flight.routeName)
        .navigationBarTitleDisplayMode(.inline)
        .task {
            guard let repo = appState.repository else { return }
            let vm = BriefingViewModel(flight: flight, repository: repo)
            viewModel = vm
            await vm.loadBriefing()
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
