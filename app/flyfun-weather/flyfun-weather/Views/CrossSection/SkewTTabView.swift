import SwiftUI

/// Full-screen Skew-T instrument tab linked to the briefing active route point.
struct SkewTTabView: View {
    @Bindable var viewModel: BriefingViewModel
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(spacing: 0) {
            controlStrip
            Divider()

            if let activePoint = viewModel.activePoint {
                SkewTDetailView(viewModel: viewModel, pointIndex: activePoint.pointIndex)
                    .id("\(activePoint.pointIndex)-\(viewModel.selectedModel)")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView(
                    "Select a Route Point",
                    systemImage: "chart.line.uptrend.xyaxis",
                    description: Text("Load the route analysis or choose a point from the cross-section.")
                )
            }
        }
        .background(WeatherTheme.background(colorScheme))
        .onAppear {
            if viewModel.activePoint == nil,
               case .loaded(let analyses) = viewModel.routeAnalysesState {
                viewModel.setActivePoint(analyses.analyses.first)
            }
        }
    }

    private var controlStrip: some View {
        HStack(spacing: WeatherTheme.Spacing.sm) {
            Button {
                moveSelection(by: -1)
            } label: {
                Image(systemName: "chevron.left")
            }
            .disabled(!canMove(by: -1))
            .accessibilityLabel("Previous route point")

            Menu {
                ForEach(routePoints, id: \.pointIndex) { point in
                    Button {
                        viewModel.setActivePoint(point)
                    } label: {
                        HStack {
                            Text(label(for: point))
                            if point.pointIndex == viewModel.activePoint?.pointIndex {
                                Image(systemName: "checkmark")
                            }
                        }
                    }
                }
            } label: {
                Label(activePointLabel, systemImage: "scope")
                    .font(.caption.bold())
                    .lineLimit(1)
                    .padding(.horizontal, WeatherTheme.Spacing.sm)
                    .padding(.vertical, WeatherTheme.Spacing.xs)
                    .background(
                        WeatherTheme.primary(colorScheme).opacity(0.10),
                        in: RoundedRectangle(cornerRadius: WeatherTheme.Radius.control)
                    )
            }
            .buttonStyle(.plain)
            .disabled(routePoints.isEmpty)

            Button {
                moveSelection(by: 1)
            } label: {
                Image(systemName: "chevron.right")
            }
            .disabled(!canMove(by: 1))
            .accessibilityLabel("Next route point")

            Spacer()

            ModelSelectorView(
                selectedModel: Binding(
                    get: { viewModel.selectedModel },
                    set: { viewModel.selectedModel = $0 }
                ),
                models: viewModel.availableModels
            )
        }
        .padding(.horizontal, WeatherTheme.Spacing.lg)
        .padding(.vertical, WeatherTheme.Spacing.sm)
        .background(WeatherTheme.surface(colorScheme))
    }

    private var routePoints: [RoutePointAnalysis] {
        guard case .loaded(let analyses) = viewModel.routeAnalysesState else { return [] }
        return analyses.analyses
    }

    private var activePointLabel: String {
        guard let point = viewModel.routePoint(for: viewModel.activePoint) else {
            return "Point"
        }
        return label(for: point)
    }

    private func label(for point: RoutePointAnalysis) -> String {
        let name = point.waypointIcao ?? "Pt \(point.pointIndex)"
        return "\(name) · \(Int(point.distanceFromOriginNm)) nm"
    }

    private func canMove(by offset: Int) -> Bool {
        guard let index = activeRouteIndex else { return false }
        return routePoints.indices.contains(index + offset)
    }

    private func moveSelection(by offset: Int) {
        guard let index = activeRouteIndex else { return }
        let nextIndex = index + offset
        guard routePoints.indices.contains(nextIndex) else { return }
        viewModel.setActivePoint(routePoints[nextIndex])
    }

    private var activeRouteIndex: Int? {
        guard let activePoint = viewModel.activePoint else { return nil }
        return routePoints.firstIndex { $0.pointIndex == activePoint.pointIndex }
    }
}
