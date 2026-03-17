import OSLog
import SwiftUI

private let logger = Logger(subsystem: "aero.flyfun.weather", category: "CrossSection")

/// SwiftUI Canvas wrapper for the cross-section visualization.
struct CrossSectionView: View {
    let viewModel: BriefingViewModel
    @State private var csVM = CrossSectionViewModel()

    var body: some View {
        VStack(spacing: 0) {
            // Layer toggle chips
            layerChips

            // Cross-section canvas
            crossSectionCanvas

            // Route graph below
            RouteGraphView(viewModel: viewModel, vizData: csVM.vizData)
        }
        .onChange(of: viewModel.selectedModel) {
            updateVizData()
        }
        .onChange(of: viewModel.routeAnalysesState.isLoaded) {
            updateVizData()
        }
        .onChange(of: viewModel.elevationState.isLoaded) {
            updateVizData()
        }
        .task {
            updateVizData()
        }
    }

    @ViewBuilder
    private var crossSectionCanvas: some View {
        if let vizData = csVM.vizData {
            Canvas { context, size in
                CrossSectionRenderer(data: vizData, enabledLayers: csVM.enabledLayers)
                    .render(context: &context, size: size)
            }
            .frame(minHeight: 300)
            .aspectRatio(2.0, contentMode: .fit)
        } else {
            switch viewModel.routeAnalysesState {
            case .idle, .loading:
                ProgressView("Loading cross-section...")
                    .frame(minHeight: 300)
            case .error(let error):
                ContentUnavailableView("Cross-Section Unavailable", systemImage: "chart.xyaxis.line",
                                       description: Text(error.localizedDescription))
            case .loaded:
                // Data loaded but vizData is nil — extraction produced no points
                ContentUnavailableView("No Data for Model", systemImage: "chart.xyaxis.line",
                                       description: Text("No cross-section data available for \(viewModel.selectedModel). Try selecting a different model."))
            }
        }
    }

    private var layerChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                // Model selector
                ModelSelectorView(selectedModel: Binding(
                    get: { viewModel.selectedModel },
                    set: { viewModel.selectedModel = $0 }
                ), models: viewModel.availableModels)

                Divider().frame(height: 20)

                ForEach(CrossSectionLayer.allLayers, id: \.id) { layer in
                    let enabled = csVM.enabledLayers[layer.id] ?? false
                    Button {
                        csVM.toggleLayer(layer.id)
                    } label: {
                        Text(layer.name)
                            .font(.caption2)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(enabled ? Color.accentColor.opacity(0.15) : Color.clear)
                            .foregroundStyle(enabled ? .primary : .secondary)
                            .clipShape(Capsule())
                            .overlay(Capsule().stroke(enabled ? Color.accentColor : Color.gray.opacity(0.3), lineWidth: 0.5))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
        }
    }

    private func updateVizData() {
        switch viewModel.routeAnalysesState {
        case .idle:
            logger.debug("updateVizData: routeAnalysesState is idle")
        case .loading:
            logger.debug("updateVizData: routeAnalysesState is loading")
        case .error(let error):
            logger.error("updateVizData: routeAnalysesState error: \(error)")
        case .loaded(let analyses):
            logger.info("updateVizData: loaded \(analyses.analyses.count) points, model=\(viewModel.selectedModel), models=\(analyses.models)")
            var elevation: ElevationResponse? = nil
            if case .loaded(let elev) = viewModel.elevationState {
                elevation = elev
            }
            csVM.update(routeAnalyses: analyses, elevation: elevation, model: viewModel.selectedModel)
            if let viz = csVM.vizData {
                logger.info("vizData: \(viz.points.count) points, \(viz.totalDistanceNm)nm, ceiling=\(viz.flightCeilingFt)ft")
            } else {
                logger.warning("vizData is nil after update")
            }
        }
    }
}
