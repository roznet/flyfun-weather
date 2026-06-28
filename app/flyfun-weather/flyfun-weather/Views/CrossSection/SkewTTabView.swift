import SwiftUI

/// The Skew-T sounding view (§4.7). Since #310 it is no longer a top-level tab
/// — it renders **below the cross-section in the same scroll** (`embeddedHeight`
/// set) and the cross-section's "Sounding ›" deep-link scrolls to it. Shows the
/// sounding for the shared `activePoint`; a route strip with ‹ prev / next ›
/// picks the point. When `embeddedHeight` is nil it fills its container.
struct SkewTTabView: View {
    let viewModel: BriefingViewModel
    /// Bounded height when embedded in a scroll (#310). nil = fill container.
    var embeddedHeight: CGFloat? = nil

    var body: some View {
        VStack(spacing: 0) {
            if let indices = pointIndices, !indices.isEmpty {
                let current = viewModel.activePointIndex ?? indices.first ?? 0
                pointStrip(indices: indices, current: current)
                Divider()
                SkewTDetailView(viewModel: viewModel, pointIndex: current)
                    .frame(maxWidth: .infinity, minHeight: embeddedHeight, maxHeight: embeddedHeight ?? .infinity)
            } else {
                placeholder
                    .frame(maxWidth: .infinity, minHeight: embeddedHeight, maxHeight: embeddedHeight ?? .infinity)
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
