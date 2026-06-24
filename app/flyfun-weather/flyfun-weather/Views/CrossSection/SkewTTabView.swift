import SwiftUI

/// The Skew-T peer tab (§4.7 — promoted from a drill-up to its own full-screen
/// tab). Shows the sounding for the shared `activePoint`; scrubbing the
/// cross-section then switching here lands on that point. A route strip with
/// ‹ prev / next › picks the point. Phase 4 adds overlay bands, interactivity,
/// and the side-panel variable(s) in the RZSkewT package.
struct SkewTTabView: View {
    let viewModel: BriefingViewModel

    var body: some View {
        VStack(spacing: 0) {
            if let indices = pointIndices, !indices.isEmpty {
                let current = viewModel.activePointIndex ?? indices.first ?? 0
                pointStrip(indices: indices, current: current)
                Divider()
                SkewTDetailView(viewModel: viewModel, pointIndex: current)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                placeholder
            }
        }
        .background(Theme.bg)
        .onAppear {
            // Default to a sensible point (departure) when nothing is active yet.
            if viewModel.activePointIndex == nil { viewModel.activePointIndex = pointIndices?.first }
        }
    }

    private var pointIndices: [Int]? {
        if case .loaded(let analyses) = viewModel.routeAnalysesState {
            return analyses.analyses.map(\.pointIndex).sorted()
        }
        return nil
    }

    private func routePoint(_ index: Int) -> RoutePointAnalysis? {
        if case .loaded(let analyses) = viewModel.routeAnalysesState {
            return analyses.analyses.first { $0.pointIndex == index }
        }
        return nil
    }

    private func pointLabel(_ index: Int) -> String {
        guard let rp = routePoint(index) else { return "Point \(index)" }
        if let icao = rp.waypointIcao, !icao.isEmpty { return icao.uppercased() }
        return "\(Int(rp.distanceFromOriginNm)) nm"
    }

    @ViewBuilder
    private func pointStrip(indices: [Int], current: Int) -> some View {
        let pos = indices.firstIndex(of: current) ?? 0
        HStack {
            Button {
                if pos > 0 { viewModel.activePointIndex = indices[pos - 1] }
            } label: { Image(systemName: "chevron.left.circle.fill") }
                .disabled(pos <= 0)

            Spacer()
            VStack(spacing: 2) {
                Text("Sounding").font(.caption).foregroundStyle(Theme.textMuted)
                Text(pointLabel(current)).font(.headline).foregroundStyle(Theme.text)
            }
            Spacer()

            Button {
                if pos < indices.count - 1 { viewModel.activePointIndex = indices[pos + 1] }
            } label: { Image(systemName: "chevron.right.circle.fill") }
                .disabled(pos >= indices.count - 1)
        }
        .font(.title2)
        .tint(Theme.primary)
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, Theme.spacingS)
    }

    @ViewBuilder
    private var placeholder: some View {
        switch viewModel.routeAnalysesState {
        case .idle, .loading:
            ProgressView("Loading sounding…").frame(maxWidth: .infinity, maxHeight: .infinity)
        case .error(let error):
            ContentUnavailableView("Skew-T Unavailable", systemImage: "chart.dots.scatter",
                                   description: Text(error.localizedDescription))
        case .loaded:
            ContentUnavailableView("No Sounding Data", systemImage: "chart.dots.scatter",
                                   description: Text("No sounding available for \(viewModel.selectedModel)."))
        }
    }
}
