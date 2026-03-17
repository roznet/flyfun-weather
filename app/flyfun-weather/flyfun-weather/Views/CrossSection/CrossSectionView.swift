import OSLog
import SwiftUI

private let logger = Logger(subsystem: "aero.flyfun.weather", category: "CrossSection")

/// SwiftUI Canvas wrapper for the cross-section visualization.
struct CrossSectionView: View {
    let viewModel: BriefingViewModel
    @State private var csVM = CrossSectionViewModel()
    @State private var selectedPointIndex: Int?

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                // Layer toggle chips
                layerChips

                // Cross-section canvas
                crossSectionCanvas

                // Route graph below
                RouteGraphView(viewModel: viewModel, vizData: csVM.vizData)

                // Skew-T detail for selected point
                if let pointIndex = selectedPointIndex {
                    Divider()
                    SkewTDetailView(viewModel: viewModel, pointIndex: pointIndex)
                        .frame(minHeight: 300)
                        .transition(.move(edge: .bottom))
                }
            }
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
            .onTapGesture { location in
                handleTap(at: location)
            }
        } else {
            switch viewModel.routeAnalysesState {
            case .idle, .loading:
                ProgressView("Loading cross-section...")
                    .frame(minHeight: 300)
            case .error(let error):
                ContentUnavailableView("Cross-Section Unavailable", systemImage: "chart.xyaxis.line",
                                       description: Text(error.localizedDescription))
            case .loaded:
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

    // MARK: - Tap handling

    private func handleTap(at location: CGPoint) {
        guard let vizData = csVM.vizData else { return }
        // Reconstruct the transform to map pixel → distance
        // Use the same size as the canvas (we need GeometryReader for exact size,
        // but we can approximate from the vizData)
        let points = vizData.points
        guard !points.isEmpty else { return }

        // Find nearest point by x-position proportion
        // The canvas uses CoordTransform with margins, but onTapGesture gives location
        // relative to the view. We'll approximate using the total distance.
        let margins = CoordTransform.margins
        // Estimate canvas width from the aspect ratio constraint
        let tapFraction = Double(location.x - margins.left) /
            Double(max(1, location.x + margins.right)) // rough fraction

        let tapDistanceNm = tapFraction * vizData.totalDistanceNm
        let nearest = points.enumerated().min(by: {
            abs($0.element.distanceNm - tapDistanceNm) < abs($1.element.distanceNm - tapDistanceNm)
        })

        guard let nearest else { return }

        // Find the point_index from route analyses
        if case .loaded(let analyses) = viewModel.routeAnalysesState {
            let routePoint = analyses.analyses.min(by: {
                abs($0.distanceFromOriginNm - vizData.points[nearest.offset].distanceNm) <
                abs($1.distanceFromOriginNm - vizData.points[nearest.offset].distanceNm)
            })
            if let routePoint {
                withAnimation {
                    if selectedPointIndex == routePoint.pointIndex {
                        selectedPointIndex = nil // toggle off
                    } else {
                        selectedPointIndex = routePoint.pointIndex
                    }
                }
                logger.info("Tapped point \(routePoint.pointIndex) at \(routePoint.distanceFromOriginNm)nm")
            }
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
